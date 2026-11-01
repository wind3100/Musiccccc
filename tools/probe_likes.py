"""探测：拿到「我喜欢的音乐」的顺序与状态（试多种参数组合）。

用法: python tools/probe_likes.py [--root dist]
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


def summarize(tag: str, body) -> int:
    if not isinstance(body, dict):
        print(f"  {tag:44} -> 非 JSON")
        return 0
    pl = body.get("playlist") or body.get("result") or {}
    tids = pl.get("trackIds") or []
    tracks = pl.get("tracks") or []
    print(f"  {tag:44} code={body.get('code')} name={pl.get('name')!r} "
          f"trackCount={pl.get('trackCount')} trackIds={len(tids)} tracks={len(tracks)}")
    if tids:
        print(f"      前 3 个 id: {[t.get('id') for t in tids[:3]]}")
    if body.get("ids") is not None:
        print(f"      body.ids = {len(body['ids'])} 个: {body['ids'][:3]}")
    return len(tids) or len(tracks) or len(body.get("ids") or [])


def main(argv: list[str]) -> int:
    root = Path(argv[argv.index("--root") + 1]) if "--root" in argv else ROOT / "dist"
    cfg = load_config(root)
    s = requests.Session()
    s.headers.update({"User-Agent": DEFAULT_UA, "Referer": "https://music.163.com/"})
    s.headers["Cookie"] = cfg.netease.cookie
    base = cfg.netease.api_base

    acc = s.get(f"{base}/api/nuser/account/get", timeout=20).json()
    uid = acc["account"]["id"]
    print(f"uid = {uid}")

    tries = [
        ("v6/playlist/detail n=1000 s=8", "v6/playlist/detail", {"id": uid, "n": 1000, "s": 8}),
        ("v6/playlist/detail n=1000 s=0", "v6/playlist/detail", {"id": uid, "n": 1000, "s": 0}),
        ("v6/playlist/detail 无 n/s", "v6/playlist/detail", {"id": uid}),
        ("playlist/detail n=1000 s=8", "playlist/detail", {"id": uid, "n": 1000, "s": 8}),
        ("playlist/detail 无参数", "playlist/detail", {"id": uid}),
        ("v6/playlist/detail offset/n", "v6/playlist/detail", {"id": uid, "n": 1000, "s": 0, "offset": 0}),
    ]
    for tag, path, params in tries:
        try:
            body = s.get(f"{base}/api/{path}", params=params, timeout=20).json()
        except Exception as exc:  # noqa: BLE001
            print(f"  {tag:44} -> 异常 {exc}")
            continue
        summarize(tag, body)

    print("--- 其他候选接口 ---")
    for tag, path, params in [
        ("song/like/get?uid", "song/like/get", {"uid": uid}),
        ("likelist?uid", "likelist", {"uid": uid}),
        ("song/like/get?uid&limit", "song/like/get", {"uid": uid, "limit": 1000, "offset": 0}),
    ]:
        try:
            r = s.get(f"{base}/api/{path}", params=params, timeout=20)
            body = r.json() if r.content else {}
        except Exception as exc:  # noqa: BLE001
            print(f"  {tag:28} -> 异常 {exc}")
            continue
        n = summarize(tag, body)
        if n:
            print("      原始键:", list(body)[:8])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
