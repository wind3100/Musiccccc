"""人工审批队列（CLI 文本菜单）。

需求文档：名称/时长不匹配、无无损资源、双源失败、疑似假无损等，一律暂停并进入
人工审批队列，禁止自动下载替代音质或错误版本。软件不主动提醒——只有用户手动
运行 `approve` 时才呈现队列。

语义：
- approve → 授权下次手动 download 时重试该曲
- reject  → 不再自动尝试该曲（最终决定由用户保留/删除文件）
"""
from __future__ import annotations

import json

from .db import DB

KIND_LABELS = {
    "name_mismatch": "名称不匹配",
    "duration_mismatch": "时长不匹配",
    "duration_unknown": "时长未知",
    "no_match": "搜不到候选",
    "no_lossless": "无无损资源",
    "both_sources_failed": "双源均失败",
    "suspect_fake_lossless": "疑似假无损",
    "rate_limited": "限速熔断",
    "manual": "手动标记",
}


def describe(item: dict) -> str:
    kind = KIND_LABELS.get(item["kind"], item["kind"])
    title = item.get("title") or f"<{item['song_id']}>"
    artist = item.get("artist") or ""
    return f"[{item['approval_id']}] {title} / {artist} —— {kind}: {item['reason']}"


def run_menu(db: DB, input_fn=input, print_fn=print, limit: int | None = None) -> dict[str, int]:
    pending = db.pending_approvals()
    if limit:
        pending = pending[:limit]
    counts = {"approved": 0, "rejected": 0, "skipped": 0}
    if not pending:
        print_fn("审批队列为空。")
        return counts

    print_fn(f"待审批 {len(pending)} 条（a=同意并重试 / r=拒绝 / d=详情 / s=跳过 / q=退出；大写=应用到全部剩余）")
    approve_all = reject_all = False
    for item in pending:
        print_fn("  " + describe(item))
        if approve_all:
            db.resolve_approval(item["approval_id"], "approved", "批量同意")
            counts["approved"] += 1
            continue
        if reject_all:
            db.resolve_approval(item["approval_id"], "rejected", "批量拒绝")
            counts["rejected"] += 1
            continue
        while True:
            answer = input_fn("  处理方式 [a/r/d/s/q]: ").strip()
            if answer == "d":
                print_fn("  详情: " + json.dumps(
                    {"kind": item["kind"], "payload": _loads(item.get("payload"))},
                    ensure_ascii=False, indent=2))
                continue
            if answer in ("a", "r", "s", "q", "A", "R"):
                break
            print_fn("  无效输入")
        if answer == "q":
            print_fn("已退出，未处理的项目保留在队列中。")
            break
        if answer in ("a", "A"):
            if answer == "A":
                approve_all = True
            db.resolve_approval(item["approval_id"], "approved", "用户同意重试")
            counts["approved"] += 1
        elif answer in ("r", "R"):
            if answer == "R":
                reject_all = True
            db.resolve_approval(item["approval_id"], "rejected", "用户拒绝")
            counts["rejected"] += 1
        else:
            counts["skipped"] += 1
    print_fn(f"本轮：同意 {counts['approved']}，拒绝 {counts['rejected']}，跳过 {counts['skipped']}")
    return counts


def _loads(text: str | None) -> dict:
    if not text:
        return {}
    try:
        return json.loads(text)
    except ValueError:
        return {"raw": text}
