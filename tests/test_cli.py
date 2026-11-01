"""CLI 行为测试：无参数（双击 exe）、参数错误、子命令分发。"""
import sys

import pytest

from nm import cli
from nm.config import Config, DownloadRules, MatchRules, Naming, NeteaseConfig, Paths


def make_cfg(root):
    return Config(
        root=root,
        paths=Paths(library=root / "library", staging=root / "staging", data=root / "data"),
        naming=Naming(),
        match=MatchRules(),
        download=DownloadRules(executors=()),
        netease=NeteaseConfig(),
        invalid_at_bottom=True,
    )


@pytest.fixture()
def tmp_cfg(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path)
    cfg.ensure_dirs()
    monkeypatch.setattr(cli, "load_config", lambda *a, **k: cfg)
    return cfg


def test_no_args_prints_welcome_not_argparse_error(tmp_cfg, capsys):
    """双击 exe（无参数）必须给出友好状态页，而不是英文 usage + 秒退。"""
    code = cli.main([])
    out = capsys.readouterr().out
    assert code == 0
    assert "本地音乐库管理工具" in out
    assert "可用命令" in out
    assert "doctor" in out and "approve" in out
    assert "arguments are required" not in out


def test_no_args_under_tty_holds_window(tmp_cfg, monkeypatch, capsys):
    """冻结态 + 交互终端时，应等待回车（避免窗口一闪而过）。"""
    held = []
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(cli, "_is_interactive", lambda: True)
    monkeypatch.setattr(cli, "_hold_open", lambda: held.append(True))
    cli.main([])
    assert held == [True]


def test_unknown_command_exits_2_with_chinese_message(tmp_cfg, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["nosuchcmd"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "参数错误" in err
    assert "可用命令" in err


def test_known_command_dispatch(tmp_cfg, capsys):
    assert cli.main(["list"]) == 0
    assert "喜欢" in capsys.readouterr().out


def test_version_flag_exits_zero(tmp_cfg, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "nm 0.1.0" in capsys.readouterr().out


def test_unexpected_error_is_reported_not_swallowed(tmp_cfg, monkeypatch, capsys):
    def boom(*_a, **_k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(cli, "cmd_list", boom)
    code = cli.main(["list"])
    assert code == 1
    assert "disk on fire" in capsys.readouterr().err


# ---------------- 双击 exe → 交互菜单 ----------------
def feed(*answers):
    it = iter(answers)
    return lambda _prompt="": next(it)


def test_menu_dispatches_selected_command(tmp_cfg, capsys):
    calls = []
    code = cli._menu(tmp_cfg, input_fn=feed("4", "0"), run=calls.append)  # [4] = list
    assert code == 0
    assert calls == [["list"]]
    out = capsys.readouterr().out
    assert "请选择要执行的操作" in out


def test_menu_handles_invalid_choice(tmp_cfg, capsys):
    calls = []
    cli._menu(tmp_cfg, input_fn=feed("9", "0"), run=calls.append)
    assert calls == []
    assert "无效选择" in capsys.readouterr().out


def test_menu_eof_exits_cleanly(tmp_cfg):
    def eof(_prompt=""):
        raise EOFError

    assert cli._menu(tmp_cfg, input_fn=eof, run=lambda argv: None) == 0


def test_menu_survives_system_exit_from_command(tmp_cfg):
    calls = []

    def runner(argv):
        calls.append(argv)
        raise SystemExit(2)

    assert cli._menu(tmp_cfg, input_fn=feed("1", "0"), run=runner) == 0
    assert calls == [["doctor"]]


def test_hold_open_only_when_frozen_and_tty(monkeypatch):
    waited = []

    def fake_input(_prompt=""):
        waited.append(True)

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(cli, "_is_interactive", lambda: True)
    cli._hold_open()
    assert waited == [], "源码运行时不应暂停"

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    cli._hold_open()
    assert waited == [True], "冻结态 + 终端应等待回车"


def test_hold_open_tolerates_eof(monkeypatch):
    def eof(_prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(cli, "_is_interactive", lambda: True)
    cli._hold_open()  # 不抛异常即通过


# ---------------- 「点了没反应」可解释性 ----------------
def test_status_hints_explain_missing_cookie_and_executor(tmp_cfg, capsys):
    cli._print_status(tmp_cfg)
    out = capsys.readouterr().out
    assert "未登录网易云" in out and "先选 [2]" in out
    assert "未配置下载执行器" in out


def test_menu_shows_pending_approval_count(tmp_cfg, capsys):
    from nm.db import DB

    db = DB(tmp_cfg.paths.db_file)
    db.upsert_song({"song_id": 1, "title": "夜曲", "artist": "周杰伦", "duration_ms": 1000})
    db.add_approval(1, "no_lossless", "无无损资源")
    db.close()

    cli._menu(tmp_cfg, input_fn=feed("0"), run=lambda argv: None)
    out = capsys.readouterr().out
    assert "1 条待处理" in out
    assert "先选 [6]" in out


def test_download_without_executor_explains_why(tmp_cfg, capsys):
    code = cli.main(["download", "--only-new"])
    out = capsys.readouterr().out
    assert code == 0
    assert "没有落库任何歌曲" in out
    assert "executors" in out


def test_download_reports_pending_approvals_as_next_step(tmp_cfg, capsys, monkeypatch):
    from nm.db import DB

    db = DB(tmp_cfg.paths.db_file)
    db.upsert_song({"song_id": 2, "title": "稻香", "artist": "周杰伦", "duration_ms": 1000})
    db.set_playlist_state(2, "like", 0, "+")
    db.add_approval(2, "duration_mismatch", "时长差")
    db.close()
    monkeypatch.setattr(cli, "build_executors", lambda cfg: [])

    code = cli.main(["download", "--only-new"])
    out = capsys.readouterr().out
    assert code == 0
    assert "卡在人工审批队列" in out
    assert "nm approve" in out
