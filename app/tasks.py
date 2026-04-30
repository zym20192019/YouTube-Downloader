import asyncio
import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List, Callable, Any

from app.models import TaskResponse, TaskStatus, DownloadFormat, ProgressMessage

TASK_HISTORY_FILE = Path("/root/youtube-downloader/task_history.json")


class TaskManager:
    """Thread-safe in-memory task manager with JSON persistence."""

    def __init__(self):
        self.tasks: Dict[str, dict] = {}
        self.subscribers: Dict[str, List[Callable]] = {}
        self._lock = threading.Lock()
        self._load_history()

    def _load_history(self):
        """Load task history from JSON file on startup."""
        if TASK_HISTORY_FILE.exists():
            try:
                with open(TASK_HISTORY_FILE, "r") as f:
                    history = json.load(f)
                with self._lock:
                    self.tasks = history
            except (json.JSONDecodeError, IOError):
                pass

    def _save_history(self):
        """Persist current tasks to JSON file."""
        try:
            with open(TASK_HISTORY_FILE, "w") as f:
                json.dump(self.tasks, f, indent=2)
        except IOError:
            pass

    def create_task(self, url: str, fmt: DownloadFormat, quality: Optional[str] = None) -> str:
        task_id = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat()
        with self._lock:
            self.tasks[task_id] = {
                "task_id": task_id,
                "url": url,
                "title": None,
                "format": fmt,
                "quality": quality,
                "status": TaskStatus.QUEUED,
                "progress": 0.0,
                "speed": None,
                "eta": None,
                "filename": None,
                "filepath": None,
                "filesize": None,
                "error": None,
                "thumbnail": None,
                "duration": None,
                "created_at": now,
                "updated_at": now,
                "cloud_path": None,
            }
        self._save_history()
        return task_id

    def get_task(self, task_id: str) -> Optional[dict]:
        with self._lock:
            return self.tasks.get(task_id)

    def list_tasks(self) -> List[dict]:
        with self._lock:
            return sorted(
                self.tasks.values(),
                key=lambda t: t["created_at"],
                reverse=True,
            )

    def update_task(self, task_id: str, **kwargs):
        with self._lock:
            if task_id in self.tasks:
                self.tasks[task_id].update(kwargs)
                self.tasks[task_id]["updated_at"] = datetime.now().isoformat()
        self._save_history()

    def set_progress(self, task_id: str, progress: float, speed: Optional[str] = None, eta: Optional[str] = None):
        with self._lock:
            if task_id in self.tasks:
                self.tasks[task_id]["progress"] = progress
                if speed:
                    self.tasks[task_id]["speed"] = speed
                if eta:
                    self.tasks[task_id]["eta"] = eta
                self.tasks[task_id]["updated_at"] = datetime.now().isoformat()
        self._notify(task_id, ProgressMessage(
            type="progress",
            task_id=task_id,
            percent=round(progress, 2),
            speed=speed,
            eta=eta,
        ))
        # Save every 10% progress to avoid too many writes
        if int(progress) % 10 == 0:
            self._save_history()

    def set_done(self, task_id: str, filename: str, filepath: str, filesize: int):
        with self._lock:
            if task_id in self.tasks:
                self.tasks[task_id]["status"] = TaskStatus.DONE
                self.tasks[task_id]["progress"] = 100.0
                self.tasks[task_id]["filename"] = filename
                self.tasks[task_id]["filepath"] = filepath
                self.tasks[task_id]["filesize"] = filesize
                self.tasks[task_id]["updated_at"] = datetime.now().isoformat()
        self._save_history()
        self._notify(task_id, ProgressMessage(
            type="done",
            task_id=task_id,
            filename=filename,
            filepath=filepath,
        ))

    def set_error(self, task_id: str, error: str):
        with self._lock:
            if task_id in self.tasks:
                self.tasks[task_id]["status"] = TaskStatus.ERROR
                self.tasks[task_id]["error"] = error
                self.tasks[task_id]["updated_at"] = datetime.now().isoformat()
        self._save_history()
        self._notify(task_id, ProgressMessage(
            type="error",
            task_id=task_id,
            message=error,
        ))

    def delete_task(self, task_id: str) -> bool:
        with self._lock:
            if task_id in self.tasks:
                del self.tasks[task_id]
                self.subscribers.pop(task_id, None)
        self._save_history()
        with self._lock:
            return task_id not in self.tasks

    def set_metadata(self, task_id: str, title: str, thumbnail: Optional[str], duration: Optional[int]):
        with self._lock:
            if task_id in self.tasks:
                self.tasks[task_id]["title"] = title
                self.tasks[task_id]["thumbnail"] = thumbnail
                self.tasks[task_id]["duration"] = duration
                self.tasks[task_id]["updated_at"] = datetime.now().isoformat()
        self._save_history()

    def create_playlist_task(self, url: str, fmt: DownloadFormat, quality: Optional[str] = None) -> str:
        """Create a parent task for playlist download."""
        task_id = "pl_" + str(uuid.uuid4())[:8]
        now = datetime.now().isoformat()
        with self._lock:
            self.tasks[task_id] = {
                "task_id": task_id,
                "url": url,
                "title": "加载中...",
                "format": fmt,
                "quality": quality,
                "status": TaskStatus.QUEUED,
                "progress": 0.0,
                "speed": None,
                "eta": None,
                "filename": None,
                "filepath": None,
                "filesize": None,
                "error": None,
                "thumbnail": None,
                "duration": None,
                "created_at": now,
                "updated_at": now,
                "cloud_path": None,
                "is_playlist": True,
                "playlist_info": None,
                "child_tasks": [],
                "playlist_progress": {"current": 0, "total": 0},
            }
        self._save_history()
        return task_id

    def create_child_task(self, task_id: str, parent_id: str, url: str, title: str, fmt: DownloadFormat, quality: Optional[str] = None, thumbnail: Optional[str] = None, duration: Optional[int] = None) -> str:
        """Create a child task for a playlist video."""
        now = datetime.now().isoformat()
        with self._lock:
            self.tasks[task_id] = {
                "task_id": task_id,
                "url": url,
                "title": title,
                "format": fmt,
                "quality": quality,
                "status": TaskStatus.QUEUED,
                "progress": 0.0,
                "speed": None,
                "eta": None,
                "filename": None,
                "filepath": None,
                "filesize": None,
                "error": None,
                "thumbnail": thumbnail,
                "duration": duration,
                "created_at": now,
                "updated_at": now,
                "cloud_path": None,
                "parent_id": parent_id,
            }
            if parent_id in self.tasks:
                self.tasks[parent_id]["child_tasks"].append(task_id)
        self._save_history()
        return task_id

    def set_playlist_info(self, task_id: str, info: dict):
        with self._lock:
            if task_id in self.tasks:
                self.tasks[task_id]["playlist_info"] = info
                self.tasks[task_id]["title"] = info.get("title")
                self.tasks[task_id]["thumbnail"] = info.get("thumbnail")
                self.tasks[task_id]["updated_at"] = datetime.now().isoformat()
        self._save_history()

    def set_playlist_progress(self, task_id: str, current: int, total: int):
        with self._lock:
            if task_id in self.tasks:
                self.tasks[task_id]["playlist_progress"] = {"current": current, "total": total}
                self.tasks[task_id]["progress"] = (current / total * 100) if total > 0 else 0
                self.tasks[task_id]["updated_at"] = datetime.now().isoformat()
        self._save_history()
        self._notify(task_id, ProgressMessage(
            type="playlist_progress",
            task_id=task_id,
            percent=round(self.tasks[task_id]["progress"], 2),
            message=f"Downloading video {current}/{total}",
        ))

    def set_playlist_done(self, task_id: str):
        with self._lock:
            if task_id in self.tasks:
                self.tasks[task_id]["status"] = TaskStatus.DONE
                self.tasks[task_id]["progress"] = 100.0
                self.tasks[task_id]["updated_at"] = datetime.now().isoformat()
        self._save_history()
        self._notify(task_id, ProgressMessage(
            type="done",
            task_id=task_id,
            message="Playlist download complete",
        ))

    def set_child_done(self, task_id: str, filename: str, filepath: str, filesize: int):
        with self._lock:
            if task_id in self.tasks:
                self.tasks[task_id]["status"] = TaskStatus.DONE
                self.tasks[task_id]["progress"] = 100.0
                self.tasks[task_id]["filename"] = filename
                self.tasks[task_id]["filepath"] = filepath
                self.tasks[task_id]["filesize"] = filesize
                self.tasks[task_id]["updated_at"] = datetime.now().isoformat()
        self._save_history()
        self._notify(task_id, ProgressMessage(
            type="done",
            task_id=task_id,
            filename=filename,
            filepath=filepath,
        ))

    def set_child_error(self, task_id: str, error: str):
        with self._lock:
            if task_id in self.tasks:
                self.tasks[task_id]["status"] = TaskStatus.ERROR
                self.tasks[task_id]["error"] = error
                self.tasks[task_id]["updated_at"] = datetime.now().isoformat()
        self._save_history()
        self._notify(task_id, ProgressMessage(
            type="error",
            task_id=task_id,
            message=error,
        ))

    def get_child_tasks(self, parent_id: str) -> List[dict]:
        """Get all child tasks for a playlist."""
        with self._lock:
            return sorted(
                [t for t in self.tasks.values() if t.get("parent_id") == parent_id],
                key=lambda t: t["task_id"],
            )

    def _notify(self, task_id: str, message: ProgressMessage):
        with self._lock:
            for callback in self.subscribers.get(task_id, []):
                try:
                    callback(message)
                except Exception:
                    pass

    def subscribe(self, task_id: str, callback: Callable) -> Callable:
        with self._lock:
            if task_id not in self.subscribers:
                self.subscribers[task_id] = []
            self.subscribers[task_id].append(callback)

        def unsubscribe():
            with self._lock:
                if task_id in self.subscribers:
                    try:
                        self.subscribers[task_id].remove(callback)
                    except ValueError:
                        pass

        return unsubscribe


# Global instance
task_manager = TaskManager()
