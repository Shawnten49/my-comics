from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image

from comic_web.database import Database
from comic_web.download_repository import DownloadError, DownloadRepository
from comic_web.download_worker import DownloadWorker
from comic_web.indexer import ComicLibrary
from image_crawler import (
    encode_rendered_capture,
    rendered_capture_blank_signature,
    resume_file_for_order,
    safe_filename,
)


class DownloadManagementTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="comic-download-test-"
        )
        base = Path(self.temporary.name)
        self.root = base / "download"
        self.root.mkdir()
        self.database_path = base / "library.db"
        self.library = ComicLibrary(
            self.root,
            database_path=self.database_path,
        )
        self.library.rebuild()
        self.repository = DownloadRepository(
            self.library.database,
            self.library,
            output_root=self.root,
            project_root=base,
        )
        self.worker = DownloadWorker(
            self.repository,
            self.library,
            project_root=base,
        )

    def tearDown(self) -> None:
        self.worker.stop(timeout=5)
        self.temporary.cleanup()

    def test_task_id_auto_increments_and_duplicate_is_rejected(self) -> None:
        first = self.repository.create_task(
            "101",
            "https://18comic.vip/photo/101",
        )
        second = self.repository.create_task(
            "102",
            "https://18comic.vip/photo/102",
        )
        self.assertEqual(1, first["task_id"])
        self.assertEqual(2, second["task_id"])
        with self.assertRaises(DownloadError) as context:
            self.repository.create_task(
                "101",
                "https://18comic.vip/photo/101",
            )
        self.assertEqual("active_task_exists", context.exception.code)
        total, tasks = self.repository.list_tasks(0, 30, search="101")
        self.assertEqual(1, total)
        self.assertEqual(101, tasks[0]["comic_id"])

    def test_url_id_mismatch_is_rejected(self) -> None:
        with self.assertRaises(DownloadError) as context:
            self.repository.create_task(
                "101",
                "https://18comic.vip/photo/102",
            )
        self.assertEqual("comic_url_mismatch", context.exception.code)

    def test_existing_books_id_is_rejected_even_when_incomplete(self) -> None:
        book_dir = self.root / "301_Existing Comic"
        book_dir.mkdir()
        Image.new("RGB", (12, 12), "white").save(
            book_dir / "00001.webp",
            "WEBP",
        )
        self.library.rebuild()
        self.assertIsNotNone(self.library.get_metadata("301"))
        with self.assertRaises(DownloadError) as context:
            self.repository.create_task(
                "301",
                "https://18comic.vip/photo/301",
            )
        self.assertEqual("comic_already_exists", context.exception.code)
        self.assertIn("books", context.exception.message)

    def test_failed_task_retry_reuses_same_id(self) -> None:
        created = self.repository.create_task(
            "101",
            "https://18comic.vip/photo/101",
        )
        self.repository.update_task(
            created["task_id"],
            status="failed",
            stage="failed",
            error_message="部分图片失败",
            finished=True,
        )
        retried = self.repository.retry_task(created["task_id"])
        self.assertEqual(created["task_id"], retried["task_id"])
        task = self.repository.get_task(created["task_id"])
        self.assertEqual("pending", task["status"])
        self.assertEqual(1, task["attempt_count"])
        self.assertIsNone(task["finished_at"])
        self.assertEqual(1, self.repository.list_tasks(0, 30)[0])

    def test_completed_task_can_be_requeued_for_recrawl(self) -> None:
        created = self.repository.create_task(
            "101",
            "https://18comic.vip/photo/101",
        )
        self.repository.update_task(
            created["task_id"],
            status="completed",
            stage="completed",
            total_images=10,
            processed_images=10,
            downloaded_images=10,
            finished=True,
        )
        result = self.repository.recrawl_task(created["task_id"])
        self.assertEqual("recrawl", result["pull_mode"])
        task = self.repository.get_task(created["task_id"])
        self.assertEqual("pending", task["status"])
        self.assertEqual("recrawl", task["pull_mode"])
        self.assertEqual(1, task["recrawl_count"])
        self.assertEqual(0, task["processed_images"])

    def test_resume_uses_previous_manifest_file(self) -> None:
        output = self.root / "101_Test"
        output.mkdir()
        image_path = output / "00001.webp"
        Image.new("RGB", (12, 12), "white").save(image_path, "WEBP")
        manifest = {
            "items": [
                {
                    "order": 1,
                    "file": "00001.webp",
                    "status": "downloaded",
                }
            ]
        }
        resolved = resume_file_for_order(
            output,
            manifest,
            1,
            use_rendered=False,
            render_format="webp",
        )
        self.assertEqual(image_path.resolve(), resolved)

    def test_blob_image_uses_stable_webp_filename(self) -> None:
        first = safe_filename(
            "blob:https://example.com/9655b604-86f2-4c67-8d9d-8fb4ce1c7149",
            1,
            set(),
        )
        second = safe_filename(
            "blob:https://example.com/different-uuid",
            2,
            set(),
        )
        self.assertTrue(first.startswith("00001_blob_"))
        self.assertTrue(first.endswith(".webp"))
        self.assertTrue(second.startswith("00002_blob_"))
        self.assertNotEqual(first, second)

    def test_stable_blank_rendered_page_can_be_encoded(self) -> None:
        capture = Path(self.temporary.name) / "blank.capture.png"
        output = Path(self.temporary.name) / "blank.webp"
        Image.new("RGB", (128, 192), "white").save(capture, "PNG")
        signature = rendered_capture_blank_signature(
            capture,
            native_size=(128, 192),
        )
        self.assertIsNotNone(signature)
        body = encode_rendered_capture(
            capture,
            output,
            native_size=(128, 192),
            render_format="webp",
            render_quality=100,
            allow_blank=True,
        )
        self.assertTrue(body.startswith(b"RIFF"))
        self.assertTrue(output.is_file())

    def test_worker_imports_completed_download_into_books(self) -> None:
        base = Path(self.temporary.name)
        output = self.root / "201_Worker Test"

        def fake_runner(task, on_line, stop_event):
            output.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (12, 12), "white").save(
                output / "00001.webp",
                "WEBP",
            )
            (output / ".download-task.json").write_text(
                json.dumps({"task_id": task["id"], "status": "success"}),
                encoding="utf-8",
            )
            manifest = {
                "source_url": task["download_url"],
                "title": "Worker Test",
                "image_count": 1,
                "downloaded": 1,
                "skipped": 0,
                "failed": 0,
                "items": [
                    {
                        "order": 1,
                        "file": "00001.webp",
                        "status": "downloaded",
                    }
                ],
            }
            (output / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False),
                encoding="utf-8",
            )
            result_file = Path(task["result_file"])
            result_file.parent.mkdir(parents=True, exist_ok=True)
            result_file.write_text(
                json.dumps(
                    {
                        "url": task["download_url"],
                        "page_id": task["comic_id"],
                        "title": "Worker Test",
                        "folder_name": output.name,
                        "output_dir": str(output),
                        "status": "success",
                        "downloaded": 1,
                        "skipped": 0,
                        "failed": 0,
                        "error": "",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            on_line("[1/1] 完成: 00001.webp\n")
            return 0

        runner = DownloadWorker(
            self.repository,
            self.library,
            project_root=base,
            crawler_runner=fake_runner,
        )
        created = self.repository.create_task(
            "201",
            "https://18comic.vip/photo/201",
            access_level=2,
        )
        runner.start()
        runner.notify()
        try:
            deadline = time.monotonic() + 10
            task = None
            while time.monotonic() < deadline:
                task = self.repository.get_task(created["task_id"])
                if task and task["status"] == "completed":
                    break
                time.sleep(0.05)
            self.assertIsNotNone(task)
            self.assertEqual("completed", task["status"])
            book = self.library.get_metadata("201")
            self.assertIsNotNone(book)
            self.assertEqual(1, book.page_count)
            self.assertEqual(2, book.access_level)
            self.assertFalse((output / ".download-task.json").exists())
        finally:
            runner.stop(timeout=5)

    def test_recrawl_replaces_whole_directory(self) -> None:
        base = Path(self.temporary.name)
        output = self.root / "401_Recrawl Test"
        output.mkdir()
        Image.new("RGB", (12, 12), "black").save(
            output / "stale.webp",
            "WEBP",
        )
        created = self.repository.create_task(
            "401",
            "https://18comic.vip/photo/401",
        )
        self.repository.update_task(
            created["task_id"],
            status="completed",
            stage="completed",
            output_dir=str(output),
            finished=True,
        )

        def fake_runner(task, on_line, stop_event):
            staging = Path(task["output_dir"])
            self.assertNotEqual(output.resolve(), staging.resolve())
            staging.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (12, 12), "white").save(
                staging / "00001.webp",
                "WEBP",
            )
            (staging / "manifest.json").write_text(
                json.dumps(
                    {
                        "source_url": task["download_url"],
                        "title": "Recrawl Test",
                        "image_count": 1,
                        "downloaded": 1,
                        "skipped": 0,
                        "failed": 0,
                        "items": [
                            {
                                "order": 1,
                                "file": "00001.webp",
                                "status": "downloaded",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result_file = Path(task["result_file"])
            result_file.parent.mkdir(parents=True, exist_ok=True)
            result_file.write_text(
                json.dumps(
                    {
                        "status": "success",
                        "title": "Recrawl Test",
                        "output_dir": str(staging),
                        "downloaded": 1,
                        "skipped": 0,
                        "failed": 0,
                        "error": "",
                    }
                ),
                encoding="utf-8",
            )
            return 0

        worker = DownloadWorker(
            self.repository,
            self.library,
            project_root=base,
            crawler_runner=fake_runner,
        )
        self.repository.recrawl_task(created["task_id"])
        worker.start()
        worker.notify()
        try:
            deadline = time.monotonic() + 10
            task = None
            while time.monotonic() < deadline:
                task = self.repository.get_task(created["task_id"])
                if task and task["status"] == "completed":
                    break
                time.sleep(0.05)
            self.assertEqual("completed", task["status"])
            self.assertFalse((output / "stale.webp").exists())
            self.assertTrue((output / "00001.webp").is_file())
        finally:
            worker.stop(timeout=5)


if __name__ == "__main__":
    unittest.main()
