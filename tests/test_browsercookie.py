"""浏览器 Cookie 读取测试（不依赖真实 DPAPI：只测装配与发现逻辑）。"""
from pathlib import Path

from nm.browsercookie import (
    BROWSER_ORDER,
    cookie_string,
    find_login_cookie,
    profiles,
)


def test_profiles_discovers_default_and_profile_dirs(tmp_path):
    user_data = tmp_path / "User Data"
    for name in ("Default", "Profile 1", "ShaderCache"):
        d = user_data / name / "Network"
        d.mkdir(parents=True)
        (d / "Cookies").write_bytes(b"fake")
    found = [p.name for p in profiles(user_data)]
    assert found == ["Default", "Profile 1"]


def test_profiles_empty_when_missing(tmp_path):
    assert profiles(tmp_path / "nope") == []


def test_cookie_string_skips_empty_values():
    assert cookie_string({"A": "1", "B": "", "C": "3"}) == "A=1; C=3"


def test_find_login_cookie_prefers_first_browser_with_login(tmp_path, monkeypatch):
    from nm import browsercookie as bc

    edge = tmp_path / "edge"
    (edge / "Default" / "Network").mkdir(parents=True)
    (edge / "Default" / "Network" / "Cookies").write_bytes(b"x")
    (edge / "Local State").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(bc, "browser_dirs", lambda: {"edge": edge, "chrome": tmp_path / "none"})
    monkeypatch.setattr(bc, "read_163_cookies",
                        lambda profile: {"MUSIC_U": "abc", "__csrf": "c1", "NMTID": "n"})
    found = bc.find_login_cookie()
    assert found is not None
    assert found.browser == "edge" and found.profile == "Default"
    assert found.has_login
    assert "MUSIC_U=abc" in found.cookie


def test_find_login_cookie_ignores_anonymous(tmp_path, monkeypatch):
    from nm import browsercookie as bc

    edge = tmp_path / "edge"
    (edge / "Default" / "Network").mkdir(parents=True)
    (edge / "Default" / "Network" / "Cookies").write_bytes(b"x")

    monkeypatch.setattr(bc, "browser_dirs", lambda: {"edge": edge})
    monkeypatch.setattr(bc, "read_163_cookies", lambda profile: {"NMTID": "n", "os": "pc"})
    assert bc.find_login_cookie() is None


def test_browser_order_is_edge_first():
    assert BROWSER_ORDER[0] == "edge"
