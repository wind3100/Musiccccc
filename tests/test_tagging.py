import wave
from pathlib import Path

import pytest
from mutagen.id3 import APIC, TALB, TIT2, TPE1, TXXX, USLT
from mutagen.wave import WAVE

from nm.tagging import (
    TagError,
    TrackMeta,
    clear_all_tags,
    extract_embedded_lyrics,
    probe,
    process,
    residual_tags,
    verify_only_allowed,
    write_sidecar_lrc,
    write_tags,
)

EMBEDDED_LRC = "[00:01.00]内嵌歌词第一行"
NETEASE_LRC = "[00:02.00]云端歌词第一行"


def make_wav(path: Path, seconds: float = 1.0, rate: int = 44100) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return path


def dirty_wav(path: Path) -> Path:
    """塞满脏标签：标题/艺术家/专辑/超长 TXXX/封面/内嵌歌词。"""
    w = WAVE(str(path))
    w.add_tags()
    w.tags.add(TIT2(encoding=3, text="下载器乱写的标题"))
    w.tags.add(TPE1(encoding=3, text="下载器乱写的艺术家"))
    w.tags.add(TALB(encoding=3, text="下载器乱写的专辑"))
    w.tags.add(TXXX(encoding=3, desc="SOURCE", text="tracker.example"))
    w.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="cover", data=b"\xff\xd8\xff\xe0fake"))
    w.tags.add(USLT(encoding=3, lang="chi", desc="", text=EMBEDDED_LRC))
    w.save()
    return path


def test_probe_duration_of_generated_wav(tmp_path):
    info = probe(make_wav(tmp_path / "a.wav", seconds=2.0))
    assert info.fmt == "wav"
    assert 1900 <= info.duration_ms <= 2100
    assert info.quality == "lossless"
    assert info.bitrate_kbps and info.bitrate_kbps > 100


def test_extract_embedded_lyrics(tmp_path):
    p = dirty_wav(make_wav(tmp_path / "b.wav"))
    assert extract_embedded_lyrics(p) == EMBEDDED_LRC


def test_clear_all_tags_removes_everything(tmp_path):
    p = dirty_wav(make_wav(tmp_path / "c.wav"))
    removed = clear_all_tags(p)
    assert removed, "应当报告被清除的标签键"
    assert residual_tags(p) == [], "清完后不得有任何残留标签/封面"


def test_process_clears_then_writes_only_four_fields(tmp_path):
    p = dirty_wav(make_wav(tmp_path / "d.wav"))
    res = process(p, TrackMeta(title="晴天", artist="周杰伦", album="叶惠美", song_id=1))
    assert res.lyrics_source == "embedded"
    assert res.sidecar is not None and res.sidecar.suffix == ".lrc"

    w = WAVE(str(p))
    # 只剩四个规定字段，脏数据与封面全部消失
    assert str(w.tags["TIT2"]) == "晴天"
    assert str(w.tags["TPE1"]) == "周杰伦"
    assert str(w.tags["TALB"]) == "叶惠美"
    assert w.tags.getall("APIC") == []
    assert w.tags.getall("TXXX") == []
    assert verify_only_allowed(p) == []


def test_process_prefers_netease_lyrics(tmp_path):
    p = dirty_wav(make_wav(tmp_path / "e.wav"))
    res = process(p, TrackMeta(title="夜曲", artist="周杰伦", album="十一月的萧邦"),
                  lyrics=NETEASE_LRC)
    assert res.lyrics_source == "netease"
    assert res.sidecar.read_text(encoding="utf-8") == NETEASE_LRC


def test_process_without_any_lyrics_does_not_break(tmp_path):
    p = make_wav(tmp_path / "f.wav")
    res = process(p, TrackMeta(title="纯音乐", artist=""))
    assert res.lyrics_source == "none"
    assert res.sidecar is None
    assert verify_only_allowed(p) == []


def test_write_tags_then_sidecar_for_wav(tmp_path):
    p = make_wav(tmp_path / "g.wav")
    write_tags(p, TrackMeta(title="t", artist="a", album="al"))
    side = write_sidecar_lrc(p, NETEASE_LRC)
    assert side.name == "g.lrc"
    assert side.exists()


def test_unsupported_audio_raises(tmp_path):
    p = tmp_path / "notaudio.bin"
    p.write_bytes(b"not audio")
    with pytest.raises(TagError):
        probe(p)
