"""配置加载：config.toml + .env。

绿色软件约定：所有相对路径都以「程序同级目录」为根（即 config.toml 所在目录），
不写系统 AppData / 注册表。
"""
from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


def app_root() -> Path:
    """程序根目录：优先 NM_ROOT，源码运行时为仓库根，打包后为 exe 所在目录。"""
    env = os.environ.get("NM_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    if getattr(sys, "frozen", False):  # PyInstaller 单文件
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _parse_dotenv(path: Path) -> dict[str, str]:
    """极简 .env 解析（避免为了一个小功能引入依赖）。"""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


@dataclass(frozen=True)
class Paths:
    library: Path
    staging: Path
    data: Path

    @property
    def db_file(self) -> Path:
        return self.data / "nm.sqlite3"

    @property
    def log_dir(self) -> Path:
        return self.data / "logs"

    @property
    def approvals_file(self) -> Path:
        return self.data / "approvals.json"


@dataclass(frozen=True)
class MatchRules:
    duration_tolerance_ms: int = 2000
    min_lossless_bitrate_kbps: int = 600


@dataclass(frozen=True)
class Naming:
    template: str = "{title} - {artist}"
    max_length: int = 120


@dataclass(frozen=True)
class DownloadRules:
    search_interval_s: float = 1.2
    download_interval_s: float = 1.5
    window_max_requests: int = 10
    window_seconds: float = 60.0
    max_consecutive_failures: int = 3
    backoff_base_s: float = 5.0
    preferred_formats: tuple[str, ...] = ("flac", "wav", "ape")
    # 缺省不启用任何执行器：宁可提示"未配置"，也不要拿演示用的 fake 去误导用户
    executors: tuple[str, ...] = ()


@dataclass(frozen=True)
class NeteaseConfig:
    api_base: str = "https://music.163.com"
    timeout_s: int = 15
    cookie: str = ""


@dataclass(frozen=True)
class Config:
    root: Path
    paths: Paths
    naming: Naming
    match: MatchRules
    download: DownloadRules
    netease: NeteaseConfig
    invalid_at_bottom: bool = True
    raw: dict = field(default_factory=dict)

    def ensure_dirs(self) -> None:
        for p in (self.paths.library, self.paths.staging, self.paths.data, self.paths.log_dir):
            p.mkdir(parents=True, exist_ok=True)


def _abs(root: Path, value: str, env_key: str | None = None) -> Path:
    if env_key:
        override = os.environ.get(env_key)
        if override:
            value = override
    p = Path(value).expanduser()
    return p if p.is_absolute() else (root / p).resolve()


def load_config(root: Path | None = None, config_file: Path | None = None) -> Config:
    root = (root or app_root()).resolve()
    cfg_path = config_file or (root / "config.toml")
    raw: dict = {}
    if cfg_path.is_file():
        # Windows 记事本/PowerShell 会写 UTF-8 BOM，tomllib 不接受，这里主动剥掉
        data = cfg_path.read_bytes()
        if data.startswith(b"\xef\xbb\xbf"):
            data = data[3:]
        try:
            raw = tomllib.loads(data.decode("utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"配置文件解析失败 {cfg_path}: {exc}") from exc
    env = _parse_dotenv(root / ".env")
    # 环境变量优先于 .env
    cookie = os.environ.get("NETEASE_COOKIE") or env.get("NETEASE_COOKIE", "")

    p = raw.get("paths", {})
    paths = Paths(
        library=_abs(root, p.get("library", "library"), "NM_LIBRARY"),
        staging=_abs(root, p.get("staging", "staging"), "NM_STAGING"),
        data=_abs(root, p.get("data", "data"), "NM_DATA"),
    )
    n = raw.get("naming", {})
    naming = Naming(template=n.get("template", "{title} - {artist}"), max_length=int(n.get("max_length", 120)))
    m = raw.get("match", {})
    match = MatchRules(
        duration_tolerance_ms=int(m.get("duration_tolerance_ms", 2000)),
        min_lossless_bitrate_kbps=int(m.get("min_lossless_bitrate_kbps", 600)),
    )
    d = raw.get("download", {})
    download = DownloadRules(
        search_interval_s=float(d.get("search_interval_s", 1.2)),
        download_interval_s=float(d.get("download_interval_s", 1.5)),
        window_max_requests=int(d.get("window_max_requests", 10)),
        window_seconds=float(d.get("window_seconds", 60)),
        max_consecutive_failures=int(d.get("max_consecutive_failures", 3)),
        backoff_base_s=float(d.get("backoff_base_s", 5.0)),
        preferred_formats=tuple(x.lower() for x in d.get("preferred_formats", ["flac", "wav", "ape"])),
        executors=tuple(d.get("executors", [])),
    )
    ne = raw.get("netease", {})
    netease = NeteaseConfig(
        api_base=ne.get("api_base", "https://music.163.com").rstrip("/"),
        timeout_s=int(ne.get("timeout_s", 15)),
        cookie=cookie,
    )
    ui = raw.get("ui", {})
    return Config(
        root=root,
        paths=paths,
        naming=naming,
        match=match,
        download=download,
        netease=netease,
        invalid_at_bottom=bool(ui.get("invalid_at_bottom", True)),
        raw=raw,
    )
