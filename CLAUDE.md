# YouTube Downloader — Liquid Glass UI

## Architecture
- **Backend:** FastAPI + yt-dlp (async subprocess with WebSocket progress streaming)
- **Frontend:** Single-page app (static/index.html) — vanilla JS + CSS, no frameworks
- **Download dir:** /root/youtube-downloader/downloads/
- **CloudDrive targets:** /Movies/CloudDrive/115/ and /Movies/CloudDrive/百度网盘/

## Key Commands
- `pip3 install --break-system-packages -r requirements.txt` — install deps
- `python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8080` — start server

## Features Required
1. **Download**: Paste YouTube URL → select format (video/audio/best) → download with yt-dlp
2. **Progress**: Real-time WebSocket progress bar (% + speed + ETA)
3. **Cookie support**: Upload/parse YouTube cookies.txt file for authentication
4. **File list**: Show downloaded files with size, duration, thumbnail
5. **Move to CloudDrive**: One-click move to 115 or 百度网盘
6. **Task queue**: Multiple downloads, show status (queued/downloading/done/error)

## UI Design — Liquid Glass
- Dark gradient background (deep purple → navy blue)
- Frosted glass cards with `backdrop-filter: blur(20px)`
- Semi-transparent white borders with subtle glow
- Animated gradient orbs floating behind glass panels
- Smooth hover animations with scale + glow effects
- Glass buttons with inner light reflections
- Progress bars with gradient + glow
- Font: Inter or system sans-serif
- Color palette: primary #6366f1, accent #a855f7, success #22c55e, error #ef4444

## Code Standards
- Python 3.11+, type hints on all functions
- Pydantic v2 for request/response models
- Async-first (async def everywhere possible)
- yt-dlp uses `yt_dlp.YoutubeDL` class directly (not subprocess) for progress hooks
- Clean error handling with user-friendly messages
- Frontend: no frameworks, vanilla JS, modern CSS (variables, grid, flexbox)
- Mobile responsive design

## yt-dlp Integration
- Use `yt_dlp.YoutubeDL` Python API (NOT subprocess)
- Progress via `progress_hooks` callback → emit via WebSocket
- Download hook for file path tracking
- Extract video info (title, thumbnail, duration, formats) via `extract_info`
- Cookie support: pass `cookiefile` param to YoutubeDL

## WebSocket Protocol
- Client connects to `/ws/{task_id}` for progress
- Server sends JSON: `{"type": "progress", "percent": 0.5, "speed": "10MB/s", "eta": "2:30"}`
- Server sends JSON: `{"type": "done", "filename": "video.mp4", "filepath": "/path/to/file"}`
- Server sends JSON: `{"type": "error", "message": "error description"}`

## File Move
- POST `/api/move` with `{task_id, target}` where target is "115" or "baidu"
- Maps to /Movies/CloudDrive/115/ or /Movies/CloudDrive/百度网盘/
- Uses `shutil.move` for local filesystem move
- Returns new path on success
