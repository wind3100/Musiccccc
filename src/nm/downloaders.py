"""下载执行器：双源冗余的可插拔接口。

需求文档 §下载与匹配：
- 集成两个独立下载软件作为执行器，主源失败或无无损资源时自动切换备用源，对用户透明
- 执行器只负责「搜索候选」与「把文件拉到暂存区」，匹配/校验/命名/清洗由上层统一负责

内置：
- fake  本地清单执行器（离线联调与自动化测试用，也是「双源」演示）
- exec  通用命令行执行器：把任意下载工具包装进来（两条命令即接入一个源）

exec 执行器约定（配置在 config.toml 的 [executors.<name>]）：
    search_cmd = "mytool search --json {query}"       # 输出 JSON 候选数组到 stdout
    fetch_cmd  = "mytool get --id {cand_id} -o {output}"  # 把音频文件写进 {output} 目录
候选 JSON 字段：title / duration_ms / format / size_bytes / id
"""
from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .match import LOSSLESS_FORMATS, Candidate, quality_of
from .naming import normalize_title


class ExecutorError(RuntimeError):
    pass


class Executor(Protocol):
    name: str

    def search(self, title: str, artist: str, song_id: int | None = None) -> list[Candidate]: ...

    def fetch(self, cand: Candidate, dest_dir: Path) -> Path: ...


# ---------------- fake：本地清单执行器 ----------------
class FakeExecutor:
    """从本地 JSON 清单「搜索」，把文件复制到暂存区。

    清单格式（歌曲列表，检索按标题子串匹配，严格匹配交给上层）：
        {"songs": [{"id": 1, "title": "晴天", "duration_ms": 269000,
                    "format": "flac", "file": "/abs/path/晴天.flac"}], "fail": false}
    """

    name = "fake"

    def __init__(self, manifest: Path | str, name: str = "fake"):
        self.name = name
        self.manifest = Path(manifest)
        self._cache: dict | None = None

    def _load(self) -> dict:
        if self._cache is None:
            if not self.manifest.is_file():
                raise ExecutorError(f"清单不存在: {self.manifest}")
            self._cache = json.loads(self.manifest.read_text(encoding="utf-8"))
        return self._cache

    def search(self, title: str, artist: str, song_id: int | None = None) -> list[Candidate]:
        data = self._load()
        if data.get("fail"):
            raise ExecutorError("模拟源失败")
        out: list[Candidate] = []
        for item in data.get("songs", []):
            item_title = str(item.get("title", ""))
            if title and title not in item_title and item_title not in title:
                continue
            out.append(Candidate(
                title=item_title,
                duration_ms=int(item.get("duration_ms", 0)),
                artist=str(item.get("artist", "")),
                album=str(item.get("album", "")),
                fmt=str(item.get("format", "")).lower(),
                size_bytes=int(item.get("size_bytes") or 0),
                source=self.name,
                raw=item,
            ))
        return out

    def fetch(self, cand: Candidate, dest_dir: Path) -> Path:
        item = cand.raw or {}
        src = item.get("file")
        if not src:
            raise ExecutorError(f"清单缺少 file 字段: {cand.title}")
        src_path = Path(src)
        if not src_path.is_absolute():
            src_path = (self.manifest.parent / src_path).resolve()
        if not src_path.is_file():
            raise ExecutorError(f"源文件不存在: {src_path}")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dst = dest_dir / src_path.name
        shutil.copy2(src_path, dst)
        return dst


# ---------------- exec：通用命令行执行器 ----------------
@dataclass
class ExecConfig:
    search_cmd: str
    fetch_cmd: str
    timeout_s: int = 300
    audio_exts: tuple[str, ...] = tuple(sorted(LOSSLESS_FORMATS)) + ("mp3", "m4a", "ogg")


class ExecExecutor:
    """把任意「能搜索、能下载」的命令行工具包装成执行器（接入真实源的最低成本）。"""

    def __init__(self, name: str, config: ExecConfig):
        self.name = name
        self.config = config

    def search(self, title: str, artist: str, song_id: int | None = None) -> list[Candidate]:
        query = f"{title} {artist}".strip()
        cmd = self.config.search_cmd.replace("{query}", _quote(query))
        proc = subprocess.run(shlex.split(cmd, posix=False), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=self.config.timeout_s)
        if proc.returncode != 0:
            raise ExecutorError(f"{self.name} 搜索失败({proc.returncode}): {proc.stderr.strip()[:300]}")
        return self._parse_candidates(proc.stdout)

    def _parse_candidates(self, stdout: str) -> list[Candidate]:
        text = stdout.strip()
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ExecutorError(f"{self.name} 搜索输出不是 JSON: {text[:200]}") from exc
        items = data.get("results", data) if isinstance(data, dict) else data
        out: list[Candidate] = []
        for item in items or []:
            out.append(Candidate(
                title=str(item.get("title", "")),
                duration_ms=int(item.get("duration_ms", 0) or 0),
                artist=str(item.get("artist", "")),
                album=str(item.get("album", "")),
                fmt=str(item.get("format", "")).lower(),
                size_bytes=int(item.get("size_bytes", 0) or 0),
                source=self.name,
                raw=item,
            ))
        return out

    def fetch(self, cand: Candidate, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        cand_id = str((cand.raw or {}).get("id", ""))
        cmd = (self.config.fetch_cmd
               .replace("{cand_id}", _quote(cand_id))
               .replace("{output}", _quote(str(dest_dir)))
               .replace("{query}", _quote(cand.title)))
        proc = subprocess.run(shlex.split(cmd, posix=False), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=self.config.timeout_s)
        if proc.returncode != 0:
            raise ExecutorError(f"{self.name} 下载失败({proc.returncode}): {proc.stderr.strip()[:300]}")
        files = [p for p in dest_dir.rglob("*") if p.is_file() and p.suffix.lstrip(".").lower() in self.config.audio_exts]
        if not files:
            raise ExecutorError(f"{self.name} 下载后未在暂存目录找到音频文件")
        return max(files, key=lambda p: p.stat().st_size)


def _quote(value: str) -> str:
    return '"' + value.replace('"', '') + '"'


# ---------------- dir：把一个「下载目录」当源 ----------------
class DirExecutor:
    """把某个目录里的音频文件当候选来源。

    适用场景：洛雪 / 其他 GUI 下载器先下到目录，或者用户手工丢进来的成品；
    也适合把「已下载好的旧歌」并入本地库。搜索按文件名子串粗筛，严格匹配仍由上层判定。
    """

    name = "dir"

    def __init__(self, path: Path | str, name: str = "dir",
                 audio_exts: tuple[str, ...] | None = None, recursive: bool = True):
        self.name = name
        self.root = Path(path)
        self.recursive = recursive
        self.audio_exts = audio_exts or (
            "flac", "wav", "ape", "aiff", "aif", "mp3", "m4a", "ogg", "opus")
        self._cache: list[tuple[Path, str, str]] | None = None

    def _iter(self) -> list[tuple[Path, str, str]]:
        """返回 [(路径, 标签标题, 标签歌手)]，标签缺失时按文件名猜。"""
        if self._cache is not None:
            return self._cache
        if not self.root.is_dir():
            raise ExecutorError(f"目录不存在: {self.root}")
        pattern = "**/*" if self.recursive else "*"
        out: list[tuple[Path, str, str]] = []
        for path in self.root.glob(pattern):
            if not path.is_file() or path.suffix.lower().lstrip(".") not in self.audio_exts:
                continue
            title, artist = _read_tags(path)
            if not title:
                artist, title = _split_filename(path.stem)
            out.append((path, title, artist))
        self._cache = out
        return out

    def search(self, title: str, artist: str, song_id: int | None = None) -> list[Candidate]:
        from .tagging import probe  # 延迟导入：探测音频才有开销

        wanted = normalize_title(title)
        out: list[Candidate] = []
        for path, tag_title, tag_artist in self._iter():
            norm = normalize_title(tag_title)
            # 只对「名字沾边」的文件做音频探测，避免大目录全量解析
            if wanted and norm and wanted not in norm and norm not in wanted:
                continue
            try:
                info = probe(path)
            except Exception:  # noqa: BLE001 —— 坏文件跳过，不让整轮失败
                continue
            out.append(Candidate(
                title=tag_title or path.stem,
                duration_ms=info.duration_ms,
                artist=tag_artist,
                fmt=info.fmt,
                size_bytes=info.size_bytes,
                bitrate_kbps=info.bitrate_kbps,
                source=self.name,
                raw={"path": str(path)},
            ))
        return out

    def fetch(self, cand: Candidate, dest_dir: Path) -> Path:
        src = Path((cand.raw or {}).get("path", ""))
        if not src.is_file():
            raise ExecutorError(f"源文件已不存在: {src}")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dst = dest_dir / src.name
        shutil.copy2(src, dst)
        return dst


def _read_tags(path: Path) -> tuple[str, str]:
    """读容器自带的 title/artist（仅用于检索，落库命名仍取歌单信息）。

    不用 mutagen 的 easy 模式：WAVE/AIFF 等容器在 easy 模式下拿不到标签。
    """
    try:
        import mutagen

        audio = mutagen.File(str(path))
        tags = getattr(audio, "tags", None) if audio is not None else None
        if not tags:
            return "", ""

        def pick(*keys: str) -> str:
            for key in keys:
                try:
                    if key not in tags:
                        continue
                except TypeError:  # 某些 tags 实现不支持 in
                    continue
                value = tags[key]
                if isinstance(value, (list, tuple)):
                    value = value[0] if value else ""
                text = getattr(value, "text", None)
                if isinstance(text, (list, tuple)):
                    text = text[0] if text else ""
                out = str(text if text is not None else value).strip()
                if out:
                    return out
            return ""

        title = pick("title", "Title", "TIT2", "\xa9nam")
        artist = pick("artist", "Artist", "TPE1", "\xa9ART")
        return title, artist
    except Exception:  # noqa: BLE001
        return "", ""


def _split_filename(stem: str) -> tuple[str, str]:
    """文件名兜底：优先 "歌手 - 歌名"，其次整串当标题。"""
    for sep in (" - ", " -", "- "):
        if sep in stem:
            left, _, right = stem.partition(sep)
            if left.strip() and right.strip():
                return left.strip(), right.strip()
    return "", stem


# ---------------- netease：走网易云官方明文接口的内置源 ----------------
class NeteaseExecutor:
    """内置源：用同一份 Cookie 走 `/api/` 明文通路（weapi 在本机被空响应拦掉）。

    search 阶段就把「实际可用音质」查清楚（/song/enhance/player/url/v1?level=lossless）：
    - 拿到 flac → 候选 fmt=flac，上层 pick_best 选中并下载
    - 只拿得到 mp3 → 候选 fmt=mp3，上层判定"无无损资源"转人工审批，绝不自动降级
    fetch 再次取直链并流式落盘到暂存区。
    """

    name = "netease"

    def __init__(self, client, name: str = "netease", timeout_s: int = 120):
        self.client = client
        self.name = name
        self.timeout_s = timeout_s

    def _candidate(self, detail, url) -> Candidate:
        return Candidate(
            title=detail.title,
            duration_ms=detail.duration_ms,
            artist=detail.artist,
            album=detail.album,
            fmt=(url.fmt or "unknown").lower(),
            size_bytes=url.size,
            bitrate_kbps=url.bitrate_kbps or None,
            source=self.name,
            raw={"id": detail.song_id, "url": url.url, "fee": url.fee},
        )

    def search(self, title: str, artist: str, song_id: int | None = None) -> list[Candidate]:
        from .netease import NeteaseError, parse_track

        if song_id:  # 歌单里有精确 id：直接查详情+直链，最准也最省请求
            detail = self.client.song_details([song_id]).get(song_id)
            if detail is None:
                raise ExecutorError(f"取不到歌曲详情: {song_id}")
            url = self.client.play_url(song_id, level="lossless")
            return [self._candidate(detail, url)]

        # 没有 id（例如别人给的歌单）：关键词搜索 → 只保留名称归一化后完全相同的候选
        items = self.client.search_songs(f"{title} {artist}".strip(), limit=10)
        out: list[Candidate] = []
        for item in items:
            detail = parse_track(item)
            if normalize_title(detail.title) != normalize_title(title):
                continue
            try:
                url = self.client.play_url(detail.song_id, level="lossless")
            except NeteaseError as exc:
                raise ExecutorError(f"取直链失败: {exc}") from exc
            out.append(self._candidate(detail, url))
        return out

    def fetch(self, cand: Candidate, dest_dir: Path) -> Path:
        song_id = int((cand.raw or {}).get("id") or 0)
        if not song_id:
            raise ExecutorError("候选缺少 song_id")
        url = (cand.raw or {}).get("url") or self.client.play_url(song_id, level="lossless").url
        if not url:
            raise ExecutorError(f"拿不到播放直链（可能需要 VIP 或已下架）: {song_id}")
        ext = (cand.fmt or "flac").lower()
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / f"{song_id}.{ext}"
        with self.client.session.get(url, stream=True, timeout=self.timeout_s) as resp:
            if resp.status_code != 200:
                raise ExecutorError(f"下载失败 HTTP {resp.status_code}")
            with path.open("wb") as fh:
                for chunk in resp.iter_content(1 << 20):
                    if chunk:
                        fh.write(chunk)
        if path.stat().st_size <= 0:
            raise ExecutorError("下载到的文件为空")
        return path


def build_executors(cfg) -> list[Executor]:
    """按 config.toml 的 [download].executors 顺序装配执行器（主源 → 备源）。"""
    executors: list[Executor] = []
    table = (cfg.raw.get("executors") or {})
    for name in cfg.download.executors:
        spec = table.get(name) or {}
        kind = spec.get("kind", name)
        if kind == "fake":
            manifest = spec.get("manifest")
            if not manifest:
                raise ExecutorError(f"执行器 {name} 缺少 manifest 配置")
            path = Path(manifest)
            if not path.is_absolute():
                path = (cfg.root / path).resolve()
            executors.append(FakeExecutor(path, name=name))
        elif kind == "exec":
            need = ("search_cmd", "fetch_cmd")
            missing = [k for k in need if not spec.get(k)]
            if missing:
                raise ExecutorError(f"执行器 {name} 缺少配置: {missing}")
            executors.append(ExecExecutor(name, ExecConfig(
                search_cmd=spec["search_cmd"],
                fetch_cmd=spec["fetch_cmd"],
                timeout_s=int(spec.get("timeout_s", 300)),
            )))
        elif kind == "netease":
            from .netease import NeteaseClient

            if not cfg.netease.cookie:
                raise ExecutorError(f"执行器 {name} 需要 NETEASE_COOKIE（先用 nm cookie 录入）")
            client = NeteaseClient(cfg.netease.api_base, cfg.netease.cookie, cfg.netease.timeout_s)
            executors.append(NeteaseExecutor(client, name=name))
        elif kind == "dir":
            target = spec.get("path")
            if not target:
                raise ExecutorError(f"执行器 {name} 缺少 path 配置（下载目录）")
            path = Path(target)
            if not path.is_absolute():
                path = (cfg.root / path).resolve()
            executors.append(DirExecutor(path, name=name,
                                         recursive=bool(spec.get("recursive", True))))
        else:
            raise ExecutorError(f"未知执行器类型: {kind}")
    return executors
