"""轮询节流的 Redis 共享门（多实例一致），带进程内回退。

设计：
- 只依赖标准库 socket，自己实现最小 RESP（AUTH / SET NX PX / PTTL / GET / PING），
  避免给网关运行时引入新依赖。
- Redis 不可用（未配置/连接失败/命令错误）时静默回退到进程内状态。
- 用法：
    from pollthrottle import SharedPollGate
    gate = SharedPollGate(os.environ.get("MOBIUS_REDIS_URL", ""))
    ok, retry_ms = gate.acquire("task-1", 5000)   # 允许则 ok=True
"""

from __future__ import annotations

import socket
import threading
import time
import urllib.parse


class _Resp:
    def __init__(self, url: str, timeout: float = 1.0) -> None:
        p = urllib.parse.urlsplit(url)
        self.host = p.hostname or "127.0.0.1"
        self.port = p.port or 6379
        self.password = urllib.parse.unquote(p.password or "")
        self.db = int((p.path or "/0").strip("/") or 0)
        self.timeout = timeout

    def _connect(self) -> socket.socket:
        s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        s.settimeout(self.timeout)
        if self.password:
            self._cmd(s, "AUTH", self.password)
        if self.db:
            self._cmd(s, "SELECT", str(self.db))
        return s

    @staticmethod
    def _encode(*args: str) -> bytes:
        out = [f"*{len(args)}\r\n".encode()]
        for a in args:
            b = str(a).encode()
            out.append(f"${len(b)}\r\n".encode() + b + b"\r\n")
        return b"".join(out)

    @staticmethod
    def _read(s: socket.socket) -> object:
        f = s.makefile("rb")
        line = f.readline()
        if not line:
            raise ConnectionError("empty reply")
        kind, body = line[:1], line[1:].strip()
        if kind == b"+":
            return body.decode()
        if kind == b"-":
            raise RuntimeError(body.decode())
        if kind == b":":
            return int(body)
        if kind == b"$":
            n = int(body)
            if n < 0:
                return None
            data = f.read(n + 2)
            return data[:-2].decode()
        if kind == b"*":
            n = int(body)
            return [RespReader.read_one(f) for _ in range(n)]
        raise RuntimeError(f"bad RESP: {line[:40]!r}")

    def _cmd(self, s: socket.socket, *args: str) -> object:
        s.sendall(self._encode(*args))
        return self._read(s)

    def set_nx_px(self, key: str, px_ms: int) -> bool:
        s = self._connect()
        try:
            r = self._cmd(s, "SET", key, "1", "NX", "PX", str(px_ms))
            return r == "OK"
        finally:
            s.close()

    def pttl(self, key: str) -> int:
        s = self._connect()
        try:
            return int(self._cmd(s, "PTTL", key))
        finally:
            s.close()

    def ping(self) -> bool:
        s = self._connect()
        try:
            return self._cmd(s, "PING") == "PONG"
        finally:
            s.close()


class RespReader:  # 只为嵌套数组读取保留兼容
    @staticmethod
    def read_one(f):
        return None


class SharedPollGate:
    """跨进程轮询门。Redis 可用时用 SET NX PX；否则用进程内字典。"""

    def __init__(self, url: str = "", namespace: str = "mobius:poll") -> None:
        self.url = (url or "").strip()
        self.ns = namespace
        self._redis: _Resp | None = None
        self._redis_checked = False
        self._local: dict[str, float] = {}
        self._lock = threading.Lock()
        self._init_lock = threading.Lock()
        self._client()  # 构造时初始化，避免首次并发调用时多线程竞争

    def _client(self) -> _Resp | None:
        if not self.url:
            return None
        if not self._redis_checked:
            with self._init_lock:
                if not self._redis_checked:
                    self._redis_checked = True
                    try:
                        client = _Resp(self.url)
                        if client.ping():
                            self._redis = client
                    except Exception:
                        self._redis = None
        return self._redis

    @property
    def backend(self) -> str:
        return "redis" if self._client() is not None else "local"

    def acquire(self, key: str, interval_ms: float) -> tuple[bool, int]:
        """尝试占用 key；返回 (是否放行, 建议重试毫秒)。"""
        interval = max(0, int(interval_ms))
        if interval == 0:
            return True, 0
        client = self._client()
        if client is not None:
            redis_key = f"{self.ns}:{key}"
            for attempt in (1, 2):
                try:
                    if client.set_nx_px(redis_key, interval):
                        return True, 0
                    ttl = client.pttl(redis_key)
                    return False, max(ttl, 1)
                except Exception:
                    if attempt == 2:
                        self._redis = None  # 两次失败后回退
                    else:
                        time.sleep(0.02)
        with self._lock:
            now = time.monotonic()
            last = self._local.get(key, 0.0)
            if now - last >= interval / 1000.0:
                self._local[key] = now
                return True, 0
            retry = int((interval / 1000.0 - (now - last)) * 1000)
            return False, max(retry, 1)
