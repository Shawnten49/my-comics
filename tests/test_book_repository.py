from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from comic_web.book_repository import BookRepository
from comic_web.indexer import ComicLibrary
from comic_web.thumbnails import ThumbnailCache


class BookRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="comic-book-repository-"
        )
        self.base = Path(self.temporary.name)
        self.root = self.base / "library"
        self.root.mkdir()
        self.library = ComicLibrary(
            self.root,
            database_path=self.base / "library.db",
        )
        self.books = BookRepository(self.library.database, self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_imports_book_directly_from_download_directory(self) -> None:
        directory = self.root / "90001_测试漫画"
        directory.mkdir()
        Image.new("RGB", (20, 30), "white").save(
            directory / "00001.webp",
            "WEBP",
        )
        (directory / "manifest.json").write_text(
            json.dumps(
                {
                    "title": "[分类] 测试漫画",
                    "source_url": "https://example.invalid/photo/90001",
                    "image_count": 1,
                    "downloaded": 1,
                    "skipped": 0,
                    "failed": 0,
                    "render_mode": "original",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (directory / "metadata.json").write_text(
            json.dumps(
                {
                    "title": "测试漫画",
                    "raw_title": "[分类] 测试漫画",
                    "source_url": "https://example.invalid/photo/90001",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        result = self.books.import_single_book(90001, directory)
        self.assertTrue(result["complete"])
        book = self.library.get_metadata(90001)
        self.assertIsNotNone(book)
        self.assertEqual("测试漫画", book.title)
        self.assertEqual(1, book.page_count)
        self.assertIn("分类", book.categories)
        total, matches = self.library.query(
            0,
            30,
            "测试漫画",
            max_access_level=2,
        )
        self.assertEqual(1, total)
        self.assertEqual(90001, matches[0].id)

    def test_thumbnail_cache_is_lazy(self) -> None:
        cache = ThumbnailCache(self.root / ".cache" / "thumbnails")
        self.assertFalse(cache.cache_dir.exists())
        source = self.root / "source.webp"
        Image.new("RGB", (20, 30), "orange").save(source, "WEBP")
        thumbnail = cache.warm(source)
        self.assertTrue(cache.cache_dir.is_dir())
        self.assertTrue(thumbnail.is_file())


if __name__ == "__main__":
    unittest.main()
