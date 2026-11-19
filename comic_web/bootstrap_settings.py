from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    from .database import Database
    from .settings_repository import SettingsRepository
except ImportError:
    from database import Database
    from settings_repository import SettingsRepository


def main() -> int:
    project_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="初始化或补充漫画库数据库配置。",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=project_root / "comic_web" / "library.db",
    )
    parser.add_argument("--project-root", type=Path, default=project_root)
    parser.add_argument("--download-root", type=Path)
    parser.add_argument("--cdp-url")
    args = parser.parse_args()

    settings = SettingsRepository(
        Database(args.database),
        args.project_root,
    )
    settings.seed_defaults()
    if args.download_root is not None:
        settings.set(
            "download.root",
            str(args.download_root.expanduser().resolve()),
        )
    if args.cdp_url:
        settings.set("download.cdp_url", args.cdp_url)

    verification_code = os.environ.get(
        "COMIC_BOOTSTRAP_VERIFICATION_CODE",
        "",
    )
    if verification_code:
        settings.set_secret(
            "security.verification_code",
            verification_code,
        )
        print("迁移/删除校验码已写入数据库。")

    if not settings.is_configured("security.verification_code"):
        print(
            "警告：尚未配置迁移/删除校验码。请通过管理员系统设置页"
            "或临时设置 COMIC_BOOTSTRAP_VERIFICATION_CODE 后重新运行。",
            file=sys.stderr,
        )
        return 2

    print(f"配置已写入数据库：{Path(args.database).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
