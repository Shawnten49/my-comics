from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from comic_web.batch_download_repository import BatchDownloadRepository
from comic_web.database import Database
from comic_web.download_repository import DownloadRepository
from comic_web.download_root import DownloadRootError, DownloadRootGuard
from comic_web.indexer import ComicLibrary
from comic_web.settings_repository import SettingsRepository


class DownloadRootGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="comic-root-test-"
        )
        self.base = Path(self.temporary.name)
        self.root = self.base / "download"
        self.root.mkdir()
        self.database = Database(self.base / "library.db")
        self.settings = SettingsRepository(self.database, self.base)
        self.settings.seed_defaults()
        self.library = ComicLibrary(
            self.root,
            database_path=self.base / "library.db",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def set_raw_root(self, value: str) -> None:
        now = "2026-01-01T00:00:00"
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO app_settings(
                    setting_key, value, value_type, description,
                    is_secret, editable, created_at, updated_at
                ) VALUES (
                    'download.root', ?, 'path', 'test root',
                    0, 1, ?, ?
                )
                ON CONFLICT(setting_key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (value, now, now),
            )
            connection.commit()

    def test_valid_root_and_restart_detection(self) -> None:
        guard = DownloadRootGuard(self.settings, expected_root=self.root)
        status = guard.validate()
        self.assertTrue(status["available"])
        self.assertTrue(status["writable"])
        self.assertFalse(any(self.root.glob(".comic-write-test-*")))

        other = self.base / "other"
        other.mkdir()
        self.set_raw_root(str(other))
        with self.assertRaises(DownloadRootError) as context:
            guard.validate()
        self.assertEqual(
            "download_root_restart_required",
            context.exception.code,
        )

    def test_missing_file_and_unconfigured_roots(self) -> None:
        guard = DownloadRootGuard(self.settings)
        self.set_raw_root(str(self.base / "missing"))
        with self.assertRaises(DownloadRootError) as missing:
            guard.validate()
        self.assertEqual("download_root_missing", missing.exception.code)

        file_path = self.base / "not-a-directory"
        file_path.write_text("x", encoding="utf-8")
        self.set_raw_root(str(file_path))
        with self.assertRaises(DownloadRootError) as not_directory:
            guard.validate()
        self.assertEqual(
            "download_root_not_directory",
            not_directory.exception.code,
        )

        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM app_settings WHERE setting_key='download.root'"
            )
            connection.commit()
        with self.assertRaises(DownloadRootError) as unconfigured:
            guard.validate()
        self.assertEqual(
            "download_root_not_configured",
            unconfigured.exception.code,
        )

    def test_task_repositories_reject_unavailable_root(self) -> None:
        self.set_raw_root(str(self.base / "missing"))
        guard = DownloadRootGuard(self.settings, expected_root=self.root)
        downloads = DownloadRepository(
            self.database,
            self.library,
            output_root=self.root,
            project_root=self.base,
            download_root_guard=guard,
        )
        with self.assertRaises(DownloadRootError):
            downloads.create_task(
                123,
                "https://18comic.vip/photo/123",
            )
        with self.database.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM download_tasks"
            ).fetchone()[0]
        self.assertEqual(0, count)

        batches = BatchDownloadRepository(
            self.database,
            self.library,
            output_root=self.root,
            project_root=self.base,
            download_root_guard=guard,
        )
        with self.assertRaises(DownloadRootError):
            batches.create_tasks("ba_" + "0" * 32, ["1"])
        with self.database.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM batch_download_tasks"
            ).fetchone()[0]
        self.assertEqual(0, count)


if __name__ == "__main__":
    unittest.main()
