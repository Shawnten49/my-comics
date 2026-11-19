from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

try:
    from .indexer import ComicLibrary
    from .migration_repository import MigrationError, MigrationRepository
    from .storage import (
        LOCAL_STORAGE_ID,
        BookContentMissing,
        ExternalStorageMismatch,
        ExternalStorageOffline,
        StorageAccessError,
    )
except ImportError:
    from indexer import ComicLibrary
    from migration_repository import MigrationError, MigrationRepository
    from storage import (
        LOCAL_STORAGE_ID,
        BookContentMissing,
        ExternalStorageMismatch,
        ExternalStorageOffline,
        StorageAccessError,
    )


CHUNK_SIZE = 4 * 1024 * 1024
PROGRESS_INTERVAL = 2.0
PROGRESS_BYTES = 32 * 1024 * 1024


class MigrationInterrupted(Exception):
    pass


class VerificationMismatch(Exception):
    pass


@dataclass(frozen=True)
class InventoryItem:
    relative_path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Inventory:
    items: List[InventoryItem]
    total_bytes: int

    @property
    def digest(self) -> str:
        payload = [
            [item.relative_path, item.size, item.sha256]
            for item in self.items
        ]
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def summary(self) -> dict:
        return {
            "algorithm": "sha256",
            "files": len(self.items),
            "bytes": self.total_bytes,
            "inventory_sha256": self.digest,
        }


class MigrationWorker:
    def __init__(
        self,
        repository: MigrationRepository,
        library: ComicLibrary,
        *,
        lock_path: Optional[Path] = None,
    ) -> None:
        self.repository = repository
        self.library = library
        self.lock_path = lock_path
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock_handle = None
        self._running = False
        self.current_record_id = ""

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
            except (OSError, ImportError):
                self._lock_handle.close()
                self._lock_handle = None
                raise RuntimeError("另一个漫画网站进程正在运行迁移线程。")
        recovered = self.repository.recover_incomplete_records()
        if recovered:
            print(f"已恢复 {recovered} 个未完成迁移记录。")
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="comic-migration",
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

    @property
    def running(self) -> bool:
        return self._running

    def _run(self) -> None:
        self._running = True
        try:
            while not self._stop.is_set():
                try:
                    record = self.repository.claim_next_record()
                except Exception as exc:
                    print(f"迁移线程读取任务失败：{exc}")
                    self._wake.wait(2)
                    self._wake.clear()
                    continue
                if record is None:
                    self._wake.wait(0.75)
                    self._wake.clear()
                    continue
                self.current_record_id = record["id"]
                try:
                    self._process(record)
                finally:
                    self.current_record_id = ""
        finally:
            self._running = False
            if self._lock_handle is not None:
                try:
                    import fcntl

                    fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
                except (OSError, ImportError):
                    pass
                self._lock_handle.close()
                self._lock_handle = None

    def _process(self, record: dict) -> None:
        try:
            self._migrate(record)
        except MigrationInterrupted:
            self.repository.update_record(
                record["id"],
                status="interrupted",
                stage=record.get("stage") or "interrupted",
                error_code="interrupted",
                error_message="网站正在停止，任务已安全暂停。",
            )
        except StorageAccessError as exc:
            self._rollback_after_failure(record, exc.message)
            self.repository.update_record(
                record["id"],
                status="blocked_external",
                error_code=exc.code,
                error_message=exc.message,
                finished=True,
            )
        except MigrationError as exc:
            self._rollback_after_failure(record, exc.message)
            self.repository.update_record(
                record["id"],
                status="failed",
                error_code=exc.code,
                error_message=exc.message,
                finished=True,
            )
        except VerificationMismatch as exc:
            self._rollback_after_failure(record, str(exc))
            self.repository.update_record(
                record["id"],
                status="failed",
                error_code="verification_mismatch",
                error_message=str(exc),
                finished=True,
            )
        except Exception as exc:
            self._rollback_after_failure(record, str(exc))
            self.repository.update_record(
                record["id"],
                status="failed",
                error_code="migration_failed",
                error_message=str(exc),
                finished=True,
            )
        else:
            latest = self.repository.get_record(record["id"])
            if latest and latest["status"] not in {
                "completed",
                "completed_with_warning",
            }:
                self.repository.update_record(
                    record["id"],
                    status="completed",
                    stage="completed",
                    error_code="",
                    error_message="",
                    finished=True,
                )
    def _migrate(self, record: dict) -> None:
        book = self.library.get_metadata(record["book_id"])
        if book is None:
            raise VerificationMismatch("迁移对应的漫画记录不存在。")

        local_root = self.library.root
        destination_storage = self.repository.storage.get(
            record["destination_storage_id"]
        )
        if destination_storage is None:
            raise ExternalStorageOffline(
                "外盘存储记录不存在。",
                storage_id=record["destination_storage_id"],
            )
        destination_root = Path(
            destination_storage.root_path
        ).resolve(strict=False)
        source = (local_root / record["source_relative_path"]).resolve(
            strict=False
        )
        try:
            source.relative_to(local_root)
        except ValueError as exc:
            raise VerificationMismatch("本地源目录超出漫画根目录。") from exc
        final = (destination_root / record["destination_relative_path"]).resolve(
            strict=False
        )
        try:
            final.relative_to(destination_root)
        except ValueError as exc:
            raise VerificationMismatch("目标目录超出外盘根目录。") from exc
        staging = (
            destination_root / ".migration-staging" / str(record["id"])
        ).resolve(strict=False)
        trash = (
            local_root / ".migration-trash" / str(record["id"])
        ).resolve(strict=False)

        if book.storage_id != LOCAL_STORAGE_ID:
            self.repository.update_record(
                record["id"],
                status="migrating",
                stage="verifying",
            )
            self._verify_external_copy(record, source, final)
            self._cleanup_source(record, book.id, source, trash, final)
            return

        recoverable_stages = {
            "publishing",
            "verifying",
            "final_verifying",
            "switching_storage",
            "cleaning_source",
            "verifying_source",
            "source_quarantining",
            "source_verifying",
            "source_deleting",
        }
        if final.exists() and record.get("stage") not in recoverable_stages:
            raise VerificationMismatch(
                "目标目录已存在，且不属于当前迁移记录。"
            )
        if final.exists():
            if source.exists():
                source_inventory = self._inventory(source)
                self._verify_final(final, source_inventory)
                self._store_summary(record, source_inventory)
                self.repository.update_record(
                    record["id"],
                    status="migrating",
                    stage="switching_storage",
                    total_files=len(source_inventory.items),
                    total_bytes=source_inventory.total_bytes,
                    copied_files=len(source_inventory.items),
                    copied_bytes=source_inventory.total_bytes,
                    verified_files=len(source_inventory.items),
                    verification_summary=json.dumps(
                        source_inventory.summary(),
                        ensure_ascii=False,
                    ),
                )
                self.repository.switch_to_external(book.id, record)
                self._cleanup_source(record, book.id, source, trash, final)
                return
            if trash.exists():
                self._verify_final_from_summary(record, final)
                self.repository.switch_to_external(book.id, record)
                self._cleanup_source(record, book.id, source, trash, final)
                return
            raise VerificationMismatch("目标目录已存在，且无法确认属于当前任务。")

        if not source.exists() or not source.is_dir():
            raise BookContentMissing(
                "本地漫画目录不存在，无法开始迁移。",
                storage_id=LOCAL_STORAGE_ID,
                details={"path": str(source)},
            )
        if trash.exists():
            raise VerificationMismatch(
                "检测到未确认的本地隔离目录，已停止迁移以保护源文件。"
            )

        if staging.exists():
            shutil.rmtree(staging)
        staging.parent.mkdir(parents=True, exist_ok=True)

        self.repository.update_record(
            record["id"],
            status="migrating",
            stage="preflight",
        )
        source_inventory = self._inventory(source)
        if not source_inventory.items:
            raise VerificationMismatch("源目录中没有可迁移的普通文件。")
        free_bytes = shutil.disk_usage(destination_root).free
        reserve = max(512 * 1024 * 1024, int(source_inventory.total_bytes * 0.05))
        if free_bytes < source_inventory.total_bytes + reserve:
            raise MigrationError(
                "外盘剩余空间不足。",
                code="insufficient_storage",
                http_status=507,
                details={
                    "required_bytes": source_inventory.total_bytes + reserve,
                    "free_bytes": free_bytes,
                },
            )
        self.repository.update_record(
            record["id"],
            total_files=len(source_inventory.items),
            total_bytes=source_inventory.total_bytes,
            copied_files=0,
            copied_bytes=0,
        )
        self._store_summary(record, source_inventory)

        self.repository.update_record(
            record["id"],
            status="migrating",
            stage="copying",
        )
        self._copy_inventory(source, staging, record, source_inventory)
        self._check_stop()

        self.repository.update_record(
            record["id"],
            status="migrating",
            stage="verifying",
        )
        self._verify_final(staging, source_inventory)

        self.repository.update_record(
            record["id"],
            status="migrating",
            stage="publishing",
        )
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            raise VerificationMismatch("目标目录在复制期间出现，拒绝覆盖。")
        os.replace(staging, final)
        self._fsync_directory(final.parent)

        self.repository.update_record(
            record["id"],
            status="migrating",
            stage="verifying",
        )
        self._verify_final(final, source_inventory)

        self.repository.update_record(
            record["id"],
            status="migrating",
            stage="switching_storage",
            verified_files=len(source_inventory.items),
        )
        self.repository.switch_to_external(book.id, record)
        self._cleanup_source(record, book.id, source, trash, final)

    def _copy_inventory(
        self,
        source_root: Path,
        staging_root: Path,
        record: dict,
        inventory: Inventory,
    ) -> None:
        copied_files = 0
        copied_bytes = 0
        last_update = time.monotonic()
        bytes_since_update = 0
        staging_root.mkdir(parents=True, exist_ok=False)
        for item in inventory.items:
            self._check_stop()
            source = source_root / item.relative_path
            target = staging_root / item.relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with source.open("rb") as reader, target.open("xb") as writer:
                while True:
                    self._check_stop()
                    chunk = reader.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    writer.write(chunk)
                    digest.update(chunk)
                    copied_bytes += len(chunk)
                    bytes_since_update += len(chunk)
                    now = time.monotonic()
                    if (
                        now - last_update >= PROGRESS_INTERVAL
                        or bytes_since_update >= PROGRESS_BYTES
                    ):
                        self.repository.update_record(
                            record["id"],
                            copied_files=copied_files,
                            copied_bytes=copied_bytes,
                        )
                        last_update = now
                        bytes_since_update = 0
                writer.flush()
                os.fsync(writer.fileno())
            if digest.hexdigest() != item.sha256:
                raise VerificationMismatch(
                    f"复制期间源文件发生变化：{item.relative_path}"
                )
            self._copy_file_metadata(source, target)
            copied_files += 1
            self.repository.update_record(
                record["id"],
                copied_files=copied_files,
                copied_bytes=copied_bytes,
            )
        self._fsync_directory(staging_root)

    def _cleanup_source(
        self,
        record: dict,
        book_id: str,
        source: Path,
        trash: Path,
        final: Path,
    ) -> None:
        self._verify_external_copy(record, source, final)
        self.repository.update_record(
            record["id"],
            status="migrating",
            stage="cleaning_source",
        )
        trash.parent.mkdir(parents=True, exist_ok=True)
        if not source.exists() and not trash.exists():
            self.repository.update_record(
                record["id"],
                status="completed",
                stage="completed",
                error_code="",
                error_message="",
                finished=True,
            )
            return
        if source.exists():
            if trash.exists():
                self.repository.restore_local(
                    book_id,
                    record["source_relative_path"],
                )
                raise VerificationMismatch(
                    "源目录和隔离目录同时存在，已停止自动清理。"
                )
            trash.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, trash)
            self._fsync_directory(trash.parent)
        elif not trash.exists():
            raise VerificationMismatch(
                "本地源目录和隔离目录都不存在，已停止任务。"
            )

        self.repository.update_record(
            record["id"],
            status="migrating",
            stage="verifying_source",
        )
        try:
            self._verify_external_copy(record, source, final)
        except Exception:
            if trash.exists() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                os.replace(trash, source)
            self.repository.restore_local(
                book_id,
                record["source_relative_path"],
            )
            raise

        self.repository.update_record(
            record["id"],
            status="migrating",
            stage="cleaning_source",
        )
        try:
            shutil.rmtree(trash)
        except OSError as exc:
            self.repository.update_record(
                record["id"],
                status="completed_with_warning",
                stage="completed",
                error_code="local_cleanup_failed",
                error_message=f"迁移成功，但本地隔离目录清理失败：{exc}",
                finished=True,
            )
            return
        try:
            trash.parent.rmdir()
        except OSError:
            pass
        # Mark completion here so the generic success path is not applied twice.
        self.repository.update_record(
            record["id"],
            status="completed",
            stage="completed",
            error_code="",
            error_message="",
            finished=True,
        )

    def _verify_external_copy(
        self,
        record: dict,
        source: Path,
        final: Path,
    ) -> None:
        probe = self.repository.storage.probe(
            record["destination_storage_id"],
            force=True,
        )
        if not probe.available:
            error = (
                ExternalStorageMismatch
                if probe.status == "external_mismatch"
                else ExternalStorageOffline
            )
            raise error(
                probe.error or "外盘未连接。",
                storage_id=record["destination_storage_id"],
            )
        if source.exists():
            inventory = self._inventory(source)
            self._verify_final(final, inventory)
        else:
            self._verify_final_from_summary(record, final)

    def _verify_final(self, final: Path, expected: Inventory) -> None:
        if not final.is_dir():
            raise VerificationMismatch("外盘最终目录不存在。")
        actual = self._inventory(final)
        if actual.digest != expected.digest:
            raise VerificationMismatch("外盘文件清单或 SHA-256 校验不一致。")

    def _verify_final_from_summary(self, record: dict, final: Path) -> None:
        if not final.is_dir():
            raise VerificationMismatch("外盘最终目录不存在。")
        try:
            expected = json.loads(record["verification_summary"])
            expected_digest = str(expected["inventory_sha256"])
            expected_files = int(expected["files"])
            expected_bytes = int(expected["bytes"])
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise VerificationMismatch("任务缺少有效校验摘要。") from exc
        actual = self._inventory(final)
        if (
            actual.digest != expected_digest
            or len(actual.items) != expected_files
            or actual.total_bytes != expected_bytes
        ):
            raise VerificationMismatch("外盘最终目录与校验摘要不一致。")

    def _store_summary(self, record: dict, inventory: Inventory) -> None:
        self.repository.update_record(
            record["id"],
            verification_summary=json.dumps(
                inventory.summary(),
                ensure_ascii=False,
            ),
        )
        record["verification_summary"] = json.dumps(
            inventory.summary(),
            ensure_ascii=False,
        )

    def _rollback_after_failure(self, record: dict, message: str) -> None:
        book = self.library.get_metadata(record["book_id"])
        if book is None or book.storage_id == LOCAL_STORAGE_ID:
            return
        source = (self.library.root / record["source_relative_path"]).resolve(
            strict=False
        )
        trash = (
            self.library.root / ".migration-trash" / str(record["id"])
        ).resolve(strict=False)
        if trash.exists() and not source.exists():
            source.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.replace(trash, source)
            except OSError:
                pass
        if source.exists():
            self.repository.restore_local(
                book.id,
                record["source_relative_path"],
            )

    def _check_stop(self) -> None:
        if self._stop.is_set():
            raise MigrationInterrupted()

    def _inventory(self, root: Path) -> Inventory:
        if root.is_symlink():
            raise VerificationMismatch("迁移目录不能是符号链接。")
        items: List[InventoryItem] = []
        for current, dirs, files in os.walk(root, followlinks=False):
            current_path = Path(current)
            dirs[:] = sorted(
                name for name in dirs
                if not name.startswith("._") and name != ".Spotlight-V100"
            )
            for name in list(dirs):
                if (current_path / name).is_symlink():
                    raise VerificationMismatch("迁移目录中包含符号链接目录。")
            for name in sorted(files):
                if name.startswith("._") or name in {
                    ".DS_Store",
                    ".localized",
                }:
                    continue
                path = current_path / name
                if path.is_symlink():
                    raise VerificationMismatch("迁移目录中包含符号链接文件。")
                if not path.is_file():
                    continue
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    while True:
                        self._check_stop()
                        chunk = handle.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        digest.update(chunk)
                items.append(
                    InventoryItem(
                        relative_path=path.relative_to(root).as_posix(),
                        size=path.stat().st_size,
                        sha256=digest.hexdigest(),
                    )
                )
        return Inventory(
            items=items,
            total_bytes=sum(item.size for item in items),
        )

    @staticmethod
    def _copy_file_metadata(source: Path, target: Path) -> None:
        source_stat = source.stat()
        try:
            os.chmod(target, stat.S_IMODE(source_stat.st_mode))
        except OSError:
            pass
        try:
            os.utime(
                target,
                ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
                follow_symlinks=False,
            )
        except OSError:
            pass

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        try:
            descriptor = os.open(str(path), os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)

