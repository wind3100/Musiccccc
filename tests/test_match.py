from nm.match import (
    Candidate,
    MATCH_OK,
    NAME_MISMATCH,
    DURATION_MISMATCH,
    is_suspect_fake_lossless,
    pick_best,
    quality_of,
    strict_match,
)


def cand(title, duration_ms=200000, fmt="flac", size=30_000_000, **kw):
    return Candidate(title=title, duration_ms=duration_ms, fmt=fmt, size_bytes=size, **kw)


def test_match_ok_exact_name_and_duration():
    r = strict_match("晴天", 269000, cand("晴天", 269000))
    assert r.ok and r.reason == MATCH_OK


def test_match_normalized_name_variants_pass():
    r = strict_match("晴天 (Live)", 269000, cand("晴天（Ｌｉｖｅ）", 269000))
    assert r.ok, r.detail


def test_match_name_mismatch_rejected():
    r = strict_match("晴天", 269000, cand("晴天2", 269000))
    assert not r.ok and r.reason == NAME_MISMATCH


def test_match_duration_tolerance_boundary():
    assert strict_match("歌", 200000, cand("歌", 202000), tolerance_ms=2000).ok
    r = strict_match("歌", 200000, cand("歌", 202100), tolerance_ms=2000)
    assert not r.ok and r.reason == DURATION_MISMATCH


def test_match_unknown_duration_rejected():
    r = strict_match("歌", 200000, cand("歌", 0))
    assert not r.ok


def test_quality_classification():
    assert quality_of("flac") == "lossless"
    assert quality_of("ape") == "lossless"
    assert quality_of("mp3") == "lossy"
    assert quality_of("xyz") == "unknown"


def test_pick_best_prefers_lossless_then_largest():
    cands = [
        cand("歌", fmt="mp3", size=10_000_000),
        cand("歌", fmt="flac", size=25_000_000),
        cand("歌", fmt="flac", size=40_000_000),
        cand("歌", fmt="wav", size=60_000_000),
    ]
    best = pick_best(cands)
    assert best is not None and best.fmt == "flac" and best.size_bytes == 40_000_000


def test_pick_best_returns_none_without_lossless():
    # 无损不可得：禁止自动降级 320k，返回 None 由上层转人工审批
    assert pick_best([cand("歌", fmt="mp3")]) is None


def test_suspect_fake_lossless():
    assert is_suspect_fake_lossless("flac", 320, min_kbps=600)
    assert not is_suspect_fake_lossless("flac", 900, min_kbps=600)
    assert not is_suspect_fake_lossless("mp3", 320, min_kbps=600)


def test_candidate_bitrate_from_size_and_duration():
    c = Candidate(title="歌", duration_ms=200000, size_bytes=25_000_000, fmt="flac")
    assert c.resolve_bitrate() == 1000
