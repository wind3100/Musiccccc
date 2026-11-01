"""PyInstaller 打包专用入口。

为什么不用一个名为 nm.py 的入口：入口脚本与 `nm` 包同名时，PyInstaller 的依赖分析会把
`import nm` 当成「入口脚本自身」而跳过收集，产物运行时报 ModuleNotFoundError: No module named 'nm'。
故打包用本文件作入口（`--name nm` 只决定 exe 名）；源码运行请用 `nm.cmd` 或 `python -m nm`。
"""
from __future__ import annotations

import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from nm.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
