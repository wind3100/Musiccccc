"""NeteaseExecutor 测试（全部离线，用桩客户端）。"""
from pathlib import Path

import pytest

from nm.downloaders import ExecutorError, NeteaseExecutor
from nm.match import pick_best, quality_of
from nm.netease import PlayUrl, TrackDetail


class StubClient:
    def __init__(self, details=None, url=None, search_items=None, session=None):
        self._details = {d.song_id: d for d in (details or [])}
        self._url = url
        self._search_items = search_items or []
        self.session = session
        self.play_url_calls = []

    def song_details(self, ids, chunk=500):
        return {i: self._details[i] for i in ids if i in self._details}

    def play_url(self, song_id, level="lossless"):
        self.play_url_calls.append((song_id, level))
        if callable(self._url):
            return self._url(song_id)
        return self._url

    def search_songs(self, keyword, limit=10):
        return self._search_items


def detail(song_id=1, title="晴天", duration=8000, artist="周杰伦", album="叶惠美", status=0):
    return TrackDetail(song_id=song_id, title=title, artist=artist, album=album,
                       duration_ms=duration, status=status)


def lossless_url(size=30_000_000, br=900_000):
    return PlayUrl(song_id=1, url="https://example.com/a.flac", fmt="flac", bitrate=br, size=size)


def lossy_url():
    return PlayUrl(song_id=1, url="https://example.com/a.mp3", fmt="mp3", bitrate=320_000, size=9_000_000)


def test_search_by_song_id_returns_lossless_candidate():
    ex = NeteaseExecutor(StubClient(details=[detail()], url=lossless_url()))
    cands = ex.search("晴天", "周杰伦", song_id=1)
    assert len(cands) == 1
    cand = cands[0]
    assert cand.fmt == "flac" and quality_of(cand.fmt) == "lossless"
    assert cand.size_bytes == 30_000_000 and cand.bitrate_kbps == 900
    assert cand.raw["id"] == 1
    assert pick_best(cands) is cand


def test_only_mp3_means_no_lossless_no_downgrade():
    ex = NeteaseExecutor(StubClient(details=[detail()], url=lossy_url()))
    cands = ex.search("晴天", "周杰伦", song_id=1)
    assert cands[0].fmt == "mp3"
    assert pick_best(cands) is None, "无可无损资源时必须返回 None（上层转审批）"


def test_missing_detail_raises():
    ex = NeteaseExecutor(StubClient(details=[], url=lossless_url()))
    with pytest.raises(ExecutorError):
        ex.search("晴天", "周杰伦", song_id=99)


def test_keyword_search_filters_by_exact_normalized_title():
    items = [
        {"id": 1, "name": "晴天", "dt": 8000, "ar": [{"name": "周杰伦"}], "al": {"name": "叶惠美"}},
        {"id": 2, "name": "晴天 (Live)", "dt": 9000, "ar": [{"name": "周杰伦"}], "al": {"name": "x"}},
        {"id": 3, "name": "晴天（Ｌｉｖｅ）", "dt": 9000, "ar": [{"name": "周杰伦"}], "al": {"name": "x"}},
    ]
    ex = NeteaseExecutor(StubClient(search_items=items, url=lossless_url()))
    cands = ex.search("晴天", "周杰伦")  # 无 song_id → 关键词通路
    assert [c.title for c in cands] == ["晴天"], "Live 版本必须被严格匹配挡掉"


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.status_code = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, size):
        for i in range(0, len(self.payload), size):
            yield self.payload[i:i + size]


class FakeSession:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.requested = []

    def get(self, url, stream=False, timeout=None):
        self.requested.append(url)
        return FakeResponse(self.payload)


def test_fetch_streams_file(tmp_path):
    payload = b"FLAC" * 1000
    session = FakeSession(payload)
    ex = NeteaseExecutor(StubClient(details=[detail()], url=lossless_url(), session=session))
    cand = ex.search("晴天", "周杰伦", song_id=1)[0]
    out = ex.fetch(cand, tmp_path / "dl")
    assert out.name == "1.flac"
    assert out.read_bytes() == payload
    assert session.requested == ["https://example.com/a.flac"]


def test_fetch_without_url_raises(tmp_path):
    ex = NeteaseExecutor(StubClient(details=[detail()],
                                    url=PlayUrl(song_id=1, url="", fmt="", bitrate=0, size=0),
                                    session=FakeSession(b"")))
    cand = ex.search("晴天", "周杰伦", song_id=1)[0]
    with pytest.raises(ExecutorError):
        ex.fetch(cand, tmp_path / "dl")


def test_build_executors_requires_cookie_for_netease(tmp_path):
    from nm.config import Config, DownloadRules, MatchRules, Naming, NeteaseConfig, Paths
    from nm.downloaders import build_executors

    cfg = Config(
        root=tmp_path,
        paths=Paths(library=tmp_path / "l", staging=tmp_path / "s", data=tmp_path / "d"),
        naming=Naming(), match=MatchRules(),
        download=DownloadRules(executors=("ne",)),
        netease=NeteaseConfig(cookie=""),
        raw={"executors": {"ne": {"kind": "netease"}}},
    )
    with pytest.raises(ExecutorError):
        build_executors(cfg)

    cfg2 = Config(
        root=tmp_path,
        paths=cfg.paths, naming=Naming(), match=MatchRules(),
        download=DownloadRules(executors=("ne",)),
        netease=NeteaseConfig(cookie="MUSIC_U=x"),
        raw={"executors": {"ne": {"kind": "netease"}}},
    )
    execs = build_executors(cfg2)
    assert len(execs) == 1 and isinstance(execs[0], NeteaseExecutor)
    assert execs[0].name == "ne"
