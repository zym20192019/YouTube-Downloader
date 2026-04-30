import os
import uuid
import asyncio
import json
from pathlib import Path
from typing import Optional
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app.models import (
    DownloadRequest, MoveRequest, TaskResponse, TaskListResponse,
    MoveResponse, FileItem, TaskStatus, DownloadFormat, CloudPath
)
from app.tasks import task_manager
from app.downloader import download_video, move_to_cloud_drive, DOWNLOAD_DIR, COOKIE_FILE


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


PATH_CONFIG_FILE = Path(__file__).parent.parent / "path_config.json"


def load_path_config() -> list[dict]:
    """Load custom paths from config file, return defaults if not exists."""
    if PATH_CONFIG_FILE.exists():
        try:
            with open(PATH_CONFIG_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return [{"id": "default", "name": "下载目录", "path": "/root/youtube-downloader/downloads", "icon": "📁"}]


def save_path_config(paths: list[dict]) -> None:
    """Save custom paths to config file."""
    with open(PATH_CONFIG_FILE, "w") as f:
        json.dump(paths, f, indent=2)


app = FastAPI(title="YouTube Downloader", description="Liquid Glass YouTube Video Downloader")

# Download queue with concurrency control
MAX_CONCURRENT_DOWNLOADS = 3
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
download_queue: asyncio.Queue = asyncio.Queue()

# Auto-check subscriptions interval (in seconds)
SUBSCRIPTION_CHECK_INTERVAL = 3 * 60 * 60  # 3 hours


async def download_worker():
    """Background worker that processes queued downloads with max concurrency."""
    while True:
        task_id, url, fmt, quality, hdr = await download_queue.get()
        try:
            async with DOWNLOAD_SEMAPHORE:
                await download_video(task_id, url, fmt, quality, hdr)
        except Exception as e:
            task_manager.set_error(task_id, str(e))
        finally:
            download_queue.task_done()


@app.on_event("startup")
async def startup():
    """Start the download worker on app startup."""
    asyncio.create_task(download_worker())


# Auth
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "Zym@qwe123"
ACTIVE_TOKENS: dict[str, str] = {}  # token -> username

AUTH_WHITELIST = {"/", "/api/login", "/api/health", "/api/queue/status", "/favicon.ico"}

def get_token(request: Request) -> str:
    """Extract and validate bearer token."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = auth[7:]
    if token not in ACTIVE_TOKENS:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return token

async def auth_middleware(request: Request, call_next):
    """Skip auth for whitelisted paths, WebSocket, and static files."""
    path = request.url.path
    if path in AUTH_WHITELIST or path.startswith("/static") or path.startswith("/ws"):
        return await call_next(request)
    # Validate token
    try:
        get_token(request)
    except HTTPException as e:
        return JSONResponse(status_code=e.status_code, content={"detail": e.detail})
    return await call_next(request)

app.middleware("http")(auth_middleware)


@app.get("/api/paths", response_model=list[CloudPath])
async def get_paths():
    """Get configured custom paths."""
    return load_path_config()


@app.post("/api/paths", response_model=CloudPath)
async def add_path(req: CloudPath):
    """Add a new custom path."""
    paths = load_path_config()
    # Check if path already exists
    for p in paths:
        if p["path"] == req.path:
            raise HTTPException(status_code=400, detail="Path already exists")
    paths.append(req.model_dump())
    save_path_config(paths)
    return req


@app.delete("/api/paths/{path_id}")
async def delete_path(path_id: str):
    """Delete a custom path by ID."""
    paths = load_path_config()
    new_paths = [p for p in paths if p["id"] != path_id]
    if len(new_paths) == len(paths):
        raise HTTPException(status_code=404, detail="Path not found")
    save_path_config(new_paths)
    return {"success": True}


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
        ACTIVE_TOKENS[token] = username
        return {"success": True, "token": token, "username": username}

    raise HTTPException(status_code=401, detail="Invalid credentials")


@app.post("/api/logout")
async def logout(request: Request):
    """Invalidate a token."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        ACTIVE_TOKENS.pop(token, None)
    return {"success": True}


@app.get("/")
async def index():
    return FileResponse(str(Path(__file__).parent.parent / "static" / "index.html"))


@app.post("/api/download", response_model=TaskResponse)
async def create_download(req: DownloadRequest):
    task_id = task_manager.create_task(req.url, req.format, req.quality)

    # Add to download queue (processed by worker with max 3 concurrency)
    await download_queue.put((task_id, req.url, req.format, req.quality, req.hdr))

    task = task_manager.get_task(task_id)
    return TaskResponse(**task)


@app.post("/api/playlist/download", response_model=TaskResponse)
async def create_playlist_download(req: DownloadRequest):
    """Start downloading a playlist. Creates a parent task with child tasks for each video."""
    from app.downloader import download_playlist

    task_id = task_manager.create_playlist_task(req.url, req.format, req.quality)

    # Playlists bypass queue - they handle their own sequential downloads
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
                            for e in entries[:50]  # Limit to first 50 for preview
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
    """Get all child tasks for a playlist, including their individual download status."""
    parent_task = task_manager.get_task(playlist_id)
    if not parent_task:
        raise HTTPException(status_code=404, detail="Playlist task not found")
    
    child_tasks = task_manager.get_child_tasks(playlist_id)
    
    return {
        "playlist": parent_task,
        "children": child_tasks,
        "total": len(child_tasks),
    }


@app.get("/api/tasks", response_model=TaskListResponse)
async def list_tasks(
    q: Optional[str] = None,
    status: Optional[str] = None,
):
    """List individual download tasks (excludes playlists and child tasks)."""
    tasks = task_manager.list_tasks()
    # Exclude playlist parent tasks and child tasks (which have a parent_id)
    tasks = [t for t in tasks if not t.get("is_playlist") and not t.get("parent_id")]
    # Filter by search query (title or URL)
    if q:
        q_lower = q.lower()
        tasks = [t for t in tasks if q_lower in (t.get("title") or "").lower() or q_lower in (t.get("url") or "").lower()]
    # Filter by status
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
    # Only playlist parent tasks
    playlists = [t for t in tasks if t.get("is_playlist")]
    if q:
        q_lower = q.lower()
        playlists = [t for t in playlists if q_lower in (t.get("title") or "").lower() or q_lower in (t.get("url") or "").lower()]
    if status:
        playlists = [t for t in playlists if t.get("status") == status]

    result = []
    for pl in playlists:
        children = task_manager.get_child_tasks(pl["task_id"])
        # 使用 playlist_progress 中记录的总数，避免边下边创建导致总数动态变化 (1/2, 2/3...)
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
        # Delete child files and tasks
        children = task_manager.get_child_tasks(task_id)
        for child in children:
            if child.get("filepath") and os.path.exists(child["filepath"]):
                try:
                    os.remove(child["filepath"])
                except OSError:
                    pass
            task_manager.subscribers.pop(child["task_id"], None)
            task_manager.delete_task(child["task_id"])
        # Delete parent
        task_manager.subscribers.pop(task_id, None)
        task_manager.delete_task(task_id)
        results["success"] += 1
        results["details"].append({"task_id": task_id, "status": "deleted"})
    return results


@app.post("/api/subscriptions/batch-move")
async def batch_move_subscriptions(req: BatchMoveRequest):
    """Move downloaded files from selected subscription tasks."""
    results = {"success": 0, "failed": 0, "details": []}
    # Find tasks associated with subscription URLs
    subs = load_subscriptions()
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
    subs = load_subscriptions()
    for sub_id in req.task_ids:
        new_subs = [s for s in subs if s["id"] != sub_id]
        if len(new_subs) == len(subs):
            results["failed"] += 1
            results["details"].append({"task_id": sub_id, "status": "skipped", "reason": "Subscription not found"})
            continue
        save_subscriptions(new_subs)
        results["success"] += 1
        results["details"].append({"task_id": sub_id, "status": "deleted"})
    return results


@app.get("/api/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskResponse(**task)


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Delete file if exists
    if task.get("filepath") and os.path.exists(task["filepath"]):
        try:
            os.remove(task["filepath"])
        except OSError:
            pass

    task_manager.delete_task(task_id)
    return {"success": True, "task_id": task_id}


@app.post("/api/move", response_model=MoveResponse)
async def move_file(req: MoveRequest):
    task = task_manager.get_task(req.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not task.get("filepath"):
        raise HTTPException(status_code=400, detail="No file to move")

    result = await move_to_cloud_drive(req.task_id, req.target_path, req.target_name)
    if not result:
        raise HTTPException(status_code=500, detail="Failed to move file")

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
        # Delete file if exists
        if task.get("filepath") and os.path.exists(task["filepath"]):
            try:
                os.remove(task["filepath"])
            except OSError:
                pass
        # Close WS connection if exists
        task_manager.subscribers.pop(task_id, None)
        task_manager.delete_task(task_id)
        results["success"] += 1
        results["details"].append({"task_id": task_id, "status": "deleted"})
    return results




# ── Subscriptions ──────────────────────────────────────────────────────────
SUBSCRIPTIONS_FILE = Path(__file__).parent.parent / "subscriptions.json"


def load_subscriptions() -> list[dict]:
    if SUBSCRIPTIONS_FILE.exists():
        try:
            with open(SUBSCRIPTIONS_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return []


def save_subscriptions(subs: list[dict]) -> None:
    with open(SUBSCRIPTIONS_FILE, "w") as f:
        json.dump(subs, f, indent=2)


def _extract_existing_video_ids() -> set[str]:
    """Get all YouTube video IDs already downloaded."""
    ids = set()
    for task in task_manager.tasks.values():
        url = task.get("url", "")
        if "watch?v=" in url:
            ids.add(url.split("watch?v=")[1].split("&")[0])
        elif "youtu.be/" in url:
            ids.add(url.split("youtu.be/")[1].split("?")[0])
    return ids


@app.get("/api/subscriptions")
async def list_subscriptions():
    return load_subscriptions()


@app.post("/api/subscriptions")
async def add_subscription(req: Subscription):
    subs = load_subscriptions()
    for s in subs:
        if s["url"] == req.url:
            raise HTTPException(status_code=400, detail="Subscription already exists")
    now = datetime.now().isoformat()
    new_sub = {
        "id": str(uuid.uuid4())[:8],
        "url": req.url,
        "name": req.name or "",
        "auto_download": req.auto_download,
        "format": req.format or "best",
        "quality": req.quality,
        "created_at": now,
        "last_checked": "",
        "last_video_count": 0,
    }
    subs.append(new_sub)
    save_subscriptions(subs)
    return new_sub


@app.delete("/api/subscriptions/{sub_id}")
async def delete_subscription(sub_id: str):
    subs = load_subscriptions()
    new_subs = [s for s in subs if s["id"] != sub_id]
    if len(new_subs) == len(subs):
        raise HTTPException(status_code=404, detail="Subscription not found")
    save_subscriptions(new_subs)
    return {"success": True}


@app.post("/api/subscriptions/{sub_id}/update")
async def update_subscription(sub_id: str, req: Subscription):
    subs = load_subscriptions()
    for i, s in enumerate(subs):
        if s["id"] == sub_id:
            subs[i].update({
                "name": req.name,
                "url": req.url,
                "auto_download": req.auto_download,
                "format": req.format,
                "quality": req.quality,
            })
            save_subscriptions(subs)
            return subs[i]
    raise HTTPException(status_code=404, detail="Subscription not found")


async def _check_single_subscription(sub: dict) -> dict:
    """Check one subscription for new videos."""
    import yt_dlp

    existing_ids = _extract_existing_video_ids()

    def _fetch_videos():
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True}) as ydl:
                info = ydl.extract_info(sub["url"], download=False)
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
                        for e in entries[:100]
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
            # Add to queue instead of direct execution
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
    subs = load_subscriptions()
    sub = next((s for s in subs if s["id"] == sub_id), None)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")

    result = await _check_single_subscription(sub)

    sub["last_checked"] = datetime.now().isoformat()
    sub["last_video_count"] = result.get("new_videos", 0)
    save_subscriptions(subs)

    return result


@app.post("/api/subscriptions/check-all")
async def check_all_subscriptions():
    subs = load_subscriptions()
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
            sub["last_checked"] = datetime.now().isoformat()
            sub["last_video_count"] = result.get("new_videos", 0)
        except Exception as e:
            results.append({"id": sub["id"], "name": sub.get("name", sub["url"]), "error": str(e)})
    save_subscriptions(subs)
    return {"total": len(subs), "results": results}


@app.post("/api/subscriptions/{sub_id}/download-history")
async def download_subscription_history(sub_id: str):
    """Download ALL videos from a subscription (historical + new). Queued with max 3 concurrency."""
    import yt_dlp

    subs = load_subscriptions()
    sub = next((s for s in subs if s["id"] == sub_id), None)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")

    existing_ids = _extract_existing_video_ids()

    def _fetch_all_videos():
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True}) as ydl:
                info = ydl.extract_info(sub["url"], download=False)
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

    # Queue ALL videos that haven't been downloaded
    new_videos = [v for v in result["entries"] if v["id"] not in existing_ids]
    queued_count = 0

    fmt = sub.get("format", "best") or "best"
    quality = sub.get("quality")

    for video in new_videos:
        task_id = task_manager.create_task(video["url"], DownloadFormat(fmt), quality)
        task_manager.set_metadata(task_id, title=video["title"], thumbnail=video.get("thumbnail"), duration=video.get("duration"))
        await download_queue.put((task_id, video["url"], DownloadFormat(fmt), quality, None))
        queued_count += 1

    sub["last_checked"] = datetime.now().isoformat()
    sub["last_video_count"] = queued_count
    save_subscriptions(subs)

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
    return {
        "pending": download_queue.qsize(),
        "max_concurrent": MAX_CONCURRENT_DOWNLOADS,
    }


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
    # Filter by search query (title)
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
    # Accept token from query param for WebSocket auth
    token = websocket.query_params.get("token", "")
    if not token or token not in ACTIVE_TOKENS:
        await websocket.close(code=1008, reason="Unauthorized")
        return
    await websocket.accept()

    messages = []

    def on_message(msg):
        messages.append(msg)
        # Fire-and-forget send
        asyncio.create_task(_safe_send(websocket, msg))

    unsubscribe = task_manager.subscribe(task_id, on_message)

    # Send initial state
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
        # Replay recent messages
        for msg in messages[-5:]:
            await websocket.send_json(msg.dict() if hasattr(msg, "dict") else msg)

    try:
        while True:
            # Keep connection alive
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
