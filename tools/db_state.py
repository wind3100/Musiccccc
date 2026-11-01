"""打印当前库状态（可复用；避免在 pwsh 里拼 SQL 的引号地雷）。

用法: python tools/db_state.py [--root dist]
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nm.config import load_config  # noqa: E402


def main(argv: list[str]) -> int:
    root = Path(argv[argv.index("--root") + 1]) if "--root" in argv else ROOT / "dist"
    cfg = load_config(root)
    if not cfg.paths.db_file.is_file():
        print(f"没有数据库: {cfg.paths.db_file}")
        return 1
    db = sqlite3.connect(str(cfg.paths.db_file))
    db.row_factory = sqlite3.Row

    def one(sql: str, *args):
        row = db.execute(sql, args).fetchone()
        return row[0] if row else 0

    print(f"数据库: {cfg.paths.db_file}")
    print(f"  歌曲 {one('SELECT COUNT(*) FROM songs')} 首")
    for bucket, label in (("like", "喜欢"), ("unliked", "已取消喜欢"), ("invalid", "失效")):
        print(f"  {label}: {one('SELECT COUNT(*) FROM playlist_state WHERE bucket=?', bucket)} 首")
    print(f"  落库文件 {one('SELECT COUNT(*) FROM files')} 个，"
          f"合计 {one('SELECT COALESCE(SUM(size_bytes),0) FROM files') / 1048576:.1f} MB")
    print(f"  待审批 {one(chr(83) + 'ELECT COUNT(*) FROM approvals WHERE status=' + chr(39) + 'pending' + chr(39))} 条")
    print("  最近任务:")
    for row in db.execute("SELECT created_at, song_id, stage, ok, detail FROM jobs "
                          "ORDER BY job_id DESC LIMIT 5"):
        print(f"    {row['created_at']} {'OK  ' if row['ok'] else 'FAIL'} "
              f"[{row['song_id']}] {row['stage']:<8} {(row['detail'] or '')[:50]}")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
