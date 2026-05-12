import os
import json
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

PATH_CONFIG_FILE = Path("/root/youtube-downloader/path_config.json")


def get_auto_move_path() -> Optional[dict]:
    """Get the auto-move path configuration if enabled."""
    if PATH_CONFIG_FILE.exists():
        try:
            with open(PATH_CONFIG_FILE, "r") as f:
                paths = json.load(f)
                for p in paths:
                    if p.get("auto_move", False):
                        return p
        except (json.JSONDecodeError, IOError):
            pass
    return None


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
        "socket_timeout": 120,                       # 增加到 2 分钟
        "retries": 15,                               # 增加重试次数
        "fragment_retries": 15,                      # 分片重试
        "file_access_retries": 5,
        "retry_sleep": 5,                            # 重试间隔 5 秒
        "buffersize": 4096,                          # 增大缓冲区
        "concurrent_fragment_downloads": 4,
        "hls_use_mpegts": True,                      # HLS 流使用 MPEG-TS（更稳定）
        "js_runtimes": {"node": {}},
        "geo_bypass": True,
        "remote_components": ["ejs:github"],
        "extractor_retries": 5,
        "sleep_interval_requests": 0.5,              # 请求间隔防限速
    }

    if COOKIE_FILE.exists():
        opts["cookiefile"] = str(COOKIE_FILE)

    return opts


def _find_downloaded_file(task_id: str) -> Optional[tuple]:
    """Find the downloaded file in downloads dir, match by video ID in filename."""
    task = task_manager.get_task(task_id)
    if not task:
        return None

    # Extract video ID from URL
    url = task.get("url", "")
    video_id = None
    if "watch?v=" in url:
        video_id = url.split("watch?v=")[1].split("&")[0]
    elif "youtu.be/" in url:
        video_id = url.split("youtu.be/")[1].split("?")[0]

    if video_id:
        # Find file containing video ID in its name
        for f in DOWNLOAD_DIR.iterdir():
            if f.is_file() and video_id in f.name:
                return (f.name, str(f), f.stat().st_size)

    # Fallback: most recent file
    files = [(f, f.stat().st_mtime) for f in DOWNLOAD_DIR.iterdir() if f.is_file()]
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
    """Run yt-dlp download in a thread to not block the event loop. Auto-retries on network errors."""
    task_manager.update_task(task_id, status="downloading")

    loop = asyncio.get_event_loop()
    ydl_opts = _get_ydl_opts(task_id, fmt, quality, hdr)
    max_retries = 3

    def _download():
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                task_manager.set_metadata(
                    task_id,
                    title=info.get("title", "Unknown"),
                    thumbnail=info.get("thumbnail"),
                    duration=info.get("duration"),
                )
                ydl.download([url])
        except Exception as e:
            task_manager.set_error(task_id, str(e))
            return False
        return True

    for attempt in range(1, max_retries + 1):
        # Reset task state before retry
        if attempt > 1:
            task_manager.update_task(task_id, status="queued", error=None)
            task_manager.update_task(task_id, status="downloading")
            await asyncio.sleep(5 * attempt)  # 5s, 10s, 15s backoff

        success = await loop.run_in_executor(None, _download)

        if success:
            result = _find_downloaded_file(task_id)
            if result:
                filename, filepath, filesize = result
                task_manager.set_done(task_id, filename, filepath, filesize)
                
                # Auto-move if configured
                auto_move_path = get_auto_move_path()
                if auto_move_path:
                    try:
                        await move_to_cloud_drive(task_id, auto_move_path["path"], auto_move_path["name"])
                    except Exception as e:
                        print(f"Auto-move failed for task {task_id}: {e}")
            else:
                task_manager.set_error(task_id, "Download completed but file not found")
            return

        # Check if error is retryable (Broken pipe, network issues)
        task = task_manager.get_task(task_id)
        error_msg = task.get("error", "") if task else ""
        if "Broken pipe" not in error_msg and "Connection" not in error_msg and "timeout" not in error_msg.lower():
            # Non-retryable error, stop trying
            return


async def download_playlist(playlist_id: str, url: str, fmt: DownloadFormat, quality: Optional[str] = None, hdr: Optional[str] = None):
    """Download all videos in a playlist. Creates a parent task with child tasks."""
    task_manager.update_task(playlist_id, status="downloading")

    loop = asyncio.get_event_loop()

    # Normalize channel URLs to "Uploads" playlist to ensure full video coverage
    # Channel URLs like /@name/videos often miss "Popular" videos or older content.
    # The UU playlist contains everything.
    def _resolve_channel_url(target_url):
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
                info = ydl.extract_info(target_url, download=False)
                channel_id = info.get("channel_id")
                if channel_id and channel_id.startswith("UC"):
                    uploads_id = "UU" + channel_id[2:]
                    return f"https://www.youtube.com/playlist?list={uploads_id}"
        except Exception:
            pass
        return target_url

    # Run resolution in thread to avoid blocking if yt-dlp hangs
    resolved_url = await loop.run_in_executor(None, _resolve_channel_url, url)
    if resolved_url != url:
        task_manager.update_task(playlist_id, title="Channel Playlist Resolved")

    ydl_opts = _get_ydl_opts(playlist_id, fmt, quality, hdr)
    ydl_opts["noplaylist"] = False  # Enable playlist download

    def _extract_playlist():
        try:
            with yt_dlp.YoutubeDL({**ydl_opts, "quiet": True, "no_warnings": True}) as ydl:
                # Use the resolved URL (UU playlist) if available
                extract_url = resolved_url
                info = ydl.extract_info(extract_url, download=False)
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
        # Check if playlist is paused — wait until resumed
        while True:
            pl_task = task_manager.get_task(playlist_id)
            if pl_task and pl_task.get("status") == "paused":
                await asyncio.sleep(2)
                continue
            break

        # Check if playlist was deleted while paused
        pl_task = task_manager.get_task(playlist_id)
        if not pl_task:
            return

        child_id = f"{playlist_id}_{i}"
        task_manager.create_child_task(child_id, playlist_id, entry["url"], entry["title"], fmt, quality, thumbnail=entry.get("thumbnail"), duration=entry.get("duration"))

        # Download this video with retry on network errors
        child_opts = _get_ydl_opts(child_id, fmt, quality, hdr)
        max_retries = 3
        success = False

        for attempt in range(1, max_retries + 1):
            if attempt > 1:
                # Reset child task state for retry
                task_manager.update_task(child_id, status="queued", error=None)
                task_manager.update_task(child_id, status="downloading")
                await asyncio.sleep(5 * attempt)  # 5s, 10s, 15s backoff

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
                break
            else:
                # Check if error is retryable
                child_task = task_manager.get_task(child_id)
                error_msg = child_task.get("error", "") if child_task else ""
                if "Broken pipe" not in error_msg and "Connection" not in error_msg and "timeout" not in error_msg.lower():
                    break  # Non-network error, don't retry

        # Update playlist progress whether success or failed
        task_manager.set_playlist_progress(playlist_id, i + 1, total)

    task_manager.set_playlist_done(playlist_id)


async def resume_playlist_download(playlist_id: str, url: str, fmt: DownloadFormat, quality: Optional[str], entries: list, resume_from: int):
    """Resume a paused playlist from a specific video index."""
    task_manager.update_task(playlist_id, status="downloading")
    loop = asyncio.get_event_loop()

    total = len(entries)
    # Update progress to reflect where we're resuming from
    task_manager.set_playlist_progress(playlist_id, resume_from, total)

    for i in range(resume_from, total):
        entry = entries[i]

        # Check if playlist is paused — wait until resumed
        while True:
            pl_task = task_manager.get_task(playlist_id)
            if pl_task and pl_task.get("status") == "paused":
                await asyncio.sleep(2)
                continue
            break

        # Check if playlist was deleted while paused
        pl_task = task_manager.get_task(playlist_id)
        if not pl_task:
            return

        child_id = f"{playlist_id}_{i}"

        # Skip if this child task already completed
        existing_child = task_manager.get_task(child_id)
        if existing_child and existing_child.get("status") in ("done", "moved"):
            task_manager.set_playlist_progress(playlist_id, i + 1, total)
            continue

        task_manager.create_child_task(child_id, playlist_id, entry["url"], entry["title"], fmt, quality, thumbnail=entry.get("thumbnail"), duration=entry.get("duration"))

        # Download this video with retry on network errors
        child_opts = _get_ydl_opts(child_id, fmt, quality, None)
        max_retries = 3
        success = False

        for attempt in range(1, max_retries + 1):
            if attempt > 1:
                task_manager.update_task(child_id, status="queued", error=None)
                task_manager.update_task(child_id, status="downloading")
                await asyncio.sleep(5 * attempt)

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
                break
            else:
                child_task = task_manager.get_task(child_id)
                error_msg = child_task.get("error", "") if child_task else ""
                if "Broken pipe" not in error_msg and "Connection" not in error_msg and "timeout" not in error_msg.lower():
                    break

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
