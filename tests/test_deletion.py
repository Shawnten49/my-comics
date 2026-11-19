from __future__ import annotations

import json
import http.client
import tempfile
import threading
import time
import unittest
from pathlib import Path

from PIL import Image

from comic_web.app import ComicHTTPServer
from comic_web.deletion_repository import DeletionError, DeletionRepository
from comic_web.deletion_worker import DeletionWorker
from comic_web.favorites import FavoriteStore
from comic_web.indexer import ComicLibrary
from comic_web.migration_repository import MigrationRepository
from comic_web.migration_worker import MigrationWorker
from comic_web.thumbnails import ThumbnailCache


class DeletionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="comic-deletion-test-"
        )
        self.base = Path(self.temporary.name)
        self.root = self.base / "download"
        self.root.mkdir()
        self.library = ComicLibrary(
            self.root,
            database_path=self.base / "library.db",
        )
        self.series = self.root / "18427_海贼王"
        self.chapter_one = self.series / "chapters" / "0001_1卷"
        self.chapter_two = self.series / "chapters" / "0002_2卷"
        self.chapter_one.mkdir(parents=True)
        self.chapter_two.mkdir(parents=True)
        Image.new("RGB", (10, 10), "white").save(
            self.chapter_one / "00001.webp",
            "WEBP",
        )
        Image.new("RGB", (10, 10), "black").save(
            self.chapter_two / "00001.webp",
            "WEBP",
        )
        (self.series / "series.json").write_text(
            json.dumps(
                {
                    "content_type": "chaptered",
                    "book_id": "18427",
                    "title": "海贼王",
                    "raw_title": "海贼王",
                    "source_url": "https://example/18427",
                    "chapters": [
                        {
                            "key": "1",
                            "title": "1卷",
                            "order": 1,
                            "source_url": "https://example/1",
                            "directory": "chapters/0001_1卷",
                        },
                        {
                            "key": "2",
                            "title": "2卷",
                            "order": 2,
                            "source_url": "https://example/2",
                            "directory": "chapters/0002_2卷",
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.library.rebuild()
        self.repository = DeletionRepository(
            self.library.database,
            self.library,
            self.library.storage,
            verification_code="test-verification-code",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_full_series_creates_chapter_and_final_tasks(self) -> None:
        result = self.repository.create_book_tasks(
            "18427",
            "test-verification-code",
            client_key="test",
        )
        self.assertEqual(3, result["task_count"])
        tasks = self.repository.list_tasks(page=1, page_size=20)["items"]
        ordered = sorted(tasks, key=lambda item: item["task_order"])
        self.assertEqual(
            ["chapter", "chapter", "series_finalize"],
            [item["task_type"] for item in ordered],
        )
        self.assertEqual("waiting_dependency", ordered[-1]["status"])

    def test_invalid_code_is_rejected(self) -> None:
        with self.assertRaises(DeletionError) as context:
            self.repository.create_book_tasks(
                "18427",
                "wrong",
                client_key="test",
            )
        self.assertEqual(
            "invalid_verification_code",
            context.exception.code,
        )

    def test_deleting_all_selected_chapters_keeps_empty_series(self) -> None:
        _, chapters = self.library.list_chapters("18427", 0, 20)
        result = self.repository.create_chapter_tasks(
            "18427",
            [chapter.id for chapter in chapters],
            "test-verification-code",
            client_key="test",
        )
        worker = DeletionWorker(
            self.repository,
            self.library,
            ThumbnailCache(self.base / "thumbnails"),
        )
        worker.start()
        worker.notify()
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                tasks = self.repository.list_tasks(
                    page=1,
                    page_size=20,
                )["items"]
                if tasks and all(
                    task["status"] in {
                        "completed",
                        "completed_with_warning",
                    }
                    for task in tasks
                ):
                    break
                time.sleep(0.05)
            self.assertTrue(self.series.is_dir())
            self.assertFalse(self.chapter_one.exists())
            self.assertFalse(self.chapter_two.exists())
            book = self.library.get_metadata("18427")
            self.assertIsNotNone(book)
            self.assertEqual(0, book.chapter_count)
        finally:
            worker.stop(timeout=5)

    def test_full_series_worker_removes_parent_book(self) -> None:
        result = self.repository.create_book_tasks(
            "18427",
            "test-verification-code",
            client_key="test",
        )
        worker = DeletionWorker(
            self.repository,
            self.library,
            ThumbnailCache(self.base / "thumbnails"),
        )
        worker.start()
        worker.notify()
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                tasks = self.repository.list_tasks(
                    page=1,
                    page_size=20,
                )["items"]
                if tasks and all(
                    task["status"] in {
                        "completed",
                        "completed_with_warning",
                    }
                    for task in tasks
                ):
                    break
                time.sleep(0.05)
            self.assertFalse(self.series.exists())
            self.assertIsNone(self.library.get_metadata("18427"))
            self.assertGreater(len(result["task_ids"]), 0)
        finally:
            worker.stop(timeout=5)

    def test_series_delete_api_is_routed(self) -> None:
        migrations = MigrationRepository(
            self.library.database,
            self.library,
            self.library.storage,
        )
        server = ComicHTTPServer(
            ("127.0.0.1", 0),
            self.library,
            ThumbnailCache(self.base / "thumbnails"),
            FavoriteStore(self.base / "library.db"),
            migrations,
            MigrationWorker(migrations, self.library),
            deletions=self.repository,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1",
                server.server_address[1],
                timeout=5,
            )
            connection.request(
                "POST",
                "/api/series/18427/delete-tasks",
                body=json.dumps(
                    {
                        "chapter_ids": [],
                        "verification_code": "test-verification-code",
                    }
                ),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            connection.close()
            self.assertEqual(400, response.status)
            self.assertEqual("empty_deletion_plan", payload["code"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
