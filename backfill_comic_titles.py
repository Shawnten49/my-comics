#!/usr/bin/env python3
"""
为旧漫画目录回填原始页面标题和分类元数据。

示例：
    python3 backfill_comic_titles.py --dry-run
    python3 backfill_comic_titles.py --limit 20
    python3 backfill_comic_titles.py --only 40055
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from playwright.sync_api import sync_playwright

from comic_web.indexer import categories_from_title
from comic_web.runtime_settings import load_runtime_defaults
from image_crawler import wait_for_manual_verification


DEFAULT_ROOT = Path(__file__).resolve().parent / "download"


@dataclass
class AlbumInfo:
    album_id: str
    directory: Path
    source_url: str


def parse_args() -> argparse.Namespace:
    runtime_defaults = load_runtime_defaults()
    parser = argparse.ArgumentParser(
        description="回填漫画原始标题和分类元数据",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=runtime_defaults.download_root,
    )
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="只处理指定 ID，可重复传入",
    )
    parser.add_argument("--start-id", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--delay", type=float, default=0.8)
    parser.add_argument("--challenge-wait", type=int, default=360)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--cache-only",
        action="store_true",
        help="只使用本地结果 JSON 缓存，不打开网页",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def log(message: str, error: bool = False) -> None:
    print(
        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}",
        file=sys.stderr if error else sys.stdout,
        flush=True,
    )


def discover_albums(root: Path) -> List[AlbumInfo]:
    albums: List[AlbumInfo] = []
    for directory in sorted(root.iterdir()):
        if not directory.is_dir() or directory.name in {"logs", ".cache"}:
            continue
        manifest_path = directory / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        source_url = str(manifest.get("source_url") or "")
        match = re.search(r"/photo/(\d+)", source_url)
        if not match:
            continue
        albums.append(
            AlbumInfo(
                album_id=match.group(1),
                directory=directory,
                source_url=source_url,
            )
        )
    return albums


def result_title_cache(root: Path) -> Dict[str, str]:
    cache: Dict[str, str] = {}
    for path in (root / "logs").glob("*.result.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        title = str(payload.get("title") or "").strip()
        url = str(payload.get("url") or "")
        match = re.search(r"/photo/(\d+)", url)
        if title and match and not title.startswith("已完成 ID"):
            cache[match.group(1)] = title
    return cache


def write_metadata(
    album: AlbumInfo,
    raw_title: str,
    dry_run: bool,
) -> None:
    categories = categories_from_title(raw_title)
    payload = {
        "source_url": album.source_url,
        "raw_title": raw_title,
        "categories": categories,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if dry_run:
        log(
            f"计划：{album.album_id} -> {len(categories)} 个分类 | {raw_title}"
        )
        return
    metadata_path = album.directory / "metadata.json"
    temporary = metadata_path.with_name("metadata.json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, metadata_path)
    log(
        f"已写入：{album.album_id} -> "
        f"{len(categories)} 个分类 | {raw_title}"
    )


def main() -> int:
    args = parse_args()
    root = args.root.expanduser().resolve()
    if not root.is_dir():
        log(f"漫画目录不存在：{root}", error=True)
        return 2

    albums = discover_albums(root)
    only = {str(value) for value in args.only}
    if only:
        albums = [album for album in albums if album.album_id in only]
    if args.start_id:
        albums = [
            album for album in albums
            if int(album.album_id) >= args.start_id
        ]
    cache = result_title_cache(root)

    pending: List[AlbumInfo] = []
    for album in albums:
        metadata_path = album.directory / "metadata.json"
        if metadata_path.is_file() and not args.force:
            try:
                payload = json.loads(
                    metadata_path.read_text(encoding="utf-8")
                )
                if str(payload.get("raw_title") or "").strip():
                    continue
            except (OSError, json.JSONDecodeError):
                pass
        if album.album_id in cache:
            write_metadata(album, cache[album.album_id], args.dry_run)
            continue
        pending.append(album)

    if args.limit > 0:
        pending = pending[:args.limit]

    if args.cache_only:
        pending = []
        log("cache-only 已开启，本次不访问网页。")
    log(
        f"需要通过网络回填：{len(pending)} 本；"
        f"已从结果缓存命中：{len(cache)} 个 ID。"
    )
    if not pending:
        return 0

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(args.cdp_url)
        if not browser.contexts:
            log("CDP 已连接，但没有浏览器上下文。", error=True)
            return 2
        page = browser.contexts[0].new_page()
        failed = 0
        try:
            for index, album in enumerate(pending, start=1):
                log(
                    f"[{index}/{len(pending)}] "
                    f"{album.album_id} {album.source_url}"
                )
                try:
                    page.goto(
                        album.source_url,
                        wait_until="domcontentloaded",
                        timeout=60000,
                    )
                    page.wait_for_timeout(800)
                    wait_for_manual_verification(
                        page,
                        verification_wait=args.challenge_wait,
                        interactive=True,
                    )
                    raw_title = (page.title() or "").strip()
                    if (
                        not raw_title
                        or raw_title in {"Just a moment...", "请稍候…"}
                    ):
                        raise RuntimeError("页面没有返回有效标题")
                    write_metadata(album, raw_title, args.dry_run)
                except Exception as exc:
                    failed += 1
                    log(
                        f"失败：{album.album_id} | "
                        f"{type(exc).__name__}: {exc}",
                        error=True,
                    )
                if index < len(pending):
                    time.sleep(max(0.0, args.delay))
        finally:
            page.close()

    log(f"标题回填完成：失败 {failed} 本。")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        log("已取消。", error=True)
        raise SystemExit(130)
