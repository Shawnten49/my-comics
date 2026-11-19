from __future__ import annotations

import hmac
import os
import sqlite3
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Deque, Dict, List, Optional, Sequence, Tuple

try:
    from .database import Database
    from .indexer import ComicLibrary
    from .settings_repository import SettingsError, SettingsRepository
    from .storage import (
        LOCAL_STORAGE_ID,
        StorageAccessError,
        StorageRegistry,
    )
except ImportError:
    from database import Database
    from indexer import ComicLibrary
    from settings_repository import SettingsError, SettingsRepository
    from storage import (
        LOCAL_STORAGE_ID,
        StorageAccessError,
        StorageRegistry,
    )


ACTIVE_RECORD_STATUSES = {"queued", "migrating", "interrupted"}


class MigrationError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "migration_error",
        http_status: int = 400,
        details: Optional[dict] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status
        self.details = details or {}


class VerificationGuard:
    def __init__(
        self,
        expected_code: Optional[str] = None,
        settings: Optional[SettingsRepository] = None,
    ) -> None:
        self.expected_code = expected_code
        self.settings = settings
        self._attempts: Dict[str, Deque[float]] = {}

    def check(self, code: str, client_key: str) -> None:
        now = monotonic()
        attempts = self._attempts.setdefault(client_key, deque())
        while attempts and now - attempts[0] > 600:
            attempts.popleft()
        if len(attempts) >= 5:
            raise MigrationError(
                "验证码尝试次数过多，请 10 分钟后再试。",
                code="too_many_verification_attempts",
                http_status=429,
            )
        if self.settings is not None:
            try:
                configured = self.settings.is_configured(
                    "security.verification_code"
                )
                valid = configured and self.settings.verify_secret(
                    "security.verification_code",
                    str(code or ""),
                )
            except SettingsError as exc:
                raise MigrationError(
                    exc.message,
                    code=exc.code,
                    http_status=exc.http_status,
                ) from exc
        elif self.expected_code:
            configured = True
            valid = hmac.compare_digest(
                str(code or "").encode("utf-8"),
                self.expected_code.encode("utf-8"),
            )
        else:
            configured = False
            valid = False
        if not configured:
            raise MigrationError(
                "尚未配置迁移/删除校验码。",
                code="verification_code_not_configured",
                http_status=503,
            )
        if not valid:
            attempts.append(now)
            raise MigrationError(
                "验证码错误。",
                code="invalid_verification_code",
                http_status=401,
            )
        attempts.clear()


class MigrationRepository:
    def __init__(
        self,
        database: Database,
        library: ComicLibrary,
        storage: StorageRegistry,
        verification_code: Optional[str] = None,
        settings: Optional[SettingsRepository] = None,
    ) -> None:
        self.database = database
        self.library = library
        self.storage = storage
        self.verification = VerificationGuard(verification_code, settings)

    def validate_destination(self, destination_path: str) -> dict:
        if not str(destination_path or "").strip():
            raise MigrationError(
                "请选择外盘目录。",
                code="invalid_destination",
            )
        try:
            root = self.storage.register_root(
                Path(str(destination_path).strip())
            )
            usage = os.statvfs(root.root_path)
        except StorageAccessError as exc:
            raise MigrationError(
                exc.message,
                code=exc.code,
                http_status=409,
                details=exc.details,
            ) from exc
        except OSError as exc:
            raise MigrationError(
                f"无法读取外盘空间信息：{exc}",
                code="invalid_destination",
                details={"path": str(destination_path)},
            ) from exc
        return {
            "storage_id": root.id,
            "path": root.root_path,
            "storage_name": root.display_name,
            "writable": os.access(root.root_path, os.W_OK),
            "free_bytes": usage.f_bavail * usage.f_frsize,
            "same_as_library_root": False,
        }

    def create_records(
        self,
        destination_path: str,
        book_ids: Sequence[str],
        verification_code: str,
        client_key: str = "local",
    ) -> dict:
        self.verification.check(verification_code, client_key)
        ordered_ids = list(dict.fromkeys(str(value) for value in book_ids))
        if not ordered_ids:
            raise MigrationError(
                "请选择要迁移的漫画。",
                code="no_eligible_books",
                http_status=422,
            )
        destination = self.validate_destination(destination_path)
        books = []
        invalid = []
        for book_id in ordered_ids:
            book = self.library.get_metadata(book_id)
            if (
                book is None
                or book.storage_id != LOCAL_STORAGE_ID
                or book.missing
            ):
                invalid.append(book_id)
                continue
            try:
                self.storage.resolve_directory(
                    LOCAL_STORAGE_ID,
                    book.directory,
                    force=True,
                )
            except StorageAccessError:
                invalid.append(book_id)
                continue
            books.append(book)
        if invalid:
            raise MigrationError(
                "部分漫画已不在本地或无法读取，请刷新后重新选择。",
                code="no_eligible_books",
                http_status=422,
                details={"invalid_book_ids": invalid},
            )

        now = datetime.now().isoformat(timespec="seconds")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        batch_no = f"MIG-{stamp}-{uuid.uuid4().hex[:6].upper()}"
        rows = []
        for book in books:
            source_path = str(
                (self.library.root / book.directory).resolve(strict=False)
            )
            destination_full_path = str(
                (Path(destination["path"]) / book.directory).resolve(
                    strict=False
                )
            )
            rows.append(
                (
                    batch_no,
                    book.id,
                    book.title,
                    LOCAL_STORAGE_ID,
                    destination["storage_id"],
                    book.directory,
                    book.directory,
                    source_path,
                    destination_full_path,
                    "queued",
                    "queued",
                    now,
                    now,
                )
            )
        try:
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                record_ids = []
                for row in rows:
                    cursor = connection.execute(
                        """
                        INSERT INTO migration_records(
                            batch_no, book_id, book_title,
                            source_storage_id, destination_storage_id,
                            source_relative_path, destination_relative_path,
                            source_path, destination_path, status, stage,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        row,
                    )
                    record_ids.append(int(cursor.lastrowid))
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise MigrationError(
                "所选漫画中已有正在进行的迁移记录。",
                code="active_migration_for_book",
                http_status=409,
            ) from exc
        return {
            "batch_no": batch_no,
            "count": len(rows),
            "record_ids": record_ids,
        }

    def recover_incomplete_records(self) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE migration_records
                SET status = 'interrupted',
                    error_code = 'interrupted',
                    error_message = '服务重启，记录等待恢复',
                    updated_at = ?
                WHERE status = 'migrating'
                """,
                (now,),
            )
            connection.commit()
        return max(0, cursor.rowcount)

    def claim_next_record(self) -> Optional[dict]:
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM migration_records
                WHERE status IN ('queued', 'interrupted')
                ORDER BY created_at, id
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            previous_status = row["status"]
            previous_stage = row["stage"]
            now = datetime.now().isoformat(timespec="seconds")
            connection.execute(
                """
                UPDATE migration_records
                SET status = 'migrating',
                    stage = CASE
                        WHEN status = 'interrupted' THEN stage
                        ELSE 'preflight'
                    END,
                    started_at = COALESCE(started_at, ?),
                    updated_at = ?,
                    error_code = '',
                    error_message = ''
                WHERE id = ?
                """,
                (now, now, row["id"]),
            )
            connection.commit()
        result = dict(row)
        result["status"] = "migrating"
        result["stage"] = (
            previous_stage if previous_status == "interrupted" else "preflight"
        )
        return result

    def get_record(self, record_id: int) -> Optional[dict]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM migration_records WHERE id = ?",
                (record_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_record(
        self,
        record_id: int,
        *,
        status: Optional[str] = None,
        stage: Optional[str] = None,
        total_files: Optional[int] = None,
        total_bytes: Optional[int] = None,
        copied_files: Optional[int] = None,
        copied_bytes: Optional[int] = None,
        verified_files: Optional[int] = None,
        verification_summary: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        finished: bool = False,
    ) -> None:
        fields = []
        params: List[object] = []
        values = {
            "status": status,
            "stage": stage,
            "total_files": total_files,
            "total_bytes": total_bytes,
            "copied_files": copied_files,
            "copied_bytes": copied_bytes,
            "verified_files": verified_files,
            "verification_summary": verification_summary,
            "error_code": error_code,
            "error_message": error_message,
        }
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
        params.append(record_id)
        with self.database.connect() as connection:
            connection.execute(
                f"UPDATE migration_records SET {', '.join(fields)} WHERE id = ?",
                tuple(params),
            )
            connection.commit()

    def switch_to_external(self, book_id: str, record: dict) -> None:
        with self.database.connect() as connection:
            storage = connection.execute(
                "SELECT 1 FROM storage_roots WHERE id = ?",
                (record["destination_storage_id"],),
            ).fetchone()
            if storage is None:
                raise MigrationError(
                    "外盘存储记录不存在。",
                    code="external_storage_mismatch",
                    http_status=503,
                )
            now = datetime.now().isoformat(timespec="seconds")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE books
                SET storage_id = ?, directory = ?, migrated_at = ?,
                    migration_job_id = ?, missing = 0,
                    directory_mtime_ns = 0
                WHERE id = ?
                """,
                (
                    record["destination_storage_id"],
                    record["destination_relative_path"],
                    now,
                    record["id"],
                    book_id,
                ),
            )
            connection.commit()

    def restore_local(
        self,
        book_id: str,
        source_relative_path: str,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE books
                SET storage_id = ?, directory = ?, migrated_at = NULL,
                    migration_job_id = NULL, missing = 0
                WHERE id = ?
                """,
                (LOCAL_STORAGE_ID, source_relative_path, book_id),
            )
            connection.commit()

    def retry_record(self, record_id: int) -> dict:
        now = datetime.now().isoformat(timespec="seconds")
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM migration_records WHERE id = ?",
                (record_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError(record_id)
            if row["status"] not in {
                "failed",
                "blocked_external",
                "interrupted",
            }:
                connection.rollback()
                raise MigrationError(
                    "当前记录状态不允许重试。",
                    code="record_not_retryable",
                    http_status=409,
                )
            try:
                connection.execute(
                    """
                    UPDATE migration_records
                    SET status = 'queued', stage = 'queued',
                        error_code = '', error_message = '',
                        retry_count = retry_count + 1,
                        finished_at = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, record_id),
                )
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise MigrationError(
                    "该漫画已有其他正在进行的迁移记录。",
                    code="active_migration_for_book",
                    http_status=409,
                ) from exc
            connection.commit()
        return {"record_id": record_id, "status": "queued"}

    def list_records(
        self,
        offset: int,
        limit: int,
        search: str = "",
        status: str = "",
        batch_no: str = "",
    ) -> Tuple[int, List[dict]]:
        conditions = ["1 = 1"]
        params: List[object] = []
        needle = search.strip().casefold()
        if needle:
            conditions.append(
                """
                (
                    LOWER(book_id) LIKE ?
                    OR LOWER(book_title) LIKE ?
                    OR LOWER(batch_no) LIKE ?
                    OR LOWER(source_path) LIKE ?
                    OR LOWER(destination_path) LIKE ?
                )
                """
            )
            params.extend([f"%{needle}%"] * 5)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if batch_no:
            conditions.append("batch_no = ?")
            params.append(batch_no)
        where = " AND ".join(conditions)
        with self.database.connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM migration_records WHERE {where}",
                tuple(params),
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT * FROM migration_records
                WHERE {where}
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                tuple(params + [limit, offset]),
            ).fetchall()
        return total, [dict(row) for row in rows]
