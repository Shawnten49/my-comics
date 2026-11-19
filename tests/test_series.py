from __future__ import annotations

import json
import http.client
import tempfile
import threading
import unittest
from pathlib import Path

from PIL import Image

from comic_web.app import ComicHTTPServer
from comic_web.favorites import FavoriteStore
from comic_web.indexer import ComicLibrary
from comic_web.migration_repository import MigrationRepository
from comic_web.migration_worker import MigrationWorker
from comic_web.thumbnails import ThumbnailCache


class SeriesChapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="comic-series-test-"
        )
        base = Path(self.temporary.name)
        self.root = base / "download"
        self.root.mkdir()
        self.library = ComicLibrary(
            self.root,
            database_path=base / "library.db",
        )
        self.series_dir = self.root / "18427_海贼王"
        self.chapter_one = self.series_dir / "chapters" / "0001_1卷"
        self.chapter_two = self.series_dir / "chapters" / "0002_2卷"
        self.chapter_one.mkdir(parents=True)
        self.chapter_two.mkdir(parents=True)
        Image.new("RGB", (20, 30), "white").save(
            self.chapter_one / "00001.webp",
            "WEBP",
        )
        Image.new("RGB", (20, 30), "black").save(
            self.chapter_two / "00001.webp",
            "WEBP",
        )
        (self.series_dir / "series.json").write_text(
            json.dumps(
                {
                    "content_type": "chaptered",
                    "book_id": "18427",
                    "title": "海贼王",
                    "raw_title": "海贼王",
                    "source_url": "https://www.xiaoaimanhua.com/18427",
                    "chapters": [
                        {
                            "key": "page-1",
                            "title": "1卷",
                            "kind": "volume",
                            "order": 1,
                            "source_url": "https://example/1.html",
                            "directory": "chapters/0001_1卷",
                        },
                        {
                            "key": "page-2",
                            "title": "2卷",
                            "kind": "volume",
                            "order": 2,
                            "source_url": "https://example/2.html",
                            "directory": "chapters/0002_2卷",
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_series_creates_one_book_and_chapters(self) -> None:
        self.library.rebuild()
        books = self.library.books()
        self.assertEqual(1, len(books))
        book = books[0]
        self.assertEqual(18427, book.id)
        self.assertEqual("chaptered", book.content_type)
        self.assertEqual(2, book.chapter_count)
        self.assertEqual(2, book.page_count)
        self.assertEqual("chaptered", book.public_dict()["content_type"])
        total, chapters = self.library.list_chapters(
            "18427",
            0,
            50,
        )
        self.assertEqual(2, total)
        self.assertEqual(["1卷", "2卷"], [item.title for item in chapters])
        self.assertTrue(all(item.id is not None for item in chapters))
        self.assertTrue(self.library.cover_path(book).is_file())
        self.assertEqual(
            (self.chapter_one / "00001.webp").resolve(),
            self.library.chapter_page_path(
                book,
                chapters[0],
                "00001.webp",
            ).resolve(),
        )

    def test_chapter_search_and_neighbors(self) -> None:
        self.library.rebuild()
        total, chapters = self.library.list_chapters(
            "18427",
            0,
            50,
            search="2卷",
        )
        self.assertEqual(1, total)
        self.assertEqual("2卷", chapters[0].title)
        all_chapters = self.library.list_chapters("18427", 0, 20)[1]
        previous, following = self.library.neighboring_chapters(
            all_chapters[1]
        )
        self.assertEqual("1卷", previous.title)
        self.assertIsNone(following)

    def test_series_and_chapter_api(self) -> None:
        self.library.rebuild()
        migrations = MigrationRepository(
            self.library.database,
            self.library,
            self.library.storage,
        )
        migration_worker = MigrationWorker(migrations, self.library)
        cache = ThumbnailCache(Path(self.temporary.name) / "cache")
        favorites = FavoriteStore(
            Path(self.temporary.name) / "library.db"
        )
        server = ComicHTTPServer(
            ("127.0.0.1", 0),
            self.library,
            cache,
            favorites,
            migrations,
            migration_worker,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1",
                port,
                timeout=5,
            )
            connection.request("GET", "/api/series/18427")
            response = connection.getresponse()
            series = json.loads(response.read().decode("utf-8"))
            self.assertEqual(200, response.status)
            self.assertEqual("chaptered", series["content_type"])
            self.assertEqual(2, series["chapter_count"])
            connection.close()

            connection = http.client.HTTPConnection(
                "127.0.0.1",
                port,
                timeout=5,
            )
            connection.request(
                "GET",
                "/api/series/18427/chapters?page=1&page_size=1",
            )
            response = connection.getresponse()
            chapters = json.loads(response.read().decode("utf-8"))
            self.assertEqual(200, response.status)
            self.assertEqual(2, chapters["total"])
            self.assertEqual(1, len(chapters["items"]))
            chapter_id = chapters["items"][0]["id"]
            connection.close()

            connection = http.client.HTTPConnection(
                "127.0.0.1",
                port,
                timeout=5,
            )
            connection.request(
                "GET",
                f"/api/books/18427/chapters/{chapter_id}/pages"
                "?offset=0&limit=1",
            )
            response = connection.getresponse()
            pages = json.loads(response.read().decode("utf-8"))
            self.assertEqual(200, response.status)
            self.assertEqual(1, pages["total"])
            self.assertEqual(1, len(pages["pages"]))
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
