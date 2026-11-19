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
from comic_web.batch_download_repository import BatchDownloadRepository
from comic_web.batch_download_source import (
    BatchChapterPreview,
    XiaoaimanhuaSource,
)
from comic_web.batch_download_worker import BatchDownloadWorker
from comic_web.database import Database
from comic_web.download_repository import DownloadError
from comic_web.favorites import FavoriteStore
from comic_web.incremental_repository import IncrementalPullRepository
from comic_web.indexer import ComicLibrary
from comic_web.migration_repository import MigrationRepository
from comic_web.migration_worker import MigrationWorker
from comic_web.thumbnails import ThumbnailCache


class FakeSource:
    def __init__(self, chapters):
        self.chapters = list(chapters)
        self.calls = 0

    def resolve_series(self, parent_id, series_url):
        self.calls += 1
        return list(self.chapters)


class BatchDownloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="comic-batch-test-"
        )
        self.base = Path(self.temporary.name)
        self.root = self.base / "download"
        self.root.mkdir()
        self.library = ComicLibrary(
            self.root,
            database_path=self.base / "library.db",
        )
        self.library.rebuild()
        self.chapters = [
            BatchChapterPreview(
                chapter_key="1",
                chapter_title="1卷",
                sort_order=1,
                download_url="https://www.xiaoaimanhua.com/18427/1.html",
            ),
            BatchChapterPreview(
                chapter_key="2",
                chapter_title="2卷",
                sort_order=2,
                download_url="https://www.xiaoaimanhua.com/18427/2.html",
            ),
            BatchChapterPreview(
                chapter_key="3",
                chapter_title="3卷",
                sort_order=3,
                download_url="https://www.xiaoaimanhua.com/18427/3.html",
            ),
        ]

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def repository(self, source=None):
        return BatchDownloadRepository(
            self.library.database,
            self.library,
            output_root=self.root,
            project_root=self.base,
            source=source or FakeSource(self.chapters),
        )

    def incremental_repository(self, batch_repository, source=None):
        if source is not None:
            batch_repository.source = source
        return IncrementalPullRepository(
            self.library.database,
            self.library,
            batch_repository,
        )

    def test_parse_html_deduplicates_chapter_links(self) -> None:
        html = """
        <a href="/18427/403.html">403话</a>
        <a href="/18427/1.html" class="comic-chapter-link">1卷</a>
        <a href="/18427/401.html">401话</a>
        <a href="/18427/1.html">重复的1卷</a>
        <a href="https://example.com/18427/2.html">错误域名</a>
        <a href="/18427/2.html">2卷</a>
        """
        items = XiaoaimanhuaSource.parse_html(
            "18427",
            "https://www.xiaoaimanhua.com/18427",
            html,
        )
        self.assertEqual(
            ["1", "2", "401", "403"],
            [item.chapter_key for item in items],
        )
        self.assertEqual("1卷", items[0].chapter_title)

    def test_download_management_has_single_and_batch_tabs(self) -> None:
        html = (
            Path(__file__).parent.parent
            / "comic_web"
            / "static"
            / "manage.html"
        ).read_text(encoding="utf-8")
        self.assertIn('id="downloadTabs"', html)
        self.assertIn('id="singleDownloadsPanel"', html)
        self.assertIn('id="batchTasksPanel"', html)
        self.assertIn('id="incrementalPullPanel"', html)
        self.assertIn('id="incrementalDialog"', html)
        self.assertIn('href="/manage/downloads/batch"', html)
        self.assertIn('href="/manage/downloads/batch/new"', html)
        self.assertIn('href="/manage/downloads/incremental"', html)
        self.assertIn('id="downloadAccessLevel"', html)
        self.assertIn('id="batchAccessLevel"', html)

    def test_preview_pages_do_not_refetch_source(self) -> None:
        source = FakeSource(self.chapters)
        repository = self.repository(source)
        preview = repository.create_analysis(
            "18427",
            "海贼王",
            "https://www.xiaoaimanhua.com/18427",
            access_level=1,
        )
        self.assertEqual(3, preview["total"])
        self.assertEqual(["1", "2", "3"], preview["default_selected_keys"])
        second = repository.get_preview(
            preview["analysis_id"],
            page=2,
            page_size=1,
        )
        self.assertEqual(1, len(second["items"]))
        self.assertEqual("2", second["items"][0]["chapter_key"])
        self.assertEqual(1, source.calls)

    def test_create_tasks_uses_one_batch_and_auto_ids(self) -> None:
        repository = self.repository()
        preview = repository.create_analysis(
            "18427",
            "海贼王",
            "https://www.xiaoaimanhua.com/18427",
            access_level=1,
        )
        result = repository.create_tasks(
            preview["analysis_id"],
            ["1", "2", "3"],
        )
        self.assertEqual(3, result["count"])
        self.assertEqual([1, 2, 3], result["task_ids"])
        tasks = repository.list_tasks(page=1, page_size=20)
        self.assertEqual(3, tasks["total"])
        self.assertEqual(
            {result["batch_no"]},
            {item["batch_no"] for item in tasks["items"]},
        )
        self.assertIsNone(self.library.get_metadata("18427"))
        incremental = self.incremental_repository(repository).list_tasks(
            page=1,
            page_size=30,
        )
        self.assertEqual(1, incremental["total"])
        item = incremental["items"][0]
        self.assertEqual(18427, item["comic_id"])
        self.assertEqual(0, item["comic_status"])
        self.assertEqual("3", item["latest_known_chapter_key"])
        self.assertEqual(3, item["pending_task_count"])

    def test_incremental_preview_finds_only_new_chapters(self) -> None:
        source = FakeSource(self.chapters)
        repository = self.repository(source)
        preview = repository.create_analysis(
            "18427",
            "海贼王",
            "https://www.xiaoaimanhua.com/18427",
        )
        created = repository.create_tasks(
            preview["analysis_id"],
            ["1", "2", "3"],
        )
        for task_id in created["task_ids"]:
            repository.update_task(
                task_id,
                status="completed",
                stage="completed",
                finished=True,
            )
        with self.library.database.connect() as connection:
            now = "2026-01-01T00:00:00"
            connection.execute(
                """
                INSERT INTO books(
                    id, title, raw_title, directory, source_url,
                    page_count, created_at, content_type
                ) VALUES (18427, '海贼王', '海贼王', '18427_海贼王',
                          'https://www.xiaoaimanhua.com/18427', 3, ?,
                          'chaptered')
                """,
                (now,),
            )
            for key in ("1", "2", "3"):
                connection.execute(
                    """
                    INSERT INTO book_chapters(
                        book_id, chapter_key, title, raw_title,
                        sort_order, directory, page_count, complete,
                        created_at
                    ) VALUES (18427, ?, ?, ?, ?, ?, 1, 1, ?)
                    """,
                    (
                        key,
                        f"{key}卷",
                        f"{key}卷",
                        int(key),
                        f"chapters/000{key}_{key}卷",
                        now,
                    ),
                )
            connection.commit()
        incremental_repository = self.incremental_repository(
            repository,
            source,
        )
        incremental = incremental_repository.list_tasks(
            page=1,
            page_size=30,
        )["items"][0]
        no_new = incremental_repository.preview_new_chapters(
            incremental["id"]
        )
        self.assertTrue(no_new["no_new_chapters"])
        self.assertEqual(0, no_new["new_count"])

        with self.library.database.connect() as connection:
            connection.execute(
                """
                DELETE FROM book_chapters
                WHERE book_id = '18427' AND chapter_key IN ('2', '3')
                """
            )
            connection.commit()
        deleted_last = incremental_repository.preview_new_chapters(
            incremental["id"]
        )
        self.assertEqual(
            {"2", "3"},
            {item["chapter_key"] for item in deleted_last["items"]},
        )

        source.chapters.append(
            BatchChapterPreview(
                chapter_key="4",
                chapter_title="4卷",
                sort_order=4,
                download_url="https://www.xiaoaimanhua.com/18427/4.html",
            )
        )
        found = incremental_repository.preview_new_chapters(
            incremental["id"]
        )
        self.assertFalse(found["no_new_chapters"])
        self.assertEqual(3, found["new_count"])
        self.assertEqual(
            {"2", "3", "4"},
            {item["chapter_key"] for item in found["items"]},
        )
        self.assertTrue(found["analysis_id"].startswith("ba_"))

    def test_completed_comic_cannot_be_pulled(self) -> None:
        repository = self.repository()
        preview = repository.create_analysis(
            "18427",
            "海贼王",
            "https://www.xiaoaimanhua.com/18427",
        )
        repository.create_tasks(preview["analysis_id"], ["1"])
        incremental_repository = self.incremental_repository(repository)
        incremental = incremental_repository.list_tasks(
            page=1,
            page_size=30,
        )["items"][0]
        incremental_repository.set_comic_status(incremental["id"], 1)
        with self.assertRaises(DownloadError) as context:
            incremental_repository.preview_new_chapters(
                incremental["id"]
            )
        self.assertEqual("comic_already_completed", context.exception.code)

    def test_existing_book_is_rejected_by_initial_batch(self) -> None:
        with self.library.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO books(id, title, directory, created_at)
                VALUES (18427, '海贼王', '18427_海贼王',
                        '2026-01-01T00:00:00')
                """
            )
            connection.commit()
        repository = self.repository()
        with self.assertRaises(DownloadError) as context:
            repository.create_analysis(
                "18427",
                "海贼王",
                "https://www.xiaoaimanhua.com/18427",
            )
        self.assertEqual("comic_already_exists", context.exception.code)

    def test_active_chapter_conflict_rolls_back_new_batch(self) -> None:
        repository = self.repository()
        first = repository.create_analysis(
            "18427",
            "海贼王",
            "https://www.xiaoaimanhua.com/18427",
        )
        created = repository.create_tasks(first["analysis_id"], ["1"])
        second = repository.create_analysis(
            "18427",
            "海贼王",
            "https://www.xiaoaimanhua.com/18427",
        )
        with self.assertRaises(DownloadError) as context:
            repository.create_tasks(second["analysis_id"], ["1", "2"])
        self.assertEqual("chapter_already_active", context.exception.code)
        tasks = repository.list_tasks(page=1, page_size=20)
        self.assertEqual(1, tasks["total"])
        self.assertEqual("1", tasks["items"][0]["chapter_key"])
        self.assertEqual(created["batch_no"], tasks["items"][0]["batch_no"])

    def test_failed_chapter_retry_reuses_same_id(self) -> None:
        repository = self.repository()
        preview = repository.create_analysis(
            "18427",
            "海贼王",
            "https://www.xiaoaimanhua.com/18427",
        )
        created = repository.create_tasks(preview["analysis_id"], ["1"])
        task_id = created["task_ids"][0]
        repository.update_task(
            task_id,
            status="failed",
            stage="failed",
            error_message="部分图片失败",
            finished=True,
        )
        retried = repository.retry_task(task_id)
        self.assertEqual(task_id, retried["task_id"])
        task = repository.get_task(task_id)
        self.assertEqual("pending", task["status"])
        self.assertEqual(1, task["attempt_count"])

    def test_completed_chapter_can_be_requeued_for_recrawl(self) -> None:
        repository = self.repository()
        preview = repository.create_analysis(
            "18427",
            "海贼王",
            "https://www.xiaoaimanhua.com/18427",
        )
        created = repository.create_tasks(preview["analysis_id"], ["1"])
        task_id = created["task_ids"][0]
        repository.update_task(
            task_id,
            status="completed",
            stage="completed",
            total_images=5,
            processed_images=5,
            downloaded_images=5,
            finished=True,
        )
        result = repository.recrawl_task(task_id)
        self.assertEqual("recrawl", result["pull_mode"])
        task = repository.get_task(task_id)
        self.assertEqual("pending", task["status"])
        self.assertEqual("recrawl", task["pull_mode"])
        self.assertEqual(1, task["recrawl_count"])

    def test_worker_completes_chapter_and_updates_book(self) -> None:
        repository = self.repository()
        preview = repository.create_analysis(
            "18427",
            "海贼王",
            "https://www.xiaoaimanhua.com/18427",
            access_level=1,
        )
        created = repository.create_tasks(preview["analysis_id"], ["1"])

        def fake_runner(task, on_line, stop_event):
            output = Path(task["output_dir"])
            output.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (20, 30), "white").save(
                output / "00001.webp",
                "WEBP",
            )
            manifest = {
                "source_url": task["download_url"],
                "title": task["chapter_title"],
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
                        "status": "success",
                        "output_dir": str(output),
                        "title": task["chapter_title"],
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

        worker = BatchDownloadWorker(
            repository,
            self.library,
            project_root=self.base,
            crawler_runner=fake_runner,
            cdp_ensurer=lambda cdp_url: False,
        )
        worker.start()
        worker.notify()
        try:
            deadline = time.monotonic() + 10
            task = None
            while time.monotonic() < deadline:
                task = repository.get_task(created["task_ids"][0])
                if task and task["status"] == "completed":
                    break
                time.sleep(0.05)
            self.assertIsNotNone(task)
            self.assertEqual("completed", task["status"])
            book = self.library.get_metadata("18427")
            self.assertIsNotNone(book)
            self.assertEqual(1, book.page_count)
            self.assertEqual(1, book.chapter_complete_count)
            self.assertEqual(1, book.access_level)
            total, chapters = self.library.list_chapters("18427", 0, 20)
            self.assertEqual(1, total)
            self.assertTrue(chapters[0].complete)
            incremental = self.incremental_repository(
                repository
            ).list_tasks(page=1, page_size=30)["items"][0]
            self.assertEqual("1", incremental["current_chapter_key"])
            self.assertEqual(0, incremental["pending_task_count"])
            self.assertEqual("idle", incremental["task_state"])
        finally:
            worker.stop(timeout=5)

    def test_batch_analysis_and_task_api(self) -> None:
        repository = self.repository()
        migrations = MigrationRepository(
            self.library.database,
            self.library,
            self.library.storage,
        )
        migration_worker = MigrationWorker(migrations, self.library)
        incremental_repository = self.incremental_repository(repository)
        server = ComicHTTPServer(
            ("127.0.0.1", 0),
            self.library,
            ThumbnailCache(self.base / "thumbnails"),
            FavoriteStore(self.base / "library.db"),
            migrations,
            migration_worker,
            batch_downloads=repository,
            incremental_pulls=incremental_repository,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1",
                server.server_address[1],
                timeout=5,
            )
            body = json.dumps(
                {
                    "parent_id": "18427",
                    "comic_name": "海贼王",
                    "series_url": "https://www.xiaoaimanhua.com/18427",
                }
            )
            connection.request(
                "POST",
                "/api/manage/downloads/batch/analyze",
                body=body,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            preview = json.loads(response.read().decode("utf-8"))
            self.assertEqual(200, response.status)
            self.assertEqual(3, preview["total"])
            connection.close()

            connection = http.client.HTTPConnection(
                "127.0.0.1",
                server.server_address[1],
                timeout=5,
            )
            body = json.dumps(
                {
                    "analysis_id": preview["analysis_id"],
                    "chapter_keys": ["1", "2"],
                }
            )
            connection.request(
                "POST",
                "/api/manage/downloads/batch",
                body=body,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            created = json.loads(response.read().decode("utf-8"))
            self.assertEqual(200, response.status)
            self.assertEqual(2, created["count"])
            connection.close()

            connection = http.client.HTTPConnection(
                "127.0.0.1",
                server.server_address[1],
                timeout=5,
            )
            connection.request(
                "GET",
                "/api/manage/downloads/batch/tasks"
                f"?q={created['batch_no']}&page=1&page_size=30",
            )
            response = connection.getresponse()
            tasks = json.loads(response.read().decode("utf-8"))
            self.assertEqual(200, response.status)
            self.assertEqual(2, tasks["total"])
            self.assertEqual(
                {"1", "2"},
                {item["chapter_key"] for item in tasks["items"]},
            )
            connection.close()

            connection = http.client.HTTPConnection(
                "127.0.0.1",
                server.server_address[1],
                timeout=5,
            )
            connection.request(
                "GET",
                "/api/manage/downloads/incremental?page=1&page_size=30",
            )
            response = connection.getresponse()
            incremental = json.loads(response.read().decode("utf-8"))
            self.assertEqual(200, response.status)
            self.assertEqual(1, incremental["total"])
            self.assertEqual(
                18427,
                incremental["items"][0]["comic_id"],
            )
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
