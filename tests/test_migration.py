from __future__ import annotations

import json
import http.client
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from comic_web.database import Database
from comic_web.app import ComicHTTPServer, migration_message, port_in_use
from comic_web.favorites import FavoriteStore
from comic_web.indexer import ComicLibrary
from comic_web.migration_repository import MigrationRepository
from comic_web.migration_worker import MigrationWorker
from comic_web.storage import ExternalStorageOffline, StorageAccessError
from comic_web.thumbnails import ThumbnailCache


class MigrationIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="comic-migration-test-"
        )
        self.root = Path(self.temporary.name) / "local"
        self.external = Path(self.temporary.name) / "external"
        self.database_path = Path(self.temporary.name) / "library.db"
        self.root.mkdir()
        self.external.mkdir()
        self.book_dir = self.root / "12345_Test Comic"
        self.book_dir.mkdir()
        (self.book_dir / "00001.webp").write_bytes(b"page-one")
        (self.book_dir / "00002.webp").write_bytes(b"page-two")
        (self.book_dir / "._00001_rendered.webp").write_bytes(b"appledouble")
        (self.book_dir / ".DS_Store").write_bytes(b"metadata")
        (self.book_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "raw_title": "Test Comic",
                    "source_url": "https://example.invalid/photo/12345",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        second = self.root / "67890_Second Comic"
        second.mkdir()
        (second / "00001.webp").write_bytes(b"second-page")
        self.library = ComicLibrary(
            self.root,
            database_path=self.database_path,
        )
        self.library.rebuild()
        self.repository = MigrationRepository(
            self.library.database,
            self.library,
            self.library.storage,
            verification_code="test-verification-code",
        )
        self.worker = MigrationWorker(self.repository, self.library)

    def tearDown(self) -> None:
        self.worker.stop(timeout=5)
        self.temporary.cleanup()

    def test_schema_has_no_foreign_keys(self) -> None:
        with self.library.database.connect() as connection:
            references = connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND sql LIKE '%REFERENCES%'
                """
            ).fetchall()
            relations = connection.execute(
                "PRAGMA foreign_key_list(book_categories)"
            ).fetchall()
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertEqual([], references)
        self.assertEqual([], relations)
        self.assertIn("migration_records", tables)
        self.assertNotIn("migration_batches", tables)
        self.assertNotIn("migration_jobs", tables)
        self.assertNotIn("migration_events", tables)

    def test_migration_message_for_success_and_failure(self) -> None:
        self.assertEqual(
            "success",
            migration_message(
                {"status": "completed", "error_message": ""}
            ),
        )
        self.assertEqual(
            "校验失败",
            migration_message(
                {"status": "failed", "error_message": "校验失败"}
            ),
        )
        self.assertEqual(
            "迁移失败",
            migration_message(
                {"status": "failed", "error_message": ""}
            ),
        )

    def test_each_book_creates_one_shared_batch_record(self) -> None:
        result = self.repository.create_records(
            str(self.external),
            ["12345", "67890"],
            "test-verification-code",
        )
        self.assertEqual(2, result["count"])
        self.assertEqual([1, 2], result["record_ids"])
        total, records = self.repository.list_records(
            0,
            30,
            batch_no=result["batch_no"],
        )
        self.assertEqual(2, total)
        self.assertEqual(
            {12345, 67890},
            {record["book_id"] for record in records},
        )
        self.assertEqual(
            {result["batch_no"]},
            {record["batch_no"] for record in records},
        )

    def test_failed_record_retry_reuses_same_row(self) -> None:
        result = self.repository.create_records(
            str(self.external),
            ["12345"],
            "test-verification-code",
        )
        record_id = result["record_ids"][0]
        self.repository.update_record(
            record_id,
            status="failed",
            error_message="校验失败",
            finished=True,
        )
        self.repository.retry_record(record_id)
        record = self.repository.get_record(record_id)
        self.assertEqual("queued", record["status"])
        self.assertEqual(1, record["retry_count"])
        self.assertIsNone(record["finished_at"])
        total, _ = self.repository.list_records(0, 30)
        self.assertEqual(1, total)

    def test_migrates_and_keeps_external_book_browsable(self) -> None:
        validation = self.repository.validate_destination(str(self.external))
        self.assertTrue(validation["writable"])
        result = self.repository.create_records(
            str(self.external),
            ["12345"],
            "test-verification-code",
        )
        self.assertEqual(1, result["count"])
        self.worker.start()
        self.worker.notify()
        record = self._wait_for_record(result["record_ids"][0])
        self.assertEqual("completed", record["status"])
        self.assertEqual(result["batch_no"], record["batch_no"])

        self.assertFalse(self.book_dir.exists())
        target = self.external / self.book_dir.name
        self.assertTrue(target.is_dir())
        self.assertEqual(
            b"page-one",
            (target / "00001.webp").read_bytes(),
        )
        self.assertFalse((target / "._00001_rendered.webp").exists())
        self.assertFalse((target / ".DS_Store").exists())
        book = self.library.get("12345")
        self.assertIsNotNone(book)
        self.assertNotEqual("local", book.storage_id)
        self.assertEqual(2, len(book.pages))
        self.assertEqual(
            (target / "00001.webp").resolve(),
            self.library.page_path(book, "00001.webp").resolve(),
        )

        offline = self.external.with_name("external-disconnected")
        self.external.rename(offline)
        self.library.storage._probe_cache.clear()
        with self.assertRaises(StorageAccessError) as context:
            self.library.get("12345")
        self.assertIsInstance(context.exception, ExternalStorageOffline)
        offline.rename(self.external)

    def test_destination_conflict_preserves_local_source(self) -> None:
        validation = self.repository.validate_destination(str(self.external))
        self.assertTrue(validation["writable"])
        conflict = self.external / self.book_dir.name
        conflict.mkdir()
        (conflict / "different.webp").write_bytes(b"other")
        result = self.repository.create_records(
            str(self.external),
            ["12345"],
            "test-verification-code",
        )
        self.worker.start()
        self.worker.notify()
        record = self._wait_for_record(result["record_ids"][0])

        self.assertEqual("failed", record["status"])
        self.assertTrue(self.book_dir.is_dir())
        self.assertEqual(
            b"other",
            (conflict / "different.webp").read_bytes(),
        )

    def test_identical_existing_destination_is_not_adopted(self) -> None:
        validation = self.repository.validate_destination(str(self.external))
        self.assertTrue(validation["writable"])
        conflict = self.external / self.book_dir.name
        shutil.copytree(self.book_dir, conflict)
        result = self.repository.create_records(
            str(self.external),
            ["12345"],
            "test-verification-code",
        )
        self.worker.start()
        self.worker.notify()
        record = self._wait_for_record(result["record_ids"][0])

        self.assertEqual("failed", record["status"])
        self.assertTrue(self.book_dir.is_dir())
        self.assertEqual("local", self.library.get_metadata("12345").storage_id)

    def test_http_reports_external_storage_offline(self) -> None:
        validation = self.repository.validate_destination(str(self.external))
        self.assertTrue(validation["writable"])
        result = self.repository.create_records(
            str(self.external),
            ["12345"],
            "test-verification-code",
        )
        self.worker.start()
        self.worker.notify()
        migrated = self._wait_for_record(result["record_ids"][0])
        self.assertEqual("completed", migrated["status"])

        cache = ThumbnailCache(Path(self.temporary.name) / "cache")
        favorites = FavoriteStore(self.database_path)
        try:
            server = ComicHTTPServer(
                ("127.0.0.1", 0),
                self.library,
                cache,
                favorites,
                self.repository,
                self.worker,
            )
        except PermissionError:
            self.skipTest("当前沙箱不允许监听本地测试端口。")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        self.assertTrue(port_in_use("127.0.0.1", port))
        offline = self.external.with_name("external-disconnected")
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1",
                port,
                timeout=5,
            )
            connection.request("GET", "/api/health")
            health = connection.getresponse()
            health_payload = json.loads(health.read().decode("utf-8"))
            connection.close()
            self.assertEqual(200, health.status)
            self.assertIn("manage_migrations", health_payload["features"])
            self.assertIsInstance(health_payload["pid"], int)

            connection = http.client.HTTPConnection(
                "127.0.0.1",
                port,
                timeout=5,
            )
            connection.request(
                "GET",
                "/api/manage/migrations?page=1&page_size=30",
            )
            records_response = connection.getresponse()
            records_payload = json.loads(
                records_response.read().decode("utf-8")
            )
            connection.close()
            self.assertEqual(200, records_response.status)
            self.assertEqual(
                "success",
                records_payload["items"][0]["message"],
            )

            self.external.rename(offline)
            self.library.storage._probe_cache.clear()
            connection = http.client.HTTPConnection(
                "127.0.0.1",
                port,
                timeout=5,
            )
            connection.request("GET", "/api/books/12345/pages?offset=0&limit=1")
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            connection.close()
            self.assertEqual(503, response.status)
            self.assertEqual(
                "external_storage_offline",
                payload["code"],
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            if offline.exists():
                offline.rename(self.external)

    def _wait_for_record(self, record_id: str, timeout: float = 15.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            record = self.repository.get_record(record_id)
            if record and record["status"] not in {
                "queued",
                "migrating",
                "interrupted",
            }:
                return record
            time.sleep(0.05)
        self.fail("迁移记录未在限定时间内结束。")


class LegacyDatabaseMigrationTest(unittest.TestCase):
    def test_rebuilds_foreign_key_schema_without_data_loss(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="comic-db-migration-test-"
        ) as temporary:
            path = Path(temporary) / "library.db"
            connection = sqlite3.connect(str(path))
            connection.executescript(
                """
                CREATE TABLE books (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    raw_title TEXT,
                    directory TEXT NOT NULL UNIQUE,
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
                    search_text TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL UNIQUE,
                    book_count INTEGER NOT NULL DEFAULT 0,
                    blocked INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE book_categories (
                    book_id TEXT NOT NULL,
                    category_id INTEGER NOT NULL,
                    PRIMARY KEY(book_id, category_id),
                    FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
                    FOREIGN KEY(category_id) REFERENCES categories(id)
                        ON DELETE CASCADE
                );
                INSERT INTO books(
                    id, title, directory, created_at
                ) VALUES ('1', 'One', '1_One', '2026-01-01');
                INSERT INTO categories(
                    name, normalized_name, created_at, updated_at
                ) VALUES ('Tag', 'tag', '2026-01-01', '2026-01-01');
                INSERT INTO book_categories(book_id, category_id)
                VALUES ('1', 1);
                """
            )
            connection.commit()
            connection.close()
            database = Database(path)
            with database.connect() as migrated:
                row = migrated.execute(
                    "SELECT id, title, storage_id FROM books"
                ).fetchone()
                refs = migrated.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table' AND sql LIKE '%REFERENCES%'
                    """
                ).fetchall()
            self.assertEqual(1, row["id"])
            self.assertEqual("One", row["title"])
            self.assertEqual("local", row["storage_id"])
            self.assertEqual([], refs)
            self.assertTrue(
                path.with_name(f"{path.name}.pre-v2.bak").is_file()
            )

    def test_v2_jobs_are_converted_to_single_records(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="comic-v3-db-test-"
        ) as temporary:
            path = Path(temporary) / "library.db"
            connection = sqlite3.connect(str(path))
            connection.executescript(
                """
                CREATE TABLE books (
                    id TEXT PRIMARY KEY,
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
                    storage_id TEXT NOT NULL DEFAULT 'local',
                    migrated_at TEXT,
                    migration_job_id TEXT,
                    UNIQUE(storage_id, directory)
                );
                CREATE TABLE migration_batches (
                    id TEXT PRIMARY KEY,
                    destination_storage_id TEXT NOT NULL,
                    destination_root_snapshot TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE migration_jobs (
                    id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL,
                    book_id TEXT NOT NULL,
                    book_title_snapshot TEXT NOT NULL,
                    source_storage_id TEXT NOT NULL,
                    source_root_snapshot TEXT NOT NULL,
                    source_relative_path TEXT NOT NULL,
                    destination_storage_id TEXT NOT NULL,
                    destination_root_snapshot TEXT NOT NULL,
                    destination_relative_path TEXT NOT NULL,
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
                );
                CREATE TABLE migration_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    level TEXT NOT NULL,
                    event_code TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                INSERT INTO migration_batches VALUES (
                    'mb_old', 'st_old', '/Volumes/Old',
                    'failed', '2026-01-01T00:00:00'
                );
                INSERT INTO migration_jobs VALUES (
                    'mj_old', 'mb_old', '1', 'Old Comic',
                    'local', '/tmp/local', '1_Old',
                    'st_old', '/Volumes/Old', '1_Old',
                    'failed', 'stage_verifying',
                    2, 1024, 2, 1024, 1, '', 'bad', 'checksum',
                    0, '2026-01-01T00:00:00', '2026-01-01T00:00:01',
                    '2026-01-01T00:00:02', '2026-01-01T00:00:02'
                );
                """
            )
            connection.commit()
            connection.close()
            database = Database(path)
            with database.connect() as migrated:
                record = migrated.execute(
                    "SELECT * FROM migration_records"
                ).fetchone()
                old_tables = {
                    row["name"]
                    for row in migrated.execute(
                        """
                        SELECT name FROM sqlite_master
                        WHERE type = 'table' AND name IN (
                            'migration_batches', 'migration_jobs',
                            'migration_events'
                        )
                        """
                    )
                }
            self.assertEqual("mb_old", record["batch_no"])
            self.assertEqual(1, record["id"])
            self.assertEqual("/tmp/local/1_Old", record["source_path"])
            self.assertEqual(
                "/Volumes/Old/1_Old",
                record["destination_path"],
            )
            self.assertEqual("failed", record["status"])
            self.assertEqual(set(), old_tables)
            self.assertTrue(
                path.with_name(f"{path.name}.pre-v3.bak").is_file()
            )

    def test_v3_text_ids_are_rebuilt_as_autoincrement_integers(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="comic-v4-db-test-"
        ) as temporary:
            path = Path(temporary) / "library.db"
            connection = sqlite3.connect(str(path))
            connection.executescript(
                """
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                INSERT INTO schema_migrations VALUES
                    (1, '2026-01-01'), (2, '2026-01-01'), (3, '2026-01-01');
                CREATE TABLE books (
                    id TEXT PRIMARY KEY,
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
                    storage_id TEXT NOT NULL DEFAULT 'local',
                    migrated_at TEXT,
                    migration_job_id TEXT,
                    UNIQUE(storage_id, directory)
                );
                INSERT INTO books(
                    id, title, directory, created_at, migration_job_id
                ) VALUES (
                    '1', 'Old', '1_Old', '2026-01-01', 'mj_old'
                );
                CREATE TABLE migration_records (
                    id TEXT PRIMARY KEY,
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
                );
                INSERT INTO migration_records VALUES (
                    'mj_old', 'mb_old', '1', 'Old',
                    'local', 'st_old', '1_Old', '1_Old',
                    '/tmp/local/1_Old', '/Volumes/Old/1_Old',
                    'failed', 'stage_verifying',
                    1, 10, 1, 10, 0, '', 'bad', 'error',
                    0, '2026-01-01T00:00:00', '2026-01-01T00:00:01',
                    '2026-01-01T00:00:02', '2026-01-01T00:00:02'
                );
                """
            )
            connection.commit()
            connection.close()
            database = Database(path)
            with database.connect() as migrated:
                record = migrated.execute(
                    "SELECT id, batch_no FROM migration_records"
                ).fetchone()
                id_info = {
                    row["name"]: row
                    for row in migrated.execute(
                        "PRAGMA table_info(migration_records)"
                    )
                }
                book = migrated.execute(
                    "SELECT migration_job_id FROM books WHERE id = '1'"
                ).fetchone()
            self.assertEqual(1, record["id"])
            self.assertEqual("mb_old", record["batch_no"])
            self.assertEqual("INTEGER", id_info["id"]["type"].upper())
            self.assertEqual(1, id_info["id"]["pk"])
            self.assertEqual("1", book["migration_job_id"])
            self.assertTrue(
                path.with_name(f"{path.name}.pre-v4.bak").is_file()
            )


if __name__ == "__main__":
    unittest.main()
