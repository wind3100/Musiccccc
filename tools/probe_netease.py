"""网易云接口探测脚本（复用：判断 weapi/旧 api 在当前网络与 Cookie 状态下是否可用）。

用法:
    python tools/probe_netease.py            # 匿名探测
    python tools/probe_netease.py .env       # 读取 .env 里的 NETEASE_COOKIE 再探测
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import requests  # noqa: E402

from nm.config import load_config  # noqa: E402
from nm.netease import DEFAULT_UA, NeteaseClient, build_payload  # noqa: E402

PUBLIC_PLAYLIST = 24381616


def brief(text: str, n: int = 160) -> str:
    return text[:n].replace("\n", " ").replace("\r", " ")


def main(argv: list[str]) -> int:
    cfg = load_config(ROOT)
    cookie = cfg.netease.cookie
    print(f"cookie: {'有（长度 %d）' % len(cookie) if cookie else '无'}")
    headers = {"User-Agent": DEFAULT_UA, "Referer": "https://music.163.com/"}

    print("--- GET /api/playlist/detail （老接口，匿名） ---")
    r = requests.get(f"https://music.163.com/api/playlist/detail?id={PUBLIC_PLAYLIST}",
                     headers=headers, timeout=15)
    print(f"HTTP {r.status_code} len={len(r.content)} :: {brief(r.text)}")

    print("--- POST /weapi/v6/playlist/detail （weapi，当前 Cookie 状态） ---")
    params, key, _ = build_payload({"id": PUBLIC_PLAYLIST, "n": 3, "s": 8, "csrf_token": ""})
    s = requests.Session()
    s.headers.update(headers)
    if cookie:
        s.headers["Cookie"] = cookie
    r = s.post("https://music.163.com/weapi/v6/playlist/detail",
               data={"params": params, "encSecKey": key}, timeout=15)
    print(f"HTTP {r.status_code} len={len(r.content)} :: {brief(r.text)}")

    client = NeteaseClient(cfg.netease.api_base, cookie, cfg.netease.timeout_s)
    print("--- NeteaseClient.weapi 封装 ---")
    try:
        body = client.weapi("v6/playlist/detail", {"id": PUBLIC_PLAYLIST, "n": 3, "s": 8})
        playlist = body.get("playlist") or {}
        print(f"OK code={body.get('code')} name={playlist.get('name')} tracks={len(playlist.get('tracks') or [])}")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL {type(exc).__name__}: {exc}")

    if cookie:
        print("--- 我喜欢的音乐（需要有效 Cookie） ---")
        try:
            tracks = client.liked_playlist()
            print(f"OK 取到 {len(tracks)} 首")
            for t in tracks[:3]:
                print(f"  {t.song_id} {t.title} / {t.artist} / {t.duration_ms}ms / st={t.status}")
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
