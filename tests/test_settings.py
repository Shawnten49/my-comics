from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from comic_web.database import Database, SCHEMA_VERSION
from comic_web.settings_repository import SettingsError, SettingsRepository


class SettingsRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="comic-settings-test-"
        )
        self.base = Path(self.temporary.name)
        self.database = Database(self.base / "library.db")
        self.settings = SettingsRepository(self.database, self.base)
        self.settings.seed_defaults()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_defaults_and_secret_storage(self) -> None:
        self.assertEqual(
            (self.base / "download").resolve(),
            self.settings.get_path("download.root"),
        )
        self.assertFalse(
            self.settings.is_configured("security.verification_code")
        )
        self.settings.set_secret(
            "security.verification_code",
            "verification-secret",
        )
        self.assertTrue(
            self.settings.verify_secret(
                "security.verification_code",
                "verification-secret",
            )
        )
        self.assertFalse(
            self.settings.verify_secret(
                "security.verification_code",
                "wrong-secret",
            )
        )
        with self.database.connect() as connection:
            stored = connection.execute(
                """
                SELECT value FROM app_settings
                WHERE setting_key = 'security.verification_code'
                """
            ).fetchone()["value"]
        self.assertNotEqual("verification-secret", stored)
        self.assertTrue(stored.startswith("pbkdf2_sha256$"))
        listed = {
            item["setting_key"]: item
            for item in self.settings.list_settings()
        }
        self.assertNotIn("project.root", listed)
        self.assertNotIn("legacy_settings_migrated", listed)
        self.assertNotIn("legacy_favorites_migrated", listed)
        self.assertNotIn("download.completed_file", listed)
        self.assertEqual("", listed["security.verification_code"]["value"])
        self.assertTrue(listed["security.verification_code"]["configured"])

    def test_path_validation(self) -> None:
        with self.assertRaises(SettingsError):
            self.settings.set("download.root", "relative/path")

    def test_secret_default_only_applies_once(self) -> None:
        self.assertTrue(
            self.settings.ensure_secret_default(
                "security.verification_code",
                "123456",
            )
        )
        self.assertTrue(
            self.settings.verify_secret(
                "security.verification_code",
                "123456",
            )
        )
        self.assertFalse(
            self.settings.ensure_secret_default(
                "security.verification_code",
                "different-code",
            )
        )
        self.assertTrue(
            self.settings.verify_secret(
                "security.verification_code",
                "123456",
            )
        )

    def test_v13_app_settings_are_migrated_without_data_loss(self) -> None:
        with self.database.connect() as connection:
            connection.execute("DROP TABLE app_settings")
            connection.execute(
                """
                CREATE TABLE app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO app_settings(key, value) VALUES (?, ?)",
                ("legacy_settings_migrated", "1"),
            )
            connection.execute(
                "INSERT INTO app_settings(key, value) VALUES (?, ?)",
                ("legacy_favorites_migrated", "1"),
            )
            connection.execute(
                "INSERT INTO app_settings(key, value) VALUES (?, ?)",
                ("project.root", "/tmp/legacy-project"),
            )
            connection.execute(
                "INSERT INTO app_settings(key, value) VALUES (?, ?)",
                ("download.completed_file", "/tmp/completed.txt"),
            )
            connection.execute(
                "DELETE FROM schema_migrations WHERE version = ?",
                (SCHEMA_VERSION,),
            )
            connection.commit()

        upgraded = Database(self.base / "library.db")
        with upgraded.connect() as connection:
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(app_settings)"
                )
            }
            removed_rows = connection.execute(
                """
                SELECT setting_key
                FROM app_settings
                WHERE setting_key IN (
                    'legacy_settings_migrated',
                    'legacy_favorites_migrated',
                    'project.root',
                    'download.completed_file'
                )
                """
            ).fetchall()
            version = connection.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
        self.assertIn("id", columns)
        self.assertIn("setting_key", columns)
        self.assertIn("description", columns)
        self.assertEqual([], removed_rows)
        self.assertEqual(19, version)


if __name__ == "__main__":
    unittest.main()
