"""把本仓库发布到 GitHub（不需要本机安装 git）：走 REST API 创建仓库并提交文件。

用法:
    # 1) 生成 fine-grained PAT（只勾目标仓库的 Contents: Read and write），二选一：
    set GITHUB_TOKEN=github_pat_xxx
    # 或写进 .github_token（已在 .gitignore 中；本脚本只认第一个非注释行）

    python tools/publish_github.py                      # 自动挑最近创建的项目仓库
    python tools/publish_github.py --repo 用户名/仓库名  # 指定仓库
    python tools/publish_github.py --dry-run            # 只看会传哪些文件

为什么用 urllib 而不是 requests：本机 requests 走 certifi 证书库验证 GitHub 失败
（unable to get local issuer certificate），而 urllib 用系统证书库正常。

安全：token 只从环境变量或 .github_token 读取，绝不打印；--dry-run 不联网。
"""
from __future__ import annotations

import argparse
import base64
import fnmatch
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com"

# 不入库的目录（相对仓库根）
SKIP_DIRS = {
    ".venv", "runtime", "build", "dist", "release", "data", "library", "staging",
    "__pycache__", ".pytest_cache", ".git", "demo/source",
}
# 不入库的文件
SKIP_FILES = ("*.pyc", "*.log", ".env", ".github_token", "probe_out.txt")
MAX_FILE_BYTES = 30 * 1024 * 1024


def collect_files(root: Path = ROOT) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        if rel_dir == ".":
            rel_dir = ""
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIRS and f"{rel_dir}/{d}".lstrip("/") not in SKIP_DIRS
        )
        for name in sorted(filenames):
            rel = f"{rel_dir}/{name}".lstrip("/")
            if any(fnmatch.fnmatch(name, pat) for pat in SKIP_FILES):
                continue
            path = Path(dirpath) / name
            if path.stat().st_size > MAX_FILE_BYTES:
                print(f"[SKIP] 超过 {MAX_FILE_BYTES // 1048576}MB，未上传: {rel}")
                continue
            out.append((rel, path))
    return out


def load_token(token_file: str) -> str:
    """从环境变量或 .github_token 读 token；忽略注释行与占位符。"""
    env_token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if env_token:
        return env_token
    path = Path(token_file)
    if not path.is_file():
        return ""
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip().strip('"').strip("'")
        if not line or line.startswith("#"):
            continue
        if line.upper() in ("PASTE_TOKEN_HERE", "TOKEN", "YOUR_TOKEN"):
            continue
        return line
    return ""


class GitHub:
    def __init__(self, token: str):
        self.token = token

    def call(self, method: str, path: str, params: dict | None = None,
             json_body: dict | None = None):
        url = API + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8") if json_body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "nm-publisher",
        })
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                body = resp.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode("utf-8", "replace")
            raise RuntimeError(f"{method} {path} -> HTTP {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"{method} {path} -> 网络错误 {exc.reason}") from exc

    def whoami(self) -> str:
        return self.call("GET", "/user")["login"]

    def list_repos(self) -> list[dict]:
        return self.call("GET", "/user/repos",
                         params={"sort": "created", "direction": "desc", "per_page": 30})

    def pick_repo(self, pattern: str = r"music|nm|网易") -> str | None:
        import re

        names = [r["full_name"] for r in self.list_repos()]
        if not names:
            return None
        for full in names:
            if re.search(pattern, full, re.I):
                return full
        return names[0]

    def repo_exists(self, full: str) -> bool:
        try:
            self.call("GET", f"/repos/{full}")
            return True
        except RuntimeError as exc:
            if "HTTP 404" in str(exc):
                return False
            raise

    def create_repo(self, name: str, private: bool, description: str) -> str:
        body = {"name": name, "private": private, "description": description,
                "auto_init": False, "has_issues": True, "has_wiki": False}
        return self.call("POST", "/user/repos", json_body=body)["full_name"]

    def head_sha(self, full: str, branch: str = "main") -> str | None:
        try:
            return self.call("GET", f"/repos/{full}/git/ref/heads/{branch}")["object"]["sha"]
        except RuntimeError as exc:
            if "HTTP 404" in str(exc) or "HTTP 409" in str(exc):
                return None
            raise

    def repo_info(self, full: str) -> dict:
        return self.call("GET", f"/repos/{full}")

    def seed_empty_repo(self, full: str) -> str:
        """空仓库不能直接写 Git Data（409 Git Repository is empty），先用 Contents API 造首个提交。

        返回默认分支名（GitHub 新仓库可能是 main 也可能是 master）。
        """
        info = self.repo_info(full)
        branch = info.get("default_branch") or "main"
        if info.get("size", 0) == 0 and self.head_sha(full, branch) is None:
            self.call("PUT", f"/repos/{full}/contents/.keep", json_body={
                "message": "chore: init repository",
                "content": "",  # 空内容即可
            })
            print(f"  空仓库已初始化（默认分支 {branch}）")
        return branch

    def upload_tree(self, full: str, files: list[tuple[str, Path]], message: str) -> str:
        branch = self.seed_empty_repo(full)
        tree = []
        for rel, path in files:
            blob = self.call("POST", f"/repos/{full}/git/blobs", json_body={
                "content": base64.b64encode(path.read_bytes()).decode("ascii"),
                "encoding": "base64",
            })
            tree.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
            print(f"  blob {rel}")
        # base_tree：本次提交只增改我们的文件，不删除仓库里已有的 README/LICENSE
        parent = self.head_sha(full, branch)
        base_tree = None
        if parent:
            base_tree = self.call("GET", f"/repos/{full}/git/commits/{parent}")["tree"]["sha"]
        payload: dict = {"tree": tree}
        if base_tree:
            payload["base_tree"] = base_tree
        new_tree = self.call("POST", f"/repos/{full}/git/trees", json_body=payload)
        commit = self.call("POST", f"/repos/{full}/git/commits", json_body={
            "message": message,
            "tree": new_tree["sha"],
            "parents": [parent] if parent else [],
        })
        if parent:
            self.call("PATCH", f"/repos/{full}/git/refs/heads/{branch}",
                      json_body={"sha": commit["sha"], "force": False})
        else:
            self.call("POST", f"/repos/{full}/git/refs",
                      json_body={"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
        return commit["sha"]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="用 GitHub API 发布本仓库（无需 git）")
    parser.add_argument("--repo", help="用户名/仓库名；省略则自动挑选")
    parser.add_argument("--private", action="store_true", help="创建私有仓库（仓库不存在时）")
    parser.add_argument("--description", default="网易云「我喜欢的音乐」本地无损归档工具")
    parser.add_argument("--message", default="chore: publish nm (本地音乐库管理工具)")
    parser.add_argument("--dry-run", action="store_true", help="只列出将上传的文件")
    parser.add_argument("--token-file", default=str(ROOT / ".github_token"))
    args = parser.parse_args(argv)

    files = collect_files()
    total = sum(p.stat().st_size for _, p in files)
    print(f"准备发布 {len(files)} 个文件，共 {total / 1024:.1f} KB")
    for rel, _ in files[:8]:
        print(f"  {rel}")
    if len(files) > 8:
        print(f"  … 其余 {len(files) - 8} 个")

    if args.dry_run:
        print("\n[dry-run] 未联网、未写入。")
        return 0

    token = load_token(args.token_file)
    if not token:
        print("\n[FAIL] 还没有有效 token。两种方式任选：")
        print("  set GITHUB_TOKEN=github_pat_xxx")
        print(f"  或把 token 粘进 {args.token_file}（第一个非注释行）")
        return 2

    gh = GitHub(token)
    login = gh.whoami()
    print(f"已认证：{login}")

    full = args.repo
    if not full:
        full = gh.pick_repo() or f"{login}/music-nm"
        print(f"自动选中仓库：{full}（可用 --repo 指定）")
    _, _, name = full.partition("/")
    if not name:
        print("[FAIL] --repo 需要形如 用户名/仓库名")
        return 2

    if gh.repo_exists(full):
        print(f"仓库已存在，提交到 {full}")
    else:
        full = gh.create_repo(name, args.private, args.description)
        print(f"已创建仓库 {full}")

    sha = gh.upload_tree(full, files, args.message)
    print(f"\n[OK] 已提交 {sha[:10]}")
    print(f"      https://github.com/{full}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
