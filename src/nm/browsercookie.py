"""从本机浏览器读取网易云登录 Cookie（Edge / Chrome）。

为什么需要：分发给别人时不能内置任何人的 Cookie，最省事的首次配置是
「你浏览器里已登录过 music.163.com → 程序自动读取」。

实现要点（本机实测）：
- Cookie 值在 Chromium 里是 AES-256-GCM 加密（v10/v11 前缀），密钥在 `Local State`
  的 `os_crypt.encrypted_key`，先用 DPAPI 解出 32 字节密钥
- 浏览器运行时 `Network/Cookies` 被锁定，直接 copy 会 PermissionError；
  用 sqlite 的 `immutable=1` URI 只读打开即可
- 只取 host 含 `163.com` 的 cookie；绝不打印值
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import sqlite3
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

LOGIN_KEYS = ("MUSIC_U", "MUSIC_A")
# 尝试顺序：优先用户最可能登录过的浏览器
BROWSER_ORDER = ("edge", "chrome", "chromium")


class BrowserCookieError(RuntimeError):
    pass


def browser_dirs() -> dict[str, Path]:
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    roaming = Path(os.environ.get("APPDATA", ""))
    return {
        "edge": local / "Microsoft/Edge/User Data",
        "chrome": local / "Google/Chrome/User Data",
        "chromium": roaming / "Chromium/User Data",
    }


def profiles(browser_dir: Path) -> list[Path]:
    if not browser_dir.is_dir():
        return []
    out = []
    for entry in sorted(browser_dir.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name != "Default" and not entry.name.startswith("Profile"):
            continue
        if (entry / "Network" / "Cookies").is_file():
            out.append(entry)
    return out


# ---------------- Windows DPAPI ----------------
class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def dpapi_unprotect(data: bytes) -> bytes:
    buf_in = _DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data),
                                              ctypes.POINTER(ctypes.c_char)))
    buf_out = _DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(buf_in), None, None, None, None, 0, ctypes.byref(buf_out))
    if not ok:
        raise BrowserCookieError("DPAPI 解密失败（可能不是同一 Windows 用户）")
    try:
        return ctypes.string_at(buf_out.pbData, buf_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(buf_out.pbData)


def master_key(profile_dir: Path) -> bytes:
    state = profile_dir.parent / "Local State"
    if not state.is_file():
        raise BrowserCookieError(f"找不到 {state}")
    data = json.loads(state.read_text(encoding="utf-8"))
    enc = base64.b64decode(data["os_crypt"]["encrypted_key"])
    if not enc.startswith(b"DPAPI"):
        raise BrowserCookieError("encrypted_key 不是 DPAPI 格式（暂不支持）")
    return dpapi_unprotect(enc[5:])


def decrypt_value(blob: bytes, key: bytes) -> str:
    from Crypto.Cipher import AES

    if blob[:3] in (b"v10", b"v11"):
        nonce, payload = blob[3:15], blob[15:]
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(payload[:-16], payload[-16:]).decode("utf-8")
    return dpapi_unprotect(blob).decode("utf-8")


# ---------------- 读取 ----------------
def _connect(cookies_db: Path) -> sqlite3.Connection:
    # 路径里有空格（"User Data"），必须做 URI 转义，否则 sqlite 打不开
    from urllib.parse import quote

    uri = f"file:{quote(str(cookies_db).replace(chr(92), '/'))}?mode=ro&immutable=1"
    try:
        return sqlite3.connect(uri, uri=True, timeout=3)
    except sqlite3.Error as exc:
        raise BrowserCookieError(f"打不开 Cookies 库: {exc}") from exc


def read_163_cookies(profile_dir: Path) -> dict[str, str]:
    db_path = profile_dir / "Network" / "Cookies"
    key = master_key(profile_dir)
    out: dict[str, str] = {}
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name, encrypted_value, value FROM cookies WHERE host_key LIKE ?",
            ("%163.com%",),
        ).fetchall()
    finally:
        conn.close()
    for name, enc, plain in rows:
        if plain:
            out[name] = plain
            continue
        try:
            out[name] = decrypt_value(enc, key)
        except Exception:  # noqa: BLE001 —— 单条解密失败不影响整体
            continue
    return out


def cookie_string(cookies: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items() if v)


@dataclass
class FoundCookie:
    cookie: str
    browser: str
    profile: str
    keys: list[str]

    @property
    def has_login(self) -> bool:
        return any(k in self.keys for k in LOGIN_KEYS)

    @property
    def masked(self) -> str:
        parts = []
        for k in self.keys:
            if k in LOGIN_KEYS:
                parts.append(f"{k}=…")
            else:
                parts.append(k)
        return ", ".join(parts)


def find_login_cookie(preferred: str | None = None) -> FoundCookie | None:
    """按顺序尝试 Edge / Chrome / Chromium，返回第一个含登录态的 Cookie。"""
    names = [preferred] if preferred else list(BROWSER_ORDER)
    for name in names:
        base = browser_dirs().get(name or "")
        if not base:
            continue
        for profile in profiles(base):
            try:
                cookies = read_163_cookies(profile)
            except Exception:  # noqa: BLE001
                continue
            if not cookies:
                continue
            if not any(k in cookies for k in LOGIN_KEYS):
                continue
            return FoundCookie(
                cookie=cookie_string(cookies),
                browser=name,
                profile=profile.name,
                keys=sorted(cookies),
            )
    return None
