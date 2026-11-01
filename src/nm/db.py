"""SQLite 持久层（单一数据库，位于程序同级 data/ 目录）。

表设计对应需求文档：
- songs/playlist_state  → 三栏视图（喜欢 / 已取消喜欢 / 失效）+ 顺序与标记
- files                 → 本地主权化：只增不减的落地记录
- jobs                  → 处理日志（匹配结果、清洗前后、耗时、错误）
- approvals             → 人工审批队列
- sync_runs             → 每次手动同步的快照
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS songs (
    song_id      INTEGER PRIMARY KEY,
    title        TEXT NOT NULL,
    artist       TEXT NOT NULL DEFAULT '',
    album        TEXT NOT NULL DEFAULT '',
    duration_ms  INTEGER NOT NULL DEFAULT 0,
    status       INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS playlist_state (
    song_id   INTEGER PRIMARY KEY REFERENCES songs(song_id) ON DELETE CASCADE,
    bucket    TEXT NOT NULL CHECK (bucket IN ('like', 'unliked', 'invalid')),
    marker    TEXT CHECK (marker IS NULL OR marker IN ('+', '-', '!')),
    position  INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    file_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    song_id     INTEGER NOT NULL REFERENCES songs(song_id),
    path        TEXT NOT NULL UNIQUE,
    format      TEXT NOT NULL,
    quality     TEXT NOT NULL DEFAULT 'unknown',
    bitrate_kbps INTEGER,
    duration_ms INTEGER,
    size_bytes  INTEGER NOT NULL,
    sha256      TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE (song_id, path)
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    song_id   INTEGER,
    stage     TEXT NOT NULL,
    source    TEXT,
    ok        INTEGER NOT NULL,
    detail    TEXT,
    elapsed_ms INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id INTEGER PRIMARY KEY AUTOINCREMENT,
    song_id     INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    reason      TEXT NOT NULL,
    payload     TEXT,
    status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at  TEXT NOT NULL,
    resolved_at TEXT,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS sync_runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    total       INTEGER,
    added       INTEGER,
    removed     INTEGER,
    invalid     INTEGER,
    raw_path    TEXT
);

CREATE INDEX IF NOT EXISTS idx_playlist_bucket ON playlist_state(bucket, position);
CREATE INDEX IF NOT EXISTS idx_jobs_song ON jobs(song_id);
CREATE INDEX IF NOT EXISTS idx_files_song ON files(song_id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class DB:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---------- 基础 ----------
    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "DB":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.conn.commit()

    # ---------- 歌曲 ----------
    def upsert_song(self, song: dict) -> None:
        ts = now_iso()
        self.conn.execute(
            """
            INSERT INTO songs(song_id, title, artist, album, duration_ms, status, first_seen_at, last_seen_at)
            VALUES(:song_id, :title, :artist, :album, :duration_ms, :status, :ts, :ts)
            ON CONFLICT(song_id) DO UPDATE SET
              title=excluded.title, artist=excluded.artist, album=excluded.album,
              duration_ms=excluded.duration_ms, status=excluded.status, last_seen_at=excluded.last_seen_at
            """,
            {
                "song_id": song["song_id"],
                "title": song.get("title", ""),
                "artist": song.get("artist", ""),
                "album": song.get("album", ""),
                "duration_ms": int(song.get("duration_ms", 0)),
                "status": int(song.get("status", 0)),
                "ts": ts,
            },
        )

    def set_playlist_state(self, song_id: int, bucket: str, position: int, marker: str | None) -> None:
        self.conn.execute(
            """
            INSERT INTO playlist_state(song_id, bucket, marker, position, updated_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(song_id) DO UPDATE SET
              bucket=excluded.bucket, marker=excluded.marker,
              position=excluded.position, updated_at=excluded.updated_at
            """,
            (song_id, bucket, marker, position, now_iso()),
        )

    def states(self) -> dict[int, sqlite3.Row]:
        return {r["song_id"]: r for r in self.conn.execute("SELECT * FROM playlist_state")}

    def snapshot(self) -> dict[int, dict]:
        """当前三栏状态（含歌曲元数据与本地文件情况），供差异计算使用。"""
        rows = self.conn.execute(
            """
            SELECT s.*, p.bucket, p.marker, p.position,
                   (SELECT COUNT(*) FROM files f WHERE f.song_id = s.song_id) AS file_count
            FROM songs s LEFT JOIN playlist_state p ON p.song_id = s.song_id
            """
        )
        return {r["song_id"]: dict(r) for r in rows}

    def list_bucket(self, bucket: str) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT s.*, p.marker, p.position,
                   (SELECT COUNT(*) FROM files f WHERE f.song_id = s.song_id) AS file_count
            FROM songs s JOIN playlist_state p ON p.song_id = s.song_id
            WHERE p.bucket = ?
            ORDER BY p.position ASC, s.song_id ASC
            """,
            (bucket,),
        )
        return [dict(r) for r in rows]

    def commit(self) -> None:
        self.conn.commit()

    # ---------- 文件 ----------
    def add_file(self, song_id: int, path: Path | str, fmt: str, size_bytes: int,
                 quality: str = "unknown", bitrate_kbps: int | None = None,
                 duration_ms: int | None = None, sha256: str | None = None) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO files(song_id, path, format, quality, bitrate_kbps, duration_ms, size_bytes, sha256, created_at)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(path) DO UPDATE SET size_bytes=excluded.size_bytes, sha256=excluded.sha256
            """,
            (song_id, str(path), fmt, quality, bitrate_kbps, duration_ms, size_bytes, sha256, now_iso()),
        )
        self.conn.commit()
        return cur.lastrowid or 0

    def files_for(self, song_id: int) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM files WHERE song_id=?", (song_id,))]

    def all_files(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM files ORDER BY created_at DESC")]

    # ---------- 日志 ----------
    def log_job(self, song_id: int | None, stage: str, ok: bool, detail: str = "",
                source: str | None = None, elapsed_ms: int | None = None) -> None:
        self.conn.execute(
            "INSERT INTO jobs(song_id, stage, source, ok, detail, elapsed_ms, created_at) VALUES(?,?,?,?,?,?,?)",
            (song_id, stage, source, 1 if ok else 0, detail, elapsed_ms, now_iso()),
        )
        self.conn.commit()

    def recent_jobs(self, limit: int = 20) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM jobs ORDER BY job_id DESC LIMIT ?", (limit,))]

    # ---------- 审批 ----------
    def add_approval(self, song_id: int, kind: str, reason: str, payload: dict | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO approvals(song_id, kind, reason, payload, status, created_at) VALUES(?,?,?,?,'pending',?)",
            (song_id, kind, reason, json.dumps(payload or {}, ensure_ascii=False), now_iso()),
        )
        self.conn.commit()
        return cur.lastrowid or 0

    def pending_approvals(self) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT a.*, s.title, s.artist, s.album, s.duration_ms,
                   (SELECT COUNT(*) FROM files f WHERE f.song_id=a.song_id) AS file_count
            FROM approvals a LEFT JOIN songs s ON s.song_id = a.song_id
            WHERE a.status='pending' ORDER BY a.approval_id ASC
            """
        )
        return [dict(r) for r in rows]

    def resolve_approval(self, approval_id: int, status: str, note: str = "") -> None:
        assert status in ("approved", "rejected")
        self.conn.execute(
            "UPDATE approvals SET status=?, resolved_at=?, note=? WHERE approval_id=?",
            (status, now_iso(), note, approval_id),
        )
        self.conn.commit()

    def has_pending_approval(self, song_id: int, kind: str | None = None) -> bool:
        if kind:
            row = self.conn.execute(
                "SELECT 1 FROM approvals WHERE song_id=? AND kind=? AND status='pending' LIMIT 1",
                (song_id, kind),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT 1 FROM approvals WHERE song_id=? AND status='pending' LIMIT 1", (song_id,)
            ).fetchone()
        return row is not None

    def last_approval(self, song_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM approvals WHERE song_id=? ORDER BY approval_id DESC LIMIT 1", (song_id,)
        ).fetchone()
        return dict(row) if row else None

    def is_rejected(self, song_id: int) -> bool:
        """用户最近一次明确拒绝该曲 → 不再自动尝试。"""
        last = self.last_approval(song_id)
        return bool(last and last["status"] == "rejected")

    # ---------- 同步记录 ----------
    def start_sync_run(self) -> int:
        cur = self.conn.execute("INSERT INTO sync_runs(started_at) VALUES(?)", (now_iso(),))
        self.conn.commit()
        return cur.lastrowid or 0

    def finish_sync_run(self, run_id: int, total: int, added: int, removed: int,
                        invalid: int, raw_path: str | None = None) -> None:
        self.conn.execute(
            "UPDATE sync_runs SET finished_at=?, total=?, added=?, removed=?, invalid=?, raw_path=? WHERE run_id=?",
            (now_iso(), total, added, removed, invalid, raw_path, run_id),
        )
        self.conn.commit()


def executemany(conn: sqlite3.Connection, sql: str, rows: Iterable[tuple]) -> None:
    conn.executemany(sql, rows)
    conn.commit()
