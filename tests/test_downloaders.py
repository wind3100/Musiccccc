"""执行器测试：目录源（dir）与装配注册。"""
import wave
from pathlib import Path

import pytest
from mutagen.id3 import TIT2, TPE1
from mutagen.wave import WAVE

from nm.config import Config, DownloadRules, MatchRules, Naming, NeteaseConfig, Paths
from nm.downloaders import DirExecutor, ExecutorError, build_executors
from nm.match import strict_match, pick_best


def make_wav(path: Path, seconds: float = 2.0, rate: int = 44100, channels: int = 2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds) * channels)
    return path


def tag(path: Path, title: str = "", artist: str = "") -> Path:
    a = WAVE(str(path))
    a.add_tags()
    if title:
        a.tags.add(TIT2(encoding=3, text=title))
    if artist:
        a.tags.add(TPE1(encoding=3, text=artist))
    a.save()
    return path


def make_cfg(root: Path, executors=(), raw=None) -> Config:
    return Config(
        root=root,
        paths=Paths(library=root / "library", staging=root / "staging", data=root / "data"),
        naming=Naming(),
        match=MatchRules(),
        download=DownloadRules(executors=tuple(executors)),
        netease=NeteaseConfig(),
        invalid_at_bottom=True,
        raw=raw or {},
    )


def test_dir_executor_reads_tags_for_search(tmp_path):
    src = tmp_path / "dl"
    tag(make_wav(src / "track01.wav"), title="晴天", artist="周杰伦")
    ex = DirExecutor(src, name="luoxue")
    cands = ex.search("晴天", "周杰伦")
    assert len(cands) == 1
    cand = cands[0]
    assert (cand.title, cand.artist, cand.fmt) == ("晴天", "周杰伦", "wav")
    assert 1900 <= cand.duration_ms <= 2100
    assert cand.source == "luoxue"


def test_dir_executor_falls_back_to_filename(tmp_path):
    src = tmp_path / "dl"
    make_wav(src / "周杰伦 - 夜曲.wav", seconds=3.0)  # 无标签
    ex = DirExecutor(src)
    cands = ex.search("夜曲", "周杰伦")
    assert len(cands) == 1
    assert cands[0].title == "夜曲"
    assert cands[0].artist == "周杰伦"


def test_dir_executor_skips_unrelated_files(tmp_path):
    src = tmp_path / "dl"
    tag(make_wav(src / "a.wav"), title="晴天")
    tag(make_wav(src / "b.wav"), title="完全不同的歌")
    ex = DirExecutor(src)
    assert [c.title for c in ex.search("晴天", "")] == ["晴天"]


def test_dir_executor_recursive_and_ext_filter(tmp_path):
    src = tmp_path / "dl"
    tag(make_wav(src / "sub" / "x.wav"), title="稻香")
    (src / "notes.txt").write_text("ignore me", encoding="utf-8")
    ex = DirExecutor(src, recursive=True)
    assert [c.title for c in ex.search("稻香", "")] == ["稻香"]


def test_dir_executor_fetch_copies(tmp_path):
    src = tmp_path / "dl"
    path = tag(make_wav(src / "a.wav"), title="晴天")
    ex = DirExecutor(src)
    dest = tmp_path / "staging"
    out = ex.fetch(ex.search("晴天", "")[0], dest)
    assert out.is_file() and out.parent == dest
    assert out.read_bytes() == path.read_bytes()


def test_dir_executor_missing_dir_raises(tmp_path):
    with pytest.raises(ExecutorError):
        DirExecutor(tmp_path / "nope").search("x", "")


def test_search_result_survives_strict_match_and_pick(tmp_path):
    """目录源出来的候选必须能通过上层的严格双维匹配 + 音质闸门。"""
    src = tmp_path / "dl"
    tag(make_wav(src / "a.wav", seconds=2.0), title="晴天", artist="周杰伦")
    cand = DirExecutor(src).search("晴天", "周杰伦")[0]
    assert strict_match("晴天", 2000, cand, tolerance_ms=200).ok
    assert pick_best([cand]) is cand


def test_build_executors_supports_dir_kind(tmp_path):
    src = tmp_path / "dl"
    make_wav(src / "a.wav")
    cfg = make_cfg(tmp_path, executors=("luoxue",),
                   raw={"executors": {"luoxue": {"kind": "dir", "path": str(src)}}})
    executors = build_executors(cfg)
    assert len(executors) == 1
    assert isinstance(executors[0], DirExecutor)
    assert executors[0].name == "luoxue"


def test_build_executors_relative_dir_path_uses_root(tmp_path):
    (tmp_path / "dl").mkdir()
    cfg = make_cfg(tmp_path, executors=("d",),
                   raw={"executors": {"d": {"kind": "dir", "path": "dl"}}})
    assert build_executors(cfg)[0].root == (tmp_path / "dl").resolve()


def test_build_executors_dir_missing_path_raises(tmp_path):
    cfg = make_cfg(tmp_path, executors=("d",), raw={"executors": {"d": {"kind": "dir"}}})
    with pytest.raises(ExecutorError):
        build_executors(cfg)
