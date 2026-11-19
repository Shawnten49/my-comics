from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from .database import Database
    from .settings_repository import SettingsRepository
except ImportError:
    from database import Database
    from settings_repository import SettingsRepository


@dataclass(frozen=True)
class RuntimeDefaults:
    download_root: Path
    cdp_url: str


def load_runtime_defaults(
    project_root: Optional[Path] = None,
    database_path: Optional[Path] = None,
) -> RuntimeDefaults:
    root = project_root or Path(__file__).resolve().parent.parent
    root = root.expanduser().resolve()
    database = Database(
        database_path or root / "comic_web" / "library.db"
    )
    settings = SettingsRepository(database, root)
    settings.seed_defaults()
    return RuntimeDefaults(
        download_root=settings.get_path(
            "download.root",
            root / "download",
        ),
        cdp_url=settings.get(
            "download.cdp_url",
            "http://127.0.0.1:9222",
        ),
    )
