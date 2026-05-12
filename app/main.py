import os
import uuid
import asyncio
from pathlib import Path
from typing import Optional
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app.models import (
    DownloadRequest, MoveRequest, TaskResponse, TaskListResponse,
    MoveResponse, FileItem, TaskStatus, DownloadFormat, CloudPath
)
from app.tasks import task_manager
from app.downloader import download_video, move_to_cloud_drive, DOWNLOAD_DIR, COOKIE_FILE
from app.database import (
    init_db, enqueue, dequeue, drain_queue, queue_size, restore_queue,
    delete_queue_item, list_subs, insert_sub, update_sub, delete_sub, get_sub,
    list_paths, insert_path, delete_path, get_path, update_path,
    get_auto_move_path, clear_auto_move_all, set_auto_move,
    get_config, set_config, get_all_config,
    insert_token, get_token, delete_token, list_tokens,
)


class BatchMoveRequest(BaseModel):
    task_ids: list[str]
    target_path: str
    target_name: Optional[str] = None


class BatchDeleteRequest(BaseModel):
    task_ids: list[str]


class Subscription(BaseModel):
    id: str
    url: str
    name: str = ""
    auto_download: bool = True
    format: str = "best"
    quality: Optional[str] = None
    created_at: str = ""
    last_checked: str = ""
    last_video_count: int = 0


app = FastAPI(title="YouTube Downloader", description="Liquid Glass YouTube Video Downloader")

# Initialize database on import
init_db()

# Load concurrency config from database
_cfg = get_all_config()
MAX_CONCURRENT_DOWNLOADS = _cfg.get("max_concurrent_downloads", 3)
CD2_TEMP_DIR = _cfg.get("cd2_temp_dir", "/opt/docker/cd2/temp")
CHECK_INTERVAL = _cfg.get("check_interval", 5)

DOWNLOAD_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
download_queue: asyncio.Queue = asyncio.Queue()

# Track active CD2 uploads from manual moves
_active_uploads = 0
_upload_lock = asyncio.Lock()

# Auto-check subscriptions interval
SUBSCRIPTION_CHECK_INTERVAL = 3 * 60 * 60  # 3 hours


def _drain_download_queue():
    """Remove all pending items from the download queue (DB + memory)."""
    # Drain memory queue
    count = 0
    while not download_queue.empty():
        try:
            download_queue.get_nowait()
            count += 1
        except asyncio.QueueEmpty:
            break
    # Drain DB queue
    db_count = drain_queue()
    return count + db_count


async def download_worker():
    """Background worker that processes queued downloads with max concurrency."""
    while True:
        task_id, url, fmt, quality, hdr = await download_queue.get()
        # Skip if task was deleted while queued
        task = task_manager.get_task(task_id)
        if not task:
            download_queue.task_done()
            continue
        auto_moved = False
        try:
            async with DOWNLOAD_SEMAPHORE:
                await download_video(task_id, url, fmt, quality, hdr)
                task = task_manager.get_task(task_id)
                if task and task.get("status") == "moved":
                    auto_moved = True
                    pre_count = count_cd2_temp_files()
                    if pre_count > 0:
                        waited = 0
                        max_wait = 7200
                        while waited < max_wait:
                            await asyncio.sleep(CHECK_INTERVAL)
                            waited += CHECK_INTERVAL
                            cur = count_cd2_temp_files()
                            if cur < pre_count:
                                break
        except Exception as e:
            task_manager.set_error(task_id, str(e))
        finally:
            download_queue.task_done()


async def _upload_monitor():
    """Background task that decrements _active_uploads when CD2 temp files disappear."""
    global _active_uploads
    last_count = count_cd2_temp_files()
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        cur = count_cd2_temp_files()
        if cur < last_count and _active_uploads > 0:
            async with _upload_lock:
                _active_uploads = max(0, _active_uploads - (last_count - cur))
        last_count = cur


@app.on_event("startup")
async def startup():
    """Start download workers and restore queue from database."""
    # Restore queue from database
    restored = restore_queue(download_queue)
    if restored > 0:
        print(f"Restored {restored} queued downloads from database")

    # Start download workers
    for _ in range(MAX_CONCURRENT_DOWNLOADS):
        asyncio.create_task(download_worker())
    asyncio.create_task(_upload_monitor())


# Auth
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "Zym@qwe123"

AUTH_WHITELIST = {"/", "/api/login", "/api/health", "/api/queue/status", "/favicon.ico"}


def validate_token(request: Request) -> str:
    """Extract and validate bearer token."""
    from app.database import get_token as db_get_token
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = auth[7:]
    username = db_get_token(token)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return token


async def auth_middleware(request: Request, call_next):
    """Skip auth for whitelisted paths, WebSocket, and static files."""
    path = request.url.path
    if path in AUTH_WHITELIST or path.startswith("/static") or path.startswith("/ws"):
        return await call_next(request)
    try:
        validate_token(request)
    except HTTPException as e:
        return JSONResponse(status_code=e.status_code, content={"detail": e.detail})
    return await call_next(request)


app.middleware("http")(auth_middleware)


@app.get("/api/paths", response_model=list[CloudPath])
async def get_paths():
    """Get configured custom paths."""
    paths = list_paths()
    # Add default if no paths exist
    if not paths:
        return [{"id": "default", "name": "下载目录", "path": "/root/youtube-downloader/downloads", "icon": "📁", "auto_move": False}]
    return paths


@app.post("/api/paths", response_model=CloudPath)
async def add_path(req: CloudPath):
    """Add a new custom path."""
    paths = list_paths()
    for p in paths:
        if p["path"] == req.path:
            raise HTTPException(status_code=400, detail="Path already exists")
    insert_path(req.id, req.name, req.path, req.icon or "📁", req.auto_move)
    return req


@app.delete("/api/paths/{path_id}")
async def delete_path_endpoint(path_id: str):
    """Delete a custom path by ID."""
    p = get_path(path_id)
    if not p:
        raise HTTPException(status_code=404, detail="Path not found")
    delete_path(path_id)
    return {"success": True}


@app.post("/api/paths/{path_id}/auto-move")
async def toggle_auto_move(path_id: str):
    """Toggle auto-move for a path. Only one path can have auto-move enabled."""
    target_path = get_path(path_id)
    if not target_path:
        raise HTTPException(status_code=404, detail="Path not found")

    new_state = not target_path.get("auto_move", False)

    # Clear auto_move for all paths first
    clear_auto_move_all()

    # Set the target path if enabling
    if new_state:
        set_auto_move(path_id, True)

    return {"success": True, "auto_move": new_state, "path_id": path_id}


@app.get("/api/paths/auto-move")
async def get_auto_move_path_endpoint():
    """Get the current auto-move path configuration."""
    p = get_auto_move_path()
    if p:
        return {"enabled": True, "path": p["path"], "name": p["name"], "path_id": p["id"]}
    return {"enabled": False}


# Mount static files
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent.parent / "static")), name="static")


@app.get("/api/health")
async def health():
    return {"status": "ok", "authenticated": True}


@app.post("/api/login")
async def login(request: Request):
    """Authenticate and return a bearer token."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body")

    username = body.get("username", "")
    password = body.get("password", "")

    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        token = str(uuid.uuid4())
        insert_token(token, username)
        return {"success": True, "token": token, "username": username}

    raise HTTPException(status_code=401, detail="Invalid credentials")


@app.post("/api/logout")
async def logout(request: Request):
    """Invalidate a token."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        delete_token(token)
    return {"success": True}


@app.get("/")
async def index():
    return FileResponse(str(Path(__file__).parent.parent / "static" / "index.html"))


@app.post("/api/download", response_model=TaskResponse)
async def create_download(req: DownloadRequest):
    task_id = task_manager.create_task(req.url, req.format, req.quality)

    # Add to database queue and memory queue
    enqueue(task_id, req.url, req.format.value, req.quality, req.hdr)
    await download_queue.put((task_id, req.url, req.format, req.quality, req.hdr))

    task = task_manager.get_task(task_id)
    return TaskResponse(**task)


@app.post("/api/playlist/download", response_model=TaskResponse)
async def create_playlist_download(req: DownloadRequest):
    """Start downloading a playlist."""
    from app.downloader import download_playlist

    task_id = task_manager.create_playlist_task(req.url, req.format, req.quality)

    asyncio.create_task(
        download_playlist(task_id, req.url, req.format, req.quality, req.hdr)
    )

    task = task_manager.get_task(task_id)
    return TaskResponse(**task)


@app.get("/api/playlist/info")
async def get_playlist_info(url: str):
    """Extract playlist information without downloading."""
    import yt_dlp

    def _extract():
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True}) as ydl:
                info = ydl.extract_info(url, download=False)
                if info.get("_type") == "playlist":
                    entries = info.get("entries", [])
                    return {
                        "title": info.get("title", "Unknown Playlist"),
                        "thumbnail": info.get("thumbnail"),
                        "count": len(entries),
                        "uploader": info.get("uploader"),
                        "entries": [
                            {
                                "id": e.get("id"),
                                "title": e.get("title", "Unknown"),
                                "url": e.get("url") or e.get("webpage_url") or f"https://www.youtube.com/watch?v={e.get('id')}",
                                "duration": e.get("duration"),
                            }
                            for e in entries[:50]
                        ]
                    }
                return None
        except Exception as e:
            return {"error": str(e)}

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _extract)
    return result


@app.get("/api/playlist/{playlist_id}/tasks")
async def get_playlist_tasks(playlist_id: str):
    """Get all child tasks for a playlist."""
    parent_task = task_manager.get_task(playlist_id)
    if not parent_task:
        raise HTTPException(status_code=404, detail="Playlist task not found")

    child_tasks = task_manager.get_child_tasks(playlist_id)

    return {
        "playlist": parent_task,
        "children": child_tasks,
        "total": len(child_tasks),
    }


@app.post("/api/playlist/{playlist_id}/pause")
async def pause_playlist(playlist_id: str):
    """Pause a downloading playlist."""
    success = task_manager.pause_playlist(playlist_id)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot pause this playlist")
    return {"success": True, "message": "Playlist paused"}


@app.post("/api/playlist/{playlist_id}/resume")
async def resume_playlist(playlist_id: str):
    """Resume a paused playlist."""
    from app.downloader import resume_playlist_download

    task = task_manager.get_task(playlist_id)
    if not task or not task.get("is_playlist"):
        raise HTTPException(status_code=404, detail="Playlist not found")
    if task.get("status") != "paused":
        raise HTTPException(status_code=400, detail="Playlist is not paused")

    playlist_info = task.get("playlist_info", {})
    entries = playlist_info.get("entries", [])
    child_tasks = task_manager.get_child_tasks(playlist_id)
    created_ids = {c["task_id"] for c in child_tasks}

    resume_from = 0
    for i, entry in enumerate(entries):
        child_id = f"{playlist_id}_{i}"
        if child_id not in created_ids:
            resume_from = i
            break
    else:
        pl_prog = task.get("playlist_progress", {})
        resume_from = pl_prog.get("current", 0)

    task_manager.resume_playlist(playlist_id)

    fmt = task.get("format", "best")
    quality = task.get("quality")
    url = task.get("url", "")
    asyncio.create_task(
        resume_playlist_download(playlist_id, url, fmt, quality, entries, resume_from)
    )

    return {"success": True, "message": f"Playlist resumed from video {resume_from + 1}"}


@app.get("/api/tasks", response_model=TaskListResponse)
async def list_tasks_endpoint(
    q: Optional[str] = None,
    status: Optional[str] = None,
):
    """List individual download tasks (excludes playlists and child tasks)."""
    tasks = task_manager.list_tasks()
    tasks = [t for t in tasks if not t.get("is_playlist") and not t.get("parent_id")]
    if q:
        q_lower = q.lower()
        tasks = [t for t in tasks if q_lower in (t.get("title") or "").lower() or q_lower in (t.get("url") or "").lower()]
    if status:
        tasks = [t for t in tasks if t.get("status") == status]
    return TaskListResponse(
        tasks=[TaskResponse(**t) for t in tasks],
        total=len(tasks),
    )


class PlaylistSummary(BaseModel):
    task_id: str
    title: Optional[str] = "Unknown Playlist"
    url: Optional[str] = ""
    status: Optional[str] = "pending"
    progress: float = 0.0
    completed_count: int = 0
    total_count: int = 0
    created_at: Optional[str] = ""
    thumbnail: Optional[str] = None


class PlaylistListResponse(BaseModel):
    playlists: list[PlaylistSummary]
    total: int


@app.get("/api/playlists", response_model=PlaylistListResponse)
async def list_playlists(
    q: Optional[str] = None,
    status: Optional[str] = None,
):
    """List playlist parent tasks with progress summary."""
    tasks = task_manager.list_tasks()
    playlists = [t for t in tasks if t.get("is_playlist")]
    if q:
        q_lower = q.lower()
        playlists = [t for t in playlists if q_lower in (t.get("title") or "").lower() or q_lower in (t.get("url") or "").lower()]
    if status:
        playlists = [t for t in playlists if t.get("status") == status]

    result = []
    for pl in playlists:
        children = task_manager.get_child_tasks(pl["task_id"])
        pl_prog = pl.get("playlist_progress", {})
        total = pl_prog.get("total") or len(children)
        completed = pl_prog.get("current", 0)
        progress = (completed / total * 100) if total > 0 else 0
        result.append(PlaylistSummary(
            task_id=pl["task_id"],
            title=pl.get("title", "Unknown Playlist"),
            url=pl.get("url", ""),
            status=pl.get("status", "pending"),
            progress=round(progress, 1),
            completed_count=completed,
            total_count=total,
            created_at=pl.get("created_at", ""),
            thumbnail=pl.get("thumbnail"),
        ))

    return PlaylistListResponse(playlists=result, total=len(result))


@app.get("/api/playlists/{playlist_id}/details")
async def get_playlist_details(playlist_id: str):
    """Get detailed child tasks for a playlist."""
    parent = task_manager.get_task(playlist_id)
    if not parent or not parent.get("is_playlist"):
        raise HTTPException(status_code=404, detail="Playlist not found")

    children = task_manager.get_child_tasks(playlist_id)
    return {
        "playlist": {
            "task_id": parent["task_id"],
            "title": parent.get("title"),
            "url": parent.get("url"),
            "status": parent.get("status"),
            "progress": parent.get("progress"),
        },
        "children": [
            {
                "task_id": c["task_id"],
                "title": c.get("title", "Unknown"),
                "url": c.get("url"),
                "status": c.get("status"),
                "progress": c.get("progress", 0),
                "speed": c.get("speed"),
                "error": c.get("error"),
                "filepath": c.get("filepath"),
                "cloud_path": c.get("cloud_path"),
                "created_at": c.get("created_at"),
                "thumbnail": c.get("thumbnail"),
                "filesize": c.get("filesize"),
                "duration": c.get("duration"),
            }
            for c in children
        ],
        "total": len(children),
    }


@app.post("/api/playlists/batch-move")
async def batch_move_playlists(req: BatchMoveRequest):
    """Move all child tasks of selected playlists to target directory."""
    results = {"success": 0, "failed": 0, "details": []}
    for task_id in req.task_ids:
        parent = task_manager.get_task(task_id)
        if not parent or not parent.get("is_playlist"):
            results["failed"] += 1
            results["details"].append({"task_id": task_id, "status": "skipped", "reason": "Not a playlist"})
            continue
        children = task_manager.get_child_tasks(task_id)
        for child in children:
            if not child.get("filepath") or child.get("status") not in (TaskStatus.DONE, TaskStatus.MOVED):
                continue
            result = await move_to_cloud_drive(child["task_id"], req.target_path, req.target_name)
            if result:
                results["success"] += 1
                results["details"].append({"task_id": child["task_id"], "playlist_id": task_id, "status": "moved", "path": result[1]})
            else:
                results["failed"] += 1
                results["details"].append({"task_id": child["task_id"], "playlist_id": task_id, "status": "failed"})
    return results


@app.post("/api/playlists/batch-delete")
async def batch_delete_playlists(req: BatchDeleteRequest):
    """Delete selected playlists and all their child tasks."""
    results = {"success": 0, "failed": 0, "details": []}
    for task_id in req.task_ids:
        parent = task_manager.get_task(task_id)
        if not parent or not parent.get("is_playlist"):
            results["failed"] += 1
            results["details"].append({"task_id": task_id, "status": "skipped", "reason": "Not a playlist"})
            continue
        children = task_manager.get_child_tasks(task_id)
        for child in children:
            if child.get("filepath") and os.path.exists(child["filepath"]):
                try:
                    os.remove(child["filepath"])
                except OSError:
                    pass
            task_manager.subscribers.pop(child["task_id"], None)
            task_manager.delete_task(child["task_id"])
        task_manager.subscribers.pop(task_id, None)
        task_manager.delete_task(task_id)
        results["success"] += 1
        results["details"].append({"task_id": task_id, "status": "deleted"})
    drained = _drain_download_queue()
    if drained:
        results["queue_drained"] = drained
    return results


@app.post("/api/subscriptions/batch-move")
async def batch_move_subscriptions(req: BatchMoveRequest):
    """Move downloaded files from selected subscription tasks."""
    results = {"success": 0, "failed": 0, "details": []}
    subs = list_subs()
    sub_urls = {s["id"]: s["url"] for s in subs if s["id"] in req.task_ids}
    for task in task_manager.list_tasks():
        if task.get("url") in sub_urls.values() and task.get("filepath") and task.get("status") in (TaskStatus.DONE, TaskStatus.MOVED):
            result = await move_to_cloud_drive(task["task_id"], req.target_path, req.target_name)
            if result:
                results["success"] += 1
                results["details"].append({"task_id": task["task_id"], "status": "moved", "path": result[1]})
            else:
                results["failed"] += 1
                results["details"].append({"task_id": task["task_id"], "status": "failed"})
    return results


@app.post("/api/subscriptions/batch-delete")
async def batch_delete_subscriptions(req: BatchDeleteRequest):
    """Delete subscription entries and their associated tasks."""
    results = {"success": 0, "failed": 0, "details": []}
    for sub_id in req.task_ids:
        sub = get_sub(sub_id)
        if not sub:
            results["failed"] += 1
            results["details"].append({"task_id": sub_id, "status": "skipped", "reason": "Subscription not found"})
            continue
        delete_sub(sub_id)
        results["success"] += 1
        results["details"].append({"task_id": sub_id, "status": "deleted"})
    return results


@app.get("/api/tasks/{task_id}", response_model=TaskResponse)
async def get_task_endpoint(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskResponse(**task)


@app.delete("/api/tasks/{task_id}")
async def delete_task_endpoint(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.get("filepath") and os.path.exists(task["filepath"]):
        try:
            os.remove(task["filepath"])
        except OSError:
            pass

    task_manager.delete_task(task_id)
    return {"success": True, "task_id": task_id}


@app.post("/api/tasks/{task_id}/retry", response_model=TaskResponse)
async def retry_task(task_id: str):
    """Retry a failed task."""
    from app.downloader import download_video
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.get("status") not in ("error",):
        raise HTTPException(status_code=400, detail="Can only retry failed tasks")

    url = task["url"]
    fmt = task.get("format", "video")
    quality = task.get("quality")
    hdr = task.get("hdr")

    if task.get("filepath") and os.path.exists(task["filepath"]):
        try:
            os.remove(task["filepath"])
        except OSError:
            pass
    task_manager.delete_task(task_id)

    from app.models import DownloadFormat
    fmt_enum = DownloadFormat(fmt) if fmt else DownloadFormat.VIDEO
    new_id = task_manager.create_task(url, fmt_enum, quality)
    enqueue(new_id, url, fmt_enum.value, quality, hdr)
    await download_queue.put((new_id, url, fmt_enum, quality, hdr))

    new_task = task_manager.get_task(new_id)
    return TaskResponse(**new_task)


@app.post("/api/move", response_model=MoveResponse)
async def move_file(req: MoveRequest):
    global _active_uploads
    task = task_manager.get_task(req.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not task.get("filepath"):
        raise HTTPException(status_code=400, detail="No file to move")

    downloading = DOWNLOAD_SEMAPHORE._value
    currently_downloading = MAX_CONCURRENT_DOWNLOADS - downloading
    if currently_downloading + _active_uploads >= MAX_CONCURRENT_DOWNLOADS:
        raise HTTPException(status_code=429, detail=f"并发已满（{MAX_CONCURRENT_DOWNLOADS}），请稍后再试")

    result = await move_to_cloud_drive(req.task_id, req.target_path, req.target_name)
    if not result:
        raise HTTPException(status_code=500, detail="Failed to move file")

    async with _upload_lock:
        _active_uploads += 1

    src, dest = result
    return MoveResponse(
        success=True,
        task_id=req.task_id,
        original_path=src,
        new_path=dest,
        target=req.target_name or req.target_path,
    )


@app.post("/api/tasks/batch-move")
async def batch_move_files(req: BatchMoveRequest):
    """Move multiple tasks to a target directory."""
    results = {"success": 0, "failed": 0, "details": []}
    for task_id in req.task_ids:
        task = task_manager.get_task(task_id)
        if not task or not task.get("filepath"):
            results["failed"] += 1
            results["details"].append({"task_id": task_id, "status": "skipped", "reason": "Task not found or no file"})
            continue
        result = await move_to_cloud_drive(task_id, req.target_path, req.target_name)
        if result:
            results["success"] += 1
            results["details"].append({"task_id": task_id, "status": "moved", "path": result[1]})
        else:
            results["failed"] += 1
            results["details"].append({"task_id": task_id, "status": "failed", "reason": "Move operation failed"})
    return results


@app.post("/api/tasks/batch-delete")
async def batch_delete_tasks(req: BatchDeleteRequest):
    """Delete multiple tasks and their files."""
    results = {"success": 0, "failed": 0, "details": []}
    for task_id in req.task_ids:
        task = task_manager.get_task(task_id)
        if not task:
            results["failed"] += 1
            results["details"].append({"task_id": task_id, "status": "skipped", "reason": "Task not found"})
            continue
        if task.get("filepath") and os.path.exists(task["filepath"]):
            try:
                os.remove(task["filepath"])
            except OSError:
                pass
        task_manager.subscribers.pop(task_id, None)
        task_manager.delete_task(task_id)
        results["success"] += 1
        results["details"].append({"task_id": task_id, "status": "deleted"})
    drained = _drain_download_queue()
    if drained:
        results["queue_drained"] = drained
    return results


# ── Subscriptions ──────────────────────────────────────────────────────────


def _extract_existing_video_ids() -> set[str]:
    """Get all YouTube video IDs already downloaded."""
    ids = set()
    for task in task_manager.list_tasks():
        url = task.get("url", "")
        if "watch?v=" in url:
            ids.add(url.split("watch?v=")[1].split("&")[0])
        elif "youtu.be/" in url:
            ids.add(url.split("youtu.be/")[1].split("?")[0])
    return ids


@app.get("/api/subscriptions")
async def list_subscriptions_endpoint():
    return list_subs()


@app.post("/api/subscriptions")
async def add_subscription(req: Subscription):
    subs = list_subs()
    for s in subs:
        if s["url"] == req.url:
            raise HTTPException(status_code=400, detail="Subscription already exists")
    now = datetime.now().isoformat()
    sub_id = str(uuid.uuid4())[:8]
    insert_sub(sub_id, req.url, req.name or "", req.auto_download, req.format or "best", req.quality)
    return {"id": sub_id, "url": req.url, "name": req.name or "", "auto_download": req.auto_download, "format": req.format or "best", "quality": req.quality, "created_at": now, "last_checked": "", "last_video_count": 0}


@app.delete("/api/subscriptions/{sub_id}")
async def delete_subscription_endpoint(sub_id: str):
    sub = get_sub(sub_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    delete_sub(sub_id)
    return {"success": True}


@app.post("/api/subscriptions/{sub_id}/update")
async def update_subscription_endpoint(sub_id: str, req: Subscription):
    sub = get_sub(sub_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    update_sub(sub_id, name=req.name, url=req.url, auto_download=req.auto_download, format=req.format, quality=req.quality)
    return {"id": sub_id, "url": req.url, "name": req.name, "auto_download": req.auto_download, "format": req.format, "quality": req.quality}


async def _check_single_subscription(sub: dict) -> dict:
    """Check one subscription for new videos."""
    import yt_dlp

    existing_ids = _extract_existing_video_ids()

    sub_url = sub["url"]
    try:
        ydl_opts_resolve = {"quiet": True, "no_warnings": True, "extract_flat": True}
        if COOKIE_FILE.exists():
            ydl_opts_resolve["cookiefile"] = str(COOKIE_FILE)
        with yt_dlp.YoutubeDL(ydl_opts_resolve) as ydl:
            _ci = ydl.extract_info(sub_url, download=False)
            _cid = _ci.get("channel_id")
            if _cid and _cid.startswith("UC"):
                sub_url = f"https://www.youtube.com/playlist?list=UU{_cid[2:]}"
    except Exception:
        pass

    def _fetch_videos():
        try:
            ydl_opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "playlistend": 30}
            if COOKIE_FILE.exists():
                ydl_opts["cookiefile"] = str(COOKIE_FILE)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(sub_url, download=False)
                if info.get("_type") == "playlist":
                    entries = info.get("entries", [])
                elif info.get("entries"):
                    entries = info.get("entries", [])
                else:
                    entries = [info] if info.get("id") else []
                return {
                    "title": info.get("title", "Unknown"),
                    "entries": [
                        {
                            "id": e.get("id"),
                            "title": e.get("title", "Unknown"),
                            "url": e.get("url") or e.get("webpage_url") or f"https://www.youtube.com/watch?v={e.get('id')}",
                            "duration": e.get("duration"),
                            "thumbnail": e.get("thumbnail"),
                        }
                        for e in entries
                        if e and e.get("id")
                    ],
                }
        except Exception as e:
            return {"error": str(e)}

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _fetch_videos)

    if "error" in result:
        return {"subscription_name": sub.get("name", ""), "error": result["error"]}

    new_videos = [v for v in result["entries"] if v["id"] not in existing_ids]

    downloaded = []
    queued_count = 0
    if sub.get("auto_download", True) and new_videos:
        for video in new_videos[:10]:
            fmt = sub.get("format", "best") or "best"
            quality = sub.get("quality")
            task_id = task_manager.create_task(video["url"], DownloadFormat(fmt), quality)
            task_manager.set_metadata(task_id, title=video["title"], thumbnail=video.get("thumbnail"), duration=video.get("duration"))
            enqueue(task_id, video["url"], fmt, quality, None)
            await download_queue.put((task_id, video["url"], DownloadFormat(fmt), quality, None))
            downloaded.append(video["title"])
            queued_count += 1

    return {
        "subscription_name": result.get("title", ""),
        "total_videos": len(result["entries"]),
        "new_videos": len(new_videos),
        "downloaded": downloaded,
        "skipped": max(0, len(new_videos) - 10),
        "queued": queued_count,
    }


@app.post("/api/subscriptions/{sub_id}/check")
async def check_subscription(sub_id: str):
    sub = get_sub(sub_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")

    result = await _check_single_subscription(sub)

    update_sub(sub_id, last_checked=datetime.now().isoformat(), last_video_count=result.get("new_videos", 0))

    return result


@app.post("/api/subscriptions/check-all")
async def check_all_subscriptions():
    subs = list_subs()
    results = []
    for sub in subs:
        try:
            result = await _check_single_subscription(sub)
            results.append({
                "id": sub["id"],
                "name": sub.get("name", sub["url"]),
                "new_videos": result.get("new_videos", 0),
                "downloaded": result.get("downloaded", []),
            })
            update_sub(sub["id"], last_checked=datetime.now().isoformat(), last_video_count=result.get("new_videos", 0))
        except Exception as e:
            results.append({"id": sub["id"], "name": sub.get("name", sub["url"]), "error": str(e)})
    return {"total": len(subs), "results": results}


@app.post("/api/subscriptions/{sub_id}/download-history")
async def download_subscription_history(sub_id: str):
    """Download ALL videos from a subscription."""
    import yt_dlp

    sub = get_sub(sub_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")

    existing_ids = _extract_existing_video_ids()

    sub_url = sub["url"]
    try:
        ydl_opts_resolve = {"quiet": True, "no_warnings": True, "extract_flat": True}
        if COOKIE_FILE.exists():
            ydl_opts_resolve["cookiefile"] = str(COOKIE_FILE)
        with yt_dlp.YoutubeDL(ydl_opts_resolve) as ydl:
            _ci2 = ydl.extract_info(sub_url, download=False)
            _cid2 = _ci2.get("channel_id")
            if _cid2 and _cid2.startswith("UC"):
                sub_url = f"https://www.youtube.com/playlist?list=UU{_cid2[2:]}"
    except Exception:
        pass

    def _fetch_all_videos():
        try:
            ydl_opts = {"quiet": True, "no_warnings": True, "extract_flat": True}
            if COOKIE_FILE.exists():
                ydl_opts["cookiefile"] = str(COOKIE_FILE)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(sub_url, download=False)
                if info.get("_type") == "playlist":
                    entries = info.get("entries", [])
                elif info.get("entries"):
                    entries = info.get("entries", [])
                else:
                    entries = [info] if info.get("id") else []
                return {
                    "title": info.get("title", "Unknown"),
                    "entries": [
                        {
                            "id": e.get("id"),
                            "title": e.get("title", "Unknown"),
                            "url": e.get("url") or e.get("webpage_url") or f"https://www.youtube.com/watch?v={e.get('id')}",
                            "duration": e.get("duration"),
                            "thumbnail": e.get("thumbnail"),
                        }
                        for e in entries
                        if e and e.get("id")
                    ],
                }
        except Exception as e:
            return {"error": str(e)}

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _fetch_all_videos)

    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    new_videos = [v for v in result["entries"] if v["id"] not in existing_ids]
    queued_count = 0

    fmt = sub.get("format", "best") or "best"
    quality = sub.get("quality")

    for video in new_videos:
        task_id = task_manager.create_task(video["url"], DownloadFormat(fmt), quality)
        task_manager.set_metadata(task_id, title=video["title"], thumbnail=video.get("thumbnail"), duration=video.get("duration"))
        enqueue(task_id, video["url"], fmt, quality, None)
        await download_queue.put((task_id, video["url"], DownloadFormat(fmt), quality, None))
        queued_count += 1

    update_sub(sub_id, last_checked=datetime.now().isoformat(), last_video_count=queued_count)

    return {
        "subscription_name": result.get("title", ""),
        "total_videos": len(result["entries"]),
        "new_to_download": len(new_videos),
        "already_downloaded": len(result["entries"]) - len(new_videos),
        "queued": queued_count,
        "queue_position": f"{queued_count} videos added to queue (max {MAX_CONCURRENT_DOWNLOADS} concurrent)",
    }


@app.get("/api/queue/status")
async def queue_status():
    """Get current download queue status."""
    from app.database import get_db
    conn = get_db()
    # Count actual task statuses from DB
    dl = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='downloading'").fetchone()[0]
    mv = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='moving'").fetchone()[0]
    cd2 = count_cd2_temp_files()
    return {
        "pending": queue_size(),
        "downloading": dl,
        "active_uploads": mv + _active_uploads,
        "max_concurrent": MAX_CONCURRENT_DOWNLOADS,
        "cd2_temp_files": cd2,
        "cd2_temp_dir": CD2_TEMP_DIR,
    }


@app.get("/api/concurrency/config")
async def get_concurrency_config_endpoint():
    """Get current concurrency configuration."""
    cfg = get_all_config()
    return {
        "max_concurrent_downloads": cfg.get("max_concurrent_downloads", 3),
        "cd2_temp_dir": cfg.get("cd2_temp_dir", "/opt/docker/cd2/temp"),
        "check_interval": cfg.get("check_interval", 5),
        "current_max": MAX_CONCURRENT_DOWNLOADS,
        "cd2_temp_files": count_cd2_temp_files(),
    }


@app.post("/api/concurrency/config")
async def update_concurrency_config_endpoint(req: dict):
    """Update concurrency configuration."""
    global MAX_CONCURRENT_DOWNLOADS, DOWNLOAD_SEMAPHORE, CD2_TEMP_DIR, CHECK_INTERVAL

    cfg = get_all_config()

    if "max_concurrent_downloads" in req:
        new_max = int(req["max_concurrent_downloads"])
        if new_max < 1 or new_max > 10:
            raise HTTPException(status_code=400, detail="max_concurrent_downloads must be between 1 and 10")
        cfg["max_concurrent_downloads"] = new_max
        set_config("max_concurrent_downloads", new_max)

    if "cd2_temp_dir" in req:
        cfg["cd2_temp_dir"] = req["cd2_temp_dir"]
        set_config("cd2_temp_dir", req["cd2_temp_dir"])

    if "check_interval" in req:
        cfg["check_interval"] = int(req["check_interval"])
        set_config("check_interval", int(req["check_interval"]))

    MAX_CONCURRENT_DOWNLOADS = cfg.get("max_concurrent_downloads", 3)
    CD2_TEMP_DIR = cfg.get("cd2_temp_dir", "/opt/docker/cd2/temp")
    CHECK_INTERVAL = cfg.get("check_interval", 5)
    DOWNLOAD_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

    return {"success": True, "config": cfg}


def count_cd2_temp_files() -> int:
    """Count files in CloudDrive2 temp directory."""
    try:
        if os.path.isdir(CD2_TEMP_DIR):
            return len([f for f in os.listdir(CD2_TEMP_DIR) if os.path.isfile(os.path.join(CD2_TEMP_DIR, f))])
    except Exception:
        pass
    return 0


@app.get("/api/files", response_model=list[FileItem])
async def list_files(
    q: Optional[str] = None,
):
    files = []
    tasks = task_manager.list_tasks()
    for task in tasks:
        if task.get("status") in (TaskStatus.DONE, TaskStatus.MOVED) and task.get("filepath"):
            fp = task["filepath"]
            if os.path.exists(fp) or task.get("cloud_path"):
                display_path = task.get("cloud_path") or fp
                files.append(FileItem(
                    task_id=task["task_id"],
                    filename=task.get("filename", os.path.basename(fp)),
                    filepath=display_path,
                    filesize=task.get("filesize", 0),
                    title=task.get("title", "Unknown"),
                    thumbnail=task.get("thumbnail"),
                    duration=task.get("duration"),
                    format=task.get("format", "video"),
                    created_at=task["created_at"],
                    cloud_path=task.get("cloud_path"),
                ))
    if q:
        q_lower = q.lower()
        files = [f for f in files if q_lower in (f.title or "").lower()]
    return files


@app.post("/api/cookies")
async def upload_cookies(file: UploadFile = File(...)):
    """Upload YouTube cookies.txt file."""
    content = await file.read()
    with open(COOKIE_FILE, "wb") as f:
        f.write(content)
    return {"success": True, "message": "Cookies uploaded successfully"}


@app.get("/api/cookies/status")
async def cookies_status():
    exists = COOKIE_FILE.exists()
    return {"has_cookies": exists}


@app.websocket("/ws/{task_id}")
async def websocket_endpoint(websocket: WebSocket, task_id: str):
    token = websocket.query_params.get("token", "")
    username = get_token(token)
    if not username:
        await websocket.close(code=1008, reason="Unauthorized")
        return
    await websocket.accept()

    messages = []

    def on_message(msg):
        messages.append(msg)
        asyncio.create_task(_safe_send(websocket, msg))

    unsubscribe = task_manager.subscribe(task_id, on_message)

    task = task_manager.get_task(task_id)
    if task:
        initial = {
            "type": "status",
            "task_id": task_id,
            "status": task["status"],
            "progress": task["progress"],
            "speed": task.get("speed"),
            "eta": task.get("eta"),
            "title": task.get("title"),
            "filename": task.get("filename"),
            "error": task.get("error"),
        }
        await websocket.send_json(initial)
        for msg in messages[-5:]:
            await websocket.send_json(msg.dict() if hasattr(msg, "dict") else msg)

    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
                continue

            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        unsubscribe()


async def _safe_send(websocket: WebSocket, msg):
    try:
        if hasattr(msg, "dict"):
            await websocket.send_json(msg.dict())
        else:
            await websocket.send_json(msg)
    except Exception:
        pass