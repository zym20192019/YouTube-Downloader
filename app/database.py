"""SQLite database module for YouTube Downloader persistence.

Uses WAL mode for better concurrency and crash recovery.
All CRUD operations for tasks, queue, subscriptions, paths, config, tokens.
Auto-migrates from JSON files on first run.
"""

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

# Database path
DATA_DIR = Path("/root/youtube-downloader/data")
DB_PATH = DATA_DIR / "ytdl.db"

# Thread-local storage for connections
_local = threading.local()

# JSON files for migration
TASK_HISTORY_FILE = Path("/root/youtube-downloader/task_history.json")
SUBSCRIPTIONS_FILE = Path("/root/youtube-downloader/subscriptions.json")
PATH_CONFIG_FILE = Path("/root/youtube-downloader/path_config.json")
CONFIG_FILE = Path("/root/youtube-downloader/config.json")


def get_db() -> sqlite3.Connection:
    """Get thread-local SQLite connection with WAL mode."""
    if not hasattr(_local, "conn") or _local.conn is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _local.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA busy_timeout=5000")
        _local.conn.row_factory = sqlite3.Row
    return _local.conn


def init_db() -> None:
    """Initialize database schema."""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            url TEXT,
            title TEXT,
            format TEXT DEFAULT 'best',
            quality TEXT,
            status TEXT DEFAULT 'queued',
            progress REAL DEFAULT 0,
            speed TEXT,
            eta TEXT,
            filename TEXT,
            filepath TEXT,
            filesize INTEGER,
            error TEXT,
            thumbnail TEXT,
            duration REAL,
            created_at TEXT,
            updated_at TEXT,
            cloud_path TEXT,
            -- playlist fields
            is_playlist INTEGER DEFAULT 0,
            playlist_title TEXT,
            playlist_total INTEGER,
            playlist_current INTEGER,
            playlist_url TEXT,
            parent_id TEXT,
            playlist_index INTEGER,
            playlist_info TEXT,
            child_tasks TEXT,
            hdr TEXT
        );

        CREATE TABLE IF NOT EXISTS queued_downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            url TEXT NOT NULL,
            format TEXT DEFAULT 'best',
            quality TEXT,
            hdr TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS subscriptions (
            sub_id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            name TEXT,
            auto_download INTEGER DEFAULT 1,
            format TEXT DEFAULT 'best',
            quality TEXT,
            created_at TEXT,
            last_checked TEXT,
            last_video_count INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS paths (
            path_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            path TEXT NOT NULL,
            icon TEXT DEFAULT '📁',
            auto_move INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tokens (
            token TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()

    # Run migration if this is first run
    migrate_from_json()


def migrate_from_json() -> None:
    """Migrate existing JSON files to SQLite on first run."""
    conn = get_db()

    # Check if migration already done by looking for existing data
    cursor = conn.execute("SELECT COUNT(*) FROM tasks")
    if cursor.fetchone()[0] > 0:
        return  # Already has data, skip migration

    # Migrate task_history.json
    if TASK_HISTORY_FILE.exists():
        try:
            with open(TASK_HISTORY_FILE, "r") as f:
                tasks = json.load(f)
            if isinstance(tasks, dict):
                for task_id, task in tasks.items():
                    insert_task_from_dict(task)
            # skip if it's a list (empty or not a dict)
            TASK_HISTORY_FILE.rename(TASK_HISTORY_FILE.with_suffix(".json.bak"))
        except (json.JSONDecodeError, IOError, OSError):
            pass

    # Migrate subscriptions.json
    if SUBSCRIPTIONS_FILE.exists():
        try:
            with open(SUBSCRIPTIONS_FILE, "r") as f:
                subs = json.load(f)
            for sub in subs:
                insert_sub_from_dict(sub)
            SUBSCRIPTIONS_FILE.rename(SUBSCRIPTIONS_FILE.with_suffix(".json.bak"))
        except (json.JSONDecodeError, IOError, OSError):
            pass

    # Migrate path_config.json
    if PATH_CONFIG_FILE.exists():
        try:
            with open(PATH_CONFIG_FILE, "r") as f:
                paths = json.load(f)
            for p in paths:
                insert_path_from_dict(p)
            PATH_CONFIG_FILE.rename(PATH_CONFIG_FILE.with_suffix(".json.bak"))
        except (json.JSONDecodeError, IOError, OSError):
            pass

    # Migrate config.json
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)
            for key, value in cfg.items():
                set_config(key, value)
            CONFIG_FILE.rename(CONFIG_FILE.with_suffix(".json.bak"))
        except (json.JSONDecodeError, IOError, OSError):
            pass


# ── Tasks CRUD ──────────────────────────────────────────────────────────────

def insert_task_from_dict(task: dict) -> None:
    """Insert a task from a dictionary (used during migration)."""
    conn = get_db()
    conn.execute("""
        INSERT OR REPLACE INTO tasks (
            task_id, url, title, format, quality, status, progress, speed, eta,
            filename, filepath, filesize, error, thumbnail, duration,
            created_at, updated_at, cloud_path, is_playlist, playlist_title,
            playlist_total, playlist_current, playlist_url, parent_id,
            playlist_index, playlist_info, child_tasks, hdr
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        task.get("task_id"),
        task.get("url"),
        task.get("title"),
        task.get("format", "best"),
        task.get("quality"),
        task.get("status", "queued"),
        task.get("progress", 0),
        task.get("speed"),
        task.get("eta"),
        task.get("filename"),
        task.get("filepath"),
        task.get("filesize"),
        task.get("error"),
        task.get("thumbnail"),
        task.get("duration"),
        task.get("created_at"),
        task.get("updated_at"),
        task.get("cloud_path"),
        task.get("is_playlist", 0),
        task.get("playlist_title"),
        task.get("playlist_total"),
        task.get("playlist_current"),
        task.get("playlist_url"),
        task.get("parent_id"),
        task.get("playlist_index"),
        json.dumps(task.get("playlist_info")) if task.get("playlist_info") else None,
        json.dumps(task.get("child_tasks")) if task.get("child_tasks") else None,
        task.get("hdr"),
    ))
    conn.commit()


def create_task(task_id: str, url: str, fmt: str, quality: Optional[str] = None) -> str:
    """Create a new download task."""
    now = datetime.now().isoformat()
    conn = get_db()
    conn.execute("""
        INSERT INTO tasks (
            task_id, url, format, quality, status, progress, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'queued', 0, ?, ?)
    """, (task_id, url, fmt, quality, now, now))
    conn.commit()
    return task_id


def get_task(task_id: str) -> Optional[dict]:
    """Get a task by ID."""
    conn = get_db()
    cursor = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
    row = cursor.fetchone()
    if not row:
        return None
    return _row_to_task(row)


def _row_to_task(row: sqlite3.Row) -> dict:
    """Convert a database row to a task dictionary."""
    task = dict(row)
    # Parse JSON fields
    if task.get("playlist_info"):
        task["playlist_info"] = json.loads(task["playlist_info"])
    if task.get("child_tasks"):
        task["child_tasks"] = json.loads(task["child_tasks"])
    # Convert is_playlist to bool
    task["is_playlist"] = bool(task.get("is_playlist", 0))
    # Add playlist_progress for compatibility
    if task.get("is_playlist"):
        task["playlist_progress"] = {
            "current": task.get("playlist_current", 0),
            "total": task.get("playlist_total", 0),
        }
    return task


def update_task(task_id: str, **kwargs) -> None:
    """Update a task with arbitrary fields."""
    conn = get_db()
    # Build dynamic UPDATE query
    fields = []
    values = []
    for key, value in kwargs.items():
        if key in ("playlist_info", "child_tasks"):
            value = json.dumps(value) if value else None
        elif key == "is_playlist":
            value = 1 if value else 0
        elif key == "playlist_progress":
            # Handle playlist_progress as dict
            if isinstance(value, dict):
                fields.append("playlist_current = ?")
                fields.append("playlist_total = ?")
                values.append(value.get("current", 0))
                values.append(value.get("total", 0))
            continue
        fields.append(f"{key} = ?")
        values.append(value)

    if fields:
        fields.append("updated_at = ?")
        values.append(datetime.now().isoformat())
        values.append(task_id)
        conn.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE task_id = ?", values)
        conn.commit()


def delete_task(task_id: str) -> bool:
    """Delete a task by ID."""
    conn = get_db()
    conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
    # Also delete from queue if present
    conn.execute("DELETE FROM queued_downloads WHERE task_id = ?", (task_id,))
    conn.commit()
    return True


def list_tasks() -> List[dict]:
    """List all tasks ordered by created_at descending."""
    conn = get_db()
    cursor = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC")
    return [_row_to_task(row) for row in cursor.fetchall()]


def set_task_cancelled(task_id: str) -> None:
    """Mark a task as cancelled."""
    update_task(task_id, status="cancelled")


def is_task_cancelled(task_id: str) -> bool:
    """Check if a task is cancelled."""
    task = get_task(task_id)
    return task and task.get("status") == "cancelled"


def create_playlist_task(task_id: str, url: str, fmt: str, quality: Optional[str] = None) -> str:
    """Create a parent task for playlist download."""
    now = datetime.now().isoformat()
    conn = get_db()
    conn.execute("""
        INSERT INTO tasks (
            task_id, url, title, format, quality, status, progress, created_at, updated_at,
            is_playlist, child_tasks
        ) VALUES (?, ?, '加载中...', ?, ?, 'queued', 0, ?, ?, 1, '[]')
    """, (task_id, url, fmt, quality, now, now))
    conn.commit()
    return task_id


def create_child_task(task_id: str, parent_id: str, url: str, title: str,
                      fmt: str, quality: Optional[str] = None,
                      thumbnail: Optional[str] = None, duration: Optional[int] = None) -> str:
    """Create a child task for a playlist video."""
    now = datetime.now().isoformat()
    conn = get_db()
    conn.execute("""
        INSERT INTO tasks (
            task_id, url, title, format, quality, status, progress, created_at, updated_at,
            thumbnail, duration, parent_id
        ) VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?, ?)
    """, (task_id, url, title, fmt, quality, now, now, thumbnail, duration, parent_id))
    # Update parent's child_tasks list
    parent = get_task(parent_id)
    if parent:
        child_tasks = parent.get("child_tasks", [])
        child_tasks.append(task_id)
        update_task(parent_id, child_tasks=child_tasks)
    conn.commit()
    return task_id


def get_child_tasks(parent_id: str) -> List[dict]:
    """Get all child tasks for a playlist."""
    conn = get_db()
    cursor = conn.execute(
        "SELECT * FROM tasks WHERE parent_id = ? ORDER BY task_id",
        (parent_id,)
    )
    return [_row_to_task(row) for row in cursor.fetchall()]


# ── Queue CRUD ───────────────────────────────────────────────────────────────

def enqueue(task_id: str, url: str, fmt: str, quality: Optional[str] = None, hdr: Optional[str] = None) -> None:
    """Add a download to the queue (persisted)."""
    conn = get_db()
    conn.execute("""
        INSERT INTO queued_downloads (task_id, url, format, quality, hdr)
        VALUES (?, ?, ?, ?, ?)
    """, (task_id, url, fmt, quality, hdr))
    conn.commit()


def dequeue() -> Optional[tuple]:
    """Remove and return the next item from the queue."""
    conn = get_db()
    cursor = conn.execute(
        "SELECT id, task_id, url, format, quality, hdr FROM queued_downloads ORDER BY id LIMIT 1"
    )
    row = cursor.fetchone()
    if not row:
        return None
    # Delete from queue
    conn.execute("DELETE FROM queued_downloads WHERE id = ?", (row["id"],))
    conn.commit()
    return (row["task_id"], row["url"], row["format"], row["quality"], row["hdr"])


def drain_queue() -> int:
    """Remove all items from the queue."""
    conn = get_db()
    cursor = conn.execute("SELECT COUNT(*) FROM queued_downloads")
    count = cursor.fetchone()[0]
    conn.execute("DELETE FROM queued_downloads")
    conn.commit()
    return count


def queue_size() -> int:
    """Get current queue size."""
    conn = get_db()
    cursor = conn.execute("SELECT COUNT(*) FROM queued_downloads")
    return cursor.fetchone()[0]


def restore_queue(queue_obj: Any) -> int:
    """Restore queue items from database to an asyncio.Queue object.

    Called at startup to repopulate the in-memory queue from persisted items.
    Returns number of items restored.
    """
    conn = get_db()
    cursor = conn.execute(
        "SELECT task_id, url, format, quality, hdr FROM queued_downloads ORDER BY id"
    )
    count = 0
    for row in cursor.fetchall():
        # Put items back into the asyncio.Queue
        # Note: This should be called from an async context
        queue_obj.put_nowait((row["task_id"], row["url"], row["format"], row["quality"], row["hdr"]))
        count += 1
    return count


def delete_queue_item(task_id: str) -> None:
    """Delete a specific item from the queue by task_id."""
    conn = get_db()
    conn.execute("DELETE FROM queued_downloads WHERE task_id = ?", (task_id,))
    conn.commit()


# ── Subscriptions CRUD ───────────────────────────────────────────────────────

def insert_sub_from_dict(sub: dict) -> None:
    """Insert a subscription from a dictionary (used during migration)."""
    conn = get_db()
    conn.execute("""
        INSERT OR REPLACE INTO subscriptions (
            sub_id, url, name, auto_download, format, quality, created_at, last_checked, last_video_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        sub.get("id"),
        sub.get("url"),
        sub.get("name"),
        1 if sub.get("auto_download", True) else 0,
        sub.get("format", "best"),
        sub.get("quality"),
        sub.get("created_at"),
        sub.get("last_checked"),
        sub.get("last_video_count", 0),
    ))
    conn.commit()


def insert_sub(sub_id: str, url: str, name: str, auto_download: bool = True,
               fmt: str = "best", quality: Optional[str] = None) -> None:
    """Insert a new subscription."""
    now = datetime.now().isoformat()
    conn = get_db()
    conn.execute("""
        INSERT INTO subscriptions (
            sub_id, url, name, auto_download, format, quality, created_at, last_checked, last_video_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, '', 0)
    """, (sub_id, url, name, 1 if auto_download else 0, fmt, quality, now))
    conn.commit()


def get_sub(sub_id: str) -> Optional[dict]:
    """Get a subscription by ID."""
    conn = get_db()
    cursor = conn.execute("SELECT * FROM subscriptions WHERE sub_id = ?", (sub_id,))
    row = cursor.fetchone()
    if not row:
        return None
    return dict(row)


def update_sub(sub_id: str, **kwargs) -> None:
    """Update a subscription."""
    conn = get_db()
    fields = []
    values = []
    for key, value in kwargs.items():
        if key == "auto_download":
            value = 1 if value else 0
        elif key == "id":
            continue  # Don't update id
        fields.append(f"{key} = ?")
        values.append(value)
    if fields:
        values.append(sub_id)
        conn.execute(f"UPDATE subscriptions SET {', '.join(fields)} WHERE sub_id = ?", values)
        conn.commit()


def delete_sub(sub_id: str) -> bool:
    """Delete a subscription."""
    conn = get_db()
    conn.execute("DELETE FROM subscriptions WHERE sub_id = ?", (sub_id,))
    conn.commit()
    return True


def list_subs() -> List[dict]:
    """List all subscriptions."""
    conn = get_db()
    cursor = conn.execute("SELECT * FROM subscriptions")
    result = []
    for row in cursor.fetchall():
        sub = dict(row)
        # Rename sub_id to id for API compatibility
        sub["id"] = sub["sub_id"]
        sub["auto_download"] = bool(sub["auto_download"])
        result.append(sub)
    return result


# ── Paths CRUD ───────────────────────────────────────────────────────────────

def insert_path_from_dict(p: dict) -> None:
    """Insert a path from a dictionary (used during migration)."""
    conn = get_db()
    conn.execute("""
        INSERT OR REPLACE INTO paths (path_id, name, path, icon, auto_move)
        VALUES (?, ?, ?, ?, ?)
    """, (
        p.get("id"),
        p.get("name"),
        p.get("path"),
        p.get("icon", "📁"),
        1 if p.get("auto_move", False) else 0,
    ))
    conn.commit()


def insert_path(path_id: str, name: str, path: str, icon: str = "📁", auto_move: bool = False) -> None:
    """Insert a new path."""
    conn = get_db()
    conn.execute("""
        INSERT INTO paths (path_id, name, path, icon, auto_move)
        VALUES (?, ?, ?, ?, ?)
    """, (path_id, name, path, icon, 1 if auto_move else 0))
    conn.commit()


def get_path(path_id: str) -> Optional[dict]:
    """Get a path by ID."""
    conn = get_db()
    cursor = conn.execute("SELECT * FROM paths WHERE path_id = ?", (path_id,))
    row = cursor.fetchone()
    if not row:
        return None
    p = dict(row)
    p["id"] = p["path_id"]
    p["auto_move"] = bool(p["auto_move"])
    return p


def update_path(path_id: str, **kwargs) -> None:
    """Update a path."""
    conn = get_db()
    fields = []
    values = []
    for key, value in kwargs.items():
        if key == "auto_move":
            value = 1 if value else 0
        elif key == "id":
            continue
        fields.append(f"{key} = ?")
        values.append(value)
    if fields:
        values.append(path_id)
        conn.execute(f"UPDATE paths SET {', '.join(fields)} WHERE path_id = ?", values)
        conn.commit()


def delete_path(path_id: str) -> bool:
    """Delete a path."""
    conn = get_db()
    conn.execute("DELETE FROM paths WHERE path_id = ?", (path_id,))
    conn.commit()
    return True


def list_paths() -> List[dict]:
    """List all paths."""
    conn = get_db()
    cursor = conn.execute("SELECT * FROM paths")
    result = []
    for row in cursor.fetchall():
        p = dict(row)
        p["id"] = p["path_id"]
        p["auto_move"] = bool(p["auto_move"])
        result.append(p)
    return result


def get_auto_move_path() -> Optional[dict]:
    """Get the path with auto_move enabled."""
    conn = get_db()
    cursor = conn.execute("SELECT * FROM paths WHERE auto_move = 1")
    row = cursor.fetchone()
    if not row:
        return None
    p = dict(row)
    p["id"] = p["path_id"]
    p["auto_move"] = True
    return p


def clear_auto_move_all() -> None:
    """Clear auto_move for all paths."""
    conn = get_db()
    conn.execute("UPDATE paths SET auto_move = 0")
    conn.commit()


def set_auto_move(path_id: str, enabled: bool) -> None:
    """Set auto_move for a specific path."""
    conn = get_db()
    conn.execute("UPDATE paths SET auto_move = ? WHERE path_id = ?", (1 if enabled else 0, path_id))
    conn.commit()


# ── Config ───────────────────────────────────────────────────────────────────

def get_config(key: str, default: Any = None) -> Any:
    """Get a config value."""
    conn = get_db()
    cursor = conn.execute("SELECT value FROM config WHERE key = ?", (key,))
    row = cursor.fetchone()
    if not row:
        return default
    value = row["value"]
    # Try to parse as JSON for complex values
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def set_config(key: str, value: Any) -> None:
    """Set a config value."""
    conn = get_db()
    if isinstance(value, (dict, list)):
        value = json.dumps(value)
    else:
        value = str(value)
    conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))
    conn.commit()


def get_all_config() -> dict:
    """Get all config as a dictionary."""
    conn = get_db()
    cursor = conn.execute("SELECT key, value FROM config")
    result = {}
    for row in cursor.fetchall():
        value = row["value"]
        try:
            result[row["key"]] = json.loads(value)
        except json.JSONDecodeError:
            result[row["key"]] = value
    return result


# ── Tokens ───────────────────────────────────────────────────────────────────

def insert_token(token: str, username: str) -> None:
    """Insert a new auth token."""
    conn = get_db()
    conn.execute("INSERT INTO tokens (token, username) VALUES (?, ?)", (token, username))
    conn.commit()


def get_token(token: str) -> Optional[str]:
    """Get username for a token, or None if not found."""
    conn = get_db()
    cursor = conn.execute("SELECT username FROM tokens WHERE token = ?", (token,))
    row = cursor.fetchone()
    return row["username"] if row else None


def delete_token(token: str) -> bool:
    """Delete a token."""
    conn = get_db()
    conn.execute("DELETE FROM tokens WHERE token = ?", (token,))
    conn.commit()
    return True


def list_tokens() -> Dict[str, str]:
    """List all tokens as token -> username dict."""
    conn = get_db()
    cursor = conn.execute("SELECT token, username FROM tokens")
    return {row["token"]: row["username"] for row in cursor.fetchall()}