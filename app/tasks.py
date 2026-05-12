"""Thread-safe task manager with SQLite persistence."""

import threading
from datetime import datetime
from typing import Dict, Optional, List, Callable

from app.models import ProgressMessage, DownloadFormat
from app.database import (
    init_db, create_task, get_task, update_task, delete_task, list_tasks,
    create_playlist_task, create_child_task, get_child_tasks,
    set_task_cancelled, is_task_cancelled, set_auto_move,
    get_auto_move_path, clear_auto_move_all
)


class TaskManager:
    """Thread-safe task manager with SQLite persistence.

    Task data is stored in SQLite. WebSocket subscribers are kept in-memory
    since they cannot be persisted.
    """

    def __init__(self):
        self.subscribers: Dict[str, List[Callable]] = {}
        self._lock = threading.Lock()
        init_db()

    def cancel_task(self, task_id: str):
        """Mark a task as cancelled to stop the background process."""
        set_task_cancelled(task_id)
        with self._lock:
            self.subscribers.pop(task_id, None)

    def is_task_cancelled(self, task_id: str) -> bool:
        return is_task_cancelled(task_id)

    def create_task(self, url: str, fmt: DownloadFormat, quality: Optional[str] = None) -> str:
        """Create a new download task."""
        import uuid
        task_id = str(uuid.uuid4())[:8]
        create_task(task_id, url, fmt.value, quality)
        return task_id

    def get_task(self, task_id: str) -> Optional[dict]:
        return get_task(task_id)

    def list_tasks(self) -> List[dict]:
        return list_tasks()

    def update_task(self, task_id: str, **kwargs):
        update_task(task_id, **kwargs)

    def set_progress(self, task_id: str, progress: float, speed: Optional[str] = None, eta: Optional[str] = None):
        update_task(task_id, progress=progress, speed=speed, eta=eta)
        self._notify(task_id, ProgressMessage(
            type="progress",
            task_id=task_id,
            percent=round(progress, 2),
            speed=speed,
            eta=eta,
        ))

    def set_done(self, task_id: str, filename: str, filepath: str, filesize: int):
        update_task(task_id, status="done", progress=100.0, filename=filename,
                    filepath=filepath, filesize=filesize)
        self._notify(task_id, ProgressMessage(
            type="done",
            task_id=task_id,
            filename=filename,
            filepath=filepath,
        ))

    def set_error(self, task_id: str, error: str):
        update_task(task_id, status="error", error=error)
        self._notify(task_id, ProgressMessage(
            type="error",
            task_id=task_id,
            message=error,
        ))

    def delete_task(self, task_id: str) -> bool:
        self.cancel_task(task_id)  # Signal the background process to stop
        return delete_task(task_id)

    def set_metadata(self, task_id: str, title: str, thumbnail: Optional[str], duration: Optional[int]):
        update_task(task_id, title=title, thumbnail=thumbnail, duration=duration)

    def create_playlist_task(self, url: str, fmt: DownloadFormat, quality: Optional[str] = None) -> str:
        """Create a parent task for playlist download."""
        import uuid
        task_id = "pl_" + str(uuid.uuid4())[:8]
        create_playlist_task(task_id, url, fmt.value, quality)
        return task_id

    def create_child_task(self, task_id: str, parent_id: str, url: str, title: str,
                          fmt: DownloadFormat, quality: Optional[str] = None,
                          thumbnail: Optional[str] = None, duration: Optional[int] = None) -> str:
        """Create a child task for a playlist video."""
        return create_child_task(task_id, parent_id, url, title, fmt.value, quality, thumbnail, duration)

    def set_playlist_info(self, task_id: str, info: dict):
        update_task(task_id, playlist_info=info, title=info.get("title"), thumbnail=info.get("thumbnail"))

    def set_playlist_progress(self, task_id: str, current: int, total: int):
        progress = (current / total * 100) if total > 0 else 0
        update_task(task_id, playlist_current=current, playlist_total=total, progress=progress)
        self._notify(task_id, ProgressMessage(
            type="playlist_progress",
            task_id=task_id,
            percent=round(progress, 2),
            message=f"Downloading video {current}/{total}",
        ))

    def set_playlist_done(self, task_id: str):
        update_task(task_id, status="done", progress=100.0)
        self._notify(task_id, ProgressMessage(
            type="done",
            task_id=task_id,
            message="Playlist download complete",
        ))

    def set_child_done(self, task_id: str, filename: str, filepath: str, filesize: int):
        update_task(task_id, status="done", progress=100.0, filename=filename,
                    filepath=filepath, filesize=filesize)
        self._notify(task_id, ProgressMessage(
            type="done",
            task_id=task_id,
            filename=filename,
            filepath=filepath,
        ))

    def set_child_error(self, task_id: str, error: str):
        update_task(task_id, status="error", error=error)

    def pause_playlist(self, task_id: str) -> bool:
        """Pause a downloading playlist. Returns True if paused."""
        task = get_task(task_id)
        if not task or not task.get("is_playlist"):
            return False
        if task.get("status") not in ("downloading", "queued"):
            return False
        update_task(task_id, status="paused")
        self._notify(task_id, ProgressMessage(
            type="paused",
            task_id=task_id,
            message="Playlist paused",
        ))
        return True

    def resume_playlist(self, task_id: str) -> bool:
        """Resume a paused playlist. Returns True if resumed."""
        task = get_task(task_id)
        if not task or not task.get("is_playlist"):
            return False
        if task.get("status") != "paused":
            return False
        update_task(task_id, status="downloading")
        self._notify(task_id, ProgressMessage(
            type="resumed",
            task_id=task_id,
            message="Playlist resumed",
        ))
        return True

    def get_child_tasks(self, parent_id: str) -> List[dict]:
        """Get all child tasks for a playlist."""
        return get_child_tasks(parent_id)

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