from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse


class ChromeCDPError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


_launch_lock = threading.Lock()


def cdp_is_available(cdp_url: str, timeout: float = 1.0) -> bool:
    endpoint = str(cdp_url or "").rstrip("/")
    if not endpoint:
        return False
    try:
        with urllib.request.urlopen(
            endpoint + "/json/version",
            timeout=timeout,
        ) as response:
            return response.status == 200
    except Exception:
        return False


def ensure_chrome_cdp(
    cdp_url: str,
    *,
    profile_dir: Optional[Path] = None,
    wait_seconds: float = 20.0,
    launcher: Optional[Callable[[list[str]], None]] = None,
    availability_check: Callable[[str, float], bool] = cdp_is_available,
    sleeper: Callable[[float], None] = time.sleep,
) -> bool:
    """Launch a local Chrome CDP endpoint when it is not already running."""
    if availability_check(cdp_url, 1.0):
        return False

    parsed = urlparse(str(cdp_url or ""))
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise ChromeCDPError(
            f"CDP 地址不可用且不是本机地址，无法自动启动 Chrome：{cdp_url}"
        )
    try:
        port = parsed.port or 9222
    except ValueError as exc:
        raise ChromeCDPError(f"CDP 地址端口无效：{cdp_url}") from exc

    configured_profile = os.environ.get("COMIC_CHROME_PROFILE_DIR", "").strip()
    profile = (
        profile_dir.expanduser()
        if profile_dir is not None
        else Path(configured_profile).expanduser()
        if configured_profile
        else Path.home() / ".comic-chrome-profile"
    ).resolve()
    profile.mkdir(parents=True, exist_ok=True)
    args = [
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--hide-crash-restore-bubble",
        "about:blank",
    ]
    if sys.platform == "darwin":
        command = ["open", "-na", "Google Chrome", "--args", *args]
    else:
        chrome = _find_chrome()
        command = [str(chrome), *args]

    with _launch_lock:
        if availability_check(cdp_url, 1.0):
            return False
        try:
            if launcher is not None:
                launcher(command)
            else:
                subprocess.run(
                    command,
                    check=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except Exception as exc:
            raise ChromeCDPError(f"启动 Chrome CDP 失败：{exc}") from exc

        deadline = time.monotonic() + max(1.0, wait_seconds)
        while time.monotonic() < deadline:
            if availability_check(cdp_url, 0.5):
                return True
            sleeper(0.25)

    raise ChromeCDPError(
        f"Chrome 已启动，但 CDP 端口在 {wait_seconds:g} 秒内未就绪：{cdp_url}"
    )


def _find_chrome() -> Path:
    candidates = [
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/usr/bin/google-chrome"),
        Path("/usr/bin/google-chrome-stable"),
        Path("/usr/bin/chromium"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ChromeCDPError("找不到可启动的 Google Chrome")
