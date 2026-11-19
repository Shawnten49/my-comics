from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

try:
    from .database import Database
except ImportError:
    from database import Database


ROLE_USER = "user"
ROLE_ADMIN = "admin"
ROLE_MAX_ACCESS = {"guest": 0, ROLE_USER: 1, ROLE_ADMIN: 2}
PBKDF2_ITERATIONS = 310_000


class AuthError(Exception):
    def __init__(self, message: str, *, code: str, http_status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status


@dataclass(frozen=True)
class CurrentUser:
    id: Optional[int]
    username: str
    role: str
    csrf_token: str = ""
    must_change_password: bool = False

    @property
    def max_access_level(self) -> int:
        return ROLE_MAX_ACCESS.get(self.role, 0)

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def is_authenticated(self) -> bool:
        return self.role in {ROLE_USER, ROLE_ADMIN}

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "can_favorite": self.is_authenticated,
            "can_manage": self.is_admin and not self.must_change_password,
            "must_change_password": self.must_change_password,
            "max_access_level": self.max_access_level,
            "csrf_token": self.csrf_token if self.is_authenticated else "",
        }


GUEST = CurrentUser(None, "", "guest")


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    if len(password) < 6 or len(password) > 128 or not password.strip():
        raise AuthError(
            "密码长度必须为 6 到 128 个字符。",
            code="invalid_password",
        )
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    return (
        f"pbkdf2_sha256${PBKDF2_ITERATIONS}$"
        f"{base64.b64encode(salt).decode()}$"
        f"{base64.b64encode(digest).decode()}"
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            base64.b64decode(salt),
            int(iterations),
        )
        return hmac.compare_digest(digest, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


class AuthService:
    def __init__(self, database: Database, project_root: Path) -> None:
        self.database = database
        self.run_dir = project_root / "run"
        self.setup_token_path = self.run_dir / "setup-token.txt"
        self._failures: dict[str, list[datetime]] = {}

    def has_admin(self) -> bool:
        with self.database.connect() as connection:
            return connection.execute(
                "SELECT 1 FROM users WHERE role = 'admin' AND active = 1 LIMIT 1"
            ).fetchone() is not None

    def user_count(self) -> int:
        with self.database.connect() as connection:
            return int(
                connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            )

    def ensure_bootstrap_admin(
        self,
        username: str,
        password: str,
    ) -> Optional[dict]:
        if self.user_count() > 0:
            return None
        user = self.create_user(
            username,
            password,
            ROLE_ADMIN,
            must_change_password=True,
        )
        self.audit(
            None,
            user["username"],
            "auth.bootstrap_admin",
            "user",
            str(user["id"]),
            "首次初始化默认管理员",
        )
        return user

    def setup_token(self) -> str:
        if self.has_admin():
            return ""
        if self.setup_token_path.is_file():
            return self.setup_token_path.read_text(encoding="utf-8").strip()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(24)
        self.setup_token_path.write_text(token, encoding="utf-8")
        return token

    def setup_admin(self, token: str, username: str, password: str) -> dict:
        if self.has_admin():
            raise AuthError("管理员已经初始化。", code="already_initialized", http_status=409)
        expected = self.setup_token()
        if not expected or not hmac.compare_digest(str(token), expected):
            raise AuthError("初始化令牌错误。", code="invalid_setup_token", http_status=403)
        user = self.create_user(username, password, ROLE_ADMIN)
        self.setup_token_path.unlink(missing_ok=True)
        self.audit(None, user["username"], "auth.setup_admin", "user", str(user["id"]), "")
        return user

    def create_user(
        self,
        username: str,
        password: str,
        role: str,
        *,
        must_change_password: bool = False,
    ) -> dict:
        normalized = str(username or "").strip()
        if len(normalized) < 3 or len(normalized) > 64:
            raise AuthError("用户名长度必须为 3 到 64 个字符。", code="invalid_username")
        if role not in {ROLE_USER, ROLE_ADMIN}:
            raise AuthError("角色不合法。", code="invalid_role")
        now = datetime.now().isoformat(timespec="seconds")
        try:
            with self.database.connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO users(
                        username, username_normalized, display_name,
                        password_hash, password_changed_at, role, active,
                        must_change_password, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        normalized,
                        normalized.casefold(),
                        normalized,
                        hash_password(password),
                        now,
                        role,
                        int(must_change_password),
                        now,
                        now,
                    ),
                )
                connection.commit()
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise AuthError("用户名已经存在。", code="username_exists", http_status=409) from exc
            raise
        return {
            "id": int(cursor.lastrowid),
            "username": normalized,
            "role": role,
            "must_change_password": bool(must_change_password),
        }

    def change_password(
        self,
        user_id: int,
        current_password: str,
        new_password: str,
    ) -> dict:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE id = ? AND active = 1",
                (int(user_id),),
            ).fetchone()
            if row is None or not verify_password(
                current_password,
                row["password_hash"],
            ):
                raise AuthError(
                    "当前密码错误。",
                    code="invalid_current_password",
                    http_status=401,
                )
            if verify_password(new_password, row["password_hash"]):
                raise AuthError(
                    "新密码不能与当前密码相同。",
                    code="password_unchanged",
                )
            now = datetime.now().isoformat(timespec="seconds")
            connection.execute(
                """
                UPDATE users
                SET password_hash = ?, password_changed_at = ?,
                    must_change_password = 0, updated_at = ?
                WHERE id = ?
                """,
                (
                    hash_password(new_password),
                    now,
                    now,
                    int(user_id),
                ),
            )
            connection.execute(
                """
                UPDATE sessions SET revoked_at = ?
                WHERE user_id = ? AND revoked_at IS NULL
                """,
                (now, int(user_id)),
            )
            connection.commit()
        self.audit(
            int(user_id),
            row["username"],
            "auth.password_change",
            "user",
            str(user_id),
            "",
        )
        return {"ok": True, "username": row["username"]}

    def login(self, username: str, password: str, ip: str, user_agent: str) -> tuple[CurrentUser, str]:
        key = f"{ip}|{username.casefold()}"
        now = datetime.now()
        attempts = [item for item in self._failures.get(key, []) if now - item < timedelta(minutes=10)]
        if len(attempts) >= 5:
            raise AuthError("登录失败次数过多，请稍后再试。", code="too_many_login_attempts", http_status=429)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username_normalized = ?",
                (str(username or "").strip().casefold(),),
            ).fetchone()
        if row is None or not int(row["active"]) or not verify_password(password, row["password_hash"]):
            attempts.append(now)
            self._failures[key] = attempts
            raise AuthError("用户名或密码错误。", code="invalid_credentials", http_status=401)
        self._failures.pop(key, None)
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        created = now.isoformat(timespec="seconds")
        expires = (now + timedelta(days=7)).isoformat(timespec="seconds")
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions(
                    token_hash, csrf_token, user_id, ip_address,
                    user_agent, created_at, last_seen_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (token_hash, csrf, row["id"], ip, user_agent[:300], created, created, expires),
            )
            connection.execute(
                "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?",
                (created, created, row["id"]),
            )
            connection.commit()
        self.audit(int(row["id"]), row["username"], "auth.login", "session", "", "", ip)
        user = CurrentUser(
            int(row["id"]),
            row["username"],
            row["role"],
            csrf,
            bool(row["must_change_password"]),
        )
        return user, token

    def resolve(self, token: str, csrf_only: bool = False) -> CurrentUser:
        if not token:
            return GUEST
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = datetime.now().isoformat(timespec="seconds")
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT s.csrf_token, s.expires_at, s.revoked_at,
                       u.id, u.username, u.role, u.active,
                       u.must_change_password
                FROM sessions s JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = ? LIMIT 1
                """,
                (token_hash,),
            ).fetchone()
            if row is None or row["revoked_at"] or row["expires_at"] <= now or not int(row["active"]):
                return GUEST
            if not csrf_only:
                connection.execute(
                    "UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?",
                    (now, token_hash),
                )
                connection.commit()
        return CurrentUser(
            int(row["id"]),
            row["username"],
            row["role"],
            row["csrf_token"],
            bool(row["must_change_password"]),
        )

    def logout(self, token: str) -> None:
        if not token:
            return
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE token_hash = ?",
                (
                    datetime.now().isoformat(timespec="seconds"),
                    hashlib.sha256(token.encode()).hexdigest(),
                ),
            )
            connection.commit()

    def list_users(self) -> list[dict]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, username, display_name, role, active,
                       last_login_at, created_at
                FROM users ORDER BY id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def set_user(self, user_id: int, *, role: Optional[str] = None, active: Optional[bool] = None) -> dict:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None:
                raise KeyError(user_id)
            role = role if role is not None else row["role"]
            active_value = int(active) if active is not None else int(row["active"])
            if row["role"] == ROLE_ADMIN and (role != ROLE_ADMIN or not active_value):
                admins = connection.execute(
                    "SELECT COUNT(*) FROM users WHERE role='admin' AND active=1"
                ).fetchone()[0]
                if admins <= 1:
                    raise AuthError("不能禁用或降级最后一个管理员。", code="last_admin_protected", http_status=409)
            now = datetime.now().isoformat(timespec="seconds")
            connection.execute(
                "UPDATE users SET role=?, active=?, updated_at=? WHERE id=?",
                (role, active_value, now, user_id),
            )
            if role != row["role"] or not active_value:
                connection.execute(
                    "UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                    (now, user_id),
                )
            connection.commit()
        return {"id": user_id, "role": role, "active": bool(active_value)}

    def is_favorite(self, user_id: int, book_id: str) -> bool:
        with self.database.connect() as connection:
            return connection.execute(
                "SELECT 1 FROM user_favorites WHERE user_id=? AND book_id=?",
                (user_id, book_id),
            ).fetchone() is not None

    def set_favorite(self, user_id: int, book_id: str, favorite: bool) -> None:
        with self.database.connect() as connection:
            if favorite:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO user_favorites(user_id, book_id, added_at)
                    VALUES (?, ?, ?)
                    """,
                    (user_id, book_id, datetime.now().isoformat(timespec="seconds")),
                )
            else:
                connection.execute(
                    "DELETE FROM user_favorites WHERE user_id=? AND book_id=?",
                    (user_id, book_id),
                )
            connection.commit()

    def favorite_count(self, user_id: int) -> int:
        with self.database.connect() as connection:
            return int(connection.execute(
                "SELECT COUNT(*) FROM user_favorites WHERE user_id=?",
                (user_id,),
            ).fetchone()[0])

    def favorite_entries(self, user_id: int) -> list[tuple[str, str]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT book_id, added_at FROM user_favorites
                WHERE user_id = ? ORDER BY added_at DESC, book_id DESC
                """,
                (user_id,),
            ).fetchall()
        return [(row["book_id"], row["added_at"]) for row in rows]

    def audit(self, user_id: Optional[int], username: str, action: str, target_type: str, target_id: str, details: str, ip: str = "") -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_logs(
                    user_id, username, action, target_type, target_id,
                    details, ip_address, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id, username, action, target_type, target_id,
                    details[:2000], ip,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            connection.commit()
