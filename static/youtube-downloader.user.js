// ==UserScript==
// @name         YouTube Downloader 一键推送
// @namespace    https://github.com/zym20192019/YouTube-Downloader
// @version      1.1.0
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
    defaultFormat: 'video',
    defaultQuality: 'best',
    username: 'admin',
    password: '',
    autoLogin: true,
  };

  // ========== 样式 ==========
  GM_addStyle(`
    /* 右上角小圆钮 */
    #ytdl-fab {
      position: fixed;
      top: 80px;
      right: 20px;
      z-index: 99999;
      width: 44px;
      height: 44px;
      border-radius: 50%;
      background: linear-gradient(135deg, #6366f1, #a855f7);
      border: none;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 20px;
      color: #fff;
      box-shadow: 0 4px 16px rgba(99, 102, 241, 0.4);
      transition: transform 0.2s, box-shadow 0.2s;
    }
    #ytdl-fab:hover {
      transform: scale(1.1);
      box-shadow: 0 6px 24px rgba(99, 102, 241, 0.5);
    }
    #ytdl-fab.ytdl-fab-spin {
      animation: ytdlSpin 1s linear infinite;
    }

    /* 展开菜单 */
    #ytdl-menu {
      position: fixed;
      top: 130px;
      right: 20px;
      z-index: 99998;
      background: rgba(20, 20, 30, 0.95);
      backdrop-filter: blur(20px);
      border: 1px solid rgba(255, 255, 255, 0.12);
      border-radius: 14px;
      padding: 12px 14px;
      min-width: 240px;
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      color: #e0e0e0;
      display: none;
    }
    #ytdl-menu.ytdl-show { display: block; }

    .ytdl-menu-title {
      font-size: 13px;
      font-weight: 600;
      color: #a78bfa;
      margin-bottom: 10px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .ytdl-menu-title button {
      background: none;
      border: none;
      color: #666;
      cursor: pointer;
      font-size: 16px;
      padding: 0 2px;
    }
    .ytdl-menu-title button:hover { color: #aaa; }

    .ytdl-menu-row {
      margin-bottom: 8px;
    }
    .ytdl-menu-label {
      font-size: 10px;
      color: #666;
      margin-bottom: 3px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    .ytdl-menu-select {
      width: 100%;
      background: rgba(255, 255, 255, 0.06);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 8px;
      color: #e0e0e0;
      padding: 6px 8px;
      font-size: 12px;
      outline: none;
      box-sizing: border-box;
    }
    .ytdl-menu-select option {
      background: #1a1a2e;
      color: #e0e0e0;
    }

    .ytdl-menu-actions {
      display: flex;
      gap: 6px;
      margin-top: 10px;
    }
    .ytdl-menu-btn {
      flex: 1;
      padding: 8px 0;
      border: none;
      border-radius: 8px;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 4px;
    }
    .ytdl-menu-btn-primary {
      background: linear-gradient(135deg, #6366f1, #a855f7);
      color: #fff;
    }
    .ytdl-menu-btn-primary:hover {
      transform: translateY(-1px);
      box-shadow: 0 4px 12px rgba(99, 102, 241, 0.4);
    }
    .ytdl-menu-btn-ghost {
      background: rgba(255, 255, 255, 0.06);
      color: #aaa;
      border: 1px solid rgba(255, 255, 255, 0.1);
    }
    .ytdl-menu-btn-ghost:hover {
      background: rgba(255, 255, 255, 0.1);
      color: #fff;
    }

    .ytdl-menu-status {
      margin-top: 8px;
      padding: 6px 8px;
      border-radius: 6px;
      font-size: 11px;
      display: none;
      word-break: break-all;
    }
    .ytdl-menu-status.ytdl-show { display: block; }
    .ytdl-menu-status.ytdl-success {
      background: rgba(34, 197, 94, 0.12);
      color: #4ade80;
    }
    .ytdl-menu-status.ytdl-error {
      background: rgba(239, 68, 68, 0.12);
      color: #f87171;
    }
    .ytdl-menu-status.ytdl-loading {
      background: rgba(99, 102, 241, 0.12);
      color: #a78bfa;
    }

    /* 页面内按钮 */
    .ytdl-page-btn {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 6px 14px;
      margin-left: 8px;
      background: linear-gradient(135deg, #6366f1, #a855f7);
      color: #fff;
      border: none;
      border-radius: 20px;
      font-size: 13px;
      font-weight: 500;
      cursor: pointer;
      transition: all 0.2s;
      vertical-align: middle;
    }
    .ytdl-page-btn:hover {
      transform: scale(1.05);
      box-shadow: 0 4px 16px rgba(99, 102, 241, 0.4);
    }
    .ytdl-page-btn.ytdl-loading {
      opacity: 0.6;
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
      width: 400px;
      max-width: 90vw;
      max-height: 80vh;
      overflow-y: auto;
      color: #e0e0e0;
    }
    .ytdl-settings-box h3 {
      margin: 0 0 16px 0;
      color: #a78bfa;
      font-size: 15px;
    }
    .ytdl-settings-box .ytdl-row { margin-bottom: 12px; }
    .ytdl-settings-box .ytdl-label {
      font-size: 11px;
      color: #888;
      margin-bottom: 4px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    .ytdl-settings-box .ytdl-input,
    .ytdl-settings-box .ytdl-select {
      width: 100%;
      background: rgba(255, 255, 255, 0.06);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 8px;
      color: #e0e0e0;
      padding: 8px 10px;
      font-size: 13px;
      outline: none;
      box-sizing: border-box;
    }
    .ytdl-settings-actions {
      display: flex;
      gap: 8px;
      margin-top: 16px;
    }
  `);

  // ========== 工具函数 ==========
  function getConfig() {
    const saved = GM_getValue('ytdl_config', null);
    return saved ? { ...DEFAULT_CONFIG, ...saved } : { ...DEFAULT_CONFIG };
  }
  function saveConfig(cfg) { GM_setValue('ytdl_config', cfg); }
  function getTokenKey(url) { return 'ytdl_token_' + btoa(url).replace(/=/g, ''); }
  function getToken(url) { return GM_getValue(getTokenKey(url), ''); }
  function setToken(url, token) { GM_setValue(getTokenKey(url), token); }
  function getVideoUrl() { return window.location.href.split('&')[0]; }
  function getVideoTitle() {
    const el = document.querySelector('h1.ytd-watch-metadata yt-formatted-string, h1.title yt-formatted-string, #title h1');
    return el ? el.textContent.trim() : document.title.replace(' - YouTube', '');
  }

  function gmFetch(opts) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({ ...opts, onload: resolve, onerror: reject });
    });
  }

  // ========== 登录 ==========
  async function ensureLogin(server) {
    const cfg = getConfig();
    const token = getToken(server.url);
    if (token) return token;
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
      } catch (e) {}
    }
    throw new Error('未登录，请在设置中配置密码');
  }

  // ========== 推送下载 ==========
  async function pushDownload(server, format, quality) {
    const url = getVideoUrl();
    const token = await ensureLogin(server);
    const res = await gmFetch({
      method: 'POST',
      url: server.url + '/api/download',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token },
      data: JSON.stringify({ url, format, quality }),
    });
    if (res.status === 401) {
      setToken(server.url, '');
      const newToken = await ensureLogin(server);
      const retry = await gmFetch({
        method: 'POST',
        url: server.url + '/api/download',
        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + newToken },
        data: JSON.stringify({ url, format, quality }),
      });
      return JSON.parse(retry.responseText);
    }
    if (res.status !== 200) throw new Error(JSON.parse(res.responseText).detail || 'HTTP ' + res.status);
    return JSON.parse(res.responseText);
  }

  // ========== Toast ==========
  function showToast(msg, ms = 3000) {
    const t = document.createElement('div');
    t.style.cssText = `
      position:fixed;bottom:30px;left:50%;transform:translateX(-50%);
      background:rgba(20,20,30,0.95);backdrop-filter:blur(10px);
      color:#e0e0e0;padding:10px 20px;border-radius:10px;
      font-size:13px;z-index:100001;pointer-events:none;
      border:1px solid rgba(255,255,255,0.1);box-shadow:0 4px 20px rgba(0,0,0,0.4);
    `;
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => { t.style.transition = 'opacity 0.3s'; t.style.opacity = '0'; setTimeout(() => t.remove(), 300); }, ms);
  }

  // ========== 一键推送（页面按钮用） ==========
  async function quickPush(btn) {
    const cfg = getConfig();
    const server = cfg.servers[cfg.defaultServer];
    if (!server) { showToast('❌ 请先配置服务器'); return; }
    const title = getVideoTitle();
    btn.classList.add('ytdl-loading');
    btn.textContent = '⏳';
    try {
      await pushDownload(server, cfg.defaultFormat, cfg.defaultQuality);
      showToast(`✅ 已推送到 ${server.name}: ${title}`);
      btn.textContent = '✅';
      setTimeout(() => { btn.textContent = '⬇️'; btn.classList.remove('ytdl-loading'); }, 2000);
    } catch (e) {
      showToast(`❌ ${e.message}`);
      btn.textContent = '❌';
      setTimeout(() => { btn.textContent = '⬇️'; btn.classList.remove('ytdl-loading'); }, 2000);
    }
  }

  // ========== 页面内按钮 ==========
  function injectPageButton() {
    if (document.querySelector('.ytdl-page-btn')) return;
    if (!location.pathname.includes('/watch')) return;
    const container = document.querySelector('#above-the-fold #actions, #actions-inner, ytd-menu-renderer.ytd-watch-metadata');
    if (!container) return;
    const btn = document.createElement('button');
    btn.className = 'ytdl-page-btn';
    btn.innerHTML = '⬇️';
    btn.title = '一键推送到 YouTube Downloader';
    btn.onclick = (e) => { e.preventDefault(); e.stopPropagation(); quickPush(btn); };
    const subBtn = container.querySelector('ytd-subscribe-button-renderer, #subscribe-button');
    if (subBtn && subBtn.parentNode) subBtn.parentNode.insertBefore(btn, subBtn.nextSibling);
    else container.appendChild(btn);
  }

  // ========== 右上角浮动按钮 + 菜单 ==========
  function createFAB() {
    // 小圆钮
    const fab = document.createElement('button');
    fab.id = 'ytdl-fab';
    fab.textContent = '⬇';
    fab.title = 'YouTube Downloader';
    document.body.appendChild(fab);

    // 菜单
    const menu = document.createElement('div');
    menu.id = 'ytdl-menu';
    const cfg = getConfig();
    menu.innerHTML = buildMenuHTML(cfg);
    document.body.appendChild(menu);

    // 点击钮展开/收起
    fab.onclick = (e) => {
      e.stopPropagation();
      menu.classList.toggle('ytdl-show');
      // 刷新视频信息
      const info = menu.querySelector('.ytdl-video-info');
      if (info) info.textContent = getVideoTitle();
    };

    // 点击外部关闭
    document.addEventListener('click', (e) => {
      if (!menu.contains(e.target) && e.target !== fab) menu.classList.remove('ytdl-show');
    });

    // 菜单内事件
    bindMenuEvents(menu, fab);

    // 拖拽
    makeDraggable(fab, menu);
  }

  function buildMenuHTML(cfg) {
    return `
      <div class="ytdl-menu-title">
        <span>⬇️ Downloader</span>
        <div>
          <button id="ytdl-cog" title="设置">⚙</button>
        </div>
      </div>
      <div class="ytdl-menu-row">
        <div class="ytdl-menu-label">服务器</div>
        <select class="ytdl-menu-select" id="ytdl-server">
          ${cfg.servers.map((s, i) => `<option value="${i}" ${i === cfg.defaultServer ? 'selected' : ''}>${s.name}</option>`).join('')}
        </select>
      </div>
      <div class="ytdl-menu-row">
        <div class="ytdl-menu-label">格式</div>
        <select class="ytdl-menu-select" id="ytdl-format">
          <option value="video" ${cfg.defaultFormat === 'video' ? 'selected' : ''}>🎬 视频</option>
          <option value="audio" ${cfg.defaultFormat === 'audio' ? 'selected' : ''}>🎵 音频</option>
          <option value="best" ${cfg.defaultFormat === 'best' ? 'selected' : ''}>✨ 最佳</option>
        </select>
      </div>
      <div class="ytdl-menu-row">
        <div class="ytdl-menu-label">画质</div>
        <select class="ytdl-menu-select" id="ytdl-quality">
          <option value="best" ${cfg.defaultQuality === 'best' ? 'selected' : ''}>最高</option>
          <option value="4320" ${cfg.defaultQuality === '4320' ? 'selected' : ''}>8K</option>
          <option value="2160" ${cfg.defaultQuality === '2160' ? 'selected' : ''}>4K</option>
          <option value="1080" ${cfg.defaultQuality === '1080' ? 'selected' : ''}>1080p</option>
          <option value="720" ${cfg.defaultQuality === '720' ? 'selected' : ''}>720p</option>
        </select>
      </div>
      <div class="ytdl-video-info" style="font-size:10px;color:#555;margin-top:4px;word-break:break-all;"></div>
      <div class="ytdl-menu-actions">
        <button class="ytdl-menu-btn ytdl-menu-btn-primary" id="ytdl-push">⬇ 推送</button>
        <button class="ytdl-menu-btn ytdl-menu-btn-ghost" id="ytdl-web">🌐 网页</button>
      </div>
      <div class="ytdl-menu-status" id="ytdl-status"></div>
    `;
  }

  function bindMenuEvents(menu, fab) {
    menu.querySelector('#ytdl-cog').onclick = () => openSettings();
    menu.querySelector('#ytdl-push').onclick = async () => {
      const cfg = getConfig();
      const si = parseInt(menu.querySelector('#ytdl-server').value);
      const fmt = menu.querySelector('#ytdl-format').value;
      const qlt = menu.querySelector('#ytdl-quality').value;
      const server = cfg.servers[si];
      const statusEl = menu.querySelector('#ytdl-status');
      const btn = menu.querySelector('#ytdl-push');
      if (!server) { statusEl.className = 'ytdl-menu-status ytdl-show ytdl-error'; statusEl.textContent = '❌ 无服务器'; return; }
      btn.textContent = '⏳'; btn.style.pointerEvents = 'none';
      statusEl.className = 'ytdl-menu-status ytdl-show ytdl-loading';
      statusEl.textContent = `📤 → ${server.name}...`;
      try {
        await pushDownload(server, fmt, qlt);
        statusEl.className = 'ytdl-menu-status ytdl-show ytdl-success';
        statusEl.textContent = `✅ 已推送`;
        showToast(`✅ ${getVideoTitle()}`);
      } catch (e) {
        statusEl.className = 'ytdl-menu-status ytdl-show ytdl-error';
        statusEl.textContent = `❌ ${e.message}`;
      }
      btn.textContent = '⬇ 推送'; btn.style.pointerEvents = '';
    };
    menu.querySelector('#ytdl-web').onclick = () => {
      const cfg = getConfig();
      const server = cfg.servers[parseInt(menu.querySelector('#ytdl-server').value)];
      if (server) window.open(server.url, '_blank');
    };
  }

  // ========== 设置弹窗 ==========
  function openSettings() {
    const cfg = getConfig();
    const overlay = document.createElement('div');
    overlay.className = 'ytdl-settings-overlay';
    overlay.innerHTML = `
      <div class="ytdl-settings-box">
        <h3>⚙️ 设置</h3>
        <div class="ytdl-row">
          <div class="ytdl-label">服务器</div>
          <div id="ytdl-srv-list"></div>
          <button class="ytdl-menu-btn ytdl-menu-btn-ghost" style="margin-top:6px;padding:5px 10px;font-size:11px;" id="ytdl-add-srv">+ 添加</button>
        </div>
        <div class="ytdl-row">
          <div class="ytdl-label">默认服务器</div>
          <select class="ytdl-select" id="ytdl-cfg-def-srv">${cfg.servers.map((s, i) => `<option value="${i}" ${i === cfg.defaultServer ? 'selected' : ''}>${s.name}</option>`).join('')}</select>
        </div>
        <div class="ytdl-row">
          <div class="ytdl-label">默认格式</div>
          <select class="ytdl-select" id="ytdl-cfg-fmt">
            <option value="video" ${cfg.defaultFormat === 'video' ? 'selected' : ''}>视频</option>
            <option value="audio" ${cfg.defaultFormat === 'audio' ? 'selected' : ''}>音频</option>
            <option value="best" ${cfg.defaultFormat === 'best' ? 'selected' : ''}>最佳</option>
          </select>
        </div>
        <div class="ytdl-row">
          <div class="ytdl-label">默认画质</div>
          <select class="ytdl-select" id="ytdl-cfg-qlt">
            <option value="best" ${cfg.defaultQuality === 'best' ? 'selected' : ''}>最高</option>
            <option value="4320" ${cfg.defaultQuality === '4320' ? 'selected' : ''}>8K</option>
            <option value="2160" ${cfg.defaultQuality === '2160' ? 'selected' : ''}>4K</option>
            <option value="1080" ${cfg.defaultQuality === '1080' ? 'selected' : ''}>1080p</option>
            <option value="720" ${cfg.defaultQuality === '720' ? 'selected' : ''}>720p</option>
          </select>
        </div>
        <div class="ytdl-row">
          <div class="ytdl-label">用户名</div>
          <input class="ytdl-input" id="ytdl-cfg-user" value="${cfg.username}">
        </div>
        <div class="ytdl-row">
          <div class="ytdl-label">密码</div>
          <input class="ytdl-input" id="ytdl-cfg-pass" type="password" value="${cfg.password}" placeholder="自动登录用">
        </div>
        <div class="ytdl-settings-actions">
          <button class="ytdl-menu-btn ytdl-menu-btn-primary" id="ytdl-save">💾 保存</button>
          <button class="ytdl-menu-btn ytdl-menu-btn-ghost" id="ytdl-cancel">取消</button>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);
    renderSrvList(cfg, overlay);

    overlay.querySelector('#ytdl-add-srv').onclick = () => {
      const name = prompt('名称'); if (!name) return;
      const url = prompt('地址 (如 http://1.2.3.4:8080)'); if (!url) return;
      cfg.servers.push({ name, url: url.replace(/\/$/, '') });
      renderSrvList(cfg, overlay);
      const sel = overlay.querySelector('#ytdl-cfg-def-srv');
      const o = document.createElement('option'); o.value = cfg.servers.length - 1; o.textContent = name; sel.appendChild(o);
    };
    overlay.querySelector('#ytdl-save').onclick = () => {
      cfg.defaultServer = parseInt(overlay.querySelector('#ytdl-cfg-def-srv').value);
      cfg.defaultFormat = overlay.querySelector('#ytdl-cfg-fmt').value;
      cfg.defaultQuality = overlay.querySelector('#ytdl-cfg-qlt').value;
      cfg.username = overlay.querySelector('#ytdl-cfg-user').value;
      cfg.password = overlay.querySelector('#ytdl-cfg-pass').value;
      saveConfig(cfg);
      overlay.remove();
      // 刷新菜单
      const menu = document.getElementById('ytdl-menu');
      if (menu) menu.innerHTML = buildMenuHTML(cfg);
      bindMenuEvents(menu, document.getElementById('ytdl-fab'));
      showToast('✅ 已保存');
    };
    overlay.querySelector('#ytdl-cancel').onclick = () => overlay.remove();
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

    function renderSrvList(cfg, overlay) {
      const c = overlay.querySelector('#ytdl-srv-list');
      c.innerHTML = cfg.servers.map((s, i) => `
        <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;padding:5px 8px;background:rgba(255,255,255,0.04);border-radius:6px;">
          <span style="flex:1;font-size:12px;">${s.name} <span style="color:#555;font-size:10px;">${s.url}</span></span>
          <button data-i="${i}" class="ytdl-del" style="background:none;border:none;color:#f87171;cursor:pointer;">✕</button>
        </div>
      `).join('');
      c.querySelectorAll('.ytdl-del').forEach(b => b.onclick = () => {
        cfg.servers.splice(parseInt(b.dataset.i), 1);
        if (cfg.defaultServer >= cfg.servers.length) cfg.defaultServer = 0;
        renderSrvList(cfg, overlay);
      });
    }
  }

  // ========== 拖拽（同时移动 fab 和 menu） ==========
  function makeDraggable(fab, menu) {
    let dragging = false, sx, sy, fx, fy;
    fab.addEventListener('mousedown', (e) => {
      dragging = true; sx = e.clientX; sy = e.clientY;
      const r = fab.getBoundingClientRect(); fx = r.left; fy = r.top;
      fab.style.right = 'auto'; fab.style.left = fx + 'px'; fab.style.top = fy + 'px';
      e.preventDefault();
    });
    document.addEventListener('mousemove', (e) => {
      if (!dragging) return;
      const dx = e.clientX - sx, dy = e.clientY - sy;
      fab.style.left = (fx + dx) + 'px'; fab.style.top = (fy + dy) + 'px';
      menu.style.right = 'auto';
      menu.style.left = (fx + dx - 240 + 44) + 'px';
      menu.style.top = (fy + dy + 50) + 'px';
    });
    document.addEventListener('mouseup', () => { dragging = false; });
  }

  // ========== URL 变化监听 ==========
  let lastUrl = '';
  let urlTimer = null;
  function onUrlChange() {
    if (urlTimer) return;
    urlTimer = setTimeout(() => {
      urlTimer = null;
      if (location.href !== lastUrl) {
        lastUrl = location.href;
        if (location.pathname.includes('/watch')) {
          setTimeout(injectPageButton, 800);
        }
      }
    }, 300);
  }
  window.addEventListener('yt-navigate-finish', onUrlChange);
  window.addEventListener('popstate', onUrlChange);
  const titleEl = document.querySelector('title');
  if (titleEl) new MutationObserver(onUrlChange).observe(titleEl, { childList: true });

  // ========== 菜单 ==========
  GM_registerMenuCommand('⚙️ 设置', openSettings);

  // ========== 初始化 ==========
  createFAB();
  setTimeout(injectPageButton, 2000);

})();
