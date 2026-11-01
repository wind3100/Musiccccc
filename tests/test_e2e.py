"""端到端：离线歌单快照 + 本地假源 → 同步 → 下载 → 清洗 → 落库 → 审批队列。

不联网、不依赖 Cookie，验证完整链路与所有熔断分支。
"""
import json
import wave
from pathlib import Path

import pytest
from mutagen.wave import WAVE
from mutagen.id3 import TIT2, TPE1, USLT

from nm.config import Config, DownloadRules, MatchRules, Naming, NeteaseConfig, Paths
from nm.db import DB
from nm.downloaders import FakeExecutor
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


def dirty(path: Path, title: str, artist: str, lyrics: str = "") -> Path:
    a = WAVE(str(path))
    a.add_tags()
    a.tags.add(TIT2(encoding=3, text=title))
    a.tags.add(TPE1(encoding=3, text=artist))
    if lyrics:
        a.tags.add(USLT(encoding=3, lang="chi", desc="", text=lyrics))
    a.save()
    return path


def make_cfg(root: Path) -> Config:
    return Config(
        root=root,
        paths=Paths(library=root / "library", staging=root / "staging", data=root / "data"),
        naming=Naming(),
        match=MatchRules(duration_tolerance_ms=1000, min_lossless_bitrate_kbps=600),
        download=DownloadRules(search_interval_s=0, download_interval_s=0,
                               window_max_requests=1000, window_seconds=60,
                               max_consecutive_failures=99, executors=("fake",)),
        netease=NeteaseConfig(),
        invalid_at_bottom=True,
    )


PLAYLIST = [
    {"id": 101, "name": "晴天", "ar": [{"name": "周杰伦"}], "al": {"name": "叶惠美"}, "dt": 8000, "st": 0},
    {"id": 102, "name": "夜曲", "ar": [{"name": "周杰伦"}], "al": {"name": "十一月的萧邦"}, "dt": 8000, "st": 0},
    {"id": 103, "name": "七里香", "ar": [{"name": "周杰伦"}], "al": {"name": "七里香"}, "dt": 8000, "st": 0},
    {"id": 104, "name": "稻香", "ar": [{"name": "周杰伦"}], "al": {"name": "魔杰座"}, "dt": 8000, "st": 0},
    {"id": 105, "name": "告白气球", "ar": [{"name": "周杰伦"}], "al": {"name": "周杰伦的床边故事"}, "dt": 8000, "st": 0},
    {"id": 106, "name": "已下架的歌", "ar": [{"name": "某歌手"}], "al": {"name": "某专辑"}, "dt": 8000, "st": -200},
]


@pytest.fixture()
def env(tmp_path):
    root = tmp_path
    cfg = make_cfg(root)
    cfg.ensure_dirs()

    src = root / "source"
    good = dirty(make_wav(src / "晴天.flac.wav", 8.0), "文件名里的脏标题", "脏艺术家")
    lyric_song = dirty(make_wav(src / "告白气球.wav", 8.0), "告白气球", "周杰伦",
                       lyrics="[00:01.00]内嵌的告白气球歌词")
    wrong_len = dirty(make_wav(src / "稻香_long.wav", 20.0), "稻香", "周杰伦")
    live = dirty(make_wav(src / "七里香live.wav", 8.0), "七里香 (Live)", "周杰伦")

    manifest = {
        "songs": [
            {"id": "a1", "title": "晴天", "duration_ms": 8000, "format": "wav", "file": str(good)},
            # 只有有损资源 → 必须熔断，绝不自动降级
            {"id": "a2", "title": "夜曲", "duration_ms": 8000, "format": "mp3", "file": ""},
            # 名称带 Live 后缀 → 严格匹配失败
            {"id": "a3", "title": "七里香 (Live)", "duration_ms": 8000, "format": "wav", "file": str(live)},
            # 时长差 12s → 超容差
            {"id": "a4", "title": "稻香", "duration_ms": 8000, "format": "wav", "file": str(wrong_len)},
            {"id": "a5", "title": "告白气球", "duration_ms": 8000, "format": "wav",
             "file": str(lyric_song)},
        ]
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    db = DB(cfg.paths.db_file)
    entries = entries_from_tracks(PLAYLIST)
    apply_sync(db, entries)
    yield cfg, db, FakeExecutor(manifest_path)
    db.close()


def test_sync_three_buckets(env):
    cfg, db, _ = env
    like = db.list_bucket("like")
    invalid = db.list_bucket("invalid")
    assert [r["song_id"] for r in like] == [101, 102, 103, 104, 105]
    assert [r["song_id"] for r in invalid] == [106]
    assert all(r["marker"] == "+" for r in like)


def test_pipeline_end_to_end(env):
    cfg, db, executor = env
    pipeline = Pipeline(cfg, db, [executor], netease=None, fetch_lyrics=False)
    summary = pipeline.run()

    # 晴天：正常落库
    assert summary.count("downloaded") == 2, summary.lines()  # 晴天 + 告白气球
    # 夜曲（无无损）/ 七里香（名称不符）/ 稻香（时长不符）→ 全部进审批
    kinds = {a["kind"] for a in db.pending_approvals()}
    assert "no_lossless" in kinds
    assert "name_mismatch" in kinds
    assert "duration_mismatch" in kinds
    # 失效歌曲不下载
    assert all(o.song_id != 106 for o in summary.outcomes)

    # 文件名来自歌单元数据，而非源文件名
    files = sorted(p.name for p in cfg.paths.library.iterdir() if p.is_file() and p.suffix == ".wav")
    assert "晴天 - 周杰伦.wav" in files
    assert "告白气球 - 周杰伦.wav" in files

    # 元数据洁癖：只剩四字段，脏标题被歌单信息覆盖
    target = cfg.paths.library / "晴天 - 周杰伦.wav"
    tags = WAVE(str(target)).tags
    assert str(tags["TIT2"]) == "晴天"
    assert str(tags["TPE1"]) == "周杰伦"

    # 内嵌歌词 → wav 无原生歌词字段 → 同名 .lrc 侧车
    sidecar = cfg.paths.library / "告白气球 - 周杰伦.lrc"
    assert sidecar.is_file()
    assert "内嵌的告白气球歌词" in sidecar.read_text(encoding="utf-8")

    # 处理日志可追溯
    stages = {row["stage"] for row in db.recent_jobs(50)}
    assert {"download", "tag", "move", "approval"} <= stages

    # 库文件登记进数据库 + 本地主权化（不删源）
    assert len(db.all_files()) == 2


def test_rerun_is_idempotent(env):
    cfg, db, executor = env
    Pipeline(cfg, db, [executor], netease=None, fetch_lyrics=False).run()
    before = sorted(p.name for p in cfg.paths.library.iterdir())
    pipeline = Pipeline(cfg, db, [executor], netease=None, fetch_lyrics=False)
    summary = pipeline.run()
    after = sorted(p.name for p in cfg.paths.library.iterdir())
    assert before == after, "重复跑不得产生重复文件"
    assert summary.count("downloaded") == 0
    # 已有文件的跳过；仍在审批队列的也跳过（不重复打扰）
    assert summary.count("skipped") >= 2


def test_no_staging_leftovers(env):
    cfg, db, executor = env
    Pipeline(cfg, db, [executor], netease=None, fetch_lyrics=False).run()
    leftovers = [p for p in cfg.paths.staging.rglob("*")]
    assert leftovers == [], f"暂存区必须清空: {leftovers}"
