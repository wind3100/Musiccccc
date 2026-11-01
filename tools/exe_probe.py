"""探测脚本：把 nm.exe 的输出重定向到文件并正确解码（避开管道/编码坑）。

用法:
    python tools/exe_probe.py                          # 不带参数 = 模拟双击 dist\\nm.exe
    python tools/exe_probe.py --stdin 4,0              # 模拟在菜单里依次按 4 然后 0
    python tools/exe_probe.py list                     # 传子命令
    python tools/exe_probe.py --exe release/nm.exe     # 指定要探测的 exe（默认 dist/nm.exe）
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dist" / "probe_out.txt"


def main(argv: list[str]) -> int:
    exe = ROOT / "dist" / "nm.exe"
    if "--exe" in argv:
        idx = argv.index("--exe")
        exe = Path(argv[idx + 1])
        if not exe.is_absolute():
            exe = (ROOT / exe).resolve()
        argv = argv[:idx] + argv[idx + 2:]
    if not exe.is_file():
        print(f"缺少 {exe}")
        return 1
    keys: list[str] = []
    if "--stdin" in argv:
        idx = argv.index("--stdin")
        keys = [k.strip() for k in argv[idx + 1].split(",") if k.strip()]
        argv = argv[:idx] + argv[idx + 2:]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("NM_ROOT", str(exe.parent))

    stdin_fh = subprocess.DEVNULL
    tmp = None
    if keys:
        tmp = Path(tempfile.mkstemp(suffix=".txt")[1])
        tmp.write_text("\n".join(keys) + "\n", encoding="utf-8")
        stdin_fh = tmp.open("rb")

    with OUT.open("wb") as fh:
        proc = subprocess.run([str(exe), *argv], stdin=stdin_fh,
                              stdout=fh, stderr=fh, env=env, cwd=str(ROOT))
    if hasattr(stdin_fh, "close"):
        stdin_fh.close()
    if tmp is not None:
        try:
            tmp.unlink()
        except OSError:
            pass  # Windows 上句柄释放有延迟，删不掉不影响探测结果

    data = OUT.read_bytes()
    for enc in ("utf-8", "gbk"):
        try:
            text = data.decode(enc)
            used = enc
            break
        except UnicodeDecodeError:
            continue
    else:
        text, used = data.decode("utf-8", "replace"), "utf-8(replace)"
    print(f"exit={proc.returncode}  encoding={used}  bytes={len(data)}")
    print("-" * 60)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
