from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

try:
    from .book_repository import BookRepository
    from .chrome_cdp import ChromeCDPError, ensure_chrome_cdp
    from .crawler_lock import crawler_lock
    from .download_repository import DownloadRepository
    from .download_root import DownloadRootError
    from .indexer import ComicLibrary
    from .sleep_inhibitor import SleepInhibitor
    from .thumbnails import ThumbnailCache
except ImportError:
    from book_repository import BookRepository
    from chrome_cdp import ChromeCDPError, ensure_chrome_cdp
    from crawler_lock import crawler_lock
    from download_repository import DownloadRepository
    from download_root import DownloadRootError
    from indexer import ComicLibrary
    from sleep_inhibitor import SleepInhibitor
    from thumbnails import ThumbnailCache


PROGRESS_RE = re.compile(r"^\[(\d+)/(\d+)\]")


class DownloadWorker:
    def __init__(
        self,
        repository: DownloadRepository,
        library: ComicLibrary,
        *,
        project_root: Path,
        lock_path: Optional[Path] = None,
        crawler_lock_path: Optional[Path] = None,
        crawler_runner: Optional[
            Callable[[dict, Callable[[str], None], threading.Event], int]
        ] = None,
        cdp_ensurer: Optional[Callable[[str], bool]] = None,
        sleep_inhibitor: Optional[SleepInhibitor] = None,
        book_repository: Optional[BookRepository] = None,
        thumbnails: Optional[ThumbnailCache] = None,
    ) -> None:
        self.repository = repository
        self.library = library
        self.project_root = project_root.expanduser().resolve()
        self.lock_path = lock_path
        self.crawler_lock_path = crawler_lock_path
        self.crawler_runner = crawler_runner or self._run_crawler
        self.cdp_ensurer = cdp_ensurer or ensure_chrome_cdp
        self.sleep_inhibitor = sleep_inhibitor
        self.books = book_repository or BookRepository(
            library.database,
            library.root,
        )
        self.thumbnails = thumbnails
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock_handle = None
        self._process: Optional[subprocess.Popen] = None
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
            except (OSError, ImportError):
                self._lock_handle.close()
                self._lock_handle = None
                raise RuntimeError("另一个漫画网站进程正在运行下载线程。")
        recovered = self.repository.recover_running_tasks()
        if recovered:
            print(f"已恢复 {recovered} 个中断的下载任务。")
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="comic-download",
            daemon=False,
        )
        self._thread.start()

    def notify(self) -> None:
        self._wake.set()

    def stop(self, timeout: float = 30.0) -> bool:
        self._stop.set()
        self._wake.set()
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
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
                    print(f"下载线程读取任务失败：{exc}")
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
        try:
            self.cdp_ensurer(self.repository.cdp_url)
        except ChromeCDPError as exc:
            self.repository.update_task(
                task["id"],
                status="failed",
                stage="failed",
                error_code="cdp_unavailable",
                error_message=exc.message,
                finished=True,
            )
            return
        try:
            self.repository.validate_output_root()
        except DownloadRootError as exc:
            self.repository.update_task(
                task["id"],
                status="failed",
                stage="failed",
                error_code=exc.code,
                error_message=exc.message,
                finished=True,
            )
            return
        result_file = Path(task["result_file"])
        log_file = Path(task["log_file"])
        result_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        result_file.unlink(missing_ok=True)
        last_lines: List[str] = []
        is_recrawl = task.get("pull_mode") == "recrawl"
        target_dir = (
            Path(task["output_dir"]).resolve(strict=False)
            if task.get("output_dir") else None
        )
        staging_dir: Optional[Path] = None
        output_dir: Optional[Path] = None
        runner_task = task
        if is_recrawl:
            if target_dir is None:
                raise RuntimeError("重新拉取任务缺少原输出目录。")
            staging_dir = target_dir.parent / (
                f".recrawl-task-{task['id']}-"
                f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
            )
            if staging_dir.exists():
                shutil.rmtree(staging_dir)
            runner_task = dict(task)
            runner_task["output_dir"] = str(staging_dir)
        def on_line(line: str) -> None:
            clean = line.rstrip()
            if not clean:
                return
            last_lines.append(clean)
            if len(last_lines) > 80:
                last_lines.pop(0)
            self._consume_progress(task["id"], clean, is_recrawl)

        with crawler_lock(self.crawler_lock_path, self._stop) as acquired:
            if not acquired:
                if not is_recrawl and output_dir is not None:
                    self._write_marker(output_dir, task["id"], "interrupted")
                self.repository.update_task(
                    task["id"],
                    status="interrupted",
                    stage="interrupted",
                    error_code="process_interrupted",
                    error_message="下载进程已停止，可重试继续",
                )
                return
            owner = f"download:{task['id']}"
            if self.sleep_inhibitor is not None:
                self.sleep_inhibitor.acquire(owner)
            try:
                exit_code = self.crawler_runner(
                    runner_task,
                    on_line,
                    self._stop,
                )
            finally:
                if self.sleep_inhibitor is not None:
                    self.sleep_inhibitor.release(owner)
        task = self.repository.get_task(task["id"]) or task
        result = self._read_json(result_file)
        output_dir = self._resolve_output_dir(runner_task, result)
        if is_recrawl and staging_dir is not None:
            output_dir = staging_dir
        counts = self._counts_from_manifest(output_dir)

        if self._stop.is_set() or exit_code in {130, -15}:
            if is_recrawl:
                self._cleanup_recrawl_staging(staging_dir)
            else:
                self._write_marker(output_dir, task["id"], "interrupted")
            self.repository.update_task(
                task["id"],
                status="interrupted",
                stage="interrupted",
                title=str(result.get("title") or task.get("title") or ""),
                raw_title=str(result.get("title") or task.get("title") or ""),
                output_dir=str(
                    target_dir if is_recrawl and target_dir else output_dir or ""
                ),
                total_images=counts["total_images"],
                processed_images=counts["processed_images"],
                downloaded_images=counts["downloaded_images"],
                skipped_images=counts["skipped_images"],
                failed_images=counts["failed_images"],
                error_code="process_interrupted",
                error_message="下载进程已停止，可重试继续",
            )
            return

        succeeded = (
            exit_code == 0
            and str(result.get("status") or "") in {"success", "existing"}
            and counts["complete"]
        )
        if not succeeded:
            error = str(result.get("error") or "")
            if not error and last_lines:
                error = last_lines[-1]
            if not error:
                error = f"图片下载失败，退出码 {exit_code}"
            if is_recrawl:
                self._cleanup_recrawl_staging(staging_dir)
            else:
                self._write_marker(output_dir, task["id"], "failed")
            self.repository.update_task(
                task["id"],
                status="failed",
                stage="failed",
                title=str(result.get("title") or task.get("title") or ""),
                raw_title=str(result.get("title") or task.get("title") or ""),
                output_dir=str(
                    target_dir if is_recrawl and target_dir else output_dir or ""
                ),
                total_images=counts["total_images"],
                processed_images=counts["processed_images"],
                downloaded_images=counts["downloaded_images"],
                skipped_images=counts["skipped_images"],
                failed_images=counts["failed_images"],
                error_code=(
                    "partial_download"
                    if counts["failed_images"]
                    else "download_failed"
                ),
                error_message=error,
                finished=True,
            )
            return

        if is_recrawl and staging_dir is not None and target_dir is not None:
            self._swap_recrawl_directory(target_dir, staging_dir)
            output_dir = target_dir
            result["output_dir"] = str(target_dir)

        self.repository.update_task(
            task["id"],
            stage="importing_book",
            output_dir=str(output_dir),
            title=str(result.get("title") or ""),
            raw_title=str(result.get("title") or ""),
            total_images=counts["total_images"],
            processed_images=counts["processed_images"],
            downloaded_images=counts["downloaded_images"],
            skipped_images=counts["skipped_images"],
            failed_images=0,
        )
        try:
            marker = output_dir / ".download-task.json"
            marker.unlink(missing_ok=True)
            self.books.import_single_book(
                int(task["comic_id"]),
                output_dir,
                access_level=int(task.get("access_level", 0)),
                source_url=str(task.get("download_url") or ""),
            )
            book = self.library.get_metadata(task["comic_id"])
            if book is None:
                raise RuntimeError("下载完成，但漫画未写入 books 表")
            if self.thumbnails is not None:
                try:
                    self.thumbnails.warm(self.library.cover_path(book))
                except Exception as exc:
                    print(
                        f"漫画缩略图预生成失败：{exc}",
                        file=sys.stderr,
                    )
            self.repository.update_task(
                task["id"],
                status="completed",
                stage="completed",
                title=book.title,
                raw_title=book.raw_title or str(result.get("title") or ""),
                output_dir=str(output_dir),
                total_images=counts["total_images"],
                processed_images=counts["processed_images"],
                downloaded_images=counts["downloaded_images"],
                skipped_images=counts["skipped_images"],
                failed_images=0,
                error_code="",
                error_message="",
                finished=True,
            )
        except Exception as exc:
            self._write_marker(output_dir, task["id"], "failed")
            self.repository.update_task(
                task["id"],
                status="failed",
                stage="importing_book",
                error_code="book_import_failed",
                error_message=str(exc),
                finished=True,
            )
            if is_recrawl:
                self._cleanup_recrawl_staging(staging_dir)

    def _run_crawler(
        self,
        task: dict,
        on_line: Callable[[str], None],
        stop_event: threading.Event,
    ) -> int:
        command = [
            sys.executable,
            str(self.project_root / "image_crawler.py"),
            task["download_url"],
            "--out-root",
            task["output_root"],
            "--selector",
            task["selector"],
            "--path-prefix",
            task["path_prefix"],
            "--render-mode",
            "auto",
            "--render-format",
            "webp",
            "--render-quality",
            "100",
            "--cdp-url",
            self.repository.cdp_url,
            "--verification-wait",
            "360",
            "--result-file",
            task["result_file"],
            "--download-task-id",
            str(task["id"]),
        ]
        if task.get("pull_mode") == "recrawl":
            command.append("--force")
        else:
            command.append("--resume")
        if task.get("output_dir"):
            command.extend(["--output-dir", task["output_dir"]])

        log_path = Path(task["log_file"])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log:
            log.write(
                f"\n===== attempt {int(task.get('attempt_count', 0)) + 1} "
                f"{datetime.now().isoformat(timespec='seconds')} =====\n"
            )
            log.flush()
            process = subprocess.Popen(
                command,
                cwd=str(self.project_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            self._process = process

            def read_output() -> None:
                if process.stdout is None:
                    return
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    try:
                        on_line(line)
                    except Exception as exc:
                        print(
                            f"下载进度更新失败：{exc}",
                            file=sys.stderr,
                        )

            reader = threading.Thread(
                target=read_output,
                name=f"download-output-{task['id']}",
                daemon=True,
            )
            reader.start()
            while process.poll() is None:
                if stop_event.is_set():
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    break
                try:
                    process.wait(timeout=0.25)
                except subprocess.TimeoutExpired:
                    pass
            reader.join(timeout=5)
            self._process = None
            return int(process.returncode if process.returncode is not None else 130)

    def _consume_progress(
        self,
        task_id: int,
        line: str,
        is_recrawl: bool = False,
    ) -> None:
        updates = {}
        if line.startswith("页面标题："):
            title = line.split("：", 1)[1].strip()
            if title and title != "无标题":
                updates.update(title=title, raw_title=title)
        elif line.startswith("保存目录：") and not is_recrawl:
            output_dir = line.split("：", 1)[1].strip()
            if output_dir:
                updates.update(output_dir=output_dir)
        match = PROGRESS_RE.match(line)
        if match:
            updates.update(
                stage="downloading",
                processed_images=int(match.group(1)),
                total_images=int(match.group(2)),
                last_image_order=int(match.group(1)),
            )
        if updates:
            self.repository.update_task(task_id, **updates)

    def _resolve_output_dir(self, task: dict, result: dict) -> Optional[Path]:
        candidates = [result.get("output_dir"), task.get("output_dir")]
        root = self.library.root.resolve()
        for value in candidates:
            if not value:
                continue
            path = Path(str(value)).expanduser().resolve(strict=False)
            try:
                path.relative_to(root)
            except ValueError:
                continue
            if path.is_dir():
                return path
        prefix = f"{task['comic_id']}_"
        for path in self.library.root.iterdir():
            if path.is_dir() and path.name.startswith(prefix):
                return path.resolve()
        return None

    def _counts_from_manifest(self, output_dir: Optional[Path]) -> dict:
        if output_dir is None:
            return {
                "total_images": 0,
                "processed_images": 0,
                "downloaded_images": 0,
                "skipped_images": 0,
                "failed_images": 0,
                "complete": False,
            }
        manifest = self._read_json(output_dir / "manifest.json")
        try:
            total = int(manifest.get("image_count", 0))
            downloaded = int(manifest.get("downloaded", 0))
            skipped = int(manifest.get("skipped", 0))
            failed = int(manifest.get("failed", 0))
        except (TypeError, ValueError):
            total = downloaded = skipped = failed = 0
        items = manifest.get("items")
        item_complete = True
        if isinstance(items, list) and len(items) == total:
            item_complete = all(
                isinstance(item, dict)
                and item.get("status") in {"downloaded", "skipped"}
                for item in items
            )
        return {
            "total_images": total,
            "processed_images": downloaded + skipped + failed,
            "downloaded_images": downloaded,
            "skipped_images": skipped,
            "failed_images": failed,
            "complete": (
                total > 0
                and failed == 0
                and downloaded + skipped >= total
                and item_complete
            ),
        }

    @staticmethod
    def _read_json(path: Path) -> dict:
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _write_marker(
        output_dir: Optional[Path],
        task_id: int,
        status: str,
    ) -> None:
        if output_dir is None:
            return
        marker = output_dir / ".download-task.json"
        temporary = marker.with_name(marker.name + ".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "task_id": task_id,
                    "status": status,
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        os.replace(temporary, marker)

    @staticmethod
    def _cleanup_recrawl_staging(staging_dir: Optional[Path]) -> None:
        if staging_dir is None:
            return
        if staging_dir.is_symlink() or staging_dir.is_file():
            staging_dir.unlink(missing_ok=True)
        elif staging_dir.is_dir():
            shutil.rmtree(staging_dir, ignore_errors=True)

    @staticmethod
    def _swap_recrawl_directory(target: Path, staging: Path) -> None:
        if not staging.is_dir():
            raise RuntimeError("重新拉取临时目录不存在。")
        backup = target.parent / (
            f".{target.name}.recrawl-old-"
            f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        )
        target_existed = target.exists()
        if target_existed:
            os.replace(target, backup)
        try:
            os.replace(staging, target)
        except Exception:
            if target_existed and backup.exists() and not target.exists():
                os.replace(backup, target)
            raise
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
