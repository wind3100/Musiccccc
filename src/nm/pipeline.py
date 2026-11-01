"""下载流水线：搜索 → 严格双维匹配 → 音质闸门 → 暂存下载 → 校验 → 全清补标签 → 原子落库。

任一步不确定，一律熔断进人工审批队列，绝不自动降级或模糊通过（需求文档 §下载与匹配）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from . import naming, staging, tagging
from .db import DB
from .limiter import RateLimiter
from .logutil import get_logger
from .match import (
    Candidate,
    DURATION_MISMATCH,
    DURATION_UNKNOWN,
    MatchResult,
    is_suspect_fake_lossless,
    pick_best,
    quality_of,
    strict_match,
)

STATUS_DOWNLOADED = "downloaded"
STATUS_SKIPPED = "skipped"
STATUS_APPROVAL = "approval"
STATUS_ERROR = "error"


@dataclass
class SongOutcome:
    song_id: int
    title: str
    status: str
    detail: str = ""
    path: Path | None = None


@dataclass
class RunSummary:
    outcomes: list[SongOutcome] = field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(1 for o in self.outcomes if o.status == status)

    def lines(self) -> list[str]:
        out = []
        for o in self.outcomes:
            icon = {
                STATUS_DOWNLOADED: "[落库]",
                STATUS_SKIPPED: "[跳过]",
                STATUS_APPROVAL: "[待审批]",
                STATUS_ERROR: "[失败]",
            }.get(o.status, o.status)
            where = f" -> {o.path.name}" if o.path else ""
            out.append(f"{icon} [{o.song_id}] {o.title}{where}  {o.detail}")
        return out

    def summary_line(self) -> str:
        return (f"落库 {self.count(STATUS_DOWNLOADED)}，跳过 {self.count(STATUS_SKIPPED)}，"
                f"待审批 {self.count(STATUS_APPROVAL)}，失败 {self.count(STATUS_ERROR)}")


class Pipeline:
    def __init__(self, cfg, db: DB, executors: list, netease=None, logger=None,
                 search_limiter: RateLimiter | None = None,
                 download_limiter: RateLimiter | None = None, fetch_lyrics: bool = True):
        self.cfg = cfg
        self.db = db
        self.executors = executors
        self.netease = netease
        self.log = logger or get_logger("nm.pipeline")
        d = cfg.download
        self.search_limiter = search_limiter or RateLimiter(
            min_interval_s=d.search_interval_s, window_max=d.window_max_requests,
            window_s=d.window_seconds, backoff_base_s=d.backoff_base_s,
            max_consecutive_failures=d.max_consecutive_failures)
        self.download_limiter = download_limiter or RateLimiter(
            min_interval_s=d.download_interval_s, window_max=d.window_max_requests,
            window_s=d.window_seconds, backoff_base_s=d.backoff_base_s,
            max_consecutive_failures=d.max_consecutive_failures)
        self.fetch_lyrics = fetch_lyrics

    # ---------- 选歌 ----------
    def select_songs(self, only_new: bool = False, limit: int | None = None,
                     song_ids: list[int] | None = None) -> list[dict]:
        songs = self.db.list_bucket("like")
        todo = []
        for song in songs:
            if song_ids is not None and song["song_id"] not in song_ids:
                continue
            if song["status"] < 0:
                continue  # 失效歌曲无文件可下
            if only_new and song["file_count"]:
                continue
            if self.db.is_rejected(song["song_id"]):
                continue  # 用户已拒绝，不重复打扰
            todo.append(song)
        if limit:
            todo = todo[:limit]
        return todo

    # ---------- 主流程 ----------
    def run(self, only_new: bool = False, limit: int | None = None,
            song_ids: list[int] | None = None) -> RunSummary:
        summary = RunSummary()
        songs = self.select_songs(only_new=only_new, limit=limit, song_ids=song_ids)
        self.log.info("待处理 %d 首", len(songs))
        for idx, song in enumerate(songs, 1):
            self.log.info("[%d/%d] %s - %s", idx, len(songs), song["title"], song["artist"])
            outcome = self.process_song(song)
            summary.outcomes.append(outcome)
            if self.search_limiter.tripped or self.download_limiter.tripped:
                self.log.warning("连续失败达到阈值，本轮停止（防止 IP 封禁），剩余歌曲转人工审批")
                for rest in songs[idx:]:
                    self.db.add_approval(rest["song_id"], "rate_limited",
                                         "连续请求失败触发退避熔断，本轮未处理")
                    summary.outcomes.append(SongOutcome(rest["song_id"], rest["title"],
                                                        STATUS_APPROVAL, "限速熔断"))
                break
        return summary

    def process_song(self, song: dict) -> SongOutcome:
        song_id = song["song_id"]
        title, artist = song["title"], song["artist"]
        if self.db.files_for(song_id):
            return SongOutcome(song_id, title, STATUS_SKIPPED, "本地已有文件（本地主权化，不覆盖）")
        if self.db.has_pending_approval(song_id):
            return SongOutcome(song_id, title, STATUS_SKIPPED, "已在人工审批队列（先运行 nm approve 处理）")

        reasons: list[tuple[str, str]] = []
        for ex in self.executors:
            started = time.time()
            try:
                self.search_limiter.wait()
                candidates = ex.search(title, artist, song_id=song_id)
            except Exception as exc:  # noqa: BLE001  —— 源失败：换备用源
                self.search_limiter.record_failure()
                self.db.log_job(song_id, "search", False, f"{ex.name}: {exc}", source=ex.name)
                reasons.append(("source_failed", f"{ex.name} 搜索失败: {exc}"))
                continue

            judged = [(c, strict_match(title, song["duration_ms"], c,
                                       tolerance_ms=self.cfg.match.duration_tolerance_ms))
                      for c in candidates]
            matched = [c for c, r in judged if r.ok]
            if not matched:
                self.search_limiter.record_success()
                detail = self._mismatch_detail(judged)
                self.db.log_job(song_id, "match", False, f"{ex.name}: {detail}", source=ex.name)
                reasons.append((self._match_failure_kind(judged), f"{ex.name}: {detail}"))
                continue

            best = pick_best(matched, preferred=self.cfg.download.preferred_formats)
            if best is None:
                self.search_limiter.record_success()
                fmts = ", ".join(sorted({c.fmt or "?" for c in matched}))
                self.db.log_job(song_id, "quality", False, f"{ex.name}: 仅有无损之外的资源 {fmts}",
                                source=ex.name)
                reasons.append(("no_lossless", f"{ex.name}: 无无损资源（仅有 {fmts}），不降级"))
                continue

            outcome = self._download_with(ex, song, best)
            elapsed = int((time.time() - started) * 1000)
            if outcome.status == STATUS_DOWNLOADED:
                self.search_limiter.record_success()
                self.download_limiter.record_success()
                self.db.log_job(song_id, "done", True, outcome.detail,
                                source=ex.name, elapsed_ms=elapsed)
                return outcome
            if outcome.status == STATUS_APPROVAL:
                self.db.log_job(song_id, "approval", False, outcome.detail,
                                source=ex.name, elapsed_ms=elapsed)
                return outcome
            reasons.append(("source_failed", f"{ex.name}: {outcome.detail}"))
            self.download_limiter.record_failure()

        # 所有源都失败 → 人工审批熔断
        kind, detail = self._classify_reasons(reasons)
        if not self.db.has_pending_approval(song_id):
            self.db.add_approval(song_id, kind, detail,
                                 payload={"reasons": [{"kind": k, "detail": d} for k, d in reasons]})
        return SongOutcome(song_id, title, STATUS_APPROVAL, detail)

    # ---------- 单源下载 ----------
    def _download_with(self, ex, song: dict, best: Candidate) -> SongOutcome:
        song_id = song["song_id"]
        job_dir = staging.new_job_dir(self.cfg.paths.staging, song_id)
        try:
            self.download_limiter.wait()
            try:
                raw_path = ex.fetch(best, job_dir)
            except Exception as exc:  # noqa: BLE001
                self.db.log_job(song_id, "download", False, f"{ex.name}: {exc}", source=ex.name)
                return SongOutcome(song_id, song["title"], STATUS_ERROR, f"下载失败: {exc}")
            self.db.log_job(song_id, "download", True, f"{ex.name}: {raw_path.name}", source=ex.name)

            # 1) 真实文件复核：时长（严格）+ 音质（无损）
            try:
                info = tagging.probe(raw_path)
            except tagging.TagError as exc:
                return SongOutcome(song_id, song["title"], STATUS_ERROR, f"音频无法解析: {exc}")
            delta = abs(info.duration_ms - int(song["duration_ms"]))
            if song["duration_ms"] and delta > self.cfg.match.duration_tolerance_ms:
                reason = f"文件时长差 {delta}ms 超容差（疑似错误版本）"
                self.db.log_job(song_id, "verify", False, reason, source=ex.name)
                if not self.db.has_pending_approval(song_id):
                    self.db.add_approval(song_id, DURATION_MISMATCH, reason,
                                         payload={"source": ex.name, "delta_ms": delta})
                return SongOutcome(song_id, song["title"], STATUS_APPROVAL, reason)

            if quality_of(info.fmt) != "lossless":
                reason = f"下载到的不是无损格式（{info.fmt}），拒绝自动降级"
                if not self.db.has_pending_approval(song_id):
                    self.db.add_approval(song_id, "no_lossless", reason, payload={"source": ex.name})
                return SongOutcome(song_id, song["title"], STATUS_APPROVAL, reason)

            if is_suspect_fake_lossless(info.fmt, info.bitrate_kbps,
                                        self.cfg.match.min_lossless_bitrate_kbps):
                reason = (f"疑似假无损：{info.fmt} 码率仅 {info.bitrate_kbps}kbps "
                          f"（阈值 {self.cfg.match.min_lossless_bitrate_kbps}kbps）")
                self.db.log_job(song_id, "verify", False, reason, source=ex.name)
                if not self.db.has_pending_approval(song_id):
                    self.db.add_approval(song_id, "suspect_fake_lossless", reason,
                                         payload={"source": ex.name, "bitrate": info.bitrate_kbps})
                return SongOutcome(song_id, song["title"], STATUS_APPROVAL, reason)

            # 2) 歌词：网易云 LRC（内嵌歌词由 tagging.process 兜底）
            lyrics = ""
            if self.fetch_lyrics and self.netease is not None:
                try:
                    lyrics = self.netease.lyric(song_id)
                except Exception as exc:  # noqa: BLE001
                    self.log.warning("歌词获取失败（不阻断）: %s", exc)

            # 3) 元数据洁癖：全清 → 回写四字段（只用歌单信息）
            meta = tagging.TrackMeta(
                title=song["title"], artist=song["artist"], album=song["album"],
                lyrics=lyrics, song_id=song_id)
            try:
                tag_result = tagging.process(raw_path, meta, lyrics=lyrics)
            except Exception as exc:  # noqa: BLE001
                self.db.log_job(song_id, "tag", False, f"{type(exc).__name__}: {exc}", source=ex.name)
                return SongOutcome(song_id, song["title"], STATUS_APPROVAL, f"标签处理失败: {exc}")
            self.db.log_job(song_id, "tag", True,
                            f"cleared={len(tag_result.cleared)} lyrics={tag_result.lyrics_source}",
                            source=ex.name)

            # 4) 命名只取歌单元数据 → 原子化落库
            filename = naming.build_filename(song, self.cfg.naming.template, self.cfg.naming.max_length)
            target = staging.target_path(self.cfg.paths.library, filename, info.fmt)
            staging.atomic_move(raw_path, target)
            lrc_target = None
            if tag_result.sidecar is not None:
                lrc_target = target.with_suffix(".lrc")
                staging.atomic_move(tag_result.sidecar, lrc_target)
            digest = staging.sha256_of(target)
            self.db.add_file(song_id, target, info.fmt, target.stat().st_size,
                             quality=info.quality, bitrate_kbps=info.bitrate_kbps,
                             duration_ms=info.duration_ms, sha256=digest)
            self.db.log_job(song_id, "move", True, str(target), source=ex.name)
            detail = f"{info.fmt} {info.duration_ms}ms {info.bitrate_kbps}kbps"
            if lrc_target:
                detail += " +lrc"
            return SongOutcome(song_id, song["title"], STATUS_DOWNLOADED, detail, path=target)
        finally:
            staging.cleanup_job_dir(job_dir)

    # ---------- 归因 ----------
    @staticmethod
    def _mismatch_detail(judged: list[tuple[Candidate, MatchResult]]) -> str:
        if not judged:
            return "搜索无结果"
        parts = []
        for cand, res in judged[:3]:
            parts.append(f"《{cand.title}》{cand.duration_ms}ms → {res.reason}: {res.detail}")
        return "；".join(parts)

    @staticmethod
    def _match_failure_kind(judged: list[tuple[Candidate, MatchResult]]) -> str:
        """把匹配失败拆细：搜不到 / 名称不符 / 时长不符 / 时长未知，别一律叫 no_match。"""
        if not judged:
            return "no_result"
        codes = {res.reason for _, res in judged}
        if codes == {DURATION_MISMATCH}:
            return DURATION_MISMATCH
        if codes <= {DURATION_MISMATCH, DURATION_UNKNOWN}:
            return DURATION_UNKNOWN
        return "name_mismatch"

    @staticmethod
    def _classify_reasons(reasons: list[tuple[str, str]]) -> tuple[str, str]:
        kinds = {k for k, _ in reasons}
        mismatch_kinds = {"no_result", "name_mismatch", DURATION_MISMATCH, DURATION_UNKNOWN}
        if not reasons:
            return "both_sources_failed", "没有可用执行器"
        if "no_lossless" in kinds and kinds <= mismatch_kinds | {"no_lossless"}:
            return "no_lossless", "所有源均无无损资源，禁止自动降级"
        if kinds <= {DURATION_UNKNOWN}:
            return DURATION_UNKNOWN, "所有源的候选时长缺失，无法通过时长校验"
        if kinds <= {DURATION_MISMATCH, DURATION_UNKNOWN}:
            return DURATION_MISMATCH, "所有源的候选时长都与歌单不符（疑似错误版本）"
        if kinds <= {"name_mismatch"}:
            return "name_mismatch", "所有源的候选名称都与歌单不一致（拒绝模糊匹配）"
        if kinds <= {"no_result"}:
            return "no_match", "所有源都搜不到候选"
        return "both_sources_failed", "；".join(d for _, d in reasons)
