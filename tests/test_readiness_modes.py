"""回归测试：服务就绪判据 ready_when（纯函数，不联网、不碰运行中的服务）。

背景 bug（2026-11-08）：
  NapCat 登录成功后才同时监听 3000/3001；旧逻辑只要 extra_ports 里任一端口
  open 就算 ready，于是「3000 开着、QQ 未登录」也会谎报就绪。新增配置字段
  ready_when 让每个服务声明自己的就绪判据。NapCat 现用 login_probe（直接探测
  QQ 登录接口 /get_login_info，比「两端口都监听」更贴近「已登录」语义；缺 url
  时回退 ports_all / port）。

运行：
  D:\\Anaconda3\\envs\\open-webui\\python.exe tests\\test_readiness_modes.py

覆盖：
  1. ready_when = health / ports_all / ports_any / port / login_probe 的模式解析
  2. 大小写与空白容错
  3. health 缺 health_url、login_probe 缺 url 时回退单端口/全端口
  4. 未配置时保持旧默认（health_url > extra_ports 任一 > 单端口）
  5. 未知值回退旧默认
  6. 端口列表解析（额外端口优先、无效值过滤、单端口回退、空配置）
  7. config.example.json 里 napcat 已声明 ready_when=login_probe
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.services import (  # noqa: E402
    READY_WHEN_MODES,
    _readiness_mode,
    _readiness_ports,
)

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name if ok else (name, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


def expect_mode(name: str, cfg, want: str) -> None:
    got = _readiness_mode(cfg)
    check(name, got == want, f"want={want!r} got={got!r}")


def expect_ports(name: str, cfg, want: list[int]) -> None:
    got = _readiness_ports(cfg)
    check(name, got == want, f"want={want!r} got={got!r}")


def test_mode_parsing() -> None:
    print("\n[1] ready_when 模式解析")
    check("模式取值集合固定",
          READY_WHEN_MODES == ("health", "ports_all", "ports_any", "port", "login_probe"),
          str(READY_WHEN_MODES))

    # 显式配置优先于其它探测字段
    expect_mode("health：只测 health_url",
                {"ready_when": "health", "health_url": "http://127.0.0.1:8000/health", "port": 8000},
                "health")
    expect_mode("ports_all：额外端口全部监听",
                {"ready_when": "ports_all", "ports": [3000, 3001], "port": 3000},
                "ports_all")
    expect_mode("ports_any：任一端口监听",
                {"ready_when": "ports_any", "ports": [3000, 3001], "port": 3000},
                "ports_any")
    expect_mode("port：只测单端口",
                {"ready_when": "port", "ports": [3000, 3001], "port": 3000},
                "port")
    expect_mode("login_probe：探测 QQ 登录接口",
                {"ready_when": "login_probe",
                 "login_probe": {"url": "http://127.0.0.1:3000/get_login_info"},
                 "ports": [3000, 3001], "port": 3000},
                "login_probe")

    # ports_all 即使配置了 health_url 也不被覆盖（显式优先）
    expect_mode("ports_all 优先于 health_url",
                {"ready_when": "ports_all", "health_url": "http://127.0.0.1:3000/health",
                 "ports": [3000, 3001]},
                "ports_all")

    print("\n[2] 大小写 / 空白容错")
    expect_mode("ports_all 大小写与空白", {"ready_when": "  Ports_All  ", "ports": [3000, 3001]},
                "ports_all")
    expect_mode("HEALTH 大写", {"ready_when": "HEALTH", "health_url": "http://x/health"}, "health")
    expect_mode("port 带空白", {"ready_when": " port ", "port": 8903}, "port")

    print("\n[3] health 缺 health_url / login_probe 缺 url 回退")
    expect_mode("health 无 health_url → port", {"ready_when": "health", "port": 8903}, "port")
    expect_mode("health 的 health_url 为空白 → port",
                {"ready_when": "health", "health_url": "   ", "port": 8903}, "port")
    expect_mode("login_probe 无 url + 有额外端口 → ports_all",
                {"ready_when": "login_probe", "ports": [3000, 3001], "port": 3000}, "ports_all")
    expect_mode("login_probe 无 url + 无额外端口 → port",
                {"ready_when": "login_probe", "port": 3000}, "port")
    # 注意：login_probe.url 不做 strip（与 health_url 不同），空白串视为已配置 → 仍 login_probe
    expect_mode("login_probe 的 url 为空白（不 strip）→ 仍 login_probe",
                {"ready_when": "login_probe", "login_probe": {"url": "  "},
                 "ports": [3000, 3001]}, "login_probe")


def test_default_fallback() -> None:
    print("\n[4] 未配置 → 旧默认（health_url > extra_ports 任一 > 单端口）")
    expect_mode("有 health_url → health",
                {"health_url": "http://127.0.0.1:8188/system_stats", "port": 8188}, "health")
    expect_mode("只有 extra_ports → ports_any",
                {"ports": [7871, 7873], "port": 7871}, "ports_any")
    expect_mode("只有 port → port", {"port": 8899}, "port")
    expect_mode("空配置 → port", {}, "port")
    expect_mode("None 配置 → port", None, "port")
    expect_mode("ready_when 为空串 → 旧默认",
                {"ready_when": "", "health_url": "http://x/health"}, "health")

    print("\n[5] 未知值回退旧默认")
    expect_mode("未知值 + health_url → health",
                {"ready_when": "login_state", "health_url": "http://x/health"}, "health")
    expect_mode("未知值 + extra_ports → ports_any",
                {"ready_when": "login_state", "ports": [3000, 3001]}, "ports_any")
    expect_mode("未知值 + 仅 port → port",
                {"ready_when": "login_state", "port": 3000}, "port")
    expect_mode("未知值 + 空配置 → port", {"ready_when": "bogus"}, "port")


def test_ports_parsing() -> None:
    print("\n[6] 端口列表解析 _readiness_ports")
    expect_ports("额外端口优先", {"ports": [3000, 3001], "port": 3000}, [3000, 3001])
    expect_ports("无额外端口回退主端口", {"port": 8899}, [8899])
    expect_ports("字符串端口被转换", {"ports": ["3000", "3001"]}, [3000, 3001])
    expect_ports("主端口支持字符串", {"port": "7871"}, [7871])
    expect_ports("无效额外端口被忽略", {"ports": ["x", None, -5, 0, 3001]}, [3001])
    expect_ports("额外端口全无效则回退主端口", {"ports": ["x"], "port": 8188}, [8188])
    expect_ports("空额外端口列表回退主端口", {"ports": [], "port": 8902}, [8902])
    expect_ports("无端口配置返回空列表", {}, [])
    expect_ports("端口为 0 返回空列表", {"port": 0}, [])
    expect_ports("端口非法返回空列表", {"port": "abc"}, [])


def test_example_config() -> None:
    print("\n[7] config.example.json：napcat 使用 login_probe")
    cfg = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    napcat = ((cfg.get("services") or {}).get("napcat") or {})
    check("napcat 声明 ready_when=login_probe", napcat.get("ready_when") == "login_probe",
          repr(napcat.get("ready_when")))
    check("napcat 仍配置 ports=[3000,3001]", napcat.get("ports") == [3000, 3001],
          repr(napcat.get("ports")))
    check("napcat login_probe.url 已配置",
          bool((napcat.get("login_probe") or {}).get("url")),
          repr((napcat.get("login_probe") or {}).get("url")))
    check("示例配置里 napcat 解析为 login_probe", _readiness_mode(napcat) == "login_probe",
          repr(_readiness_mode(napcat)))


def main() -> int:
    print("==> 服务就绪判据 ready_when 回归测试")
    test_mode_parsing()
    test_default_fallback()
    test_ports_parsing()
    test_example_config()
    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    if FAILED:
        for name, detail in FAILED:
            print(f"  FAIL: {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
