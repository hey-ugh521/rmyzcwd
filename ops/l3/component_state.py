"""组件安装状态机（absent/downloading/installing/installed/failed）。

- 状态与合法转移集中定义；非法转移抛 StateTransitionError
- 原子持久化（临时文件 + os.replace），进程崩溃后下次启动可恢复：
  downloading/installing 超过 stale_seconds 视为中断 -> failed
- 与 versions.json 互补：versions.json 是「安装记录」，本文件是「安装过程状态」
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

STATES = ("absent", "downloading", "installing", "installed", "failed")

TRANSITIONS: dict[str, set[str]] = {
    "absent": {"absent", "downloading", "installed"},
    "downloading": {"downloading", "installing", "failed", "absent"},
    "installing": {"installing", "installed", "failed", "absent"},
    "installed": {"installed", "downloading", "absent"},
    "failed": {"failed", "downloading", "absent"},
}

DEFAULT_STALE_SECONDS = 6 * 3600


class StateTransitionError(RuntimeError):
    pass


def transition_allowed(old: str, new: str) -> bool:
    return old == new or new in TRANSITIONS.get(old, set())


def _iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts if ts is not None else time.time()))


class ComponentStateStore:
    def __init__(self, path: str | os.PathLike, stale_seconds: float = DEFAULT_STALE_SECONDS):
        self.path = Path(path)
        self.stale_seconds = float(stale_seconds)
        self.data: dict[str, dict] = self._load()
        self._recover_stale()

    # ---------------- 持久化 ----------------

    def _load(self) -> dict[str, dict]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        comps = raw.get("components") if isinstance(raw, dict) else None
        return comps if isinstance(comps, dict) else {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema": 1, "updated_at": _iso(), "components": self.data}
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        fd, tmp = tempfile.mkstemp(prefix=".component_states-", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _recover_stale(self) -> None:
        changed = False
        now = time.time()
        for cid, entry in list(self.data.items()):
            if entry.get("state") not in ("downloading", "installing"):
                continue
            updated = float(entry.get("updated_ts") or 0)
            if updated and now - updated > self.stale_seconds:
                self.data[cid] = {
                    **entry,
                    "state": "failed",
                    "error": f"安装中断（超过 {self.stale_seconds:.0f}s 未更新，已恢复为 failed）",
                    "updated_at": _iso(now),
                    "updated_ts": now,
                }
                changed = True
        if changed:
            self._save()

    # ---------------- 查询 ----------------

    def get(self, cid: str) -> dict | None:
        entry = self.data.get(cid)
        return dict(entry) if entry else None

    def state_of(self, cid: str) -> str:
        entry = self.data.get(cid)
        return str(entry.get("state") or "absent") if entry else "absent"

    def summary(self) -> dict:
        counts = {s: 0 for s in STATES}
        for entry in self.data.values():
            st = str(entry.get("state") or "absent")
            if st in counts:
                counts[st] += 1
        return counts

    # ---------------- 转移 ----------------

    def transition(
        self,
        cid: str,
        new: str,
        *,
        version: str | None = None,
        sha256: str | None = None,
        phase: str = "",
        error: str = "",
        detail: str = "",
    ) -> dict:
        if new not in STATES:
            raise StateTransitionError(f"未知状态: {new}")
        old = self.state_of(cid)
        if not transition_allowed(old, new):
            raise StateTransitionError(f"非法状态转移: {cid} {old} -> {new}")
        now = time.time()
        entry = dict(self.data.get(cid) or {"id": cid})
        entry["state"] = new
        entry["updated_at"] = _iso(now)
        entry["updated_ts"] = now
        if version is not None:
            entry["version"] = version
        if sha256 is not None:
            entry["sha256"] = sha256
        if phase:
            entry["phase"] = phase
        if detail:
            entry["detail"] = detail
        if new == "downloading" and old != "downloading":
            entry["started_at"] = _iso(now)
            entry["attempts"] = int(entry.get("attempts") or 0) + 1
            entry.pop("error", None)
            entry.pop("finished_at", None)
        if new == "failed":
            entry["error"] = error or entry.get("error") or "未知错误"
            entry["finished_at"] = _iso(now)
        if new in ("installed", "absent"):
            entry.pop("error", None)
            if new == "installed":
                entry["finished_at"] = _iso(now)
                entry.pop("phase", None)
        self.data[cid] = entry
        self._save()
        return dict(entry)

    # ---------------- 便捷方法 ----------------

    def mark_downloading(self, cid: str, version: str = "", sha256: str = "") -> dict:
        return self.transition(cid, "downloading", version=version or None, sha256=sha256 or None, phase="download")

    def mark_installing(self, cid: str) -> dict:
        return self.transition(cid, "installing", phase="extract")

    def mark_installed(self, cid: str, version: str = "", sha256: str = "") -> dict:
        return self.transition(cid, "installed", version=version or None, sha256=sha256 or None, phase="")

    def mark_failed(self, cid: str, error: str) -> dict:
        return self.transition(cid, "failed", error=str(error)[:500])

    def mark_absent(self, cid: str) -> dict:
        return self.transition(cid, "absent", phase="")
