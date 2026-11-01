"""探测：网易云扫码登录（无密码、无浏览器锁）能否走 /api 明文通路。

流程：
1. GET /api/login/qrcode/unikey?type=1        → unikey
2. 打开 https://music.163.com/login?codekey=<unikey> 让用户用网易云 App 扫码
3. GET /api/login/qrcode/client/login?key=<unikey>&type=1
   code: 800 过期 / 801 等待扫码 / 802 已扫待确认 / 803 成功（返回 Set-Cookie）
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import requests  # noqa: E402

from nm.netease import DEFAULT_UA  # noqa: E402


def main() -> int:
    s = requests.Session()
    s.headers.update({"User-Agent": DEFAULT_UA, "Referer": "https://music.163.com/"})

    r = s.get("https://music.163.com/api/login/qrcode/unikey", params={"type": 1}, timeout=15)
    print("unikey:", r.status_code, len(r.content), r.text[:200])
    try:
        body = r.json()
    except ValueError:
        print("非 JSON，扫码登录不可用")
        return 1
    key = body.get("unikey")
    print("  code =", body.get("code"), " unikey =", key)
    if not key:
        return 1

    poll = s.get("https://music.163.com/api/login/qrcode/client/login",
                 params={"key": key, "type": 1}, timeout=15)
    print("poll:", poll.status_code, len(poll.content), poll.text[:200])
    print("  Set-Cookie 头数:", len(poll.headers.get("Set-Cookie") or ""))
    print("  登录 URL: https://music.163.com/login?codekey=" + key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
