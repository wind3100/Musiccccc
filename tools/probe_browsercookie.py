"""本地实测：从本机 Edge 读网易云 Cookie 并验证登录态（不打印 Cookie 值）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nm.browsercookie import browser_dirs, find_login_cookie, profiles, read_163_cookies  # noqa: E402
from nm.netease import NeteaseClient  # noqa: E402


def main() -> int:
    print("=== 浏览器 profile 探测 ===")
    for name, base in browser_dirs().items():
        found = profiles(base)
        if found:
            print(f"  {name}: {[p.name for p in found]}")

    print("=== 尝试读取 ===")
    for name, base in browser_dirs().items():
        for profile in profiles(base):
            try:
                cookies = read_163_cookies(profile)
            except Exception as exc:  # noqa: BLE001
                print(f"  {name}/{profile.name}: 读取失败 {type(exc).__name__}: {exc}")
                continue
            keys = sorted(cookies)
            print(f"  {name}/{profile.name}: {len(keys)} 个 163 cookie，含登录态: "
                  f"{[k for k in keys if k in ('MUSIC_U', 'MUSIC_A')] or '无'}")

    found = find_login_cookie()
    if not found:
        print("未找到含登录态的 Cookie")
        return 1
    print(f"=== 命中 {found.browser}/{found.profile}（{found.masked}） ===")
    client = NeteaseClient("https://music.163.com", found.cookie, 15)
    try:
        uid = client.account_id()
        print(f"  账号 id = {uid}")
        details = client.liked_playlist()
        print(f"  我喜欢的音乐 = {len(details)} 首")
        if details:
            d = details[0]
            print(f"  首曲: {d.title} / {d.artist} / {d.duration_ms}ms / st={d.status}")
    except Exception as exc:  # noqa: BLE001
        print(f"  验证失败: {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
