from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urlsplit

try:
    from .database import Database
    from .download_root import DownloadRootGuard
    from .indexer import ComicLibrary
except ImportError:
    from database import Database
    from download_root import DownloadRootGuard
    from indexer import ComicLibrary


ALLOWED_PHOTO_HOSTS = {"18comic.vip"}
ACTIVE_DOWNLOAD_STATUSES = {"pending", "running", "interrupted"}


class DownloadError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "download_error",
        http_status: int = 400,
        details: Optional[dict] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status
        self.details = details or {}


class DownloadRepository:
    def __init__(
        self,
        database: Database,
        library: ComicLibrary,
        *,
        output_root: Path,
        project_root: Path,
        cdp_url: Optional[str] = None,
        download_root_guard: Optional[DownloadRootGuard] = None,
    ) -> None:
        self.database = database
        self.library = library
        self.output_root = output_root.expanduser().resolve()
        self.project_root = project_root.expanduser().resolve()
        self.cdp_url = (
            cdp_url
            or os.environ.get("COMIC_DOWNLOAD_CDP_URL")
            or "http://127.0.0.1:9222"
        )
        self.download_root_guard = download_root_guard

    def validate_output_root(self) -> dict:
        if self.download_root_guard is None:
            return {"available": True, "path": str(self.output_root)}
        return self.download_root_guard.validate()

    def create_task(
        self,
        comic_id: int,
        download_url: str,
        access_level: int = 0,
    ) -> dict:
        self.validate_output_root()
        try:
            normalized_id = int(comic_id)
        except (TypeError, ValueError):
            normalized_id = 0
        if normalized_id <= 0:
            raise DownloadError(
                "漫画 ID 必须是正整数。",
                code="invalid_comic_id",
            )
        try:
            normalized_access = int(access_level)
        except (TypeError, ValueError):
            normalized_access = -1
        if normalized_access not in {0, 1, 2}:
            raise DownloadError(
                "漫画可见性必须为 0、1 或 2。",
                code="invalid_access_level",
            )
        normalized_url = self._validate_url(
            str(normalized_id), download_url
        )
        if self._book_exists(normalized_id):
            raise DownloadError(
                f"漫画 ID {normalized_id} 已存在于 books 表中，不能重复创建下载任务。",
                code="comic_already_exists",
                http_status=409,
            )
        now = datetime.now().isoformat(timespec="seconds")
        selector = ".scramble-page img"
        path_prefix = f"/media/photos/{normalized_id}/"
        try:
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    INSERT INTO download_tasks(
                        comic_id, download_url, status, stage,
                        selector, path_prefix, output_root,
                        access_level, created_at, updated_at
                    ) VALUES (?, ?, 'pending', 'queued', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized_id,
                        normalized_url,
                        selector,
                        path_prefix,
                        str(self.output_root),
                        normalized_access,
                        now,
                        now,
                    ),
                )
                task_id = int(cursor.lastrowid)
                result_file = (
                    self.project_root
                    / "run"
                    / "download-results"
                    / f"task-{task_id}.json"
                )
                log_file = (
                    self.project_root
                    / "run"
                    / "download-logs"
                    / f"task-{task_id}.log"
                )
                connection.execute(
                    """
                    UPDATE download_tasks
                    SET result_file = ?, log_file = ?
                    WHERE id = ?
                    """,
                    (str(result_file), str(log_file), task_id),
                )
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise DownloadError(
                "该漫画已有等待、下载中或已中断的任务。",
                code="active_task_exists",
                http_status=409,
            ) from exc
        return {"task_id": task_id, "status": "pending"}

    def list_tasks(
        self,
        offset: int,
        limit: int,
        search: str = "",
        status: str = "",
    ) -> Tuple[int, List[dict]]:
        conditions = ["1 = 1"]
        params: List[object] = []
        needle = search.strip().casefold()
        if needle:
            conditions.append(
                """
                (
                    CAST(id AS TEXT) LIKE ?
                    OR LOWER(comic_id) LIKE ?
                    OR LOWER(title) LIKE ?
                    OR LOWER(download_url) LIKE ?
                    OR LOWER(output_dir) LIKE ?
                )
                """
            )
            params.extend([f"%{needle}%"] * 5)
        if status:
            conditions.append("status = ?")
            params.append(status)
        where = " AND ".join(conditions)
        with self.database.connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM download_tasks WHERE {where}",
                tuple(params),
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT * FROM download_tasks
                WHERE {where}
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                tuple(params + [limit, offset]),
            ).fetchall()
        return total, [dict(row) for row in rows]

    def get_task(self, task_id: int) -> Optional[dict]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM download_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        return dict(row) if row else None

    def recover_running_tasks(self) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE download_tasks
                SET status = 'interrupted', stage = 'interrupted',
                    error_code = 'process_interrupted',
                    error_message = '网站已停止，任务可继续下载',
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
                SELECT * FROM download_tasks
                WHERE status IN ('pending', 'interrupted')
                ORDER BY created_at, id
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            now = datetime.now().isoformat(timespec="seconds")
            connection.execute(
                """
                UPDATE download_tasks
                SET status = 'running', stage = 'starting',
                    started_at = COALESCE(started_at, ?),
                    finished_at = NULL,
                    error_code = '', error_message = '',
                    updated_at = ?
                WHERE id = ?
                """,
                (now, now, row["id"]),
            )
            connection.commit()
        result = dict(row)
        result["status"] = "running"
        result["stage"] = "starting"
        result["started_at"] = result.get("started_at") or now
        return result

    def update_task(
        self,
        task_id: int,
        *,
        title: Optional[str] = None,
        raw_title: Optional[str] = None,
        status: Optional[str] = None,
        stage: Optional[str] = None,
        output_dir: Optional[str] = None,
        total_images: Optional[int] = None,
        processed_images: Optional[int] = None,
        downloaded_images: Optional[int] = None,
        skipped_images: Optional[int] = None,
        failed_images: Optional[int] = None,
        last_image_order: Optional[int] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        finished: bool = False,
    ) -> None:
        values = {
            "title": title,
            "raw_title": raw_title,
            "status": status,
            "stage": stage,
            "output_dir": output_dir,
            "total_images": total_images,
            "processed_images": processed_images,
            "downloaded_images": downloaded_images,
            "skipped_images": skipped_images,
            "failed_images": failed_images,
            "last_image_order": last_image_order,
            "error_code": error_code,
            "error_message": error_message,
        }
        fields = []
        params: List[object] = []
        for column, value in values.items():
            if value is None:
                continue
            fields.append(f"{column} = ?")
            params.append(value)
        fields.append("updated_at = ?")
        params.append(datetime.now().isoformat(timespec="seconds"))
        if finished:
            fields.append("finished_at = ?")
            params.append(datetime.now().isoformat(timespec="seconds"))
        params.append(task_id)
        with self.database.connect() as connection:
            connection.execute(
                f"UPDATE download_tasks SET {', '.join(fields)} WHERE id = ?",
                tuple(params),
            )
            connection.commit()

    def retry_task(self, task_id: int) -> dict:
        now = datetime.now().isoformat(timespec="seconds")
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM download_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError(task_id)
            if row["status"] not in {"failed", "interrupted"}:
                connection.rollback()
                raise DownloadError(
                    "当前任务状态不允许重试。",
                    code="task_not_retryable",
                    http_status=409,
                )
            try:
                connection.execute(
                    """
                    UPDATE download_tasks
                    SET status = 'pending', stage = 'queued',
                        attempt_count = attempt_count + 1,
                        finished_at = NULL,
                        error_code = '', error_message = '',
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now, task_id),
                )
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise DownloadError(
                    "该漫画已有其他活动下载任务。",
                    code="active_task_exists",
                    http_status=409,
                ) from exc
            connection.commit()
        return {"task_id": task_id, "status": "pending"}

    def recrawl_task(self, task_id: int) -> dict:
        now = datetime.now().isoformat(timespec="seconds")
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM download_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError(task_id)
            if row["status"] != "completed":
                connection.rollback()
                raise DownloadError(
                    "只有已完成的下载任务可以重新拉取。",
                    code="task_not_recrawlable",
                    http_status=409,
                )
            if connection.execute(
                """
                SELECT 1 FROM migration_records
                WHERE book_id = ? AND status IN (
                    'queued', 'migrating', 'interrupted'
                ) LIMIT 1
                """,
                (row["comic_id"],),
            ).fetchone():
                connection.rollback()
                raise DownloadError(
                    "该漫画正在迁移，不能重新拉取。",
                    code="book_has_active_migration",
                    http_status=409,
                )
            if connection.execute(
                """
                SELECT 1 FROM deletion_tasks
                WHERE book_id = ? AND status IN (
                    'pending', 'waiting_dependency',
                    'running', 'interrupted'
                ) LIMIT 1
                """,
                (row["comic_id"],),
            ).fetchone():
                connection.rollback()
                raise DownloadError(
                    "该漫画存在活动删除任务，不能重新拉取。",
                    code="book_has_active_delete",
                    http_status=409,
                )
            connection.execute(
                """
                UPDATE download_tasks
                SET status = 'pending', stage = 'queued',
                    pull_mode = 'recrawl',
                    recrawl_count = recrawl_count + 1,
                    recrawl_requested_at = ?,
                    total_images = 0, processed_images = 0,
                    downloaded_images = 0, skipped_images = 0,
                    failed_images = 0, last_image_order = 0,
                    error_code = '', error_message = '',
                    finished_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now, now, task_id),
            )
            connection.commit()
        return {
            "task_id": task_id,
            "status": "pending",
            "pull_mode": "recrawl",
        }

    def _book_exists(self, comic_id: int) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM books WHERE id = ? LIMIT 1",
                (comic_id,),
            ).fetchone()
        return row is not None

    @staticmethod
    def _validate_url(comic_id: str, value: str) -> str:
        raw = str(value or "").strip()
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise DownloadError(
                "下载 URL 不合法。",
                code="invalid_download_url",
            )
        host = parsed.hostname.lower()
        if host not in ALLOWED_PHOTO_HOSTS and not host.endswith(
            ".18comic.vip"
        ):
            raise DownloadError(
                "下载 URL 不是允许的漫画站点。",
                code="invalid_download_url",
            )
        match = re.fullmatch(r"/photo/(\d+)/?", parsed.path)
        if match is None:
            raise DownloadError(
                "下载 URL 必须指向漫画详情页。",
                code="invalid_download_url",
            )
        if match.group(1) != comic_id:
            raise DownloadError(
                "URL 中的漫画 ID 与输入不一致。",
                code="comic_url_mismatch",
            )
        return raw
