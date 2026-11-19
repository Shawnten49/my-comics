from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from .database import Database
except ImportError:
    from database import Database


PBKDF2_ITERATIONS = 310_000


class SettingsError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "settings_error",
        http_status: int = 400,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status


@dataclass(frozen=True)
class SettingDefinition:
    key: str
    value_type: str
    description: str
    default: str = ""
    is_secret: bool = False
    editable: bool = True


SETTING_DEFINITIONS = {
    "download.root": SettingDefinition(
        "download.root",
        "path",
        "漫画下载根目录。",
    ),
    "download.cdp_url": SettingDefinition(
        "download.cdp_url",
        "string",
        "Chrome DevTools Protocol 地址。",
        default="http://127.0.0.1:9222",
    ),
    "security.verification_code": SettingDefinition(
        "security.verification_code",
        "secret",
        "迁移和删除操作的二次验证码，数据库只保存哈希。",
        is_secret=True,
    ),
}


def _encode_secret(value: str) -> str:
    if len(value) < 6 or len(value) > 128 or not value.strip():
        raise SettingsError(
            "敏感配置长度必须为 6 到 128 个字符。",
            code="invalid_secret_value",
        )
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        value.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    return (
        f"pbkdf2_sha256${PBKDF2_ITERATIONS}$"
        f"{base64.b64encode(salt).decode()}$"
        f"{base64.b64encode(digest).decode()}"
    )


def _verify_encoded_secret(value: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            value.encode("utf-8"),
            base64.b64decode(salt),
            int(iterations),
        )
        return hmac.compare_digest(digest, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


class SettingsRepository:
    def __init__(self, database: Database, project_root: Path) -> None:
        self.database = database
        self.project_root = project_root.expanduser().resolve()

    def seed_defaults(self) -> None:
        defaults = {
            "download.root": str(self.project_root / "download"),
            "download.cdp_url": "http://127.0.0.1:9222",
        }
        now = datetime.now().isoformat(timespec="seconds")
        with self.database.connect() as connection:
            for key, value in defaults.items():
                definition = SETTING_DEFINITIONS[key]
                connection.execute(
                    """
                    INSERT OR IGNORE INTO app_settings(
                        setting_key, value, value_type, description,
                        is_secret, editable, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key,
                        value,
                        definition.value_type,
                        definition.description,
                        int(definition.is_secret),
                        int(definition.editable),
                        now,
                        now,
                    ),
                )
            connection.commit()

    def get(self, key: str, default: str = "") -> str:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE setting_key = ?",
                (key,),
            ).fetchone()
        return str(row["value"]) if row else default

    def require(self, key: str) -> str:
        value = self.get(key)
        if not value:
            raise SettingsError(
                f"配置项未设置：{key}",
                code="setting_not_configured",
                http_status=503,
            )
        return value

    def get_path(self, key: str, default: Optional[Path] = None) -> Path:
        value = self.get(key)
        if not value:
            if default is None:
                raise SettingsError(
                    f"路径配置未设置：{key}",
                    code="setting_not_configured",
                    http_status=503,
                )
            return default.expanduser().resolve()
        return Path(value).expanduser().resolve()

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self.get(key)
        if not value:
            return default
        return value.strip().casefold() in {"1", "true", "yes", "on"}

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self.get(key, str(default)))
        except ValueError as exc:
            raise SettingsError(
                f"配置项不是整数：{key}",
                code="invalid_setting_value",
            ) from exc

    def set(
        self,
        key: str,
        value: str,
        *,
        description: Optional[str] = None,
        value_type: Optional[str] = None,
        is_secret: Optional[bool] = None,
        editable: Optional[bool] = None,
    ) -> None:
        definition = SETTING_DEFINITIONS.get(key)
        if definition is not None and not definition.editable:
            raise SettingsError(
                f"配置项不允许修改：{key}",
                code="setting_not_editable",
            )
        normalized_type = value_type or (
            definition.value_type if definition else "string"
        )
        normalized_description = description or (
            definition.description if definition else ""
        )
        secret = (
            bool(is_secret)
            if is_secret is not None
            else bool(definition and definition.is_secret)
        )
        if secret:
            raise SettingsError(
                "敏感配置必须通过 set_secret 写入。",
                code="secret_write_required",
            )
        self._validate_value(normalized_type, value)
        now = datetime.now().isoformat(timespec="seconds")
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO app_settings(
                    setting_key, value, value_type, description,
                    is_secret, editable, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    value = excluded.value,
                    value_type = excluded.value_type,
                    description = excluded.description,
                    is_secret = excluded.is_secret,
                    editable = excluded.editable,
                    updated_at = excluded.updated_at
                """,
                (
                    key,
                    value,
                    normalized_type,
                    normalized_description,
                    int(
                        editable
                        if editable is not None
                        else (definition.editable if definition else True)
                    ),
                    now,
                    now,
                ),
            )
            connection.commit()

    def set_secret(self, key: str, value: str) -> None:
        definition = SETTING_DEFINITIONS.get(key)
        if definition is None or not definition.is_secret:
            raise SettingsError(
                f"配置项不是敏感配置：{key}",
                code="invalid_secret_key",
            )
        encoded = _encode_secret(value)
        now = datetime.now().isoformat(timespec="seconds")
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO app_settings(
                    setting_key, value, value_type, description,
                    is_secret, editable, created_at, updated_at
                ) VALUES (?, ?, 'secret', ?, 1, ?, ?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    value = excluded.value,
                    value_type = 'secret',
                    description = excluded.description,
                    is_secret = 1,
                    editable = excluded.editable,
                    updated_at = excluded.updated_at
                """,
                (
                    key,
                    encoded,
                    definition.description,
                    int(definition.editable),
                    now,
                    now,
                ),
            )
            connection.commit()

    def ensure_secret_default(self, key: str, value: str) -> bool:
        if self.is_configured(key):
            return False
        self.set_secret(key, value)
        return True

    def verify_secret(self, key: str, value: str) -> bool:
        definition = SETTING_DEFINITIONS.get(key)
        if definition is None or not definition.is_secret:
            raise SettingsError(
                f"配置项不是敏感配置：{key}",
                code="invalid_secret_key",
            )
        encoded = self.get(key)
        return bool(encoded) and _verify_encoded_secret(value, encoded)

    def is_configured(self, key: str) -> bool:
        return bool(self.get(key))

    def list_settings(self) -> list[dict]:
        keys = tuple(SETTING_DEFINITIONS)
        placeholders = ",".join("?" for _ in keys)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT id, setting_key, value, value_type, description,
                       is_secret, editable, created_at, updated_at
                FROM app_settings
                WHERE setting_key IN ({placeholders})
                ORDER BY id
                """,
                keys,
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            configured = bool(item["value"])
            if item["is_secret"]:
                item["value"] = ""
            item["configured"] = configured
            item["is_secret"] = bool(item["is_secret"])
            item["editable"] = bool(item["editable"])
            items.append(item)
        return items

    def update_from_payload(self, key: str, payload: dict) -> dict:
        definition = SETTING_DEFINITIONS.get(key)
        if definition is None:
            raise SettingsError(
                f"未知配置项：{key}",
                code="unknown_setting",
                http_status=404,
            )
        if not definition.editable:
            raise SettingsError(
                f"配置项不允许修改：{key}",
                code="setting_not_editable",
                http_status=409,
            )
        value = str(payload.get("value", ""))
        if definition.value_type == "path":
            if key == "download.root":
                try:
                    from .download_root import (
                        DownloadRootError,
                        validate_download_root,
                    )
                except ImportError:
                    from download_root import (
                        DownloadRootError,
                        validate_download_root,
                    )
                try:
                    validate_download_root(value)
                except DownloadRootError as exc:
                    raise SettingsError(
                        exc.message,
                        code=exc.code,
                        http_status=exc.http_status,
                    ) from exc
        if definition.is_secret:
            self.set_secret(key, value)
        else:
            self.set(key, value)
        return {
            "key": key,
            "configured": self.is_configured(key),
            "requires_restart": definition.value_type == "path",
        }

    @staticmethod
    def _validate_value(value_type: str, value: str) -> None:
        if value_type == "path":
            path = Path(value).expanduser()
            if not path.is_absolute():
                raise SettingsError(
                    "路径配置必须是绝对路径。",
                    code="invalid_path_setting",
                )
            if path == Path(path.anchor):
                raise SettingsError(
                    "路径配置不能指向文件系统根目录。",
                    code="invalid_path_setting",
                )
        elif value_type == "integer":
            try:
                int(value)
            except ValueError as exc:
                raise SettingsError(
                    "配置值必须是整数。",
                    code="invalid_integer_setting",
                ) from exc
        elif value_type == "boolean" and value.casefold() not in {
            "0", "1", "true", "false", "yes", "no", "on", "off",
        }:
            raise SettingsError(
                "布尔配置值不合法。",
                code="invalid_boolean_setting",
            )
