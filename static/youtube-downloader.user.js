// ==UserScript==
// @name         YouTube Downloader 一键推送
// @namespace    https://github.com/zym20192019/YouTube-Downloader
// @version      1.0.0
// @description  在 YouTube 视频页面添加下载按钮，一键推送到 YouTube Downloader 服务器
// @author       You
// @match        *://www.youtube.com/*
// @match        *://m.youtube.com/*
// @icon         https://www.youtube.com/favicon.ico
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_addStyle
// @grant        GM_registerMenuCommand
// @connect      *
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';

  // ========== 配置 ==========
  const DEFAULT_CONFIG = {
    servers: [
      { name: '本机', url: 'http://23.19.231.70:8080' },
      { name: 'RN', url: 'http://142.171.27.76:8080' },
    ],
    defaultServer: 0,
    defaultFormat: 'video',   // video / audio / best
    defaultQuality: 'best',   // best / 2160 / 1080 / 720 / 480
    username: 'admin',
    password: '',
    autoLogin: true,
  };

  // ========== 样式 ==========
  GM_addStyle(`
    #ytdl-panel {
      position: fixed;
      top: 80px;
      right: 20px;
      z-index: 99999;
      background: rgba(20, 20, 30, 0.95);
      backdrop-filter: blur(20px);
      border: 1px solid rgba(255, 255, 255, 0.12);
      border-radius: 16px;
      padding: 0;
      min-width: 300px;
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      color: #e0e0e0;
      transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
      overflow: hidden;
    }
    #ytdl-panel.ytdl-collapsed {
      min-width: auto;
    }
    #ytdl-panel.ytdl-collapsed .ytdl-body {
      display: none;
    }
    .ytdl-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 12px 16px;
      background: rgba(99, 102, 241, 0.15);
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
      cursor: move;
      user-select: none;
    }
    .ytdl-header span {
      font-size: 14px;
      font-weight: 600;
      color: #a78bfa;
    }
    .ytdl-header-btns {
      display: flex;
      gap: 6px;
    }
    .ytdl-header-btns button {
      background: rgba(255, 255, 255, 0.08);
      border: none;
      color: #999;
      cursor: pointer;
      font-size: 16px;
      width: 28px;
      height: 28px;
      border-radius: 6px;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: all 0.2s;
    }
    .ytdl-header-btns button:hover {
      background: rgba(255, 255, 255, 0.15);
      color: #fff;
    }
    .ytdl-body {
      padding: 14px 16px;
    }
    .ytdl-row {
      margin-bottom: 12px;
    }
    .ytdl-label {
      font-size: 11px;
      color: #888;
      margin-bottom: 4px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    .ytdl-select, .ytdl-input {
      width: 100%;
      background: rgba(255, 255, 255, 0.06);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 8px;
      color: #e0e0e0;
      padding: 8px 10px;
      font-size: 13px;
      outline: none;
      transition: border-color 0.2s;
      box-sizing: border-box;
    }
    .ytdl-select:focus, .ytdl-input:focus {
      border-color: rgba(99, 102, 241, 0.5);
    }
    .ytdl-select option {
      background: #1a1a2e;
      color: #e0e0e0;
    }
    .ytdl-btn-row {
      display: flex;
      gap: 8px;
      margin-top: 14px;
    }
    .ytdl-btn {
      flex: 1;
      padding: 10px 0;
      border: none;
      border-radius: 10px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
    }
    .ytdl-btn-primary {
      background: linear-gradient(135deg, #6366f1, #a855f7);
      color: #fff;
    }
    .ytdl-btn-primary:hover {
      transform: translateY(-1px);
      box-shadow: 0 4px 16px rgba(99, 102, 241, 0.4);
    }
    .ytdl-btn-primary:active {
      transform: translateY(0);
    }
    .ytdl-btn-secondary {
      background: rgba(255, 255, 255, 0.06);
      color: #aaa;
      border: 1px solid rgba(255, 255, 255, 0.1);
    }
    .ytdl-btn-secondary:hover {
      background: rgba(255, 255, 255, 0.1);
      color: #fff;
    }
    .ytdl-status {
      margin-top: 10px;
      padding: 8px 10px;
      border-radius: 8px;
      font-size: 12px;
      display: none;
      word-break: break-all;
    }
    .ytdl-status.ytdl-show { display: block; }
    .ytdl-status.ytdl-success {
      background: rgba(34, 197, 94, 0.15);
      color: #4ade80;
      border: 1px solid rgba(34, 197, 94, 0.2);
    }
    .ytdl-status.ytdl-error {
      background: rgba(239, 68, 68, 0.15);
      color: #f87171;
      border: 1px solid rgba(239, 68, 68, 0.2);
    }
    .ytdl-status.ytdl-loading {
      background: rgba(99, 102, 241, 0.15);
      color: #a78bfa;
      border: 1px solid rgba(99, 102, 241, 0.2);
    }

    /* 页面内的下载按钮 */
    .ytdl-page-btn {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 8px 16px;
      margin-left: 8px;
      background: linear-gradient(135deg, #6366f1, #a855f7);
      color: #fff;
      border: none;
      border-radius: 20px;
      font-size: 14px;
      font-weight: 500;
      cursor: pointer;
      transition: all 0.2s;
      vertical-align: middle;
    }
    .ytdl-page-btn:hover {
      transform: scale(1.05);
      box-shadow: 0 4px 16px rgba(99, 102, 241, 0.4);
    }
    .ytdl-page-btn.ytdl-page-btn-loading {
      opacity: 0.7;
      pointer-events: none;
    }

    /* 设置弹窗 */
    .ytdl-settings-overlay {
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.6);
      z-index: 100000;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .ytdl-settings-box {
      background: rgba(20, 20, 30, 0.98);
      border: 1px solid rgba(255, 255, 255, 0.12);
      border-radius: 16px;
      padding: 24px;
      width: 420px;
      max-width: 90vw;
      max-height: 80vh;
      overflow-y: auto;
      color: #e0e0e0;
    }
    .ytdl-settings-box h3 {
      margin: 0 0 16px 0;
      color: #a78bfa;
      font-size: 16px;
    }
    .ytdl-settings-box .ytdl-row { margin-bottom: 14px; }
    .ytdl-settings-actions {
      display: flex;
      gap: 8px;
      margin-top: 18px;
    }
  `);

  // ========== 工具函数 ==========
  function getConfig() {
    const saved = GM_getValue('ytdl_config', null);
    return saved ? { ...DEFAULT_CONFIG, ...saved } : { ...DEFAULT_CONFIG };
  }

  function saveConfig(cfg) {
    GM_setValue('ytdl_config', cfg);
  }

  function getTokenKey(serverUrl) {
    return 'ytdl_token_' + btoa(serverUrl).replace(/=/g, '');
  }

  function getToken(serverUrl) {
    return GM_getValue(getTokenKey(serverUrl), '');
  }

  function setToken(serverUrl, token) {
    GM_setValue(getTokenKey(serverUrl), token);
  }

  function getVideoUrl() {
    return window.location.href.split('&')[0]; // 去掉播放列表等参数
  }

  function getVideoTitle() {
    const el = document.querySelector('h1.ytd-watch-metadata yt-formatted-string, h1.title yt-formatted-string, #title h1');
    return el ? el.textContent.trim() : document.title.replace(' - YouTube', '');
  }

  function showStatus(el, type, msg) {
    el.className = 'ytdl-status ytdl-show ytdl-' + type;
    el.textContent = msg;
  }

  function gmFetch(opts) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        ...opts,
        onload: (res) => resolve(res),
        onerror: (err) => reject(err),
      });
    });
  }

  // ========== 登录 ==========
  async function ensureLogin(server) {
    const cfg = getConfig();
    const token = getToken(server.url);
    if (token) return token;

    // 自动登录
    if (cfg.autoLogin && cfg.password) {
      try {
        const res = await gmFetch({
          method: 'POST',
          url: server.url + '/api/login',
          headers: { 'Content-Type': 'application/json' },
          data: JSON.stringify({ username: cfg.username, password: cfg.password }),
        });
        const data = JSON.parse(res.responseText);
        if (data.success && data.token) {
          setToken(server.url, data.token);
          return data.token;
        }
      } catch (e) {
        // 登录失败
      }
    }

    throw new Error('未登录，请在设置中配置密码或手动登录');
  }

  // ========== 下载 ==========
  async function pushDownload(server, format, quality) {
    const url = getVideoUrl();
    const title = getVideoTitle();
    const token = await ensureLogin(server);

    const res = await gmFetch({
      method: 'POST',
      url: server.url + '/api/download',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + token,
      },
      data: JSON.stringify({ url, format, quality }),
    });

    if (res.status === 401) {
      // Token 过期，清除后重试
      setToken(server.url, '');
      const newToken = await ensureLogin(server);
      const retry = await gmFetch({
        method: 'POST',
        url: server.url + '/api/download',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer ' + newToken,
        },
        data: JSON.stringify({ url, format, quality }),
      });
      return JSON.parse(retry.responseText);
    }

    if (res.status !== 200) {
      const err = JSON.parse(res.responseText);
      throw new Error(err.detail || 'HTTP ' + res.status);
    }

    return JSON.parse(res.responseText);
  }

  // ========== 设置弹窗 ==========
  function openSettings() {
    const cfg = getConfig();
    const overlay = document.createElement('div');
    overlay.className = 'ytdl-settings-overlay';
    overlay.innerHTML = `
      <div class="ytdl-settings-box">
        <h3>⚙️ YouTube Downloader 设置</h3>

        <div class="ytdl-row">
          <div class="ytdl-label">服务器列表</div>
          <div id="ytdl-server-list"></div>
          <button class="ytdl-btn ytdl-btn-secondary" style="margin-top:8px;padding:6px 12px;font-size:12px;" id="ytdl-add-server">+ 添加服务器</button>
        </div>

        <div class="ytdl-row">
          <div class="ytdl-label">默认服务器</div>
          <select class="ytdl-select" id="ytdl-cfg-default-server">
            ${cfg.servers.map((s, i) => `<option value="${i}" ${i === cfg.defaultServer ? 'selected' : ''}>${s.name}</option>`).join('')}
          </select>
        </div>

        <div class="ytdl-row">
          <div class="ytdl-label">默认格式</div>
          <select class="ytdl-select" id="ytdl-cfg-format">
            <option value="video" ${cfg.defaultFormat === 'video' ? 'selected' : ''}>🎬 视频</option>
            <option value="audio" ${cfg.defaultFormat === 'audio' ? 'selected' : ''}>🎵 仅音频 (MP3)</option>
            <option value="best" ${cfg.defaultFormat === 'best' ? 'selected' : ''}>✨ 最佳画质</option>
          </select>
        </div>

        <div class="ytdl-row">
          <div class="ytdl-label">默认画质</div>
          <select class="ytdl-select" id="ytdl-cfg-quality">
            <option value="best" ${cfg.defaultQuality === 'best' ? 'selected' : ''}>最高可用</option>
            <option value="4320" ${cfg.defaultQuality === '4320' ? 'selected' : ''}>8K (4320p)</option>
            <option value="2160" ${cfg.defaultQuality === '2160' ? 'selected' : ''}>4K (2160p)</option>
            <option value="1080" ${cfg.defaultQuality === '1080' ? 'selected' : ''}>1080p</option>
            <option value="720" ${cfg.defaultQuality === '720' ? 'selected' : ''}>720p</option>
            <option value="480" ${cfg.defaultQuality === '480' ? 'selected' : ''}>480p</option>
          </select>
        </div>

        <div class="ytdl-row">
          <div class="ytdl-label">登录用户名</div>
          <input class="ytdl-input" id="ytdl-cfg-username" value="${cfg.username}">
        </div>

        <div class="ytdl-row">
          <div class="ytdl-label">登录密码</div>
          <input class="ytdl-input" id="ytdl-cfg-password" type="password" value="${cfg.password}" placeholder="用于自动登录">
        </div>

        <div class="ytdl-settings-actions">
          <button class="ytdl-btn ytdl-btn-primary" id="ytdl-save-cfg">💾 保存</button>
          <button class="ytdl-btn ytdl-btn-secondary" id="ytdl-cancel-cfg">取消</button>
        </div>
      </div>
    `;

    document.body.appendChild(overlay);
    renderServerList(cfg);

    // 添加服务器
    overlay.querySelector('#ytdl-add-server').onclick = () => {
      const name = prompt('服务器名称（如：本机、RN）');
      if (!name) return;
      const url = prompt('服务器地址（如：http://1.2.3.4:8080）');
      if (!url) return;
      cfg.servers.push({ name, url: url.replace(/\/$/, '') });
      renderServerList(cfg);
      // 更新默认服务器下拉
      const sel = overlay.querySelector('#ytdl-cfg-default-server');
      const opt = document.createElement('option');
      opt.value = cfg.servers.length - 1;
      opt.textContent = name;
      sel.appendChild(opt);
    };

    // 保存
    overlay.querySelector('#ytdl-save-cfg').onclick = () => {
      cfg.defaultServer = parseInt(overlay.querySelector('#ytdl-cfg-default-server').value);
      cfg.defaultFormat = overlay.querySelector('#ytdl-cfg-format').value;
      cfg.defaultQuality = overlay.querySelector('#ytdl-cfg-quality').value;
      cfg.username = overlay.querySelector('#ytdl-cfg-username').value;
      cfg.password = overlay.querySelector('#ytdl-cfg-password').value;
      saveConfig(cfg);
      overlay.remove();
      showToast('✅ 设置已保存');
    };

    // 取消
    overlay.querySelector('#ytdl-cancel-cfg').onclick = () => overlay.remove();
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

    function renderServerList(cfg) {
      const container = overlay.querySelector('#ytdl-server-list');
      container.innerHTML = cfg.servers.map((s, i) => `
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;padding:6px 8px;background:rgba(255,255,255,0.04);border-radius:8px;">
          <span style="flex:1;font-size:13px;">${s.name} <span style="color:#666;font-size:11px;">${s.url}</span></span>
          <button data-idx="${i}" class="ytdl-del-server" style="background:none;border:none;color:#f87171;cursor:pointer;font-size:14px;">✕</button>
        </div>
      `).join('');
      container.querySelectorAll('.ytdl-del-server').forEach(btn => {
        btn.onclick = () => {
          cfg.servers.splice(parseInt(btn.dataset.idx), 1);
          if (cfg.defaultServer >= cfg.servers.length) cfg.defaultServer = 0;
          renderServerList(cfg);
        };
      });
    }
  }

  // ========== Toast 提示 ==========
  function showToast(msg, duration = 3000) {
    const toast = document.createElement('div');
    toast.style.cssText = `
      position: fixed; bottom: 30px; left: 50%; transform: translateX(-50%);
      background: rgba(20, 20, 30, 0.95); backdrop-filter: blur(10px);
      color: #e0e0e0; padding: 12px 24px; border-radius: 12px;
      font-size: 14px; z-index: 100001; pointer-events: none;
      border: 1px solid rgba(255,255,255,0.1);
      box-shadow: 0 4px 20px rgba(0,0,0,0.4);
      animation: ytdlToastIn 0.3s ease;
    `;
    toast.textContent = msg;
    document.body.appendChild(toast);
    setTimeout(() => {
      toast.style.transition = 'opacity 0.3s';
      toast.style.opacity = '0';
      setTimeout(() => toast.remove(), 300);
    }, duration);
  }

  // ========== 页面内按钮 ==========
  function injectPageButton() {
    if (document.querySelector('.ytdl-page-btn')) return;
    if (!location.pathname.includes('/watch')) return;

    // 找到标题栏的操作按钮区域
    const container = document.querySelector('#above-the-fold #actions, #actions-inner, ytd-menu-renderer.ytd-watch-metadata');
    if (!container) return;

    const btn = document.createElement('button');
    btn.className = 'ytdl-page-btn';
    btn.innerHTML = '⬇️ 推送下载';
    btn.title = '推送到 YouTube Downloader';
    btn.onclick = (e) => {
      e.preventDefault();
      e.stopPropagation();
      openPanel();
    };

    // 尝试插入到 subscribe 按钮旁边
    const subBtn = container.querySelector('ytd-subscribe-button-renderer, #subscribe-button');
    if (subBtn && subBtn.parentNode) {
      subBtn.parentNode.insertBefore(btn, subBtn.nextSibling);
    } else {
      container.appendChild(btn);
    }
  }

  // ========== 浮窗面板 ==========
  function createPanel() {
    const cfg = getConfig();
    const panel = document.createElement('div');
    panel.id = 'ytdl-panel';
    panel.className = 'ytdl-collapsed';
    panel.innerHTML = `
      <div class="ytdl-header">
        <span>⬇️ YouTube Downloader</span>
        <div class="ytdl-header-btns">
          <button id="ytdl-settings-btn" title="设置">⚙</button>
          <button id="ytdl-minimize-btn" title="最小化">—</button>
        </div>
      </div>
      <div class="ytdl-body">
        <div class="ytdl-row">
          <div class="ytdl-label">目标服务器</div>
          <select class="ytdl-select" id="ytdl-server">
            ${cfg.servers.map((s, i) => `<option value="${i}" ${i === cfg.defaultServer ? 'selected' : ''}>${s.name} — ${s.url}</option>`).join('')}
          </select>
        </div>
        <div class="ytdl-row">
          <div class="ytdl-label">格式</div>
          <select class="ytdl-select" id="ytdl-format">
            <option value="video" ${cfg.defaultFormat === 'video' ? 'selected' : ''}>🎬 视频</option>
            <option value="audio" ${cfg.defaultFormat === 'audio' ? 'selected' : ''}>🎵 仅音频 (MP3)</option>
            <option value="best" ${cfg.defaultFormat === 'best' ? 'selected' : ''}>✨ 最佳画质</option>
          </select>
        </div>
        <div class="ytdl-row">
          <div class="ytdl-label">画质</div>
          <select class="ytdl-select" id="ytdl-quality">
            <option value="best" ${cfg.defaultQuality === 'best' ? 'selected' : ''}>最高可用</option>
            <option value="4320" ${cfg.defaultQuality === '4320' ? 'selected' : ''}>8K (4320p)</option>
            <option value="2160" ${cfg.defaultQuality === '2160' ? 'selected' : ''}>4K (2160p)</option>
            <option value="1080" ${cfg.defaultQuality === '1080' ? 'selected' : ''}>1080p</option>
            <option value="720" ${cfg.defaultQuality === '720' ? 'selected' : ''}>720p</option>
            <option value="480" ${cfg.defaultQuality === '480' ? 'selected' : ''}>480p</option>
          </select>
        </div>
        <div class="ytdl-row" style="font-size:12px;color:#888;word-break:break-all;" id="ytdl-video-info"></div>
        <div class="ytdl-btn-row">
          <button class="ytdl-btn ytdl-btn-primary" id="ytdl-download-btn">⬇️ 下载</button>
        </div>
        <div class="ytdl-status" id="ytdl-status"></div>
      </div>
    `;

    document.body.appendChild(panel);
    updateVideoInfo();

    // 事件绑定
    panel.querySelector('#ytdl-settings-btn').onclick = openSettings;
    panel.querySelector('#ytdl-minimize-btn').onclick = () => panel.classList.add('ytdl-collapsed');
    panel.querySelector('.ytdl-header').onclick = (e) => {
      if (e.target.closest('.ytdl-header-btns')) return;
      panel.classList.toggle('ytdl-collapsed');
    };
    panel.querySelector('#ytdl-download-btn').onclick = handleDownload;

    // 拖拽
    makeDraggable(panel, panel.querySelector('.ytdl-header'));

    return panel;
  }

  function updateVideoInfo() {
    const info = document.querySelector('#ytdl-video-info');
    if (info) {
      info.textContent = getVideoTitle() + ' | ' + getVideoUrl();
    }
  }

  function openPanel() {
    let panel = document.getElementById('ytdl-panel');
    if (!panel) panel = createPanel();
    panel.classList.remove('ytdl-collapsed');
    updateVideoInfo();
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  async function handleDownload() {
    const cfg = getConfig();
    const serverIdx = parseInt(document.getElementById('ytdl-server').value);
    const format = document.getElementById('ytdl-format').value;
    const quality = document.getElementById('ytdl-quality').value;
    const statusEl = document.getElementById('ytdl-status');
    const btn = document.getElementById('ytdl-download-btn');
    const server = cfg.servers[serverIdx];

    if (!server) {
      showStatus(statusEl, 'error', '❌ 请先配置服务器');
      return;
    }

    const title = getVideoTitle();
    btn.classList.add('ytdl-page-btn-loading');
    btn.textContent = '⏳ 推送中...';
    showStatus(statusEl, 'loading', `📤 正在推送到 ${server.name}...`);

    try {
      const result = await pushDownload(server, format, quality);
      showStatus(statusEl, 'success', `✅ 已推送到 ${server.name}\n${title}`);
      showToast(`✅ 已推送: ${title}`);
    } catch (e) {
      showStatus(statusEl, 'error', `❌ 失败: ${e.message}`);
      showToast(`❌ 推送失败: ${e.message}`);
    } finally {
      btn.classList.remove('ytdl-page-btn-loading');
      btn.textContent = '⬇️ 下载';
    }
  }

  // ========== 拖拽 ==========
  function makeDraggable(panel, handle) {
    let isDragging = false, startX, startY, startLeft, startTop;
    handle.addEventListener('mousedown', (e) => {
      if (e.target.closest('button')) return;
      isDragging = true;
      startX = e.clientX;
      startY = e.clientY;
      const rect = panel.getBoundingClientRect();
      startLeft = rect.left;
      startTop = rect.top;
      panel.style.position = 'fixed';
      panel.style.left = startLeft + 'px';
      panel.style.top = startTop + 'px';
      panel.style.right = 'auto';
      e.preventDefault();
    });
    document.addEventListener('mousemove', (e) => {
      if (!isDragging) return;
      panel.style.left = (startLeft + e.clientX - startX) + 'px';
      panel.style.top = (startTop + e.clientY - startY) + 'px';
    });
    document.addEventListener('mouseup', () => { isDragging = false; });
  }

  // ========== YouTube SPA 路由监听 ==========
  let lastUrl = '';
  let urlCheckTimer = null;

  function onUrlChange() {
    // 防抖：YouTube DOM 变化极频繁，300ms 内只触发一次
    if (urlCheckTimer) return;
    urlCheckTimer = setTimeout(() => {
      urlCheckTimer = null;
      if (location.href !== lastUrl) {
        lastUrl = location.href;
        if (location.pathname.includes('/watch')) {
          setTimeout(() => {
            injectPageButton();
            updateVideoInfo();
          }, 800);
        }
      }
    }, 300);
  }

  // 主要依赖 YouTube 自带事件，MutationObserver 仅作备用
  window.addEventListener('yt-navigate-finish', onUrlChange);
  window.addEventListener('popstate', onUrlChange);

  // 轻量级 observer：只监听 title 变化（URL 改变必改 title），不监听整个 body
  const titleEl = document.querySelector('title');
  if (titleEl) {
    new MutationObserver(onUrlChange).observe(titleEl, { childList: true });
  }

  // ========== 菜单命令 ==========
  GM_registerMenuCommand('⚙️ 设置', openSettings);
  GM_registerMenuCommand('⬇️ 打开下载面板', openPanel);

  // ========== 初始化 ==========
  createPanel();
  setTimeout(injectPageButton, 2000);

  // 添加 toast 动画
  const style = document.createElement('style');
  style.textContent = `@keyframes ytdlToastIn { from { opacity: 0; transform: translateX(-50%) translateY(20px); } to { opacity: 1; transform: translateX(-50%) translateY(0); } }`;
  document.head.appendChild(style);

})();
