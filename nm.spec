# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 规格文件：单文件 exe（绿色软件，配置/数据/媒体库都在 exe 同级）。

关键点：pathex 用绝对路径 + 显式把 src 加入 sys.path，才能让依赖分析真正收集 nm 包
（相对 pathex 在本机会失效，导致产物运行时报 ModuleNotFoundError: nm）。
入口用 pack_entry.py 而非 nm.py：入口与包同名会让 PyInstaller 跳过收集 nm。
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(SPEC))  # noqa: F821  —— SPEC 由 PyInstaller 注入
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)

from PyInstaller.utils.hooks import collect_submodules  # noqa: E402

hidden = collect_submodules("nm") + ["Crypto.Cipher.AES"]

a = Analysis(
    [os.path.join(ROOT, "pack_entry.py")],
    pathex=[SRC],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tkinter"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="nm",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
