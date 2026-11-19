from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

try:
    from .database import Database
except ImportError:
    from database import Database


LOCAL_STORAGE_ID = "local"
STORAGE_MARKER_NAME = ".comic-library-storage.json"


class StorageAccessError(Exception):
    code = "storage_error"
    status = "unavailable"

    def __init__(
        self,
        message: str,
        *,
        storage_id: str = "",
        details: Optional[dict] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.storage_id = storage_id
        self.details = details or {}


class InvalidStoragePath(StorageAccessError):
    code = "invalid_storage_path"


class ExternalStorageOffline(StorageAccessError):
    code = "external_storage_offline"
    status = "external_offline"


class ExternalStorageMismatch(StorageAccessError):
    code = "external_storage_mismatch"
    status = "external_mismatch"


class LocalStorageMissing(StorageAccessError):
    code = "local_storage_missing"
    status = "local_missing"


class BookContentMissing(StorageAccessError):
    code = "book_content_missing"
    status = "content_missing"


@dataclass(frozen=True)
class StorageRoot:
    id: str
    root_path: str
    display_name: str
    last_status: str = "unknown"
    last_error: str = ""
    last_seen_at: str = ""


@dataclass(frozen=True)
class StorageProbe:
    storage_id: str
    status: str
    available: bool
    root: Optional[StorageRoot]
    error: str = ""

    def public_dict(self) -> dict:
        return {
            "storage_id": self.storage_id,
            "storage_name": self.root.display_name if self.root else "本地目录",
            "storage_path": self.root.root_path if self.root else "",
            "availability": self.status,
            "availability_error": self.error,
        }


class StorageRegistry:
    def __init__(self, database: Database, local_root: Path) -> None:
        self.database = database
        self.local_root = local_root.expanduser().resolve()
        self._lock = threading.RLock()
        self._probe_cache: Dict[str, tuple[float, StorageProbe]] = {}

    def get(self, storage_id: str) -> Optional[StorageRoot]:
        if storage_id == LOCAL_STORAGE_ID:
            return StorageRoot(
                id=LOCAL_STORAGE_ID,
                root_path=str(self.local_root),
                display_name="本地目录",
                last_status="available",
            )
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM storage_roots WHERE id = ?",
                (storage_id,),
            ).fetchone()
        if row is None:
            return None
        return StorageRoot(
            id=row["id"],
            root_path=row["root_path"],
            display_name=row["display_name"] or Path(row["root_path"]).name,
            last_status=row["last_status"],
            last_error=row["last_error"] or "",
            last_seen_at=row["last_seen_at"] or "",
        )

    def list_roots(self) -> List[StorageRoot]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM storage_roots
                ORDER BY updated_at DESC, id DESC
                """
            ).fetchall()
        return [
            StorageRoot(
                id=row["id"],
                root_path=row["root_path"],
                display_name=row["display_name"] or Path(row["root_path"]).name,
                last_status=row["last_status"],
                last_error=row["last_error"] or "",
                last_seen_at=row["last_seen_at"] or "",
            )
            for row in rows
        ]

    def register_root(
        self,
        path: Path,
        display_name: str = "",
    ) -> StorageRoot:
        root_path = path.expanduser().resolve(strict=False)
        if not root_path.is_absolute():
            raise InvalidStoragePath("外盘路径必须是绝对路径。")
        if not root_path.exists() or not root_path.is_dir():
            raise ExternalStorageOffline(
                f"外盘目录不存在或未连接：{root_path}",
                details={"path": str(root_path)},
            )
        if self._paths_overlap(root_path, self.local_root):
            raise InvalidStoragePath("外盘路径不能与本地漫画目录重叠。")
        if not os.access(root_path, os.W_OK):
            raise InvalidStoragePath(f"外盘目录不可写：{root_path}")

        marker_path = root_path / STORAGE_MARKER_NAME
        marker = self._read_marker(marker_path)
        marker_id = str(marker.get("storage_id") or "")
        if marker and not marker_id.startswith("st_"):
            raise ExternalStorageMismatch(
                f"外盘标识文件无效：{marker_path}",
                details={"path": str(root_path)},
            )

        with self.database.connect() as connection:
            path_row = connection.execute(
                "SELECT * FROM storage_roots WHERE root_path = ?",
                (str(root_path),),
            ).fetchone()
            if path_row and marker_id and path_row["id"] != marker_id:
                raise ExternalStorageMismatch(
                    "该路径登记的存储标识与目录中的标识不一致。",
                    storage_id=marker_id,
                    details={"path": str(root_path)},
                )
            if marker_id:
                id_row = connection.execute(
                    "SELECT * FROM storage_roots WHERE id = ?",
                    (marker_id,),
                ).fetchone()
                if id_row and id_row["root_path"] != str(root_path):
                    raise ExternalStorageMismatch(
                        "该外盘已经在另一个挂载路径登记，请使用重新定位。",
                        storage_id=marker_id,
                        details={
                            "registered_path": id_row["root_path"],
                            "selected_path": str(root_path),
                        },
                    )
            elif path_row:
                marker_id = path_row["id"]
            else:
                marker_id = f"st_{uuid.uuid4().hex}"
            if not marker:
                self._write_marker(marker_path, marker_id)

            now = datetime.now().isoformat(timespec="seconds")
            connection.execute(
                """
                INSERT INTO storage_roots(
                    id, root_path, display_name, marker_version,
                    last_status, last_error, last_seen_at, created_at, updated_at
                ) VALUES (?, ?, ?, 1, 'available', '', ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    root_path = excluded.root_path,
                    display_name = excluded.display_name,
                    last_status = excluded.last_status,
                    last_error = '',
                    last_seen_at = excluded.last_seen_at,
                    updated_at = excluded.updated_at
                """,
                (
                    marker_id,
                    str(root_path),
                    display_name.strip() or root_path.name or str(root_path),
                    now,
                    now,
                    now,
                ),
            )
            connection.commit()
        with self._lock:
            self._probe_cache.pop(marker_id, None)
        result = self.get(marker_id)
        if result is None:
            raise RuntimeError("外盘登记后读取失败。")
        return result

    def relocate_root(self, storage_id: str, path: Path) -> StorageRoot:
        root_path = path.expanduser().resolve(strict=False)
        if not root_path.is_dir():
            raise ExternalStorageOffline(
                f"外盘目录不存在或未连接：{root_path}",
                storage_id=storage_id,
            )
        marker = self._read_marker(root_path / STORAGE_MARKER_NAME)
        if str(marker.get("storage_id") or "") != storage_id:
            raise ExternalStorageMismatch(
                "所选目录不属于当前外盘。",
                storage_id=storage_id,
                details={"path": str(root_path)},
            )
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM storage_roots WHERE id = ?",
                (storage_id,),
            ).fetchone()
            if row is None:
                raise KeyError(storage_id)
            conflict = connection.execute(
                """
                SELECT id FROM storage_roots
                WHERE root_path = ? AND id <> ?
                """,
                (str(root_path), storage_id),
            ).fetchone()
            if conflict:
                raise ExternalStorageMismatch(
                    "该路径已经登记为其他外盘。",
                    storage_id=storage_id,
                )
            now = datetime.now().isoformat(timespec="seconds")
            connection.execute(
                """
                UPDATE storage_roots
                SET root_path = ?, last_status = 'available',
                    last_error = '', last_seen_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (str(root_path), now, now, storage_id),
            )
            connection.commit()
        with self._lock:
            self._probe_cache.pop(storage_id, None)
        result = self.get(storage_id)
        if result is None:
            raise RuntimeError("外盘重新定位后读取失败。")
        return result

    def probe(
        self,
        storage_id: str,
        *,
        force: bool = False,
    ) -> StorageProbe:
        now = time.monotonic()
        with self._lock:
            cached = self._probe_cache.get(storage_id)
            if cached and not force and now - cached[0] < 1.5:
                return cached[1]

        root = self.get(storage_id)
        if root is None:
            probe = StorageProbe(
                storage_id=storage_id,
                status="external_mismatch",
                available=False,
                root=None,
                error="存储根记录不存在。",
            )
        else:
            path = Path(root.root_path)
            try:
                if not path.exists() or not path.is_dir():
                    raise ExternalStorageOffline(
                        f"外盘未连接：{root.display_name}",
                        storage_id=storage_id,
                        details={"path": root.root_path},
                    )
                if storage_id != LOCAL_STORAGE_ID:
                    marker = self._read_marker(path / STORAGE_MARKER_NAME)
                    marker_id = str(marker.get("storage_id") or "")
                    if marker_id != storage_id:
                        raise ExternalStorageMismatch(
                            "外盘身份不匹配，请检查连接的磁盘。",
                            storage_id=storage_id,
                            details={"path": root.root_path},
                        )
                probe = StorageProbe(
                    storage_id=storage_id,
                    status="available",
                    available=True,
                    root=root,
                )
            except PermissionError:
                probe = StorageProbe(
                    storage_id=storage_id,
                    status=(
                        "local_missing"
                        if storage_id == LOCAL_STORAGE_ID
                        else "external_offline"
                    ),
                    available=False,
                    root=root,
                    error=f"存储目录不可访问：{root.root_path}",
                )
            except ExternalStorageOffline as exc:
                probe = StorageProbe(
                    storage_id=storage_id,
                    status=exc.status,
                    available=False,
                    root=root,
                    error=exc.message,
                )
            except ExternalStorageMismatch as exc:
                probe = StorageProbe(
                    storage_id=storage_id,
                    status=exc.status,
                    available=False,
                    root=root,
                    error=exc.message,
                )

        with self._lock:
            self._probe_cache[storage_id] = (now, probe)
        self._record_probe(probe)
        return probe

    def availability(
        self,
        storage_id: str,
        directory: str = "",
        *,
        inspect_content: bool = False,
        force: bool = False,
    ) -> StorageProbe:
        probe = self.probe(storage_id, force=force)
        if not probe.available or not inspect_content or not directory:
            return probe
        try:
            path = self.resolve_directory(
                storage_id,
                directory,
                force=force,
            )
        except BookContentMissing as exc:
            return StorageProbe(
                storage_id=storage_id,
                status=exc.status,
                available=False,
                root=probe.root,
                error=exc.message,
            )
        if not path.is_dir():
            return StorageProbe(
                storage_id=storage_id,
                status="content_missing",
                available=False,
                root=probe.root,
                error="漫画目录不存在。",
            )
        return probe

    def resolve_directory(
        self,
        storage_id: str,
        directory: str,
        *,
        force: bool = False,
    ) -> Path:
        probe = self.probe(storage_id, force=force)
        if not probe.available or probe.root is None:
            if storage_id == LOCAL_STORAGE_ID:
                raise LocalStorageMissing(
                    probe.error or "本地漫画目录不可访问。",
                    storage_id=storage_id,
                    details={"path": probe.root.root_path if probe.root else ""},
                )
            error = (
                ExternalStorageMismatch
                if probe.status == "external_mismatch"
                else ExternalStorageOffline
            )
            raise error(
                probe.error or "外盘未连接。",
                storage_id=storage_id,
                details={"path": probe.root.root_path if probe.root else ""},
            )
        relative = Path(directory)
        if relative.is_absolute() or ".." in relative.parts:
            raise InvalidStoragePath("漫画目录包含非法路径。")
        root = Path(probe.root.root_path).resolve(strict=False)
        candidate = (root / relative).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise InvalidStoragePath("漫画目录超出存储根范围。") from exc
        if not candidate.exists() or not candidate.is_dir():
            raise BookContentMissing(
                "漫画文件在存储位置中不存在。",
                storage_id=storage_id,
                details={
                    "storage_id": storage_id,
                    "relative_path": directory,
                    "path": str(candidate),
                },
            )
        return candidate

    def _record_probe(self, probe: StorageProbe) -> None:
        if probe.storage_id == LOCAL_STORAGE_ID or probe.root is None:
            return
        now = datetime.now().isoformat(timespec="seconds")
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE storage_roots
                SET last_status = ?, last_error = ?,
                    last_seen_at = CASE WHEN ? = 'available' THEN ? ELSE last_seen_at END,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    probe.status,
                    probe.error,
                    probe.status,
                    now,
                    now,
                    probe.storage_id,
                ),
            )
            connection.commit()

    @staticmethod
    def _read_marker(path: Path) -> dict:
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _write_marker(path: Path, storage_id: str) -> None:
        temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        payload = {
            "version": 1,
            "storage_id": storage_id,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _paths_overlap(left: Path, right: Path) -> bool:
        left = left.resolve(strict=False)
        right = right.resolve(strict=False)
        try:
            left.relative_to(right)
            return True
        except ValueError:
            pass
        try:
            right.relative_to(left)
            return True
        except ValueError:
            return False
