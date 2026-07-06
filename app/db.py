from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "getbalance.sqlite3"
DB_PATH = Path(os.getenv("GETBALANCE_DB", DEFAULT_DB_PATH))


LOGIN_COLUMNS: dict[str, str] = {
    "request_body": "TEXT NOT NULL DEFAULT ''",
    "login_enabled": "INTEGER NOT NULL DEFAULT 0",
    "login_url": "TEXT NOT NULL DEFAULT ''",
    "login_method": "TEXT NOT NULL DEFAULT 'api'",
    "login_username": "TEXT NOT NULL DEFAULT ''",
    "login_password": "TEXT NOT NULL DEFAULT ''",
    "login_headers": "TEXT NOT NULL DEFAULT '{}'",
    "login_body_template": "TEXT NOT NULL DEFAULT ''",
    "login_token_path": "TEXT NOT NULL DEFAULT ''",
    "login_token_prefix": "TEXT NOT NULL DEFAULT 'Bearer'",
    "login_cookie_from_response": "INTEGER NOT NULL DEFAULT 0",
    "auth_fail_status_codes": "TEXT NOT NULL DEFAULT '401,403'",
    "auth_fail_keywords": "TEXT NOT NULL DEFAULT 'unauthorized,token expired,login required,未登录,登录过期'",
    "login_status": "TEXT NOT NULL DEFAULT 'not_configured'",
    "last_login_error": "TEXT NOT NULL DEFAULT ''",
    "last_login_at": "TEXT",
}


def dict_factory(cursor: sqlite3.Cursor, row: sqlite3.Row) -> dict[str, Any]:
    return {column[0]: row[index] for index, column in enumerate(cursor.description)}


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = dict_factory
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_sites_column(db: sqlite3.Connection, column_name: str, definition: str) -> None:
    existing_columns = {row["name"] for row in db.execute("PRAGMA table_info(sites)").fetchall()}
    if column_name not in existing_columns:
        db.execute(f"ALTER TABLE sites ADD COLUMN {column_name} {definition}")


def init_db() -> None:
    with get_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS sites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                base_url TEXT NOT NULL,
                balance_endpoint TEXT NOT NULL DEFAULT '/',
                method TEXT NOT NULL DEFAULT 'GET',
                adapter TEXT NOT NULL DEFAULT 'generic_json',
                cookie TEXT NOT NULL DEFAULT '',
                authorization TEXT NOT NULL DEFAULT '',
                extra_headers TEXT NOT NULL DEFAULT '{}',
                request_body TEXT NOT NULL DEFAULT '',
                balance_path TEXT NOT NULL DEFAULT 'balance',
                currency_path TEXT NOT NULL DEFAULT '',
                default_currency TEXT NOT NULL DEFAULT 'USD',
                scale REAL NOT NULL DEFAULT 1,
                notes TEXT NOT NULL DEFAULT '',
                is_active INTEGER NOT NULL DEFAULT 1,
                login_enabled INTEGER NOT NULL DEFAULT 0,
                login_url TEXT NOT NULL DEFAULT '',
                login_method TEXT NOT NULL DEFAULT 'api',
                login_username TEXT NOT NULL DEFAULT '',
                login_password TEXT NOT NULL DEFAULT '',
                login_headers TEXT NOT NULL DEFAULT '{}',
                login_body_template TEXT NOT NULL DEFAULT '',
                login_token_path TEXT NOT NULL DEFAULT '',
                login_token_prefix TEXT NOT NULL DEFAULT 'Bearer',
                login_cookie_from_response INTEGER NOT NULL DEFAULT 0,
                auth_fail_status_codes TEXT NOT NULL DEFAULT '401,403',
                auth_fail_keywords TEXT NOT NULL DEFAULT 'unauthorized,token expired,login required,未登录,登录过期',
                login_status TEXT NOT NULL DEFAULT 'not_configured',
                last_login_error TEXT NOT NULL DEFAULT '',
                last_login_at TEXT,
                last_balance REAL,
                last_currency TEXT,
                last_status TEXT NOT NULL DEFAULT 'never',
                last_error TEXT NOT NULL DEFAULT '',
                last_checked_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS balance_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id INTEGER NOT NULL,
                balance REAL,
                currency TEXT,
                status TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '',
                raw_response_preview TEXT NOT NULL DEFAULT '',
                checked_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY(site_id) REFERENCES sites(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_history_site_checked
                ON balance_history(site_id, checked_at DESC);

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        for column_name, definition in LOGIN_COLUMNS.items():
            _ensure_sites_column(db, column_name, definition)
        _ensure_auto_refresh_defaults(db)


AUTO_REFRESH_DEFAULT_MINUTES = 5


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _ensure_app_settings_table(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )


def _setting_rows(db: sqlite3.Connection) -> dict[str, str]:
    _ensure_app_settings_table(db)
    rows = db.execute("SELECT key, value FROM app_settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


def _set_setting(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _ensure_auto_refresh_defaults(db: sqlite3.Connection) -> None:
    settings = _setting_rows(db)
    if "auto_refresh_enabled" not in settings:
        _set_setting(db, "auto_refresh_enabled", "1")
    if "auto_refresh_minutes" not in settings:
        _set_setting(db, "auto_refresh_minutes", str(AUTO_REFRESH_DEFAULT_MINUTES))
    if "auto_refresh_last_run_at" not in settings:
        _set_setting(db, "auto_refresh_last_run_at", "")
    if "auto_refresh_next_run_at" not in settings:
        _set_setting(
            db,
            "auto_refresh_next_run_at",
            _iso(_utc_now() + timedelta(minutes=AUTO_REFRESH_DEFAULT_MINUTES)),
        )
    if "auto_refresh_running" not in settings:
        _set_setting(db, "auto_refresh_running", "0")
    if "auto_refresh_last_error" not in settings:
        _set_setting(db, "auto_refresh_last_error", "")


def get_auto_refresh_settings() -> dict[str, Any]:
    with get_db() as db:
        _ensure_auto_refresh_defaults(db)
        settings = _setting_rows(db)
    minutes = int(settings.get("auto_refresh_minutes") or AUTO_REFRESH_DEFAULT_MINUTES)
    minutes = max(1, min(1440, minutes))
    next_run_at = settings.get("auto_refresh_next_run_at") or ""
    return {
        "enabled": settings.get("auto_refresh_enabled", "1") == "1",
        "minutes": minutes,
        "last_run_at": settings.get("auto_refresh_last_run_at") or "",
        "next_run_at": next_run_at,
        "running": settings.get("auto_refresh_running", "0") == "1",
        "last_error": settings.get("auto_refresh_last_error") or "",
        "server_now": _iso(_utc_now()),
    }


def update_auto_refresh_settings(*, enabled: bool, minutes: int) -> dict[str, Any]:
    minutes = max(1, min(1440, int(minutes or AUTO_REFRESH_DEFAULT_MINUTES)))
    with get_db() as db:
        _ensure_auto_refresh_defaults(db)
        _set_setting(db, "auto_refresh_enabled", "1" if enabled else "0")
        _set_setting(db, "auto_refresh_minutes", str(minutes))
        _set_setting(db, "auto_refresh_next_run_at", _iso(_utc_now() + timedelta(minutes=minutes)))
        if not enabled:
            _set_setting(db, "auto_refresh_running", "0")
    return get_auto_refresh_settings()


def mark_auto_refresh_started() -> None:
    with get_db() as db:
        _ensure_auto_refresh_defaults(db)
        _set_setting(db, "auto_refresh_running", "1")
        _set_setting(db, "auto_refresh_last_error", "")


def mark_auto_refresh_finished(*, error: str = "") -> dict[str, Any]:
    with get_db() as db:
        _ensure_auto_refresh_defaults(db)
        settings = _setting_rows(db)
        minutes = max(1, min(1440, int(settings.get("auto_refresh_minutes") or AUTO_REFRESH_DEFAULT_MINUTES)))
        now = _utc_now()
        _set_setting(db, "auto_refresh_running", "0")
        _set_setting(db, "auto_refresh_last_run_at", _iso(now))
        _set_setting(db, "auto_refresh_next_run_at", _iso(now + timedelta(minutes=minutes)))
        _set_setting(db, "auto_refresh_last_error", error)
    return get_auto_refresh_settings()


def auto_refresh_due() -> bool:
    settings = get_auto_refresh_settings()
    if not settings["enabled"] or settings["running"]:
        return False
    next_run_at = _parse_iso(settings.get("next_run_at"))
    if next_run_at is None:
        return True
    return _utc_now() >= next_run_at


def list_sites() -> list[dict[str, Any]]:
    with get_db() as db:
        return db.execute("SELECT * FROM sites ORDER BY id DESC").fetchall()


def get_site(site_id: int) -> dict[str, Any] | None:
    with get_db() as db:
        return db.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()


def _site_values(data: dict[str, Any]) -> tuple[Any, ...]:
    return (
        data["name"],
        data["base_url"],
        data["balance_endpoint"],
        data["method"],
        data["adapter"],
        data["cookie"],
        data["authorization"],
        data["extra_headers"],
        data["request_body"],
        data["balance_path"],
        data["currency_path"],
        data["default_currency"],
        data["scale"],
        data["notes"],
        1 if data["is_active"] else 0,
        1 if data["login_enabled"] else 0,
        data["login_url"],
        data["login_method"],
        data["login_username"],
        data["login_password"],
        data["login_headers"],
        data["login_body_template"],
        data["login_token_path"],
        data["login_token_prefix"],
        1 if data["login_cookie_from_response"] else 0,
        data["auth_fail_status_codes"],
        data["auth_fail_keywords"],
    )


def create_site(data: dict[str, Any]) -> int:
    with get_db() as db:
        cursor = db.execute(
            """
            INSERT INTO sites (
                name, base_url, balance_endpoint, method, adapter, cookie,
                authorization, extra_headers, request_body, balance_path, currency_path,
                default_currency, scale, notes, is_active, login_enabled, login_url,
                login_method, login_username, login_password, login_headers,
                login_body_template, login_token_path, login_token_prefix,
                login_cookie_from_response, auth_fail_status_codes, auth_fail_keywords
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _site_values(data),
        )
        return int(cursor.lastrowid)


def update_site(site_id: int, data: dict[str, Any]) -> None:
    with get_db() as db:
        db.execute(
            """
            UPDATE sites
            SET name = ?, base_url = ?, balance_endpoint = ?, method = ?,
                adapter = ?, cookie = ?, authorization = ?, extra_headers = ?,
                request_body = ?, balance_path = ?, currency_path = ?, default_currency = ?,
                scale = ?, notes = ?, is_active = ?, login_enabled = ?, login_url = ?,
                login_method = ?, login_username = ?, login_password = ?, login_headers = ?,
                login_body_template = ?, login_token_path = ?, login_token_prefix = ?,
                login_cookie_from_response = ?, auth_fail_status_codes = ?,
                auth_fail_keywords = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (*_site_values(data), site_id),
        )


def update_site_auth(
    site_id: int,
    *,
    authorization: str | None = None,
    cookie: str | None = None,
    login_status: str = "ok",
    last_login_error: str = "",
) -> None:
    assignments = [
        "login_status = ?",
        "last_login_error = ?",
        "last_login_at = datetime('now')",
        "updated_at = datetime('now')",
    ]
    values: list[Any] = [login_status, last_login_error]
    if authorization is not None:
        assignments.append("authorization = ?")
        values.append(authorization)
    if cookie is not None:
        assignments.append("cookie = ?")
        values.append(cookie)
    values.append(site_id)

    with get_db() as db:
        db.execute(f"UPDATE sites SET {', '.join(assignments)} WHERE id = ?", tuple(values))


def delete_site(site_id: int) -> None:
    with get_db() as db:
        db.execute("DELETE FROM sites WHERE id = ?", (site_id,))


def record_balance(site_id: int, result: dict[str, Any]) -> None:
    with get_db() as db:
        db.execute(
            """
            UPDATE sites
            SET last_balance = ?, last_currency = ?, last_status = ?,
                last_error = ?, last_checked_at = datetime('now'),
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (
                result.get("balance"),
                result.get("currency"),
                result["status"],
                result.get("error", ""),
                site_id,
            ),
        )
        db.execute(
            """
            INSERT INTO balance_history (
                site_id, balance, currency, status, error, raw_response_preview
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                site_id,
                result.get("balance"),
                result.get("currency"),
                result["status"],
                result.get("error", ""),
                result.get("raw_response_preview", ""),
            ),
        )


def list_history(site_id: int, limit: int = 100) -> list[dict[str, Any]]:
    with get_db() as db:
        return db.execute(
            """
            SELECT * FROM balance_history
            WHERE site_id = ?
            ORDER BY checked_at DESC, id DESC
            LIMIT ?
            """,
            (site_id, limit),
        ).fetchall()

