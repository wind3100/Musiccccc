"""生成一套可演示的离线环境（不需要 Cookie/不需要真实下载器）。

用法: python tools/make_demo.py
生成: demo/ 目录（歌单快照、假源清单、脏源文件）+ demo.toml
之后即可跑:
    nm.cmd --config demo.toml sync --offline demo\playlist.json
    nm.cmd --config demo.toml download --only-new
    nm.cmd --config demo.toml approve --list
"""
from __future__ import annotations

import json
import shutil
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mutagen.id3 import TALB, TIT2, TPE1, TXXX, APIC, USLT  # noqa: E402
from mutagen.wave import WAVE  # noqa: E402

DEMO = ROOT / "demo"

PLAYLIST = [
    {"id": 101, "name": "晴天", "ar": [{"name": "周杰伦"}], "al": {"name": "叶惠美"}, "dt": 8000, "st": 0},
    {"id": 102, "name": "夜曲", "ar": [{"name": "周杰伦"}], "al": {"name": "十一月的萧邦"}, "dt": 8000, "st": 0},
    {"id": 103, "name": "七里香", "ar": [{"name": "周杰伦"}], "al": {"name": "七里香"}, "dt": 8000, "st": 0},
    {"id": 104, "name": "稻香", "ar": [{"name": "周杰伦"}], "al": {"name": "魔杰座"}, "dt": 8000, "st": 0},
    {"id": 105, "name": "告白气球", "ar": [{"name": "周杰伦"}], "al": {"name": "周杰伦的床边故事"}, "dt": 8000, "st": 0},
    {"id": 106, "name": "已下架的歌", "ar": [{"name": "某歌手"}], "al": {"name": "某专辑"}, "dt": 8000, "st": -200},
]


def make_wav(path: Path, seconds: float, rate: int = 44100, channels: int = 2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds) * channels)
    return path


def dirty(path: Path, title: str, artist: str, album: str, lyrics: str = "") -> Path:
    a = WAVE(str(path))
    a.add_tags()
    a.tags.add(TIT2(encoding=3, text=title))
    a.tags.add(TPE1(encoding=3, text=artist))
    a.tags.add(TALB(encoding=3, text=album))
    a.tags.add(TXXX(encoding=3, desc="SOURCE", text="downloader.example/search/123"))
    a.tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="cover",
                    data=b"\xff\xd8\xff\xe0" + b"\x00" * 64))
    if lyrics:
        a.tags.add(USLT(encoding=3, lang="chi", desc="", text=lyrics))
    a.save()
    return path


def main() -> int:
    if DEMO.exists():
        shutil.rmtree(DEMO)
    src = DEMO / "source"

    good = dirty(make_wav(src / "track01.flac.wav", 8.0), "track01", "未知艺术家", "未知专辑")
    live = dirty(make_wav(src / "track03.wav", 8.0), "七里香 (Live)", "周杰伦", "七里香")
    long_one = dirty(make_wav(src / "track04_long.wav", 20.0), "稻香", "周杰伦", "魔杰座")
    lyric_song = dirty(make_wav(src / "track05.wav", 8.0), "告白气球", "周杰伦",
                       "周杰伦的床边故事", lyrics="[00:01.00]内嵌歌词：告白气球\n[00:05.00]第二行")

    manifest = {
        "songs": [
            {"id": "s1", "title": "晴天", "duration_ms": 8000, "format": "wav", "file": "source/track01.flac.wav"},
            {"id": "s2", "title": "夜曲", "duration_ms": 8000, "format": "mp3", "file": ""},
            {"id": "s3", "title": "七里香 (Live)", "duration_ms": 8000, "format": "wav", "file": "source/track03.wav"},
            {"id": "s4", "title": "稻香", "duration_ms": 8000, "format": "wav", "file": "source/track04_long.wav"},
            {"id": "s5", "title": "告白气球", "duration_ms": 8000, "format": "wav", "file": "source/track05.wav"},
        ]
    }
    (DEMO / "playlist.json").write_text(json.dumps(PLAYLIST, ensure_ascii=False, indent=1), encoding="utf-8")
    (DEMO / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    (ROOT / "demo.toml").write_text(
        """# 演示配置：不用 Cookie、不联网，只用本地假源
[paths]
library = "demo/library"
staging = "demo/staging"
data = "demo/data"

[download]
executors = ["fake"]
search_interval_s = 0
download_interval_s = 0
max_consecutive_failures = 99

[executors.fake]
kind = "fake"
manifest = "demo/manifest.json"
""",
        encoding="utf-8",
    )
    print(f"demo 已生成: {DEMO}")
    print("下一步:")
    print("  nm.cmd --config demo.toml sync --offline demo\\playlist.json")
    print("  nm.cmd --config demo.toml download --only-new")
    print("  nm.cmd --config demo.toml approve --list")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
