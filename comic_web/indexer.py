from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

try:
    from .database import Database
    from .storage import (
        LOCAL_STORAGE_ID,
        BookContentMissing,
        StorageAccessError,
        StorageRegistry,
    )
except ImportError:
    from database import Database
    from storage import (
        LOCAL_STORAGE_ID,
        BookContentMissing,
        StorageAccessError,
        StorageRegistry,
    )


IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp", ".svg"
}
ID_PREFIX_RE = re.compile(r"^(\d+)(?:_|$)")
CATEGORY_RE = re.compile(
    r"[\[［]([^\]］]+)[\]］]|[\(（]([^\)）]+)[\)）]"
)


@dataclass
class PageEntry:
    index: int
    filename: str
    rendered: bool


@dataclass
class ChapterEntry:
    id: Optional[int]
    book_id: str
    chapter_key: str
    title: str
    raw_title: str
    chapter_type: str
    sort_order: int
    source_url: str
    directory: str
    page_count: int
    cover: str = ""
    cover_version: str = ""
    complete: bool = False
    metadata_missing: bool = True
    missing: bool = False
    updated_at: str = ""

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "book_id": self.book_id,
            "chapter_key": self.chapter_key,
            "title": self.title,
            "raw_title": self.raw_title,
            "chapter_type": self.chapter_type,
            "sort_order": self.sort_order,
            "source_url": self.source_url,
            "directory": self.directory,
            "page_count": self.page_count,
            "complete": self.complete,
            "missing": self.missing,
            "updated_at": self.updated_at,
            "read_url": (
                f"/reader/{quote(str(self.book_id), safe='')}/chapter/{self.id}"
                if self.id is not None
                else ""
            ),
        }


@dataclass
class BookEntry:
    id: int
    title: str
    directory: str
    page_count: int
    cover: str = ""
    cover_version: str = ""
    updated_at: str = ""
    created_at: str = ""
    complete: bool = False
    raw_title: str = ""
    source_url: str = ""
    categories: List[str] = field(default_factory=list)
    metadata_missing: bool = True
    missing: bool = False
    storage_id: str = LOCAL_STORAGE_ID
    storage_name: str = "本地目录"
    storage_path: str = ""
    availability: str = "available"
    availability_error: str = ""
    content_type: str = "single"
    chapter_count: int = 0
    chapter_complete_count: int = 0
    chapter_updated_at: str = ""
    access_level: int = 0
    pages: List[PageEntry] = field(default_factory=list, repr=False)

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "page_count": self.page_count,
            "cover_url": (
                (
                    f"/api/books/{quote(str(self.id), safe='')}/cover-file"
                    if self.content_type == "chaptered"
                    else f"/media/{self.id}/{quote(self.cover, safe='')}"
                )
                if self.cover else ""
            ),
            "thumbnail_url": (
                f"/api/books/{quote(str(self.id), safe='')}/thumbnail"
                f"?v={self.cover_version}"
            ),
            "cover_file": self.cover,
            "cover_version": self.cover_version,
            "updated_at": self.updated_at,
            "complete": self.complete,
            "categories": self.categories,
            "metadata_missing": self.metadata_missing,
            "folder_name": Path(self.directory).name,
            "content_type": self.content_type,
            "chapter_count": self.chapter_count,
            "chapter_complete_count": self.chapter_complete_count,
            "chapter_updated_at": self.chapter_updated_at,
            "detail_url": (
                f"/series/{quote(str(self.id), safe='')}"
                if self.content_type == "chaptered"
                else f"/reader/{quote(str(self.id), safe='')}"
            ),
            "storage_id": self.storage_id,
            "storage_name": self.storage_name,
            "storage_path": self.storage_path,
            "availability": self.availability,
            "availability_error": self.availability_error,
            "access_level": self.access_level,
        }


def manifest_is_complete(manifest: dict) -> bool:
    try:
        image_count = int(manifest.get("image_count", 0))
        downloaded = int(manifest.get("downloaded", 0))
        skipped = int(manifest.get("skipped", 0))
        failed = int(manifest.get("failed", 0))
    except (TypeError, ValueError):
        return False
    return (
        image_count > 0
        and failed == 0
        and downloaded + skipped >= image_count
    )


def page_number(filename: str, fallback: int) -> Tuple[int, bool]:
    stem = Path(filename).stem
    if stem.endswith("_rendered"):
        return (
            int(stem[:-9]) if stem[:-9].isdigit() else fallback,
            True,
        )
    return (int(stem) if stem.isdigit() else fallback, False)


def preferred_pages(
    image_files: List[Path],
    manifest: dict,
) -> List[PageEntry]:
    render_mode = str(manifest.get("render_mode") or "")
    grouped: Dict[int, List[Tuple[int, Path, bool]]] = {}
    for order, path in enumerate(sorted(image_files), start=1):
        number, rendered = page_number(path.name, order)
        suffix = path.suffix.lower()
        if rendered and suffix == ".webp":
            priority = 100
        elif rendered:
            priority = 90
        elif suffix == ".webp":
            priority = 70
        elif suffix in {".jpg", ".jpeg"}:
            priority = 60
        elif suffix == ".png":
            priority = 55
        else:
            priority = 50
        if render_mode == "original" and not rendered:
            priority += 30
        elif render_mode == "rendered" and rendered:
            priority += 30
        grouped.setdefault(number, []).append((priority, path, rendered))

    result: List[PageEntry] = []
    for index, number in enumerate(sorted(grouped), start=1):
        priority, path, rendered = sorted(
            grouped[number],
            key=lambda item: (-item[0], item[1].name.lower()),
        )[0]
        del priority
        result.append(
            PageEntry(index=index, filename=path.name, rendered=rendered)
        )
    return result


def normalize_category(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"\s+", " ", normalized).strip()


def categories_from_title(title: str) -> List[str]:
    result: List[str] = []
    seen = set()
    for match in CATEGORY_RE.finditer(title or ""):
        value = normalize_category(match.group(1) or match.group(2) or "")
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def display_title(directory_name: str, book_id: int) -> str:
    title = re.sub(
        rf"^{re.escape(str(book_id))}_?",
        "",
        directory_name,
        count=1,
    )
    title = re.sub(r"\s+", " ", title).strip(" .")
    return title or (f"漫画 {book_id}" if book_id else directory_name)


class ComicLibrary:
    def __init__(
        self,
        root: Path,
        database_path: Optional[Path] = None,
    ) -> None:
        self.root = root.expanduser().resolve()
        self.database = Database(
            database_path
            if database_path is not None
            else Path(__file__).with_name("library.db")
        )
        self.storage = StorageRegistry(self.database, self.root)
        self._scan_lock = threading.Lock()
        self._pages_lock = threading.RLock()
        self._pages_cache: Dict[
            str,
            Tuple[Tuple[str, str, int], List[PageEntry]],
        ] = {}
        self.scanned_at = ""
        self.cache_dir = self.root / ".cache"
        self.thumbnail_dir = self.cache_dir / "thumbnails"

    def rebuild(
        self,
        blocking: bool = True,
        *,
        only_book_id: Optional[str] = None,
    ) -> bool:
        if not self._scan_lock.acquire(blocking=blocking):
            return False
        try:
            self._rebuild_locked(only_book_id=only_book_id)
        finally:
            self._scan_lock.release()
        return True

    def _scan_series_directory(
        self,
        current: Path,
        metadata: dict,
        started: str,
    ) -> Optional[Tuple[BookEntry, List[ChapterEntry]]]:
        prefix = ID_PREFIX_RE.match(current.name)
        book_id_value = str(metadata.get("book_id") or "").strip()
        book_id = int(book_id_value) if book_id_value.isdigit() else 0
        if not book_id and prefix:
            book_id = int(prefix.group(1))
        if not book_id:
            return None
        raw_title = str(metadata.get("raw_title") or "").strip()
        display = str(metadata.get("title") or "").strip()
        if not display:
            display = display_title(current.name, book_id)
        categories = categories_from_title(raw_title or display)
        chapter_specs = metadata.get("chapters")
        if not isinstance(chapter_specs, list):
            chapter_specs = []
            chapters_root = current / "chapters"
            if chapters_root.is_dir():
                chapter_specs = [
                    {
                        "key": path.name,
                        "title": display_title(path.name, ""),
                        "order": order,
                        "directory": path.relative_to(current).as_posix(),
                    }
                    for order, path in enumerate(
                        sorted(
                            item for item in chapters_root.iterdir()
                            if item.is_dir() and not item.is_symlink()
                        ),
                        start=1,
                    )
                ]

        chapters: List[ChapterEntry] = []
        for fallback_order, spec in enumerate(chapter_specs, start=1):
            if not isinstance(spec, dict):
                continue
            directory_value = str(spec.get("directory") or "").strip()
            if not directory_value:
                continue
            chapter_dir = (current / directory_value).resolve(strict=False)
            try:
                chapter_dir.relative_to(current.resolve())
            except ValueError:
                continue
            if (chapter_dir / ".batch-download-task.json").is_file():
                # Do not expose a chapter that is still being written by the
                # batch worker. A failed marker also keeps partial pages out
                # of the library until the user retries successfully.
                images = []
            else:
                images = self._valid_images(chapter_dir)
            manifest = self._read_json(chapter_dir / "manifest.json")
            pages = preferred_pages(images, manifest) if images else []
            chapter_title = str(spec.get("title") or "").strip()
            if not chapter_title:
                chapter_title = display_title(chapter_dir.name, "")
            chapter_key = str(spec.get("key") or "").strip()
            if not chapter_key:
                chapter_key = f"chapter-{fallback_order}"
            try:
                sort_order = int(spec.get("order") or fallback_order)
            except (TypeError, ValueError):
                sort_order = fallback_order
            cover = pages[0].filename if pages else ""
            cover_version = ""
            updated_at = ""
            if pages:
                cover_path = chapter_dir / cover
                try:
                    stat = cover_path.stat()
                    cover_version = hashlib.sha1(
                        (
                            cover
                            + f"|{stat.st_mtime_ns}|{stat.st_size}"
                        ).encode("utf-8")
                    ).hexdigest()[:12]
                    updated_at = datetime.fromtimestamp(
                        max(
                            item.stat().st_mtime
                            for item in images
                            if item.exists()
                        )
                    ).isoformat(timespec="seconds")
                except (OSError, ValueError):
                    pass
            chapters.append(
                ChapterEntry(
                    id=None,
                    book_id=book_id,
                    chapter_key=chapter_key,
                    title=chapter_title,
                    raw_title=str(spec.get("raw_title") or chapter_title),
                    chapter_type=str(spec.get("kind") or "chapter"),
                    sort_order=sort_order,
                    source_url=str(spec.get("source_url") or ""),
                    directory=chapter_dir.relative_to(
                        current.resolve()
                    ).as_posix(),
                    page_count=len(pages),
                    cover=cover,
                    cover_version=cover_version,
                    complete=manifest_is_complete(manifest),
                    metadata_missing=not bool(chapter_title),
                    missing=not chapter_dir.is_dir() or not pages,
                    updated_at=updated_at,
                )
            )

        chapters.sort(key=lambda chapter: (chapter.sort_order, chapter.title))
        for index, chapter in enumerate(chapters, start=1):
            chapter.sort_order = index
        cover = ""
        requested_cover = str(metadata.get("cover_file") or "").strip()
        if requested_cover:
            requested_path = (current / requested_cover).resolve(strict=False)
            try:
                requested_path.relative_to(current.resolve())
                if requested_path.is_file():
                    cover = requested_path.relative_to(current.resolve()).as_posix()
            except ValueError:
                pass
        if not cover:
            first_cover = next(
                (
                    chapter
                    for chapter in chapters
                    if chapter.cover and not chapter.missing
                ),
                None,
            )
            if first_cover is not None:
                cover = (
                    Path(first_cover.directory) / first_cover.cover
                ).as_posix()
        cover_version = ""
        if cover:
            try:
                stat = (current / cover).stat()
                cover_version = hashlib.sha1(
                    (
                        cover
                        + f"|{stat.st_mtime_ns}|{stat.st_size}"
                    ).encode("utf-8")
                ).hexdigest()[:12]
            except OSError:
                pass
        chapter_updated_at = max(
            (chapter.updated_at for chapter in chapters if chapter.updated_at),
            default="",
        )
        complete_count = sum(
            1 for chapter in chapters if chapter.complete and not chapter.missing
        )
        book = BookEntry(
            id=book_id,
            title=display,
            directory=str(current.relative_to(self.root)),
            page_count=sum(chapter.page_count for chapter in chapters),
            cover=cover,
            cover_version=cover_version,
            updated_at=chapter_updated_at
            or datetime.fromtimestamp(
                current.stat().st_mtime
            ).isoformat(timespec="seconds"),
            created_at=started,
            complete=bool(chapters) and complete_count == len(chapters),
            raw_title=raw_title or display,
            source_url=str(metadata.get("source_url") or ""),
            categories=categories,
            metadata_missing=False,
            storage_id=LOCAL_STORAGE_ID,
            storage_name="本地目录",
            storage_path=str(self.root),
            content_type="chaptered",
            chapter_count=len(chapters),
            chapter_complete_count=complete_count,
            chapter_updated_at=chapter_updated_at,
        )
        return book, chapters

    @staticmethod
    def _valid_images(directory: Path) -> List[Path]:
        if not directory.is_dir():
            return []
        return sorted(
            path
            for path in directory.iterdir()
            if path.is_file()
            and not path.name.startswith("._")
            and path.name not in {".DS_Store", ".localized"}
            and path.suffix.lower() in IMAGE_EXTENSIONS
            and not path.name.endswith("_rendered.capture.png")
            and not path.name.endswith(".part")
        )

    def _rebuild_locked(self, only_book_id: Optional[str] = None) -> None:
        started = datetime.now().isoformat(timespec="seconds")
        discovered: List[Tuple[BookEntry, dict, List[Path]]] = []
        discovered_series: List[Tuple[BookEntry, List[ChapterEntry]]] = []
        used_ids = set()
        with self.database.connect() as connection:
            external_ids = {
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM books WHERE storage_id <> ?",
                    (LOCAL_STORAGE_ID,),
                )
            }
            target_row = None
            if only_book_id is not None:
                target_row = connection.execute(
                    """
                    SELECT id, directory, storage_id
                    FROM books WHERE id = ?
                    """,
                    (only_book_id,),
                ).fetchone()
                if (
                    target_row is None
                    or target_row["storage_id"] != LOCAL_STORAGE_ID
                ):
                    return
                external_ids = set()
        target_dir = (
            (self.root / target_row["directory"]).resolve(strict=False)
            if target_row is not None else None
        )
        target_top_name = (
            Path(target_row["directory"]).parts[0]
            if target_row is not None else ""
        )

        for current_root, dirs, files in os.walk(self.root):
            current = Path(current_root)
            dirs[:] = sorted(
                name for name in dirs
                if not name.startswith(".")
                and name not in {"logs", ".cache", "thumbnails"}
                and not (current / name).is_symlink()
            )
            if only_book_id is not None:
                if current == self.root:
                    dirs[:] = [
                        name for name in dirs if name == target_top_name
                    ]
                elif (
                    target_dir is None
                    or (
                        current != target_dir
                        and target_dir not in current.parents
                    )
                ):
                    dirs[:] = []
                    continue
            if current == self.root:
                continue
            if (current / ".download-task.json").is_file():
                dirs[:] = []
                continue
            series_metadata = self._read_json(current / "series.json")
            if series_metadata.get("content_type") == "chaptered":
                series = self._scan_series_directory(
                    current,
                    series_metadata,
                    started,
                )
                if series is not None:
                    book, chapters = series
                    if book.id not in external_ids:
                        used_ids.add(book.id)
                        discovered_series.append((book, chapters))
                dirs[:] = []
                continue
            images = [
                current / name for name in files
                if Path(name).suffix.lower() in IMAGE_EXTENSIONS
                and not name.startswith("._")
                and name not in {".DS_Store", ".localized"}
                and not name.endswith("_rendered.capture.png")
                and not name.endswith(".part")
            ]
            if not images:
                continue

            manifest = self._read_json(current / "manifest.json")
            metadata = self._read_json(current / "metadata.json")
            prefix = ID_PREFIX_RE.match(current.name)
            book_id = int(prefix.group(1)) if prefix else 0
            if not book_id:
                source_url = str(manifest.get("source_url") or "")
                match = re.search(r"/photo/(\d+)", source_url)
                if match:
                    book_id = int(match.group(1))
            if not book_id:
                continue
            if book_id in used_ids:
                continue
            if book_id in external_ids:
                # A migrated book may reappear locally after a manual copy.
                # Keep the authoritative external record and do not overwrite it.
                continue
            used_ids.add(book_id)

            raw_title = str(
                metadata.get("raw_title")
                or manifest.get("title")
                or ""
            ).strip()
            categories = categories_from_title(raw_title or current.name)
            pages = preferred_pages(images, manifest)
            if not pages:
                continue
            mtimes = []
            for image in images:
                try:
                    mtimes.append(image.stat().st_mtime)
                except OSError:
                    pass
            updated = max(mtimes) if mtimes else current.stat().st_mtime
            cover = self._cover_for_page(pages, current)
            cover_path = current / cover
            try:
                stat = cover_path.stat()
                cover_version = hashlib.sha1(
                    (
                        cover
                        + f"|{stat.st_mtime_ns}|{stat.st_size}"
                    ).encode("utf-8")
                ).hexdigest()[:12]
            except OSError:
                cover_version = ""
            source_url = str(
                metadata.get("source_url")
                or manifest.get("source_url")
                or ""
            )
            book = BookEntry(
                id=book_id,
                title=display_title(current.name, book_id),
                directory=str(current.relative_to(self.root)),
                page_count=len(pages),
                cover=cover,
                cover_version=cover_version,
                updated_at=datetime.fromtimestamp(updated).isoformat(
                    timespec="seconds"
                ),
                created_at=started,
                complete=manifest_is_complete(manifest),
                raw_title=raw_title,
                source_url=source_url,
                categories=categories,
                metadata_missing=not bool(raw_title),
                storage_id=LOCAL_STORAGE_ID,
                storage_name="本地目录",
                storage_path=str(self.root),
                pages=pages,
            )
            discovered.append((book, manifest, images))

        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if only_book_id is None:
                existing_rows = connection.execute(
                    "SELECT id, storage_id FROM books"
                ).fetchall()
            else:
                existing_rows = connection.execute(
                    "SELECT id, storage_id FROM books WHERE id = ?",
                    (only_book_id,),
                ).fetchall()
            existing_ids = {row["id"] for row in existing_rows}
            local_ids = {
                row["id"]
                for row in existing_rows
                if row["storage_id"] == LOCAL_STORAGE_ID
            }
            discovered_local_ids = {
                book.id for book, _, _ in discovered
            } | {
                book.id for book, _ in discovered_series
            }
            if only_book_id is None:
                connection.execute(
                    "UPDATE books SET missing = 1 WHERE storage_id = ?",
                    (LOCAL_STORAGE_ID,),
                )
            else:
                connection.execute(
                    "UPDATE books SET missing = 1 WHERE id = ?",
                    (only_book_id,),
                )
            missing_local_ids = local_ids - discovered_local_ids
            if missing_local_ids:
                placeholders = ",".join("?" for _ in missing_local_ids)
                connection.execute(
                    f"DELETE FROM book_categories WHERE book_id IN ({placeholders})",
                    tuple(missing_local_ids),
                )
            for book, manifest, images in discovered:
                del manifest, images
                search_text = normalize_category(
                    " ".join(
                        [
                            str(book.id),
                            book.title,
                            book.raw_title,
                        ] + book.categories
                    )
                ).casefold()
                connection.execute(
                    """
                    INSERT INTO books(
                        id, title, raw_title, directory, source_url,
                        page_count, cover_file, cover_version, complete,
                        metadata_missing, directory_mtime_ns, updated_at,
                        created_at, missing, search_text, storage_id,
                        content_type, chapter_count, chapter_complete_count,
                        chapter_updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?,
                        'single', 0, 0, NULL
                    )
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        raw_title = excluded.raw_title,
                        directory = excluded.directory,
                        source_url = excluded.source_url,
                        page_count = excluded.page_count,
                        cover_file = excluded.cover_file,
                        cover_version = excluded.cover_version,
                        complete = excluded.complete,
                        metadata_missing = excluded.metadata_missing,
                        directory_mtime_ns = excluded.directory_mtime_ns,
                        updated_at = excluded.updated_at,
                        missing = 0,
                        search_text = excluded.search_text,
                        content_type = 'single',
                        chapter_count = 0,
                        chapter_complete_count = 0,
                        chapter_updated_at = NULL
                    """,
                    (
                        book.id, book.title, book.raw_title, book.directory,
                        book.source_url, book.page_count, book.cover,
                        book.cover_version, int(book.complete),
                        int(book.metadata_missing),
                        (self.root / book.directory).stat().st_mtime_ns,
                        book.updated_at, book.created_at, search_text,
                        LOCAL_STORAGE_ID,
                    ),
                )
                connection.execute(
                    "DELETE FROM book_categories WHERE book_id = ?",
                    (book.id,),
                )
                for category in book.categories:
                    normalized = normalize_category(category)
                    connection.execute(
                        """
                        INSERT INTO categories(
                            name, normalized_name, book_count,
                            blocked, created_at, updated_at
                        ) VALUES (?, ?, 0, 0, ?, ?)
                        ON CONFLICT(normalized_name) DO UPDATE SET
                            name = excluded.name,
                            updated_at = excluded.updated_at
                        """,
                        (
                            normalized, normalized.casefold(), started, started,
                        ),
                    )
                    row = connection.execute(
                        "SELECT id FROM categories WHERE normalized_name = ?",
                        (normalized.casefold(),),
                    ).fetchone()
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO book_categories(book_id, category_id)
                        VALUES (?, ?)
                        """,
                        (book.id, row["id"]),
                    )

            for book, chapters in discovered_series:
                search_text = normalize_category(
                    " ".join(
                        [
                            str(book.id),
                            book.title,
                            book.raw_title,
                        ]
                        + book.categories
                    )
                ).casefold()
                connection.execute(
                    """
                    INSERT INTO books(
                        id, title, raw_title, directory, source_url,
                        page_count, cover_file, cover_version, complete,
                        metadata_missing, directory_mtime_ns, updated_at,
                        created_at, missing, search_text, storage_id,
                        content_type, chapter_count, chapter_complete_count,
                        chapter_updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?,
                        'chaptered', ?, ?, ?
                    )
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        raw_title = excluded.raw_title,
                        directory = excluded.directory,
                        source_url = excluded.source_url,
                        page_count = excluded.page_count,
                        cover_file = excluded.cover_file,
                        cover_version = excluded.cover_version,
                        complete = excluded.complete,
                        metadata_missing = excluded.metadata_missing,
                        directory_mtime_ns = excluded.directory_mtime_ns,
                        updated_at = excluded.updated_at,
                        missing = 0,
                        search_text = excluded.search_text,
                        content_type = 'chaptered',
                        chapter_count = excluded.chapter_count,
                        chapter_complete_count =
                            excluded.chapter_complete_count,
                        chapter_updated_at = excluded.chapter_updated_at
                    """,
                    (
                        book.id,
                        book.title,
                        book.raw_title,
                        book.directory,
                        book.source_url,
                        book.page_count,
                        book.cover,
                        book.cover_version,
                        int(book.complete),
                        int(book.metadata_missing),
                        (self.root / book.directory).stat().st_mtime_ns,
                        book.updated_at,
                        book.created_at,
                        search_text,
                        LOCAL_STORAGE_ID,
                        book.chapter_count,
                        book.chapter_complete_count,
                        book.chapter_updated_at or None,
                    ),
                )
                connection.execute(
                    "DELETE FROM book_categories WHERE book_id = ?",
                    (book.id,),
                )
                for category in book.categories:
                    normalized = normalize_category(category)
                    connection.execute(
                        """
                        INSERT INTO categories(
                            name, normalized_name, book_count,
                            blocked, created_at, updated_at
                        ) VALUES (?, ?, 0, 0, ?, ?)
                        ON CONFLICT(normalized_name) DO UPDATE SET
                            name = excluded.name,
                            updated_at = excluded.updated_at
                        """,
                        (
                            normalized,
                            normalized.casefold(),
                            started,
                            started,
                        ),
                    )
                    row = connection.execute(
                        """
                        SELECT id FROM categories
                        WHERE normalized_name = ?
                        """,
                        (normalized.casefold(),),
                    ).fetchone()
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO book_categories(
                            book_id, category_id
                        ) VALUES (?, ?)
                        """,
                        (book.id, row["id"]),
                    )

                # Move existing sort positions out of the way during reorder.
                connection.execute(
                    """
                    UPDATE book_chapters SET sort_order = -id
                    WHERE book_id = ?
                    """,
                    (book.id,),
                )
                chapter_keys = []
                for chapter in chapters:
                    chapter_dir = self.root / book.directory / chapter.directory
                    connection.execute(
                        """
                        INSERT INTO book_chapters(
                            book_id, chapter_key, title, raw_title,
                            chapter_type, sort_order, source_url, directory,
                            page_count, cover_file, cover_version, complete,
                            metadata_missing, missing, directory_mtime_ns,
                            updated_at, created_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        ON CONFLICT(book_id, chapter_key) DO UPDATE SET
                            title = excluded.title,
                            raw_title = excluded.raw_title,
                            chapter_type = excluded.chapter_type,
                            sort_order = excluded.sort_order,
                            source_url = excluded.source_url,
                            directory = excluded.directory,
                            page_count = excluded.page_count,
                            cover_file = excluded.cover_file,
                            cover_version = excluded.cover_version,
                            complete = excluded.complete,
                            metadata_missing = excluded.metadata_missing,
                            missing = excluded.missing,
                            directory_mtime_ns =
                                excluded.directory_mtime_ns,
                            updated_at = excluded.updated_at
                        """,
                        (
                            book.id,
                            chapter.chapter_key,
                            chapter.title,
                            chapter.raw_title,
                            chapter.chapter_type,
                            chapter.sort_order,
                            chapter.source_url,
                            chapter.directory,
                            chapter.page_count,
                            chapter.cover,
                            chapter.cover_version,
                            int(chapter.complete),
                            int(chapter.metadata_missing),
                            int(chapter.missing),
                            chapter_dir.stat().st_mtime_ns
                            if chapter_dir.is_dir() else 0,
                            chapter.updated_at or None,
                            started,
                        ),
                    )
                    row = connection.execute(
                        """
                        SELECT id FROM book_chapters
                        WHERE book_id = ? AND chapter_key = ?
                        """,
                        (book.id, chapter.chapter_key),
                    ).fetchone()
                    chapter.id = int(row["id"])
                    chapter_keys.append(chapter.chapter_key)

                if chapter_keys:
                    placeholders = ",".join("?" for _ in chapter_keys)
                    connection.execute(
                        f"""
                        DELETE FROM book_chapters
                        WHERE book_id = ? AND chapter_key NOT IN (
                            {placeholders}
                        )
                        """,
                        (book.id, *chapter_keys),
                    )
                else:
                    connection.execute(
                        "DELETE FROM book_chapters WHERE book_id = ?",
                        (book.id,),
                    )
                connection.execute(
                    "DELETE FROM book_chapters_fts WHERE book_id = ?",
                    (book.id,),
                )
                for chapter in chapters:
                    connection.execute(
                        """
                        INSERT INTO book_chapters_fts(
                            chapter_id, book_id, title, raw_title
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            chapter.id,
                            book.id,
                            chapter.title,
                            chapter.raw_title,
                        ),
                    )

            connection.execute(
                """
                UPDATE categories SET book_count = (
                    SELECT COUNT(*) FROM book_categories
                    WHERE book_categories.category_id = categories.id
                )
                """
            )
            connection.execute(
                """
                DELETE FROM categories
                WHERE book_count = 0 AND blocked = 0
                """
            )
            if only_book_id is None:
                connection.execute("DELETE FROM books_fts")
            else:
                connection.execute(
                    "DELETE FROM books_fts WHERE book_id = ?",
                    (only_book_id,),
                )
            for row in connection.execute(
                f"""
                SELECT b.id, b.title, b.raw_title, b.search_text,
                       COALESCE(GROUP_CONCAT(c.name, ' '), '') AS categories_text
                FROM books b
                LEFT JOIN book_categories bc ON bc.book_id = b.id
                LEFT JOIN categories c ON c.id = bc.category_id
                WHERE b.missing = 0
                {"AND b.id = ?" if only_book_id is not None else ""}
                GROUP BY b.id
                """,
                (only_book_id,) if only_book_id is not None else (),
            ):
                connection.execute(
                    """
                    INSERT INTO books_fts(
                        book_id, title, raw_title, categories_text, search_text
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        row["id"], row["title"], row["raw_title"] or "",
                        row["categories_text"], row["search_text"],
                    ),
                )
            finished = datetime.now().isoformat(timespec="seconds")
            connection.commit()

        with self._pages_lock:
            self._pages_cache = {
                book.id: (
                    (
                        book.storage_id,
                        book.directory,
                        (self.root / book.directory).stat().st_mtime_ns,
                    ),
                    book.pages,
                )
                for book, _, _ in discovered
            }
        self.scanned_at = finished

    @staticmethod
    def _read_json(path: Path) -> dict:
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _cover_for_page(pages: List[PageEntry], directory: Path) -> str:
        cover = pages[0].filename
        override = ComicLibrary._read_json(directory / "cover.json")
        requested = str(override.get("file") or "")
        if requested in {page.filename for page in pages}:
            return requested
        return cover

    def _row_to_book(
        self,
        row: sqlite3.Row,
        categories: Optional[List[str]] = None,
    ) -> BookEntry:
        return BookEntry(
            id=int(row["id"]),
            title=row["title"],
            directory=row["directory"],
            page_count=row["page_count"],
            cover=row["cover_file"] or "",
            cover_version=row["cover_version"] or "",
            updated_at=row["updated_at"] or "",
            created_at=row["created_at"] or "",
            complete=bool(row["complete"]),
            raw_title=row["raw_title"] or "",
            source_url=row["source_url"] or "",
            categories=categories or [],
            metadata_missing=bool(row["metadata_missing"]),
            missing=bool(row["missing"]),
            storage_id=row["storage_id"] or LOCAL_STORAGE_ID,
            content_type=row["content_type"] or "single",
            chapter_count=row["chapter_count"] or 0,
            chapter_complete_count=row["chapter_complete_count"] or 0,
            chapter_updated_at=row["chapter_updated_at"] or "",
            access_level=int(row["access_level"] or 0),
        )

    def _categories_for_books(
        self,
        connection: sqlite3.Connection,
        book_ids: Sequence[int],
    ) -> Dict[int, List[str]]:
        if not book_ids:
            return {}
        placeholders = ",".join("?" for _ in book_ids)
        result: Dict[int, List[str]] = {book_id: [] for book_id in book_ids}
        for row in connection.execute(
            f"""
            SELECT bc.book_id, c.name
            FROM book_categories bc
            JOIN categories c ON c.id = bc.category_id
            WHERE bc.book_id IN ({placeholders})
            ORDER BY c.book_count DESC, c.name
            """,
            tuple(book_ids),
        ):
            result[row["book_id"]].append(row["name"])
        return result

    def books(self) -> List[BookEntry]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM books WHERE missing = 0 ORDER BY updated_at DESC, id DESC"
            ).fetchall()
            categories = self._categories_for_books(
                connection, [row["id"] for row in rows]
            )
            return [
                self._row_to_book(row, categories.get(row["id"], []))
                for row in rows
            ]

    def annotate_availability(
        self,
        books: Sequence[BookEntry],
        *,
        inspect_content: bool = False,
    ) -> None:
        probes = {}
        for book in books:
            probe = probes.get(book.storage_id)
            if probe is None:
                probe = self.storage.probe(book.storage_id)
                probes[book.storage_id] = probe
            current = probe
            if inspect_content and probe.available:
                current = self.storage.availability(
                    book.storage_id,
                    book.directory,
                    inspect_content=True,
                )
            book.availability = current.status
            book.availability_error = current.error
            book.storage_name = (
                probe.root.display_name
                if probe.root is not None
                else book.storage_name
            )
            book.storage_path = (
                probe.root.root_path
                if probe.root is not None
                else book.storage_path
            )

    def managed_books(
        self,
        offset: int,
        limit: int,
        search: str = "",
        storage: str = "",
        status: str = "",
    ) -> Tuple[int, List[BookEntry]]:
        needle = search.strip().casefold()
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM books
                ORDER BY updated_at DESC, id DESC
                """
            ).fetchall()
            active = {
                row["book_id"]
                for row in connection.execute(
                    """
                    SELECT book_id FROM migration_records
                    WHERE status IN ('queued', 'migrating', 'interrupted')
                    """
                )
            }
        items = [self._row_to_book(row) for row in rows]
        if needle:
            items = [
                book for book in items
                if needle in book.id.casefold()
                or needle in book.title.casefold()
                or needle in book.raw_title.casefold()
                or needle in Path(book.directory).name.casefold()
            ]
        if storage in {"local", "external"}:
            items = [
                book for book in items
                if (
                    book.storage_id == LOCAL_STORAGE_ID
                    if storage == "local"
                    else book.storage_id != LOCAL_STORAGE_ID
                )
            ]
        self.annotate_availability(items, inspect_content=True)
        for book in items:
            if book.id in active:
                book.availability = "migrating"
                book.availability_error = ""
        if status:
            items = [
                book for book in items
                if book.availability == status
            ]
        return len(items), items[offset:offset + limit]

    def get_metadata(self, book_id: int) -> Optional[BookEntry]:
        legacy_id = str(book_id)
        try:
            normalized_id = int(book_id)
        except (TypeError, ValueError):
            normalized_id = 0
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM books WHERE id = ? OR legacy_id = ?",
                (normalized_id, legacy_id),
            ).fetchone()
            if row is None:
                return None
            book_id_int = int(row["id"])
            categories = self._categories_for_books(
                connection, [book_id_int]
            )
            book = self._row_to_book(
                row, categories.get(book_id_int, [])
            )
        self.annotate_availability([book])
        return book

    def set_access_level(self, book_id: int, access_level: int) -> None:
        try:
            normalized_id = int(book_id)
            normalized_level = int(access_level)
        except (TypeError, ValueError) as exc:
            raise ValueError("漫画 ID 和访问级别必须是整数。") from exc
        if normalized_id <= 0 or normalized_level not in {0, 1, 2}:
            raise ValueError("漫画 ID 必须为正整数，访问级别必须为 0、1 或 2。")
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE books SET access_level = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    normalized_level,
                    datetime.now().isoformat(timespec="seconds"),
                    normalized_id,
                ),
            )
            if cursor.rowcount == 0:
                raise KeyError(normalized_id)
            connection.commit()

    def get(self, book_id: int) -> Optional[BookEntry]:
        book = self.get_metadata(book_id)
        if book is None:
            return None
        if book.content_type != "chaptered":
            book.pages = self._pages_for(book)
        return book

    def _row_to_chapter(self, row: sqlite3.Row) -> ChapterEntry:
        return ChapterEntry(
            id=int(row["id"]),
            book_id=row["book_id"],
            chapter_key=row["chapter_key"],
            title=row["title"],
            raw_title=row["raw_title"] or "",
            chapter_type=row["chapter_type"] or "chapter",
            sort_order=int(row["sort_order"]),
            source_url=row["source_url"] or "",
            directory=row["directory"],
            page_count=int(row["page_count"]),
            cover=row["cover_file"] or "",
            cover_version=row["cover_version"] or "",
            complete=bool(row["complete"]),
            metadata_missing=bool(row["metadata_missing"]),
            missing=bool(row["missing"]),
            updated_at=row["updated_at"] or "",
        )

    def list_chapters(
        self,
        book_id: str,
        offset: int,
        limit: int,
        *,
        search: str = "",
        status: str = "",
        order: str = "asc",
    ) -> Tuple[int, List[ChapterEntry]]:
        conditions = ["book_id = ?"]
        params: List[object] = [book_id]
        needle = search.strip()
        if needle:
            if len(needle) >= 3:
                phrase = '"' + needle.replace('"', '""') + '"'
                conditions.append(
                    """
                    id IN (
                        SELECT CAST(chapter_id AS INTEGER)
                        FROM book_chapters_fts
                        WHERE book_chapters_fts MATCH ?
                          AND book_id = ?
                    )
                    """
                )
                params.extend([phrase, book_id])
            else:
                conditions.append("(title LIKE ? OR raw_title LIKE ?)")
                params.extend([f"%{needle}%", f"%{needle}%"])
        if status == "available":
            conditions.append("missing = 0")
        elif status == "missing":
            conditions.append("missing = 1")
        where = " AND ".join(conditions)
        direction = "DESC" if order == "desc" else "ASC"
        with self.database.connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM book_chapters WHERE {where}",
                tuple(params),
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT * FROM book_chapters
                WHERE {where}
                ORDER BY sort_order {direction}, id {direction}
                LIMIT ? OFFSET ?
                """,
                tuple(params + [limit, offset]),
            ).fetchall()
        return total, [self._row_to_chapter(row) for row in rows]

    def get_chapter(
        self,
        book_id: str,
        chapter_id: int,
    ) -> Optional[ChapterEntry]:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM book_chapters
                WHERE id = ? AND book_id = ?
                """,
                (chapter_id, book_id),
            ).fetchone()
        return self._row_to_chapter(row) if row else None

    def neighboring_chapters(
        self,
        chapter: ChapterEntry,
    ) -> Tuple[Optional[ChapterEntry], Optional[ChapterEntry]]:
        with self.database.connect() as connection:
            previous = connection.execute(
                """
                SELECT * FROM book_chapters
                WHERE book_id = ? AND sort_order < ?
                ORDER BY sort_order DESC, id DESC LIMIT 1
                """,
                (chapter.book_id, chapter.sort_order),
            ).fetchone()
            following = connection.execute(
                """
                SELECT * FROM book_chapters
                WHERE book_id = ? AND sort_order > ?
                ORDER BY sort_order ASC, id ASC LIMIT 1
                """,
                (chapter.book_id, chapter.sort_order),
            ).fetchone()
        return (
            self._row_to_chapter(previous) if previous else None,
            self._row_to_chapter(following) if following else None,
        )

    def chapter_directory(
        self,
        book: BookEntry,
        chapter: ChapterEntry,
    ) -> Path:
        combined = Path(book.directory) / chapter.directory
        return self.storage.resolve_directory(
            book.storage_id,
            combined.as_posix(),
        )

    def chapter_pages(
        self,
        book: BookEntry,
        chapter: ChapterEntry,
    ) -> List[PageEntry]:
        directory = self.chapter_directory(book, chapter)
        manifest = self._read_json(directory / "manifest.json")
        return preferred_pages(self._valid_images(directory), manifest)

    def chapter_page_path(
        self,
        book: BookEntry,
        chapter: ChapterEntry,
        filename: str,
    ) -> Path:
        pages = self.chapter_pages(book, chapter)
        if filename not in {page.filename for page in pages}:
            raise KeyError(filename)
        directory = self.chapter_directory(book, chapter)
        path = directory / filename
        if not path.is_file():
            raise BookContentMissing(
                "章节页面文件不存在。",
                storage_id=book.storage_id,
                details={
                    "book_id": book.id,
                    "chapter_id": chapter.id,
                    "filename": filename,
                },
            )
        return path

    def cover_path(self, book: BookEntry) -> Path:
        if not book.cover:
            raise KeyError("cover")
        directory = self.storage.resolve_directory(
            book.storage_id,
            book.directory,
        )
        path = directory / book.cover
        try:
            path.relative_to(directory)
        except ValueError as exc:
            raise KeyError("cover") from exc
        if not path.is_file():
            raise KeyError("cover")
        return path

    def _pages_for(self, book: BookEntry) -> List[PageEntry]:
        directory = self.storage.resolve_directory(
            book.storage_id,
            book.directory,
        )
        mtime = directory.stat().st_mtime_ns
        cache_key = (book.storage_id, book.directory, mtime)
        with self._pages_lock:
            cached = self._pages_cache.get(book.id)
        if cached and cached[0] == cache_key:
            return cached[1]
        manifest = self._read_json(directory / "manifest.json")
        images = [
            path for path in directory.iterdir()
            if path.is_file()
            and not path.name.startswith("._")
            and path.name not in {".DS_Store", ".localized"}
            and path.suffix.lower() in IMAGE_EXTENSIONS
            and not path.name.endswith("_rendered.capture.png")
            and not path.name.endswith(".part")
        ]
        pages = preferred_pages(images, manifest)
        with self._pages_lock:
            self._pages_cache[book.id] = (cache_key, pages)
        return pages

    def query(
        self,
        offset: int,
        limit: int,
        search: str = "",
        category: str = "",
        favorites_only: bool = False,
        favorite_user_id: Optional[int] = None,
        max_access_level: int = 2,
    ) -> Tuple[int, List[BookEntry]]:
        conditions = ["b.missing = 0", "b.access_level <= ?"]
        params: List[object] = [max(0, min(2, int(max_access_level)))]
        joins = []
        if category:
            category_key = normalize_category(category).casefold()
            with self.database.connect() as connection:
                blocked = connection.execute(
                    "SELECT blocked FROM categories WHERE normalized_name = ?",
                    (category_key,),
                ).fetchone()
            if blocked and blocked["blocked"]:
                return 0, []
            joins.append(
                "JOIN book_categories bc ON bc.book_id = b.id "
                "JOIN categories c ON c.id = bc.category_id"
            )
            conditions.append("c.normalized_name = ?")
            params.append(category_key)
        if favorite_user_id is not None:
            joins.append(
                "JOIN user_favorites uf ON uf.book_id = b.id "
                "AND uf.user_id = ?"
            )
            params.insert(0, int(favorite_user_id))
        elif favorites_only:
            joins.append("JOIN favorites fav ON fav.book_id = b.id")
        needle = search.strip().casefold()
        if needle:
            if len(needle) >= 3:
                phrase = '"' + needle.replace('"', '""') + '"'
                conditions.append(
                    "b.id IN ("
                    "SELECT book_id FROM books_fts "
                    "WHERE books_fts MATCH ?"
                    ")"
                )
                params.append(phrase)
            else:
                conditions.append(
                    "(b.search_text LIKE ? OR b.id LIKE ?)"
                )
                params.extend([f"%{needle}%", f"%{needle}%"])
        where = " AND ".join(conditions)
        from_sql = "FROM books b " + " ".join(joins)
        with self.database.connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(DISTINCT b.id) {from_sql} WHERE {where}",
                tuple(params),
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT DISTINCT b.* {from_sql}
                WHERE {where}
                ORDER BY b.updated_at DESC, b.id DESC
                LIMIT ? OFFSET ?
                """,
                tuple(params + [limit, offset]),
            ).fetchall()
            categories = self._categories_for_books(
                connection, [row["id"] for row in rows]
            )
        return total, [
            self._row_to_book(row, categories.get(row["id"], []))
            for row in rows
        ]

    def categories(self) -> List[dict]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT name, book_count, blocked
                FROM categories
                WHERE book_count > 0 OR blocked = 1
                ORDER BY book_count DESC, name COLLATE NOCASE ASC
                """
            ).fetchall()
        return [
            {
                "name": row["name"],
                "book_count": row["book_count"],
                "visible_count": 0 if row["blocked"] else row["book_count"],
                "blocked": bool(row["blocked"]),
            }
            for row in rows
        ]

    def set_category_blocked(self, name: str, blocked: bool) -> dict:
        key = normalize_category(name).casefold()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT id, name, book_count FROM categories WHERE normalized_name = ?",
                (key,),
            ).fetchone()
            if row is None:
                raise ValueError("分类不存在")
            connection.execute(
                "UPDATE categories SET blocked = ?, updated_at = ? WHERE id = ?",
                (
                    int(blocked),
                    datetime.now().isoformat(timespec="seconds"),
                    row["id"],
                ),
            )
            connection.commit()
        return next(item for item in self.categories() if item["name"] == row["name"])

    def set_cover(self, book_id: str, filename: str) -> BookEntry:
        book = self.get(book_id)
        if book is None:
            raise KeyError(book_id)
        if filename not in {page.filename for page in book.pages}:
            raise ValueError("封面文件不属于该漫画")
        directory = self.storage.resolve_directory(
            book.storage_id,
            book.directory,
            force=True,
        )
        cover_path = directory / "cover.json"
        temporary = cover_path.with_name("cover.json.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "file": filename,
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        os.replace(temporary, cover_path)
        self._refresh_cover_metadata(book, directory, filename)
        updated = self.get_metadata(book.id)
        if updated is None:
            raise RuntimeError("封面更新后漫画不存在")
        return updated

    def reset_cover(self, book_id: str) -> BookEntry:
        book = self.get(book_id)
        if book is None:
            raise KeyError(book_id)
        directory = self.storage.resolve_directory(
            book.storage_id,
            book.directory,
            force=True,
        )
        try:
            (directory / "cover.json").unlink()
        except FileNotFoundError:
            pass
        self._refresh_cover_metadata(book, directory, "")
        updated = self.get_metadata(book.id)
        if updated is None:
            raise RuntimeError("封面重置后漫画不存在")
        return updated

    def page_path(self, book: BookEntry, filename: str) -> Path:
        pages = self._pages_for(book)
        if filename not in {page.filename for page in pages}:
            raise KeyError(filename)
        if Path(filename).name != filename:
            raise KeyError(filename)
        directory = self.storage.resolve_directory(
            book.storage_id,
            book.directory,
        )
        path = directory / filename
        if not path.is_file():
            raise BookContentMissing(
                "漫画页面文件不存在。",
                storage_id=book.storage_id,
                details={
                    "storage_id": book.storage_id,
                    "relative_path": f"{book.directory}/{filename}",
                },
            )
        return path

    def book_directory(self, book: BookEntry, *, force: bool = False) -> Path:
        return self.storage.resolve_directory(
            book.storage_id,
            book.directory,
            force=force,
        )

    def _refresh_cover_metadata(
        self,
        book: BookEntry,
        directory: Path,
        cover: str,
    ) -> None:
        if not cover:
            pages = self._pages_for(book)
            if not pages:
                cover = ""
            else:
                cover = pages[0].filename
        cover_version = ""
        if cover:
            stat = (directory / cover).stat()
            cover_version = hashlib.sha1(
                (
                    cover
                    + f"|{stat.st_mtime_ns}|{stat.st_size}"
                ).encode("utf-8")
            ).hexdigest()[:12]
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE books
                SET cover_file = ?, cover_version = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    cover,
                    cover_version,
                    datetime.now().isoformat(timespec="seconds"),
                    book.id,
                ),
            )
            connection.commit()
