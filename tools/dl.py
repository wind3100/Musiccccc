#!/usr/bin/env python
"""HTTPS 下载器（本机 Schannel 损坏，curl/Invoke-WebRequest 全部 SEC_E_NO_CREDENTIALS，
而 Python 自带 OpenSSL 正常，故统一用本脚本下载）。

用法: python tools/dl.py <url> <outfile> [--sha256 <hex>]
"""
import hashlib
import os
import sys
import urllib.request


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    url, out = argv[1], argv[2]
    expect = None
    if "--sha256" in argv:
        expect = argv[argv.index("--sha256") + 1].lower()

    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    h = hashlib.sha256()
    total = 0
    with urllib.request.urlopen(req, timeout=60) as resp, open(out, "wb") as fh:
        length = resp.headers.get("Content-Length")
        print("HTTP %s  size=%s  -> %s" % (resp.status, length, out))
        while True:
            chunk = resp.read(262144)
            if not chunk:
                break
            fh.write(chunk)
            h.update(chunk)
            total += len(chunk)
            if total % (4 * 1024 * 1024) < 262144:
                print("  %.1f MiB" % (total / 1048576.0))
    digest = h.hexdigest()
    print("done %d bytes  sha256=%s" % (total, digest))
    if expect and expect != digest:
        print("SHA256 MISMATCH expected %s" % expect)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
