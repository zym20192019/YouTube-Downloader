import os
import asyncio
import threading
from pathlib import Path
from typing import Optional, Callable, Dict, Any

import yt_dlp

from app.tasks import task_manager
from app.models import DownloadFormat


DOWNLOAD_DIR = Path("/root/youtube-downloader/downloads")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

COOKIE_FILE = Path("/root/youtube-downloader/cookies.txt")


def _format_seconds(seconds: Optional[float]) -> Optional[str]:
    if not seconds:
        return None
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _format_bytes(bytes_val: Optional[float]) -> Optional[str]:
    if not bytes_val:
        return None
    for unit in ["B", "KB", "MB", "GB"]:
        if bytes_val < 1024:
            return f"{bytes_val:.1f}{unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f}TB"


def _get_ydl_opts(task_id: str, fmt: DownloadFormat, quality: Optional[str] = None, hdr: Optional[str] = None) -> Dict[str, Any]:
    outtmpl = str(DOWNLOAD_DIR / "%(title)s [%(id)s].%(ext)s")

    if fmt == DownloadFormat.AUDIO:
        format_spec = "bestaudio/best"
        postprocessors = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    elif fmt == DownloadFormat.VIDEO:
        if quality and "4320" in quality:
            format_spec = "bestvideo[height<=4320]+bestaudio/best"
        elif quality and "2160" in quality:
            format_spec = "bestvideo[height<=2160]+bestaudio/best"
        elif quality and "1080" in quality:
            format_spec = "bestvideo[height<=1080]+bestaudio/best"
        elif quality and "720" in quality:
            format_spec = "bestvideo[height<=720]+bestaudio/best"
        else:
            format_spec = "bestvideo+bestaudio/best"
        postprocessors = [{
            "key": "FFmpegVideoRemuxer",
            "preferedformat": "mp4",
        }]
    else:  # best
        format_spec = "bestvideo+bestaudio/best"
        postprocessors = [{
            "key": "FFmpegVideoRemuxer",
            "preferedformat": "mp4",
        }]

    # HDR preference: add HDR format preferences
    if hdr == "hdr" and fmt != DownloadFormat.AUDIO:
        # Prefer HDR formats (VP9.2/AV1 with HDR, or bt2020 color primaries)
        hdr_formats = "bestvideo[dynamicrange=hdr]+bestvideo[vcodec^=av1]+bestvideo[vcodec^=vp9.2]"
        format_spec = f"{hdr_formats}+bestaudio/best/{format_spec}"

    def progress_hook(d: dict):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            if total and total > 0:
                pct = (downloaded / total) * 100
            else:
                pct = 0
            speed = _format_bytes(d.get("speed", 0))
            eta = _format_seconds(d.get("eta"))
            task_manager.set_progress(task_id, pct, speed, eta)
        elif d["status"] == "finished":
            task_manager.set_progress(task_id, 99.0, None, "Processing...")

    opts = {
        "outtmpl": outtmpl,
        "format": format_spec,
        "progress_hooks": [progress_hook],
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
        "postprocessors": postprocessors if fmt == DownloadFormat.AUDIO else [],
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "js_runtimes": {"node": {}},              # YouTube JS 签名解析
        "geo_bypass": True,                        # 绕过地理限制
        "remote_components": ["ejs:github"],       # 下载远程 EJS 挑战脚本
    }

    if COOKIE_FILE.exists():
        opts["cookiefile"] = str(COOKIE_FILE)

    return opts


def _find_downloaded_file(task_id: str) -> Optional[tuple]:
    """Find the most recently created file in downloads dir for this task."""
    task = task_manager.get_task(task_id)
    if not task:
        return None

    files = []
    for f in DOWNLOAD_DIR.iterdir():
        if f.is_file() and f.stat().st_mtime > datetime_to_timestamp(task["created_at"]):
            files.append((f, f.stat().st_mtime))

    if not files:
        return None

    files.sort(key=lambda x: x[1], reverse=True)
    latest = files[0][0]
    return (latest.name, str(latest), latest.stat().st_size)


def datetime_to_timestamp(iso_str: str) -> float:
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.timestamp()
    except Exception:
        return 0


async def download_video(task_id: str, url: str, fmt: DownloadFormat, quality: Optional[str] = None, hdr: Optional[str] = None):
    """Run yt-dlp download in a thread to not block the event loop."""
    task_manager.update_task(task_id, status="downloading")

    loop = asyncio.get_event_loop()
    ydl_opts = _get_ydl_opts(task_id, fmt, quality, hdr)

    def _download():
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Extract info first
                info = ydl.extract_info(url, download=False)
                task_manager.set_metadata(
                    task_id,
                    title=info.get("title", "Unknown"),
                    thumbnail=info.get("thumbnail"),
                    duration=info.get("duration"),
                )
                # Now download
                ydl.download([url])
        except Exception as e:
            task_manager.set_error(task_id, str(e))
            return False
        return True

    success = await loop.run_in_executor(None, _download)

    if success:
        # Find the downloaded file
        result = _find_downloaded_file(task_id)
        if result:
            filename, filepath, filesize = result
            task_manager.set_done(task_id, filename, filepath, filesize)
        else:
            task_manager.set_error(task_id, "Download completed but file not found")


async def move_to_cloud_drive(task_id: str, target_path: str, target_name: Optional[str] = None) -> Optional[tuple]:
    """Move downloaded file to custom directory.
    
    Uses cp + verify + rm for FUSE-based CloudDrive mounts (shutil.move is unreliable).
    """
    task = task_manager.get_task(task_id)
    if not task or not task.get("filepath"):
        return None

    src = task["filepath"]
    if not os.path.exists(src):
        return None

    dest_dir = target_path

    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(src))

    # Handle duplicate filenames
    if os.path.exists(dest):
        base, ext = os.path.splitext(dest)
        counter = 1
        while os.path.exists(f"{base}_{counter}{ext}"):
            counter += 1
        dest = f"{base}_{counter}{ext}"

    task_manager.update_task(task_id, status="moving")

    def _safe_move():
        """Copy first, verify, then delete original (safe for FUSE mounts)."""
        import subprocess
        # Step 1: Copy file using cp -p (preserve attributes)
        result = subprocess.run(
            ["cp", "-p", src, dest],
            capture_output=True, text=True, timeout=3600
        )
        if result.returncode != 0:
            raise RuntimeError(f"Copy failed: {result.stderr}")

        # Step 2: Verify destination file exists and size matches
        if not os.path.exists(dest):
            raise RuntimeError("Destination file not created")
        src_size = os.path.getsize(src)
        dest_size = os.path.getsize(dest)
        if dest_size != src_size:
            os.remove(dest)  # Clean up partial copy
            raise RuntimeError(
                f"Size mismatch: src={src_size} dest={dest_size}"
            )

        # Step 3: Delete original
        os.remove(src)
        return (src, dest)

    try:
        loop = asyncio.get_event_loop()
        original_src, final_dest = await loop.run_in_executor(None, _safe_move)
        task_manager.update_task(
            task_id,
            status="moved",
            cloud_path=final_dest,
            filepath=final_dest,
        )
        return (original_src, final_dest)
    except Exception as e:
        task_manager.update_task(task_id, status="error", error=f"Move failed: {str(e)}")
        return None
