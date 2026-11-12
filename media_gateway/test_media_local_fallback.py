"""Gateway /img 本地 output 回退的路径安全与命中测试。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" — " + detail) if detail else ""))
    if not ok:
        raise AssertionError(name)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mobius-img-test-") as tmp:
        out = Path(tmp) / "output"
        out.mkdir()
        image = out / "sample.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        nested = out / "sub"
        nested.mkdir()
        (nested / "nested.png").write_bytes(b"\x89PNG\r\n\x1a\nnested")

        hit = server._local_output_image(str(out), "sample.png", "")
        check("本地 output 命中", hit is not None and os.path.samefile(hit, image))
        check("子目录命中", server._local_output_image(str(out), "nested.png", "sub") is not None)
        check("缺失文件不命中", server._local_output_image(str(out), "missing.png", "") is None)
        check("绝对文件名被拒绝", server._local_output_image(str(out), str(image), "") is None)
        check("路径穿越被拒绝", server._local_output_image(str(out), "nested.png", "..") is None)
        check("子目录穿越被拒绝", server._local_output_image(str(out), "x.png", "../sub") is None)

    print("\n结果: 6/6 通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
