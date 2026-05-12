# SQLite 持久化重构计划

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** 将所有 JSON 文件 + 内存状态迁移到 SQLite，重启不丢任何数据。

**Architecture:** 单一 SQLite 数据库 (`data/ytdl.db`)，WAL 模式。保留现有 API 端点不变，前端无需修改。

**Tech Stack:** Python sqlite3 (stdlib), FastAPI, 现有 yt-dlp 集成

---

## 当前数据清单

| 数据 | 当前存储 | 重启丢失? | 迁移目标 |
|------|---------|----------|---------|
| 任务记录 | task_history.json (JSON dict) | 否 | SQLite tasks 表 |
| 下载队列 | asyncio.Queue (内存) | **是** | SQLite queued_downloads 表 |
| 订阅 | subscriptions.json | 否 | SQLite subscriptions 表 |
| 转存路径 | path_config.json | 否 | SQLite paths 表 |
| 并发配置 | config.json | 否 | SQLite config 表 |
| 登录 token | dict (内存) | **是** | SQLite tokens 表 |
| 取消任务 | set (内存) | **是** | SQLite tasks.status='cancelled' |
| CD2上传计数 | int (内存) | 否(运行时) | 保持内存 |

## SQLite Schema

```sql
-- data/ytdl.db

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    url TEXT,
    title TEXT,
    format TEXT DEFAULT 'best',
    quality TEXT,
    status TEXT DEFAULT 'queued',
    progress REAL DEFAULT 0,
    speed TEXT,
    eta TEXT,
    filename TEXT,
    filepath TEXT,
    filesize INTEGER,
    error TEXT,
    thumbnail TEXT,
    duration REAL,
    created_at TEXT,
    updated_at TEXT,
    cloud_path TEXT,
    -- playlist fields
    is_playlist INTEGER DEFAULT 0,
    playlist_title TEXT,
    playlist_total INTEGER,
    playlist_current INTEGER,
    playlist_url TEXT,
    parent_id TEXT,
    playlist_index INTEGER,
    FOREIGN KEY (parent_id) REFERENCES tasks(task_id)
);

CREATE TABLE IF NOT EXISTS queued_downloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    url TEXT NOT NULL,
    format TEXT DEFAULT 'best',
    quality TEXT,
    hdr INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    sub_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    name TEXT,
    auto_download INTEGER DEFAULT 0,
    format TEXT DEFAULT 'best',
    quality TEXT,
    created_at TEXT,
    last_checked TEXT,
    last_video_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS paths (
    path_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    icon TEXT DEFAULT '📁',
    auto_move INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tokens (
    token TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
```

## Task List

### Task 1: 创建数据库模块 `app/database.py`

**Objective:** 新建 database.py，初始化 SQLite 连接 + schema + CRUD 方法

**Files:**
- Create: `app/database.py`

**内容:**
- `get_db()` — 获取线程本地连接 (WAL mode)
- `init_db()` — 建表 (CREATE TABLE IF NOT EXISTS)
- Tasks CRUD: `insert_task`, `update_task`, `get_task`, `delete_task`, `list_tasks`
- Queue CRUD: `enqueue`, `dequeue`, `drain_queue`, `queue_size`, `restore_queue`
- Subscriptions CRUD: `insert_sub`, `update_sub`, `delete_sub`, `list_subs`
- Paths CRUD: `insert_path`, `update_path`, `delete_path`, `list_paths`
- Config: `get_config`, `set_config`
- Tokens: `insert_token`, `get_token`, `delete_token`

**验证:** `python3 -c "from app.database import init_db; init_db(); print('OK')"`

### Task 2: 重构 `app/tasks.py` — TaskManager 用 SQLite

**Objective:** 让 TaskManager 所有读写走 SQLite，不再用 JSON 文件

**Files:**
- Modify: `app/tasks.py` (完全重写)

**关键变更:**
- `__init__` 调用 `init_db()`
- 所有 `self.tasks` dict 操作 → `database.xxx_task()` 调用
- `_load_history()` / `_save_history()` → 删除
- `subscribers` 保持内存 (WebSocket 回调无法持久化)
- `cancelled_tasks` → 检查 DB 中 status='cancelled'

**验证:** `python3 -c "from app.tasks import TaskManager; tm = TaskManager(); print(tm.list_tasks())"`

### Task 3: 重构 `app/main.py` — 端点用数据库

**Objective:** 所有 API 端点从 JSON 文件切换到数据库

**Files:**
- Modify: `app/main.py`

**关键变更:**
- 删除 `load_subscriptions()`, `save_subscriptions()`, `load_path_config()`, `save_path_config()`, `load_concurrency_config()`
- 所有端点改用 `database.xxx()` 调用
- `download_queue` 改用 DB 持久化：enqueue 时写 DB，dequeue 时从 DB 删除
- 启动时从 DB 恢复队列: `database.restore_queue(download_queue)`
- `ACTIVE_TOKENS` 改用 DB
- 删除所有 JSON 文件 I/O

**验证:** 启动服务，测试 `/api/tasks`, `/api/queue/status`, `/api/concurrency/config`

### Task 4: 持久化 download_queue

**Objective:** 重启后自动恢复排队中的下载任务

**Files:**
- Modify: `app/main.py` (startup)
- Modify: `app/database.py` (queue methods)

**关键变更:**
- `download_queue.put()` → 先写 DB `queued_downloads` 表，再放内存队列
- `download_worker` dequeue → 从 DB 删除记录
- `startup()` → 从 DB 恢复队列到内存
- `_drain_download_queue()` → 清空 DB + 内存队列
- 删除任务时 → 从 DB 队列也删除

**验证:** 添加 3 个下载任务 → 重启服务 → 检查队列是否自动恢复

### Task 5: 迁移现有 JSON 数据到 SQLite

**Objective:** 首次启动时自动迁移 JSON 文件数据到 DB

**Files:**
- Modify: `app/database.py` (添加 `migrate_from_json()`)

**迁移逻辑:**
- 如果 `data/ytdl.db` 不存在且 `task_history.json` 存在 → 迁移 tasks
- 如果 `subscriptions.json` 存在 → 迁移 subscriptions
- 如果 `path_config.json` 存在 → 迁移 paths
- 如果 `config.json` 存在 → 迁移 config
- 迁移后重命名原文件为 `.json.bak`

**验证:** 在有旧数据的目录运行 → 检查 DB 中数据完整

### Task 6: 更新 .gitignore + 创建 data/ 目录

**Objective:** 确保 data/ 目录不被 git 跟踪

**Files:**
- Modify: `.gitignore`
- Create: `data/.gitkeep`

**验证:** `git status` 不显示 data/ 内容

### Task 7: 测试 + 部署

**Objective:** 本地测试通过后部署两台机器

**步骤:**
1. 本地: `python3 -m py_compile app/database.py app/tasks.py app/main.py`
2. 本地: 启动服务，测试所有 API
3. 本地: 添加任务 → 重启 → 验证数据保留
4. Commit + Push
5. 两台机器同步 + 重启
6. 验证: 添加任务 → 重启 → 确认数据不丢
