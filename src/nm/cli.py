"""命令行入口：所有网络动作都只能由用户手动触发（无定时、无后台、无自动）。

    nm.cmd doctor                 环境自检
    nm.cmd sync                   手动同步「我喜欢的音乐」
    nm.cmd sync --offline x.json  用离线快照同步（无 Cookie 也能验证）
    nm.cmd list [--bucket like]   查看三栏
    nm.cmd download --only-new    下载待补歌曲（双源+严格匹配+审批熔断）
    nm.cmd approve                处理人工审批队列
    nm.cmd jobs                   查看处理日志

（nm.cmd 等价于 PYTHONPATH=src python -m nm；打包后直接是 nm.exe <子命令>）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, naming, staging
from .approval import run_menu
from .config import Config, load_config
from .db import DB
from .downloaders import build_executors
from .logutil import setup_logging
from .match import quality_of
from .netease import NeteaseClient, NeteaseError
from .pipeline import Pipeline
from .sync import apply_sync, entries_from_details, entries_from_tracks, load_entries_from_json, save_snapshot

BUCKET_TITLES = {"like": "喜欢", "unliked": "已取消喜欢", "invalid": "失效"}


def _safe_stdout() -> None:
    """Windows GBK 控制台：避免个别字符导致 UnicodeEncodeError。"""
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass


def _open_db(cfg: Config) -> DB:
    cfg.ensure_dirs()
    return DB(cfg.paths.db_file)


def _client(cfg: Config) -> NeteaseClient:
    return NeteaseClient(cfg.netease.api_base, cfg.netease.cookie, cfg.netease.timeout_s)


# ---------------- doctor ----------------
def cmd_doctor(cfg: Config, args) -> int:
    ok = True
    print(f"nm {__version__}  |  Python {sys.version.split()[0]}  |  root={cfg.root}")
    print(f"库目录   {cfg.paths.library}")
    print(f"暂存区   {cfg.paths.staging}")
    print(f"数据目录 {cfg.paths.data}")

    try:
        cfg.ensure_dirs()
        print("[OK] 目录可写")
    except OSError as exc:
        ok = False
        print(f"[FAIL] 目录不可写: {exc}")

    try:
        db = _open_db(cfg)
        print(f"[OK] 数据库 {cfg.paths.db_file.name}，歌曲 {len(db.snapshot())} 首")
        db.close()
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"[FAIL] 数据库: {exc}")

    if cfg.netease.cookie:
        print(f"[OK] 已配置 NETEASE_COOKIE（{len(cfg.netease.cookie)} 字符）")
    else:
        print("[WARN] 未配置 NETEASE_COOKIE：sync 不可用（下载仍可用，歌词会留空）")

    try:
        executors = build_executors(cfg)
        if executors:
            print(f"[OK] 执行器 {len(executors)} 个: {', '.join(e.name for e in executors)}")
            if len(executors) < 2:
                print("[WARN] 建议配置主源 + 备源两个执行器（双源冗余）")
        else:
            print("[WARN] 未配置下载执行器：download 会把所有歌曲转入人工审批（见 README §4）")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"[FAIL] 执行器: {exc}")

    free_gb = staging.free_bytes(cfg.paths.library) / (1024 ** 3)
    print(f"[OK] 库所在盘剩余 {free_gb:.1f} GiB")

    client = _client(cfg)
    try:
        reachable = client.ping()
        print(f"[{'OK' if reachable else 'WARN'}] 网易云接口连通性: {'可访问' if reachable else '空响应/需登录'}")
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] 网易云接口: {exc}")
    return 0 if ok else 1


# ---------------- sync ----------------
def cmd_sync(cfg: Config, args) -> int:
    db = _open_db(cfg)
    logger = setup_logging(cfg.paths.log_dir)
    try:
        if args.offline:
            entries = load_entries_from_json(args.offline)
            source = f"离线快照 {args.offline}"
        else:
            if not cfg.netease.cookie:
                print("缺少 NETEASE_COOKIE：请把 music.163.com 的 Cookie 写入 .env 后再同步。")
                return 2
            client = _client(cfg)
            logger.info("手动同步：拉取「我喜欢的音乐」")
            try:
                details = client.liked_playlist()
            except NeteaseError as exc:
                print(f"同步失败: {exc}")
                return 3
            entries = entries_from_details(details)
            source = "网易云「我喜欢的音乐」"

        run_id = db.start_sync_run()
        diff = apply_sync(db, entries, invalid_at_bottom=cfg.invalid_at_bottom)
        snap = save_snapshot(cfg.paths.data / "snapshots", entries,
                             f"sync-{run_id:04d}.json")
        db.finish_sync_run(run_id, diff.cloud_total, len(diff.added), len(diff.removed),
                           len(diff.invalid), str(snap))

        print(f"来源：{source}")
        print(diff.summary())
        _print_markers(db, diff)
        print(f"快照已存档: {snap}")
        return 0
    finally:
        db.close()


def _print_markers(db: DB, diff) -> None:
    by_id = {s["song_id"]: s for s in db.snapshot().values()}
    groups = (("+", "新增", diff.added), ("-", "减少", diff.removed), ("!", "失效", diff.invalid))
    for marker, label, ids in groups:
        if not ids:
            continue
        print(f"  ({marker}) {label} {len(ids)} 首：")
        for song_id in ids[:20]:
            row = by_id.get(song_id) or {}
            title = row.get("title", "?")
            artist = row.get("artist", "")
            has_file = "有文件" if row.get("file_count") else "无文件"
            print(f"      [{song_id}] {title} / {artist}  ({has_file})")
        if len(ids) > 20:
            print(f"      …… 其余 {len(ids) - 20} 首略")
    if diff.restored:
        print(f"  (恢复) {len(diff.restored)} 首误触取消后又收藏回来，已自动归位")


# ---------------- list ----------------
def cmd_list(cfg: Config, args) -> int:
    db = _open_db(cfg)
    try:
        buckets = ["like", "unliked", "invalid"] if args.bucket == "all" else [args.bucket]
        for bucket in buckets:
            rows = db.list_bucket(bucket)
            title = BUCKET_TITLES[bucket]
            print(f"== {title}（{len(rows)} 首）==")
            if not rows:
                print("   （空）")
                continue
            for row in rows:
                marker = f"({row['marker']})" if row.get("marker") else "   "
                state = "已下载" if row["file_count"] else ("已失效" if row["status"] < 0 else "待下载")
                print(f"  {marker} [{row['song_id']}] {row['title']} / {row['artist']}  {state}")
        return 0
    finally:
        db.close()


# ---------------- download ----------------
def cmd_download(cfg: Config, args) -> int:
    cfg.ensure_dirs()
    logger = setup_logging(cfg.paths.log_dir)
    db = _open_db(cfg)
    try:
        try:
            executors = build_executors(cfg)
        except Exception as exc:  # noqa: BLE001
            print(f"执行器配置有误：{exc}")
            print("请在 config.toml 里配置 [download].executors 与 [executors.<name>]（见 README §4）")
            return 2
        if args.dry_run:
            songs = Pipeline(cfg, db, executors).select_songs(
                only_new=args.only_new, limit=args.limit, song_ids=args.id)
            print(f"将处理 {len(songs)} 首（dry-run，未发任何请求）：")
            for song in songs:
                print(f"  [{song['song_id']}] {song['title']} / {song['artist']} {song['duration_ms']}ms")
            return 0

        client = _client(cfg) if cfg.netease.cookie else None
        pipeline = Pipeline(cfg, db, executors, netease=client, logger=logger)
        summary = pipeline.run(only_new=args.only_new, limit=args.limit, song_ids=args.id)
        for line in summary.lines():
            print(line)
        print(summary.summary_line())

        # 死路提示：该说的下一步必须说出来，否则用户只会看到"什么都没发生"
        pending = len(db.pending_approvals())
        if summary.count("downloaded") == 0:
            if pending:
                print(f"\n没有落库任何歌曲：{pending} 首卡在人工审批队列。")
                print("下一步：运行 nm approve（或双击 exe 选 [6]）逐条处理，处理完再执行本命令。")
            elif not executors:
                print("\n没有落库任何歌曲：未配置下载执行器（config.toml 的 [download].executors）。")
            elif summary.count("skipped") == len(summary.outcomes) and summary.outcomes:
                print("\n本次全部跳过（本地已有文件 / 已在审批队列），无需处理。")
        return 0
    finally:
        db.close()


# ---------------- cookie ----------------
ENV_KEY = "NETEASE_COOKIE"
COOKIE_HELP = """怎么拿 Cookie（1 分钟）：
  1) 浏览器登录 https://music.163.com
  2) F12 → Network（网络）→ 刷新页面 → 点任意一个请求
  3) Request Headers 里复制整条 Cookie（至少要有 MUSIC_U=，带 __csrf= 更好）
  4) 粘到下面，回车结束（不回显，只写本机 .env，不会上传到任何地方）
"""


def _mask_cookie(cookie: str) -> str:
    if not cookie:
        return "（未配置）"
    parts = [p.strip() for p in cookie.split(";") if p.strip()]
    kept = []
    for part in parts:
        key, _, value = part.partition("=")
        if key.strip() in ("MUSIC_U", "MUSIC_A"):
            kept.append(f"{key.strip()}={value[:6]}…({len(value)}字符)")
        else:
            kept.append(f"{key.strip()}=…")
    return "; ".join(kept)


def _write_env_cookie(env_file: Path, cookie: str) -> None:
    """写入/更新 .env 里的 NETEASE_COOKIE，保留其它行。"""
    line = f"{ENV_KEY}={cookie}"
    lines: list[str] = []
    if env_file.is_file():
        lines = env_file.read_text(encoding="utf-8-sig").splitlines()
    replaced = False
    for idx, old in enumerate(lines):
        if old.strip().startswith(ENV_KEY + "="):
            lines[idx] = line
            replaced = True
            break
    if not replaced:
        lines.append(line)
    env_file.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")


def _verify_login(cfg: Config, cookie: str) -> tuple[bool, str]:
    """用 Cookie 真连一次账号接口，确认登录态有效（顺便拿到昵称）。"""
    from .netease import NeteaseClient, NeteaseError

    client = NeteaseClient(cfg.netease.api_base, cookie, cfg.netease.timeout_s)
    try:
        uid = client.account_id()
    except NeteaseError as exc:
        return False, f"验证失败：{exc}"
    name = ""
    try:
        body = client._get_json("nuser/account/get")
        name = str((body.get("profile") or {}).get("nickname") or "")
    except Exception:  # noqa: BLE001
        pass
    return True, f"已登录：{name or '(未取到昵称)'}，uid={uid}"


def cmd_login(cfg: Config, args) -> int:
    """扫码登录：手机上确认一下就把 Cookie 写好，不碰浏览器、不输密码。"""
    import time
    import webbrowser

    from .netease import NeteaseClient, NeteaseError

    env_file = cfg.root / ".env"
    client = NeteaseClient(cfg.netease.api_base, "", cfg.netease.timeout_s)
    try:
        key = client.qr_unikey()
    except NeteaseError as exc:
        print(f"取二维码失败：{exc}")
        return 2
    url = client.qr_login_url(key)
    print("请用手机网易云音乐 App 扫码登录：")
    print(f"  {url}")
    print("（页面上的二维码就是登录码。按 Ctrl+C 可取消）")
    if not os.environ.get("NM_NO_BROWSER"):
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    print("等待扫码", end="", flush=True)

    wait_s = getattr(args, "timeout", None)
    deadline = time.time() + (180 if wait_s is None else float(wait_s))
    last = None
    try:
        while time.time() < deadline:
            try:
                body = client.qr_poll(key)
            except NeteaseError as exc:
                print(f"\n轮询失败：{exc}")
                return 2
            code = int(body.get("code") or 0)
            if code != last:
                print(f"\n  [{code}] {body.get('message') or ''}", end="", flush=True)
                last = code
            if code == 803:  # 登录成功
                cookie = client.session_cookie_string()
                if "MUSIC_U=" not in cookie and "MUSIC_A=" not in cookie:
                    print("\n[FAIL] 登录成功但没拿到 MUSIC_U，请改用 nm cookie 手动粘贴。")
                    return 2
                _write_env_cookie(env_file, cookie)
                ok, msg = _verify_login(cfg, cookie)
                print(f"\n[OK] 登录成功，已写入 {env_file}")
                print(f"     {_mask_cookie(cookie)}")
                print(f"     {msg}")
                print("下一步：nm sync（或双击 exe 选 [3]）")
                return 0
            if code == 800:  # 二维码过期
                key = client.qr_unikey()
                url = client.qr_login_url(key)
                print(f"\n  二维码过期，已刷新：{url}")
                last = None
            time.sleep(1.5)
    except KeyboardInterrupt:
        print("\n已取消。")
        return 1
    print("\n超时未扫码，请重试 nm login。")
    return 1


def _cookie_from_browser(cfg: Config, env_file: Path) -> int:
    from .browsercookie import BrowserCookieError, find_login_cookie

    print("正在从本机浏览器读取网易云 Cookie …")
    try:
        found = find_login_cookie()
    except BrowserCookieError as exc:
        print(f"[FAIL] {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] 读取失败：{type(exc).__name__}: {exc}")
        print("提示：Chromium 系浏览器运行时 Cookies 库被独占锁，需先完全退出浏览器。")
        return 2
    if found is None:
        print("[FAIL] 没找到含登录态的 Cookie。")
        print("      可能浏览器没登录 music.163.com，或浏览器正在运行（Cookies 库被锁）。")
        print("      建议改用 `nm login` 扫码登录。")
        return 2
    _write_env_cookie(env_file, found.cookie)
    ok, msg = _verify_login(cfg, found.cookie)
    print(f"[OK] 已从 {found.browser}/{found.profile} 读取并写入 {env_file}")
    print(f"     {_mask_cookie(found.cookie)}")
    print(f"     {msg}" if ok else f"     [WARN] {msg}")
    return 0 if ok else 2


def _cookie_paste(cfg: Config, env_file: Path) -> int:
    print(COOKIE_HELP)
    try:
        import getpass

        raw = getpass.getpass("粘贴 Cookie（输入不回显，直接回车取消）> ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消。")
        return 1
    if not raw:
        print("已取消（空输入）。")
        return 1
    raw = raw.replace("\n", "").replace("\r", "")
    if "MUSIC_U=" not in raw and "MUSIC_A=" not in raw:
        print("[FAIL] 没找到 MUSIC_U= 或 MUSIC_A=，这条 Cookie 不含登录态，已放弃写入。")
        return 2
    _write_env_cookie(env_file, raw)
    ok, msg = _verify_login(cfg, raw)
    print(f"[OK] 已写入 {env_file}")
    print(f"     {_mask_cookie(raw)}")
    print(f"     {msg}" if ok else f"     [WARN] {msg}")
    return 0 if ok else 2


def cmd_cookie(cfg: Config, args) -> int:
    env_file = cfg.root / ".env"
    if args.check:
        cookie = cfg.netease.cookie
        print(f".env 位置：{env_file}")
        print(f"当前 Cookie：{_mask_cookie(cookie)}")
        if not cookie:
            print("未配置。运行 `nm login`（扫码，推荐）或 `nm cookie` 交互配置。")
            return 1
        if "MUSIC_U=" not in cookie and "MUSIC_A=" not in cookie:
            print("[WARN] 缺少 MUSIC_U / MUSIC_A，登录态可能无效")
            return 1
        ok, msg = _verify_login(cfg, cookie)
        print(f"[{'OK' if ok else 'WARN'}] {msg}")
        return 0 if ok else 1

    if getattr(args, "from_browser", False):
        return _cookie_from_browser(cfg, env_file)

    print("选择登录方式：")
    print("  [1] 手机扫码登录（推荐，无需浏览器/密码）")
    print("  [2] 从本机浏览器读取（需先完全退出浏览器）")
    print("  [3] 手动粘贴 Cookie")
    try:
        choice = input("> ").strip() or "1"
    except (EOFError, KeyboardInterrupt):
        print("\n已取消。")
        return 1
    if choice == "1":
        return cmd_login(cfg, args)
    if choice == "2":
        return _cookie_from_browser(cfg, env_file)
    if choice == "3":
        return _cookie_paste(cfg, env_file)
    print("无效选择。")
    return 1


# ---------------- approve ----------------
def cmd_approve(cfg: Config, args) -> int:
    db = _open_db(cfg)
    try:
        pending = db.pending_approvals()
        if args.list:
            if not pending:
                print("审批队列为空。")
            for item in pending:
                print(f"  [{item['approval_id']}] {item['song_id']} {item['title']} / "
                      f"{item['artist']} —— {item['kind']}: {item['reason']}")
            return 0
        run_menu(db, limit=args.limit)
        return 0
    finally:
        db.close()


# ---------------- jobs ----------------
def cmd_jobs(cfg: Config, args) -> int:
    db = _open_db(cfg)
    try:
        rows = db.recent_jobs(args.n)
        if not rows:
            print("暂无处理日志。")
            return 0
        for row in rows:
            flag = "OK  " if row["ok"] else "FAIL"
            print(f"{row['created_at']} {flag} [{row['song_id']}] {row['stage']:<8} "
                  f"{row['source'] or '-':<10} {row['detail'] or ''}")
        return 0
    finally:
        db.close()


# ---------------- 参数解析 ----------------
COMMAND_HELP = [
    ("doctor", "环境自检：路径/数据库/Cookie/执行器/接口/磁盘"),
    ("login", "扫码登录网易云（手机确认，自动写入 .env）"),
    ("cookie", "录入/检查 Cookie（扫码 / 浏览器 / 手动粘贴）"),
    ("sync", "手动同步「我喜欢的音乐」（需先登录）"),
    ("list", "查看三栏（like / unliked / invalid）"),
    ("download", "下载待补歌曲（双源 + 严格匹配 + 审批熔断）"),
    ("approve", "处理人工审批队列（CLI 菜单）"),
    ("jobs", "查看处理日志"),
]


def _is_interactive() -> bool:
    try:
        return bool(sys.stdin and sys.stdin.isatty())
    except (AttributeError, ValueError):
        return False


def _hold_open() -> None:
    """双击 exe 时窗口会瞬间关闭：暂停，让用户看清内容再退。"""
    if not getattr(sys, "frozen", False) or not _is_interactive():
        return
    try:
        input("\n按回车键关闭窗口...")
    except (EOFError, KeyboardInterrupt):
        pass


class _Parser(argparse.ArgumentParser):
    """中文报错 + 双击时暂停，避免用户只看到一闪而过的英文 usage。"""

    def error(self, message: str) -> None:  # type: ignore[override]
        print(f"参数错误：{message}", file=sys.stderr)
        print("可用命令：" + " / ".join(name for name, _ in COMMAND_HELP), file=sys.stderr)
        print("查看帮助：nm --help", file=sys.stderr)
        _hold_open()
        raise SystemExit(2)


def _library_stats(cfg: Config) -> dict:
    try:
        db = _open_db(cfg)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    try:
        snapshot = db.snapshot()
        like = sum(1 for row in snapshot.values() if row.get("bucket") in ("like", "invalid"))
        unliked = sum(1 for row in snapshot.values() if row.get("bucket") == "unliked")
        return {
            "songs": len(snapshot),
            "like": like,
            "unliked": unliked,
            "files": len(db.all_files()),
            "pending": len(db.pending_approvals()),
        }
    finally:
        db.close()


MENU_ARGS = {
    "1": ["doctor"],
    "2": ["cookie"],
    "3": ["sync"],
    "4": ["list"],
    "5": ["download", "--only-new"],
    "6": ["approve"],
    "7": ["jobs", "-n", "20"],
}
MENU_LABELS = [
    ("1", "doctor", "环境自检"),
    ("2", "login", "登录网易云（扫码 / 浏览器 / 粘贴）"),
    ("3", "sync", "手动同步「我喜欢的音乐」"),
    ("4", "list", "查看三栏"),
    ("5", "download", "下载待补歌曲（--only-new）"),
    ("6", "approve", "处理人工审批队列"),
    ("7", "jobs", "查看处理日志"),
]


def _print_status(cfg: Config) -> dict:
    print(f"nm {__version__} —— 本地音乐库管理工具（纯手动触发，不做后台同步）")
    print(f"程序目录：{cfg.root}")
    print(f"音乐库  ：{cfg.paths.library}")
    stats = _library_stats(cfg)
    if "error" in stats:
        print(f"[WARN] 数据库不可用：{stats['error']}")
    else:
        print(f"库状态  ：歌单 {stats['songs']} 首（喜欢 {stats['like']} / 已取消喜欢 {stats['unliked']}），"
              f"已落库文件 {stats['files']} 个，待审批 {stats['pending']} 条")

    # 让用户一眼看懂"为什么点了没反应"：缺什么就直说
    hints: list[str] = []
    if not cfg.netease.cookie:
        hints.append("未登录网易云 → 先选 [2] 登录（手机扫码，10 秒搞定）")
        executors = []
    else:
        try:
            executors = build_executors(cfg)
        except Exception as exc:  # noqa: BLE001
            executors = []
            if "NETEASE_COOKIE" not in str(exc):  # 缺登录已经在上一条提示里讲了
                hints.append(f"执行器配置有误：{exc}")
    if not executors:
        hints.append("未配置下载执行器 → [5] download 只会把歌曲送进审批队列（见 README §4）")
    pending = int(stats.get("pending", 0) or 0)
    if pending:
        hints.append(f"有 {pending} 条待审批 → 先选 [6] 处理完，[5] 才会真的去下载")
    for hint in hints:
        print(f"[提示] {hint}")
    return stats


def _menu(cfg: Config, input_fn=None, run=None) -> int:
    """双击 exe 进入的交互菜单：每项都等价于一条手动命令，不做任何自动行为。"""
    runner = run or main
    ask = input_fn or input  # 不写默认参数，避免 import 时就把 input 固定住
    while True:
        stats = _print_status(cfg)
        pending = int(stats.get("pending", 0) or 0)
        print("\n请选择要执行的操作：")
        for key, name, desc in MENU_LABELS:
            suffix = f"（{pending} 条待处理）" if key == "5" and pending else ""
            print(f"  [{key}] {name:<9}{desc}{suffix}")
        print("  [0] 退出")
        try:
            choice = ask("\n> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if choice in ("0", "q", "quit", "exit"):
            return 0
        argv = MENU_ARGS.get(choice)
        if not argv:
            print("无效选择，请重新输入。\n")
            continue
        print()
        try:
            runner(argv)
        except SystemExit:
            pass
        print()


def _welcome(cfg: Config) -> int:
    """无子命令：交互终端（双击 exe）进菜单；管道/脚本里只打印状态页。"""
    if _is_interactive():
        return _menu(cfg)
    _print_status(cfg)
    print("\n可用命令：")
    for name, desc in COMMAND_HELP:
        print(f"  nm {name:<9}{desc}")
    print("\n示例：nm doctor | nm sync | nm list | nm download --only-new | nm approve")
    print("提示：不带参数运行不会做事（没有自动同步）；双击 exe 可进入交互菜单。")
    _hold_open()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="nm", description="本地音乐库管理（网易云歌单手动同步）")
    parser.add_argument("--config", help="指定 config.toml 路径")
    parser.add_argument("--version", action="version", version=f"nm {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="环境自检").set_defaults(func=cmd_doctor)

    p_cookie = sub.add_parser("cookie", help="录入/检查网易云 Cookie（扫码/浏览器/手动）")
    p_cookie.add_argument("--check", action="store_true", help="只检查当前 Cookie，不交互录入")
    p_cookie.add_argument("--from-browser", action="store_true", help="直接从本机浏览器读取（需退出浏览器）")
    p_cookie.set_defaults(func=cmd_cookie)

    p_login = sub.add_parser("login", help="扫码登录网易云（手机确认，自动写入 .env）")
    p_login.add_argument("--timeout", type=int, default=180, help="等待扫码的秒数")
    p_login.set_defaults(func=cmd_login)

    p_sync = sub.add_parser("sync", help="手动同步「我喜欢的音乐」")
    p_sync.add_argument("--offline", help="使用离线歌单 JSON 快照，不联网")
    p_sync.set_defaults(func=cmd_sync)

    p_list = sub.add_parser("list", help="查看三栏")
    p_list.add_argument("--bucket", choices=["like", "unliked", "invalid", "all"], default="all")
    p_list.set_defaults(func=cmd_list)

    p_dl = sub.add_parser("download", help="下载待补歌曲")
    p_dl.add_argument("--only-new", action="store_true", help="只处理新增（本地无文件的）歌曲")
    p_dl.add_argument("--limit", type=int, help="最多处理几首")
    p_dl.add_argument("--id", type=int, nargs="+", help="只处理指定 song_id")
    p_dl.add_argument("--dry-run", action="store_true", help="只列清单，不发请求")
    p_dl.set_defaults(func=cmd_download)

    p_ap = sub.add_parser("approve", help="处理人工审批队列")
    p_ap.add_argument("--list", action="store_true", help="只列出，不交互")
    p_ap.add_argument("--limit", type=int, help="最多处理几条")
    p_ap.set_defaults(func=cmd_approve)

    p_jobs = sub.add_parser("jobs", help="查看处理日志")
    p_jobs.add_argument("-n", type=int, default=20)
    p_jobs.set_defaults(func=cmd_jobs)
    return parser


def main(argv: list[str] | None = None) -> int:
    _safe_stdout()
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if not argv:  # 双击 exe / 无参数：给友好状态页而不是 argparse 英文报错
            return _welcome(load_config())
        parser = build_parser()
        args = parser.parse_args(argv)
        config_file = Path(args.config).resolve() if args.config else None
        cfg = load_config(config_file=config_file)
        return int(args.func(cfg, args) or 0)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 —— 双击时不要把 traceback 一闪而过
        print(f"运行出错：{type(exc).__name__}: {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        _hold_open()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
