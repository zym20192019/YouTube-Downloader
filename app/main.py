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

from app.models import (
    DownloadRequest, MoveRequest, TaskResponse, TaskListResponse,
    MoveResponse, FileItem, TaskStatus, DownloadFormat, CloudPath
)
from app.tasks import task_manager
from app.downloader import download_video, move_to_cloud_drive, DOWNLOAD_DIR, COOKIE_FILE


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

# Auth
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "Zym@qwe123"
ACTIVE_TOKENS: dict[str, str] = {}  # token -> username

AUTH_WHITELIST = {"/api/login", "/api/health"}

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

    # Start download in background
    asyncio.create_task(
        download_video(task_id, req.url, req.format, req.quality)
    )

    task = task_manager.get_task(task_id)
    return TaskResponse(**task)


@app.get("/api/tasks", response_model=TaskListResponse)
async def list_tasks():
    tasks = task_manager.list_tasks()
    return TaskListResponse(
        tasks=[TaskResponse(**t) for t in tasks],
        total=len(tasks),
    )


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


@app.get("/api/files", response_model=list[FileItem])
async def list_files():
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
