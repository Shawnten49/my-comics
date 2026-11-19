from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence
from urllib.parse import urlsplit

try:
    from .batch_download_source import (
        BatchChapterPreview,
        BatchSourceError,
        XiaoaimanhuaSource,
    )
    from .database import Database
    from .download_repository import DownloadError
    from .download_root import DownloadRootGuard
    from .indexer import ComicLibrary
except ImportError:
    from batch_download_source import (
        BatchChapterPreview,
        BatchSourceError,
        XiaoaimanhuaSource,
    )
    from database import Database
    from download_repository import DownloadError
    from download_root import DownloadRootGuard
    from indexer import ComicLibrary


PREVIEW_ID_RE = re.compile(r"^ba_[a-f0-9]{32}$")
CHAPTER_SELECTOR = "#chapter-images img"
SAFE_COMPONENT_RE = re.compile(r"[\x00-\x1f<>:\"/\\|?*]+")


class BatchDownloadRepository:
    def __init__(
        self,
        database: Database,
        library: ComicLibrary,
        *,
        output_root: Path,
        project_root: Path,
        source: Optional[XiaoaimanhuaSource] = None,
        preview_ttl_seconds: int = 1800,
        download_root_guard: Optional[DownloadRootGuard] = None,
    ) -> None:
        self.database = database
        self.library = library
        self.output_root = output_root.expanduser().resolve()
        self.project_root = project_root.expanduser().resolve()
        self.source = source or XiaoaimanhuaSource()
        self.preview_ttl_seconds = max(60, int(preview_ttl_seconds))
        self.preview_dir = self.project_root / "run" / "batch-previews"
        self.result_dir = self.project_root / "run" / "batch-results"
        self.log_dir = self.project_root / "run" / "batch-logs"
        self.download_root_guard = download_root_guard

    def validate_output_root(self) -> dict:
        if self.download_root_guard is None:
            return {"available": True, "path": str(self.output_root)}
        return self.download_root_guard.validate()

    def create_analysis(
        self,
        parent_id: int,
        comic_name: str,
        series_url: str,
        access_level: int = 0,
    ) -> dict:
        self.validate_output_root()
        try:
            normalized_id = int(parent_id)
        except (TypeError, ValueError):
            normalized_id = 0
        if normalized_id <= 0:
            raise DownloadError(
                "漫画 ID 必须是正整数。",
                code="invalid_parent_id",
            )
        normalized_name = re.sub(
            r"\s+",
            " ",
            str(comic_name or "").strip(),
        )
        normalized_url = str(series_url or "").strip()
        if not normalized_name:
            raise DownloadError(
                "漫画名称不能为空。",
                code="invalid_comic_name",
            )
        if self._book_exists(normalized_id):
            raise DownloadError(
                f"漫画 ID {normalized_id} 已存在于 books 表中，不能创建批量下载任务。",
                code="comic_already_exists",
                http_status=409,
            )
        try:
            chapters = self.source.resolve_series(
                str(normalized_id),
                normalized_url,
            )
        except BatchSourceError as exc:
            raise DownloadError(
                exc.message,
                code=exc.code,
                http_status=400,
            ) from exc

        return self.create_analysis_from_chapters(
            normalized_id,
            normalized_name,
            normalized_url,
            chapters,
            source_chapter_count=len(chapters),
            access_level=access_level,
        )

    def create_analysis_from_chapters(
        self,
        parent_id: int,
        comic_name: str,
        series_url: str,
        chapters: Sequence[BatchChapterPreview],
        *,
        source_chapter_count: Optional[int] = None,
        access_level: int = 0,
        allow_existing_book: bool = False,
    ) -> dict:
        normalized_id = int(parent_id)
        normalized_name = re.sub(
            r"\s+",
            " ",
            str(comic_name or "").strip(),
        )
        normalized_url = str(series_url or "").strip()
        try:
            normalized_access = int(access_level)
        except (TypeError, ValueError):
            normalized_access = -1
        if normalized_access not in {0, 1, 2}:
            raise DownloadError(
                "漫画可见性必须为 0、1 或 2。",
                code="invalid_access_level",
            )
        self._cleanup_previews()
        analysis_id = f"ba_{uuid.uuid4().hex}"
        now = datetime.now()
        parsed = urlsplit(normalized_url)
        payload = {
            "analysis_id": analysis_id,
            "parent_id": normalized_id,
            "comic_name": normalized_name,
            "series_url": normalized_url,
            "source_host": (parsed.hostname or "").lower(),
            "created_at": now.isoformat(timespec="seconds"),
            "expires_at": (
                now + timedelta(seconds=self.preview_ttl_seconds)
            ).isoformat(timespec="seconds"),
            "chapters": [chapter.public_dict() for chapter in chapters],
            "source_chapter_count": int(
                source_chapter_count
                if source_chapter_count is not None
                else len(chapters)
            ),
            "access_level": normalized_access,
            "allow_existing_book": bool(allow_existing_book),
        }
        self._write_preview(payload)
        return self.get_preview(analysis_id, page=1, page_size=50)

    def get_preview(
        self,
        analysis_id: str,
        *,
        page: int = 1,
        page_size: int = 50,
        search: str = "",
    ) -> dict:
        analysis = self._load_preview(analysis_id)
        page_size = min(200, max(1, int(page_size)))
        page = max(1, int(page))
        chapters = self._decorate_chapters(analysis)
        default_selected = [
            item["chapter_key"]
            for item in chapters
            if item["status"] == "available"
        ]
        needle = search.strip().casefold()
        if needle:
            chapters = [
                item
                for item in chapters
                if needle
                in (
                    f"{item['chapter_key']} {item['chapter_title']} "
                    f"{item['download_url']}"
                ).casefold()
            ]
        total = len(chapters)
        total_pages = (total + page_size - 1) // page_size
        if total_pages and page > total_pages:
            page = total_pages
        offset = (page - 1) * page_size
        status_counts = self._status_counts(self._decorate_chapters(analysis))
        return {
            "analysis_id": analysis_id,
            "parent_id": analysis["parent_id"],
            "comic_name": analysis["comic_name"],
            "series_url": analysis["series_url"],
            "expires_at": analysis["expires_at"],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "status_counts": status_counts,
            "default_selected_keys": default_selected,
            "items": chapters[offset:offset + page_size],
        }

    def create_tasks(
        self,
        analysis_id: str,
        chapter_keys: Iterable[str],
    ) -> dict:
        self.validate_output_root()
        analysis = self._load_preview(analysis_id)
        if (
            not analysis.get("allow_existing_book")
            and self._book_exists(int(analysis["parent_id"]))
        ):
            raise DownloadError(
                f"漫画 ID {analysis['parent_id']} 已存在于 books 表中，"
                "不能创建批量下载任务。",
                code="comic_already_exists",
                http_status=409,
            )
        all_preview = {
            item["chapter_key"]: item
            for item in self._decorate_chapters(analysis)
        }
        selected_keys: List[str] = []
        seen = set()
        for value in chapter_keys:
            key = str(value).strip()
            if not key or key in seen:
                continue
            if key not in all_preview:
                raise DownloadError(
                    f"章节 {key} 不属于本次预分析结果。",
                    code="chapter_not_in_analysis",
                )
            seen.add(key)
            selected_keys.append(key)
        if not selected_keys:
            raise DownloadError(
                "请至少选择一个章节。",
                code="no_chapters_selected",
            )

        active_keys = [
            key
            for key in selected_keys
            if all_preview[key]["status"] == "active"
        ]
        if active_keys:
            raise DownloadError(
                "部分章节已有活动批量任务，请刷新预分析结果后重试。",
                code="chapter_already_active",
                http_status=409,
                details={"chapter_keys": active_keys},
            )

        parent_id = analysis["parent_id"]
        parent_dir = self._resolve_series_directory(
            parent_id,
            analysis["comic_name"],
        )
        chapter_dirs = self._existing_chapter_directories(parent_id)
        series = self._load_series(parent_dir)
        series_chapters = {
            str(item.get("key") or ""): dict(item)
            for item in series.get("chapters", [])
            if isinstance(item, dict)
        }

        prepared = []
        for key in selected_keys:
            item = all_preview[key]
            directory = chapter_dirs.get(key, "")
            if not directory:
                directory = (
                    Path("chapters")
                    / (
                        f"{int(item['sort_order']):04d}_"
                        f"{self._safe_component(item['chapter_title'] or key)}"
                    )
                ).as_posix()
            absolute_dir = (parent_dir / directory).resolve(strict=False)
            try:
                absolute_dir.relative_to(self.output_root)
            except ValueError as exc:
                raise DownloadError(
                    "章节目录超出漫画下载根目录。",
                    code="invalid_chapter_directory",
                ) from exc
            absolute_dir.mkdir(parents=True, exist_ok=True)
            series_chapters[key] = {
                "key": key,
                "title": item["chapter_title"],
                "raw_title": item["chapter_title"],
                "kind": (
                    "volume"
                    if "卷" in item["chapter_title"]
                    else "chapter"
                ),
                "order": int(item["sort_order"]),
                "source_url": item["download_url"],
                "directory": directory,
            }
            prepared.append((item, directory, absolute_dir))

        series["content_type"] = "chaptered"
        series["book_id"] = parent_id
        series["title"] = analysis["comic_name"]
        series["raw_title"] = analysis["comic_name"]
        series["source_url"] = analysis["series_url"]
        series["cover_file"] = str(series.get("cover_file") or "")
        series["chapters"] = sorted(
            series_chapters.values(),
            key=lambda value: (
                int(value.get("order") or 0),
                str(value.get("key") or ""),
            ),
        )
        previous_series = self._read_bytes(parent_dir / "series.json")
        self._write_json(parent_dir / "series.json", series)

        batch_no = self._new_batch_no()
        now = datetime.now().isoformat(timespec="seconds")
        task_ids: List[int] = []
        skipped_count = 0
        try:
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                placeholders = ",".join("?" for _ in selected_keys)
                conflicts = connection.execute(
                    f"""
                    SELECT chapter_key FROM batch_download_tasks
                    WHERE parent_id = ?
                      AND chapter_key IN ({placeholders})
                      AND status IN ('pending', 'running', 'interrupted')
                    """,
                    (parent_id, *selected_keys),
                ).fetchall()
                if conflicts:
                    keys = [row["chapter_key"] for row in conflicts]
                    raise DownloadError(
                        "部分章节已有活动批量任务。",
                        code="chapter_already_active",
                        http_status=409,
                        details={"chapter_keys": keys},
                    )

                for item, directory, absolute_dir in prepared:
                    is_existing = item["status"] == "already_exists"
                    if is_existing:
                        skipped_count += 1
                    status = "skipped_existing" if is_existing else "pending"
                    stage = "completed" if is_existing else "queued"
                    cursor = connection.execute(
                        """
                        INSERT INTO batch_download_tasks(
                            batch_no, parent_id, comic_name, chapter_key,
                            chapter_title, sort_order, download_url,
                            source_host, status, stage, selector,
                            path_prefix, output_root, output_dir,
                            total_images, processed_images,
                            downloaded_images, skipped_images,
                            failed_images, access_level,
                            created_at, finished_at,
                            updated_at
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '',
                            ?, ?, ?, ?, 0, 0, 0, ?, ?, ?, ?
                        )
                        """,
                        (
                            batch_no,
                            parent_id,
                            analysis["comic_name"],
                            item["chapter_key"],
                            item["chapter_title"],
                            int(item["sort_order"]),
                            item["download_url"],
                            analysis["source_host"],
                            status,
                            stage,
                            CHAPTER_SELECTOR,
                            str(self.output_root),
                            str(absolute_dir),
                            0,
                            0,
                            int(analysis.get("access_level") or 0),
                            now,
                            now if is_existing else None,
                            now,
                        ),
                    )
                    task_id = int(cursor.lastrowid)
                    task_ids.append(task_id)
                    result_file = (
                        self.result_dir / f"task-{task_id}.json"
                    )
                    log_file = self.log_dir / f"task-{task_id}.log"
                    connection.execute(
                        """
                        UPDATE batch_download_tasks
                        SET result_file = ?, log_file = ?
                        WHERE id = ?
                        """,
                        (str(result_file), str(log_file), task_id),
                    )
                self._upsert_incremental_task(
                    connection,
                    analysis,
                    prepared,
                    batch_no,
                    now,
                )
                connection.commit()
        except DownloadError:
            self._restore_bytes(parent_dir / "series.json", previous_series)
            raise
        except sqlite3.IntegrityError as exc:
            self._restore_bytes(parent_dir / "series.json", previous_series)
            raise DownloadError(
                "创建批量章节任务时发生唯一性冲突。",
                code="batch_task_conflict",
                http_status=409,
            ) from exc
        except Exception:
            self._restore_bytes(parent_dir / "series.json", previous_series)
            raise

        index_warning = ""
        preview_path = self._preview_path(analysis_id)
        preview_path.unlink(missing_ok=True)
        return {
            "batch_no": batch_no,
            "count": len(task_ids),
            "created_count": len(task_ids) - skipped_count,
            "skipped_count": skipped_count,
            "task_ids": task_ids,
            "index_warning": index_warning,
        }

    def list_tasks(
        self,
        *,
        page: int = 1,
        page_size: int = 30,
        search: str = "",
        status: str = "",
        batch_no: str = "",
        parent_id: str = "",
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
                    LOWER(comic_name) LIKE ?
                    OR LOWER(parent_id) LIKE ?
                    OR LOWER(chapter_key) LIKE ?
                    OR LOWER(chapter_title) LIKE ?
                    OR LOWER(download_url) LIKE ?
                    OR LOWER(error_message) LIKE ?
                    OR LOWER(batch_no) LIKE ?
                )
                """
            )
            base_params.extend([needle] * 7)
        if batch_no:
            base_conditions.append("batch_no = ?")
            base_params.append(batch_no)
        if parent_id:
            base_conditions.append("parent_id = ?")
            base_params.append(parent_id)
        conditions = list(base_conditions)
        params = list(base_params)
        if status:
            conditions.append("status = ?")
            params.append(status)
        where = " AND ".join(conditions) if conditions else "1 = 1"
        base_where = (
            " AND ".join(base_conditions)
            if base_conditions else "1 = 1"
        )
        with self.database.connect() as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM batch_download_tasks WHERE {where}",
                    tuple(params),
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"""
                SELECT * FROM batch_download_tasks
                WHERE {where}
                ORDER BY created_at DESC, batch_no DESC,
                         sort_order, id
                LIMIT ? OFFSET ?
                """,
                tuple(params + [page_size, (page - 1) * page_size]),
            ).fetchall()
            count_rows = connection.execute(
                f"""
                SELECT status, COUNT(*) AS count
                FROM batch_download_tasks
                WHERE {base_where}
                GROUP BY status
                """,
                tuple(base_params),
            ).fetchall()
        total_pages = (total + page_size - 1) // page_size
        if total_pages and page > total_pages:
            return self.list_tasks(
                page=total_pages,
                page_size=page_size,
                search=search,
                status=status,
                batch_no=batch_no,
                parent_id=parent_id,
            )
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "status_counts": {
                row["status"]: int(row["count"]) for row in count_rows
            },
            "items": [dict(row) for row in rows],
        }

    def get_task(self, task_id: int) -> Optional[dict]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM batch_download_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        return dict(row) if row else None

    def recover_running_tasks(self) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE batch_download_tasks
                SET status = 'interrupted', stage = 'interrupted',
                    error_code = 'process_interrupted',
                    error_message = '网站已停止，章节可从断点继续',
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
                SELECT * FROM batch_download_tasks
                WHERE status IN ('pending', 'interrupted')
                ORDER BY created_at, batch_no, sort_order, id
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            now = datetime.now().isoformat(timespec="seconds")
            connection.execute(
                """
                UPDATE batch_download_tasks
                SET status = 'running', stage = 'starting',
                    started_at = COALESCE(started_at, ?),
                    finished_at = NULL, error_code = '',
                    error_message = '', updated_at = ?
                WHERE id = ?
                """,
                (now, now, row["id"]),
            )
            connection.commit()
        result = dict(row)
        result.update(status="running", stage="starting", updated_at=now)
        result["started_at"] = result.get("started_at") or now
        return result

    def update_task(
        self,
        task_id: int,
        *,
        status: Optional[str] = None,
        stage: Optional[str] = None,
        output_dir: Optional[str] = None,
        total_images: Optional[int] = None,
        processed_images: Optional[int] = None,
        downloaded_images: Optional[int] = None,
        skipped_images: Optional[int] = None,
        failed_images: Optional[int] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        finished: bool = False,
    ) -> None:
        values = {
            "status": status,
            "stage": stage,
            "output_dir": output_dir,
            "total_images": total_images,
            "processed_images": processed_images,
            "downloaded_images": downloaded_images,
            "skipped_images": skipped_images,
            "failed_images": failed_images,
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
        now = datetime.now().isoformat(timespec="seconds")
        fields.append("updated_at = ?")
        params.append(now)
        if finished:
            fields.append("finished_at = ?")
            params.append(now)
        params.append(task_id)
        with self.database.connect() as connection:
            connection.execute(
                f"""
                UPDATE batch_download_tasks
                SET {", ".join(fields)}
                WHERE id = ?
                """,
                tuple(params),
            )
            connection.commit()

    def retry_task(self, task_id: int) -> dict:
        now = datetime.now().isoformat(timespec="seconds")
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM batch_download_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError(task_id)
            if row["status"] not in {"failed", "interrupted"}:
                connection.rollback()
                raise DownloadError(
                    "当前章节任务状态不允许重试。",
                    code="task_not_retryable",
                    http_status=409,
                )
            try:
                connection.execute(
                    """
                    UPDATE batch_download_tasks
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
                    "该章节已有其他活动批量任务。",
                    code="chapter_already_active",
                    http_status=409,
                ) from exc
            connection.commit()
        self.sync_incremental_progress(task_id)
        return {"task_id": task_id, "status": "pending"}

    def recrawl_task(self, task_id: int) -> dict:
        now = datetime.now().isoformat(timespec="seconds")
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM batch_download_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError(task_id)
            if row["status"] != "completed":
                connection.rollback()
                raise DownloadError(
                    "只有已完成的批量章节可以重新拉取。",
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
                (row["parent_id"],),
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
                (row["parent_id"],),
            ).fetchone():
                connection.rollback()
                raise DownloadError(
                    "该漫画存在活动删除任务，不能重新拉取。",
                    code="book_has_active_delete",
                    http_status=409,
                )
            connection.execute(
                """
                UPDATE batch_download_tasks
                SET status = 'pending', stage = 'queued',
                    pull_mode = 'recrawl',
                    recrawl_count = recrawl_count + 1,
                    recrawl_requested_at = ?,
                    total_images = 0, processed_images = 0,
                    downloaded_images = 0, skipped_images = 0,
                    failed_images = 0,
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

    def chapter_is_complete(self, parent_id: str, chapter_key: str) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT complete, missing, page_count
                FROM book_chapters
                WHERE book_id = ? AND chapter_key = ?
                LIMIT 1
                """,
                (parent_id, chapter_key),
            ).fetchone()
        return bool(
            row
            and int(row["complete"] or 0) == 1
            and int(row["missing"] or 0) == 0
            and int(row["page_count"] or 0) > 0
        )

    def sync_incremental_progress(self, task_id: int) -> None:
        task = self.get_task(task_id)
        if task is None:
            return
        now = datetime.now().isoformat(timespec="seconds")
        with self.database.connect() as connection:
            incremental = connection.execute(
                """
                SELECT * FROM incremental_pull_tasks
                WHERE comic_id = ?
                LIMIT 1
                """,
                (task["parent_id"],),
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
                (task["parent_id"],),
            ).fetchone()
            pending = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM batch_download_tasks
                    WHERE parent_id = ?
                      AND status IN ('pending', 'running', 'interrupted')
                    """,
                    (task["parent_id"],),
                ).fetchone()[0]
            )
            current_order = int(
                incremental["current_chapter_order"] or 0
            )
            current_key = str(
                incremental["current_chapter_key"] or ""
            )
            current_title = str(
                incremental["current_chapter_title"] or ""
            )
            if current and int(current["sort_order"] or 0) > current_order:
                current_order = int(current["sort_order"] or 0)
                current_key = str(current["chapter_key"])
                current_title = str(current["title"])
            status = str(task["status"] or "")
            error_code = str(task["error_code"] or "")
            error_message = str(task["error_message"] or "")
            if status in {"failed", "interrupted"}:
                task_state = "error"
            elif status in {"pending", "running"} or pending:
                task_state = "downloading"
            else:
                task_state = "idle"
            finished_now = status in {"completed", "skipped_existing"}
            connection.execute(
                """
                UPDATE incremental_pull_tasks
                SET current_chapter_key = ?,
                    current_chapter_title = ?,
                    current_chapter_order = ?,
                    pending_task_count = ?,
                    task_state = ?,
                    error_code = ?,
                    error_message = ?,
                    last_run_at = ?,
                    last_completed_at =
                        CASE WHEN ? = 1 THEN ? ELSE last_completed_at END,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    current_key,
                    current_title,
                    current_order,
                    pending,
                    task_state,
                    error_code if task_state == "error" else "",
                    error_message if task_state == "error" else "",
                    now,
                    int(finished_now),
                    now,
                    now,
                    incremental["id"],
                ),
            )
            connection.commit()

    def repair_parent_series(self, task: dict) -> Path:
        output_dir = Path(task["output_dir"]).expanduser().resolve(strict=False)
        try:
            output_dir.relative_to(self.output_root)
        except ValueError as exc:
            raise RuntimeError("章节输出目录不在下载根目录内。") from exc
        parent_dir = output_dir.parent.parent
        series = self._load_series(parent_dir)
        chapters = {
            str(item.get("key") or ""): dict(item)
            for item in series.get("chapters", [])
            if isinstance(item, dict)
        }
        directory = output_dir.relative_to(parent_dir).as_posix()
        chapters[task["chapter_key"]] = {
            "key": task["chapter_key"],
            "title": task["chapter_title"],
            "raw_title": task["chapter_title"],
            "kind": (
                "volume"
                if "卷" in task["chapter_title"]
                else "chapter"
            ),
            "order": int(task["sort_order"]),
            "source_url": task["download_url"],
            "directory": directory,
        }
        series.update(
            {
                "content_type": "chaptered",
                "book_id": task["parent_id"],
                "title": task["comic_name"],
                "raw_title": task["comic_name"],
                "source_url": series.get("source_url") or "",
                "chapters": sorted(
                    chapters.values(),
                    key=lambda item: (
                        int(item.get("order") or 0),
                        str(item.get("key") or ""),
                    ),
                ),
            }
        )
        self._write_json(parent_dir / "series.json", series)
        return parent_dir

    def _upsert_incremental_task(
        self,
        connection: sqlite3.Connection,
        analysis: dict,
        prepared: Sequence[tuple],
        batch_no: str,
        now: str,
    ) -> None:
        if not prepared:
            return
        selected = sorted(
            (item for item, _, _ in prepared),
            key=lambda item: int(item.get("sort_order") or 0),
        )
        latest = selected[-1]
        parent_id = str(analysis["parent_id"])
        current = connection.execute(
            """
            SELECT chapter_key, title, sort_order
            FROM book_chapters
            WHERE book_id = ? AND complete = 1 AND missing = 0
            ORDER BY sort_order DESC, id DESC
            LIMIT 1
            """,
            (parent_id,),
        ).fetchone()
        existing = connection.execute(
            """
            SELECT * FROM incremental_pull_tasks
            WHERE comic_id = ? LIMIT 1
            """,
            (parent_id,),
        ).fetchone()
        pending = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM batch_download_tasks
                WHERE parent_id = ?
                  AND status IN ('pending', 'running', 'interrupted')
                """,
                (parent_id,),
            ).fetchone()[0]
        )
        source_count = int(
            analysis.get("source_chapter_count")
            or len(analysis.get("chapters") or [])
        )
        latest_order = int(latest.get("sort_order") or 0)
        latest_key = str(latest.get("chapter_key") or "")
        latest_title = str(latest.get("chapter_title") or "")
        comic_name = str(analysis["comic_name"])
        series_url = str(analysis["series_url"])
        source_host = str(analysis.get("source_host") or "")
        task_state = "downloading" if pending else "idle"
        if existing is None:
            current_key = str(current["chapter_key"]) if current else ""
            current_title = str(current["title"]) if current else ""
            current_order = int(current["sort_order"]) if current else 0
            connection.execute(
                """
                INSERT INTO incremental_pull_tasks(
                    comic_id, comic_name, series_url, source_host,
                    comic_status, current_chapter_key,
                    current_chapter_title, current_chapter_order,
                    latest_known_chapter_key,
                    latest_known_chapter_title,
                    latest_known_chapter_order,
                    known_chapter_count, pending_task_count,
                    last_batch_no, task_state, first_batch_at,
                    last_pull_at, last_run_at, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?
                )
                """,
                (
                    parent_id,
                    comic_name,
                    series_url,
                    source_host,
                    current_key,
                    current_title,
                    current_order,
                    latest_key,
                    latest_title,
                    latest_order,
                    source_count,
                    pending,
                    batch_no,
                    task_state,
                    now,
                    now,
                    now,
                    now,
                    now,
                ),
            )
            return

        previous_latest_order = int(
            existing["latest_known_chapter_order"] or 0
        )
        if latest_order >= previous_latest_order:
            next_latest_key = latest_key
            next_latest_title = latest_title
            next_latest_order = latest_order
        else:
            next_latest_key = existing["latest_known_chapter_key"]
            next_latest_title = existing["latest_known_chapter_title"]
            next_latest_order = previous_latest_order

        current_key = str(existing["current_chapter_key"] or "")
        current_title = str(existing["current_chapter_title"] or "")
        current_order = int(existing["current_chapter_order"] or 0)
        if current and int(current["sort_order"] or 0) > current_order:
            current_key = str(current["chapter_key"])
            current_title = str(current["title"])
            current_order = int(current["sort_order"])

        connection.execute(
            """
            UPDATE incremental_pull_tasks
            SET comic_name = ?, series_url = ?, source_host = ?,
                current_chapter_key = ?,
                current_chapter_title = ?,
                current_chapter_order = ?,
                latest_known_chapter_key = ?,
                latest_known_chapter_title = ?,
                latest_known_chapter_order = ?,
                known_chapter_count = MAX(known_chapter_count, ?),
                pending_task_count = ?,
                last_batch_no = ?, task_state = ?,
                error_code = '', error_message = '',
                last_pull_at = ?, last_run_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                comic_name,
                series_url,
                source_host,
                current_key,
                current_title,
                current_order,
                next_latest_key,
                next_latest_title,
                next_latest_order,
                source_count,
                pending,
                batch_no,
                task_state,
                now,
                now,
                now,
                existing["id"],
            ),
        )

    def _decorate_chapters(self, analysis: dict) -> List[dict]:
        parent_id = analysis["parent_id"]
        with self.database.connect() as connection:
            completed_rows = connection.execute(
                """
                SELECT chapter_key FROM book_chapters
                WHERE book_id = ? AND complete = 1 AND missing = 0
                  AND page_count > 0
                """,
                (parent_id,),
            ).fetchall()
            active_rows = connection.execute(
                """
                SELECT chapter_key, id FROM batch_download_tasks
                WHERE parent_id = ?
                  AND status IN ('pending', 'running', 'interrupted')
                """,
                (parent_id,),
            ).fetchall()
        completed = {row["chapter_key"] for row in completed_rows}
        active = {
            row["chapter_key"]: int(row["id"]) for row in active_rows
        }
        result = []
        for raw in analysis["chapters"]:
            item = dict(raw)
            key = str(item["chapter_key"])
            if key in completed:
                item["status"] = "already_exists"
                item["active_task_id"] = None
            elif key in active:
                item["status"] = "active"
                item["active_task_id"] = active[key]
            else:
                item["status"] = "available"
                item["active_task_id"] = None
            result.append(item)
        result.sort(
            key=lambda item: (
                int(item.get("sort_order") or 0),
                int(item["chapter_key"])
                if str(item["chapter_key"]).isdigit()
                else 10**18,
            )
        )
        return result

    @staticmethod
    def _status_counts(chapters: List[dict]) -> dict:
        counts = {
            "available": 0,
            "already_exists": 0,
            "active": 0,
        }
        for chapter in chapters:
            status = chapter.get("status", "available")
            counts[status] = counts.get(status, 0) + 1
        return counts

    def _resolve_series_directory(
        self,
        parent_id: str,
        comic_name: str,
    ) -> Path:
        with self.database.connect() as connection:
            book = connection.execute(
                """
                SELECT directory, content_type FROM books
                WHERE id = ? LIMIT 1
                """,
                (parent_id,),
            ).fetchone()
        if book is not None:
            if book["content_type"] != "chaptered":
                raise DownloadError(
                    f"漫画 ID {parent_id} 已作为单话漫画存在。",
                    code="book_content_type_conflict",
                    http_status=409,
                )
            candidate = (self.output_root / book["directory"]).resolve()
            try:
                candidate.relative_to(self.output_root)
            except ValueError as exc:
                raise DownloadError(
                    "数据库中的漫画目录不合法。",
                    code="invalid_series_directory",
                ) from exc
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate

        prefix = f"{parent_id}_"
        matches = sorted(
            (
                path for path in self.output_root.iterdir()
                if path.is_dir() and path.name.startswith(prefix)
            ),
            key=lambda path: path.name,
        )
        if len(matches) > 1:
            raise DownloadError(
                "检测到多个同名漫画目录，请先在漫画管理中整理目录。",
                code="ambiguous_series_directory",
                http_status=409,
            )
        if matches:
            return matches[0].resolve()
        directory = (
            self.output_root
            / f"{parent_id}_{self._safe_component(comic_name)}"
        )
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _existing_chapter_directories(self, parent_id: str) -> Dict[str, str]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT chapter_key, directory FROM book_chapters
                WHERE book_id = ?
                """,
                (parent_id,),
            ).fetchall()
        return {
            row["chapter_key"]: row["directory"]
            for row in rows
            if row["directory"]
        }

    def _load_series(self, parent_dir: Path) -> dict:
        path = parent_dir / "series.json"
        if not path.is_file():
            return {
                "content_type": "chaptered",
                "chapters": [],
            }
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DownloadError(
                f"无法读取 series.json：{exc}",
                code="invalid_series_metadata",
                http_status=500,
            ) from exc
        if not isinstance(payload, dict):
            raise DownloadError(
                "series.json 内容格式不正确。",
                code="invalid_series_metadata",
                http_status=500,
            )
        if not isinstance(payload.get("chapters"), list):
            payload["chapters"] = []
        return payload

    def _load_preview(self, analysis_id: str) -> dict:
        if not PREVIEW_ID_RE.fullmatch(str(analysis_id or "")):
            raise DownloadError(
                "预分析编号不合法。",
                code="invalid_analysis_id",
            )
        path = self._preview_path(analysis_id)
        if not path.is_file():
            raise DownloadError(
                "预分析结果已过期或不存在，请重新预分析。",
                code="analysis_not_found",
                http_status=410,
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            expires_at = datetime.fromisoformat(str(payload["expires_at"]))
        except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
            path.unlink(missing_ok=True)
            raise DownloadError(
                "预分析结果损坏，请重新预分析。",
                code="analysis_corrupt",
                http_status=410,
            ) from exc
        if datetime.now() > expires_at:
            path.unlink(missing_ok=True)
            raise DownloadError(
                "预分析结果已过期，请重新预分析。",
                code="analysis_expired",
                http_status=410,
            )
        if not isinstance(payload.get("chapters"), list):
            raise DownloadError(
                "预分析结果中没有章节。",
                code="analysis_corrupt",
                http_status=410,
            )
        return payload

    def _book_exists(self, book_id: int) -> bool:
        with self.database.connect() as connection:
            return connection.execute(
                "SELECT 1 FROM books WHERE id = ? LIMIT 1",
                (int(book_id),),
            ).fetchone() is not None

    def _write_preview(self, payload: dict) -> None:
        self.preview_dir.mkdir(parents=True, exist_ok=True)
        path = self._preview_path(payload["analysis_id"])
        self._write_json(path, payload)

    def _preview_path(self, analysis_id: str) -> Path:
        return self.preview_dir / f"{analysis_id}.json"

    def _cleanup_previews(self) -> None:
        if not self.preview_dir.is_dir():
            return
        now = datetime.now()
        for path in self.preview_dir.glob("ba_*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                expires_at = datetime.fromisoformat(
                    str(payload.get("expires_at") or "")
                )
            except (OSError, ValueError, json.JSONDecodeError):
                path.unlink(missing_ok=True)
                continue
            if now > expires_at:
                path.unlink(missing_ok=True)

    @staticmethod
    def _new_batch_no() -> str:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"BDL-{stamp}-{uuid.uuid4().hex[:6].upper()}"

    @staticmethod
    def _safe_component(value: str, max_length: int = 72) -> str:
        cleaned = SAFE_COMPONENT_RE.sub("_", str(value or "")).strip(" .")
        cleaned = re.sub(r"\s+", " ", cleaned)
        if not cleaned:
            cleaned = "未命名章节"
        return cleaned[:max_length].rstrip(" .") or "未命名章节"

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, path)

    @staticmethod
    def _read_bytes(path: Path) -> Optional[bytes]:
        try:
            return path.read_bytes()
        except OSError:
            return None

    @staticmethod
    def _restore_bytes(path: Path, previous: Optional[bytes]) -> None:
        if previous is None:
            path.unlink(missing_ok=True)
            return
        temporary = path.with_name(path.name + ".restore.tmp")
        temporary.write_bytes(previous)
        os.replace(temporary, path)
