"""暂存区与原子化落库。

需求文档：
- 下载输出到暂存区，重命名、清洗、校验都在暂存区完成
- 成功后原子化移动到正式库；失败时清理暂存，不留痕迹
- 绿色：不写系统目录，所有文件在程序同级目录
"""
from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from pathlib import Path


def sha256_of(path: Path | str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def new_job_dir(staging_root: Path | str, song_id: int | str) -> Path:
    """为一首歌创建独立暂存目录（避免并发/重名互相污染）。"""
    d = Path(staging_root) / f"{song_id}-{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=False)
    return d


def atomic_move(src: Path | str, dst: Path | str) -> Path:
    """原子化落库：先复制到同目录临时名，再 os.replace 改名。

    跨盘时 shutil.move 无法原子，故显式两步 + 同目录临时文件。
    """
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part")
    if tmp.exists():
        tmp.unlink()
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)  # 同目录 replace 原子
    return dst


def cleanup_job_dir(job_dir: Path | str) -> None:
    """失败清理：暂存目录整体删除，不留垃圾文件。"""
    d = Path(job_dir)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def target_path(library_root: Path | str, filename: str, ext: str) -> Path:
    """正式库目标路径：{filename}.{ext}；同名已存在时加 (2)(3) 序号，绝不覆盖已有文件。"""
    root = Path(library_root)
    ext = ext.lower().lstrip(".")
    candidate = root / f"{filename}.{ext}"
    if not candidate.exists():
        return candidate
    for i in range(2, 1000):
        candidate = root / f"{filename} ({i}).{ext}"
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"同名文件过多: {filename}")


def free_bytes(path: Path | str) -> int:
    return shutil.disk_usage(str(path)).free


def same_file_by_hash(a: Path | str, b: Path | str) -> bool:
    pa, pb = Path(a), Path(b)
    if pa.stat().st_size != pb.stat().st_size:
        return False
    return sha256_of(pa) == sha256_of(pb)
