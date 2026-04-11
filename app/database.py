"""SQLite database initialization and CRUD helpers."""

import sqlite3
import os
import threading
from typing import Optional

DB_PATH = os.environ.get("DB_PATH", "/app/data/devices.db")
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Return a thread-local SQLite connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def init_db():
    """Create tables if they do not exist."""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS devices (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT    NOT NULL,
            host          TEXT    NOT NULL,
            port          INTEGER DEFAULT 5900,
            password      TEXT    DEFAULT '',
            group_name    TEXT    DEFAULT 'Ungrouped',
            group_color   TEXT    DEFAULT '#4589ff',
            sort_order    INTEGER DEFAULT 0,
            view_only     INTEGER DEFAULT 0,
            enabled       INTEGER DEFAULT 1,
            needs_password INTEGER DEFAULT 0,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS groups_table (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT    UNIQUE NOT NULL,
            color      TEXT    DEFAULT '#4589ff',
            sort_order INTEGER DEFAULT 0,
            enabled    INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    # Seed default settings
    defaults = {
        "grid_columns": "auto",
        "thumbnail_quality": "low",
        "dark_mode": "true",
        "auto_reconnect": "true",
        "health_check_interval": "30",
        "vnc_default_port": "5900",
        "app_username": "",
        "app_password": "",
    }
    for k, v in defaults.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v)
        )
    conn.commit()


# ── Device CRUD ────────────────────────────────────────────────────────

def get_all_devices() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM devices ORDER BY group_name, sort_order, id"
    ).fetchall()
    return [dict(r) for r in rows]


def get_device(device_id: int) -> Optional[dict]:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
    return dict(row) if row else None


def create_device(data: dict) -> int:
    conn = _get_conn()
    cur = conn.execute(
        """INSERT INTO devices
           (name, host, port, password, group_name, group_color,
            sort_order, view_only, enabled, needs_password)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            data["name"],
            data["host"],
            data.get("port", 5900),
            data.get("password", ""),
            data.get("group_name", "Ungrouped"),
            data.get("group_color", "#4589ff"),
            data.get("sort_order", 0),
            int(data.get("view_only", False)),
            int(data.get("enabled", True)),
            int(data.get("needs_password", False)),
        ),
    )
    conn.commit()
    # Auto-create group if it does not exist
    gname = data.get("group_name", "Ungrouped")
    gcolor = data.get("group_color", "#4589ff")
    conn.execute(
        "INSERT OR IGNORE INTO groups_table (name, color) VALUES (?, ?)",
        (gname, gcolor),
    )
    conn.commit()
    return cur.lastrowid


def update_device(device_id: int, updates: dict) -> bool:
    conn = _get_conn()
    existing = get_device(device_id)
    if not existing:
        return False
    fields = []
    values = []
    for col in [
        "name", "host", "port", "password", "group_name", "group_color",
        "sort_order", "view_only", "enabled", "needs_password",
    ]:
        if col in updates and updates[col] is not None:
            val = updates[col]
            if col in ("view_only", "enabled", "needs_password"):
                val = int(val)
            fields.append(f"{col} = ?")
            values.append(val)
    if not fields:
        return True
    values.append(device_id)
    conn.execute(
        f"UPDATE devices SET {', '.join(fields)} WHERE id = ?", values
    )
    conn.commit()
    # Auto-create group
    if "group_name" in updates and updates["group_name"]:
        conn.execute(
            "INSERT OR IGNORE INTO groups_table (name, color) VALUES (?, ?)",
            (updates["group_name"], updates.get("group_color", "#4589ff")),
        )
        conn.commit()
    return True


def delete_device(device_id: int) -> bool:
    conn = _get_conn()
    cur = conn.execute("DELETE FROM devices WHERE id = ?", (device_id,))
    conn.commit()
    return cur.rowcount > 0


def bulk_create_devices(devices: list[dict]) -> int:
    count = 0
    for d in devices:
        create_device(d)
        count += 1
    return count


def update_device_order(device_id: int, sort_order: int, group_name: str = None):
    conn = _get_conn()
    if group_name is not None:
        conn.execute(
            "UPDATE devices SET sort_order = ?, group_name = ? WHERE id = ?",
            (sort_order, group_name, device_id),
        )
    else:
        conn.execute(
            "UPDATE devices SET sort_order = ? WHERE id = ?",
            (sort_order, device_id),
        )
    conn.commit()


def find_device_by_host_port(host: str, port: int) -> Optional[dict]:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM devices WHERE host = ? AND port = ?", (host, port)
    ).fetchone()
    return dict(row) if row else None


# ── Group CRUD ─────────────────────────────────────────────────────────

def get_all_groups() -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM groups_table ORDER BY sort_order, id"
    ).fetchall()
    return [dict(r) for r in rows]


def create_group(name: str, color: str = "#4589ff") -> int:
    conn = _get_conn()
    cur = conn.execute(
        "INSERT OR IGNORE INTO groups_table (name, color) VALUES (?, ?)",
        (name, color),
    )
    conn.commit()
    if cur.lastrowid:
        return cur.lastrowid
    row = conn.execute(
        "SELECT id FROM groups_table WHERE name = ?", (name,)
    ).fetchone()
    return row["id"] if row else 0


def update_group(group_id: int, updates: dict) -> bool:
    conn = _get_conn()
    fields = []
    values = []
    for col in ["name", "color", "sort_order", "enabled"]:
        if col in updates and updates[col] is not None:
            val = updates[col]
            if col == "enabled":
                val = int(val)
            fields.append(f"{col} = ?")
            values.append(val)
    if not fields:
        return True
    values.append(group_id)
    cur = conn.execute(
        f"UPDATE groups_table SET {', '.join(fields)} WHERE id = ?", values
    )
    conn.commit()
    return cur.rowcount > 0


def delete_group(group_id: int) -> bool:
    conn = _get_conn()
    row = conn.execute(
        "SELECT name FROM groups_table WHERE id = ?", (group_id,)
    ).fetchone()
    if not row:
        return False
    conn.execute(
        "UPDATE devices SET group_name = 'Ungrouped' WHERE group_name = ?",
        (row["name"],),
    )
    conn.execute("DELETE FROM groups_table WHERE id = ?", (group_id,))
    conn.commit()
    return True


def get_group_device_counts() -> dict:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT group_name, COUNT(*) as cnt FROM devices GROUP BY group_name"
    ).fetchall()
    return {r["group_name"]: r["cnt"] for r in rows}


# ── Settings ───────────────────────────────────────────────────────────

def get_all_settings() -> dict:
    conn = _get_conn()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def get_setting(key: str, default: str = None) -> Optional[str]:
    conn = _get_conn()
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row else default


def update_setting(key: str, value: str):
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, value),
    )
    conn.commit()


def update_settings(settings: dict):
    for k, v in settings.items():
        if v is not None:
            update_setting(k, str(v))
