"""端到端：dir 执行器（洛雪下载目录式源）走完整流水线。"""
import wave
from pathlib import Path

import pytest
from mutagen.id3 import TIT2, TPE1
from mutagen.wave import WAVE

from nm.config import Config, DownloadRules, MatchRules, Naming, NeteaseConfig, Paths
from nm.db import DB
from nm.downloaders import DirExecutor
from nm.pipeline import Pipeline
from nm.sync import apply_sync, entries_from_tracks


def make_wav(path: Path, seconds: float, rate: int = 44100, channels: int = 2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds) * channels)
    return path


def tagged(path: Path, title: str, artist: str) -> Path:
    a = WAVE(str(path))
    a.add_tags()
    a.tags.add(TIT2(encoding=3, text=title))
    a.tags.add(TPE1(encoding=3, text=artist))
    a.save()
    return path


PLAYLIST = [
    {"id": 201, "name": "晴天", "ar": [{"name": "周杰伦"}], "al": {"name": "叶惠美"}, "dt": 8000, "st": 0},
    {"id": 202, "name": "夜曲", "ar": [{"name": "周杰伦"}], "al": {"name": "十一月的萧邦"}, "dt": 8000, "st": 0},
    {"id": 203, "name": "缺席的歌", "ar": [{"name": "周杰伦"}], "al": {"name": "x"}, "dt": 8000, "st": 0},
]


@pytest.fixture()
def env(tmp_path):
    cfg = Config(
        root=tmp_path,
        paths=Paths(library=tmp_path / "library", staging=tmp_path / "staging", data=tmp_path / "data"),
        naming=Naming(),
        match=MatchRules(duration_tolerance_ms=1000, min_lossless_bitrate_kbps=600),
        download=DownloadRules(search_interval_s=0, download_interval_s=0,
                               window_max_requests=1000, max_consecutive_failures=99,
                               executors=("luoxue",)),
        netease=NeteaseConfig(),
        invalid_at_bottom=True,
        raw={"executors": {"luoxue": {"kind": "dir", "path": "dl"}}},
    )
    cfg.ensure_dirs()
    dl = tmp_path / "dl"
    tagged(make_wav(dl / "x1.wav", 8.0), "晴天", "周杰伦")      # 正常
    tagged(make_wav(dl / "x2.wav", 20.0), "夜曲", "周杰伦")     # 时长不符
    db = DB(cfg.paths.db_file)
    apply_sync(db, entries_from_tracks(PLAYLIST))
    yield cfg, db, DirExecutor(dl, name="luoxue")
    db.close()


def test_dir_source_full_pipeline(env):
    cfg, db, executor = env
    summary = Pipeline(cfg, db, [executor], netease=None, fetch_lyrics=False).run()

    downloaded = [o for o in summary.outcomes if o.status == "downloaded"]
    assert [o.song_id for o in downloaded] == [201], summary.lines()

    files = [p.name for p in cfg.paths.library.iterdir() if p.is_file()]
    assert files == ["晴天 - 周杰伦.wav"], files

    kinds = {a["song_id"]: a["kind"] for a in db.pending_approvals()}
    # 202 名称对但时长 20s vs 8s → 时长不匹配；203 目录里根本没有 → 搜不到候选
    assert kinds == {202: "duration_mismatch", 203: "no_match"}, kinds


def test_dir_source_respects_local_sovereignty(env):
    cfg, db, executor = env
    Pipeline(cfg, db, [executor], netease=None, fetch_lyrics=False).run()
    # 再次运行：已有文件不覆盖、不重复落库
    summary = Pipeline(cfg, db, [executor], netease=None, fetch_lyrics=False).run()
    assert summary.count("downloaded") == 0
    assert len([p for p in cfg.paths.library.iterdir() if p.is_file()]) == 1
    assert len(db.all_files()) == 1
