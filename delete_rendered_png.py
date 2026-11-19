#!/usr/bin/env python3
"""
递归删除指定目录下的 *_rendered.png 文件。

默认只预览，不删除。确认后加 --execute。

预览：
    python3 delete_rendered_png.py \
      "<项目根目录>/download"

执行删除：
    python3 delete_rendered_png.py \
      "<项目根目录>/download" \
      --execute
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import List, Sequence, Tuple


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="递归删除指定目录下的 *_rendered.png 文件。"
    )
    parser.add_argument(
        "directory",
        type=Path,
        help="要递归扫描的目录",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="真正删除文件；未指定时仅预览",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="只输出汇总，不逐个显示文件路径",
    )
    return parser.parse_args()


def timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def log(message: str, error: bool = False) -> None:
    print(
        f"[{timestamp()}] {message}",
        file=sys.stderr if error else sys.stdout,
        flush=True,
    )


def find_targets(root: Path) -> Tuple[List[Path], List[Path]]:
    targets: List[Path] = []
    skipped_symlinks: List[Path] = []
    for path in root.rglob("*_rendered.png"):
        if not path.is_file():
            continue
        if path.name.endswith("_rendered.capture.png"):
            continue
        if path.is_symlink():
            skipped_symlinks.append(path)
            continue
        targets.append(path)
    return sorted(targets), sorted(skipped_symlinks)


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} TB"


def delete_files(
    targets: Sequence[Path],
    root: Path,
    quiet: bool,
) -> Tuple[int, int, List[Tuple[Path, str]]]:
    deleted = 0
    deleted_bytes = 0
    failures: List[Tuple[Path, str]] = []
    for index, path in enumerate(targets, start=1):
        try:
            size = path.stat().st_size
            path.unlink()
            deleted += 1
            deleted_bytes += size
            if not quiet:
                log(
                    f"[{index}/{len(targets)}] 已删除："
                    f"{path.relative_to(root)} ({human_size(size)})"
                )
        except OSError as exc:
            error = f"{type(exc).__name__}: {exc}"
            failures.append((path, error))
            log(f"删除失败：{path} | {error}", error=True)
    return deleted, deleted_bytes, failures


def main() -> int:
    args = parse_args()
    root = args.directory.expanduser().resolve()
    if not root.is_dir():
        log(f"目录不存在或不是目录：{root}", error=True)
        return 2
    if root.parent == root:
        log("拒绝递归处理文件系统根目录。", error=True)
        return 2

    targets, skipped_symlinks = find_targets(root)
    total_bytes = 0
    for path in targets:
        try:
            total_bytes += path.stat().st_size
        except OSError:
            pass

    log(f"扫描目录：{root}")
    log(
        f"找到 {len(targets)} 个 *_rendered.png，"
        f"合计 {human_size(total_bytes)}。"
    )
    if skipped_symlinks:
        log(f"跳过符号链接：{len(skipped_symlinks)} 个。")

    if not targets:
        log("没有可删除的文件。")
        return 0

    if not args.execute:
        if not args.quiet:
            for index, path in enumerate(targets, start=1):
                try:
                    size = human_size(path.stat().st_size)
                except OSError:
                    size = "未知大小"
                log(
                    f"[{index}/{len(targets)}] 将删除："
                    f"{path.relative_to(root)} ({size})"
                )
        log("当前为预览模式，未删除任何文件。")
        log("确认无误后添加 --execute 执行删除。")
        return 0

    deleted, deleted_bytes, failures = delete_files(
        targets,
        root,
        quiet=args.quiet,
    )
    log(
        f"删除完成：成功 {deleted}，失败 {len(failures)}，"
        f"释放空间 {human_size(deleted_bytes)}。"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        log("操作已取消。", error=True)
        raise SystemExit(130)
