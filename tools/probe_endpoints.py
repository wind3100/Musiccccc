"""探测：用同一份 Cookie 试多个网易云端点，找出当前可用的那条路。

用法: python tools/probe_endpoints.py [--root dist]
只打印状态码 / 长度 / body 头部，不打印 cookie。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import requests  # noqa: E402

from nm.config import load_config  # noqa: E402
from nm.netease import DEFAULT_UA, build_payload  # noqa: E402

UA = DEFAULT_UA


def head(text: str, n: int = 160) -> str:
    return text[:n].replace("\n", " ").replace("\r", " ")


def main(argv: list[str]) -> int:
    root = Path(argv[argv.index("--root") + 1]) if "--root" in argv else ROOT / "dist"
    cfg = load_config(root)
    cookie = cfg.netease.cookie
    print(f"cookie 长度 {len(cookie)}，含 MUSIC_U: {'MUSIC_U=' in cookie}")
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": "https://music.163.com/", "Origin": "https://music.163.com"})
    if cookie:
        s.headers["Cookie"] = cookie

    def show(tag: str, r: requests.Response) -> bool:
        ok = r.status_code == 200 and len(r.content) > 2
        print(f"[{'OK ' if ok else 'FAIL'}] {tag}: HTTP {r.status_code} len={len(r.content)} :: {head(r.text)}")
        return ok

    def try_json(tag: str, r: requests.Response):
        try:
            body = r.json()
            print(f"       code={body.get('code')} keys={list(body)[:6]}")
            return body
        except ValueError:
            return None

    csrf = ""
    for part in cookie.split(";"):
        k, _, v = part.strip().partition("=")
        if k == "__csrf":
            csrf = v

    # A. weapi（当前实现）
    params, key, _ = build_payload({"csrf_token": csrf})
    r = s.post("https://music.163.com/weapi/w/nuser/account/get",
               data={"params": params, "encSecKey": key}, timeout=15)
    if show("A weapi/w/nuser/account/get", r):
        try_json("A", r)

    # B. weapi + csrf 在 query
    params, key, _ = build_payload({"csrf_token": csrf})
    r = s.post(f"https://music.163.com/weapi/w/nuser/account/get?csrf_token={csrf}",
               data={"params": params, "encSecKey": key}, timeout=15)
    if show("B weapi?csrf_token=", r):
        try_json("B", r)

    # C. 明文 api（部分 host 仍可用）
    for tag, url in [
        ("C GET music.163.com/api/nuser/account/get", "https://music.163.com/api/nuser/account/get"),
        ("D GET interface.music.163.com/api/nuser/account/get",
         "https://interface.music.163.com/api/nuser/account/get"),
    ]:
        r = s.get(url, timeout=15)
        if show(tag, r):
            try_json(tag, r)

    # E. 老 weapi 路径（v1）
    params, key, _ = build_payload({"csrf_token": csrf})
    r = s.post("https://music.163.com/weapi/login/token/refresh?csrf_token=" + csrf,
               data={"params": params, "encSecKey": key}, timeout=15)
    show("E weapi/login/token/refresh", r)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
