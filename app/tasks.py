import asyncio
import threading
import uuid
from datetime import datetime
from typing import Dict, Optional, List, Callable, Any

from app.models import TaskResponse, TaskStatus, DownloadFormat, ProgressMessage


class TaskManager:
    """Thread-safe in-memory task manager."""

    def __init__(self):
        self.tasks: Dict[str, dict] = {}
        self.subscribers: Dict[str, List[Callable]] = {}
        self._lock = threading.Lock()

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

    def set_done(self, task_id: str, filename: str, filepath: str, filesize: int):
        with self._lock:
            if task_id in self.tasks:
                self.tasks[task_id]["status"] = TaskStatus.DONE
                self.tasks[task_id]["progress"] = 100.0
                self.tasks[task_id]["filename"] = filename
                self.tasks[task_id]["filepath"] = filepath
                self.tasks[task_id]["filesize"] = filesize
                self.tasks[task_id]["updated_at"] = datetime.now().isoformat()
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
                return True
        return False

    def set_metadata(self, task_id: str, title: str, thumbnail: Optional[str], duration: Optional[int]):
        with self._lock:
            if task_id in self.tasks:
                self.tasks[task_id]["title"] = title
                self.tasks[task_id]["thumbnail"] = thumbnail
                self.tasks[task_id]["duration"] = duration
                self.tasks[task_id]["updated_at"] = datetime.now().isoformat()

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
