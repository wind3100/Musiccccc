"""严格双维匹配 + 音质闸门。

需求文档：
- 仅当「歌曲名称归一化后完全相等」且「时长差值在容差范围内」才算匹配成功；
  艺术家/专辑不参与判定，缺失时用歌单信息补全。
- 音质：无损优先（FLAC/WAV/APE），无损不可得时不自动降级 320k，转人工审批。
- 警惕假无损：靠时长与文件大小（码率）判断。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .naming import normalize_title

LOSSLESS_FORMATS = {"flac", "wav", "ape", "aiff", "aif", "dsf"}
LOSSY_FORMATS = {"mp3", "m4a", "aac", "ogg", "opus", "wma"}

MATCH_OK = "ok"
NAME_MISMATCH = "name_mismatch"
DURATION_MISMATCH = "duration_mismatch"
DURATION_UNKNOWN = "duration_unknown"


@dataclass
class Candidate:
    """一个下载候选（来自某个执行器的搜索结果）。"""
    title: str
    duration_ms: int = 0
    artist: str = ""
    album: str = ""
    fmt: str = ""
    size_bytes: int = 0
    path: str | None = None
    source: str = ""
    bitrate_kbps: int | None = None
    raw: dict = field(default_factory=dict)

    def resolve_bitrate(self) -> int | None:
        if self.bitrate_kbps:
            return self.bitrate_kbps
        if self.duration_ms and self.size_bytes:
            return int(self.size_bytes * 8 / (self.duration_ms / 1000.0) / 1000)
        return None


@dataclass
class MatchResult:
    ok: bool
    reason: str = MATCH_OK
    detail: str = ""
    name_equal: bool = False
    duration_delta_ms: int | None = None


def format_of(path_or_name: str) -> str:
    return Path(path_or_name).suffix.lower().lstrip(".")


def quality_of(fmt: str) -> str:
    fmt = (fmt or "").lower().lstrip(".")
    if fmt in LOSSLESS_FORMATS:
        return "lossless"
    if fmt in LOSSY_FORMATS:
        return "lossy"
    return "unknown"


def strict_match(target_title: str, target_duration_ms: int, cand: Candidate,
                 tolerance_ms: int = 2000) -> MatchResult:
    """严格双维匹配：名称归一化后完全相等 且 时长差在容差内。"""
    name_equal = normalize_title(cand.title) == normalize_title(target_title)
    if not name_equal:
        return MatchResult(
            ok=False, reason=NAME_MISMATCH,
            detail=f"名称不匹配: 歌单《{target_title}》 vs 候选《{cand.title}》",
            name_equal=False,
        )
    if not cand.duration_ms:
        return MatchResult(ok=False, reason=DURATION_UNKNOWN,
                           detail="候选时长未知，无法验证", name_equal=True)
    if not target_duration_ms:
        return MatchResult(ok=False, reason=DURATION_UNKNOWN,
                           detail="歌单时长缺失，无法验证", name_equal=True)
    delta = abs(int(cand.duration_ms) - int(target_duration_ms))
    if delta > tolerance_ms:
        return MatchResult(
            ok=False, reason=DURATION_MISMATCH,
            detail=f"时长差 {delta}ms 超出容差 {tolerance_ms}ms",
            name_equal=True, duration_delta_ms=delta,
        )
    return MatchResult(ok=True, reason=MATCH_OK,
                       detail=f"名称一致，时长差 {delta}ms", name_equal=True, duration_delta_ms=delta)


def is_suspect_fake_lossless(fmt: str, bitrate_kbps: int | None, min_kbps: int = 600) -> bool:
    """假无损警戒：无损容器但码率过低。"""
    if quality_of(fmt) != "lossless":
        return False
    if not bitrate_kbps:
        return False
    return bitrate_kbps < min_kbps


def pick_best(cands: list[Candidate], preferred: tuple[str, ...] = ("flac", "wav", "ape")) -> Candidate | None:
    """音质优先级：无损优先；同格式按文件大小降序。绝不在无损缺失时自动降级。"""
    rank = {f: i for i, f in enumerate(preferred)}

    def key(c: Candidate) -> tuple:
        lossless = 0 if quality_of(c.fmt) == "lossless" else 1
        return (lossless, rank.get(c.fmt.lower(), len(rank)), -int(c.size_bytes or 0))

    lossless_cands = [c for c in cands if quality_of(c.fmt) == "lossless"]
    if lossless_cands:
        return sorted(lossless_cands, key=key)[0]
    return None  # 无损不可得 → 调用方转人工审批，禁止自动降级


def has_lossless(cands: list[Candidate]) -> bool:
    return any(quality_of(c.fmt) == "lossless" for c in cands)
