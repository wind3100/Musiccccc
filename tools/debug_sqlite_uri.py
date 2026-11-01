"""调试：锁定中的 Chromium Cookies 库怎么只读打开（URI 转义 / 复制 / immutable）。"""
import shutil
import sqlite3
import tempfile
from pathlib import Path
from urllib.parse import quote

P = Path.home() / "AppData/Local/Microsoft/Edge/User Data/Default/Network/Cookies"
print("db:", P, "exists:", P.is_file(), "size:", P.stat().st_size if P.is_file() else 0)

plain = str(P).replace("\\", "/")
for safe in ("/", ":/", "/:"):
    uri = "file:" + quote(plain, safe=safe) + "?mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=3)
        rows = conn.execute("select count(*) from cookies").fetchone()[0]
        conn.close()
        print(f"OK   safe={safe!r} rows={rows}\n     {uri}")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL safe={safe!r} {type(exc).__name__}: {exc}\n     {uri}")

# 直接路径（不加 URI 参数）
try:
    conn = sqlite3.connect(f"{P}?mode=ro&immutable=1", uri=True, timeout=3)
    print("OK   plain-path+uri-params rows=", conn.execute("select count(*) from cookies").fetchone()[0])
    conn.close()
except Exception as exc:  # noqa: BLE001
    print("FAIL plain-path+uri-params", type(exc).__name__, exc)

# 复制一份（浏览器锁定时通常会失败）
try:
    tmp = Path(tempfile.gettempdir()) / "cookies_copy_test.db"
    shutil.copy2(P, tmp)
    print("OK   copy ->", tmp, tmp.stat().st_size)
    tmp.unlink(missing_ok=True)
except Exception as exc:  # noqa: BLE001
    print("FAIL copy", type(exc).__name__, exc)
