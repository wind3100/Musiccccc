"""探测：歌曲详情 / 歌词 / 播放直链（明文 /api 通路）。

用法: python tools/probe_details.py [--root dist]
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

    uid = s.get(f"{base}/api/nuser/account/get", timeout=20).json()["account"]["id"]
    ids = s.get(f"{base}/api/song/like/get", params={"uid": uid}, timeout=20).json().get("ids") or []
    print(f"uid={uid}  liked={len(ids)} 首")
    if not ids:
        return 1
    sample = ids[0]
    print(f"样本 song_id = {sample}")

    print("--- 详情 ---")
    for tag, path, params in [
        ("v3/song/detail (c=JSON)", "v3/song/detail", {"c": json.dumps([{"id": i} for i in ids[:3]])}),
        ("song/detail (ids=JSON)", "song/detail", {"ids": json.dumps(ids[:3])}),
    ]:
        r = s.get(f"{base}/api/{path}", params=params, timeout=20)
        try:
            body = r.json()
        except ValueError:
            print(f"  {tag:26} 非 JSON, len={len(r.content)}")
            continue
        songs = body.get("songs") or []
        print(f"  {tag:26} code={body.get('code')} songs={len(songs)}")
        for song in songs[:3]:
            print(f"      {song.get('id')} {song.get('name')!r} dt={song.get('dt')} st={song.get('st')} "
                  f"ar={[a.get('name') for a in (song.get('ar') or [])]} al={(song.get('al') or {}).get('name')!r}")

    print("--- 歌词 ---")
    r = s.get(f"{base}/api/song/lyric", params={"id": sample, "lv": -1, "kv": -1, "tv": -1}, timeout=20)
    body = r.json() if r.content else {}
    lrc = ((body.get("lrc") or {}).get("lyric") or "")
    print(f"  code={body.get('code')} lrc 长度={len(lrc)} 首行={lrc.split(chr(10))[0][:40] if lrc else '(空)'}")

    print("--- 播放直链（判断有没有无损权限）---")
    for level in ("lossless", "exhigh", "standard"):
        for tag, path, params in [
            ("play 4.x", "song/enhance/player/url/v1",
             {"ids": json.dumps([sample]), "level": level, "encodeType": "flac"}),
        ]:
            r = s.get(f"{base}/api/{path}", params=params, timeout=20)
            body = r.json() if r.content else {}
            data = (body.get("data") or [{}])[0]
            print(f"  level={level:9} code={body.get('code')} url={'有' if data.get('url') else '无'} "
                  f"type={data.get('type')!r} br={data.get('br')} size={data.get('size')} "
                  f"fee={data.get('fee')}")
            break

    print("--- 旧直链接口 ---")
    r = s.get(f"{base}/api/song/enhance/player/url", params={"ids": f"[{sample}]", "br": 999000}, timeout=20)
    body = r.json() if r.content else {}
    data = (body.get("data") or [{}])[0]
    print(f"  br=999000 code={body.get('code')} url={'有' if data.get('url') else '无'} "
          f"type={data.get('type')!r} br={data.get('br')} fee={data.get('fee')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
