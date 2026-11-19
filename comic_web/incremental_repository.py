from __future__ import annotations

from datetime import datetime
from typing import List, Optional

try:
    from .batch_download_repository import BatchDownloadRepository
    from .batch_download_source import BatchSourceError
    from .database import Database
    from .download_repository import DownloadError
    from .indexer import ComicLibrary
except ImportError:
    from batch_download_repository import BatchDownloadRepository
    from batch_download_source import BatchSourceError
    from database import Database
    from download_repository import DownloadError
    from indexer import ComicLibrary


class IncrementalPullRepository:
    def __init__(
        self,
        database: Database,
        library: ComicLibrary,
        batch_downloads: BatchDownloadRepository,
    ) -> None:
        self.database = database
        self.library = library
        self.batch_downloads = batch_downloads

    def list_tasks(
        self,
        *,
        page: int = 1,
        page_size: int = 30,
        search: str = "",
        comic_status: str = "",
        task_state: str = "",
    ) -> dict:
        page_size = min(200, max(1, int(page_size)))
        page = max(1, int(page))
        base_conditions: List[str] = []
        base_params: List[object] = []
        if search.strip():
            needle = f"%{search.strip().casefold()}%"
            base_conditions.append(
                """
                (
                    LOWER(comic_id) LIKE ?
                    OR LOWER(comic_name) LIKE ?
                    OR LOWER(latest_known_chapter_title) LIKE ?
                    OR LOWER(error_message) LIKE ?
                )
                """
            )
            base_params.extend([needle] * 4)
        conditions = list(base_conditions)
        params = list(base_params)
        if comic_status in {"0", "1"}:
            conditions.append("comic_status = ?")
            params.append(int(comic_status))
        if task_state:
            conditions.append("task_state = ?")
            params.append(task_state)
        where = " AND ".join(conditions) if conditions else "1 = 1"
        base_where = (
            " AND ".join(base_conditions)
            if base_conditions else "1 = 1"
        )
        with self.database.connect() as connection:
            total = int(
                connection.execute(
                    f"""
                    SELECT COUNT(*) FROM incremental_pull_tasks
                    WHERE {where}
                    """,
                    tuple(params),
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"""
                SELECT * FROM incremental_pull_tasks
                WHERE {where}
                ORDER BY updated_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                tuple(params + [page_size, (page - 1) * page_size]),
            ).fetchall()
            count_rows = connection.execute(
                f"""
                SELECT comic_status, COUNT(*) AS count
                FROM incremental_pull_tasks
                WHERE {base_where}
                GROUP BY comic_status
                """,
                tuple(base_params),
            ).fetchall()
        total_pages = (total + page_size - 1) // page_size
        if total_pages and page > total_pages:
            return self.list_tasks(
                page=total_pages,
                page_size=page_size,
                search=search,
                comic_status=comic_status,
                task_state=task_state,
            )
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "status_counts": {
                str(row["comic_status"]): int(row["count"])
                for row in count_rows
            },
            "items": [dict(row) for row in rows],
        }

    def get_task(self, task_id: int) -> Optional[dict]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM incremental_pull_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        return dict(row) if row else None

    def set_comic_status(self, task_id: int, comic_status: int) -> dict:
        if comic_status not in {0, 1}:
            raise DownloadError(
                "漫画状态只能是 0（连载中）或 1（已完结）。",
                code="invalid_comic_status",
            )
        now = datetime.now().isoformat(timespec="seconds")
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE incremental_pull_tasks
                SET comic_status = ?, error_code = '',
                    error_message = '', updated_at = ?
                WHERE id = ?
                """,
                (comic_status, now, task_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(task_id)
            connection.commit()
        return {"task_id": task_id, "comic_status": comic_status}

    def preview_new_chapters(self, task_id: int) -> dict:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        if int(task["comic_status"] or 0) == 1:
            raise DownloadError(
                "该漫画已标记为完结，请先恢复连载状态。",
                code="comic_already_completed",
                http_status=409,
            )
        active_count = self._active_batch_count(task["comic_id"])
        if active_count:
            raise DownloadError(
                f"该漫画还有 {active_count} 个批量任务未完成，请完成后再增量拉取。",
                code="active_batch_exists",
                http_status=409,
                details={"pending_task_count": active_count},
            )

        self._update_check_state(task_id, "checking")
        try:
            chapters = self.batch_downloads.source.resolve_series(
                str(task["comic_id"]),
                task["series_url"],
            )
        except BatchSourceError as exc:
            self._update_check_state(
                task_id,
                "error",
                error_code=exc.code,
                error_message=exc.message,
            )
            raise DownloadError(
                exc.message,
                code=exc.code,
                http_status=400,
            ) from exc
        except Exception as exc:
            self._update_check_state(
                task_id,
                "error",
                error_code="incremental_check_failed",
                error_message=str(exc),
            )
            raise DownloadError(
                f"增量检查失败：{exc}",
                code="incremental_check_failed",
                http_status=500,
            ) from exc

        baseline_order = int(task["latest_known_chapter_order"] or 0)
        present = self._present_chapter_keys(task["comic_id"])
        candidates = [
            chapter
            for chapter in chapters
            if (
                chapter.sort_order > baseline_order
                or chapter.chapter_key not in present
            )
        ]
        source_latest = chapters[-1] if chapters else None
        now = datetime.now().isoformat(timespec="seconds")
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE incremental_pull_tasks
                SET known_chapter_count = ?,
                    last_checked_at = ?, last_run_at = ?,
                    task_state = 'idle', error_code = '',
                    error_message = '', updated_at = ?
                WHERE id = ?
                """,
                (
                    len(chapters),
                    now,
                    now,
                    now,
                    task_id,
                ),
            )
            connection.commit()

        if not candidates:
            return {
                "task_id": task_id,
                "comic_id": task["comic_id"],
                "comic_name": task["comic_name"],
                "analysis_id": "",
                "current_chapter_key": task["current_chapter_key"],
                "latest_source_chapter_key": (
                    source_latest.chapter_key if source_latest else ""
                ),
                "new_count": 0,
                "no_new_chapters": True,
                "items": [],
            }

        book = self.library.get_metadata(int(task["comic_id"]))
        analysis = self.batch_downloads.create_analysis_from_chapters(
            task["comic_id"],
            task["comic_name"],
            task["series_url"],
            candidates,
            source_chapter_count=len(chapters),
            access_level=book.access_level if book is not None else 0,
            allow_existing_book=True,
        )
        return {
            "task_id": task_id,
            "comic_id": task["comic_id"],
            "comic_name": task["comic_name"],
            "analysis_id": analysis["analysis_id"],
            "current_chapter_key": task["current_chapter_key"],
            "latest_source_chapter_key": (
                source_latest.chapter_key if source_latest else ""
            ),
            "new_count": len(candidates),
            "no_new_chapters": False,
            "items": [
                {
                    **chapter.public_dict(),
                    "status": "available",
                }
                for chapter in candidates
            ],
        }

    def _active_batch_count(self, comic_id: str) -> int:
        with self.database.connect() as connection:
            return int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM batch_download_tasks
                    WHERE parent_id = ?
                      AND status IN ('pending', 'running', 'interrupted')
                    """,
                    (comic_id,),
                ).fetchone()[0]
            )

    def _present_chapter_keys(self, comic_id: str) -> set:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT chapter_key FROM book_chapters
                WHERE book_id = ?
                """,
                (comic_id,),
            ).fetchall()
        return {str(row["chapter_key"]) for row in rows}

    def _update_check_state(
        self,
        task_id: int,
        state: str,
        *,
        error_code: str = "",
        error_message: str = "",
    ) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE incremental_pull_tasks
                SET task_state = ?, error_code = ?, error_message = ?,
                    attempt_count = attempt_count + ?,
                    last_checked_at = ?, last_run_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    state,
                    error_code,
                    error_message,
                    1 if state == "checking" else 0,
                    now,
                    now,
                    now,
                    task_id,
                ),
            )
            connection.commit()
