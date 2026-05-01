# 🚀 部署指南

## 环境要求

| 依赖 | 最低版本 | 说明 |
|------|---------|------|
| Python | 3.11+ | 推荐 3.12 |
| FFmpeg | 4.0+ | 视频合并、音频提取必须 |
| Node.js | 18+ | yt-dlp JS 签名解析（可选但推荐） |
| Git | 2.0+ | 克隆项目、后续更新 |

## 一、快速部署（5 分钟）

### 1. 克隆项目

```bash
git clone https://github.com/zym20192019/YouTube-Downloader.git /root/youtube-downloader
cd /root/youtube-downloader
```

### 2. 安装依赖

```bash
# 系统依赖
apt update && apt install -y python3 python3-pip ffmpeg git nodejs

# Python 依赖（Debian/Ubuntu 需要 --break-system-packages）
pip3 install --break-system-packages -r requirements.txt
```

> **CentOS/RHEL**：用 `yum install python3 ffmpeg git` + `pip3 install -r requirements.txt`（无需 `--break-system-packages`）

### 3. 修改密码（重要！）

编辑 `app/main.py`，找到第 94-95 行：

```python
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "你的密码"   # ← 改成你自己的密码
```

### 4. 启动

```bash
# 方式一：直接启动
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8080

# 方式二：用启动脚本（支持自定义端口）
chmod +x start.sh
./start.sh          # 默认 8080
./start.sh 3000     # 自定义端口
```

访问 `http://<你的IP>:8080`，用 admin + 你设置的密码登录。

---

## 二、systemd 服务化（推荐）

让服务开机自启、崩溃自动重启：

```bash
# 复制服务文件
cp youtube-downloader.service /etc/systemd/system/

# 如果项目不在 /root/youtube-downloader，修改服务文件：
# sed -i 's|/root/youtube-downloader|你的实际路径|g' /etc/systemd/system/youtube-downloader.service

# 启用并启动
systemctl daemon-reload
systemctl enable youtube-downloader
systemctl start youtube-downloader

# 查看状态
systemctl status youtube-downloader

# 查看日志
journalctl -u youtube-downloader -f
```

---

## 三、配置说明

### 密码

`app/main.py` 第 95 行：

```python
ADMIN_PASSWORD = "你的密码"
```

### 自定义转存路径

通过网页 UI 添加（📁 已下载文件 → 添加路径），或直接编辑 `path_config.json`：

```json
[
  {"id": "default", "name": "下载目录", "path": "/root/youtube-downloader/downloads", "icon": "📁"},
  {"id": "custom1", "name": "NAS备份", "path": "/mnt/nas/videos", "icon": "💾"}
]
```

### Cookie（可选）

如果需要下载会员内容或遇到 403 错误：

1. 浏览器安装 [Get cookies.txt](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) 扩展
2. 登录 YouTube 后导出 `cookies.txt`
3. 放到项目根目录，重启服务

---

## 四、Nginx 反向代理（可选）

如果需要 HTTPS 或域名访问：

```nginx
server {
    listen 80;
    server_name dl.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;

        # WebSocket 支持
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # 大文件上传
        client_max_body_size 500M;
    }
}
```

加上 HTTPS（Let's Encrypt）：

```bash
apt install certbot python3-certbot-nginx
certbot --nginx -d dl.yourdomain.com
```

---

## 五、更新

```bash
cd /root/youtube-downloader
git pull
systemctl restart youtube-downloader    # 如果用了 systemd
```

---

## 六、常见问题

### 下载失败：`Sign in to confirm you're not a bot`

YouTube 对无 Cookie 请求做了限制。解决：
1. 上传 `cookies.txt`（见上方 Cookie 说明）
2. 或者等几分钟后重试

### 下载速度慢

- YouTube 对高码率视频有服务端限速，正常现象
- 确保服务器带宽足够（4K 视频建议 50Mbps+）

### 端口被占用

```bash
# 查看占用端口的进程
lsof -i :8080

# 杀掉后重启
kill -9 <PID>
systemctl restart youtube-downloader
```

### 视频合并失败

确保 FFmpeg 已安装且在 PATH 中：

```bash
ffmpeg -version
# 如果没有：apt install ffmpeg
```

### 磁盘空间不足

下载的文件在 `downloads/` 目录，定期清理或配置自动转存路径。

---

## 七、安全建议

1. **务必修改默认密码**（`app/main.py` 第 95 行）
2. 如果公网暴露，建议加 Nginx + HTTPS + fail2ban
3. `cookies.txt` 和 `task_history.json` 已在 `.gitignore` 中，不会被提交
4. 定期 `git pull` 获取安全更新

---

## 八、项目结构

```
youtube-downloader/
├── app/
│   ├── main.py          # FastAPI 路由 + 鉴权
│   ├── models.py        # 数据模型
│   ├── tasks.py         # 任务管理器
│   └── downloader.py    # yt-dlp 下载逻辑
├── static/
│   └── index.html       # 前端（单文件，液态玻璃 UI）
├── downloads/           # 下载文件存储
├── cookies.txt          # YouTube Cookie（可选）
├── path_config.json     # 转存路径配置
├── task_history.json    # 任务持久化
├── requirements.txt     # Python 依赖
├── start.sh             # 启动脚本
└── youtube-downloader.service  # systemd 服务文件
```

---

## 九、API 速览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/login` | 登录获取 Token |
| POST | `/api/download` | 下载单个视频 |
| POST | `/api/playlist/download` | 下载播放列表 |
| POST | `/api/playlist/{id}/pause` | 暂停播放列表 |
| POST | `/api/playlist/{id}/resume` | 继续播放列表 |
| GET | `/api/tasks` | 任务列表 |
| GET | `/api/files` | 已下载文件列表 |
| POST | `/api/move` | 移动文件到云端 |
| WS | `/ws/{task_id}` | WebSocket 进度 |

---

有问题提 [Issue](https://github.com/zym20192019/YouTube-Downloader/issues)。
