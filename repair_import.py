#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from comic_web.indexer import ComicLibrary
from comic_web.runtime_settings import load_runtime_defaults


def main() -> int:
    project_root = Path(__file__).resolve().parent
    defaults = load_runtime_defaults(project_root)
    parser = argparse.ArgumentParser(
        description=(
            "显式扫描本地目录并修复数据库。"
            "该工具不会在网站启动时自动执行。"
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=defaults.download_root,
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=project_root / "comic_web" / "library.db",
    )
    args = parser.parse_args()
    library = ComicLibrary(
        args.root.expanduser().resolve(),
        database_path=args.database.expanduser().resolve(),
    )
    library.rebuild(blocking=True)
    print(f"数据库修复完成，共 {len(library.books())} 本漫画。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
