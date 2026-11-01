"""元数据洁癖：全清后补标签。

需求文档 §文件处理与命名：
1. 文件名严格由歌单元数据生成（见 naming.build_filename），不看下载文件自带信息
2. 对所有音频格式先彻底清除全部标签、封面、隐藏帧，再统一回写
   标题 / 艺术家 / 专辑 / 歌词 四个字段，内容同样来自歌单信息
3. 歌词：内嵌歌词先提取缓存 → 全清后回写；无内嵌则用网易云 LRC；都没有就跳过
   不支持原生歌词字段的格式（wav/aiff/ape 等）生成同名 .lrc 侧车文件
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import mutagen
from mutagen.aiff import AIFF
from mutagen.apev2 import APEv2, APETextValue
from mutagen.flac import FLAC
from mutagen.id3 import ID3, TALB, TIT2, TPE1, USLT
from mutagen.monkeysaudio import MonkeysAudio
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE

from .match import format_of, quality_of

ALLOWED_FIELDS = ("title", "artist", "album", "lyrics")

# 原生支持歌词字段的容器
NATIVE_LYRICS = {"flac", "mp3", "m4a", "ogg", "opus"}
# 无原生歌词字段（或播放器普遍不识别）→ 生成同名 .lrc 侧车
SIDECAR_LYRICS = {"wav", "aiff", "aif", "ape", "dsf"}

_VORBIS = (FLAC, OggVorbis, OggOpus)
_ID3 = (MP3, WAVE, AIFF)

_LYRICS_KEYS_VORBIS = ("lyrics", "unsyncedlyrics", "syncedlyrics", "unsynced lyrics")


class TagError(RuntimeError):
    """标签处理失败（会升级为人工审批）。"""


@dataclass
class AudioInfo:
    fmt: str
    duration_ms: int = 0
    bitrate_kbps: int | None = None
    size_bytes: int = 0
    quality: str = "unknown"


@dataclass
class TrackMeta:
    title: str
    artist: str = ""
    album: str = ""
    lyrics: str = ""
    song_id: int | None = None


@dataclass
class TagResult:
    info: AudioInfo
    lyrics_source: str = "none"  # netease | embedded | none
    sidecar: Path | None = None
    cleared: list[str] = field(default_factory=list)  # 清除掉的标签键（诊断用）


def probe(path: Path | str) -> AudioInfo:
    """读取音频真实信息（时长/码率），用于匹配与假无损判断。"""
    path = Path(path)
    fmt = format_of(path)
    audio = mutagen.File(str(path))
    if audio is None or audio.info is None:
        raise TagError(f"无法识别的音频文件: {path.name}")
    info = audio.info
    duration_ms = int(round(float(getattr(info, "length", 0) or 0) * 1000))
    raw_bitrate = getattr(info, "bitrate", None)
    bitrate = int(round(raw_bitrate / 1000)) if raw_bitrate else None
    size = path.stat().st_size
    if not bitrate and duration_ms:
        bitrate = int(size * 8 / (duration_ms / 1000.0) / 1000)
    return AudioInfo(fmt=fmt, duration_ms=duration_ms, bitrate_kbps=bitrate,
                     size_bytes=size, quality=quality_of(fmt))


def _tag_keys(audio) -> list[str]:
    tags = getattr(audio, "tags", None)
    if tags is None:
        return []
    try:
        return [str(k) for k in tags.keys()]
    except TypeError:
        return [str(k) for k in list(tags)]


def extract_embedded_lyrics(path: Path | str) -> str:
    """全清前提取内嵌歌词（缓存后回写）。"""
    audio = mutagen.File(str(path))
    if audio is None:
        return ""
    tags = getattr(audio, "tags", None)
    if tags is None:
        return ""
    if isinstance(audio, _VORBIS):
        for key in _LYRICS_KEYS_VORBIS:
            if key in tags:
                value = tags[key]
                return value[0] if isinstance(value, list) and value else str(value)
        return ""
    if isinstance(audio, _ID3):
        frames = [f for f in tags.getall("USLT")]
        return frames[0].text if frames else ""
    if isinstance(audio, MonkeysAudio):
        if "Lyrics" in tags:
            return str(tags["Lyrics"])
        return ""
    if isinstance(audio, MP4):
        value = tags.get("\xa9lyr")
        if isinstance(value, list) and value:
            return str(value[0])
        return ""
    return ""


def clear_all_tags(path: Path | str) -> list[str]:
    """彻底清除全部标签、封面、隐藏帧；返回被清除的标签键（用于日志）。"""
    path = Path(path)
    audio = mutagen.File(str(path))
    if audio is None:
        raise TagError(f"无法识别的音频文件: {path.name}")
    removed = _tag_keys(audio)

    if isinstance(audio, FLAC):
        audio.clear_pictures()
        audio.delete()
        audio.save()
    elif isinstance(audio, _VORBIS):
        audio.delete()
        audio.save()
    elif isinstance(audio, _ID3):
        try:
            audio.delete()
        except Exception:  # 无标签时 delete 可能抛错，视为已干净
            pass
    elif isinstance(audio, MonkeysAudio):
        audio.delete()
    elif isinstance(audio, MP4):
        audio.delete()

    # 旁路标签：APEv2 残留一并清掉（部分 MP3/APE 文件会带）
    try:
        APEv2(str(path)).delete()
    except Exception:
        pass
    return removed


def residual_tags(path: Path | str) -> list[str]:
    """全清后复核：还有没有任何残留标签/封面。"""
    audio = mutagen.File(str(path))
    if audio is None:
        return ["<unreadable>"]
    leftovers = _tag_keys(audio)
    if isinstance(audio, FLAC) and audio.pictures:
        leftovers.append("<picture>")
    tags = getattr(audio, "tags", None)
    if tags is not None:
        try:
            for key in list(tags.keys()):
                if str(key).upper().startswith(("APIC", "PIC")):
                    leftovers.append("<cover>")
        except TypeError:
            pass
    return leftovers


def write_tags(path: Path | str, meta: TrackMeta) -> None:
    """回写且仅回写四个字段。"""
    path = Path(path)
    audio = mutagen.File(str(path))
    if audio is None:
        raise TagError(f"无法识别的音频文件: {path.name}")

    if isinstance(audio, FLAC):
        audio["title"] = meta.title
        audio["artist"] = meta.artist
        audio["album"] = meta.album
        if meta.lyrics:
            audio["lyrics"] = meta.lyrics
        audio.save()
    elif isinstance(audio, _VORBIS):
        audio["title"] = meta.title
        audio["artist"] = meta.artist
        audio["album"] = meta.album
        if meta.lyrics:
            audio["lyrics"] = meta.lyrics
        audio.save()
    elif isinstance(audio, _ID3):
        if audio.tags is None:
            audio.add_tags()
        audio.tags.add(TIT2(encoding=3, text=meta.title))
        audio.tags.add(TPE1(encoding=3, text=meta.artist))
        audio.tags.add(TALB(encoding=3, text=meta.album))
        if meta.lyrics:
            audio.tags.add(USLT(encoding=3, lang="chi", desc="", text=meta.lyrics))
        audio.save()
    elif isinstance(audio, MonkeysAudio):
        tags = audio.tags if audio.tags is not None else APEv2()
        tags["Title"] = APETextValue(meta.title)
        tags["Artist"] = APETextValue(meta.artist)
        tags["Album"] = APETextValue(meta.album)
        if meta.lyrics:
            tags["Lyrics"] = APETextValue(meta.lyrics)
        audio.tags = tags
        audio.save()
    elif isinstance(audio, MP4):
        audio["\xa9nam"] = [meta.title]
        audio["\xa9ART"] = [meta.artist]
        audio["\xa9alb"] = [meta.album]
        if meta.lyrics:
            audio["\xa9lyr"] = [meta.lyrics]
        audio.save()
    else:
        raise TagError(f"不支持的标签写入格式: {path.suffix}")


def verify_only_allowed(path: Path | str) -> list[str]:
    """复核「仅保留规定字段」：返回多余字段名列表（应为空）。"""
    audio = mutagen.File(str(path))
    if audio is None:
        raise TagError(f"无法识别的音频文件: {path}")
    tags = getattr(audio, "tags", None)
    if tags is None:
        return []
    keys = _tag_keys(audio)
    allowed = {
        "title", "artist", "album", "lyrics",  # vorbis
        "TIT2", "TPE1", "TALB", "USLT",        # id3（哈希键取前缀比较）
        "Title", "Artist", "Album", "Lyrics",  # apev2
        "\xa9nam", "\xa9ART", "\xa9alb", "\xa9lyr",  # mp4
    }
    extra = []
    for key in keys:
        base = re.split(r"[:\x00]", key)[0]
        if base not in allowed and key not in allowed:
            extra.append(key)
    return extra


def write_sidecar_lrc(audio_path: Path | str, lyrics: str) -> Path:
    """无原生歌词字段的格式：生成同名 .lrc 侧车文件。"""
    audio_path = Path(audio_path)
    sidecar = audio_path.with_suffix(".lrc")
    if sidecar.exists():
        sidecar.unlink()
    sidecar.write_text(lyrics, encoding="utf-8")
    return sidecar


def process(path: Path | str, meta: TrackMeta, lyrics: str | None = None) -> TagResult:
    """对暂存区文件执行「全清 → 复核 → 回写四字段 → 歌词侧车」。

    lyrics 参数优先（网易云 LRC）；为空时回退到内嵌歌词。
    """
    path = Path(path)
    info = probe(path)
    fmt = info.fmt

    embedded = extract_embedded_lyrics(path)
    if lyrics:
        final_lyrics, source = lyrics, "netease"
    elif embedded:
        final_lyrics, source = embedded, "embedded"
    else:
        final_lyrics, source = "", "none"

    cleared = clear_all_tags(path)
    leftovers = residual_tags(path)
    if leftovers:
        raise TagError(f"全清后仍有残留标签: {leftovers}")

    write_tags(path, TrackMeta(title=meta.title, artist=meta.artist,
                               album=meta.album, lyrics=final_lyrics,
                               song_id=meta.song_id))
    extra = verify_only_allowed(path)
    if extra:
        raise TagError(f"回写后出现多余字段: {extra}")

    sidecar = None
    if final_lyrics and (fmt in SIDECAR_LYRICS or fmt not in NATIVE_LYRICS):
        sidecar = write_sidecar_lrc(path, final_lyrics)

    return TagResult(info=probe(path), lyrics_source=source, sidecar=sidecar, cleared=cleared)
