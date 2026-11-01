"""歌单同步：云端顺序为唯一排序依据，本地只做状态归档（不删文件）。

三栏与标记（需求文档 §歌单同步与管理）：
- 喜欢（like）：严格对齐云端正常歌曲列表；失效歌曲置底（bucket=invalid，仍显示在喜欢栏底部）
- 已取消喜欢（unliked）：云端移除的歌曲归入此栏，本地文件保留
- 失效（invalid）：版权下架，保留在喜欢栏底部
- 标记：(-) 减少，(+) 增加，(!) 无效
- 误触取消又收藏回来：再次出现在云端即自动恢复为喜欢，不产生新文件、不丢云端顺序
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .db import DB


@dataclass
class PlaylistEntry:
    song_id: int
    title: str
    artist: str = ""
    album: str = ""
    duration_ms: int = 0
    status: int = 0  # 网易云 st：0 正常，<0 失效（版权下架等）

    @property
    def invalid(self) -> bool:
        return self.status < 0

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
class DiffResult:
    added: list[int] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)
    invalid: list[int] = field(default_factory=list)
    restored: list[int] = field(default_factory=list)
    kept: list[int] = field(default_factory=list)

    @property
    def cloud_total(self) -> int:
        return len(self.added) + len(self.invalid) + len(self.restored) + len(self.kept)

    def summary(self) -> str:
        return (
            f"云端 {self.cloud_total} 首：新增(+) {len(self.added)}，减少(-) {len(self.removed)}，"
            f"失效(!) {len(self.invalid)}，恢复 {len(self.restored)}，未变 {len(self.kept)}"
        )


def _first_name(value) -> str:
    if isinstance(value, list) and value:
        first = value[0]
        return str(first.get("name", "")) if isinstance(first, dict) else str(first)
    if isinstance(value, dict):
        return str(value.get("name", ""))
    if isinstance(value, str):
        return value
    return ""


def entry_from_track(track: dict) -> PlaylistEntry:
    """兼容新版（ar/al/dt）、旧版（artists/album/duration）与本地 meta（duration_ms/status）。"""
    song_id = track.get("id") or track.get("songId") or track.get("song_id")
    title = track.get("name") or track.get("title") or ""
    artist = _first_name(track.get("ar") or track.get("artists") or track.get("artist"))
    album = _first_name(track.get("al") or track.get("album"))
    duration = (track.get("dt") or track.get("duration") or track.get("durationMs")
                or track.get("duration_ms") or 0)
    raw_status = track.get("st", track.get("status", 0))
    try:
        status = int(raw_status)
    except (TypeError, ValueError):
        status = 0
    return PlaylistEntry(
        song_id=int(song_id) if song_id is not None else 0,
        title=str(title),
        artist=artist,
        album=album,
        duration_ms=int(duration or 0),
        status=status,
    )


def entries_from_details(details: list) -> list[PlaylistEntry]:
    """直接从客户端 TrackDetail 构造（避免 as_meta → 再解析的键名往返丢失字段）。"""
    out: list[PlaylistEntry] = []
    seen: set[int] = set()
    for detail in details:
        song_id = int(getattr(detail, "song_id", 0) or 0)
        if not song_id or song_id in seen:
            continue
        seen.add(song_id)
        out.append(PlaylistEntry(
            song_id=song_id,
            title=str(getattr(detail, "title", "") or ""),
            artist=str(getattr(detail, "artist", "") or ""),
            album=str(getattr(detail, "album", "") or ""),
            duration_ms=int(getattr(detail, "duration_ms", 0) or 0),
            status=int(getattr(detail, "status", 0) or 0),
        ))
    return out


def entries_from_tracks(tracks: list[dict]) -> list[PlaylistEntry]:
    out: list[PlaylistEntry] = []
    seen: set[int] = set()
    for track in tracks:
        entry = entry_from_track(track)
        if not entry.song_id or entry.song_id in seen:
            continue
        seen.add(entry.song_id)
        out.append(entry)
    return out


def load_entries_from_json(path: Path | str) -> list[PlaylistEntry]:
    """离线歌单快照（无 Cookie 时验证、以及同步记录存档复用同一格式）。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        for key in ("songs", "tracks", "playlist"):
            if key in data:
                data = data[key]
                break
    if not isinstance(data, list):
        raise ValueError("歌单 JSON 需为歌曲数组，或 {songs|tracks|playlist: [...]}")
    return entries_from_tracks(data)


def compute_diff(prev: dict[int, dict], entries: list[PlaylistEntry]) -> DiffResult:
    """对比上次本地状态（DB.snapshot()）与本次云端快照。"""
    diff = DiffResult()
    cloud_ids = {e.song_id for e in entries}
    for entry in entries:
        before = prev.get(entry.song_id)
        bucket_before = before.get("bucket") if before else None
        if entry.invalid:
            diff.invalid.append(entry.song_id)
        elif bucket_before is None:
            diff.added.append(entry.song_id)
        elif bucket_before == "unliked":
            diff.restored.append(entry.song_id)
        else:
            diff.kept.append(entry.song_id)
    for song_id, row in prev.items():
        bucket_before = (row or {}).get("bucket")
        if bucket_before in ("like", "invalid") and song_id not in cloud_ids:
            diff.removed.append(song_id)
    return diff


def _bucket_for(entry: PlaylistEntry, bucket_before: str | None) -> tuple[str, str | None]:
    """决定本次落库的 (bucket, marker)。"""
    if entry.invalid:
        return "invalid", "!"
    if bucket_before is None:
        return "like", "+"
    if bucket_before == "unliked":
        return "like", None  # 误触取消后又收藏：恢复，不算新增
    if bucket_before == "invalid":
        return "like", None  # 版权恢复
    return "like", None


def apply_sync(db: DB, entries: list[PlaylistEntry], invalid_at_bottom: bool = True) -> DiffResult:
    """把云端快照落到本地三栏状态（顺序 = 云端顺序，失效置底），返回差异。"""
    prev = db.snapshot()
    diff = compute_diff(prev, entries)

    valid = [e for e in entries if not e.invalid]
    invalid = [e for e in entries if e.invalid]
    order = (valid + invalid) if invalid_at_bottom else list(entries)

    for position, entry in enumerate(order):
        db.upsert_song(entry.as_meta())
        bucket_before = (prev.get(entry.song_id) or {}).get("bucket")
        bucket, marker = _bucket_for(entry, bucket_before)
        db.set_playlist_state(entry.song_id, bucket, position, marker)

    # 云端已移除 → 归入「已取消喜欢」，本地文件一律保留
    for song_id in diff.removed:
        db.set_playlist_state(song_id, "unliked", 0, "-")

    db.set_meta("last_sync_at", datetime.now().isoformat(timespec="seconds"))
    db.commit()
    return diff


def save_snapshot(dir_path: Path, entries: list[PlaylistEntry], name: str) -> Path:
    """保存本次云端快照（原始云端顺序），用于追溯与离线复现。"""
    dir_path.mkdir(parents=True, exist_ok=True)
    out = dir_path / name
    out.write_text(
        json.dumps([e.as_meta() for e in entries], ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return out
