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
    try:
        bytes_val = float(bytes_val)
    except (ValueError, TypeError):
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
    else:  # best — 8K HDR → 4K HDR → 8K → 最高画质
        format_spec = (
            "bestvideo[height<=4320][dynamicrange=hdr]+bestaudio/"
            "bestvideo[height<=4320][color_primaries=bt2020]+bestaudio/"
            "bestvideo[height<=4320][dynamicrange=sdr]+bestaudio/"
            "bestvideo[height<=4320]+bestaudio/"
            "bestvideo+bestaudio/best"
        )
        postprocessors = [{
            "key": "FFmpegVideoRemuxer",
            "preferedformat": "mp4",
        }]

    def progress_hook(d: dict):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0) or 0
            try:
                total = int(total) if total else 0
                downloaded = int(downloaded) if downloaded else 0
            except (ValueError, TypeError):
                total = 0
                downloaded = 0
            if total and total > 0:
                pct = (downloaded / total) * 100
            else:
                pct = 0
            speed = _format_bytes(d.get("speed") or 0)
            eta = _format_seconds(d.get("eta") or 0)
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
        "socket_timeout": 60,
        "retries": 10,
        "fragment_retries": 10,
        "file_access_retries": 5,
        "retry_sleep": 2,
        "buffersize": 1024,
        "concurrent_fragment_downloads": 4,
        "js_runtimes": {"node": {}},              # YouTube JS 签名解析
        "geo_bypass": True,                        # 绕过地理限制
        "remote_components": ["ejs:github"],       # 下载远程 EJS 挑战脚本
        "extractor_retries": 3,                    # 提取器重试
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


async def download_playlist(playlist_id: str, url: str, fmt: DownloadFormat, quality: Optional[str] = None, hdr: Optional[str] = None):
    """Download all videos in a playlist. Creates a parent task with child tasks."""
    task_manager.update_task(playlist_id, status="downloading")

    loop = asyncio.get_event_loop()
    ydl_opts = _get_ydl_opts(playlist_id, fmt, quality, hdr)
    ydl_opts["noplaylist"] = False  # Enable playlist download

    def _extract_playlist():
        try:
            with yt_dlp.YoutubeDL({**ydl_opts, "quiet": True, "no_warnings": True}) as ydl:
                info = ydl.extract_info(url, download=False)
                if info.get("_type") == "playlist":
                    return {
                        "title": info.get("title", "Unknown Playlist"),
                        "thumbnail": info.get("thumbnail"),
                        "entries": [
                            {
                                "id": e.get("id"),
                                "title": e.get("title", "Unknown"),
                                "url": e.get("webpage_url") or f"https://www.youtube.com/watch?v={e.get('id')}",
                                "duration": e.get("duration"),
                                "thumbnail": e.get("thumbnail"),
                            }
                            for e in info.get("entries", [])
                            if e and e.get("id")
                        ]
                    }
                else:
                    # Single video, not a playlist
                    return None
        except Exception as e:
            task_manager.set_error(playlist_id, str(e))
            return None

    playlist_info = await loop.run_in_executor(None, _extract_playlist)

    if not playlist_info:
        task_manager.set_error(playlist_id, "Not a valid playlist or extraction failed")
        return

    task_manager.set_playlist_info(playlist_id, playlist_info)

    # Create child tasks and download them sequentially
    total = len(playlist_info["entries"])
    task_manager.set_playlist_progress(playlist_id, 0, total)

    for i, entry in enumerate(playlist_info["entries"]):
        child_id = f"{playlist_id}_{i}"
        task_manager.create_child_task(child_id, playlist_id, entry["url"], entry["title"], fmt, quality)

        # Download this video
        child_opts = _get_ydl_opts(child_id, fmt, quality, hdr)
        def _download_single():
            try:
                with yt_dlp.YoutubeDL(child_opts) as ydl:
                    ydl.download([entry["url"]])
            except Exception as e:
                task_manager.set_child_error(child_id, str(e))
                return False
            return True

        success = await loop.run_in_executor(None, _download_single)
        if success:
            result = _find_downloaded_file(child_id)
            if result:
                filename, filepath, filesize = result
                task_manager.set_child_done(child_id, filename, filepath, filesize)
        
        # Update playlist progress
        task_manager.set_playlist_progress(playlist_id, i + 1, total)

    task_manager.set_playlist_done(playlist_id)


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
