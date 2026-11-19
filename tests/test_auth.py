from __future__ import annotations

import tempfile
import unittest
import sqlite3
from pathlib import Path

from PIL import Image

from comic_web.auth_service import AuthService, ROLE_ADMIN, ROLE_USER
from comic_web.indexer import ComicLibrary


class AuthTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="comic-auth-test-")
        self.base = Path(self.temporary.name)
        self.root = self.base / "download"
        self.root.mkdir()
        self.library = ComicLibrary(
            self.root,
            database_path=self.base / "library.db",
        )
        self.auth = AuthService(self.library.database, self.base)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_setup_login_and_session(self) -> None:
        token = self.auth.setup_token()
        created = self.auth.setup_admin(token, "admin", "correct-password")
        self.assertEqual(ROLE_ADMIN, created["role"])
        user, session = self.auth.login(
            "admin",
            "correct-password",
            "127.0.0.1",
            "test",
        )
        self.assertEqual("admin", user.role)
        resolved = self.auth.resolve(session)
        self.assertEqual(user.id, resolved.id)
        self.assertTrue(resolved.is_admin)

    def test_bootstrap_admin_and_forced_password_change(self) -> None:
        created = self.auth.ensure_bootstrap_admin("admin", "admin123")
        self.assertIsNotNone(created)
        user, session = self.auth.login(
            "admin",
            "admin123",
            "127.0.0.1",
            "test",
        )
        self.assertTrue(user.must_change_password)
        self.assertFalse(user.public_dict()["can_manage"])
        self.auth.change_password(
            user.id,
            "admin123",
            "new-admin-password",
        )
        changed, _ = self.auth.login(
            "admin",
            "new-admin-password",
            "127.0.0.1",
            "test",
        )
        self.assertFalse(changed.must_change_password)
        self.assertTrue(changed.public_dict()["can_manage"])
        self.assertIsNone(
            self.auth.ensure_bootstrap_admin("admin", "another-password")
        )

    def test_user_favorites_are_isolated(self) -> None:
        admin = self.auth.create_user("admin", "correct-password", ROLE_ADMIN)
        user_a = self.auth.create_user("reader-a", "correct-password", ROLE_USER)
        user_b = self.auth.create_user("reader-b", "correct-password", ROLE_USER)
        self.auth.set_favorite(user_a["id"], "100", True)
        self.assertTrue(self.auth.is_favorite(user_a["id"], "100"))
        self.assertFalse(self.auth.is_favorite(user_b["id"], "100"))
        self.assertEqual(1, self.auth.favorite_count(user_a["id"]))
        self.assertEqual(0, self.auth.favorite_count(user_b["id"]))
        self.assertEqual(ROLE_ADMIN, admin["role"])

    def test_access_level_filters_books(self) -> None:
        for book_id, level in (("100", 0), ("200", 1), ("300", 2)):
            directory = self.root / f"{book_id}_Book"
            directory.mkdir()
            Image.new("RGB", (10, 10), "white").save(
                directory / "00001.webp",
                "WEBP",
            )
        self.library.rebuild()
        with self.library.database.connect() as connection:
            for book_id, level in (("100", 0), ("200", 1), ("300", 2)):
                connection.execute(
                    "UPDATE books SET access_level=? WHERE id=?",
                    (level, book_id),
                )
            connection.commit()
        total_guest, guest = self.library.query(0, 30, max_access_level=0)
        total_user, user = self.library.query(0, 30, max_access_level=1)
        total_admin, admin = self.library.query(0, 30, max_access_level=2)
        self.assertEqual({100}, {book.id for book in guest})
        self.assertEqual({100, 200}, {book.id for book in user})
        self.assertEqual({100, 200, 300}, {book.id for book in admin})
        self.assertEqual(1, total_guest)
        self.assertEqual(2, total_user)
        self.assertEqual(3, total_admin)

    def test_books_insert_requires_explicit_id(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            with self.library.database.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO books(title, directory, created_at)
                    VALUES ('No ID', 'no-id', '2026-01-01')
                    """
                )


if __name__ == "__main__":
    unittest.main()
