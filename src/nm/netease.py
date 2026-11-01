"""网易云客户端。

**实测结论（2026-11-01，本机）**：weapi（AES+RSA 加密 POST）当前返回空 body，
明文 `/api/...` GET 带 Cookie 完全可用。因此业务接口全部走 /api/ 明文通路，
weapi 的加密函数保留（单测覆盖、以备将来切换）。

业务：
- 我喜欢的音乐：`/api/song/like/get` 拿 id 顺序 → `/api/v3/song/detail` 拿元数据（含 st 失效标记）
- 歌词：`/api/song/lyric`
- 无损直链：`/api/song/enhance/player/url/v1?level=lossless`（实测返回 flac/fee=0）
- 搜索：`/api/search/get?type=1`
"""
from __future__ import annotations

import base64
import json
import random
import string
from dataclasses import dataclass, field
from typing import Any

import requests
from Crypto.Cipher import AES

PRESET_KEY = b"0CoJUm6Qyw8W8jud"
IV = b"0102030405060708"
PUBKEY = "010001"
MODULUS = (
    "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76d2e417629ec"
    "4ee341f56135fccf695280104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d81"
    "3cfe4875d3e82"
)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

LOSSLESS_LEVEL = "lossless"
LOSSLESS_TYPES = {"flac", "wav", "ape", "aiff", "alac"}


class NeteaseError(RuntimeError):
    pass


# ---------------- weapi 加密（保留，单测覆盖） ----------------
def _pad(text: bytes) -> bytes:
    pad_len = 16 - len(text) % 16
    return text + bytes([pad_len]) * pad_len


def aes_encrypt(text: str, key: bytes) -> str:
    cipher = AES.new(key, AES.MODE_CBC, IV)
    return base64.b64encode(cipher.encrypt(_pad(text.encode("utf-8")))).decode("ascii")


def aes_decrypt(b64_text: str, key: bytes) -> str:
    cipher = AES.new(key, AES.MODE_CBC, IV)
    raw = cipher.decrypt(base64.b64decode(b64_text))
    return raw[:-raw[-1]].decode("utf-8")


def rsa_encrypt(text: str) -> str:
    value = int.from_bytes(text.encode("ascii"), "big")
    enc = pow(value, int(PUBKEY, 16), int(MODULUS, 16))
    return hex(enc)[2:].rjust(256, "0")


def build_payload(data: dict, secret_key: str | None = None) -> tuple[str, str, str]:
    secret = secret_key or "".join(random.choice(string.ascii_letters + string.digits) for _ in range(16))
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    first = aes_encrypt(text, PRESET_KEY)
    params = aes_encrypt(first, secret.encode("ascii"))
    return params, rsa_encrypt(secret), secret


# ---------------- 数据模型 ----------------
@dataclass
class TrackDetail:
    song_id: int
    title: str
    artist: str
    album: str
    duration_ms: int
    status: int

    def as_meta(self) -> dict:
        return {
            "song_id": self.song_id,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "duration_ms": self.duration_ms,
            "status": self.status,
        }


@dataclass
class PlayUrl:
    song_id: int
    url: str = ""
    fmt: str = ""
    bitrate: int = 0
    size: int = 0
    fee: int = 0

    @property
    def lossless(self) -> bool:
        return bool(self.url) and self.fmt.lower() in LOSSLESS_TYPES

    @property
    def bitrate_kbps(self) -> int:
        return int(round(self.bitrate / 1000)) if self.bitrate else 0


def _join_names(value: Any) -> str:
    if isinstance(value, list):
        return "/".join(str(x.get("name", "")) if isinstance(x, dict) else str(x) for x in value)
    if isinstance(value, dict):
        return str(value.get("name", ""))
    return str(value or "")


def parse_track(track: dict) -> TrackDetail:
    song_id = track.get("id") or track.get("songId") or 0
    artist = track.get("ar") or track.get("artists") or track.get("artist")
    album = track.get("al") or track.get("album")
    duration = track.get("dt") or track.get("duration") or 0
    raw_status = track.get("st", track.get("status", 0))
    try:
        status = int(raw_status)
    except (TypeError, ValueError):
        status = 0
    return TrackDetail(
        song_id=int(song_id),
        title=str(track.get("name") or ""),
        artist=_join_names(artist),
        album=_join_names(album),
        duration_ms=int(duration or 0),
        status=status,
    )


class NeteaseClient:
    def __init__(self, api_base: str = "https://music.163.com", cookie: str = "",
                 timeout_s: int = 15, session: requests.Session | None = None):
        self.api_base = api_base.rstrip("/")
        self.cookie = cookie or ""
        self.timeout_s = timeout_s
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": DEFAULT_UA,
            "Referer": "https://music.163.com/",
            "Origin": "https://music.163.com",
        })
        if self.cookie:
            self.session.headers["Cookie"] = self.cookie

    # ---------- 底层 ----------
    def _get_json(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.api_base}/api/{path.lstrip('/')}"
        resp = self.session.get(url, params=params or {}, timeout=self.timeout_s)
        if resp.status_code != 200:
            raise NeteaseError(f"HTTP {resp.status_code}: {path}")
        if not resp.content:
            raise NeteaseError(f"空响应（Cookie 可能失效或接口变更）: {path}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise NeteaseError(f"非 JSON 响应: {path}") from exc
        code = body.get("code", 200)
        if code in (301, -2, 401):
            raise NeteaseError(f"需要登录（Cookie 无效/过期），code={code}")
        if code not in (200,):
            raise NeteaseError(f"接口返回 code={code}: {path}")
        return body

    def csrf(self) -> str:
        for part in (self.session.headers.get("Cookie") or "").split(";"):
            key, _, value = part.strip().partition("=")
            if key == "__csrf":
                return value
        return ""

    def _get_raw(self, path: str, params: dict | None = None) -> dict:
        """不做 code==200 校验的 GET：扫码轮询用（800/801/802 都是状态码，不是错误）。"""
        url = f"{self.api_base}/api/{path.lstrip('/')}"
        resp = self.session.get(url, params=params or {}, timeout=self.timeout_s)
        if resp.status_code != 200:
            raise NeteaseError(f"HTTP {resp.status_code}: {path}")
        if not resp.content:
            raise NeteaseError(f"空响应: {path}")
        try:
            return resp.json()
        except ValueError as exc:
            raise NeteaseError(f"非 JSON 响应: {path}") from exc

    def weapi(self, path: str, data: dict | None = None) -> dict:
        """保留的加密通路（当前服务端返回空 body，仅备用）。"""
        payload = dict(data or {})
        payload["csrf_token"] = self.csrf()
        params, enc_sec_key, _ = build_payload(payload)
        resp = self.session.post(
            f"{self.api_base}/weapi/{path.lstrip('/')}",
            data={"params": params, "encSecKey": enc_sec_key},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self.timeout_s,
        )
        if not resp.content:
            raise NeteaseError("weapi 返回空 body（该通路当前不可用）")
        return resp.json()

    # ---------- 账号 ----------
    def account_id(self) -> int:
        body = self._get_json("nuser/account/get")
        account = body.get("account") or {}
        uid = account.get("id") or (body.get("profile") or {}).get("userId")
        if not uid:
            raise NeteaseError("取不到账号 id：请检查 .env 里的 NETEASE_COOKIE 是否为登录态")
        return int(uid)

    # ---------- 我喜欢的音乐 ----------
    def liked_ids(self) -> list[int]:
        uid = self.account_id()
        body = self._get_json("song/like/get", {"uid": uid})
        return [int(i) for i in (body.get("ids") or [])]

    def song_details(self, song_ids: list[int], chunk: int = 500) -> dict[int, TrackDetail]:
        out: dict[int, TrackDetail] = {}
        for start in range(0, len(song_ids), chunk):
            part = song_ids[start:start + chunk]
            body = self._get_json("v3/song/detail", {"c": json.dumps([{"id": i} for i in part])})
            for track in body.get("songs") or []:
                detail = parse_track(track)
                out[detail.song_id] = detail
        return out

    def liked_playlist(self) -> list[TrackDetail]:
        """按接口返回顺序（即云端顺序）给出「我喜欢的音乐」全部歌曲，含失效标记。"""
        ids = self.liked_ids()
        if not ids:
            return []
        details = self.song_details(ids)
        missing = [i for i in ids if i not in details]
        if missing:  # 少数歌曲 v3/detail 取不到（版权区），补一次
            body = self._get_json("song/detail", {"ids": json.dumps(missing)})
            for track in body.get("songs") or []:
                detail = parse_track(track)
                details[detail.song_id] = detail
        return [details[i] for i in ids if i in details]

    # ---------- 歌词 / 直链 / 搜索 ----------
    def lyric(self, song_id: int) -> str:
        body = self._get_json("song/lyric", {"id": song_id, "lv": -1, "kv": -1, "tv": -1})
        lrc = (body.get("lrc") or {}).get("lyric") or ""
        translation = (body.get("tlyric") or {}).get("lyric") or ""
        if not lrc:
            return ""
        return f"{lrc}\n{translation}" if translation else lrc

    def play_url(self, song_id: int, level: str = LOSSLESS_LEVEL) -> PlayUrl:
        body = self._get_json("song/enhance/player/url/v1",
                              {"ids": json.dumps([song_id]), "level": level, "encodeType": "flac"})
        data = (body.get("data") or [{}])[0] or {}
        fmt = str(data.get("type") or "").lower()
        return PlayUrl(
            song_id=song_id,
            url=str(data.get("url") or ""),
            fmt=fmt,
            bitrate=int(data.get("br") or 0),
            size=int(data.get("size") or 0),
            fee=int(data.get("fee") or 0),
        )

    def search_songs(self, keyword: str, limit: int = 10) -> list[dict]:
        body = self._get_json("search/get",
                              {"s": keyword, "type": 1, "limit": limit, "offset": 0})
        result = body.get("result") or {}
        return list(result.get("songs") or [])

    # ---------- 扫码登录（首次配置最省事：不用浏览器、不用密码） ----------
    def qr_unikey(self) -> str:
        body = self._get_json("login/qrcode/unikey", {"type": 1})
        key = body.get("unikey")
        if not key:
            raise NeteaseError("取不到二维码 key")
        return str(key)

    def qr_login_url(self, key: str) -> str:
        return f"https://music.163.com/login?codekey={key}"

    def qr_poll(self, key: str) -> dict:
        return self._get_raw("login/qrcode/client/login", {"key": key, "type": 1})

    def session_cookie_string(self, domain_suffix: str = "163.com") -> str:
        parts = []
        for c in self.session.cookies:
            if domain_suffix in (c.domain or ""):
                parts.append(f"{c.name}={c.value}")
        return "; ".join(parts)

    # ---------- 自检 ----------
    def ping(self) -> bool:
        try:
            if self.cookie:
                self.account_id()
                return True
            body = self._get_json("search/get", {"s": "test", "type": 1, "limit": 1})
            return bool(body)
        except Exception:  # noqa: BLE001
            return False
