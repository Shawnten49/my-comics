#!/usr/bin/env python3
from __future__ import annotations

import argparse
import errno
import hmac
import ipaddress
import json
import mimetypes
import os
import re
import signal
import socket
import ssl
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs, quote, unquote, urlparse

from PIL import Image

try:
    from .auth_service import AuthError, AuthService, CurrentUser
    from .book_repository import BookRepository
    from .batch_download_repository import BatchDownloadRepository
    from .batch_download_worker import BatchDownloadWorker
    from .database import Database
    from .deletion_repository import DeletionError, DeletionRepository
    from .deletion_worker import DeletionWorker
    from .download_repository import DownloadError, DownloadRepository
    from .download_root import DownloadRootError, DownloadRootGuard
    from .download_worker import DownloadWorker
    from .favorites import FavoriteStore
    from .incremental_repository import IncrementalPullRepository
    from .indexer import BookEntry, ComicLibrary
    from .migration_repository import (
        MigrationError,
        MigrationRepository,
    )
    from .migration_worker import MigrationWorker
    from .settings_repository import SettingsError, SettingsRepository
    from .sleep_inhibitor import SleepInhibitor
    from .storage import (
        BookContentMissing,
        InvalidStoragePath,
        StorageAccessError,
        StorageRegistry,
    )
    from .thumbnails import ThumbnailCache
except ImportError:
    from auth_service import AuthError, AuthService, CurrentUser
    from book_repository import BookRepository
    from batch_download_repository import BatchDownloadRepository
    from batch_download_worker import BatchDownloadWorker
    from database import Database
    from deletion_repository import DeletionError, DeletionRepository
    from deletion_worker import DeletionWorker
    from download_repository import DownloadError, DownloadRepository
    from download_root import DownloadRootError, DownloadRootGuard
    from download_worker import DownloadWorker
    from favorites import FavoriteStore
    from incremental_repository import IncrementalPullRepository
    from indexer import BookEntry, ComicLibrary
    from migration_repository import MigrationError, MigrationRepository
    from migration_worker import MigrationWorker
    from settings_repository import SettingsError, SettingsRepository
    from sleep_inhibitor import SleepInhibitor
    from storage import (
        BookContentMissing,
        InvalidStoragePath,
        StorageAccessError,
        StorageRegistry,
    )
    from thumbnails import ThumbnailCache


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE = Path(__file__).with_name("library.db")
STATIC_DIR = Path(__file__).with_name("static")
LAN_ALLOWED_CIDRS_DEFAULT = (
    "127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,"
    "169.254.0.0/16,::1/128,fc00::/7,fe80::/10"
)
DEFAULT_BOOTSTRAP_ADMIN_USERNAME = "admin"
DEFAULT_BOOTSTRAP_ADMIN_PASSWORD = "admin123"
DEFAULT_BOOTSTRAP_VERIFICATION_CODE = "123456"


def detect_lan_ip() -> str:
    candidates = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.168.1.1", 80))
            candidates.append(sock.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            if info[0] == socket.AF_INET:
                candidates.append(info[4][0])
    except OSError:
        pass
    for value in candidates:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if address.is_private and not address.is_loopback:
            return str(address)
    raise RuntimeError(
        "未检测到局域网 IPv4 地址，请使用 --lan-address 指定。"
    )


def parse_networks(value: str) -> list:
    return [
        ipaddress.ip_network(item.strip(), strict=False)
        for item in str(value or "").split(",")
        if item.strip()
    ]


def ensure_lan_certificate(
    directory: Path,
    lan_ip: str,
    hostname: str = "",
) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    cert = directory / "lan-cert.pem"
    key = directory / "lan-key.pem"
    marker = directory / "lan-ip.txt"
    identity = f"{lan_ip}\n{hostname}\n"
    if cert.is_file() and key.is_file() and marker.is_file():
        if marker.read_text(encoding="utf-8") == identity:
            return cert, key
    sans = [
        f"IP:{lan_ip}",
        "IP:127.0.0.1",
        "DNS:localhost",
    ]
    if hostname:
        sans.append(f"DNS:{hostname}")
    command = [
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-sha256",
        "-nodes",
        "-days",
        "825",
        "-keyout",
        str(key),
        "-out",
        str(cert),
        "-subj",
        f"/CN={lan_ip}",
        "-addext",
        f"subjectAltName={','.join(sans)}",
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"无法生成局域网 TLS 证书：{completed.stderr.strip()}"
        )
    try:
        key.chmod(0o600)
    except OSError:
        pass
    marker.write_text(identity, encoding="utf-8")
    return cert, key


def migration_message(record: dict) -> str:
    status = record.get("status", "")
    if status in {"completed", "completed_with_warning"}:
        return "success"
    if status in {"failed", "blocked_external", "interrupted"}:
        return str(record.get("error_message") or "迁移失败")
    return ""


class ComicHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: Tuple[str, int],
        library: ComicLibrary,
        cache: ThumbnailCache,
        favorites: FavoriteStore,
        migrations: MigrationRepository,
        worker: MigrationWorker,
        downloads: Optional[DownloadRepository] = None,
        download_worker: Optional[DownloadWorker] = None,
        batch_downloads: Optional[BatchDownloadRepository] = None,
        batch_download_worker: Optional[BatchDownloadWorker] = None,
        incremental_pulls: Optional[IncrementalPullRepository] = None,
        deletions: Optional[DeletionRepository] = None,
        deletion_worker: Optional[DeletionWorker] = None,
        auth: Optional[AuthService] = None,
        allow_migration: bool = True,
        lan_mode: bool = False,
        allowed_networks: Optional[list] = None,
        allowed_hosts: Optional[set] = None,
        use_https: bool = False,
        sleep_inhibitor: Optional[SleepInhibitor] = None,
        settings: Optional[SettingsRepository] = None,
    ) -> None:
        self.library = library
        self.thumbnail_cache = cache
        self.favorites = favorites
        self.migrations = migrations
        self.worker = worker
        self.downloads = downloads
        self.download_worker = download_worker
        self.batch_downloads = batch_downloads
        self.batch_download_worker = batch_download_worker
        self.incremental_pulls = incremental_pulls
        self.deletions = deletions
        self.deletion_worker = deletion_worker
        self.auth = auth
        self.allow_migration = allow_migration
        self.lan_mode = lan_mode
        self.allowed_networks = allowed_networks or []
        self.allowed_hosts = {
            str(item).lower() for item in (allowed_hosts or set())
        }
        self.use_https = use_https
        self.sleep_inhibitor = sleep_inhibitor
        self.settings = settings
        self.dimension_cache: Dict[str, Tuple[int, int]] = {}
        self.dimension_lock = threading.Lock()
        super().__init__(server_address, ComicRequestHandler)


class ComicRequestHandler(BaseHTTPRequestHandler):
    server: ComicHTTPServer

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(
            f"[{self.log_date_time_string()}] {fmt % args}\n"
        )

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'",
        )
        super().end_headers()

    def _check_lan_access(self) -> bool:
        if not self.server.lan_mode:
            return True
        try:
            address = ipaddress.ip_address(self.client_address[0])
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                address = address.ipv4_mapped
        except ValueError:
            address = None
        if address is None or not any(
            address in network for network in self.server.allowed_networks
        ):
            self._send_error_json(
                HTTPStatus.FORBIDDEN,
                "来源地址不在允许的局域网范围。",
                code="lan_access_denied",
            )
            return False
        host_header = self.headers.get("Host", "")
        try:
            hostname = urlparse("//" + host_header).hostname or ""
        except ValueError:
            hostname = ""
        if hostname.lower().rstrip(".") not in self.server.allowed_hosts:
            self._send_error_json(
                HTTPStatus.MISDIRECTED_REQUEST,
                "Host 不在允许范围。",
                code="invalid_host",
            )
            return False
        return True

    def _session_token(self) -> str:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return ""
        morsel = cookie.get("comic_session")
        return morsel.value if morsel else ""

    def _current_user(self) -> CurrentUser:
        if hasattr(self, "_auth_user_cache"):
            return self._auth_user_cache
        if self.server.auth is None:
            self._auth_user_cache = CurrentUser(None, "", "admin", "")
        else:
            self._auth_user_cache = self.server.auth.resolve(
                self._session_token()
            )
        return self._auth_user_cache

    def _set_session_cookie(self, token: str) -> None:
        secure = "; Secure" if self.server.use_https else ""
        self._extra_headers = [
            (
                "Set-Cookie",
                f"comic_session={token}; Path=/; HttpOnly; SameSite=Lax; "
                f"Max-Age=604800{secure}",
            )
        ]

    def _clear_session_cookie(self) -> None:
        secure = "; Secure" if self.server.use_https else ""
        self._extra_headers = [
            (
                "Set-Cookie",
                "comic_session=; Path=/; HttpOnly; SameSite=Lax; "
                f"Max-Age=0{secure}",
            )
        ]

    def _require_admin(self) -> Optional[CurrentUser]:
        user = self._current_user()
        if user.must_change_password:
            self._send_error_json(
                HTTPStatus.FORBIDDEN,
                "请先修改默认管理员密码。",
                code="password_change_required",
            )
            return None
        if not user.is_admin:
            self._send_error_json(
                HTTPStatus.FORBIDDEN if user.is_authenticated else HTTPStatus.UNAUTHORIZED,
                "需要管理员权限。",
                code="admin_required",
            )
            return None
        return user

    def _require_user(self) -> Optional[CurrentUser]:
        user = self._current_user()
        if not user.is_authenticated:
            self._send_error_json(
                HTTPStatus.UNAUTHORIZED,
                "请先登录。",
                code="authentication_required",
            )
            return None
        return user

    def _require_csrf(self, user: CurrentUser) -> bool:
        if self.server.auth is None:
            return True
        supplied = self.headers.get("X-CSRF-Token", "")
        if not supplied or not hmac.compare_digest(supplied, user.csrf_token):
            self._send_error_json(
                HTTPStatus.FORBIDDEN,
                "CSRF 校验失败。",
                code="csrf_invalid",
            )
            return False
        return True

    def do_HEAD(self) -> None:
        self._handle(head_only=True)

    def do_GET(self) -> None:
        self._handle(head_only=False)

    def do_POST(self) -> None:
        if not self._check_lan_access():
            return
        parsed = urlparse(self.path)
        if parsed.path == "/api/auth/login":
            try:
                payload = self._read_json_body()
                user, token = self.server.auth.login(
                    str(payload.get("username") or ""),
                    str(payload.get("password") or ""),
                    self.client_address[0],
                    self.headers.get("User-Agent", ""),
                )
                self._set_session_cookie(token)
                self._send_json({"ok": True, **user.public_dict()})
            except AuthError as exc:
                self._send_error_json(
                    HTTPStatus(exc.http_status),
                    exc.message,
                    code=exc.code,
                )
            return
        if parsed.path == "/api/auth/logout":
            user = self._current_user()
            if not user.is_authenticated:
                self._clear_session_cookie()
                self._send_json({"ok": True})
                return
            if not self._require_csrf(user):
                return
            self.server.auth.logout(self._session_token())
            self._clear_session_cookie()
            self._send_json({"ok": True})
            return
        if parsed.path == "/api/auth/password":
            user = self._current_user()
            if not user.is_authenticated:
                self._send_error_json(
                    HTTPStatus.UNAUTHORIZED,
                    "请先登录。",
                    code="authentication_required",
                )
                return
            if not self._require_csrf(user):
                return
            try:
                payload = self._read_json_body()
                result = self.server.auth.change_password(
                    int(user.id),
                    str(payload.get("current_password") or ""),
                    str(payload.get("new_password") or ""),
                )
                self._clear_session_cookie()
                self._send_json(result)
            except AuthError as exc:
                self._send_error_json(
                    HTTPStatus(int(exc.http_status)),
                    exc.message,
                    code=exc.code,
                )
            return
        if parsed.path == "/api/auth/setup":
            if self.client_address[0] not in {"127.0.0.1", "::1"}:
                self._send_error_json(HTTPStatus.FORBIDDEN, "仅允许本机初始化。")
                return
            try:
                payload = self._read_json_body()
                result = self.server.auth.setup_admin(
                    str(payload.get("setup_token") or ""),
                    str(payload.get("username") or ""),
                    str(payload.get("password") or ""),
                )
                self._send_json({"ok": True, "user": result})
            except AuthError as exc:
                self._send_error_json(
                    HTTPStatus(exc.http_status),
                    exc.message,
                    code=exc.code,
                )
            return
        if (
            parsed.path.startswith("/api/manage/")
            or re.fullmatch(
                r"/api/series/[^/]+/delete-tasks",
                unquote(parsed.path),
            )
        ):
            self._handle_manage_post(parsed)
            return
        self._send_error_json(HTTPStatus.NOT_FOUND, "Not found")

    def do_PUT(self) -> None:
        if not self._check_lan_access():
            return
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        match = re.fullmatch(r"/api/books/([^/]+)/cover", path)
        if match:
            user = self._require_admin()
            if user is None or not self._require_csrf(user):
                return
            try:
                payload = self._read_json_body()
                filename = str(payload.get("filename") or "").strip()
                if not filename:
                    raise ValueError("缺少 filename")
                book = self.server.library.set_cover(match.group(1), filename)
                self.server.thumbnail_cache.clear_all()
                self._send_json(
                    {
                        "ok": True,
                        "book": book.public_dict(),
                    }
                )
            except KeyError:
                self._send_error_json(HTTPStatus.NOT_FOUND, "Book not found")
            except StorageAccessError as exc:
                self._send_storage_error(exc)
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return

        match = re.fullmatch(r"/api/books/([^/]+)/favorite", path)
        if match:
            try:
                user = self._require_user()
                if user is None or not self._require_csrf(user):
                    return
                payload = self._read_json_body()
                if "favorite" not in payload:
                    raise ValueError("缺少 favorite")
                book_id = match.group(1)
                book = self.server.library.get_metadata(book_id)
                if (
                    book is None
                    or int(book.access_level) > user.max_access_level
                ):
                    raise KeyError(book_id)
                favorite = bool(payload.get("favorite"))
                if self.server.auth is None:
                    self.server.favorites.set_favorite(book_id, favorite)
                    favorite_count = self.server.favorites.count()
                else:
                    self.server.auth.set_favorite(user.id, book_id, favorite)
                    favorite_count = self.server.auth.favorite_count(user.id)
                self._send_json(
                    {
                        "ok": True,
                        "book_id": book_id,
                        "favorite": favorite,
                        "favorite_count": favorite_count,
                    }
                )
            except KeyError:
                self._send_error_json(HTTPStatus.NOT_FOUND, "Book not found")
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return

        if path == "/api/categories/blocked":
            user = self._require_admin()
            if user is None or not self._require_csrf(user):
                return
            try:
                payload = self._read_json_body()
                name = str(payload.get("name") or "").strip()
                blocked = bool(payload.get("blocked"))
                category = self.server.library.set_category_blocked(
                    name,
                    blocked,
                )
                self._send_json(
                    {
                        "ok": True,
                        "category": category,
                        "categories": self.server.library.categories(),
                    }
                )
            except ValueError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            except (OSError, json.JSONDecodeError) as exc:
                self._send_error_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    str(exc),
                )
            return

        self._send_error_json(HTTPStatus.NOT_FOUND, "Not found")

    def do_DELETE(self) -> None:
        if not self._check_lan_access():
            return
        user = self._require_admin()
        if user is None or not self._require_csrf(user):
            return
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        match = re.fullmatch(r"/api/books/([^/]+)/cover", path)
        if not match:
            self._send_error_json(HTTPStatus.NOT_FOUND, "Not found")
            return
        try:
            book = self.server.library.reset_cover(match.group(1))
            self._send_json(
                {
                    "ok": True,
                    "book": book.public_dict(),
                }
            )
        except KeyError:
            self._send_error_json(HTTPStatus.NOT_FOUND, "Book not found")
        except StorageAccessError as exc:
            self._send_storage_error(exc)
        except OSError as exc:
            self._send_error_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                str(exc),
            )

    def _handle(self, head_only: bool) -> None:
        if not self._check_lan_access():
            return
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)

        if path == "/api/auth/session":
            self._send_json(self._current_user().public_dict(), head_only)
            return

        if path == "/login" or path == "/setup":
            self._static("login.html", head_only)
            return
        if path == "/change-password":
            self._static("change_password.html", head_only)
            return

        if path.startswith("/api/manage/"):
            if self._require_admin() is None:
                return
            self._handle_manage_get(path, query, head_only)
            return

        if path == "/api/books":
            self._books(query, head_only)
            return

        if path == "/api/favorites/count":
            user = self._require_user()
            if user is None:
                return
            self._send_json(
                {
                    "count": (
                        self.server.auth.favorite_count(user.id)
                        if self.server.auth is not None
                        else self.server.favorites.count()
                    )
                },
                head_only,
            )
            return

        if path == "/api/favorites":
            user = self._require_user()
            if user is None:
                return
            self._favorites(query, head_only)
            return

        if path == "/api/categories":
            if self._require_admin() is None:
                return
            self._send_json(
                {
                    "items": self.server.library.categories(),
                },
                head_only,
            )
            return

        if re.fullmatch(r"/api/books/[^/]+/cover-options", path):
            if self._require_admin() is None:
                return

        user = self._current_user()
        access_match = re.fullmatch(
            r"/(?:api/books|api/series|media)/([^/]+)(?:/.*)?",
            path,
        )
        if access_match:
            book = self.server.library.get_metadata(access_match.group(1))
            if (
                book is None
                or int(book.access_level) > user.max_access_level
            ):
                self._send_error_json(
                    HTTPStatus.NOT_FOUND,
                    "Not found",
                    code="not_found",
                )
                return

        match = re.fullmatch(r"/api/series/([^/]+)/chapters", path)
        if match:
            self._series_chapters(match.group(1), query, head_only)
            return

        match = re.fullmatch(r"/api/series/([^/]+)", path)
        if match:
            self._series_detail(match.group(1), head_only)
            return

        match = re.fullmatch(
            r"/api/books/([^/]+)/chapters/(\d+)/pages",
            path,
        )
        if match:
            self._chapter_pages(
                match.group(1),
                int(match.group(2)),
                query,
                head_only,
            )
            return

        match = re.fullmatch(
            r"/api/books/([^/]+)/chapters/(\d+)",
            path,
        )
        if match:
            self._chapter_detail(
                match.group(1),
                int(match.group(2)),
                head_only,
            )
            return

        match = re.fullmatch(r"/api/books/([^/]+)/cover-file", path)
        if match:
            self._book_cover_file(match.group(1), head_only)
            return

        match = re.fullmatch(r"/api/books/([^/]+)", path)
        if match:
            self._book_detail(match.group(1), head_only)
            return

        match = re.fullmatch(r"/api/books/([^/]+)/pages", path)
        if match:
            self._book_pages(match.group(1), query, head_only)
            return

        match = re.fullmatch(
            r"/api/books/([^/]+)/pages/(\d+)/thumbnail",
            path,
        )
        if match:
            self._page_thumbnail(
                match.group(1),
                int(match.group(2)),
                query,
                head_only,
            )
            return

        match = re.fullmatch(r"/api/books/([^/]+)/cover-options", path)
        if match:
            self._cover_options(
                match.group(1),
                query,
                head_only,
            )
            return

        match = re.fullmatch(r"/api/books/([^/]+)/thumbnail", path)
        if match:
            self._thumbnail(match.group(1), query, head_only)
            return

        match = re.fullmatch(
            r"/media/([^/]+)/chapter/(\d+)/(.+)",
            path,
        )
        if match:
            self._chapter_media(
                match.group(1),
                int(match.group(2)),
                match.group(3),
                head_only,
            )
            return

        match = re.fullmatch(r"/media/([^/]+)/(.+)", path)
        if match:
            self._media(match.group(1), match.group(2), head_only)
            return

        if path == "/api/health":
            self._send_json(
                {
                    "ok": True,
                    "pid": os.getpid(),
                    "books": len(self.server.library.books()),
                    "features": [
                        "manage_migrations",
                        "download_management",
                        "batch_download",
                        "incremental_pull",
                        "deletion_management",
                        "auth_roles",
                        "series_chapters",
                        "system_settings",
                    ],
                    "sleep_inhibitor": (
                        self.server.sleep_inhibitor.status()
                        if self.server.sleep_inhibitor is not None
                        else {
                            "enabled": False,
                            "supported": False,
                            "active": False,
                            "owners": [],
                        }
                    ),
                },
                head_only,
            )
            return

        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return

        if (
            path == "/"
            or path.startswith("/reader/")
            or path.startswith("/series/")
            or path.startswith("/category/")
            or path == "/favorites"
        ):
            self._static("index.html", head_only)
            return
        if path.startswith("/manage/"):
            self._static("manage.html", head_only)
            return
        if path.startswith("/static/"):
            self._static(path[len("/static/"):], head_only)
            return

        self._send_error_json(HTTPStatus.NOT_FOUND, "Not found")

    def _require_incremental_pulls(self) -> IncrementalPullRepository:
        if self.server.incremental_pulls is None:
            raise DownloadError(
                "增量拉取管理未启用。",
                code="incremental_pull_disabled",
                http_status=503,
            )
        return self.server.incremental_pulls

    def _require_deletions(self) -> DeletionRepository:
        if self.server.deletions is None:
            raise DeletionError(
                "删除管理未启用。",
                code="deletion_management_disabled",
                http_status=503,
            )
        return self.server.deletions

    def _handle_manage_get(
        self,
        path: str,
        query: dict,
        head_only: bool,
    ) -> None:
        if not self.server.allow_migration:
            self._send_error_json(
                HTTPStatus.FORBIDDEN,
                "管理功能只允许在本机回环地址使用。",
                code="migration_disabled",
            )
            return
        try:
            if path == "/api/manage/books":
                self._manage_books(query, head_only)
                return
            if path == "/api/manage/downloads":
                self._manage_downloads(query, head_only)
                return
            if path == "/api/manage/downloads/root-status":
                if self.server.downloads is None:
                    raise DownloadError(
                        "下载管理未启用。",
                        code="download_management_disabled",
                        http_status=503,
                    )
                self._send_json(
                    self.server.downloads.validate_output_root(),
                    head_only,
                )
                return
            match = re.fullmatch(
                r"/api/manage/downloads/batch/analyze/(ba_[a-f0-9]{32})",
                path,
            )
            if match:
                if self.server.batch_downloads is None:
                    raise DownloadError(
                        "批量下载管理未启用。",
                        code="batch_download_disabled",
                        http_status=503,
                    )
                page_size = self._int_param(
                    query,
                    "page_size",
                    50,
                    minimum=1,
                    maximum=200,
                )
                page = self._int_param(query, "page", 1, minimum=1)
                result = self.server.batch_downloads.get_preview(
                    match.group(1),
                    page=page,
                    page_size=page_size,
                    search=(query.get("q") or [""])[0],
                )
                self._send_json(result, head_only)
                return
            if path == "/api/manage/downloads/batch/tasks":
                self._manage_batch_downloads(query, head_only)
                return
            if path == "/api/manage/downloads/incremental":
                self._manage_incremental_pulls(query, head_only)
                return
            if path == "/api/manage/deletions":
                self._manage_deletions(query, head_only)
                return
            if path == "/api/manage/users":
                self._send_json(
                    {"items": self.server.auth.list_users()},
                    head_only,
                )
                return
            if path == "/api/manage/settings":
                if self.server.settings is None:
                    raise SettingsError(
                        "系统设置未启用。",
                        code="settings_disabled",
                        http_status=503,
                    )
                self._send_json(
                    {"items": self.server.settings.list_settings()},
                    head_only,
                )
                return
            if path == "/api/manage/storages":
                roots = []
                for root in self.server.library.storage.list_roots():
                    probe = self.server.library.storage.probe(root.id)
                    roots.append(
                        {
                            "id": root.id,
                            "root_path": root.root_path,
                            "display_name": root.display_name,
                            "status": probe.status,
                            "error": probe.error,
                            "last_seen_at": root.last_seen_at,
                        }
                    )
                self._send_json({"items": roots}, head_only)
                return
            if path == "/api/manage/migrations":
                self._manage_migration_records(query, head_only)
                return
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_error_json(
                HTTPStatus.BAD_REQUEST,
                str(exc),
                code="invalid_request",
            )
            return
        except MigrationError as exc:
            self._send_migration_error(exc)
            return
        except DownloadError as exc:
            self._send_download_error(exc)
            return
        except DownloadRootError as exc:
            self._send_error_json(
                HTTPStatus(int(exc.http_status)),
                exc.message,
                code=exc.code,
                details=exc.details,
            )
            return
        except DeletionError as exc:
            self._send_deletion_error(exc)
            return
        except AuthError as exc:
            self._send_error_json(
                HTTPStatus(int(exc.http_status)),
                exc.message,
                code=exc.code,
            )
            return
        except SettingsError as exc:
            self._send_error_json(
                HTTPStatus(int(exc.http_status)),
                exc.message,
                code=exc.code,
            )
            return
        self._send_error_json(HTTPStatus.NOT_FOUND, "Not found")

    def _handle_manage_post(self, parsed) -> None:
        user = self._require_admin()
        if user is None or not self._require_csrf(user):
            return
        if not self.server.allow_migration:
            self._send_error_json(
                HTTPStatus.FORBIDDEN,
                "管理功能只允许在本机回环地址使用。",
                code="migration_disabled",
            )
            return
        path = unquote(parsed.path)
        try:
            payload = self._read_json_body()
            if path == "/api/manage/users":
                result = self.server.auth.create_user(
                    str(payload.get("username") or ""),
                    str(payload.get("password") or ""),
                    str(payload.get("role") or "user"),
                )
                self._send_json({"ok": True, **result})
                return
            match = re.fullmatch(
                r"/api/manage/settings/([A-Za-z0-9._-]+)",
                path,
            )
            if match:
                if self.server.settings is None:
                    raise SettingsError(
                        "系统设置未启用。",
                        code="settings_disabled",
                        http_status=503,
                    )
                key = match.group(1)
                result = self.server.settings.update_from_payload(
                    key,
                    payload,
                )
                self.server.auth.audit(
                    user.id,
                    user.username,
                    "setting.update",
                    "setting",
                    key,
                    "updated",
                    self.client_address[0],
                )
                self._send_json({"ok": True, **result})
                return
            match = re.fullmatch(
                r"/api/manage/users/(\d+)",
                path,
            )
            if match:
                role = payload.get("role")
                active = payload.get("active")
                result = self.server.auth.set_user(
                    int(match.group(1)),
                    role=str(role) if role is not None else None,
                    active=bool(active) if active is not None else None,
                )
                self._send_json({"ok": True, **result})
                return
            match = re.fullmatch(
                r"/api/manage/books/([^/]+)/access-level",
                path,
            )
            if match:
                try:
                    access_level = int(payload.get("access_level"))
                except (TypeError, ValueError) as exc:
                    raise AuthError(
                        "访问级别必须是 0、1 或 2。",
                        code="invalid_access_level",
                    ) from exc
                if access_level not in {0, 1, 2}:
                    raise AuthError(
                        "访问级别必须是 0、1 或 2。",
                        code="invalid_access_level",
                    )
                with self.server.library.database.connect() as connection:
                    connection.execute(
                        "UPDATE books SET access_level=?, updated_at=? WHERE id=?",
                        (
                            access_level,
                            datetime.now().isoformat(timespec="seconds"),
                            match.group(1),
                        ),
                    )
                    connection.commit()
                self.server.auth.audit(
                    user.id,
                    user.username,
                    "book.access_level.update",
                    "book",
                    match.group(1),
                    str(access_level),
                    self.client_address[0],
                )
                self._send_json(
                    {"ok": True, "book_id": match.group(1), "access_level": access_level}
                )
                return
            if path == "/api/manage/downloads":
                if self.server.downloads is None:
                    raise DownloadError(
                        "下载管理未启用。",
                        code="download_management_disabled",
                        http_status=503,
                    )
                result = self.server.downloads.create_task(
                    str(payload.get("comic_id") or ""),
                    str(payload.get("download_url") or ""),
                    payload.get("access_level", 0),
                )
                if self.server.download_worker is not None:
                    self.server.download_worker.notify()
                self._send_json({"ok": True, **result})
                return
            if path == "/api/manage/downloads/batch/analyze":
                if self.server.batch_downloads is None:
                    raise DownloadError(
                        "批量下载管理未启用。",
                        code="batch_download_disabled",
                        http_status=503,
                    )
                result = self.server.batch_downloads.create_analysis(
                    str(payload.get("parent_id") or ""),
                    str(payload.get("comic_name") or ""),
                    str(payload.get("series_url") or ""),
                    payload.get("access_level", 0),
                )
                self._send_json({"ok": True, **result})
                return
            if path == "/api/manage/downloads/batch":
                if self.server.batch_downloads is None:
                    raise DownloadError(
                        "批量下载管理未启用。",
                        code="batch_download_disabled",
                        http_status=503,
                    )
                chapter_keys = payload.get("chapter_keys")
                if not isinstance(chapter_keys, list):
                    raise DownloadError(
                        "chapter_keys 必须是数组。",
                        code="invalid_chapter_keys",
                    )
                result = self.server.batch_downloads.create_tasks(
                    str(payload.get("analysis_id") or ""),
                    chapter_keys,
                )
                if self.server.batch_download_worker is not None:
                    self.server.batch_download_worker.notify()
                self._send_json({"ok": True, **result})
                return
            match = re.fullmatch(
                r"/api/manage/downloads/incremental/(\d+)/preview",
                path,
            )
            if match:
                self._require_incremental_pulls()
                result = self.server.incremental_pulls.preview_new_chapters(
                    int(match.group(1))
                )
                self._send_json({"ok": True, **result})
                return
            match = re.fullmatch(
                r"/api/manage/downloads/incremental/(\d+)/status",
                path,
            )
            if match:
                self._require_incremental_pulls()
                try:
                    status = int(payload.get("comic_status"))
                except (TypeError, ValueError) as exc:
                    raise DownloadError(
                        "comic_status 必须是 0 或 1。",
                        code="invalid_comic_status",
                    ) from exc
                result = self.server.incremental_pulls.set_comic_status(
                    int(match.group(1)),
                    status,
                )
                self._send_json({"ok": True, **result})
                return
            match = re.fullmatch(
                r"/api/manage/books/([^/]+)/delete-preview",
                path,
            )
            if match:
                repository = self._require_deletions()
                chapter_ids = payload.get("chapter_ids")
                result = repository.preview_delete(
                    match.group(1),
                    chapter_ids if isinstance(chapter_ids, list) else None,
                )
                self._send_json({"ok": True, **result})
                return
            match = re.fullmatch(
                r"/api/manage/books/([^/]+)/delete-tasks",
                path,
            )
            if match:
                repository = self._require_deletions()
                result = repository.create_book_tasks(
                    match.group(1),
                    str(payload.get("verification_code") or ""),
                    client_key=self.client_address[0],
                )
                if self.server.deletion_worker is not None:
                    self.server.deletion_worker.notify()
                self._send_json({"ok": True, **result})
                return
            match = re.fullmatch(
                r"/api/series/([^/]+)/delete-tasks",
                path,
            )
            if match:
                repository = self._require_deletions()
                chapter_ids = payload.get("chapter_ids")
                if not isinstance(chapter_ids, list):
                    raise DeletionError(
                        "chapter_ids 必须是数组。",
                        code="invalid_chapter_ids",
                    )
                result = repository.create_chapter_tasks(
                    match.group(1),
                    chapter_ids,
                    str(payload.get("verification_code") or ""),
                    client_key=self.client_address[0],
                )
                if self.server.deletion_worker is not None:
                    self.server.deletion_worker.notify()
                self._send_json({"ok": True, **result})
                return
            match = re.fullmatch(
                r"/api/manage/deletions/(\d+)/retry",
                path,
            )
            if match:
                repository = self._require_deletions()
                result = repository.retry_task(int(match.group(1)))
                if self.server.deletion_worker is not None:
                    self.server.deletion_worker.notify()
                self._send_json({"ok": True, **result})
                return
            match = re.fullmatch(
                r"/api/manage/downloads/(\d+)/retry",
                path,
            )
            if match:
                if self.server.downloads is None:
                    raise DownloadError(
                        "下载管理未启用。",
                        code="download_management_disabled",
                        http_status=503,
                    )
                result = self.server.downloads.retry_task(int(match.group(1)))
                if self.server.download_worker is not None:
                    self.server.download_worker.notify()
                self._send_json({"ok": True, **result})
                return
            match = re.fullmatch(
                r"/api/manage/downloads/(\d+)/recrawl",
                path,
            )
            if match:
                if self.server.downloads is None:
                    raise DownloadError(
                        "下载管理未启用。",
                        code="download_management_disabled",
                        http_status=503,
                    )
                result = self.server.downloads.recrawl_task(
                    int(match.group(1))
                )
                if self.server.download_worker is not None:
                    self.server.download_worker.notify()
                self._send_json({"ok": True, **result})
                return
            match = re.fullmatch(
                r"/api/manage/downloads/batch/tasks/(\d+)/retry",
                path,
            )
            if match:
                if self.server.batch_downloads is None:
                    raise DownloadError(
                        "批量下载管理未启用。",
                        code="batch_download_disabled",
                        http_status=503,
                    )
                result = self.server.batch_downloads.retry_task(
                    int(match.group(1))
                )
                if self.server.batch_download_worker is not None:
                    self.server.batch_download_worker.notify()
                self._send_json({"ok": True, **result})
                return
            match = re.fullmatch(
                r"/api/manage/downloads/batch/tasks/(\d+)/recrawl",
                path,
            )
            if match:
                if self.server.batch_downloads is None:
                    raise DownloadError(
                        "批量下载管理未启用。",
                        code="batch_download_disabled",
                        http_status=503,
                    )
                result = self.server.batch_downloads.recrawl_task(
                    int(match.group(1))
                )
                if self.server.batch_download_worker is not None:
                    self.server.batch_download_worker.notify()
                self._send_json({"ok": True, **result})
                return
            if path == "/api/manage/storages/validate":
                destination = str(payload.get("path") or "")
                result = self.server.migrations.validate_destination(destination)
                self._send_json({"ok": True, **result})
                return
            if path == "/api/manage/storages/pick":
                if self.client_address[0] not in {"127.0.0.1", "::1"}:
                    raise MigrationError(
                        "目录选择器只允许本机使用。",
                        code="migration_disabled",
                        http_status=403,
                    )
                selected = self._pick_directory()
                result = self.server.migrations.validate_destination(selected)
                self._send_json(
                    {
                        "ok": True,
                        "path": selected,
                        **result,
                    }
                )
                return
            match = re.fullmatch(
                r"/api/manage/storages/([^/]+)/relocate",
                path,
            )
            if match:
                root = self.server.library.storage.relocate_root(
                    match.group(1),
                    Path(str(payload.get("path") or "")),
                )
                self._send_json(
                    {
                        "ok": True,
                        "storage_id": root.id,
                        "path": root.root_path,
                    }
                )
                return
            if path == "/api/manage/migrations":
                result = self.server.migrations.create_records(
                    str(payload.get("destination_path") or ""),
                    payload.get("book_ids") or [],
                    str(payload.get("verification_code") or ""),
                    client_key=self.client_address[0],
                )
                self.server.worker.notify()
                self._send_json({"ok": True, **result})
                return
            match = re.fullmatch(
                r"/api/manage/migrations/(\d+)/retry",
                path,
            )
            if match:
                result = self.server.migrations.retry_record(
                    int(match.group(1))
                )
                self.server.worker.notify()
                self._send_json({"ok": True, **result})
                return
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_error_json(
                HTTPStatus.BAD_REQUEST,
                str(exc),
                code="invalid_request",
            )
            return
        except MigrationError as exc:
            self._send_migration_error(exc)
            return
        except DownloadError as exc:
            self._send_download_error(exc)
            return
        except DownloadRootError as exc:
            self._send_error_json(
                HTTPStatus(int(exc.http_status)),
                exc.message,
                code=exc.code,
                details=exc.details,
            )
            return
        except DeletionError as exc:
            self._send_deletion_error(exc)
            return
        except StorageAccessError as exc:
            self._send_storage_error(exc)
            return
        except SettingsError as exc:
            self._send_error_json(
                HTTPStatus(int(exc.http_status)),
                exc.message,
                code=exc.code,
            )
            return
        except AuthError as exc:
            self._send_error_json(
                HTTPStatus(int(exc.http_status)),
                exc.message,
                code=exc.code,
            )
            return
        except KeyError:
            self._send_error_json(
                HTTPStatus.NOT_FOUND,
                "迁移记录或存储根不存在。",
                code="not_found",
            )
            return
        self._send_error_json(HTTPStatus.NOT_FOUND, "Not found")

    def _manage_books(self, query: dict, head_only: bool) -> None:
        page_size = self._int_param(
            query,
            "page_size",
            30,
            minimum=1,
            maximum=100,
        )
        page = self._int_param(query, "page", 1, minimum=1)
        search = (query.get("q") or [""])[0]
        storage = (query.get("storage") or [""])[0]
        status = (query.get("status") or [""])[0]
        offset = (page - 1) * page_size
        total, books = self.server.library.managed_books(
            offset,
            page_size,
            search=search,
            storage=storage,
            status=status,
        )
        total_pages = (total + page_size - 1) // page_size
        if total_pages and page > total_pages:
            page = total_pages
            offset = (page - 1) * page_size
            _, books = self.server.library.managed_books(
                offset,
                page_size,
                search=search,
                storage=storage,
                status=status,
            )
        self._send_json(
            {
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "items": [self._public_book(book) for book in books],
            },
            head_only,
        )

    def _manage_downloads(self, query: dict, head_only: bool) -> None:
        if self.server.downloads is None:
            self._send_error_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "下载管理未启用。",
                code="download_management_disabled",
            )
            return
        page_size = self._int_param(
            query,
            "page_size",
            30,
            minimum=1,
            maximum=100,
        )
        page = self._int_param(query, "page", 1, minimum=1)
        search = (query.get("q") or [""])[0]
        status = (query.get("status") or [""])[0]
        total, tasks = self.server.downloads.list_tasks(
            (page - 1) * page_size,
            page_size,
            search=search,
            status=status,
        )
        total_pages = (total + page_size - 1) // page_size
        items = []
        for task in tasks:
            items.append(
                {
                    "id": task["id"],
                    "comic_id": task["comic_id"],
                    "title": task["title"],
                    "download_url": task["download_url"],
                    "status": task["status"],
                    "stage": task["stage"],
                    "output_dir": task["output_dir"],
                    "total_images": task["total_images"],
                    "processed_images": task["processed_images"],
                    "downloaded_images": task["downloaded_images"],
                    "skipped_images": task["skipped_images"],
                    "failed_images": task["failed_images"],
                    "error_message": task["error_message"],
                    "attempt_count": task["attempt_count"],
                    "pull_mode": task.get("pull_mode", "initial"),
                    "recrawl_count": task.get("recrawl_count", 0),
                    "access_level": task.get("access_level", 0),
                    "created_at": task["created_at"],
                    "finished_at": task["finished_at"],
                }
            )
        self._send_json(
            {
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "items": items,
            },
            head_only,
        )

    def _manage_batch_downloads(
        self,
        query: dict,
        head_only: bool,
    ) -> None:
        if self.server.batch_downloads is None:
            self._send_error_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "批量下载管理未启用。",
                code="batch_download_disabled",
            )
            return
        page_size = self._int_param(
            query,
            "page_size",
            30,
            minimum=1,
            maximum=200,
        )
        page = self._int_param(query, "page", 1, minimum=1)
        result = self.server.batch_downloads.list_tasks(
            page=page,
            page_size=page_size,
            search=(query.get("q") or [""])[0],
            status=(query.get("status") or [""])[0],
            batch_no=(query.get("batch_no") or [""])[0],
            parent_id=(query.get("parent_id") or [""])[0],
        )
        items = []
        for task in result["items"]:
            items.append(
                {
                    "id": task["id"],
                    "batch_no": task["batch_no"],
                    "parent_id": task["parent_id"],
                    "comic_name": task["comic_name"],
                    "chapter_key": task["chapter_key"],
                    "chapter_title": task["chapter_title"],
                    "sort_order": task["sort_order"],
                    "download_url": task["download_url"],
                    "status": task["status"],
                    "stage": task["stage"],
                    "output_dir": task["output_dir"],
                    "total_images": task["total_images"],
                    "processed_images": task["processed_images"],
                    "downloaded_images": task["downloaded_images"],
                    "skipped_images": task["skipped_images"],
                    "failed_images": task["failed_images"],
                    "error_code": task["error_code"],
                    "error_message": task["error_message"],
                    "attempt_count": task["attempt_count"],
                    "pull_mode": task.get("pull_mode", "initial"),
                    "recrawl_count": task.get("recrawl_count", 0),
                    "access_level": task.get("access_level", 0),
                    "created_at": task["created_at"],
                    "finished_at": task["finished_at"],
                }
            )
        self._send_json({**result, "items": items}, head_only)

    def _manage_incremental_pulls(
        self,
        query: dict,
        head_only: bool,
    ) -> None:
        repository = self._require_incremental_pulls()
        page_size = self._int_param(
            query,
            "page_size",
            30,
            minimum=1,
            maximum=200,
        )
        page = self._int_param(query, "page", 1, minimum=1)
        result = repository.list_tasks(
            page=page,
            page_size=page_size,
            search=(query.get("q") or [""])[0],
            comic_status=(query.get("comic_status") or [""])[0],
            task_state=(query.get("task_state") or [""])[0],
        )
        self._send_json(result, head_only)

    def _manage_deletions(
        self,
        query: dict,
        head_only: bool,
    ) -> None:
        repository = self._require_deletions()
        result = repository.list_tasks(
            page=self._int_param(query, "page", 1, minimum=1),
            page_size=self._int_param(
                query,
                "page_size",
                30,
                minimum=1,
                maximum=200,
            ),
            search=(query.get("q") or [""])[0],
            status=(query.get("status") or [""])[0],
            task_type=(query.get("task_type") or [""])[0],
            batch_no=(query.get("batch_no") or [""])[0],
        )
        self._send_json(result, head_only)

    def _manage_migration_records(self, query: dict, head_only: bool) -> None:
        page_size = self._int_param(
            query,
            "page_size",
            20,
            minimum=1,
            maximum=100,
        )
        page = self._int_param(query, "page", 1, minimum=1)
        search = (query.get("q") or [""])[0]
        status = (query.get("status") or [""])[0]
        batch_no = (query.get("batch_no") or [""])[0]
        total, items = self.server.migrations.list_records(
            (page - 1) * page_size,
            page_size,
            search=search,
            status=status,
            batch_no=batch_no,
        )
        total_pages = (total + page_size - 1) // page_size
        public_items = []
        for item in items:
            public_items.append(
                {
                    "id": item["id"],
                    "book_id": item["book_id"],
                    "book_title": item["book_title"],
                    "batch_no": item["batch_no"],
                    "source_path": item["source_path"],
                    "destination_path": item["destination_path"],
                    "status": item["status"],
                    "stage": item["stage"],
                    "created_at": item["created_at"],
                    "finished_at": item["finished_at"],
                    "message": migration_message(item),
                    "progress": {
                        "copied_files": item["copied_files"],
                        "total_files": item["total_files"],
                    },
                    "error": item["error_message"],
                }
            )
        self._send_json(
            {
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "items": public_items,
            },
            head_only,
        )

    def _pick_directory(self) -> str:
        script = (
            'POSIX path of (choose folder with prompt '
            '"选择漫画外盘目录")'
        )
        try:
            completed = subprocess.run(
                ["osascript", "-e", script],
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MigrationError(
                f"无法打开本机目录选择器：{exc}",
                code="directory_picker_unavailable",
                http_status=503,
            ) from exc
        selected = completed.stdout.strip()
        if completed.returncode != 0 or not selected:
            raise MigrationError(
                "未选择目录。",
                code="directory_not_selected",
                http_status=400,
            )
        return selected

    def _books(self, query: dict, head_only: bool) -> None:
        user = self._current_user()
        page_size = self._int_param(
            query,
            "page_size",
            self._int_param(query, "limit", 30, minimum=1, maximum=100),
            minimum=1,
            maximum=100,
        )
        page = self._int_param(query, "page", 1, minimum=1)
        page_mode = "page" in query or "page_size" in query
        offset = self._int_param(
            query,
            "offset",
            (page - 1) * page_size,
            minimum=0,
        )
        limit = page_size
        search = (query.get("q") or [""])[0]
        category = (query.get("category") or [""])[0]
        favorites_only = (query.get("favorite") or ["false"])[0].lower() in {
            "1", "true", "yes"
        }
        if category and not user.is_admin:
            self._send_error_json(
                HTTPStatus.FORBIDDEN,
                "需要管理员权限使用分类。",
                code="admin_required",
            )
            return
        if favorites_only and not user.is_authenticated:
            self._send_error_json(
                HTTPStatus.UNAUTHORIZED,
                "请先登录。",
                code="authentication_required",
            )
            return
        total, books = self.server.library.query(
            offset,
            limit,
            search,
            category,
            favorites_only=favorites_only,
            favorite_user_id=user.id if favorites_only else None,
            max_access_level=user.max_access_level,
        )
        total_pages = (total + limit - 1) // limit
        if page_mode and total_pages > 0 and offset >= total:
            page = total_pages
            offset = (page - 1) * limit
            _, books = self.server.library.query(
                offset,
                limit,
                search,
                category,
                favorites_only=favorites_only,
                favorite_user_id=user.id if favorites_only else None,
                max_access_level=user.max_access_level,
            )
        elif page_mode and total_pages == 0:
            page = 1
            offset = 0
        self._send_json(
            {
                "total": total,
                "page": page if page_mode else offset // limit + 1,
                "page_size": limit,
                "total_pages": total_pages,
                "offset": offset,
                "limit": limit,
                "next_offset": offset + len(books),
                "category": category,
                "favorite_count": (
                    self.server.auth.favorite_count(user.id)
                    if user.is_authenticated and self.server.auth is not None
                    else self.server.favorites.count()
                ),
                "items": [self._public_book(book) for book in books],
            },
            head_only,
        )

    def _favorites(self, query: dict, head_only: bool) -> None:
        user = self._require_user()
        if user is None:
            return
        limit = self._int_param(
            query,
            "page_size",
            self._int_param(query, "limit", 30, minimum=1, maximum=100),
            minimum=1,
            maximum=100,
        )
        page = self._int_param(query, "page", 1, minimum=1)
        page_mode = "page" in query or "page_size" in query
        offset = self._int_param(
            query,
            "offset",
            (page - 1) * limit,
            minimum=0,
        )
        search = (query.get("q") or [""])[0].strip().casefold()
        books = []
        entries = (
            self.server.auth.favorite_entries(user.id)
            if self.server.auth is not None
            else self.server.favorites.entries()
        )
        for book_id, added_at in entries:
            book = self.server.library.get_metadata(book_id)
            if book is None:
                continue
            if int(book.access_level) > user.max_access_level:
                continue
            if search and (
                search not in book.title.casefold()
                and search not in book.id
            ):
                continue
            payload = self._public_book(book)
            payload["favorite_added_at"] = added_at
            books.append(payload)
        total_pages = (len(books) + limit - 1) // limit
        if page_mode and total_pages > 0 and offset >= len(books):
            page = total_pages
            offset = (page - 1) * limit
        elif page_mode and total_pages == 0:
            page = 1
            offset = 0
        selected_items = books[offset:offset + limit]
        self._send_json(
            {
                "total": len(books),
                "page": page if page_mode else offset // limit + 1,
                "page_size": limit,
                "total_pages": total_pages,
                "offset": offset,
                "limit": limit,
                "next_offset": offset + len(selected_items),
                "favorite_count": (
                    self.server.auth.favorite_count(user.id)
                    if self.server.auth is not None
                    else self.server.favorites.count()
                ),
                "items": selected_items,
            },
            head_only,
        )

    def _public_book(self, book: BookEntry) -> dict:
        self.server.library.annotate_availability([book])
        payload = book.public_dict()
        user = self._current_user()
        payload["favorite"] = (
            self.server.auth.is_favorite(user.id, book.id)
            if user.is_authenticated and self.server.auth is not None else False
        )
        payload["favorite_added_at"] = ""
        return payload

    def _series_detail(self, book_id: str, head_only: bool) -> None:
        book = self.server.library.get_metadata(book_id)
        if book is None or book.content_type != "chaptered":
            self._send_error_json(
                HTTPStatus.NOT_FOUND,
                "系列漫画不存在。",
            )
            return
        self.server.library.annotate_availability([book])
        user = self._current_user()
        payload = book.public_dict()
        payload["favorite"] = (
            self.server.auth.is_favorite(user.id, book.id)
            if user.is_authenticated and self.server.auth is not None else False
        )
        payload["favorite_added_at"] = ""
        payload["favorite_count"] = (
            self.server.auth.favorite_count(user.id)
            if user.is_authenticated and self.server.auth is not None else 0
        )
        self._send_json(payload, head_only)

    def _series_chapters(
        self,
        book_id: str,
        query: dict,
        head_only: bool,
    ) -> None:
        book = self.server.library.get_metadata(book_id)
        if book is None or book.content_type != "chaptered":
            self._send_error_json(
                HTTPStatus.NOT_FOUND,
                "系列漫画不存在。",
            )
            return
        page_size = self._int_param(
            query,
            "page_size",
            50,
            minimum=1,
            maximum=200,
        )
        page = self._int_param(query, "page", 1, minimum=1)
        search = (query.get("q") or [""])[0]
        status = (query.get("status") or [""])[0]
        order = (query.get("order") or ["asc"])[0]
        total, chapters = self.server.library.list_chapters(
            book_id,
            (page - 1) * page_size,
            page_size,
            search=search,
            status=status,
            order=order,
        )
        total_pages = (total + page_size - 1) // page_size
        self._send_json(
            {
                "book_id": book_id,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "order": "desc" if order == "desc" else "asc",
                "items": [chapter.public_dict() for chapter in chapters],
            },
            head_only,
        )

    def _chapter_detail(
        self,
        book_id: str,
        chapter_id: int,
        head_only: bool,
    ) -> None:
        book = self.server.library.get_metadata(book_id)
        if book is None or book.content_type != "chaptered":
            self._send_error_json(
                HTTPStatus.NOT_FOUND,
                "系列漫画不存在。",
            )
            return
        chapter = self.server.library.get_chapter(book_id, chapter_id)
        if chapter is None:
            self._send_error_json(
                HTTPStatus.NOT_FOUND,
                "章节不存在。",
            )
            return
        previous, following = self.server.library.neighboring_chapters(chapter)
        payload = chapter.public_dict()
        payload.update(
            {
                "book": self._public_book(book),
                "previous_chapter": previous.public_dict() if previous else None,
                "next_chapter": following.public_dict() if following else None,
            }
        )
        self._send_json(payload, head_only)

    def _chapter_pages(
        self,
        book_id: str,
        chapter_id: int,
        query: dict,
        head_only: bool,
    ) -> None:
        book = self.server.library.get_metadata(book_id)
        if book is None or book.content_type != "chaptered":
            self._send_error_json(
                HTTPStatus.NOT_FOUND,
                "系列漫画不存在。",
            )
            return
        chapter = self.server.library.get_chapter(book_id, chapter_id)
        if chapter is None:
            self._send_error_json(
                HTTPStatus.NOT_FOUND,
                "章节不存在。",
            )
            return
        try:
            pages = self.server.library.chapter_pages(book, chapter)
        except StorageAccessError as exc:
            self._send_storage_error(exc)
            return
        offset = self._int_param(query, "offset", 0, minimum=0)
        limit = self._int_param(query, "limit", 1, minimum=1, maximum=20)
        items = []
        for page in pages[offset:offset + limit]:
            try:
                path = self.server.library.chapter_page_path(
                    book,
                    chapter,
                    page.filename,
                )
            except StorageAccessError as exc:
                self._send_storage_error(exc)
                return
            width, height = self._dimensions(path)
            items.append(
                {
                    "index": page.index,
                    "filename": page.filename,
                    "url": (
                        f"/media/{quote(str(book.id), safe='')}/chapter/"
                        f"{chapter.id}/{quote(page.filename, safe='')}"
                    ),
                    "width": width,
                    "height": height,
                    "rendered": page.rendered,
                }
            )
        self._send_json(
            {
                "book_id": book_id,
                "chapter_id": chapter_id,
                "total": len(pages),
                "offset": offset,
                "limit": limit,
                "pages": items,
            },
            head_only,
        )

    def _chapter_media(
        self,
        book_id: str,
        chapter_id: int,
        filename: str,
        head_only: bool,
    ) -> None:
        book = self.server.library.get_metadata(book_id)
        chapter = self.server.library.get_chapter(book_id, chapter_id)
        if book is None or chapter is None:
            self._send_error_json(
                HTTPStatus.NOT_FOUND,
                "章节不存在。",
            )
            return
        try:
            path = self.server.library.chapter_page_path(
                book,
                chapter,
                filename,
            )
        except StorageAccessError as exc:
            self._send_storage_error(exc)
            return
        except KeyError:
            self._send_error_json(
                HTTPStatus.NOT_FOUND,
                "章节页面不存在。",
            )
            return
        self._serve_file(
            path,
            head_only,
            cache_control="public, max-age=31536000, immutable",
        )

    def _book_cover_file(self, book_id: str, head_only: bool) -> None:
        book = self.server.library.get_metadata(book_id)
        if book is None:
            self._send_error_json(
                HTTPStatus.NOT_FOUND,
                "漫画不存在。",
            )
            return
        try:
            path = self.server.library.cover_path(book)
        except StorageAccessError as exc:
            self._send_storage_error(exc)
            return
        except KeyError:
            self._send_error_json(
                HTTPStatus.NOT_FOUND,
                "漫画封面不存在。",
            )
            return
        self._serve_file(
            path,
            head_only,
            cache_control="public, max-age=31536000, immutable",
            etag=f'"{book.cover_version or "cover"}"',
        )

    def _book_detail(self, book_id: str, head_only: bool) -> None:
        user = self._current_user()
        book = self.server.library.get_metadata(book_id)
        if book is None:
            self._send_error_json(HTTPStatus.NOT_FOUND, "Book not found")
            return
        self.server.library.annotate_availability([book])
        payload = book.public_dict()
        payload["favorite"] = (
            self.server.auth.is_favorite(user.id, book.id)
            if self.server.auth is not None and user.is_authenticated else False
        )
        payload["favorite_added_at"] = ""
        payload["favorite_count"] = (
            self.server.auth.favorite_count(user.id)
            if self.server.auth is not None and user.is_authenticated
            else 0
        )
        self._send_json(payload, head_only)

    def _book_pages(self, book_id: str, query: dict, head_only: bool) -> None:
        try:
            book = self.server.library.get(book_id)
        except StorageAccessError as exc:
            self._send_storage_error(exc)
            return
        if book is None:
            self._send_error_json(HTTPStatus.NOT_FOUND, "Book not found")
            return
        self.server.library.annotate_availability(
            [book],
            inspect_content=True,
        )
        offset = self._int_param(query, "offset", 0, minimum=0)
        limit = self._int_param(query, "limit", 1, minimum=1, maximum=20)
        selected = book.pages[offset:offset + limit]
        pages = []
        for page in selected:
            try:
                path = self.server.library.page_path(book, page.filename)
            except StorageAccessError as exc:
                self._send_storage_error(exc)
                return
            width, height = self._dimensions(path)
            pages.append(
                {
                    "index": page.index,
                    "filename": page.filename,
                    "url": (
                        f"/media/{quote(str(book.id), safe='')}/"
                        f"{quote(page.filename, safe='')}"
                    ),
                    "width": width,
                    "height": height,
                    "rendered": page.rendered,
                }
            )
        self._send_json(
            {
                "book_id": book.id,
                "total": book.page_count,
                "offset": offset,
                "limit": limit,
                "pages": pages,
            },
            head_only,
        )

    def _cover_options(self, book_id: str, query: dict, head_only: bool) -> None:
        try:
            book = self.server.library.get(book_id)
        except StorageAccessError as exc:
            self._send_storage_error(exc)
            return
        if book is None:
            self._send_error_json(HTTPStatus.NOT_FOUND, "Book not found")
            return
        self.server.library.annotate_availability(
            [book],
            inspect_content=True,
        )
        offset = self._int_param(query, "offset", 0, minimum=0)
        limit = self._int_param(query, "limit", 12, minimum=1, maximum=30)
        selected = book.pages[offset:offset + limit]
        items = [
            {
                "index": page.index,
                "filename": page.filename,
                "thumbnail_url": (
                    f"/api/books/{quote(str(book.id), safe='')}/pages/"
                    f"{page.index}/thumbnail?w=240"
                ),
                "selected": page.filename == book.cover,
            }
            for page in selected
        ]
        self._send_json(
            {
                "book": book.public_dict(),
                "total": book.page_count,
                "offset": offset,
                "limit": limit,
                "items": items,
            },
            head_only,
        )

    def _page_thumbnail(
        self,
        book_id: str,
        page_index: int,
        query: dict,
        head_only: bool,
    ) -> None:
        if self.server.lan_mode and cache_control.startswith("public"):
            cache_control = cache_control.replace(
                "public",
                "private",
                1,
            )
        try:
            book = self.server.library.get(book_id)
        except StorageAccessError as exc:
            self._send_storage_error(exc)
            return
        if book is None:
            self._send_error_json(HTTPStatus.NOT_FOUND, "Book not found")
            return
        page = next(
            (item for item in book.pages if item.index == page_index),
            None,
        )
        if page is None:
            self._send_error_json(HTTPStatus.NOT_FOUND, "Page not found")
            return
        width = self._int_param(
            query,
            "w",
            240,
            minimum=80,
            maximum=480,
        )
        try:
            source = self.server.library.page_path(book, page.filename)
            thumbnail = self.server.thumbnail_cache.get(source, width=width)
        except StorageAccessError as exc:
            self._send_storage_error(exc)
            return
        except Exception as exc:
            self._send_error_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"Thumbnail failed: {exc}",
            )
            return
        stat = thumbnail.stat()
        self._serve_file(
            thumbnail,
            head_only,
            cache_control="public, max-age=86400",
            etag=f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"',
        )

    def _thumbnail(
        self,
        book_id: str,
        query: dict,
        head_only: bool,
    ) -> None:
        book = self.server.library.get_metadata(book_id)
        if book is None or not book.cover:
            self._send_error_json(HTTPStatus.NOT_FOUND, "Cover not found")
            return
        try:
            source = self.server.library.cover_path(book)
            thumbnail = self.server.thumbnail_cache.get(source)
        except StorageAccessError as exc:
            self._send_storage_error(exc)
            return
        except KeyError:
            self._send_error_json(HTTPStatus.NOT_FOUND, "Cover not found")
            return
        except Exception as exc:
            self._send_error_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"Thumbnail failed: {exc}",
            )
            return
        version = (query.get("v") or [""])[0]
        cache_control = (
            "public, max-age=31536000, immutable"
            if version
            else "no-cache, must-revalidate"
        )
        self._serve_file(
            thumbnail,
            head_only,
            cache_control=cache_control,
            etag=f'"{book.cover_version or "unversioned"}"',
        )

    def _media(
        self,
        book_id: str,
        filename: str,
        head_only: bool,
    ) -> None:
        book = self.server.library.get_metadata(book_id)
        if book is None:
            self._send_error_json(HTTPStatus.NOT_FOUND, "Book not found")
            return
        try:
            path = self.server.library.page_path(book, filename)
        except StorageAccessError as exc:
            self._send_storage_error(exc)
            return
        except KeyError:
            self._send_error_json(HTTPStatus.NOT_FOUND, "Page not found")
            return
        self._serve_file(
            path,
            head_only,
            cache_control="public, max-age=31536000, immutable",
        )

    def _dimensions(self, path: Path) -> Tuple[int, int]:
        key = str(path)
        with self.server.dimension_lock:
            cached = self.server.dimension_cache.get(key)
        if cached:
            return cached
        try:
            with Image.open(path) as image:
                size = (max(1, image.width), max(1, image.height))
        except Exception:
            size = (800, 1200)
        with self.server.dimension_lock:
            self.server.dimension_cache[key] = size
        return size

    def _static(self, relative: str, head_only: bool) -> None:
        candidate = (STATIC_DIR / relative).resolve()
        try:
            candidate.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self._send_error_json(HTTPStatus.FORBIDDEN, "Forbidden")
            return
        if not candidate.is_file():
            self._send_error_json(HTTPStatus.NOT_FOUND, "Not found")
            return
        self._serve_file(candidate, head_only)

    def _serve_file(
        self,
        path: Path,
        head_only: bool,
        cache_control: str = "no-cache",
        etag: str = "",
    ) -> None:
        try:
            size = path.stat().st_size
            handle = path.open("rb")
        except OSError:
            self._send_error_json(HTTPStatus.NOT_FOUND, "File not found")
            return

        content_type = mimetypes.guess_type(str(path))[0]
        if not content_type:
            content_type = "application/octet-stream"

        if etag and self.headers.get("If-None-Match") == etag:
            self.send_response(HTTPStatus.NOT_MODIFIED)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", cache_control)
            self.end_headers()
            handle.close()
            return

        start = 0
        end = size - 1
        status = HTTPStatus.OK
        range_header = self.headers.get("Range")
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if match:
                if match.group(1):
                    start = int(match.group(1))
                if match.group(2):
                    end = int(match.group(2))
                if not match.group(1) and match.group(2):
                    length = int(match.group(2))
                    start = max(0, size - length)
                end = min(end, size - 1)
                if start <= end:
                    status = HTTPStatus.PARTIAL_CONTENT
                else:
                    start, end = 0, size - 1

        length = max(0, end - start + 1)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", cache_control)
        if etag:
            self.send_header("ETag", etag)
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header(
                "Content-Range",
                f"bytes {start}-{end}/{size}",
            )
        self.end_headers()
        if head_only:
            handle.close()
            return
        try:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(128 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            handle.close()

    def _read_json_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 1024 * 1024:
            raise ValueError("请求体为空或过大")
        body = self.rfile.read(length)
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON 请求体必须是对象")
        return payload

    def _send_json(
        self,
        payload: object,
        head_only: bool = False,
    ) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in getattr(self, "_extra_headers", []):
            self.send_header(name, value)
        self._extra_headers = []
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _send_error_json(
        self,
        status: HTTPStatus,
        message: str,
        *,
        code: str = "",
        details: Optional[dict] = None,
    ) -> None:
        payload = {"error": message}
        if code:
            payload["code"] = code
        if details:
            payload["details"] = details
        body = json.dumps(
            payload,
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(HTTPStatus(int(status)))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, value in getattr(self, "_extra_headers", []):
            self.send_header(name, value)
        self._extra_headers = []
        self.end_headers()
        self.wfile.write(body)

    def _send_storage_error(self, exc: StorageAccessError) -> None:
        if isinstance(exc, InvalidStoragePath):
            status = HTTPStatus.BAD_REQUEST
        elif isinstance(exc, BookContentMissing):
            status = HTTPStatus.NOT_FOUND
        else:
            status = HTTPStatus.SERVICE_UNAVAILABLE
        self._send_error_json(
            status,
            exc.message,
            code=exc.code,
            details=exc.details,
        )

    def _send_migration_error(self, exc: MigrationError) -> None:
        self._send_error_json(
            HTTPStatus(int(exc.http_status)),
            exc.message,
            code=exc.code,
            details=exc.details,
        )

    def _send_download_error(self, exc: DownloadError) -> None:
        self._send_error_json(
            HTTPStatus(int(exc.http_status)),
            exc.message,
            code=exc.code,
            details=exc.details,
        )

    def _send_deletion_error(self, exc: DeletionError) -> None:
        self._send_error_json(
            HTTPStatus(int(exc.http_status)),
            exc.message,
            code=exc.code,
            details=exc.details,
        )

    @staticmethod
    def _int_param(
        query: dict,
        name: str,
        default: int,
        minimum: int = 0,
        maximum: Optional[int] = None,
    ) -> int:
        try:
            value = int((query.get(name) or [default])[0])
        except (TypeError, ValueError):
            value = default
        value = max(minimum, value)
        if maximum is not None:
            value = min(maximum, value)
        return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="本地漫画库浏览网站",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="漫画根目录；默认读取数据库 download.root",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
        help=f"SQLite 数据库路径，默认 {DEFAULT_DATABASE}",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument(
        "--lan",
        action="store_true",
        help="发布到一个私有局域网 IPv4 地址",
    )
    parser.add_argument(
        "--lan-address",
        default="",
        help="局域网模式绑定的 IPv4 地址",
    )
    parser.add_argument(
        "--lan-allow-http",
        action="store_true",
        help="允许局域网模式使用不安全 HTTP",
    )
    parser.add_argument("--tls-cert", type=Path)
    parser.add_argument("--tls-key", type=Path)
    parser.add_argument(
        "--lan-allowed-cidrs",
        default=os.environ.get(
            "COMIC_LAN_ALLOWED_CIDRS",
            LAN_ALLOWED_CIDRS_DEFAULT,
        ),
        help="允许访问的逗号分隔 CIDR",
    )
    parser.add_argument(
        "--lan-hostname",
        default=os.environ.get("COMIC_LAN_HOSTNAME", ""),
        help="额外允许的局域网 Host 名称",
    )
    parser.add_argument(
        "--keep-awake",
        dest="keep_awake",
        action="store_true",
        default=os.environ.get("COMIC_DOWNLOAD_KEEP_AWAKE", "1") != "0",
        help="下载期间阻止系统空闲休眠，默认开启",
    )
    parser.add_argument(
        "--no-keep-awake",
        dest="keep_awake",
        action="store_false",
        help="禁用下载防休眠",
    )
    parser.add_argument(
        "--keep-display-awake",
        action="store_true",
        default=os.environ.get("COMIC_DOWNLOAD_KEEP_DISPLAY", "0") == "1",
        help="下载期间同时保持显示器点亮",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="启动后自动打开浏览器",
    )
    return parser.parse_args()


def port_in_use(host: str, port: int) -> bool:
    target_host = host
    if host in {"0.0.0.0", "localhost"}:
        target_host = "127.0.0.1"
    elif host == "::":
        target_host = "::1"
    family = socket.AF_INET6 if ":" in target_host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        return probe.connect_ex((target_host, port)) == 0


def main() -> int:
    args = parse_args()
    project_root = PROJECT_ROOT
    database_path = args.database.expanduser().resolve()
    try:
        config_database = Database(database_path)
        settings = SettingsRepository(config_database, project_root)
        settings.seed_defaults()
        bootstrap_verification_code = os.environ.get(
            "COMIC_BOOTSTRAP_VERIFICATION_CODE",
            DEFAULT_BOOTSTRAP_VERIFICATION_CODE,
        )
        settings.ensure_secret_default(
            "security.verification_code",
            bootstrap_verification_code,
        )
        root = (
            args.root.expanduser().resolve()
            if args.root is not None
            else settings.get_path(
                "download.root",
                project_root / "download",
            )
        )
        cdp_url = settings.get(
            "download.cdp_url",
            "http://127.0.0.1:9222",
        )
    except (OSError, SettingsError) as exc:
        print(f"启动失败：无法加载数据库配置：{exc}", file=sys.stderr)
        return 2
    lan_mode = bool(args.lan)
    use_https = False
    tls_cert = args.tls_cert
    tls_key = args.tls_key
    allowed_networks = []
    allowed_hosts = set()
    if lan_mode:
        try:
            lan_ip = str(args.lan_address or detect_lan_ip()).strip()
            address = ipaddress.ip_address(lan_ip)
        except (ValueError, RuntimeError) as exc:
            print(f"启动失败：{exc}", file=sys.stderr)
            return 2
        if not address.is_private or address.is_loopback:
            print("启动失败：--lan 必须绑定私有局域网 IPv4 地址。", file=sys.stderr)
            return 2
        args.host = lan_ip
        try:
            allowed_networks = parse_networks(args.lan_allowed_cidrs)
        except ValueError as exc:
            print(f"启动失败：局域网 CIDR 不合法：{exc}", file=sys.stderr)
            return 2
        if tls_cert is None and tls_key is None and not args.lan_allow_http:
            try:
                tls_cert, tls_key = ensure_lan_certificate(
                    project_root / "run" / "tls",
                    lan_ip,
                    args.lan_hostname,
                )
            except RuntimeError as exc:
                print(f"启动失败：{exc}", file=sys.stderr)
                return 2
        if (tls_cert is None) != (tls_key is None):
            print("启动失败：TLS 证书和私钥必须同时提供。", file=sys.stderr)
            return 2
        use_https = tls_cert is not None and tls_key is not None
        if not use_https and not args.lan_allow_http:
            print("启动失败：局域网模式需要 HTTPS。", file=sys.stderr)
            return 2
        if not use_https:
            print(
                "警告：局域网 HTTP 会暴露登录密码和会话 Cookie，仅用于临时调试。",
                file=sys.stderr,
            )
        allowed_hosts = {
            lan_ip,
            "127.0.0.1",
            "localhost",
            socket.gethostname().lower(),
        }
        if args.lan_hostname:
            allowed_hosts.add(args.lan_hostname.strip().lower())
    if port_in_use(args.host, args.port):
        print(
            f"启动失败：端口 {args.port} 已被占用。"
            "请先运行 ./stop_site.sh，或使用 --port 指定其他端口。",
            file=sys.stderr,
        )
        return 3
    if not root.is_dir():
        print(f"漫画目录不存在：{root}", file=sys.stderr)
        return 2
    download_root_guard = DownloadRootGuard(
        settings,
        expected_root=root,
    )

    library = ComicLibrary(
        root,
        database_path=database_path,
    )
    books = BookRepository(library.database, root)
    with library.database.connect() as connection:
        book_count = int(
            connection.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        )
    print(f"已加载数据库：{book_count} 本漫画。")

    cache = ThumbnailCache(library.thumbnail_dir)
    favorites = FavoriteStore(database_path)
    migrations = MigrationRepository(
        library.database,
        library,
        library.storage,
        settings=settings,
    )
    worker = MigrationWorker(
        migrations,
        library,
        lock_path=Path(__file__).parent.parent / "run" / "migration.lock",
    )
    auth = AuthService(library.database, project_root)
    bootstrap_admin = auth.ensure_bootstrap_admin(
        os.environ.get(
            "COMIC_BOOTSTRAP_ADMIN_USERNAME",
            DEFAULT_BOOTSTRAP_ADMIN_USERNAME,
        ),
        os.environ.get(
            "COMIC_BOOTSTRAP_ADMIN_PASSWORD",
            DEFAULT_BOOTSTRAP_ADMIN_PASSWORD,
        ),
    )
    if bootstrap_admin is not None:
        print(
            "首次初始化管理员："
            f"{bootstrap_admin['username']}，"
            "请登录后立即修改默认密码。"
        )
        print(
            "首次初始化校验码已写入数据库；"
            "默认值仅用于初始化，请尽快在系统设置中修改。"
        )
    elif not auth.has_admin():
        token = auth.setup_token()
        print(f"首次初始化管理员令牌：{token}")
        print("访问 /setup 创建首个管理员。")
    downloads = DownloadRepository(
        library.database,
        library,
        output_root=root,
        project_root=project_root,
        cdp_url=cdp_url,
        download_root_guard=download_root_guard,
    )
    sleep_inhibitor = SleepInhibitor(
        enabled=args.keep_awake,
        keep_display_awake=args.keep_display_awake,
    )
    crawler_lock_path = project_root / "run" / "crawler.lock"
    download_worker = DownloadWorker(
        downloads,
        library,
        book_repository=books,
        thumbnails=cache,
        project_root=project_root,
        lock_path=project_root / "run" / "download.lock",
        crawler_lock_path=crawler_lock_path,
        sleep_inhibitor=sleep_inhibitor,
    )
    batch_downloads = BatchDownloadRepository(
        library.database,
        library,
        output_root=root,
        project_root=project_root,
        download_root_guard=download_root_guard,
    )
    batch_download_worker = BatchDownloadWorker(
        batch_downloads,
        library,
        book_repository=books,
        thumbnails=cache,
        project_root=project_root,
        crawler_lock_path=crawler_lock_path,
        cdp_url=downloads.cdp_url,
        sleep_inhibitor=sleep_inhibitor,
    )
    incremental_pulls = IncrementalPullRepository(
        library.database,
        library,
        batch_downloads,
    )
    deletions = DeletionRepository(
        library.database,
        library,
        library.storage,
        settings=settings,
    )
    deletion_worker = DeletionWorker(
        deletions,
        library,
        cache,
        lock_path=project_root / "run" / "deletion.lock",
    )
    try:
        server = ComicHTTPServer(
            (args.host, args.port),
            library,
            cache,
            favorites,
            migrations,
            worker,
            downloads,
            download_worker,
            batch_downloads,
            batch_download_worker,
            incremental_pulls,
            deletions,
            deletion_worker,
            auth,
            allow_migration=lan_mode or args.host in {
                "127.0.0.1",
                "::1",
                "localhost",
            },
            lan_mode=lan_mode,
            allowed_networks=allowed_networks,
            allowed_hosts=allowed_hosts,
            use_https=use_https,
            sleep_inhibitor=sleep_inhibitor,
            settings=settings,
        )
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        print(
            f"启动失败：端口 {args.port} 已被占用。"
            "请先运行 ./stop_site.sh，或使用 --port 指定其他端口。",
            file=sys.stderr,
        )
        return 3
    loopback_server = None
    scheme = "https" if use_https else "http"
    url = f"{scheme}://{args.host}:{args.port}/"
    if use_https:
        tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        try:
            tls_context.load_cert_chain(str(tls_cert), str(tls_key))
            server.socket = tls_context.wrap_socket(
                server.socket,
                server_side=True,
            )
        except (OSError, ssl.SSLError) as exc:
            print(f"启动失败：无法加载 TLS 证书：{exc}", file=sys.stderr)
            server.server_close()
            return 2
    if lan_mode:
        try:
            loopback_server = ComicHTTPServer(
                ("127.0.0.1", args.port),
                library,
                cache,
                favorites,
                migrations,
                worker,
                downloads,
                download_worker,
                batch_downloads,
                batch_download_worker,
                incremental_pulls,
                deletions,
                deletion_worker,
                auth,
                allow_migration=True,
                lan_mode=False,
                allowed_networks=[],
                allowed_hosts=set(),
                use_https=False,
                sleep_inhibitor=sleep_inhibitor,
                settings=settings,
            )
            threading.Thread(
                target=loopback_server.serve_forever,
                name="comic-http-loopback",
                daemon=True,
            ).start()
        except OSError as exc:
            print(f"启动失败：无法监听 127.0.0.1：{exc}", file=sys.stderr)
            server.server_close()
            return 2
    print(f"本地漫画库：{url}")
    if loopback_server is not None:
        print(f"本机访问：http://127.0.0.1:{args.port}/")
        (project_root / "run" / "site.local.url").write_text(
            f"http://127.0.0.1:{args.port}/\n",
            encoding="utf-8",
        )
    print("按 Ctrl+C 停止服务。")

    try:
        worker.start()
        download_worker.start()
        batch_download_worker.start()
        deletion_worker.start()
    except Exception as exc:
        sleep_inhibitor.release_all()
        deletion_worker.stop(timeout=10)
        batch_download_worker.stop(timeout=10)
        worker.stop(timeout=10)
        server.server_close()
        print(f"后台线程启动失败：{exc}", file=sys.stderr)
        return 2

    def request_shutdown(_signum=None, _frame=None) -> None:
        if loopback_server is not None:
            threading.Thread(
                target=loopback_server.shutdown,
                name="comic-http-loopback-shutdown",
                daemon=True,
            ).start()
        threading.Thread(
            target=server.shutdown,
            name="comic-http-shutdown",
            daemon=True,
        ).start()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)

    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        print("\n正在停止服务……")
    finally:
        if not download_worker.stop(timeout=30):
            print(
                "下载线程未在 30 秒内停止；当前任务会在下一次重试时继续。",
                file=sys.stderr,
            )
        if not batch_download_worker.stop(timeout=30):
            print(
                "批量下载线程未在 30 秒内停止；当前章节会在下一次重试时继续。",
                file=sys.stderr,
            )
        if not deletion_worker.stop(timeout=30):
            print(
                "删除线程未在 30 秒内停止；当前任务已保留隔离路径，可重试。",
                file=sys.stderr,
            )
        if not worker.stop(timeout=60):
            print(
                "迁移线程未在 60 秒内停止；当前任务会在复制分块结束后安全暂停。",
                file=sys.stderr,
            )
        sleep_inhibitor.release_all()
        server.server_close()
        if loopback_server is not None:
            loopback_server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
