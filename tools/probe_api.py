"""探测：明文 /api/ 通路能否拿到「我喜欢的音乐」/ 歌曲详情 / 歌词。

用法: python tools/probe_api.py [--root dist]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import requests  # noqa: E402

from nm.config import load_config  # noqa: E402
from nm.netease import DEFAULT_UA  # noqa: E402


def main(argv: list[str]) -> int:
    root = Path(argv[argv.index("--root") + 1]) if "--root" in argv else ROOT / "dist"
    cfg = load_config(root)
    s = requests.Session()
    s.headers.update({"User-Agent": DEFAULT_UA, "Referer": "https://music.163.com/"})
    s.headers["Cookie"] = cfg.netease.cookie
    base = cfg.netease.api_base

    def get(path: str, params: dict | None = None):
        r = s.get(f"{base}/api/{path}", params=params, timeout=20)
        try:
            body = r.json()
        except ValueError:
            body = None
        print(f"  {path:34} HTTP {r.status_code} len={len(r.content)} "
              f"code={body.get('code') if isinstance(body, dict) else 'n/a'}")
        return body

    print("--- 账号 ---")
    acc = get("nuser/account/get")
    uid = ((acc or {}).get("account") or {}).get("id")
    print("  uid =", uid)
    if not uid:
        return 1

    print("--- 我喜欢的音乐 ---")
    pl = get("v6/playlist/detail", {"id": uid, "n": 1000, "s": 8})
    playlist = (pl or {}).get("playlist") or {}
    if not playlist:
        pl2 = get("playlist/detail", {"id": uid})
        playlist = (pl2 or {}).get("playlist") or (pl2 or {}).get("result") or {}
    tids = [t.get("id") for t in (playlist.get("trackIds") or [])]
    tracks = playlist.get("tracks") or []
    print(f"  playlist.name = {playlist.get('name')!r}")
    print(f"  trackIds = {len(tids)} 首; tracks 内联 = {len(tracks)} 首")
    if tracks:
        t = tracks[0]
        print("  首曲字段:", sorted(t.keys())[:14])
        print("  首曲:", t.get("name"), "| dt", t.get("dt"), "| st", t.get("st"),
              "| ar", [a.get("name") for a in (t.get("ar") or [])][:2],
              "| al", (t.get("al") or {}).get("name"))
    sample_id = tids[0] if tids else (tracks[0].get("id") if tracks else None)

    print("--- 歌曲详情 ---")
    if sample_id:
        body = get("v3/song/detail", {"c": json.dumps([{"id": sample_id}])})
        songs = (body or {}).get("songs") or []
        print("  songs:", len(songs), "| 首曲:", (songs[0].get("name") if songs else None))
        body2 = get("song/detail", {"ids": json.dumps([sample_id])})
        songs2 = (body2 or {}).get("songs") or []
        print("  song/detail songs:", len(songs2))

    print("--- 歌词 ---")
    if sample_id:
        body = get("song/lyric", {"id": sample_id, "lv": -1, "kv": -1, "tv": -1})
        lrc = ((body or {}).get("lrc") or {}).get("lyric") or ""
        print("  lrc 长度:", len(lrc), "| 首行:", lrc.split("\n")[0][:40] if lrc else "(空)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
