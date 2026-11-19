from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

try:
    from .database import Database
    from .indexer import (
        IMAGE_EXTENSIONS,
        PageEntry,
        categories_from_title,
        normalize_category,
        preferred_pages,
    )
except ImportError:
    from database import Database
    from indexer import (
        IMAGE_EXTENSIONS,
        PageEntry,
        categories_from_title,
        normalize_category,
        preferred_pages,
    )


class BookImportError(Exception):
    def __init__(self, message: str, *, code: str = "book_import_error") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class BookRepository:
    """Direct SQLite writer for books, chapters, categories and FTS."""

    def __init__(self, database: Database, library_root: Path) -> None:
        self.database = database
        self.library_root = library_root.expanduser().resolve()

    def has_book(self, book_id: int) -> bool:
        with self.database.connect() as connection:
            return connection.execute(
                "SELECT 1 FROM books WHERE id = ? LIMIT 1",
                (int(book_id),),
            ).fetchone() is not None

    def import_single_book(
        self,
        book_id: int,
        directory: Path,
        *,
        access_level: int = 0,
        title: str = "",
        raw_title: str = "",
        source_url: str = "",
    ) -> dict:
        directory = self._relative_directory(directory)
        manifest = self._read_json(directory / "manifest.json")
        metadata = self._read_json(directory / "metadata.json")
        pages = self._pages(directory, manifest)
        if not pages:
            raise BookImportError(
                "下载目录中没有有效图片。",
                code="downloaded_book_empty",
            )
        resolved_title = str(
            title
            or metadata.get("title")
            or metadata.get("raw_title")
            or manifest.get("title")
            or directory.name
        ).strip()
        resolved_raw_title = str(
            raw_title
            or metadata.get("raw_title")
            or manifest.get("title")
            or resolved_title
        ).strip()
        resolved_source_url = str(
            source_url
            or metadata.get("source_url")
            or manifest.get("source_url")
            or ""
        ).strip()
        categories = categories_from_title(
            resolved_raw_title or resolved_title
        )
        cover, cover_version = self._cover(
            directory,
            pages,
            str(metadata.get("cover_file") or ""),
        )
        now = datetime.now().isoformat(timespec="seconds")
        complete = self._manifest_complete(manifest)
        relative = directory.relative_to(self.library_root).as_posix()
        search_text = self._search_text(
            book_id,
            resolved_title,
            resolved_raw_title,
            categories,
        )
        try:
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO books(
                        id, title, raw_title, directory, source_url,
                        page_count, cover_file, cover_version, complete,
                        metadata_missing, directory_mtime_ns, updated_at,
                        created_at, missing, search_text, storage_id,
                        access_level, content_type, chapter_count,
                        chapter_complete_count, chapter_updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'local',
                        ?, 'single', 0, 0, NULL
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
                        access_level = excluded.access_level,
                        content_type = 'single',
                        chapter_count = 0,
                        chapter_complete_count = 0,
                        chapter_updated_at = NULL
                    """,
                    (
                        int(book_id),
                        resolved_title,
                        resolved_raw_title,
                        relative,
                        resolved_source_url,
                        len(pages),
                        cover,
                        cover_version,
                        int(complete),
                        int(not bool(resolved_raw_title)),
                        directory.stat().st_mtime_ns,
                        now,
                        now,
                        search_text,
                        int(access_level),
                    ),
                )
                self._replace_categories(connection, int(book_id), categories)
                self._sync_book_fts(connection, int(book_id))
                self._refresh_category_counts(connection)
                connection.commit()
        except Exception:
            raise
        return {
            "book_id": int(book_id),
            "title": resolved_title,
            "directory": relative,
            "page_count": len(pages),
            "cover_file": cover,
            "cover_version": cover_version,
            "complete": complete,
        }

    def import_chapter(
        self,
        *,
        parent_id: int,
        parent_directory: Path,
        chapter_directory: Path,
        chapter_key: str,
        chapter_title: str,
        sort_order: int,
        source_url: str,
        access_level: int = 0,
    ) -> dict:
        parent_directory = self._relative_directory(parent_directory)
        chapter_directory = chapter_directory.expanduser().resolve()
        try:
            chapter_relative = chapter_directory.relative_to(
                parent_directory
            ).as_posix()
        except ValueError as exc:
            raise BookImportError(
                "章节目录不属于漫画目录。",
                code="invalid_chapter_directory",
            ) from exc
        manifest = self._read_json(chapter_directory / "manifest.json")
        metadata = self._read_json(chapter_directory / "metadata.json")
        pages = self._pages(chapter_directory, manifest)
        if not pages:
            raise BookImportError(
                "章节下载目录中没有有效图片。",
                code="downloaded_chapter_empty",
            )
        cover, cover_version = self._cover(
            chapter_directory,
            pages,
            str(metadata.get("cover_file") or ""),
        )
        now = datetime.now().isoformat(timespec="seconds")
        complete = self._manifest_complete(manifest)
        series = self._read_json(parent_directory / "series.json")
        raw_title = str(
            series.get("raw_title")
            or series.get("title")
            or metadata.get("raw_title")
            or chapter_title
        ).strip()
        title = str(
            series.get("title")
            or series.get("raw_title")
            or chapter_title
        ).strip()
        series_source = str(
            series.get("source_url") or source_url
        ).strip()
        parent_relative = parent_directory.relative_to(
            self.library_root
        ).as_posix()
        categories = categories_from_title(raw_title or title)
        search_text = self._search_text(
            parent_id,
            title,
            raw_title,
            categories,
        )
        try:
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO books(
                        id, title, raw_title, directory, source_url,
                        page_count, cover_file, cover_version, complete,
                        metadata_missing, directory_mtime_ns, updated_at,
                        created_at, missing, search_text, storage_id,
                        access_level, content_type, chapter_count,
                        chapter_complete_count, chapter_updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, 0, '', '', 0, 0, ?, ?, ?, 0, ?,
                        'local', ?, 'chaptered', 0, 0, NULL
                    )
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        raw_title = excluded.raw_title,
                        directory = excluded.directory,
                        source_url = excluded.source_url,
                        updated_at = excluded.updated_at,
                        missing = 0,
                        search_text = excluded.search_text,
                        access_level = excluded.access_level,
                        content_type = 'chaptered'
                    """,
                    (
                        int(parent_id),
                        title,
                        raw_title,
                        parent_relative,
                        series_source,
                        parent_directory.stat().st_mtime_ns,
                        now,
                        now,
                        search_text,
                        int(access_level),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO book_chapters(
                        book_id, chapter_key, title, raw_title,
                        chapter_type, sort_order, source_url, directory,
                        page_count, cover_file, cover_version, complete,
                        metadata_missing, missing, directory_mtime_ns,
                        updated_at, created_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?
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
                        missing = 0,
                        directory_mtime_ns = excluded.directory_mtime_ns,
                        updated_at = excluded.updated_at
                    """,
                    (
                        int(parent_id),
                        chapter_key,
                        chapter_title,
                        chapter_title,
                        "volume" if "卷" in chapter_title else "chapter",
                        int(sort_order),
                        source_url,
                        chapter_relative,
                        len(pages),
                        cover,
                        cover_version,
                        int(complete),
                        chapter_directory.stat().st_mtime_ns,
                        now,
                        now,
                    ),
                )
                chapter_row = connection.execute(
                    """
                    SELECT id FROM book_chapters
                    WHERE book_id = ? AND chapter_key = ?
                    """,
                    (int(parent_id), chapter_key),
                ).fetchone()
                connection.execute(
                    "DELETE FROM book_chapters_fts WHERE chapter_id = ?",
                    (int(chapter_row["id"]),),
                )
                connection.execute(
                    """
                    INSERT INTO book_chapters_fts(
                        chapter_id, book_id, title, raw_title
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        int(chapter_row["id"]),
                        int(parent_id),
                        chapter_title,
                        chapter_title,
                    ),
                )
                self._replace_categories(connection, int(parent_id), categories)
                self._recalculate_series(connection, int(parent_id), now)
                self._sync_book_fts(connection, int(parent_id))
                self._refresh_category_counts(connection)
                connection.commit()
        except BookImportError:
            raise
        except Exception as exc:
            raise BookImportError(
                f"章节数据写入失败：{exc}",
                code="chapter_db_write_failed",
            ) from exc
        return {
            "book_id": int(parent_id),
            "chapter_key": chapter_key,
            "page_count": len(pages),
            "complete": complete,
        }

    def delete_chapter(self, book_id: int, chapter_id: int) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM book_chapters_fts WHERE chapter_id = ?",
                (int(chapter_id),),
            )
            connection.execute(
                "DELETE FROM book_chapters WHERE id = ? AND book_id = ?",
                (int(chapter_id), int(book_id)),
            )
            self._recalculate_series(connection, int(book_id), now)
            self._sync_book_fts(connection, int(book_id))
            self._refresh_category_counts(connection)
            connection.commit()

    def refresh_cover(
        self,
        book_id: int,
        directory: Path,
        cover: str,
    ) -> None:
        directory = directory.expanduser().resolve()
        version = ""
        cover = str(cover or "").strip()
        if cover:
            path = directory / cover
            if path.is_file():
                stat = path.stat()
                version = hashlib.sha1(
                    (
                        cover
                        + f"|{stat.st_mtime_ns}|{stat.st_size}"
                    ).encode("utf-8")
                ).hexdigest()[:12]
            else:
                cover = ""
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE books
                SET cover_file = ?, cover_version = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    cover,
                    version,
                    datetime.now().isoformat(timespec="seconds"),
                    int(book_id),
                ),
            )
            connection.commit()

    def _relative_directory(self, directory: Path) -> Path:
        directory = directory.expanduser().resolve()
        try:
            directory.relative_to(self.library_root)
        except ValueError as exc:
            raise BookImportError(
                "下载目录不属于本地漫画库。",
                code="download_directory_outside_library",
            ) from exc
        return directory

    @staticmethod
    def _read_json(path: Path) -> dict:
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _pages(directory: Path, manifest: dict) -> Sequence[PageEntry]:
        images = [
            path
            for path in directory.iterdir()
            if path.is_file()
            and path.suffix.lower() in IMAGE_EXTENSIONS
            and not path.name.startswith("._")
            and path.name not in {".DS_Store", ".localized"}
            and not path.name.endswith("_rendered.capture.png")
            and not path.name.endswith(".part")
        ]
        return preferred_pages(images, manifest)

    @staticmethod
    def _manifest_complete(manifest: dict) -> bool:
        try:
            image_count = int(manifest.get("image_count", 0))
            downloaded = int(manifest.get("downloaded", 0))
            skipped = int(manifest.get("skipped", 0))
            failed = int(manifest.get("failed", 0))
        except (TypeError, ValueError):
            return False
        if image_count <= 0 or failed != 0:
            return False
        return downloaded + skipped >= image_count

    @staticmethod
    def _cover(
        directory: Path,
        pages: Sequence[PageEntry],
        requested: str,
    ) -> tuple[str, str]:
        filenames = {page.filename for page in pages}
        cover = requested if requested in filenames else pages[0].filename
        stat = (directory / cover).stat()
        version = hashlib.sha1(
            (
                cover + f"|{stat.st_mtime_ns}|{stat.st_size}"
            ).encode("utf-8")
        ).hexdigest()[:12]
        return cover, version

    @staticmethod
    def _search_text(
        book_id: int,
        title: str,
        raw_title: str,
        categories: Sequence[str],
    ) -> str:
        return normalize_category(
            " ".join([str(book_id), title, raw_title, *categories])
        ).casefold()

    def _replace_categories(
        self,
        connection,
        book_id: int,
        categories: Sequence[str],
    ) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        connection.execute(
            "DELETE FROM book_categories WHERE book_id = ?",
            (book_id,),
        )
        for category in categories:
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
                    now,
                    now,
                ),
            )
            category_row = connection.execute(
                """
                SELECT id FROM categories
                WHERE normalized_name = ?
                """,
                (normalized.casefold(),),
            ).fetchone()
            connection.execute(
                """
                INSERT OR IGNORE INTO book_categories(book_id, category_id)
                VALUES (?, ?)
                """,
                (book_id, category_row["id"]),
            )

    @staticmethod
    def _refresh_category_counts(connection) -> None:
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

    @staticmethod
    def _sync_book_fts(connection, book_id: int) -> None:
        connection.execute(
            "DELETE FROM books_fts WHERE book_id = ?",
            (book_id,),
        )
        row = connection.execute(
            """
            SELECT b.id, b.title, b.raw_title, b.search_text,
                   COALESCE(GROUP_CONCAT(c.name, ' '), '') AS categories_text
            FROM books b
            LEFT JOIN book_categories bc ON bc.book_id = b.id
            LEFT JOIN categories c ON c.id = bc.category_id
            WHERE b.id = ? AND b.missing = 0
            GROUP BY b.id
            """,
            (book_id,),
        ).fetchone()
        if row is not None:
            connection.execute(
                """
                INSERT INTO books_fts(
                    book_id, title, raw_title, categories_text, search_text
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["title"],
                    row["raw_title"] or "",
                    row["categories_text"],
                    row["search_text"],
                ),
            )

    @staticmethod
    def _recalculate_series(connection, book_id: int, now: str) -> None:
        summary = connection.execute(
            """
            SELECT COUNT(*) AS count,
                   COALESCE(SUM(complete), 0) AS complete_count,
                   COALESCE(SUM(page_count), 0) AS page_count,
                   MAX(updated_at) AS updated_at
            FROM book_chapters WHERE book_id = ?
            """,
            (book_id,),
        ).fetchone()
        first_cover = connection.execute(
            """
            SELECT directory, cover_file FROM book_chapters
            WHERE book_id = ? AND cover_file <> ''
            ORDER BY sort_order, id LIMIT 1
            """,
            (book_id,),
        ).fetchone()
        cover = ""
        cover_version = ""
        if first_cover is not None:
            cover = (
                Path(first_cover["directory"]) / first_cover["cover_file"]
            ).as_posix()
            version_row = connection.execute(
                """
                SELECT cover_version FROM book_chapters
                WHERE book_id = ? AND cover_file != ''
                ORDER BY sort_order, id LIMIT 1
                """,
                (book_id,),
            ).fetchone()
            cover_version = version_row["cover_version"] or ""
        chapter_count = int(summary["count"])
        complete_count = int(summary["complete_count"])
        complete = chapter_count > 0 and complete_count == chapter_count
        connection.execute(
            """
            UPDATE books
            SET page_count = ?, chapter_count = ?,
                chapter_complete_count = ?, complete = ?,
                chapter_updated_at = ?, cover_file = ?,
                cover_version = ?, updated_at = ?, missing = 0
            WHERE id = ?
            """,
            (
                int(summary["page_count"]),
                chapter_count,
                complete_count,
                int(complete),
                summary["updated_at"] or now,
                cover,
                cover_version,
                now,
                book_id,
            ),
        )
