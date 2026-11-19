from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

try:
    from .database import Database
    from .indexer import BookEntry, ComicLibrary
    from .migration_repository import VerificationGuard
    from .settings_repository import SettingsRepository
    from .storage import StorageAccessError, StorageRegistry
except ImportError:
    from database import Database
    from indexer import BookEntry, ComicLibrary
    from migration_repository import VerificationGuard
    from settings_repository import SettingsRepository
    from storage import StorageAccessError, StorageRegistry


ACTIVE_DELETE_STATUSES = {
    "pending",
    "waiting_dependency",
    "running",
    "interrupted",
}


class DeletionError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "deletion_error",
        http_status: int = 400,
        details: Optional[dict] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status
        self.details = details or {}


class DeletionRepository:
    def __init__(
        self,
        database: Database,
        library: ComicLibrary,
        storage: StorageRegistry,
        *,
        verification_code: Optional[str] = None,
        settings: Optional[SettingsRepository] = None,
    ) -> None:
        self.database = database
        self.library = library
        self.storage = storage
        self.verification = VerificationGuard(verification_code, settings)

    def preview_delete(
        self,
        book_id: str,
        chapter_ids: Optional[List[int]] = None,
    ) -> dict:
        book = self._book(book_id)
        if chapter_ids is None:
            if book.content_type == "chaptered":
                chapters = self._all_chapters(book_id)
                count = len(chapters)
                task_count = count + 1
                scope = "full_series"
            else:
                count = 0
                task_count = 1
                scope = "single_book"
        else:
            if book.content_type != "chaptered":
                raise DeletionError(
                    "只有连载漫画支持章节删除。",
                    code="book_type_mismatch",
                )
            chapters = self._chapters_by_ids(book_id, chapter_ids)
            if len(chapters) != len(set(chapter_ids)):
                raise DeletionError(
                    "部分章节不属于该漫画。",
                    code="chapter_not_in_book",
                )
            count = len(chapters)
            task_count = count
            scope = "chapters"
        root = self.storage.get(book.storage_id)
        return {
            "book_id": book.id,
            "book_title": book.title,
            "content_type": book.content_type,
            "scope": scope,
            "chapter_count": count,
            "task_count": task_count,
            "storage_id": book.storage_id,
            "storage_path": root.root_path if root else book.storage_path,
            "book_directory": book.directory,
            "leaves_empty_series": bool(
                scope == "chapters" and count == book.chapter_count
            ),
        }

    def create_book_tasks(
        self,
        book_id: str,
        verification_code: str,
        *,
        client_key: str,
    ) -> dict:
        book = self._book(book_id)
        self._check_verification(verification_code, client_key)
        self._ensure_no_active_work(book.id)
        if book.content_type == "chaptered":
            chapters = self._all_chapters(book.id)
            plan = [
                {
                    "task_type": "chapter",
                    "chapter": row,
                    "order": index,
                }
                for index, row in enumerate(chapters, start=1)
            ]
            plan.append(
                {
                    "task_type": "series_finalize",
                    "chapter": None,
                    "order": len(chapters) + 1,
                }
            )
        else:
            plan = [
                {
                    "task_type": "single_book",
                    "chapter": None,
                    "order": 1,
                }
            ]
        return self._insert_plan(book, plan, final_waiting=True)

    def create_chapter_tasks(
        self,
        book_id: str,
        chapter_ids: List[int],
        verification_code: str,
        *,
        client_key: str,
    ) -> dict:
        book = self._book(book_id)
        if book.content_type != "chaptered":
            raise DeletionError(
                "只有连载漫画支持章节删除。",
                code="book_type_mismatch",
            )
        self._check_verification(verification_code, client_key)
        self._ensure_no_active_work(book.id)
        normalized_ids = []
        for value in chapter_ids:
            try:
                chapter_id = int(value)
            except (TypeError, ValueError) as exc:
                raise DeletionError(
                    "章节 ID 不合法。",
                    code="invalid_chapter_ids",
                ) from exc
            if chapter_id not in normalized_ids:
                normalized_ids.append(chapter_id)
        chapters = self._chapters_by_ids(book.id, normalized_ids)
        if len(chapters) != len(normalized_ids):
            raise DeletionError(
                "部分章节不属于该漫画。",
                code="chapter_not_in_book",
            )
        plan = [
            {
                "task_type": "chapter",
                "chapter": row,
                "order": index,
            }
            for index, row in enumerate(chapters, start=1)
        ]
        return self._insert_plan(book, plan, final_waiting=False)

    def list_tasks(
        self,
        *,
        page: int = 1,
        page_size: int = 30,
        search: str = "",
        status: str = "",
        task_type: str = "",
        batch_no: str = "",
    ) -> dict:
        page_size = min(200, max(1, int(page_size)))
        page = max(1, int(page))
        conditions: List[str] = []
        params: List[object] = []
        if search.strip():
            needle = f"%{search.strip().casefold()}%"
            conditions.append(
                """
                (
                    LOWER(book_id) LIKE ?
                    OR LOWER(book_title) LIKE ?
                    OR LOWER(chapter_key) LIKE ?
                    OR LOWER(chapter_title) LIKE ?
                    OR LOWER(batch_no) LIKE ?
                    OR LOWER(error_message) LIKE ?
                )
                """
            )
            params.extend([needle] * 6)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if task_type:
            conditions.append("task_type = ?")
            params.append(task_type)
        if batch_no:
            conditions.append("batch_no = ?")
            params.append(batch_no)
        where = " AND ".join(conditions) if conditions else "1 = 1"
        with self.database.connect() as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM deletion_tasks WHERE {where}",
                    tuple(params),
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"""
                SELECT * FROM deletion_tasks
                WHERE {where}
                ORDER BY created_at DESC, batch_no DESC,
                         task_order, id
                LIMIT ? OFFSET ?
                """,
                tuple(params + [page_size, (page - 1) * page_size]),
            ).fetchall()
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "items": [dict(row) for row in rows],
        }

    def get_task(self, task_id: int) -> Optional[dict]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM deletion_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        return dict(row) if row else None

    def recover_running_tasks(self) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE deletion_tasks
                SET status = 'interrupted', stage = 'interrupted',
                    error_code = 'process_interrupted',
                    error_message = '网站已停止，任务可重试',
                    updated_at = ?
                WHERE status = 'running'
                """,
                (now,),
            )
            connection.commit()
        return max(0, cursor.rowcount)

    def claim_next_task(self) -> Optional[dict]:
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM deletion_tasks AS task
                WHERE task.status IN (
                    'pending', 'waiting_dependency', 'interrupted'
                )
                  AND (
                      task.task_type <> 'series_finalize'
                      OR NOT EXISTS (
                          SELECT 1 FROM deletion_tasks AS dependency
                          WHERE dependency.batch_no = task.batch_no
                            AND dependency.task_type = 'chapter'
                            AND dependency.status NOT IN (
                                'completed', 'completed_with_warning'
                            )
                      )
                  )
                ORDER BY task.created_at, task.batch_no,
                         task.task_order, task.id
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            now = datetime.now().isoformat(timespec="seconds")
            connection.execute(
                """
                UPDATE deletion_tasks
                SET status = 'running', stage = 'preflight',
                    started_at = COALESCE(started_at, ?),
                    finished_at = NULL, error_code = '',
                    error_message = '', updated_at = ?
                WHERE id = ?
                """,
                (now, now, row["id"]),
            )
            connection.commit()
        result = dict(row)
        result.update(status="running", stage="preflight", updated_at=now)
        return result

    def update_task(
        self,
        task_id: int,
        **values,
    ) -> None:
        allowed = {
            "status",
            "stage",
            "message",
            "error_code",
            "error_message",
            "total_files",
            "processed_files",
            "total_bytes",
            "quarantine_path",
            "cleanup_required",
        }
        fields = []
        params: List[object] = []
        for key, value in values.items():
            if key not in allowed or value is None:
                continue
            fields.append(f"{key} = ?")
            params.append(value)
        now = datetime.now().isoformat(timespec="seconds")
        fields.append("updated_at = ?")
        params.append(now)
        if values.get("status") in {
            "completed",
            "completed_with_warning",
            "failed",
            "blocked_external",
        }:
            fields.append("finished_at = ?")
            params.append(now)
        params.append(task_id)
        with self.database.connect() as connection:
            connection.execute(
                f"""
                UPDATE deletion_tasks
                SET {", ".join(fields)}
                WHERE id = ?
                """,
                tuple(params),
            )
            connection.commit()

    def retry_task(self, task_id: int) -> dict:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM deletion_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            if row["status"] not in {
                "failed",
                "blocked_external",
                "interrupted",
                "completed_with_warning",
            }:
                raise DeletionError(
                    "当前删除任务状态不允许重试。",
                    code="task_not_retryable",
                    http_status=409,
                )
            status = (
                "waiting_dependency"
                if row["task_type"] == "series_finalize"
                else "pending"
            )
            now = datetime.now().isoformat(timespec="seconds")
            connection.execute(
                """
                UPDATE deletion_tasks
                SET status = ?, stage = 'queued',
                    retry_count = retry_count + 1,
                    error_code = '', error_message = '',
                    message = '', finished_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (status, now, task_id),
            )
            connection.commit()
        return {"task_id": task_id, "status": status}

    def group_has_failures(self, batch_no: str) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM deletion_tasks
                WHERE batch_no = ?
                  AND task_type = 'chapter'
                  AND status NOT IN (
                      'completed', 'completed_with_warning'
                  )
                LIMIT 1
                """,
                (batch_no,),
            ).fetchone()
        return row is not None

    def sync_incremental_progress(self, book_id: str) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.database.connect() as connection:
            incremental = connection.execute(
                """
                SELECT id FROM incremental_pull_tasks
                WHERE comic_id = ? LIMIT 1
                """,
                (book_id,),
            ).fetchone()
            if incremental is None:
                return
            current = connection.execute(
                """
                SELECT chapter_key, title, sort_order
                FROM book_chapters
                WHERE book_id = ? AND complete = 1 AND missing = 0
                ORDER BY sort_order DESC, id DESC
                LIMIT 1
                """,
                (book_id,),
            ).fetchone()
            pending = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM batch_download_tasks
                    WHERE parent_id = ? AND status IN (
                        'pending', 'running', 'interrupted'
                    )
                    """,
                    (book_id,),
                ).fetchone()[0]
            )
            connection.execute(
                """
                UPDATE incremental_pull_tasks
                SET current_chapter_key = ?,
                    current_chapter_title = ?,
                    current_chapter_order = ?,
                    pending_task_count = ?,
                    task_state = ?, last_run_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    str(current["chapter_key"]) if current else "",
                    str(current["title"]) if current else "",
                    int(current["sort_order"]) if current else 0,
                    pending,
                    "downloading" if pending else "idle",
                    now,
                    now,
                    incremental["id"],
                ),
            )
            connection.commit()

    def _insert_plan(
        self,
        book: BookEntry,
        plan: List[dict],
        *,
        final_waiting: bool,
    ) -> dict:
        if not plan:
            raise DeletionError(
                "没有可删除的目标。",
                code="empty_deletion_plan",
            )
        root = self.storage.get(book.storage_id)
        if root is None:
            raise DeletionError(
                "漫画存储根不存在。",
                code="storage_root_missing",
                http_status=409,
            )
        batch_no = (
            "DEL-"
            + datetime.now().strftime("%Y%m%d-%H%M%S")
            + "-"
            + uuid.uuid4().hex[:6].upper()
        )
        now = datetime.now().isoformat(timespec="seconds")
        task_ids = []
        try:
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                for item in plan:
                    chapter = item["chapter"]
                    task_type = item["task_type"]
                    if task_type == "chapter" and chapter is not None:
                        relative = (
                            Path(book.directory)
                            / str(chapter["directory"])
                        ).as_posix()
                    else:
                        relative = book.directory
                    target = (
                        Path(root.root_path) / relative
                    ).resolve(strict=False)
                    target.relative_to(
                        Path(root.root_path).resolve(strict=False)
                    )
                    status = (
                        "waiting_dependency"
                        if task_type == "series_finalize" and final_waiting
                        else "pending"
                    )
                    cursor = connection.execute(
                        """
                        INSERT INTO deletion_tasks(
                            batch_no, task_type, task_order,
                            book_id, book_title, content_type,
                            chapter_id, chapter_key, chapter_title,
                            storage_id, storage_root,
                            target_relative_path, target_path_snapshot,
                            status, stage, created_at, updated_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, 'queued', ?, ?
                        )
                        """,
                        (
                            batch_no,
                            task_type,
                            item["order"],
                            book.id,
                            book.title,
                            book.content_type,
                            int(chapter["id"]) if chapter is not None else None,
                            str(chapter["chapter_key"]) if chapter is not None else "",
                            str(chapter["title"]) if chapter is not None else "",
                            book.storage_id,
                            root.root_path,
                            relative,
                            str(target),
                            status,
                            now,
                            now,
                        ),
                    )
                    task_ids.append(int(cursor.lastrowid))
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise DeletionError(
                "该漫画已存在活动删除任务。",
                code="book_has_active_delete",
                http_status=409,
            ) from exc
        return {
            "batch_no": batch_no,
            "task_count": len(task_ids),
            "task_ids": task_ids,
        }

    def _ensure_no_active_work(self, book_id: str) -> None:
        with self.database.connect() as connection:
            checks = [
                (
                    "book_has_active_delete",
                    """
                    SELECT 1 FROM deletion_tasks
                    WHERE book_id = ? AND status IN (
                        'pending', 'waiting_dependency',
                        'running', 'interrupted'
                    ) LIMIT 1
                    """,
                ),
                (
                    "book_has_active_migration",
                    """
                    SELECT 1 FROM migration_records
                    WHERE book_id = ? AND status IN (
                        'queued', 'migrating', 'interrupted'
                    ) LIMIT 1
                    """,
                ),
                (
                    "book_has_active_download",
                    """
                    SELECT 1 FROM download_tasks
                    WHERE comic_id = ? AND status IN (
                        'pending', 'running', 'interrupted'
                    ) LIMIT 1
                    """,
                ),
                (
                    "book_has_active_download",
                    """
                    SELECT 1 FROM batch_download_tasks
                    WHERE parent_id = ? AND status IN (
                        'pending', 'running', 'interrupted'
                    ) LIMIT 1
                    """,
                ),
            ]
            for code, sql in checks:
                if connection.execute(sql, (book_id,)).fetchone():
                    raise DeletionError(
                        "该漫画存在活动迁移、下载或删除任务。",
                        code=code,
                        http_status=409,
                    )

    def _check_verification(self, code: str, client_key: str) -> None:
        try:
            self.verification.check(code, client_key)
        except Exception as exc:
            raise DeletionError(
                getattr(exc, "message", str(exc)),
                code=getattr(exc, "code", "invalid_verification_code"),
                http_status=getattr(exc, "http_status", 401),
                details=getattr(exc, "details", {}),
            ) from exc

    def _book(self, book_id: str) -> BookEntry:
        book = self.library.get_metadata(str(book_id))
        if book is None:
            raise DeletionError(
                "漫画不存在。",
                code="book_not_found",
                http_status=404,
            )
        return book

    def _all_chapters(self, book_id: str) -> List[sqlite3.Row]:
        with self.database.connect() as connection:
            return connection.execute(
                """
                SELECT * FROM book_chapters
                WHERE book_id = ?
                ORDER BY sort_order, id
                """,
                (book_id,),
            ).fetchall()

    def _chapters_by_ids(
        self,
        book_id: str,
        chapter_ids: List[int],
    ) -> List[sqlite3.Row]:
        if not chapter_ids:
            return []
        placeholders = ",".join("?" for _ in chapter_ids)
        with self.database.connect() as connection:
            return connection.execute(
                f"""
                SELECT * FROM book_chapters
                WHERE book_id = ? AND id IN ({placeholders})
                ORDER BY sort_order, id
                """,
                (book_id, *chapter_ids),
            ).fetchall()
