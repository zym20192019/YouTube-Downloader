#!/bin/bash
# YouTube Downloader - Startup Script

PORT=${1:-8080}

echo "🚀 Starting YouTube Downloader on port $PORT..."

cd /root/youtube-downloader
python3 -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
