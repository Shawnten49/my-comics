from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional


class DownloadRootError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        http_status: int = 409,
        details: Optional[dict] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status
        self.details = details or {}


def validate_download_root(
    value: str,
    *,
    expected_root: Optional[Path] = None,
) -> dict:
    raw_value = str(value or "").strip()
    if not raw_value:
        raise DownloadRootError(
            "下载根目录尚未配置，请前往系统设置配置 download.root。",
            code="download_root_not_configured",
            details={"settings_path": "/manage/settings"},
        )
    candidate = Path(raw_value).expanduser()
    if not candidate.is_absolute():
        raise DownloadRootError(
            "下载根目录必须是绝对路径。",
            code="download_root_invalid_path",
            http_status=400,
            details={"path": raw_value, "settings_path": "/manage/settings"},
        )
    resolved = candidate.resolve(strict=False)
    if resolved == Path(resolved.anchor):
        raise DownloadRootError(
            "下载根目录不能指向文件系统根目录。",
            code="download_root_invalid_path",
            http_status=400,
            details={
                "path": str(resolved),
                "settings_path": "/manage/settings",
            },
        )
    if not resolved.exists():
        raise DownloadRootError(
            f"下载根目录不存在：{resolved}",
            code="download_root_missing",
            details={
                "path": str(resolved),
                "reason": "目录不存在，请检查外盘是否已连接",
                "settings_path": "/manage/settings",
            },
        )
    if not resolved.is_dir():
        raise DownloadRootError(
            f"下载根目录不是目录：{resolved}",
            code="download_root_not_directory",
            http_status=422,
            details={"path": str(resolved), "settings_path": "/manage/settings"},
        )
    mode = os.R_OK | os.W_OK | os.X_OK
    if not os.access(resolved, mode):
        raise DownloadRootError(
            f"下载根目录没有读写权限：{resolved}",
            code="download_root_not_writable",
            http_status=403,
            details={"path": str(resolved), "settings_path": "/manage/settings"},
        )

    probe_path: Optional[Path] = None
    try:
        descriptor, probe_name = tempfile.mkstemp(
            prefix=".comic-write-test-",
            dir=resolved,
        )
        probe_path = Path(probe_name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(b"ok")
            handle.flush()
            os.fsync(handle.fileno())
        probe_path.unlink()
        probe_path = None
    except OSError as exc:
        raise DownloadRootError(
            f"下载根目录写入检测失败：{resolved}",
            code="download_root_probe_failed",
            http_status=403,
            details={
                "path": str(resolved),
                "reason": str(exc),
                "settings_path": "/manage/settings",
            },
        ) from exc
    finally:
        if probe_path is not None:
            try:
                probe_path.unlink(missing_ok=True)
            except OSError:
                pass

    if expected_root is not None:
        expected = expected_root.expanduser().resolve(strict=False)
        if expected != resolved:
            raise DownloadRootError(
                "download.root 已修改，需要重启网站后才能创建任务。",
                code="download_root_restart_required",
                details={
                    "path": str(resolved),
                    "running_path": str(expected),
                    "settings_path": "/manage/settings",
                },
            )

    try:
        free_bytes = int(shutil.disk_usage(resolved).free)
    except OSError:
        free_bytes = 0
    return {
        "path": str(resolved),
        "available": True,
        "writable": True,
        "free_bytes": free_bytes,
    }


class DownloadRootGuard:
    def __init__(self, settings: Any, expected_root: Optional[Path] = None) -> None:
        self.settings = settings
        self.expected_root = expected_root

    def validate(self) -> dict:
        return validate_download_root(
            self.settings.get("download.root"),
            expected_root=self.expected_root,
        )

    def status(self) -> dict:
        try:
            return self.validate()
        except DownloadRootError as exc:
            return {
                "available": False,
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
