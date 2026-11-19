from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from typing import Callable, Optional, Set


class SleepInhibitor:
    """Reference-counted macOS caffeinate assertion."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        keep_display_awake: bool = False,
        popen_factory: Optional[Callable[..., subprocess.Popen]] = None,
    ) -> None:
        self.enabled = enabled and sys.platform == "darwin"
        self.keep_display_awake = keep_display_awake
        self.supported = (
            sys.platform == "darwin"
            and shutil.which("caffeinate") is not None
        )
        self.popen_factory = popen_factory or subprocess.Popen
        self._owners: Set[str] = set()
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.RLock()

    def acquire(self, owner: str) -> None:
        if not self.enabled or not self.supported:
            return
        with self._lock:
            self._owners.add(owner)
            if (
                self._process is not None
                and self._process.poll() is None
            ):
                return
            command = ["caffeinate", "-i", "-m"]
            if self.keep_display_awake:
                command.append("-d")
            command.extend(["-w", str(os.getpid())])
            try:
                self._process = self.popen_factory(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                print(
                    f"下载防休眠已启用，caffeinate PID={self._process.pid}",
                    flush=True,
                )
            except Exception as exc:
                self._process = None
                print(
                    f"无法启动防休眠，继续执行下载：{exc}",
                    file=sys.stderr,
                )

    def release(self, owner: str) -> None:
        if not self.enabled or not self.supported:
            return
        with self._lock:
            self._owners.discard(owner)
            if self._owners:
                return
            self._stop_process()

    def release_all(self) -> None:
        with self._lock:
            self._owners.clear()
            self._stop_process()

    def status(self) -> dict:
        with self._lock:
            active = bool(
                self._process is not None
                and self._process.poll() is None
            )
            return {
                "enabled": self.enabled,
                "supported": self.supported,
                "active": active,
                "keep_display_awake": self.keep_display_awake,
                "owners": sorted(self._owners),
            }

    def _stop_process(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        except Exception:
            pass
        print("下载防休眠已释放", flush=True)
