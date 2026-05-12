# YouTube Downloader — Liquid Glass UI

> 基于 yt-dlp 的全栈 Web 下载器，液态玻璃 UI 设计，WebSocket 实时进度推送，支持播放列表批量下载与云端转存。

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-005571?logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/yt--dlp-latest-green?logo=youtube" alt="yt-dlp">
  <img src="https://img.shields.io/badge/Vanilla%20JS-No%20Framework-orange" alt="Vanilla JS">
  <img src="https://img.shields.io/badge/License-MIT-yellow" alt="License">
</p>

---

## ✨ 功能特性

### 下载能力
- **视频下载**：支持最高 8K 分辨率，自动优选 HDR 格式（8K HDR → 4K HDR → 8K SDR → 最高画质）
- **音频提取**：自动提取为 MP3（192kbps）
- **播放列表批量下载**：一键解析整个播放列表，逐视频顺序下载，子任务独立进度追踪
- **Cookie 认证**：上传 cookies.txt 解锁会员专属内容

### 实时体验
- **WebSocket 进度推送**：百分比、下载速度、剩余时间毫秒级更新
- **液态玻璃 UI**：磨砂玻璃卡片 + 浮动渐变光晕 + 丝滑动画，纯 CSS 实现无依赖

### 文件管理
- **云端转存**：一键移动到 115 网盘 / 百度网盘 或自定义路径（FUSE 安全模式：先复制 → 校验大小 → 删除源文件）
- **自动转存**：开启后下载完成自动移动到指定路径，一次只能开启一个路径
- **文件列表**：展示缩略图、时长、文件大小、下载时间
- **自定义路径管理**：动态添加/删除转存目标，无需改代码

### 并发控制
- **可配置并发数**：页面右上角 ⚙️ 设置最大同时任务数（1-10），下载排队，移动不受限
- **队列状态监控**：实时显示下载数、上传数、CD2临时文件数
- **CD2 上传监控**：CloudDrive2 临时目录自动监控，上传完成后自动释放槽位

### 订阅管理
- **频道订阅**：添加 YouTube 频道，定时检查新视频
- **批量历史下载**：一键下载频道全部视频（UU 播放列表解析，突破 100 条限制）
- **自动下载**：开启后新视频自动入队

### 安全与部署
- **SQLite 持久化**：所有数据存入 `data/ytdl.db`，重启零丢失，`git pull` 升级不丢数据
- **Token 鉴权**：Bearer Token 认证，登录获取令牌，登出销毁（Token 也持久化）
- **双服务器部署**：支持本地 + 远程机器同步运行，`git pull` 即可更新
- **systemd 服务化**：开机自启，崩溃自动重启

---

## 🏗️ 技术架构

```
┌─────────────────────────────────────────────────────┐
│                    Frontend                          │
│  static/index.html — Vanilla JS + CSS (3232 lines)   │
│  • Liquid Glass UI / 响应式 / 移动端适配              │
│  • WebSocket 客户端（进度订阅 + ping/pong 保活）       │
│  • 播放列表视图（父任务 → 子任务树）                    │
│  • Cookie 上传 / 路径管理 / 文件列表 / 订阅管理        │
│  • 并发设置面板 / 队列状态实时显示                     │
└──────────────────────┬──────────────────────────────┘
                       │ HTTP + WebSocket
┌──────────────────────▼──────────────────────────────┐
│                    Backend                           │
│  FastAPI (app/main.py — 1138 lines)                  │
│  • REST API：下载 / 任务 / 文件 / 鉴权 / 路径管理      │
│  • WebSocket：/ws/{task_id} 实时推送                  │
│  • Auth Middleware + Token 管理                       │
│  • 异步架构（asyncio + run_in_executor）              │
│  • 并发控制（信号量限制下载，移动不受限）              │
├─────────────────────────────────────────────────────┤
│  Database (app/database.py — 706 lines)              │
│  • SQLite + WAL 模式，线程安全                        │
│  • 全量持久化：任务/队列/订阅/路径/配置/Token          │
│  • 首次启动自动从 JSON 文件迁移                        │
├─────────────────────────────────────────────────────┤
│  Task Manager (app/tasks.py — 194 lines)             │
│  • SQLite 持久化（通过 database.py）                   │
│  • 发布/订阅模式（WebSocket 消息广播）                  │
│  • 播放列表父子任务层级管理                            │
├─────────────────────────────────────────────────────┤
│  Downloader (app/downloader.py — 501 lines)          │
│  • yt-dlp Python API（非 subprocess）                 │
│  • progress_hook → WebSocket 推送                     │
│  • HDR 自动优选 / 地理绕过 / JS 签名解析               │
│  • FUSE-safe 文件移动（cp + verify + rm）             │
│  • 下载失败自动重试（Broken pipe / 网络错误）          │
└─────────────────────────────────────────────────────┘
```

### 数据模型 (Pydantic v2)
- `DownloadRequest` / `TaskResponse` / `FileItem` / `MoveRequest` / `CloudPath`
- 全量 type hints，请求/响应自动校验

### 前端技术
- **零框架依赖**：纯 Vanilla JS + 现代 CSS（CSS Variables / Grid / Flexbox）
- **Liquid Glass 设计**：`backdrop-filter: blur(20px)` + 半透明边框 + 渐变光晕
- **响应式布局**：移动端 / 桌面端自适应

---

## 🚀 快速开始

### 环境要求
- Python 3.11+
- FFmpeg（视频合并 / 音频提取）

### 安装

```bash
cd /root/youtube-downloader

# 安装依赖
pip3 install --break-system-packages -r requirements.txt

# 启动
./start.sh          # 默认端口 8080
./start.sh 3000     # 自定义端口
```

### systemd 服务（开机自启）

```bash
sudo cp youtube-downloader.service /etc/systemd/system/
sudo systemctl enable youtube-downloader
sudo systemctl start youtube-downloader
```

### 访问
打开浏览器访问 `http://<your-ip>:8080`，使用 admin 账号登录。

---

## 🧩 油猴脚本（Tampermonkey）

安装油猴脚本后，可在 YouTube 页面直接一键推送视频到下载器，无需手动复制链接。

**安装地址：** [youtube-downloader.user.js](https://raw.githubusercontent.com/zym20192019/YouTube-Downloader/main/static/youtube-downloader.user.js)

**功能：**
- YouTube 视频页面显示下载按钮
- 支持多服务器配置
- 自动登录（保存密码）
- 格式/质量选择
- 可拖拽浮动面板

---

## 📡 API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/login` | 登录获取 Token |
| `POST` | `/api/logout` | 登出销毁 Token |
| `POST` | `/api/download` | 创建单个视频下载任务 |
| `POST` | `/api/playlist/download` | 创建播放列表下载任务 |
| `GET` | `/api/playlist/info` | 预览播放列表信息 |
| `GET` | `/api/playlist/{id}/tasks` | 获取播放列表子任务 |
| `GET` | `/api/tasks` | 获取所有任务列表 |
| `GET` | `/api/tasks/{id}` | 获取单个任务详情 |
| `DELETE` | `/api/tasks/{id}` | 删除任务及文件 |
| `POST` | `/api/move` | 移动文件到云端 |
| `GET` | `/api/files` | 列出已下载文件 |
| `POST` | `/api/cookies` | 上传 Cookie 文件 |
| `GET` | `/api/cookies/status` | Cookie 状态 |
| `GET/POST/DELETE` | `/api/paths` | 自定义转存路径 CRUD |
| `POST` | `/api/paths/{id}/auto-move` | 切换自动转存（仅一个路径可开启） |
| `GET` | `/api/paths/auto-move` | 获取当前自动转存配置 |
| `GET` | `/api/queue/status` | 下载队列状态（下载/上传/并发） |
| `GET/POST` | `/api/concurrency/config` | 并发设置（最大任务数/CD2目录/检查间隔） |
| `GET` | `/api/subscriptions` | 订阅列表 |
| `POST` | `/api/subscriptions` | 添加订阅 |
| `POST` | `/api/subscriptions/{id}/check` | 检查订阅更新 |
| `POST` | `/api/subscriptions/{id}/download-history` | 下载订阅历史视频 |
| `POST` | `/api/tasks/batch-move` | 批量转存 |
| `POST` | `/api/tasks/batch-delete` | 批量删除 |
| `WS` | `/ws/{task_id}` | WebSocket 进度订阅 |

### WebSocket 消息格式

**服务端推送：**
```json
{"type": "progress", "task_id": "abc", "percent": 45.2, "speed": "10.5MB/s", "eta": "2:30"}
{"type": "done", "task_id": "abc", "filename": "video.mp4", "filepath": "/path/to/file"}
{"type": "error", "task_id": "abc", "message": "error description"}
{"type": "playlist_progress", "task_id": "pl_abc", "percent": 50.0, "message": "Downloading video 5/10"}
{"type": "ping"}
```

**客户端心跳：**
```json
"ping" → 服务端回复 {"type": "pong"}
```

---

## 📁 项目结构

```
youtube-downloader/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI 路由 + 鉴权中间件 + 并发控制
│   ├── models.py        # Pydantic 数据模型
│   ├── tasks.py         # 任务管理器（SQLite 持久化）
│   ├── downloader.py    # yt-dlp 集成 + 云端转存 + 自动重试
│   └── database.py      # SQLite 数据库模块（WAL 模式）
├── static/
│   └── index.html       # 液态玻璃 UI 前端（单文件，无构建步骤）
├── data/
│   └── ytdl.db          # SQLite 数据库（所有持久化数据）
├── downloads/           # 下载文件存储目录
├── cookies.txt          # YouTube Cookie（可选）
├── requirements.txt     # Python 依赖
├── start.sh             # 启动脚本
└── youtube-downloader.service  # systemd 服务文件
```

---

## 🔑 核心设计决策

| 决策 | 原因 |
|------|------|
| yt-dlp Python API 而非 subprocess | 可直接注册 progress_hook，避免 stdout 解析 |
| WebSocket 而非 SSE | 双向通信（心跳保活），更适合实时进度 |
| `asyncio.run_in_executor` | yt-dlp 是阻塞 I/O，不阻塞 FastAPI 事件循环 |
| cp + verify + rm 替代 shutil.move | FUSE 挂载的 CloudDrive 对 rename 操作不可靠 |
| 按视频 ID 匹配下载文件 | 时间戳匹配在并发下载时不可靠 |
| 纯前端零框架 | 减少依赖，部署简单，体积轻量 |
| SQLite + WAL 持久化 | 所有数据（任务/队列/订阅/路径/配置/Token）存入 SQLite，重启零丢失 |
| 下载队列持久化 | 排队中的下载任务写入 DB，重启后自动恢复继续下载 |
| 信号量并发控制 | 下载受信号量限制（默认 3 并发），移动/转存永远立即执行 |

---

## 🛠️ 开发记录

本项目通过 **Vibe Coding**（AI 驱动编程）从零构建，持续迭代：

- `3a3510c` — 初始版本：液态玻璃 UI + yt-dlp 后端 + WebSocket + Token 鉴权
- `cf67f81` — 自定义转存路径管理（动态 CRUD）
- `7f822f5` — 8K 分辨率 + HDR 支持
- `b5ab9a0` — 播放列表批量下载 + 暂停/恢复
- `5bd05e7` — 播放列表详情页 + 子任务进度
- `c61e97c` — 任务历史 JSON 持久化
- `ece76f7` — Best 格式自动优选 HDR（8K HDR → 4K HDR → 8K SDR → Max）
- `50d2e65` — 订阅管理 + 频道历史批量下载
- `e181007` — 并发控制 + CD2 上传监控 + SQLite 全量持久化

---

## 📄 License

MIT
