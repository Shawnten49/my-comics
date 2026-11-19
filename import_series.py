#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, List

from comic_web.runtime_settings import load_runtime_defaults


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_ROOT = PROJECT_ROOT / "download"


def safe_folder_title(value: str) -> str:
    value = re.sub(r'[\\/:*?"<>|]+', "_", value or "").strip(" .")
    value = re.sub(r"\s+", " ", value)
    return value or "未命名漫画"


def read_chapters_file(path: Path) -> List[dict[str, Any]]:
    payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("章节 JSON 必须是数组。")
    return [item for item in payload if isinstance(item, dict)]


def discover_chapters(book_dir: Path) -> List[dict[str, Any]]:
    chapters_root = book_dir / "chapters"
    if not chapters_root.is_dir():
        return []
    result = []
    for order, directory in enumerate(
        sorted(
            item for item in chapters_root.iterdir()
            if item.is_dir() and not item.is_symlink()
        ),
        start=1,
    ):
        match = re.match(r"^(\d+)_?(.*)$", directory.name)
        title = match.group(2).strip() if match else directory.name
        title = title or directory.name
        result.append(
            {
                "key": directory.name,
                "title": title,
                "kind": "volume" if "卷" in title else "chapter",
                "order": order,
                "source_url": "",
                "directory": directory.relative_to(book_dir).as_posix(),
            }
        )
    return result


def main() -> int:
    runtime_defaults = load_runtime_defaults(PROJECT_ROOT)
    parser = argparse.ArgumentParser(
        description="生成本地多话漫画的 series.json",
    )
    parser.add_argument("--book-id", required=True, type=int)
    parser.add_argument("--url", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--raw-title", default="")
    parser.add_argument(
        "--root",
        type=Path,
        default=runtime_defaults.download_root,
    )
    parser.add_argument(
        "--chapters-json",
        type=Path,
        default=None,
        help="可选章节数组 JSON；不传则扫描 chapters/ 子目录",
    )
    args = parser.parse_args()
    if args.book_id <= 0:
        print("book-id 必须是正整数。")
        return 2
    root = args.root.expanduser().resolve()
    title = args.title.strip() or f"漫画 {args.book_id}"
    folder = f"{args.book_id}_{safe_folder_title(title)}"
    book_dir = root / folder
    book_dir.mkdir(parents=True, exist_ok=True)
    chapters = (
        read_chapters_file(args.chapters_json)
        if args.chapters_json
        else discover_chapters(book_dir)
    )
    normalized = []
    for order, chapter in enumerate(chapters, start=1):
        directory = str(chapter.get("directory") or "").strip()
        if not directory:
            continue
        normalized.append(
            {
                "key": str(chapter.get("key") or directory),
                "title": str(chapter.get("title") or directory),
                "raw_title": str(
                    chapter.get("raw_title")
                    or chapter.get("title")
                    or directory
                ),
                "kind": str(
                    chapter.get("kind")
                    or ("volume" if "卷" in str(chapter.get("title")) else "chapter")
                ),
                "order": int(chapter.get("order") or order),
                "source_url": str(chapter.get("source_url") or ""),
                "directory": directory,
            }
        )
    payload = {
        "content_type": "chaptered",
        "book_id": args.book_id,
        "title": title,
        "raw_title": args.raw_title.strip() or title,
        "source_url": args.url.strip(),
        "cover_file": "",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "chapters": normalized,
    }
    target = book_dir / "series.json"
    temporary = target.with_name("series.json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, target)
    print(f"已生成：{target}")
    print(f"章节数量：{len(normalized)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
