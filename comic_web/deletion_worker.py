from __future__ import annotations

import json
import os
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from .book_repository import BookRepository
    from .deletion_repository import DeletionRepository
    from .indexer import ComicLibrary
    from .storage import StorageAccessError
    from .thumbnails import ThumbnailCache
except ImportError:
    from book_repository import BookRepository
    from deletion_repository import DeletionRepository
    from indexer import ComicLibrary
    from storage import StorageAccessError
    from thumbnails import ThumbnailCache


class DeletionWorker:
    def __init__(
        self,
        repository: DeletionRepository,
        library: ComicLibrary,
        thumbnails: ThumbnailCache,
        *,
        lock_path: Optional[Path] = None,
    ) -> None:
        self.repository = repository
        self.library = library
        self.thumbnails = thumbnails
        self.books = BookRepository(library.database, library.root)
        self.lock_path = lock_path
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock_handle = None
        self.current_task_id: Optional[int] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if self.lock_path is not None:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            self._lock_handle = self.lock_path.open("a+")
            try:
                import fcntl

                fcntl.flock(
                    self._lock_handle.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
            except (OSError, ImportError) as exc:
                self._lock_handle.close()
                self._lock_handle = None
                raise RuntimeError("另一个漫画网站进程正在运行删除线程。") from exc
        recovered = self.repository.recover_running_tasks()
        if recovered:
            print(f"已恢复 {recovered} 个中断的删除任务。")
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="comic-deletion",
            daemon=False,
        )
        self._thread.start()

    def notify(self) -> None:
        self._wake.set()

    def stop(self, timeout: float = 30.0) -> bool:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            return not self._thread.is_alive()
        return True

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    task = self.repository.claim_next_task()
                except Exception as exc:
                    print(f"删除线程读取任务失败：{exc}")
                    self._wake.wait(2)
                    self._wake.clear()
                    continue
                if task is None:
                    self._wake.wait(0.75)
                    self._wake.clear()
                    continue
                self.current_task_id = task["id"]
                try:
                    self._execute(task)
                except Exception as exc:
                    self.repository.update_task(
                        task["id"],
                        status="failed",
                        stage="failed",
                        error_code="deletion_worker_error",
                        error_message=str(exc),
                    )
                finally:
                    self.current_task_id = None
        finally:
            if self._lock_handle is not None:
                try:
                    import fcntl

                    fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
                except (OSError, ImportError):
                    pass
                self._lock_handle.close()
                self._lock_handle = None

    def _execute(self, task: dict) -> None:
        if (
            task["task_type"] == "series_finalize"
            and self.repository.group_has_failures(task["batch_no"])
        ):
            self.repository.update_task(
                task["id"],
                status="waiting_dependency",
                stage="preflight",
                error_code="delete_dependency_failed",
                error_message="存在未成功的章节删除任务，暂不删除系列主体。",
            )
            return
        probe = self.repository.storage.probe(
            task["storage_id"],
            force=True,
        )
        if not probe.available:
            self.repository.update_task(
                task["id"],
                status="blocked_external",
                stage="checking_storage",
                error_code="delete_storage_offline",
                error_message=probe.error or "漫画存储不可用。",
            )
            return

        root = Path(task["storage_root"]).resolve(strict=False)
        target = (
            root / str(task["target_relative_path"])
        ).resolve(strict=False)
        try:
            target.relative_to(root)
        except ValueError as exc:
            self.repository.update_task(
                task["id"],
                status="failed",
                stage="preflight",
                error_code="delete_path_outside_storage",
                error_message="删除目标超出漫画存储根目录。",
            )
            return
        if target == root:
            self.repository.update_task(
                task["id"],
                status="failed",
                stage="preflight",
                error_code="delete_path_outside_storage",
                error_message="禁止删除存储根目录。",
            )
            return

        files, total_bytes = self._scan_target(target)
        self.repository.update_task(
            task["id"],
            stage="quarantining",
            total_files=files,
            processed_files=0,
            total_bytes=total_bytes,
        )
        quarantine = self._quarantine_path(root, task, target)
        target_existed = target.exists() or target.is_symlink()
        if target_existed:
            quarantine.parent.mkdir(parents=True, exist_ok=True)
            os.replace(target, quarantine)
            self.repository.update_task(
                task["id"],
                quarantine_path=str(quarantine),
            )

        try:
            self.repository.update_task(
                task["id"],
                stage="deleting_data",
                processed_files=files,
            )
            book = self.library.get_metadata(task["book_id"])
            if task["task_type"] == "chapter":
                self._delete_chapter_data(task, book)
            else:
                self._delete_book_data(task["book_id"])
            self.repository.update_task(task["id"], stage="updating_database")
            self.repository.sync_incremental_progress(task["book_id"])
            self.thumbnails.clear_all()
        except Exception as exc:
            if target_existed and quarantine.exists() and not target.exists():
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(quarantine, target)
                except OSError:
                    pass
            self.repository.update_task(
                task["id"],
                status="failed",
                stage="deleting_data",
                error_code="delete_data_failed",
                error_message=str(exc),
            )
            return

        cleanup_ok = True
        cleanup_error = ""
        if quarantine.exists() or quarantine.is_symlink():
            self.repository.update_task(task["id"], stage="cleaning_resource")
            try:
                if quarantine.is_symlink() or quarantine.is_file():
                    quarantine.unlink()
                else:
                    shutil.rmtree(quarantine)
            except OSError as exc:
                cleanup_ok = False
                cleanup_error = str(exc)

        if not target_existed:
            self.repository.update_task(
                task["id"],
                status="completed_with_warning",
                stage="completed",
                message="资源目录不存在，已清理数据库记录",
                error_code="delete_resource_missing",
                error_message="资源目录不存在，已清理数据库记录。",
                cleanup_required=0,
            )
        elif not cleanup_ok:
            self.repository.update_task(
                task["id"],
                status="completed_with_warning",
                stage="completed",
                message="数据已删除，隔离资源清理失败",
                error_code="delete_cleanup_failed",
                error_message=cleanup_error,
                cleanup_required=1,
            )
        else:
            self.repository.update_task(
                task["id"],
                status="completed",
                stage="completed",
                message="success",
                error_code="",
                error_message="",
                cleanup_required=0,
            )
    def _delete_chapter_data(self, task: dict, book) -> None:
        if book is None:
            raise RuntimeError("漫画记录不存在。")
        series_path = (
            Path(task["storage_root"]) / book.directory / "series.json"
        )
        previous = series_path.read_bytes() if series_path.is_file() else None
        if series_path.is_file():
            try:
                payload = json.loads(previous.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"series.json 无法解析：{exc}") from exc
            payload["chapters"] = [
                item for item in payload.get("chapters", [])
                if isinstance(item, dict)
                and str(item.get("key") or "") != task["chapter_key"]
            ]
            if payload.get("cover_file"):
                cover = str(payload["cover_file"])
                chapter_prefix = str(task["target_relative_path"])
                if chapter_prefix and cover.startswith(
                    Path(chapter_prefix).relative_to(book.directory).as_posix()
                ):
                    payload["cover_file"] = ""
            temporary = series_path.with_name("series.json.delete.tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, series_path)
        try:
            self.books.delete_chapter(
                int(task["book_id"]),
                int(task["chapter_id"]),
            )
        except Exception:
            if previous is None:
                series_path.unlink(missing_ok=True)
            else:
                temporary = series_path.with_name("series.json.restore.tmp")
                temporary.write_bytes(previous)
                os.replace(temporary, series_path)
            raise

    def _delete_book_data(self, book_id: str) -> None:
        with self.repository.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM book_chapters_fts WHERE book_id = ?",
                (book_id,),
            )
            connection.execute(
                "DELETE FROM book_chapters WHERE book_id = ?",
                (book_id,),
            )
            connection.execute(
                "DELETE FROM books_fts WHERE book_id = ?",
                (book_id,),
            )
            connection.execute(
                "DELETE FROM book_categories WHERE book_id = ?",
                (book_id,),
            )
            connection.execute(
                "DELETE FROM favorites WHERE book_id = ?",
                (book_id,),
            )
            connection.execute(
                "DELETE FROM incremental_pull_tasks WHERE comic_id = ?",
                (book_id,),
            )
            connection.execute(
                "DELETE FROM download_tasks WHERE comic_id = ?",
                (book_id,),
            )
            connection.execute(
                "DELETE FROM batch_download_tasks WHERE parent_id = ?",
                (book_id,),
            )
            connection.execute(
                "DELETE FROM migration_records WHERE book_id = ?",
                (book_id,),
            )
            connection.execute("DELETE FROM books WHERE id = ?", (book_id,))
            connection.execute(
                """
                UPDATE categories SET book_count = (
                    SELECT COUNT(*) FROM book_categories
                    WHERE book_categories.category_id = categories.id
                )
                """
            )
            connection.execute(
                "DELETE FROM categories WHERE book_count = 0 AND blocked = 0"
            )
            connection.commit()

    @staticmethod
    def _scan_target(target: Path) -> tuple[int, int]:
        if not target.exists() or target.is_symlink():
            return (1 if target.is_symlink() else 0), 0
        files = 0
        total = 0
        for root, _, names in os.walk(target):
            for name in names:
                path = Path(root) / name
                try:
                    total += path.stat().st_size
                    files += 1
                except OSError:
                    continue
        return files, total

    @staticmethod
    def _quarantine_path(root: Path, task: dict, target: Path) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_name = target.name or f"task-{task['id']}"
        return (
            root
            / ".deletion-trash"
            / stamp
            / str(task["batch_no"])
            / f"{task['id']}_{safe_name}"
        )
