from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum
from datetime import datetime


class DownloadFormat(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    BEST = "best"


class CloudDriveTarget(str, Enum):
    DRIVE_115 = "115"
    BAIDU = "baidu"


class DownloadRequest(BaseModel):
    url: str = Field(..., description="YouTube video URL")
    format: DownloadFormat = Field(default=DownloadFormat.BEST, description="Download format")
    quality: Optional[str] = Field(default=None, description="Quality preference (e.g. '1080p', '720p')")


class MoveRequest(BaseModel):
    task_id: str = Field(..., description="Task ID")
    target: CloudDriveTarget = Field(..., description="Cloud drive target")


class TaskStatus(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    DONE = "done"
    ERROR = "error"
    MOVING = "moving"
    MOVED = "moved"


class TaskResponse(BaseModel):
    task_id: str
    url: str
    title: Optional[str] = None
    format: DownloadFormat
    status: TaskStatus
    progress: float = 0.0
    speed: Optional[str] = None
    eta: Optional[str] = None
    filename: Optional[str] = None
    filepath: Optional[str] = None
    filesize: Optional[int] = None
    error: Optional[str] = None
    thumbnail: Optional[str] = None
    duration: Optional[int] = None
    created_at: str
    updated_at: str
    cloud_path: Optional[str] = None


class TaskListResponse(BaseModel):
    tasks: List[TaskResponse]
    total: int


class FileItem(BaseModel):
    task_id: str
    filename: str
    filepath: str
    filesize: int
    title: str
    thumbnail: Optional[str] = None
    duration: Optional[int] = None
    format: str
    created_at: str
    cloud_path: Optional[str] = None


class MoveResponse(BaseModel):
    success: bool
    task_id: str
    original_path: str
    new_path: str
    target: str


class ProgressMessage(BaseModel):
    type: str  # "progress", "done", "error"
    task_id: Optional[str] = None
    percent: Optional[float] = None
    speed: Optional[str] = None
    eta: Optional[str] = None
    filename: Optional[str] = None
    filepath: Optional[str] = None
    message: Optional[str] = None
