"""调试：检查 DB 里的时长与 NeteaseExecutor 候选字段。

用法: python tools/debug_candidate.py [--root dist]
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nm.config import load_config  # noqa: E402
from nm.downloaders import NeteaseExecutor  # noqa: E402
from nm.netease import NeteaseClient  # noqa: E402


def main(argv: list[str]) -> int:
    root = Path(argv[argv.index("--root") + 1]) if "--root" in argv else ROOT / "dist"
    cfg = load_config(root)
    db = sqlite3.connect(str(cfg.paths.db_file))
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT song_id, title, duration_ms, status FROM songs ORDER BY song_id LIMIT 3").fetchall()
    print("DB 里的歌曲:")
    for r in rows:
        print(f"  {r['song_id']} {r['title']!r} duration_ms={r['duration_ms']} st={r['status']}")
    db.close()

    client = NeteaseClient(cfg.netease.api_base, cfg.netease.cookie, cfg.netease.timeout_s)
    ex = NeteaseExecutor(client)
    for r in rows:
        print(f"\n--- {r['song_id']} {r['title']} ---")
        details = client.song_details([r["song_id"]])
        d = details.get(r["song_id"])
        print(f"  detail: {d}")
        url = client.play_url(r["song_id"])
        print(f"  play_url: fmt={url.fmt!r} br={url.bitrate} size={url.size} fee={url.fee} url={'有' if url.url else '无'}")
        cands = ex.search(r["title"], "", song_id=r["song_id"])
        for c in cands:
            print(f"  cand: title={c.title!r} dur={c.duration_ms} fmt={c.fmt!r} size={c.size_bytes} br={c.bitrate_kbps}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
