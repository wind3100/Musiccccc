"""诊断：.env 里的 Cookie 到底是不是登录态；浏览器里有没有 MUSIC_U。

用法:
    python tools/probe_cookies.py            # 只诊断，不打印任何 cookie 值
    python tools/probe_cookies.py --extract  # 从 Edge 解出 MUSIC_U 并写进 dist\\.env（需已登录）

只输出键名/长度，绝不回显密文。
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nm.config import load_config  # noqa: E402

BROWSERS = {
    "Edge": Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Edge/User Data",
    "Chrome": Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/User Data",
}


def env_cookie_keys(cfg) -> list[str]:
    return [p.split("=")[0].strip() for p in cfg.netease.cookie.split(";") if "=" in p]


def copy_cookie_db(profile_dir: Path) -> Path | None:
    db = profile_dir / "Default" / "Network" / "Cookies"
    if not db.is_file():
        return None
    tmp = Path(tempfile.gettempdir()) / f"cookies_probe_{os.getpid()}.db"
    shutil.copy2(db, tmp)  # 浏览器运行时库被锁，复制一份读
    return tmp


def list_163_cookie_names(db_path: Path) -> list[tuple[str, str, int]]:
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT host_key, name, LENGTH(encrypted_value) FROM cookies "
            "WHERE host_key LIKE ? ORDER BY host_key, name",
            ("%163.com%",),
        ).fetchall()
    finally:
        conn.close()
    return [(h, n, int(l or 0)) for h, n, l in rows]


# ---- Windows DPAPI：解开 Edge 的 AES 主密钥 ----
class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def dpapi_unprotect(data: bytes) -> bytes:
    buf_in = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)))
    buf_out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(buf_in), None, None, None, None, 0, ctypes.byref(buf_out)
    ):
        raise OSError("CryptUnprotectData 失败（可能不是同一用户/会话）")
    try:
        return ctypes.string_at(buf_out.pbData, buf_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(buf_out.pbData)


def master_key(profile_dir: Path) -> bytes:
    local_state = json.loads((profile_dir / "Local State").read_text(encoding="utf-8"))
    enc = base64.b64decode(local_state["os_crypt"]["encrypted_key"])
    if enc[:5] != b"DPAPI":
        raise ValueError("未知的 encrypted_key 前缀")
    return dpapi_unprotect(enc[5:])


def decrypt_value(blob: bytes, key: bytes) -> str:
    from Crypto.Cipher import AES

    if blob[:3] in (b"v10", b"v11"):
        nonce, payload = blob[3:15], blob[15:]
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(payload[:-16], payload[-16:]).decode("utf-8")
    return dpapi_unprotect(blob).decode("utf-8")


def read_cookie_map(profile_dir: Path, key: bytes) -> dict[str, str]:
    db_path = copy_cookie_db(profile_dir)
    if db_path is None:
        return {}
    out: dict[str, str] = {}
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT name, encrypted_value, value FROM cookies WHERE host_key LIKE ?",
            ("%163.com%",),
        ).fetchall()
    finally:
        conn.close()
        db_path.unlink(missing_ok=True)
    for name, enc, plain in rows:
        try:
            out[name] = plain or decrypt_value(enc, key)
        except Exception:  # noqa: BLE001
            continue
    return out


def write_env(env_path: Path, cookie: str) -> None:
    line = "NETEASE_COOKIE=" + cookie
    lines = env_path.read_text(encoding="utf-8-sig").splitlines() if env_path.is_file() else []
    for i, old in enumerate(lines):
        if old.strip().startswith("NETEASE_COOKIE="):
            lines[i] = line
            break
    else:
        lines.append(line)
    env_path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    root = Path(os.environ.get("NM_ROOT", ROOT / "dist"))
    cfg = load_config(root)
    keys = env_cookie_keys(cfg)
    print(f"[.env] {root / '.env'}")
    print(f"       cookie 长度 {len(cfg.netease.cookie)}，键 {len(keys)} 个")
    print(f"       含 MUSIC_U: {'MUSIC_U' in keys} | 含 MUSIC_A: {'MUSIC_A' in keys}")
    print(f"       键名: {keys}")

    for name, profile in BROWSERS.items():
        if not (profile / "Local State").is_file():
            continue
        db_path = copy_cookie_db(profile)
        if db_path is None:
            continue
        try:
            names = list_163_cookie_names(db_path)
        finally:
            db_path.unlink(missing_ok=True)
        has = [n for _, n, _ in names if n in ("MUSIC_U", "MUSIC_A")]
        print(f"[{name}] 163.com cookie {len(names)} 个，含登录态: {has or '无'}")
        if has:
            print(f"       登录态键存在：{has}")

    if "--extract" not in argv:
        return 0

    for name, profile in BROWSERS.items():
        if not (profile / "Local State").is_file():
            continue
        try:
            key = master_key(profile)
            cookie_map = read_cookie_map(profile, key)
        except Exception as exc:  # noqa: BLE001
            print(f"[{name}] 解密失败: {exc}")
            continue
        if "MUSIC_U" not in cookie_map and "MUSIC_A" not in cookie_map:
            print(f"[{name}] 没有 MUSIC_U/MUSIC_A，跳过")
            continue
        ordered = [f"{k}={v}" for k, v in sorted(cookie_map.items())]
        write_env(root / ".env", "; ".join(ordered))
        print(f"[{name}] 已写入 {root / '.env'}：{len(ordered)} 个键（值不回显）")
        return 0
    print("没有找到带登录态的浏览器 cookie。请确认已在浏览器里登录 music.163.com。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
