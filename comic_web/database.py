from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


SCHEMA_VERSION = 19


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.was_upgraded = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            previous_version = int(
                connection.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
                ).fetchone()[0]
            )
            books_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(books)")
            }
            download_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(download_tasks)")
            }
            batch_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(batch_download_tasks)"
                )
            }
            existing_tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            settings_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(app_settings)"
                )
            }
            if (
                "download_tasks" not in existing_tables
                and "schema_migrations" in existing_tables
            ):
                self._backup_before_v5()
            if books_columns and "content_type" not in books_columns:
                self._backup_before_v6()
            if "batch_download_tasks" not in existing_tables:
                self._backup_before_v7()
            if "incremental_pull_tasks" not in existing_tables:
                self._backup_before_v8()
            if "deletion_tasks" not in existing_tables:
                self._backup_before_v9()
            if (
                (download_columns and "pull_mode" not in download_columns)
                or (batch_columns and "pull_mode" not in batch_columns)
            ):
                self._backup_before_v10()
            if books_columns and "access_level" not in books_columns:
                self._backup_before_v11()
            books_id_type = ""
            if books_columns:
                books_id_type = str(
                    connection.execute(
                        "SELECT type FROM pragma_table_info('books') WHERE name='id'"
                    ).fetchone()["type"]
                ).upper()
            if books_id_type and books_id_type != "INTEGER":
                self._backup_before_v12()
            if (
                (download_columns and "access_level" not in download_columns)
                or (batch_columns and "access_level" not in batch_columns)
            ):
                self._backup_before_v13()
            if settings_columns and (
                "id" not in settings_columns
                or "setting_key" not in settings_columns
                or "description" not in settings_columns
            ):
                self._backup_before_v14()
            elif previous_version == 14:
                self._backup_before_v15()
            elif previous_version == 15:
                self._backup_before_v16()
            elif previous_version == 16:
                self._backup_before_v17()
            elif previous_version == 17:
                self._backup_before_v18()
            elif previous_version == 18:
                self._backup_before_v19()
            if books_columns and "storage_id" not in books_columns:
                self._backup_before_v2()
                self._migrate_v1_to_v2(connection)
            else:
                self._create_tables(connection)
                self._rebuild_book_categories_if_needed(connection)

            self._create_tables(connection)
            self._migrate_v2_to_v3(connection)
            self._migrate_v3_to_v4(connection)
            self._migrate_v5_to_v6(connection)
            self._migrate_incremental_pull_tasks(connection)
            self._migrate_v9_to_v10(connection)
            self._migrate_v10_to_v11(connection)
            self._migrate_v11_to_v12(connection)
            self._migrate_v12_to_v13(connection)
            self._migrate_v13_to_v14(connection)
            self._migrate_v14_to_v15(connection)
            self._migrate_v15_to_v16(connection)
            self._migrate_v16_to_v17(connection)
            self._migrate_v17_to_v18(connection)
            self._migrate_v18_to_v19(connection)
            self._create_indexes(connection)
            self._create_fts(connection)
            connection.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                VALUES (?, ?)
                """,
                (SCHEMA_VERSION, datetime.now().isoformat(timespec="seconds")),
            )
            self.was_upgraded = previous_version < SCHEMA_VERSION
            connection.commit()
    def _backup_before_v2(self) -> None:
        if not self.path.is_file():
            return
        backup_path = self.path.with_name(f"{self.path.name}.pre-v2.bak")
        if backup_path.is_file():
            return
        source = sqlite3.connect(str(self.path))
        target = sqlite3.connect(str(backup_path))
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()

    def _backup_before_v3(self) -> None:
        if not self.path.is_file():
            return
        backup_path = self.path.with_name(f"{self.path.name}.pre-v3.bak")
        if backup_path.is_file():
            return
        source = sqlite3.connect(str(self.path))
        target = sqlite3.connect(str(backup_path))
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()

    def _backup_before_v4(self) -> None:
        if not self.path.is_file():
            return
        backup_path = self.path.with_name(f"{self.path.name}.pre-v4.bak")
        if backup_path.is_file():
            return
        source = sqlite3.connect(str(self.path))
        target = sqlite3.connect(str(backup_path))
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()

    def _backup_before_v5(self) -> None:
        if not self.path.is_file():
            return
        backup_path = self.path.with_name(f"{self.path.name}.pre-v5.bak")
        if backup_path.is_file():
            return
        source = sqlite3.connect(str(self.path))
        target = sqlite3.connect(str(backup_path))
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()

    def _backup_before_v6(self) -> None:
        if not self.path.is_file():
            return
        backup_path = self.path.with_name(f"{self.path.name}.pre-v6.bak")
        if backup_path.is_file():
            return
        source = sqlite3.connect(str(self.path))
        target = sqlite3.connect(str(backup_path))
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()

    def _backup_before_v7(self) -> None:
        if not self.path.is_file():
            return
        backup_path = self.path.with_name(f"{self.path.name}.pre-v7.bak")
        if backup_path.is_file():
            return
        source = sqlite3.connect(str(self.path))
        target = sqlite3.connect(str(backup_path))
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()

    def _backup_before_v8(self) -> None:
        if not self.path.is_file():
            return
        backup_path = self.path.with_name(f"{self.path.name}.pre-v8.bak")
        if backup_path.is_file():
            return
        source = sqlite3.connect(str(self.path))
        target = sqlite3.connect(str(backup_path))
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()

    def _backup_before_v9(self) -> None:
        if not self.path.is_file():
            return
        backup_path = self.path.with_name(f"{self.path.name}.pre-v9.bak")
        if backup_path.is_file():
            return
        source = sqlite3.connect(str(self.path))
        target = sqlite3.connect(str(backup_path))
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()

    def _backup_before_v10(self) -> None:
        if not self.path.is_file():
            return
        backup_path = self.path.with_name(f"{self.path.name}.pre-v10.bak")
        if backup_path.is_file():
            return
        source = sqlite3.connect(str(self.path))
        target = sqlite3.connect(str(backup_path))
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()

    def _backup_before_v11(self) -> None:
        if not self.path.is_file():
            return
        backup_path = self.path.with_name(f"{self.path.name}.pre-v11.bak")
        if backup_path.is_file():
            return
        source = sqlite3.connect(str(self.path))
        target = sqlite3.connect(str(backup_path))
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()

    def _backup_before_v12(self) -> None:
        if not self.path.is_file():
            return
        backup_path = self.path.with_name(f"{self.path.name}.pre-v12.bak")
        if backup_path.is_file():
            return
        source = sqlite3.connect(str(self.path))
        target = sqlite3.connect(str(backup_path))
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()

    def _backup_before_v13(self) -> None:
        if not self.path.is_file():
            return
        backup_path = self.path.with_name(f"{self.path.name}.pre-v13.bak")
        if backup_path.is_file():
            return
        source = sqlite3.connect(str(self.path))
        target = sqlite3.connect(str(backup_path))
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()

    def _backup_before_v14(self) -> None:
        if not self.path.is_file():
            return
        backup_path = self.path.with_name(f"{self.path.name}.pre-v14.bak")
        if backup_path.is_file():
            return
        source = sqlite3.connect(str(self.path))
        target = sqlite3.connect(str(backup_path))
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()

    def _backup_before_v15(self) -> None:
        if not self.path.is_file():
            return
        backup_path = self.path.with_name(f"{self.path.name}.pre-v15.bak")
        if backup_path.is_file():
            return
        source = sqlite3.connect(str(self.path))
        target = sqlite3.connect(str(backup_path))
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()

    def _backup_before_v16(self) -> None:
        if not self.path.is_file():
            return
        backup_path = self.path.with_name(f"{self.path.name}.pre-v16.bak")
        if backup_path.is_file():
            return
        source = sqlite3.connect(str(self.path))
        target = sqlite3.connect(str(backup_path))
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()

    def _backup_before_v17(self) -> None:
        if not self.path.is_file():
            return
        backup_path = self.path.with_name(f"{self.path.name}.pre-v17.bak")
        if backup_path.is_file():
            return
        source = sqlite3.connect(str(self.path))
        target = sqlite3.connect(str(backup_path))
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()

    def _backup_before_v18(self) -> None:
        if not self.path.is_file():
            return
        backup_path = self.path.with_name(f"{self.path.name}.pre-v18.bak")
        if backup_path.is_file():
            return
        source = sqlite3.connect(str(self.path))
        target = sqlite3.connect(str(backup_path))
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()

    def _backup_before_v19(self) -> None:
        if not self.path.is_file():
            return
        backup_path = self.path.with_name(f"{self.path.name}.pre-v19.bak")
        if backup_path.is_file():
            return
        source = sqlite3.connect(str(self.path))
        target = sqlite3.connect(str(backup_path))
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()

    def _migrate_v1_to_v2(self, connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            connection.execute("ALTER TABLE books RENAME TO books_v1_old")
            connection.execute(
                "ALTER TABLE book_categories RENAME TO book_categories_v1_old"
            )
            self._create_tables(connection)
            connection.execute(
                """
                INSERT INTO books(
                    id, title, raw_title, directory, source_url,
                    page_count, cover_file, cover_version, complete,
                    metadata_missing, directory_mtime_ns, updated_at,
                    created_at, missing, search_text, storage_id,
                    migrated_at, migration_job_id
                )
                SELECT
                    id, title, raw_title, directory, source_url,
                    page_count, cover_file, cover_version, complete,
                    metadata_missing, directory_mtime_ns, updated_at,
                    created_at, missing, search_text, 'local', NULL, NULL
                FROM books_v1_old
                """
            )
            connection.execute(
                """
                INSERT INTO book_categories(book_id, category_id)
                SELECT book_id, category_id FROM book_categories_v1_old
                """
            )
            connection.execute("DROP TABLE book_categories_v1_old")
            connection.execute("DROP TABLE books_v1_old")
            connection.execute(
                """
                INSERT OR REPLACE INTO schema_migrations(version, applied_at)
                VALUES (?, ?)
                """,
                (SCHEMA_VERSION, datetime.now().isoformat(timespec="seconds")),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _rebuild_book_categories_if_needed(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(book_categories)"
        ).fetchall()
        table = connection.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'table' AND name = 'book_categories'
            """
        ).fetchone()
        if not foreign_keys and not (
            table and "REFERENCES" in (table["sql"] or "").upper()
        ):
            return
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                "ALTER TABLE book_categories RENAME TO book_categories_old"
            )
            connection.execute(
                """
                CREATE TABLE book_categories (
                    book_id TEXT NOT NULL,
                    category_id INTEGER NOT NULL,
                    PRIMARY KEY(book_id, category_id)
                )
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO book_categories(book_id, category_id)
                SELECT book_id, category_id FROM book_categories_old
                """
            )
            connection.execute("DROP TABLE book_categories_old")
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _migrate_v2_to_v3(self, connection: sqlite3.Connection) -> None:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "migration_jobs" not in tables:
            return
        self._backup_before_v3()
        connection.execute("BEGIN IMMEDIATE")
        try:
            has_batches = "migration_batches" in tables
            batch_join = (
                "LEFT JOIN migration_batches b ON b.id = j.batch_id"
                if has_batches
                else "LEFT JOIN (SELECT NULL AS destination_storage_id) b ON 1 = 0"
            )
            destination_storage = (
                "COALESCE(j.destination_storage_id, "
                "b.destination_storage_id)"
                if has_batches
                else "j.destination_storage_id"
            )
            batch_no = "j.batch_id"
            source_root = "j.source_root_snapshot"
            destination_root = "j.destination_root_snapshot"
            rows = connection.execute(
                f"""
                SELECT
                    j.id AS old_id,
                    {batch_no} AS batch_no,
                    j.book_id,
                    j.book_title_snapshot,
                    j.source_storage_id,
                    {destination_storage} AS destination_storage_id,
                    j.source_relative_path,
                    j.destination_relative_path,
                    rtrim({source_root}, '/') || '/' ||
                        j.source_relative_path AS source_path,
                    rtrim({destination_root}, '/') || '/' ||
                        j.destination_relative_path AS destination_path,
                    CASE
                        WHEN j.status IN (
                            'preflight', 'copying', 'stage_verifying',
                            'publishing', 'final_verifying',
                            'switching_storage', 'source_quarantining',
                            'source_verifying', 'source_deleting'
                        ) THEN 'migrating'
                        ELSE j.status
                    END AS status,
                    j.stage,
                    j.total_files,
                    j.total_bytes,
                    j.copied_files,
                    j.copied_bytes,
                    j.verified_files,
                    j.verification_summary,
                    j.error_code,
                    j.error_message,
                    j.retry_count,
                    j.created_at,
                    j.started_at,
                    j.finished_at,
                    j.updated_at
                FROM migration_jobs j
                {batch_join}
                ORDER BY j.created_at, j.id
                """
            ).fetchall()
            for row in rows:
                cursor = connection.execute(
                    """
                    INSERT INTO migration_records(
                        batch_no, book_id, book_title,
                        source_storage_id, destination_storage_id,
                        source_relative_path, destination_relative_path,
                        source_path, destination_path, status, stage,
                        total_files, total_bytes, copied_files, copied_bytes,
                        verified_files, verification_summary, error_code,
                        error_message, retry_count, created_at, started_at,
                        finished_at, updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        row["batch_no"],
                        row["book_id"],
                        row["book_title_snapshot"],
                        row["source_storage_id"],
                        row["destination_storage_id"],
                        row["source_relative_path"],
                        row["destination_relative_path"],
                        row["source_path"],
                        row["destination_path"],
                        row["status"],
                        row["stage"],
                        row["total_files"],
                        row["total_bytes"],
                        row["copied_files"],
                        row["copied_bytes"],
                        row["verified_files"],
                        row["verification_summary"],
                        row["error_code"],
                        row["error_message"],
                        row["retry_count"],
                        row["created_at"],
                        row["started_at"],
                        row["finished_at"],
                        row["updated_at"],
                    ),
                )
                connection.execute(
                    """
                    UPDATE books SET migration_job_id = ?
                    WHERE migration_job_id = ?
                    """,
                    (str(cursor.lastrowid), row["old_id"]),
                )
            connection.execute("DROP TABLE IF EXISTS migration_events")
            connection.execute("DROP TABLE IF EXISTS migration_jobs")
            if has_batches:
                connection.execute("DROP TABLE IF EXISTS migration_batches")
            connection.execute(
                """
                INSERT OR REPLACE INTO schema_migrations(version, applied_at)
                VALUES (?, ?)
                """,
                (SCHEMA_VERSION, datetime.now().isoformat(timespec="seconds")),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _migrate_v3_to_v4(self, connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]: row
            for row in connection.execute(
                "PRAGMA table_info(migration_records)"
            )
        }
        id_column = columns.get("id")
        if (
            id_column is None
            or str(id_column["type"]).upper() == "INTEGER"
            and int(id_column["pk"]) == 1
        ):
            return
        self._backup_before_v4()
        connection.execute("BEGIN IMMEDIATE")
        try:
            rows = connection.execute(
                """
                SELECT * FROM migration_records
                ORDER BY created_at, id
                """
            ).fetchall()
            connection.execute(
                "ALTER TABLE migration_records RENAME TO migration_records_v3_old"
            )
            connection.execute(
                """
                CREATE TABLE migration_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_no TEXT NOT NULL,
                    book_id TEXT NOT NULL,
                    book_title TEXT NOT NULL,
                    source_storage_id TEXT NOT NULL DEFAULT 'local',
                    destination_storage_id TEXT NOT NULL,
                    source_relative_path TEXT NOT NULL,
                    destination_relative_path TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    destination_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    total_files INTEGER NOT NULL DEFAULT 0,
                    total_bytes INTEGER NOT NULL DEFAULT 0,
                    copied_files INTEGER NOT NULL DEFAULT 0,
                    copied_bytes INTEGER NOT NULL DEFAULT 0,
                    verified_files INTEGER NOT NULL DEFAULT 0,
                    verification_summary TEXT NOT NULL DEFAULT '',
                    error_code TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            for row in rows:
                cursor = connection.execute(
                    """
                    INSERT INTO migration_records(
                        batch_no, book_id, book_title,
                        source_storage_id, destination_storage_id,
                        source_relative_path, destination_relative_path,
                        source_path, destination_path, status, stage,
                        total_files, total_bytes, copied_files, copied_bytes,
                        verified_files, verification_summary, error_code,
                        error_message, retry_count, created_at, started_at,
                        finished_at, updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        row["batch_no"],
                        row["book_id"],
                        row["book_title"],
                        row["source_storage_id"],
                        row["destination_storage_id"],
                        row["source_relative_path"],
                        row["destination_relative_path"],
                        row["source_path"],
                        row["destination_path"],
                        row["status"],
                        row["stage"],
                        row["total_files"],
                        row["total_bytes"],
                        row["copied_files"],
                        row["copied_bytes"],
                        row["verified_files"],
                        row["verification_summary"],
                        row["error_code"],
                        row["error_message"],
                        row["retry_count"],
                        row["created_at"],
                        row["started_at"],
                        row["finished_at"],
                        row["updated_at"],
                    ),
                )
                connection.execute(
                    """
                    UPDATE books SET migration_job_id = ?
                    WHERE migration_job_id = ?
                    """,
                    (str(cursor.lastrowid), str(row["id"])),
                )
            connection.execute("DROP TABLE migration_records_v3_old")
            connection.execute(
                """
                INSERT OR REPLACE INTO schema_migrations(version, applied_at)
                VALUES (?, ?)
                """,
                (SCHEMA_VERSION, datetime.now().isoformat(timespec="seconds")),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _migrate_v5_to_v6(self, connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(books)")
        }
        if "content_type" in columns:
            return
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                "ALTER TABLE books ADD COLUMN content_type TEXT NOT NULL DEFAULT 'single'"
            )
            connection.execute(
                "ALTER TABLE books ADD COLUMN chapter_count INTEGER NOT NULL DEFAULT 0"
            )
            connection.execute(
                """
                ALTER TABLE books
                ADD COLUMN chapter_complete_count INTEGER NOT NULL DEFAULT 0
                """
            )
            connection.execute(
                "ALTER TABLE books ADD COLUMN chapter_updated_at TEXT"
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO schema_migrations(version, applied_at)
                VALUES (?, ?)
                """,
                (SCHEMA_VERSION, datetime.now().isoformat(timespec="seconds")),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _migrate_incremental_pull_tasks(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        """Backfill one incremental task for existing batch comics."""
        groups = connection.execute(
            """
            SELECT
                parent_id,
                MAX(comic_name) AS comic_name,
                MAX(source_host) AS source_host,
                COUNT(DISTINCT chapter_key) AS known_chapter_count,
                MIN(created_at) AS first_batch_at,
                MAX(created_at) AS last_pull_at
            FROM batch_download_tasks
            GROUP BY parent_id
            """
        ).fetchall()
        now = datetime.now().isoformat(timespec="seconds")
        for group in groups:
            comic_id = str(group["parent_id"])
            latest = connection.execute(
                """
                SELECT chapter_key, chapter_title, sort_order
                FROM batch_download_tasks
                WHERE parent_id = ?
                ORDER BY sort_order DESC, id DESC
                LIMIT 1
                """,
                (comic_id,),
            ).fetchone()
            current = connection.execute(
                """
                SELECT chapter_key, title, sort_order
                FROM book_chapters
                WHERE book_id = ? AND complete = 1 AND missing = 0
                ORDER BY sort_order DESC, id DESC
                LIMIT 1
                """,
                (comic_id,),
            ).fetchone()
            pending = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM batch_download_tasks
                    WHERE parent_id = ?
                      AND status IN ('pending', 'running', 'interrupted')
                    """,
                    (comic_id,),
                ).fetchone()[0]
            )
            source_host = str(group["source_host"] or "").strip()
            series_url = (
                f"https://{source_host}/{comic_id}"
                if source_host else ""
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO incremental_pull_tasks(
                    comic_id, comic_name, series_url, source_host,
                    comic_status, current_chapter_key,
                    current_chapter_title, current_chapter_order,
                    latest_known_chapter_key,
                    latest_known_chapter_title,
                    latest_known_chapter_order,
                    known_chapter_count, pending_task_count,
                    task_state, first_batch_at, last_pull_at,
                    last_run_at, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    comic_id,
                    str(group["comic_name"] or ""),
                    series_url,
                    source_host,
                    str(current["chapter_key"]) if current else "",
                    str(current["title"]) if current else "",
                    int(current["sort_order"]) if current else 0,
                    str(latest["chapter_key"]) if latest else "",
                    str(latest["chapter_title"]) if latest else "",
                    int(latest["sort_order"]) if latest else 0,
                    int(group["known_chapter_count"] or 0),
                    pending,
                    "downloading" if pending else "idle",
                    group["first_batch_at"],
                    group["last_pull_at"],
                    group["last_pull_at"],
                    now,
                    now,
                ),
            )

    def _migrate_v9_to_v10(self, connection: sqlite3.Connection) -> None:
        for table in ("download_tasks", "batch_download_tasks"):
            columns = {
                row["name"]
                for row in connection.execute(
                    f"PRAGMA table_info({table})"
                )
            }
            if "pull_mode" not in columns:
                connection.execute(
                    f"""
                    ALTER TABLE {table}
                    ADD COLUMN pull_mode TEXT NOT NULL DEFAULT 'initial'
                    """
                )
            if "recrawl_count" not in columns:
                connection.execute(
                    f"""
                    ALTER TABLE {table}
                    ADD COLUMN recrawl_count INTEGER NOT NULL DEFAULT 0
                    """
                )
            if "recrawl_requested_at" not in columns:
                connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN recrawl_requested_at TEXT"
                )

    def _migrate_v10_to_v11(self, connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(books)")
        }
        if "access_level" not in columns:
            connection.execute(
                """
                ALTER TABLE books
                ADD COLUMN access_level INTEGER NOT NULL DEFAULT 0
                """
            )

    def _migrate_v11_to_v12(self, connection: sqlite3.Connection) -> None:
        book_info = connection.execute("PRAGMA table_info(books)").fetchall()
        if not book_info or str(book_info[0]["type"]).upper() == "INTEGER":
            return
        try:
            rows = connection.execute("SELECT * FROM books").fetchall()
            numeric_ids = []
            for row in rows:
                value = str(row["id"])
                if value.isdigit() and int(value) > 0:
                    numeric_ids.append(int(value))
            next_id = (max(numeric_ids) if numeric_ids else 0) + 1
            mapping = {}
            for row in rows:
                legacy = str(row["id"])
                if legacy.isdigit() and int(legacy) > 0:
                    mapping[legacy] = int(legacy)
                else:
                    while next_id in mapping.values():
                        next_id += 1
                    mapping[legacy] = next_id
                    next_id += 1
            if len(set(mapping.values())) != len(mapping):
                raise sqlite3.IntegrityError("book id migration produced duplicates")
            connection.execute(
                """
                CREATE TABLE _id_migration_map (
                    legacy_id TEXT PRIMARY KEY,
                    new_id INTEGER NOT NULL UNIQUE
                )
                """
            )
            connection.executemany(
                "INSERT INTO _id_migration_map(legacy_id, new_id) VALUES (?, ?)",
                list(mapping.items()),
            )

            connection.execute("ALTER TABLE books RENAME TO _books_v11")
            connection.execute(
                """
                CREATE TABLE books (
                    id INTEGER PRIMARY KEY NOT NULL,
                    legacy_id TEXT,
                    title TEXT NOT NULL,
                    raw_title TEXT,
                    directory TEXT NOT NULL,
                    source_url TEXT,
                    page_count INTEGER NOT NULL DEFAULT 0,
                    cover_file TEXT,
                    cover_version TEXT,
                    complete INTEGER NOT NULL DEFAULT 0,
                    metadata_missing INTEGER NOT NULL DEFAULT 0,
                    directory_mtime_ns INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT,
                    created_at TEXT NOT NULL,
                    missing INTEGER NOT NULL DEFAULT 0,
                    search_text TEXT NOT NULL DEFAULT '',
                    access_level INTEGER NOT NULL DEFAULT 0,
                    storage_id TEXT NOT NULL DEFAULT 'local',
                    migrated_at TEXT,
                    migration_job_id TEXT,
                    content_type TEXT NOT NULL DEFAULT 'single',
                    chapter_count INTEGER NOT NULL DEFAULT 0,
                    chapter_complete_count INTEGER NOT NULL DEFAULT 0,
                    chapter_updated_at TEXT,
                    UNIQUE(storage_id, directory)
                )
                """
            )
            columns = [
                row["name"]
                for row in connection.execute("PRAGMA table_info(_books_v11)")
            ]
            insert_columns = ["id", "legacy_id"] + [
                name for name in columns if name != "id"
            ]
            for row in connection.execute("SELECT * FROM _books_v11"):
                old_id = str(row["id"])
                values = [mapping[old_id], old_id] + [
                    row[name] for name in columns if name != "id"
                ]
                placeholders = ",".join("?" for _ in insert_columns)
                connection.execute(
                    f"INSERT INTO books({','.join(insert_columns)}) "
                    f"VALUES ({placeholders})",
                    tuple(values),
                )
            connection.execute("DROP TABLE _books_v11")

            table_ddl = {
                "book_categories": """
                    CREATE TABLE book_categories (
                        book_id INTEGER NOT NULL,
                        category_id INTEGER NOT NULL,
                        PRIMARY KEY(book_id, category_id)
                    )
                """,
                "book_chapters": """
                    CREATE TABLE book_chapters (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        book_id INTEGER NOT NULL,
                        chapter_key TEXT NOT NULL,
                        title TEXT NOT NULL,
                        raw_title TEXT NOT NULL DEFAULT '',
                        chapter_type TEXT NOT NULL DEFAULT 'chapter',
                        sort_order INTEGER NOT NULL,
                        source_url TEXT NOT NULL DEFAULT '',
                        directory TEXT NOT NULL,
                        page_count INTEGER NOT NULL DEFAULT 0,
                        cover_file TEXT NOT NULL DEFAULT '',
                        cover_version TEXT NOT NULL DEFAULT '',
                        complete INTEGER NOT NULL DEFAULT 0,
                        metadata_missing INTEGER NOT NULL DEFAULT 0,
                        missing INTEGER NOT NULL DEFAULT 0,
                        directory_mtime_ns INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT,
                        created_at TEXT NOT NULL,
                        UNIQUE(book_id, chapter_key)
                    )
                """,
                "favorites": """
                    CREATE TABLE favorites (
                        book_id INTEGER PRIMARY KEY,
                        added_at TEXT NOT NULL
                    )
                """,
                "user_favorites": """
                    CREATE TABLE user_favorites (
                        user_id INTEGER NOT NULL,
                        book_id INTEGER NOT NULL,
                        added_at TEXT NOT NULL,
                        PRIMARY KEY(user_id, book_id)
                    )
                """,
                "download_tasks": """
                    CREATE TABLE download_tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        comic_id INTEGER NOT NULL,
                        download_url TEXT NOT NULL,
                        title TEXT NOT NULL DEFAULT '',
                        raw_title TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'pending',
                        stage TEXT NOT NULL DEFAULT 'queued',
                        selector TEXT NOT NULL,
                        path_prefix TEXT NOT NULL,
                        output_root TEXT NOT NULL,
                        output_dir TEXT NOT NULL DEFAULT '',
                        result_file TEXT NOT NULL DEFAULT '',
                        log_file TEXT NOT NULL DEFAULT '',
                        total_images INTEGER NOT NULL DEFAULT 0,
                        processed_images INTEGER NOT NULL DEFAULT 0,
                        downloaded_images INTEGER NOT NULL DEFAULT 0,
                        skipped_images INTEGER NOT NULL DEFAULT 0,
                        failed_images INTEGER NOT NULL DEFAULT 0,
                        last_image_order INTEGER NOT NULL DEFAULT 0,
                        access_level INTEGER NOT NULL DEFAULT 0,
                        error_code TEXT NOT NULL DEFAULT '',
                        error_message TEXT NOT NULL DEFAULT '',
                        attempt_count INTEGER NOT NULL DEFAULT 0,
                        pull_mode TEXT NOT NULL DEFAULT 'initial',
                        recrawl_count INTEGER NOT NULL DEFAULT 0,
                        recrawl_requested_at TEXT,
                        created_at TEXT NOT NULL,
                        started_at TEXT,
                        finished_at TEXT,
                        updated_at TEXT NOT NULL
                    )
                """,
                "batch_download_tasks": """
                    CREATE TABLE batch_download_tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        batch_no TEXT NOT NULL,
                        parent_id INTEGER NOT NULL,
                        comic_name TEXT NOT NULL,
                        chapter_key TEXT NOT NULL,
                        chapter_title TEXT NOT NULL,
                        sort_order INTEGER NOT NULL,
                        download_url TEXT NOT NULL,
                        source_host TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        stage TEXT NOT NULL DEFAULT 'queued',
                        selector TEXT NOT NULL,
                        path_prefix TEXT NOT NULL DEFAULT '',
                        output_root TEXT NOT NULL,
                        output_dir TEXT NOT NULL DEFAULT '',
                        result_file TEXT NOT NULL DEFAULT '',
                        log_file TEXT NOT NULL DEFAULT '',
                        total_images INTEGER NOT NULL DEFAULT 0,
                        processed_images INTEGER NOT NULL DEFAULT 0,
                        downloaded_images INTEGER NOT NULL DEFAULT 0,
                        skipped_images INTEGER NOT NULL DEFAULT 0,
                        failed_images INTEGER NOT NULL DEFAULT 0,
                        access_level INTEGER NOT NULL DEFAULT 0,
                        error_code TEXT NOT NULL DEFAULT '',
                        error_message TEXT NOT NULL DEFAULT '',
                        attempt_count INTEGER NOT NULL DEFAULT 0,
                        pull_mode TEXT NOT NULL DEFAULT 'initial',
                        recrawl_count INTEGER NOT NULL DEFAULT 0,
                        recrawl_requested_at TEXT,
                        created_at TEXT NOT NULL,
                        started_at TEXT,
                        finished_at TEXT,
                        updated_at TEXT NOT NULL,
                        UNIQUE(batch_no, parent_id, chapter_key)
                    )
                """,
                "incremental_pull_tasks": """
                    CREATE TABLE incremental_pull_tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        comic_id INTEGER NOT NULL,
                        comic_name TEXT NOT NULL DEFAULT '',
                        series_url TEXT NOT NULL,
                        source_host TEXT NOT NULL DEFAULT '',
                        comic_status INTEGER NOT NULL DEFAULT 0,
                        current_chapter_key TEXT NOT NULL DEFAULT '',
                        current_chapter_title TEXT NOT NULL DEFAULT '',
                        current_chapter_order INTEGER NOT NULL DEFAULT 0,
                        latest_known_chapter_key TEXT NOT NULL DEFAULT '',
                        latest_known_chapter_title TEXT NOT NULL DEFAULT '',
                        latest_known_chapter_order INTEGER NOT NULL DEFAULT 0,
                        known_chapter_count INTEGER NOT NULL DEFAULT 0,
                        pending_task_count INTEGER NOT NULL DEFAULT 0,
                        last_batch_no TEXT NOT NULL DEFAULT '',
                        task_state TEXT NOT NULL DEFAULT 'idle',
                        error_code TEXT NOT NULL DEFAULT '',
                        error_message TEXT NOT NULL DEFAULT '',
                        attempt_count INTEGER NOT NULL DEFAULT 0,
                        first_batch_at TEXT,
                        last_checked_at TEXT,
                        last_pull_at TEXT,
                        last_run_at TEXT,
                        last_completed_at TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """,
                "deletion_tasks": """
                    CREATE TABLE deletion_tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        batch_no TEXT NOT NULL,
                        task_type TEXT NOT NULL,
                        task_order INTEGER NOT NULL,
                        book_id INTEGER NOT NULL,
                        book_title TEXT NOT NULL DEFAULT '',
                        content_type TEXT NOT NULL DEFAULT 'single',
                        chapter_id INTEGER,
                        chapter_key TEXT NOT NULL DEFAULT '',
                        chapter_title TEXT NOT NULL DEFAULT '',
                        storage_id TEXT NOT NULL,
                        storage_root TEXT NOT NULL,
                        target_relative_path TEXT NOT NULL DEFAULT '',
                        target_path_snapshot TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'pending',
                        stage TEXT NOT NULL DEFAULT 'queued',
                        message TEXT NOT NULL DEFAULT '',
                        error_code TEXT NOT NULL DEFAULT '',
                        error_message TEXT NOT NULL DEFAULT '',
                        retry_count INTEGER NOT NULL DEFAULT 0,
                        total_files INTEGER NOT NULL DEFAULT 0,
                        processed_files INTEGER NOT NULL DEFAULT 0,
                        total_bytes INTEGER NOT NULL DEFAULT 0,
                        quarantine_path TEXT NOT NULL DEFAULT '',
                        cleanup_required INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        started_at TEXT,
                        finished_at TEXT,
                        updated_at TEXT NOT NULL
                    )
                """,
                "migration_records": """
                    CREATE TABLE migration_records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        batch_no TEXT NOT NULL,
                        book_id INTEGER NOT NULL,
                        book_title TEXT NOT NULL,
                        source_storage_id TEXT NOT NULL DEFAULT 'local',
                        destination_storage_id TEXT NOT NULL,
                        source_relative_path TEXT NOT NULL,
                        destination_relative_path TEXT NOT NULL,
                        source_path TEXT NOT NULL,
                        destination_path TEXT NOT NULL,
                        status TEXT NOT NULL,
                        stage TEXT NOT NULL,
                        total_files INTEGER NOT NULL DEFAULT 0,
                        total_bytes INTEGER NOT NULL DEFAULT 0,
                        copied_files INTEGER NOT NULL DEFAULT 0,
                        copied_bytes INTEGER NOT NULL DEFAULT 0,
                        verified_files INTEGER NOT NULL DEFAULT 0,
                        verification_summary TEXT NOT NULL DEFAULT '',
                        error_code TEXT NOT NULL DEFAULT '',
                        error_message TEXT NOT NULL DEFAULT '',
                        retry_count INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        started_at TEXT,
                        finished_at TEXT,
                        updated_at TEXT NOT NULL
                    )
                """,
            }
            id_columns = {
                "book_categories": {"book_id"},
                "book_chapters": {"book_id"},
                "favorites": {"book_id"},
                "user_favorites": {"book_id"},
                "download_tasks": {"comic_id"},
                "batch_download_tasks": {"parent_id"},
                "incremental_pull_tasks": {"comic_id"},
                "deletion_tasks": {"book_id"},
                "migration_records": {"book_id"},
            }
            for table, ddl in table_ddl.items():
                old_table = f"_{table}_v11"
                connection.execute(f"ALTER TABLE {table} RENAME TO {old_table}")
                connection.execute(ddl)
                columns = [
                    row["name"]
                    for row in connection.execute(
                        f"PRAGMA table_info({old_table})"
                    )
                ]
                selects = []
                for name in columns:
                    if name in id_columns[table]:
                        selects.append(
                            f"COALESCE((SELECT new_id FROM _id_migration_map "
                            f"WHERE legacy_id = CAST({name} AS TEXT)), "
                            f"CASE WHEN CAST({name} AS TEXT) NOT GLOB '*[^0-9]*' "
                            f"AND CAST({name} AS TEXT) <> '' "
                            f"THEN CAST({name} AS INTEGER) ELSE 0 END) AS {name}"
                        )
                    else:
                        selects.append(name)
                connection.execute(
                    f"INSERT INTO {table}({','.join(columns)}) "
                    f"SELECT {','.join(selects)} FROM {old_table}"
                )
                connection.execute(f"DROP TABLE {old_table}")

            connection.execute("DROP TABLE IF EXISTS books_fts")
            connection.execute("DROP TABLE IF EXISTS book_chapters_fts")
            connection.execute("DROP TABLE _id_migration_map")
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS trg_books_require_id
                BEFORE INSERT ON books
                WHEN NEW.id IS NULL OR NEW.id <= 0
                BEGIN
                    SELECT RAISE(ABORT, 'books.id is required');
                END
                """
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO schema_migrations(version, applied_at)
                VALUES (?, ?)
                """,
                (SCHEMA_VERSION, datetime.now().isoformat(timespec="seconds")),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _migrate_v12_to_v13(self, connection: sqlite3.Connection) -> None:
        for table in ("download_tasks", "batch_download_tasks"):
            columns = {
                row["name"]
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
            if "access_level" not in columns:
                connection.execute(
                    f"""
                    ALTER TABLE {table}
                    ADD COLUMN access_level INTEGER NOT NULL DEFAULT 0
                    """
                )

    def _migrate_v13_to_v14(self, connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(app_settings)")
        }
        if (
            columns
            and "id" in columns
            and "setting_key" in columns
            and "description" in columns
        ):
            return
        if not columns:
            return

        now = datetime.now().isoformat(timespec="seconds")
        connection.execute("SAVEPOINT migrate_app_settings_v14")
        try:
            rows = connection.execute(
                "SELECT key, value FROM app_settings"
            ).fetchall()
            connection.execute(
                "ALTER TABLE app_settings RENAME TO app_settings_v13_old"
            )
            connection.execute(
                """
                CREATE TABLE app_settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    setting_key TEXT NOT NULL UNIQUE,
                    value TEXT NOT NULL DEFAULT '',
                    value_type TEXT NOT NULL DEFAULT 'string',
                    description TEXT NOT NULL DEFAULT '',
                    is_secret INTEGER NOT NULL DEFAULT 0,
                    editable INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            for row in rows:
                key = str(row["key"])
                connection.execute(
                    """
                    INSERT INTO app_settings(
                        setting_key, value, value_type, description,
                        is_secret, editable, created_at, updated_at
                    ) VALUES (?, ?, 'string', ?, 0, ?, ?, ?)
                    """,
                    (
                        key,
                        str(row["value"]),
                        "旧版配置项。",
                        int(not key.startswith("legacy_")),
                        now,
                        now,
                    ),
                )
            connection.execute("DROP TABLE app_settings_v13_old")
            connection.execute("RELEASE SAVEPOINT migrate_app_settings_v14")
        except Exception:
            connection.execute(
                "ROLLBACK TO SAVEPOINT migrate_app_settings_v14"
            )
            connection.execute("RELEASE SAVEPOINT migrate_app_settings_v14")
            raise

    def _migrate_v14_to_v15(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            DELETE FROM app_settings
            WHERE setting_key = 'project.root'
            """
        )

    def _migrate_v15_to_v16(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            DELETE FROM app_settings
            WHERE setting_key IN (
                'legacy_settings_migrated',
                'legacy_favorites_migrated'
            )
            """
        )

    def _migrate_v16_to_v17(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            DELETE FROM app_settings
            WHERE setting_key = 'download.completed_file'
            """
        )

    def _migrate_v17_to_v18(self, connection: sqlite3.Connection) -> None:
        connection.execute("DROP TABLE IF EXISTS scan_runs")

    def _migrate_v18_to_v19(self, connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(users)")
        }
        if columns and "must_change_password" not in columns:
            connection.execute(
                """
                ALTER TABLE users
                ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0
                """
            )

    def _create_tables(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY NOT NULL,
                legacy_id TEXT,
                title TEXT NOT NULL,
                raw_title TEXT,
                directory TEXT NOT NULL,
                source_url TEXT,
                page_count INTEGER NOT NULL DEFAULT 0,
                cover_file TEXT,
                cover_version TEXT,
                complete INTEGER NOT NULL DEFAULT 0,
                metadata_missing INTEGER NOT NULL DEFAULT 0,
                directory_mtime_ns INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT,
                created_at TEXT NOT NULL,
                missing INTEGER NOT NULL DEFAULT 0,
                search_text TEXT NOT NULL DEFAULT '',
                access_level INTEGER NOT NULL DEFAULT 0,
                storage_id TEXT NOT NULL DEFAULT 'local',
                migrated_at TEXT,
                migration_job_id TEXT,
                content_type TEXT NOT NULL DEFAULT 'single',
                chapter_count INTEGER NOT NULL DEFAULT 0,
                chapter_complete_count INTEGER NOT NULL DEFAULT 0,
                chapter_updated_at TEXT,
                UNIQUE(storage_id, directory)
            )
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_books_require_id
            BEFORE INSERT ON books
            WHEN NEW.id IS NULL OR NEW.id <= 0
            BEGIN
                SELECT RAISE(ABORT, 'books.id is required');
            END
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL UNIQUE,
                book_count INTEGER NOT NULL DEFAULT 0,
                blocked INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS book_categories (
                book_id INTEGER NOT NULL,
                category_id INTEGER NOT NULL,
                PRIMARY KEY(book_id, category_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS book_chapters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL,
                chapter_key TEXT NOT NULL,
                title TEXT NOT NULL,
                raw_title TEXT NOT NULL DEFAULT '',
                chapter_type TEXT NOT NULL DEFAULT 'chapter',
                sort_order INTEGER NOT NULL,
                source_url TEXT NOT NULL DEFAULT '',
                directory TEXT NOT NULL,
                page_count INTEGER NOT NULL DEFAULT 0,
                cover_file TEXT NOT NULL DEFAULT '',
                cover_version TEXT NOT NULL DEFAULT '',
                complete INTEGER NOT NULL DEFAULT 0,
                metadata_missing INTEGER NOT NULL DEFAULT 0,
                missing INTEGER NOT NULL DEFAULT 0,
                directory_mtime_ns INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(book_id, chapter_key)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS favorites (
                book_id INTEGER PRIMARY KEY,
                added_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                setting_key TEXT NOT NULL UNIQUE,
                value TEXT NOT NULL DEFAULT '',
                value_type TEXT NOT NULL DEFAULT 'string',
                description TEXT NOT NULL DEFAULT '',
                is_secret INTEGER NOT NULL DEFAULT 0,
                editable INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS storage_roots (
                id TEXT PRIMARY KEY,
                root_path TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                marker_version INTEGER NOT NULL DEFAULT 1,
                last_status TEXT NOT NULL DEFAULT 'unknown',
                last_error TEXT NOT NULL DEFAULT '',
                last_seen_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS migration_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_no TEXT NOT NULL,
                book_id INTEGER NOT NULL,
                book_title TEXT NOT NULL,
                source_storage_id TEXT NOT NULL DEFAULT 'local',
                destination_storage_id TEXT NOT NULL,
                source_relative_path TEXT NOT NULL,
                destination_relative_path TEXT NOT NULL,
                source_path TEXT NOT NULL,
                destination_path TEXT NOT NULL,
                status TEXT NOT NULL,
                stage TEXT NOT NULL,
                total_files INTEGER NOT NULL DEFAULT 0,
                total_bytes INTEGER NOT NULL DEFAULT 0,
                copied_files INTEGER NOT NULL DEFAULT 0,
                copied_bytes INTEGER NOT NULL DEFAULT 0,
                verified_files INTEGER NOT NULL DEFAULT 0,
                verification_summary TEXT NOT NULL DEFAULT '',
                error_code TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                retry_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS download_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                comic_id INTEGER NOT NULL,
                download_url TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                raw_title TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                stage TEXT NOT NULL DEFAULT 'queued',
                selector TEXT NOT NULL,
                path_prefix TEXT NOT NULL,
                output_root TEXT NOT NULL,
                output_dir TEXT NOT NULL DEFAULT '',
                result_file TEXT NOT NULL DEFAULT '',
                log_file TEXT NOT NULL DEFAULT '',
                total_images INTEGER NOT NULL DEFAULT 0,
                processed_images INTEGER NOT NULL DEFAULT 0,
                downloaded_images INTEGER NOT NULL DEFAULT 0,
                skipped_images INTEGER NOT NULL DEFAULT 0,
                failed_images INTEGER NOT NULL DEFAULT 0,
                last_image_order INTEGER NOT NULL DEFAULT 0,
                access_level INTEGER NOT NULL DEFAULT 0,
                error_code TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                pull_mode TEXT NOT NULL DEFAULT 'initial',
                recrawl_count INTEGER NOT NULL DEFAULT 0,
                recrawl_requested_at TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS batch_download_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_no TEXT NOT NULL,
                parent_id INTEGER NOT NULL,
                comic_name TEXT NOT NULL,
                chapter_key TEXT NOT NULL,
                chapter_title TEXT NOT NULL,
                sort_order INTEGER NOT NULL,
                download_url TEXT NOT NULL,
                source_host TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                stage TEXT NOT NULL DEFAULT 'queued',
                selector TEXT NOT NULL,
                path_prefix TEXT NOT NULL DEFAULT '',
                output_root TEXT NOT NULL,
                output_dir TEXT NOT NULL DEFAULT '',
                result_file TEXT NOT NULL DEFAULT '',
                log_file TEXT NOT NULL DEFAULT '',
                total_images INTEGER NOT NULL DEFAULT 0,
                processed_images INTEGER NOT NULL DEFAULT 0,
                downloaded_images INTEGER NOT NULL DEFAULT 0,
                skipped_images INTEGER NOT NULL DEFAULT 0,
                failed_images INTEGER NOT NULL DEFAULT 0,
                access_level INTEGER NOT NULL DEFAULT 0,
                error_code TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                pull_mode TEXT NOT NULL DEFAULT 'initial',
                recrawl_count INTEGER NOT NULL DEFAULT 0,
                recrawl_requested_at TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(batch_no, parent_id, chapter_key)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS incremental_pull_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                comic_id INTEGER NOT NULL,
                comic_name TEXT NOT NULL DEFAULT '',
                series_url TEXT NOT NULL,
                source_host TEXT NOT NULL DEFAULT '',
                comic_status INTEGER NOT NULL DEFAULT 0,
                current_chapter_key TEXT NOT NULL DEFAULT '',
                current_chapter_title TEXT NOT NULL DEFAULT '',
                current_chapter_order INTEGER NOT NULL DEFAULT 0,
                latest_known_chapter_key TEXT NOT NULL DEFAULT '',
                latest_known_chapter_title TEXT NOT NULL DEFAULT '',
                latest_known_chapter_order INTEGER NOT NULL DEFAULT 0,
                known_chapter_count INTEGER NOT NULL DEFAULT 0,
                pending_task_count INTEGER NOT NULL DEFAULT 0,
                last_batch_no TEXT NOT NULL DEFAULT '',
                task_state TEXT NOT NULL DEFAULT 'idle',
                error_code TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                first_batch_at TEXT,
                last_checked_at TEXT,
                last_pull_at TEXT,
                last_run_at TEXT,
                last_completed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS deletion_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_no TEXT NOT NULL,
                task_type TEXT NOT NULL,
                task_order INTEGER NOT NULL,
                book_id INTEGER NOT NULL,
                book_title TEXT NOT NULL DEFAULT '',
                content_type TEXT NOT NULL DEFAULT 'single',
                chapter_id INTEGER,
                chapter_key TEXT NOT NULL DEFAULT '',
                chapter_title TEXT NOT NULL DEFAULT '',
                storage_id TEXT NOT NULL,
                storage_root TEXT NOT NULL,
                target_relative_path TEXT NOT NULL DEFAULT '',
                target_path_snapshot TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                stage TEXT NOT NULL DEFAULT 'queued',
                message TEXT NOT NULL DEFAULT '',
                error_code TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                retry_count INTEGER NOT NULL DEFAULT 0,
                total_files INTEGER NOT NULL DEFAULT 0,
                processed_files INTEGER NOT NULL DEFAULT 0,
                total_bytes INTEGER NOT NULL DEFAULT 0,
                quarantine_path TEXT NOT NULL DEFAULT '',
                cleanup_required INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                username_normalized TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL DEFAULT '',
                password_hash TEXT NOT NULL,
                password_changed_at TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                active INTEGER NOT NULL DEFAULT 1,
                must_change_password INTEGER NOT NULL DEFAULT 0,
                last_login_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT NOT NULL UNIQUE,
                csrf_token TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                ip_address TEXT NOT NULL DEFAULT '',
                user_agent TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_favorites (
                user_id INTEGER NOT NULL,
                book_id INTEGER NOT NULL,
                added_at TEXT NOT NULL,
                PRIMARY KEY(user_id, book_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL,
                target_type TEXT NOT NULL DEFAULT '',
                target_id TEXT NOT NULL DEFAULT '',
                details TEXT NOT NULL DEFAULT '',
                ip_address TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )

    def _create_indexes(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_books_updated
            ON books(missing, updated_at DESC, id DESC)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_books_storage
            ON books(storage_id, directory)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_book_categories_category
            ON book_categories(category_id, book_id)
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_book_chapters_key
            ON book_chapters(book_id, chapter_key)
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_book_chapters_order
            ON book_chapters(book_id, sort_order)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_book_chapters_book
            ON book_chapters(book_id, sort_order)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_book_chapters_missing
            ON book_chapters(book_id, missing, sort_order)
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_storage_roots_path
            ON storage_roots(root_path)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_storage_roots_status
            ON storage_roots(last_status, updated_at DESC)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_migration_records_created
            ON migration_records(created_at DESC, id DESC)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_migration_records_batch
            ON migration_records(batch_no, created_at, id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_migration_records_book
            ON migration_records(book_id, created_at DESC)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_migration_records_status
            ON migration_records(status, updated_at)
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_migration_records_active_book
            ON migration_records(book_id)
            WHERE status IN ('queued', 'migrating', 'interrupted')
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_download_tasks_created
            ON download_tasks(created_at DESC, id DESC)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_download_tasks_comic
            ON download_tasks(comic_id, created_at DESC)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_download_tasks_status
            ON download_tasks(status, updated_at)
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_download_tasks_active_comic
            ON download_tasks(comic_id)
            WHERE status IN ('pending', 'running', 'interrupted')
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_batch_download_created
            ON batch_download_tasks(created_at DESC, id DESC)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_batch_download_batch
            ON batch_download_tasks(batch_no, sort_order, id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_batch_download_parent
            ON batch_download_tasks(parent_id, sort_order, id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_batch_download_status
            ON batch_download_tasks(status, updated_at)
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_batch_download_active_chapter
            ON batch_download_tasks(parent_id, chapter_key)
            WHERE status IN ('pending', 'running', 'interrupted')
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_incremental_pull_comic
            ON incremental_pull_tasks(comic_id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_incremental_pull_status
            ON incremental_pull_tasks(
                comic_status, updated_at DESC, id DESC
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_incremental_pull_state
            ON incremental_pull_tasks(task_state, updated_at)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_deletion_created
            ON deletion_tasks(created_at DESC, id DESC)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_deletion_batch
            ON deletion_tasks(batch_no, task_order, id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_deletion_book
            ON deletion_tasks(book_id, task_order)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_deletion_status
            ON deletion_tasks(status, updated_at)
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_deletion_active_chapter
            ON deletion_tasks(book_id, chapter_key)
            WHERE task_type = 'chapter'
              AND status IN (
                  'pending', 'waiting_dependency',
                  'running', 'interrupted'
              )
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_deletion_active_book
            ON deletion_tasks(book_id)
            WHERE task_type IN ('single_book', 'series_finalize')
              AND status IN (
                  'pending', 'waiting_dependency',
                  'running', 'interrupted'
              )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_books_access_updated
            ON books(access_level, missing, updated_at DESC, id DESC)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_users_role_active
            ON users(role, active, username_normalized)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sessions_user
            ON sessions(user_id, revoked_at, expires_at)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sessions_expiry
            ON sessions(expires_at)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_user_favorites_book
            ON user_favorites(book_id, user_id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_user_favorites_user_added
            ON user_favorites(user_id, added_at DESC)
            """
        )

    def _create_fts(self, connection: sqlite3.Connection) -> None:
        try:
            connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS books_fts USING fts5(
                    book_id UNINDEXED,
                    title,
                    raw_title,
                    categories_text,
                    search_text,
                    tokenize='trigram'
                )
                """
            )
        except sqlite3.OperationalError:
            connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS books_fts USING fts5(
                    book_id UNINDEXED,
                    title,
                    raw_title,
                    categories_text,
                    search_text
                )
                """
            )
        try:
            connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS book_chapters_fts USING fts5(
                    chapter_id UNINDEXED,
                    book_id UNINDEXED,
                    title,
                    raw_title,
                    tokenize='trigram'
                )
                """
            )
        except sqlite3.OperationalError:
            connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS book_chapters_fts USING fts5(
                    chapter_id UNINDEXED,
                    book_id UNINDEXED,
                    title,
                    raw_title
                )
                """
            )
