"""命名归一化与文件名生成。

需求文档要求：
- 严格双维匹配中的「名称归一化后完全相等」→ normalize_title
- 文件名严格依据歌单元数据生成，不取下载结果/原始名/搜索结果 → build_filename
"""
from __future__ import annotations

import re
import unicodedata

# Windows 文件名非法字符
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WS = re.compile(r"\s+")
# 归一化时抹平的标点：全角已由 NFKC 处理，这里处理常见的空格/连字符差异
_SEP = re.compile(r"[\u2010-\u2015\u2212\uff0d]")  # hyphen variants → '-'
_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")
_DOTS = re.compile(r"[.\u3002]+")
# 括号/连字符两侧的空格：纯排版差异，抹平后不影响语义
_BR_OPEN = "([\\[{\u3010\u300a\u300c"
_BR_CLOSE = ")\\]}\u3011\u300b\u300d"
_SPACE_BEFORE_OPEN = re.compile(r"\s+([" + _BR_OPEN + r"])")
_SPACE_AFTER_OPEN = re.compile(r"([" + _BR_OPEN + r"])\s+")
_SPACE_BEFORE_CLOSE = re.compile(r"\s+([" + _BR_CLOSE + r"])")
_SPACE_AFTER_CLOSE = re.compile(r"([" + _BR_CLOSE + r"])\s+")
_HYPHEN_PAD = re.compile(r"\s*-\s*")

_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def normalize_title(title: str) -> str:
    """歌曲名归一化：抹平「同曲不同写法」，但绝不做模糊匹配。

    只消除排版/编码差异，不吞掉任何语义内容（"Song (Live)" 与 "Song" 仍不相等）：
    1. NFKC（全角→半角、兼容字符统一）
    2. 去掉零宽字符
    3. 统一各类连字符/破折号为 '-'
    4. 大小写折叠（casefold）
    5. 空白折叠（含全角空格），去掉括号与 '- ' 两侧的排版空格，首尾去空白
    """
    if not title:
        return ""
    s = unicodedata.normalize("NFKC", title)
    s = _ZERO_WIDTH.sub("", s)
    s = _SEP.sub("-", s)
    s = s.casefold()
    s = s.replace("\u3000", " ")
    s = _WS.sub(" ", s).strip()
    s = _SPACE_BEFORE_OPEN.sub(r"\1", s)
    s = _SPACE_AFTER_OPEN.sub(r"\1", s)
    s = _SPACE_BEFORE_CLOSE.sub(r"\1", s)
    s = _SPACE_AFTER_CLOSE.sub(r"\1", s)
    s = _HYPHEN_PAD.sub("-", s).strip()
    return s


def normalize_artist(artist: str) -> str:
    """艺术家归一化：NFKC + 去零宽 + 空白折叠 + 分隔符统一（多艺术家用 '/ ' 连接）。"""
    if not artist:
        return ""
    s = unicodedata.normalize("NFKC", artist)
    s = _ZERO_WIDTH.sub("", s)
    s = s.replace("／", "/").replace("、", "/").replace("&", "/")
    s = re.sub(r"\s*/\s*", "/", s)
    s = _WS.sub(" ", s).strip()
    return s


def sanitize_filename(name: str, max_length: int = 120, allow_empty: bool = False) -> str:
    """生成安全的 Windows 文件名（不含扩展名）。

    allow_empty=True 用于模板中的可选字段（如专辑为空时应留空，而不是补「未命名」）。
    """
    s = unicodedata.normalize("NFC", name or "")
    s = _ZERO_WIDTH.sub("", s)
    s = _ILLEGAL.sub("_", s)
    s = _WS.sub(" ", s).strip()
    s = s.rstrip(". ")  # Windows 不允许结尾空格/点
    if not s:
        if allow_empty:
            return ""
        s = "未命名"
    stem, dot, ext = s.rpartition(".") if "." in s else ("", "", s)
    if dot and stem.upper() in _RESERVED_NAMES:
        s = f"_{stem}.{ext}"
    elif s.upper() in _RESERVED_NAMES:
        s = f"_{s}"
    if len(s) > max_length:
        s = s[:max_length].rstrip(". ")
    return s


def build_filename(meta: dict, template: str = "{title} - {artist}", max_length: int = 120) -> str:
    """按模板用歌单元数据生成文件名（不含扩展名）。

    只从歌单信息取值，绝不使用下载文件自带的标题/艺术家。
    """
    fields = {
        "title": sanitize_filename(str(meta.get("title") or ""), max_length=max_length, allow_empty=True),
        "artist": sanitize_filename(str(meta.get("artist") or ""), max_length=max_length, allow_empty=True),
        "album": sanitize_filename(str(meta.get("album") or ""), max_length=max_length, allow_empty=True),
        "song_id": str(meta.get("song_id") or ""),
        "track": str(meta.get("track") or ""),
    }
    try:
        raw = template.format(**fields)
    except (KeyError, IndexError):
        raw = f"{fields['title']} - {fields['artist']}"
    # 模板里可能残留分隔符组合，如 artist 为空时的 "标题 - "
    raw = re.sub(r"\s*-\s*$", "", raw.strip())
    raw = re.sub(r"^\s*-\s*", "", raw)
    return sanitize_filename(raw, max_length=max_length)
