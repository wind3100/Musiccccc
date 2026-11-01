"""Cookie / 登录命令测试：写入 .env、掩码、格式校验、扫码登录流程（全部离线）。"""
import time

import pytest

from nm import cli
from nm.config import Config, DownloadRules, MatchRules, Naming, NeteaseConfig, Paths

GOOD = "MUSIC_U=0123456789abcdef; __csrf=deadbeef; NMTID=xyz"
NO_LOGIN = "NMTID=xyz; os=pc"


def make_cfg(root, cookie=""):
    cfg = Config(
        root=root,
        paths=Paths(library=root / "library", staging=root / "staging", data=root / "data"),
        naming=Naming(),
        match=MatchRules(),
        download=DownloadRules(executors=()),
        netease=NeteaseConfig(cookie=cookie),
        invalid_at_bottom=True,
    )
    cfg.ensure_dirs()
    return cfg


def args(**kw):
    base = {"check": False, "from_browser": False, "timeout": 5}
    base.update(kw)
    return type("A", (), base)()


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)


@pytest.fixture(autouse=True)
def stub_verify(monkeypatch):
    monkeypatch.setattr(cli, "_verify_login", lambda cfg, cookie: (True, "已登录：测试用户，uid=1"))


# ---------------- .env 写入 ----------------
def test_write_env_cookie_creates_and_updates(tmp_path):
    env = tmp_path / ".env"
    cli._write_env_cookie(env, GOOD)
    assert "NETEASE_COOKIE=" + GOOD in env.read_text(encoding="utf-8")

    cli._write_env_cookie(env, "MUSIC_U=newvalue")
    text = env.read_text(encoding="utf-8")
    assert text.count("NETEASE_COOKIE=") == 1
    assert "newvalue" in text


def test_write_env_preserves_other_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# 我的配置\nOTHER_KEY=1\nNETEASE_COOKIE=old\n", encoding="utf-8")
    cli._write_env_cookie(env, GOOD)
    text = env.read_text(encoding="utf-8")
    assert "# 我的配置" in text and "OTHER_KEY=1" in text


def test_mask_hides_secret_body():
    masked = cli._mask_cookie(GOOD)
    assert "0123456789abcdef" not in masked
    assert "MUSIC_U=012345" in masked


# ---------------- --check ----------------
def test_cookie_check_reports_missing(tmp_path, capsys):
    assert cli.cmd_cookie(make_cfg(tmp_path), args(check=True)) == 1
    out = capsys.readouterr().out
    assert "未配置" in out and "nm login" in out


def test_cookie_check_ok(tmp_path, capsys):
    assert cli.cmd_cookie(make_cfg(tmp_path, GOOD), args(check=True)) == 0
    assert "已登录" in capsys.readouterr().out


def test_cookie_check_rejects_anonymous_cookie(tmp_path, capsys):
    assert cli.cmd_cookie(make_cfg(tmp_path, NO_LOGIN), args(check=True)) == 1
    assert "MUSIC_U" in capsys.readouterr().out


# ---------------- 交互：三种方式 ----------------
def test_cookie_menu_default_is_qr_login(tmp_path, capsys, monkeypatch):
    called = []
    monkeypatch.setattr(cli, "cmd_login", lambda cfg, a: called.append(True) or 0)
    monkeypatch.setattr("builtins.input", lambda _p="": "")
    assert cli.cmd_cookie(make_cfg(tmp_path), args()) == 0
    assert called == [True]
    out = capsys.readouterr().out
    assert "手机扫码登录" in out and "从本机浏览器读取" in out and "手动粘贴" in out


def test_cookie_paste_path_writes_env(tmp_path, monkeypatch):
    import getpass

    monkeypatch.setattr(getpass, "getpass", lambda prompt="": GOOD)
    monkeypatch.setattr("builtins.input", lambda _p="": "3")
    assert cli.cmd_cookie(make_cfg(tmp_path), args()) == 0
    assert GOOD in (tmp_path / ".env").read_text(encoding="utf-8")


def test_cookie_paste_rejects_bad_input(tmp_path, monkeypatch):
    import getpass

    monkeypatch.setattr(getpass, "getpass", lambda prompt="": NO_LOGIN)
    monkeypatch.setattr("builtins.input", lambda _p="": "3")
    assert cli.cmd_cookie(make_cfg(tmp_path), args()) == 2
    assert not (tmp_path / ".env").exists()


def test_cookie_paste_cancel_on_empty(tmp_path, monkeypatch):
    import getpass

    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "")
    monkeypatch.setattr("builtins.input", lambda _p="": "3")
    assert cli.cmd_cookie(make_cfg(tmp_path), args()) == 1


def test_cookie_browser_path_success(tmp_path, capsys, monkeypatch):
    from nm.browsercookie import FoundCookie

    monkeypatch.setattr("nm.browsercookie.find_login_cookie",
                        lambda preferred=None: FoundCookie(GOOD, "edge", "Default", ["MUSIC_U", "__csrf"]))
    assert cli.cmd_cookie(make_cfg(tmp_path), args(from_browser=True)) == 0
    assert GOOD in (tmp_path / ".env").read_text(encoding="utf-8")
    assert "edge/Default" in capsys.readouterr().out


def test_cookie_browser_path_not_found(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("nm.browsercookie.find_login_cookie", lambda preferred=None: None)
    assert cli.cmd_cookie(make_cfg(tmp_path), args(from_browser=True)) == 2
    assert "扫码" in capsys.readouterr().out


# ---------------- 扫码登录 ----------------
class FakeSession:
    cookies = []


class FakeClient:
    def __init__(self, *a, **k):
        self.session = FakeSession()
        self.polls = 0

    def qr_unikey(self):
        return "TESTKEY"

    def qr_login_url(self, key):
        return f"https://music.163.com/login?codekey={key}"

    def qr_poll(self, key):
        self.polls += 1
        if self.polls < 2:
            return {"code": 801, "message": "等待扫码"}
        return {"code": 803, "message": "授权登陆成功"}

    def session_cookie_string(self, domain_suffix="163.com"):
        return "MUSIC_U=from_qr; __csrf=csrf123"


def test_login_qr_writes_env(tmp_path, capsys, monkeypatch):
    import nm.netease as ne

    monkeypatch.setattr(ne, "NeteaseClient", FakeClient)
    monkeypatch.setattr("webbrowser.open", lambda url: True)
    cfg = make_cfg(tmp_path)
    assert cli.cmd_login(cfg, args()) == 0
    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "MUSIC_U=from_qr" in text
    out = capsys.readouterr().out
    assert "codekey=TESTKEY" in out and "登录成功" in out


def test_login_expired_key_is_refreshed(tmp_path, capsys, monkeypatch):
    import nm.netease as ne

    class ExpireOnce(FakeClient):
        def qr_poll(self, key):
            self.polls += 1
            if self.polls == 1:
                return {"code": 800, "message": "二维码已失效"}
            if self.polls == 2:
                return {"code": 801, "message": "等待扫码"}
            return {"code": 803, "message": "授权登陆成功"}

    monkeypatch.setattr(ne, "NeteaseClient", ExpireOnce)
    monkeypatch.setattr("webbrowser.open", lambda url: True)
    assert cli.cmd_login(make_cfg(tmp_path), args()) == 0
    assert "二维码过期" in capsys.readouterr().out


def test_login_timeout(tmp_path, capsys, monkeypatch):
    import nm.netease as ne

    class Never(FakeClient):
        def qr_poll(self, key):
            return {"code": 801, "message": "等待扫码"}

    monkeypatch.setattr(ne, "NeteaseClient", Never)
    monkeypatch.setattr("webbrowser.open", lambda url: True)
    assert cli.cmd_login(make_cfg(tmp_path), args(timeout=0)) == 1
    assert "超时" in capsys.readouterr().out
