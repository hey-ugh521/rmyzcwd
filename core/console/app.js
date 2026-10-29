/* Möbius Console —— 原生 JS 单页应用（无构建步骤、无 CDN 依赖）
 * 契约：mobius/docs/ARCHITECTURE.md §4
 * 所有请求同源 /api/*；任何失败都在页面内可见（toast + 内联错误）。
 */
(function () {
  'use strict';

  /* ============================================================
   * 常量
   * ============================================================ */

  var API_BASE = '/api';
  var STATUS_POLL_MS = 8000;
  var CHROME_POLL_MS = 12000;
  var PENDING_POLL_MS = 60000;

  var VIEW_META = {
    overview: { title: '概览', crumb: '概览' },
    tasks: { title: '任务', crumb: '任务' },
    characters: { title: '角色', crumb: '角色' },
    memory: { title: '记忆', crumb: '记忆' },
    audit: { title: '审计', crumb: '审计' },
    settings: { title: '设置', crumb: '设置' }
  };

  var SERVICE_META = {
    comfyui: { name: 'ComfyUI', port: 8188, short: 'GPU' },
    gateway: { name: 'Media Gateway', port: 8899, short: '网关' },
    kokoro: { name: 'Kokoro TTS', port: 7870, short: '语音' },
    f5: { name: 'F5 克隆', port: 7871, short: '克隆' }
  };

  var MODE_META = {
    off: { name: '关闭', desc: '所有媒体入口禁用' },
    on_demand: { name: '按需', desc: '请求时唤醒，闲置休眠' },
    prewarm: { name: '预热', desc: '启动即拉起，不休眠' }
  };

  var STATUS_TEXT = {
    queued: '排队',
    running: '运行中',
    succeeded: '成功',
    failed: '失败',
    canceled: '已取消',
    unknown: '未知'
  };

  var KIND_TEXT = { image: '生图', voice: '语音', unknown: '未知' };
  var SOURCE_TEXT = { qq: 'QQ', web: 'Web', console: 'Console' };
  var STATE_TEXT = {
    ready: 'ready · 就绪',
    starting: 'starting · 启动中',
    busy: 'busy · 忙碌',
    down: 'down · 离线',
    disabled: 'disabled · 未启用',
    error: 'error · 异常',
    unknown: 'unknown · 未知'
  };

  /* ============================================================
   * 全局状态
   * ============================================================ */

  var state = {
    view: 'overview',
    timers: [],
    chromeTimer: null,
    clockTimer: null,
    pendingTimer: null,
    status: null,
    statusFailToastShown: false,
    pendingCount: 0,
    tasks: { items: [], filters: { status: '', source: '' } },
    characters: { list: [], raw: null, selected: null },
    memory: { users: [], selected: null, usersFromApi: true, candidates: [] },
    audit: { kind: '', limit: 100, items: [] },
    overview: { sigRecent: null, sigFailed: null, lastUpdated: 0 },
    drawerTaskId: null
  };

  /* ============================================================
   * 基础工具
   * ============================================================ */

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function esc(value) {
    if (value === null || value === undefined) return '';
    return String(value).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function pad2(n) { return (n < 10 ? '0' : '') + n; }

  function toMs(ts) {
    var n = Number(ts);
    if (!n || !isFinite(n)) return 0;
    return n > 1e12 ? n : n * 1000;
  }

  function fmtTs(ts) {
    var ms = toMs(ts);
    if (!ms) return '—';
    var d = new Date(ms);
    return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()) +
      ' ' + pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds());
  }

  function fmtTimeShort(ts) {
    var ms = toMs(ts);
    if (!ms) return '—';
    var d = new Date(ms);
    return pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()) + ' ' + pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds());
  }

  function relTime(ts) {
    var ms = toMs(ts);
    if (!ms) return '—';
    var diff = Math.floor((Date.now() - ms) / 1000);
    if (diff < 0) return '刚刚';
    if (diff < 10) return '刚刚';
    if (diff < 60) return diff + ' 秒前';
    if (diff < 3600) return Math.floor(diff / 60) + ' 分钟前';
    if (diff < 86400) return Math.floor(diff / 3600) + ' 小时前';
    if (diff < 86400 * 7) return Math.floor(diff / 86400) + ' 天前';
    return fmtTimeShort(ts);
  }

  function fmtUptime(sec) {
    var s = Number(sec) || 0;
    if (s <= 0) return '0m';
    var d = Math.floor(s / 86400);
    var h = Math.floor((s % 86400) / 3600);
    var m = Math.floor((s % 3600) / 60);
    if (d > 0) return d + 'd ' + h + 'h';
    if (h > 0) return h + 'h ' + m + 'm';
    return m + 'm ' + Math.floor(s % 60) + 's';
  }

  function fmtSeconds(v) {
    var n = Number(v);
    if (!isFinite(n) || n < 0) return '—';
    if (n < 60) return n.toFixed(1) + 's';
    return Math.floor(n / 60) + 'm ' + Math.round(n % 60) + 's';
  }

  function truncate(text, len) {
    var s = String(text === null || text === undefined ? '' : text);
    return s.length > len ? s.slice(0, len) + '…' : s;
  }

  function asList(payload, keys) {
    if (Array.isArray(payload)) return payload;
    if (!payload || typeof payload !== 'object') return [];
    var candidates = (keys || []).concat(['items', 'data', 'results', 'list']);
    for (var i = 0; i < candidates.length; i++) {
      var v = payload[candidates[i]];
      if (Array.isArray(v)) return v;
    }
    return [];
  }

  function numberOr(value, fallback) {
    var n = Number(value);
    return isFinite(n) ? n : fallback;
  }

  function statusClass(s) {
    var v = String(s || 'unknown').toLowerCase();
    return ['queued', 'running', 'succeeded', 'failed', 'canceled'].indexOf(v) >= 0 ? v : 'canceled';
  }

  function statusText(s) { return STATUS_TEXT[String(s || 'unknown').toLowerCase()] || String(s || '未知'); }
  function kindText(k) { return KIND_TEXT[String(k || '').toLowerCase()] || String(k || '—'); }
  function sourceText(s) { return SOURCE_TEXT[String(s || '').toLowerCase()] || String(s || '—'); }

  function ledClass(s) {
    var v = String(s || 'unknown').toLowerCase();
    if (v === 'disabled') return 'led-down';
    return ['ready', 'starting', 'busy', 'down', 'error'].indexOf(v) >= 0 ? 'led-' + v : 'led-unknown';
  }

  function svcChipClass(s) {
    var v = String(s || 'unknown').toLowerCase();
    if (v === 'ready') return 'chip-active';
    if (v === 'starting' || v === 'busy') return 'chip-warn';
    if (v === 'error') return 'chip-error';
    return 'chip-muted';
  }

  function mediaUrl(url) {
    if (!url) return '';
    return String(url).replace(/host\.docker\.internal/g, '127.0.0.1');
  }

  function taskDuration(t) {
    if (!t) return '—';
    if (t.started_at && t.finished_at) return fmtSeconds((toMs(t.finished_at) - toMs(t.started_at)) / 1000);
    if (t.started_at && !t.finished_at && t.status === 'running') {
      return fmtSeconds(Math.max(0, (Date.now() - toMs(t.started_at)) / 1000)) + '…';
    }
    if (t.result && t.result.seconds !== undefined && t.result.seconds !== null) return fmtSeconds(t.result.seconds);
    return '—';
  }

  function jsonText(v) {
    if (v === null || v === undefined) return 'null';
    if (typeof v === 'string') return v;
    try { return JSON.stringify(v, null, 2); } catch (e) { return String(v); }
  }

  function errorText(err) {
    if (!err) return '—';
    if (typeof err === 'string') return err;
    if (err.message) return String(err.message);
    return jsonText(err);
  }

  function stripHtml(html) {
    var tmp = document.createElement('div');
    tmp.innerHTML = html;
    return tmp.textContent || tmp.innerText || '';
  }

  /* ============================================================
   * Toast / Modal
   * ============================================================ */

  function toast(message, type, ttl) {
    var root = $('#toast-root');
    if (!root) return;
    var kind = type || 'info';
    var icon = kind === 'error' ? '✕' : (kind === 'success' ? '✓' : 'ℹ');
    var node = document.createElement('div');
    node.className = 'toast ' + kind;
    node.innerHTML =
      '<span class="toast-icon">' + icon + '</span>' +
      '<div class="toast-msg">' + esc(message) + '</div>' +
      '<button class="toast-x" type="button" aria-label="关闭">×</button>';
    var remove = function () {
      if (node.parentNode) node.parentNode.removeChild(node);
    };
    node.querySelector('.toast-x').addEventListener('click', remove);
    root.appendChild(node);
    while (root.children.length > 4) root.removeChild(root.firstChild);
    setTimeout(remove, ttl || (kind === 'error' ? 9000 : 4200));
  }

  function confirmDialog(opts) {
    var options = opts || {};
    return new Promise(function (resolve) {
      var root = $('#modal-root');
      root.innerHTML =
        '<div class="modal-overlay" id="modal-overlay">' +
        '<div class="modal" role="dialog" aria-modal="true">' +
        '<div class="modal-head"><h3>' + esc(options.title || '确认操作') + '</h3></div>' +
        '<div class="modal-body">' + (options.html || esc(options.message || '')) + '</div>' +
        '<div class="modal-foot">' +
        '<button type="button" class="btn" data-modal="cancel">' + esc(options.cancelText || '取消') + '</button>' +
        '<button type="button" class="btn ' + (options.danger ? 'danger' : 'primary') + '" data-modal="ok">' + esc(options.okText || '确认') + '</button>' +
        '</div></div></div>';
      var overlay = $('#modal-overlay');
      var onKey = function (e) {
        if (e.key === 'Escape') close(false);
      };
      function close(value) {
        document.removeEventListener('keydown', onKey);
        root.innerHTML = '';
        resolve(value);
      }
      $('#modal-overlay').addEventListener('click', function (e) {
        if (e.target === overlay) close(false);
      });
      $('[data-modal="cancel"]').addEventListener('click', function () { close(false); });
      $('[data-modal="ok"]').addEventListener('click', function () { close(true); });
      document.addEventListener('keydown', onKey);
      var ok = $('[data-modal="ok"]');
      if (ok) ok.focus();
    });
  }

  /* ============================================================
   * HTTP 封装
   * ============================================================ */

  function ApiError(message, status, body, path) {
    this.name = 'ApiError';
    this.message = message;
    this.status = status || 0;
    this.body = body || null;
    this.path = path || '';
  }
  ApiError.prototype = Object.create(Error.prototype);
  ApiError.prototype.constructor = ApiError;

  function api(path, opts) {
    var options = opts || {};
    var url = String(path).indexOf('/api/') === 0 ? String(path) : (API_BASE + path);
    var init = {
      method: options.method || 'GET',
      headers: { 'Accept': 'application/json' }
    };
    if (options.body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(options.body);
    }
    var timeoutMs = options.timeoutMs || 20000;
    var controller = null;
    var timer = null;
    if (typeof AbortController !== 'undefined') {
      controller = new AbortController();
      init.signal = controller.signal;
      timer = setTimeout(function () { controller.abort(); }, timeoutMs);
    }
    return fetch(url, init).then(function (res) {
      return res.text().then(function (text) {
        var data = null;
        if (text) {
          try { data = JSON.parse(text); } catch (e) { data = null; }
        }
        if (!res.ok) {
          var detail = data && (data.message || data.error || data.detail || data.hint);
          var msg = detail ? (typeof detail === 'string' ? detail : jsonText(detail)) : ('HTTP ' + res.status + ' ' + res.statusText);
          throw new ApiError(msg, res.status, data, url);
        }
        return data;
      });
    }, function (err) {
      if (err && err.name === 'AbortError') {
        throw new ApiError('请求超时（' + Math.round(timeoutMs / 1000) + 's）：' + url, 0, null, url);
      }
      throw new ApiError('无法连接 Möbius Core（' + url + '）：' + (err && err.message ? err.message : err), 0, null, url);
    }).then(function (data) {
      if (timer) clearTimeout(timer);
      return data;
    }, function (err) {
      if (timer) clearTimeout(timer);
      throw err;
    });
  }

  /* ============================================================
   * 渲染工具
   * ============================================================ */

  function skeletonRows(n, heights) {
    var out = '';
    for (var i = 0; i < n; i++) {
      var h = heights ? heights[i % heights.length] : 13;
      out += '<div class="sk sk-row" style="height:' + h + 'px;width:' + (45 + ((i * 17) % 50)) + '%"></div>';
    }
    return out;
  }

  function tableSkeleton(rows, cols) {
    var out = '<div class="card-body">';
    for (var i = 0; i < rows; i++) {
      out += '<div class="sk sk-row" style="height:12px;width:' + (38 + ((i * 13) % 55)) + '%"></div>';
    }
    return out + '</div>';
  }

  function errorBoxHTML(err, title, retryLabel) {
    return '<div class="error-box" role="alert">' +
      '<div style="flex:1;min-width:0">' +
      '<div class="err-title">' + esc(title || '请求失败') + '</div>' +
      '<div class="err-msg">' + esc(errorText(err)) + '</div>' +
      '</div>' +
      '<div class="err-actions"><button type="button" class="btn sm" data-retry="1">' + esc(retryLabel || '重试') + '</button></div>' +
      '</div>';
  }

  function bindRetry(container, fn) {
    if (!container) return;
    var btn = container.querySelector('[data-retry]');
    if (btn) btn.addEventListener('click', fn);
  }

  function emptyHTML(title, sub) {
    return '<div class="empty"><div class="empty-title">' + esc(title) + '</div>' +
      (sub ? '<div>' + esc(sub) + '</div>' : '') + '</div>';
  }

  function pageErrorHTML(err) {
    return '<div class="view">' + errorBoxHTML(err, '页面渲染失败', '重试') + '</div>';
  }

  /* ============================================================
   * 定时器管理（切换视图时清空视图级定时器）
   * ============================================================ */

  function addTimer(fn, ms) {
    var id = setInterval(fn, ms);
    state.timers.push(id);
    return id;
  }

  function clearTimers() {
    state.timers.forEach(function (id) { clearInterval(id); });
    state.timers = [];
  }

  /* ============================================================
   * 状态归一化 + 顶栏/侧栏
   * ============================================================ */

  function normService(svc, key) {
    var s = svc || {};
    var meta = SERVICE_META[key] || {};
    return {
      state: String(s.state || 'unknown').toLowerCase(),
      port: s.port !== undefined && s.port !== null ? s.port : meta.port,
      lastCheck: numberOr(s.last_check, 0)
    };
  }

  function normStatus(raw) {
    var data = raw || {};
    var media = data.media || {};
    var services = media.services || {};
    var counters = data.counters || {};
    var hasTodayKeys = Object.prototype.hasOwnProperty.call(counters, 'tasks_today');
    var running = numberOr(counters.running, 0);
    var queued = numberOr(counters.queued, 0);
    var active = media.active_tasks !== undefined && media.active_tasks !== null
      ? numberOr(media.active_tasks, running + queued)
      : running + queued;
    return {
      coreOk: data.core ? data.core.ok !== false : true,
      uptime: numberOr((data.core || {}).uptime_sec, 0),
      mode: media.mode || 'unknown',
      idleSleep: numberOr(media.idle_sleep_sec, 600),
      prewarmOnStart: !!media.prewarm_on_start,
      hasPrewarm: Object.prototype.hasOwnProperty.call(media, 'prewarm_on_start'),
      activeTasks: active,
      runningCount: running,
      queuedCount: queued,
      services: {
        comfyui: normService(services.comfyui, 'comfyui'),
        gateway: normService(services.gateway, 'gateway'),
        kokoro: normService(services.kokoro, 'kokoro'),
        f5: normService(services.f5, 'f5')
      },
      // 契约 §4.3 用 tasks_today/failed_today；实际 Core 用按状态拆分的
      // counters（succeeded/failed/canceled/running/queued/total，均按今日统计）。
      // 两种形态都兼容。
      tasksToday: numberOr(hasTodayKeys ? counters.tasks_today : counters.total, 0),
      failedToday: numberOr(hasTodayKeys ? counters.failed_today : counters.failed, 0),
      hasTodayKeys: hasTodayKeys,
      langbot: data.langbot ? !!data.langbot.reachable : null,
      raw: data
    };
  }

  function paintChrome(st) {
    var chips = $('#chips');
    if (!chips) return;
    var html = '<span class="chip ' + (st.coreOk ? 'chip-active' : 'chip-error') + '">' +
      '<span class="led ' + (st.coreOk ? 'led-ready' : 'led-error') + '"></span>Core</span>';
    Object.keys(SERVICE_META).forEach(function (key) {
      var svc = st.services[key];
      var meta = SERVICE_META[key];
      html += '<span class="chip ' + svcChipClass(svc.state) + '" title="' + esc(meta.name + ' :' + svc.port + ' · ' + svc.state) + '">' +
        '<span class="led ' + ledClass(svc.state) + '"></span>' + esc(meta.short) + '</span>';
    });
    chips.innerHTML = html;

    var coreLed = $('#foot-core-led');
    var coreVal = $('#foot-core');
    if (coreLed) { coreLed.className = 'led ' + (st.coreOk ? 'led-ready' : 'led-error'); }
    if (coreVal) { coreVal.textContent = st.coreOk ? '在线 · ' + fmtUptime(st.uptime) : '离线'; }

    var mediaLed = $('#foot-media-led');
    var modeVal = $('#foot-mode');
    if (mediaLed) {
      var comfy = st.services.comfyui.state;
      mediaLed.className = 'led ' + ledClass(comfy === 'down' && st.mode === 'off' ? 'down' : comfy);
    }
    if (modeVal) {
      var mm = MODE_META[st.mode];
      modeVal.textContent = (mm ? mm.name : st.mode) + ' · ' + String(st.mode);
    }

    var gwLed = $('#foot-gw-led');
    var gwVal = $('#foot-gateway');
    if (gwLed) gwLed.className = 'led ' + ledClass(st.services.gateway.state);
    if (gwVal) gwVal.textContent = st.services.gateway.state;
  }

  function paintChromeOffline() {
    var chips = $('#chips');
    if (chips) {
      chips.innerHTML = '<span class="chip chip-error"><span class="led led-error"></span>Core 连接中断</span>';
    }
    var coreLed = $('#foot-core-led');
    var coreVal = $('#foot-core');
    if (coreLed) coreLed.className = 'led led-error';
    if (coreVal) coreVal.textContent = '离线';
    var mediaLed = $('#foot-media-led');
    if (mediaLed) mediaLed.className = 'led led-unknown';
    var modeVal = $('#foot-mode');
    if (modeVal) modeVal.textContent = '—';
    var gwLed = $('#foot-gw-led');
    if (gwLed) gwLed.className = 'led led-unknown';
    var gwVal = $('#foot-gateway');
    if (gwVal) gwVal.textContent = '—';
  }

  function refreshChrome(silent) {
    return api('/api/status').then(function (data) {
      var st = normStatus(data);
      state.status = st;
      state.statusFailToastShown = false;
      paintChrome(st);
      return st;
    }, function (err) {
      state.status = null;
      paintChromeOffline();
      if (!silent && !state.statusFailToastShown) {
        state.statusFailToastShown = true;
        toast('Core 状态获取失败：' + errorText(err), 'error');
      }
      return null;
    });
  }

  function refreshPendingBadge() {
    return api('/api/memory/candidates?status=pending').then(function (data) {
      var list = asList(data, ['candidates']);
      setPendingBadge(list.length);
      return list;
    }, function () {
      return null;
    });
  }

  function setPendingBadge(count) {
    state.pendingCount = count;
    var badge = $('#nav-badge-memory');
    if (!badge) return;
    if (count > 0) {
      badge.textContent = String(count > 99 ? '99+' : count);
      badge.hidden = false;
    } else {
      badge.hidden = true;
    }
  }

  /* ============================================================
   * 概览
   * ============================================================ */

  function renderOverview(root) {
    root.innerHTML = overviewShell();
    bindOverview();
    return tickOverview(true).then(function () {
      addTimer(function () { tickOverview(false); }, STATUS_POLL_MS);
    });
  }

  function overviewShell() {
    return '' +
      '<div class="view">' +
      '<div class="view-head">' +
      '<div class="vh-main">' +
      '<h2>运行概况 <span class="hint">GET /api/status</span></h2>' +
      '<p class="view-sub" id="ov-sub">正在加载 Core 状态…</p>' +
      '</div>' +
      '<div class="view-actions"><span class="hint" id="ov-updated"></span>' +
      '<button type="button" class="btn sm" id="ov-refresh"><span class="refresh-glyph">↻</span> 刷新</button></div>' +
      '</div>' +
      '<div id="ov-error"></div>' +
      '<div class="stat-row" id="ov-stats">' + statSkeletons() + '</div>' +
      '<div class="grid-2">' +
      '<section class="card media-card">' +
      '<div class="card-head"><h3>媒体总控</h3><span class="card-sub">Core supervisor</span>' +
      '<div class="card-actions"><span class="hint" id="ov-mode-text"></span></div></div>' +
      '<div class="card-body">' +
      '<div class="mode-seg" id="ov-mode">' +
      '<button type="button" class="mode-btn" disabled><span class="m-name">加载中…</span></button>' +
      '</div>' +
      '<div class="media-actions">' +
      '<button type="button" class="btn primary" id="ov-wake">一键唤醒媒体</button>' +
      '<button type="button" class="btn" id="ov-sleep">休眠 ComfyUI</button>' +
      '<span class="hint" id="ov-media-hint"></span>' +
      '</div>' +
      '<div class="media-meta" id="ov-meta"><span class="sk sk-row" style="width:220px;display:inline-block"></span></div>' +
      '</div></section>' +
      '<section class="card">' +
      '<div class="card-head"><h3>服务健康</h3><span class="card-sub">supervisor · 端口</span></div>' +
      '<div class="service-grid" id="ov-services">' + serviceSkeletons() + '</div>' +
      '</section>' +
      '</div>' +
      '<div class="grid-2 uneven">' +
      '<section class="card">' +
      '<div class="card-head"><h3>最近任务</h3><span class="card-sub">最近 10 条</span>' +
      '<div class="card-actions"><a class="btn sm ghost" href="#/tasks">全部任务 →</a></div></div>' +
      '<div class="table-wrap" id="ov-recent">' + tableSkeleton(5) + '</div>' +
      '</section>' +
      '<section class="card">' +
      '<div class="card-head"><h3>失败任务</h3><span class="card-sub">错误原因</span>' +
      '<div class="card-actions"><span class="hint" id="ov-failed-count"></span></div></div>' +
      '<ul class="fail-list" id="ov-failed">' + failSkeletons() + '</ul>' +
      '</section>' +
      '</div>' +
      '</div>';
  }

  function statSkeletons() {
    var out = '';
    for (var i = 0; i < 5; i++) {
      out += '<div class="stat"><div class="sk sk-row" style="width:50%"></div>' +
        '<div class="sk sk-row" style="height:22px;width:36%;margin:8px 0"></div>' +
        '<div class="sk sk-row" style="width:64%"></div></div>';
    }
    return out;
  }

  function serviceSkeletons() {
    var out = '';
    for (var i = 0; i < 4; i++) {
      out += '<div class="svc"><div class="sk sk-row" style="width:60%"></div>' +
        '<div class="sk sk-row" style="width:40%"></div>' +
        '<div class="sk sk-row" style="width:75%"></div></div>';
    }
    return out;
  }

  function failSkeletons() {
    var out = '';
    for (var i = 0; i < 3; i++) {
      out += '<li class="fail-item"><div class="sk sk-row" style="width:42%"></div>' +
        '<div class="sk sk-row" style="width:82%"></div>' +
        '<div class="sk sk-row" style="width:66%"></div></li>';
    }
    return out;
  }

  function bindOverview() {
    var refreshBtn = $('#ov-refresh');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', function () {
        refreshBtn.classList.add('spinning');
        tickOverview(true, true).then(function () { refreshBtn.classList.remove('spinning'); });
      });
    }
    var modeBox = $('#ov-mode');
    if (modeBox) {
      modeBox.addEventListener('click', function (e) {
        var btn = e.target.closest ? e.target.closest('.mode-btn') : null;
        if (!btn || btn.disabled) return;
        setMediaMode(btn.getAttribute('data-mode'));
      });
    }
    var wakeBtn = $('#ov-wake');
    if (wakeBtn) wakeBtn.addEventListener('click', wakeMedia);
    var sleepBtn = $('#ov-sleep');
    if (sleepBtn) sleepBtn.addEventListener('click', sleepMedia);

    var recent = $('#ov-recent');
    if (recent) {
      recent.addEventListener('click', function (e) {
        var row = e.target.closest ? e.target.closest('[data-task-id]') : null;
        if (row) openTaskDetail(row.getAttribute('data-task-id'));
      });
    }
    var failed = $('#ov-failed');
    if (failed) {
      failed.addEventListener('click', function (e) {
        var item = e.target.closest ? e.target.closest('[data-task-id]') : null;
        if (item) openTaskDetail(item.getAttribute('data-task-id'));
      });
    }
  }

  function tickOverview(initial, manual) {
    var statusP = api('/api/status').then(function (data) {
      var st = normStatus(data);
      state.status = st;
      state.statusFailToastShown = false;
      paintChrome(st);
      paintOverviewStatus(st);
      var errBox = $('#ov-error');
      if (errBox) errBox.innerHTML = '';
      return st;
    }, function (err) {
      var errBox = $('#ov-error');
      if (errBox) {
        errBox.innerHTML = errorBoxHTML(err, 'Core 状态获取失败', '重试');
        bindRetry(errBox, function () { tickOverview(true, true); });
      }
      paintChromeOffline();
      if (!state.statusFailToastShown) {
        state.statusFailToastShown = true;
        toast('无法获取 /api/status：' + errorText(err), 'error');
      }
      return null;
    });

    var recentP = api('/api/tasks?limit=10').then(function (data) {
      paintRecentTasks(normTasks(data));
    }, function (err) {
      state.overview.sigRecent = null;
      var box = $('#ov-recent');
      if (box) {
        box.innerHTML = errorBoxHTML(err, '最近任务加载失败', '重试');
        bindRetry(box, function () { tickOverview(false, true); });
      }
    });

    var failedP = api('/api/tasks?limit=20&status=failed').then(function (data) {
      paintFailedTasks(normTasks(data));
    }, function (err) {
      state.overview.sigFailed = null;
      var box = $('#ov-failed');
      if (box) {
        box.innerHTML = '<li>' + errorBoxHTML(err, '失败任务加载失败', '重试') + '</li>';
        bindRetry(box, function () { tickOverview(false, true); });
      }
    });

    return Promise.all([statusP, recentP, failedP]).then(function () {
      var upd = $('#ov-updated');
      if (upd) upd.textContent = '最后更新 ' + new Date().toLocaleTimeString('zh-CN', { hour12: false });
      if (manual) toast('概览已刷新', 'info', 1800);
    });
  }

  function paintOverviewStatus(st) {
    var stats = $('#ov-stats');
    if (stats) {
      var tasksFoot = st.hasTodayKeys
        ? 'counters.tasks_today'
        : ('counters.total（今日）· 运行 ' + st.runningCount + ' · 排队 ' + st.queuedCount);
      var failedFoot = st.hasTodayKeys ? 'counters.failed_today' : 'counters.failed（今日）';
      stats.innerHTML = '' +
        statCard('今日任务', String(st.tasksToday), tasksFoot, 'stat-blue') +
        statCard('今日失败', String(st.failedToday), failedFoot, st.failedToday > 0 ? 'stat-red' : 'stat-green') +
        statCard('活跃任务', String(st.activeTasks),
          'media.active_tasks' + (st.runningCount || st.queuedCount ? ' · 运行 ' + st.runningCount + ' / 排队 ' + st.queuedCount : ''),
          'stat-amber') +
        statCard('Core 运行时长', fmtUptime(st.uptime), 'core.uptime_sec', 'stat-green') +
        statCard('LangBot', st.langbot === null ? '未知' : (st.langbot ? '可达' : '不可达'),
          'langbot.reachable', st.langbot === false ? 'stat-red' : 'stat-blue');
    }

    var sub = $('#ov-sub');
    if (sub) {
      sub.textContent = '媒体模式 ' + st.mode + ' · ' + (MODE_META[st.mode] ? MODE_META[st.mode].name : '未知') +
        ' · 活跃任务 ' + st.activeTasks + ' · Core ' + (st.coreOk ? '在线' : '异常');
    }

    var modeBox = $('#ov-mode');
    if (modeBox) {
      var html = '';
      Object.keys(MODE_META).forEach(function (key) {
        var meta = MODE_META[key];
        html += '<button type="button" class="mode-btn' + (key === st.mode ? ' active' : '') + '" data-mode="' + esc(key) + '">' +
          '<span class="m-name"><span class="led ' + (key === st.mode ? 'led-ready' : 'led-unknown') + '"></span>' + esc(meta.name) +
          '<span class="mono small dim" style="font-weight:400">' + esc(key) + '</span></span>' +
          '<span class="m-desc">' + esc(meta.desc) + '</span></button>';
      });
      modeBox.innerHTML = html;
    }
    var modeText = $('#ov-mode-text');
    if (modeText) modeText.textContent = st.mode;

    var hint = $('#ov-media-hint');
    if (hint) {
      hint.textContent = st.mode === 'off'
        ? '媒体入口已禁用，唤醒会被拒绝（409 media_disabled）'
        : (st.mode === 'on_demand' ? '按需唤醒 · 闲置 ' + st.idleSleep + 's 后休眠' : '预热模式 · 服务常驻');
    }

    var meta = $('#ov-meta');
    if (meta) {
      meta.innerHTML = '' +
        '<span>闲置休眠 <b>' + esc(String(st.idleSleep)) + 's</b></span>' +
        '<span>活跃任务 <b>' + esc(String(st.activeTasks)) + '</b></span>' +
        '<span>ComfyUI <b>' + esc(st.services.comfyui.state) + '</b></span>' +
        '<span>Gateway <b>' + esc(st.services.gateway.state) + '</b></span>' +
        '<span>Kokoro <b>' + esc(st.services.kokoro.state) + '</b></span>' +
        '<span>F5 <b>' + esc(st.services.f5.state) + '</b></span>';
    }

    var services = $('#ov-services');
    if (services) {
      var svcHtml = '';
      Object.keys(SERVICE_META).forEach(function (key) {
        var meta2 = SERVICE_META[key];
        var svc = st.services[key];
        svcHtml += '<div class="svc">' +
          '<div class="svc-head"><span class="led ' + ledClass(svc.state) + '"></span>' +
          '<span class="svc-name">' + esc(meta2.name) + '</span>' +
          '<span class="svc-port">:' + esc(String(svc.port)) + '</span></div>' +
          '<div class="svc-state state-' + esc(svc.state) + '">' + esc(STATE_TEXT[svc.state] || svc.state) + '</div>' +
          '<div class="svc-check">' + (svc.lastCheck ? '最后检查 ' + relTime(svc.lastCheck) : '暂无检查记录') + '</div>' +
          '</div>';
      });
      services.innerHTML = svcHtml;
    }
  }

  function statCard(label, value, foot, cls) {
    return '<div class="stat ' + esc(cls || '') + '">' +
      '<div class="stat-label">' + esc(label) + '</div>' +
      '<div class="stat-value">' + esc(value) + '</div>' +
      '<div class="stat-foot">' + esc(foot) + '</div>' +
      '</div>';
  }

  function normTasks(payload) {
    var list = asList(payload, ['tasks']);
    return list.filter(function (t) { return t && typeof t === 'object'; }).map(function (t) {
      return {
        raw: t,
        id: String(t.id === undefined || t.id === null ? '' : t.id),
        kind: String(t.kind || 'unknown').toLowerCase(),
        status: String(t.status || 'unknown').toLowerCase(),
        source: t.source || '',
        user_id: t.user_id || '',
        session_id: t.session_id || '',
        character: t.character || '',
        prompt: t.prompt || '',
        params: t.params,
        result: t.result || null,
        error: t.error,
        remote_id: t.remote_id || '',
        created_at: numberOr(t.created_at, 0),
        started_at: numberOr(t.started_at, 0),
        finished_at: numberOr(t.finished_at, 0)
      };
    });
  }

  function paintRecentTasks(tasks) {
    var box = $('#ov-recent');
    if (!box) return;
    var sig = tasks.map(function (t) { return t.id + ':' + t.status; }).join('|');
    if (sig === state.overview.sigRecent) return;
    state.overview.sigRecent = sig;
    var list = tasks.slice().sort(function (a, b) { return toMs(b.created_at) - toMs(a.created_at); }).slice(0, 10);
    if (!list.length) {
      box.innerHTML = emptyHTML('暂无任务', 'QQ / Web / Console 产生的任务会出现在这里');
      return;
    }
    var rows = list.map(function (t) {
      return '<tr class="clickable" data-task-id="' + esc(t.id) + '" title="' + esc(t.prompt || '') + '">' +
        '<td class="cell-id">' + esc(t.id) + '</td>' +
        '<td><span class="badge badge-kind">' + esc(kindText(t.kind)) + '</span></td>' +
        '<td><span class="badge badge-' + statusClass(t.status) + '">' + esc(statusText(t.status)) + '</span></td>' +
        '<td>' + esc(sourceText(t.source)) + '</td>' +
        '<td class="cell-num">' + esc(fmtTimeShort(t.created_at)) + '</td>' +
        '<td class="cell-num">' + esc(taskDuration(t)) + '</td>' +
        '</tr>';
    }).join('');
    box.innerHTML = '<table class="table"><thead><tr>' +
      '<th>任务</th><th>类型</th><th>状态</th><th>来源</th><th>创建</th><th>耗时</th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table>';
  }

  function paintFailedTasks(tasks) {
    var box = $('#ov-failed');
    if (!box) return;
    var failed = tasks.filter(function (t) { return t.status === 'failed'; });
    var sig = failed.map(function (t) { return t.id; }).join('|');
    if (sig === state.overview.sigFailed) return;
    state.overview.sigFailed = sig;
    var countEl = $('#ov-failed-count');
    if (countEl) countEl.textContent = failed.length ? failed.length + ' 条' : '';
    if (!failed.length) {
      box.innerHTML = emptyHTML('近期无失败任务', '错误会显示在任务详情与审计中');
      return;
    }
    box.innerHTML = failed.slice(0, 20).map(function (t) {
      return '<li class="fail-item" data-task-id="' + esc(t.id) + '" style="cursor:pointer">' +
        '<div class="fail-head"><span class="badge badge-failed">' + esc(t.id) + '</span>' +
        '<span class="badge badge-kind">' + esc(kindText(t.kind)) + '</span>' +
        '<span class="fail-time">' + esc(relTime(t.created_at)) + '</span></div>' +
        '<div class="fail-prompt">' + esc(t.prompt || '（无 prompt）') + '</div>' +
        '<div class="fail-err">' + esc(errorText(t.error)) + '</div>' +
        '</li>';
    }).join('');
  }

  /* ============================================================
   * 媒体操作
   * ============================================================ */

  function setMediaMode(mode) {
    if (!MODE_META[mode]) return;
    var current = state.status ? state.status.mode : null;
    if (mode === current) {
      toast('当前已经是「' + MODE_META[mode].name + '」模式', 'info', 2000);
      return;
    }
    var box = $('#ov-mode');
    if (box) $$('.mode-btn', box).forEach(function (b) { b.disabled = true; });
    api('/api/media/mode', { method: 'PUT', body: { mode: mode } }).then(function () {
      toast('媒体模式已切换为「' + MODE_META[mode].name + '」', 'success');
      return refreshChrome(true).then(function (st) { if (st) paintOverviewStatus(st); });
    }, function (err) {
      toast('切换媒体模式失败：' + errorText(err), 'error');
      return refreshChrome(true).then(function (st) { if (st) paintOverviewStatus(st); });
    }).then(function () {
      if (box) $$('.mode-btn', box).forEach(function (b) { b.disabled = false; });
    });
  }

  function wakeMedia() {
    var mode = state.status ? state.status.mode : null;
    if (mode === 'off') {
      toast('媒体模式为「关闭（off）」，唤醒会被 Core 拒绝（409 media_disabled）。请先切换模式。', 'error');
      return;
    }
    var btn = $('#ov-wake');
    var old = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = '唤醒中…（最多 240s）'; }
    var hint = $('#ov-media-hint');
    if (hint) hint.textContent = '正在启动媒体服务，等待健康检查通过…';
    api('/api/media/wake', { method: 'POST', timeoutMs: 250000 }).then(function (data) {
      var ok = !data || data.ok !== false;
      toast(ok ? '媒体服务已唤醒（' + ((data && data.state) || 'ready') + '）' : '唤醒返回失败：' + jsonText(data), ok ? 'success' : 'error');
    }, function (err) {
      if (err && err.status === 409) {
        toast('Core 拒绝唤醒：media_disabled（当前模式为 off）', 'error');
      } else {
        toast('唤醒失败：' + errorText(err), 'error');
      }
    }).then(function () {
      if (btn) { btn.disabled = false; btn.textContent = old; }
      return refreshChrome(true).then(function (st) { if (st) paintOverviewStatus(st); });
    });
  }

  function sleepMedia() {
    var btn = $('#ov-sleep');
    var old = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = '休眠中…'; }
    api('/api/media/sleep', { method: 'POST', timeoutMs: 60000 }).then(function (data) {
      toast('已发送休眠指令（ComfyUI 停止，Core / Gateway 不动）', 'success');
      return data;
    }, function (err) {
      toast('休眠失败：' + errorText(err), 'error');
    }).then(function () {
      if (btn) { btn.disabled = false; btn.textContent = old; }
      return refreshChrome(true).then(function (st) { if (st) paintOverviewStatus(st); });
    });
  }

  /* ============================================================
   * 任务视图
   * ============================================================ */

  function renderTasks(root) {
    root.innerHTML = tasksShell();
    bindTasks();
    return loadTasks();
  }

  function tasksShell() {
    var statusOptions = [
      ['', '全部状态'], ['queued', 'queued · 排队'], ['running', 'running · 运行中'],
      ['succeeded', 'succeeded · 成功'], ['failed', 'failed · 失败'], ['canceled', 'canceled · 已取消']
    ].map(function (pair) {
      return '<option value="' + esc(pair[0]) + '"' + (state.tasks.filters.status === pair[0] ? ' selected' : '') + '>' + esc(pair[1]) + '</option>';
    }).join('');
    var sourceOptions = [
      ['', '全部来源'], ['qq', 'qq'], ['web', 'web'], ['console', 'console']
    ].map(function (pair) {
      return '<option value="' + esc(pair[0]) + '"' + (state.tasks.filters.source === pair[0] ? ' selected' : '') + '>' + esc(pair[1]) + '</option>';
    }).join('');

    return '' +
      '<div class="view">' +
      '<div class="view-head">' +
      '<div class="vh-main"><h2>任务中心 <span class="hint">GET /api/tasks</span></h2>' +
      '<p class="view-sub">统一任务 ID（生图 / 语音）· 状态、参数、产物与错误</p></div>' +
      '<div class="view-actions"><button type="button" class="btn sm" id="tasks-refresh"><span class="refresh-glyph">↻</span> 刷新</button></div>' +
      '</div>' +
      '<div class="card toolbar">' +
      '<div class="filters">' +
      '<label class="hint" for="f-status">状态</label>' +
      '<select class="input" id="f-status">' + statusOptions + '</select>' +
      '<label class="hint" for="f-source">来源</label>' +
      '<select class="input" id="f-source">' + sourceOptions + '</select>' +
      '</div>' +
      '<div class="result-count" id="tasks-count">加载中…</div>' +
      '</div>' +
      '<div class="card" id="tasks-table">' + tableSkeleton(8) + '</div>' +
      '</div>';
  }

  function bindTasks() {
    var refresh = $('#tasks-refresh');
    if (refresh) {
      refresh.addEventListener('click', function () {
        refresh.classList.add('spinning');
        loadTasks().then(function () { refresh.classList.remove('spinning'); });
      });
    }
    ['f-status', 'f-source'].forEach(function (id) {
      var el = $('#' + id);
      if (el) el.addEventListener('change', function () {
        state.tasks.filters.status = $('#f-status').value;
        state.tasks.filters.source = $('#f-source').value;
        loadTasks();
      });
    });
    var tableBox = $('#tasks-table');
    if (tableBox) {
      tableBox.addEventListener('click', function (e) {
        var btn = e.target.closest ? e.target.closest('[data-detail]') : null;
        var row = e.target.closest ? e.target.closest('[data-task-id]') : null;
        if (btn) { openTaskDetail(btn.getAttribute('data-detail')); return; }
        if (row) openTaskDetail(row.getAttribute('data-task-id'));
      });
    }
  }

  function loadTasks() {
    var box = $('#tasks-table');
    var count = $('#tasks-count');
    if (box) box.innerHTML = tableSkeleton(8);
    if (count) count.textContent = '加载中…';
    var qs = new URLSearchParams();
    qs.set('limit', '50');
    if (state.tasks.filters.status) qs.set('status', state.tasks.filters.status);
    if (state.tasks.filters.source) qs.set('source', state.tasks.filters.source);
    return api('/api/tasks?' + qs.toString()).then(function (data) {
      var tasks = normTasks(data);
      state.tasks.items = tasks;
      paintTasks(tasks);
      if (count) count.textContent = tasks.length + ' 条';
      if (state.pendingTaskId) {
        var id = state.pendingTaskId;
        state.pendingTaskId = null;
        openTaskDetail(id);
      }
    }, function (err) {
      if (box) {
        box.innerHTML = '<div class="card-body">' + errorBoxHTML(err, '任务列表加载失败', '重试') + '</div>';
        bindRetry(box, loadTasks);
      }
      if (count) count.textContent = '加载失败';
      toast('任务列表加载失败：' + errorText(err), 'error');
    });
  }

  function paintTasks(tasks) {
    var box = $('#tasks-table');
    if (!box) return;
    if (!tasks.length) {
      box.innerHTML = emptyHTML('没有匹配的任务', '调整过滤条件，或等待新的媒体请求');
      return;
    }
    var rows = tasks.map(function (t) {
      var thumb = '';
      if (t.kind === 'image' && t.result && t.result.url) {
        thumb = '<img class="thumb" loading="lazy" src="' + esc(mediaUrl(t.result.url)) + '" alt="" data-thumb="' + esc(t.id) + '">';
      }
      return '<tr class="clickable" data-task-id="' + esc(t.id) + '">' +
        '<td class="cell-id">' + esc(t.id) + '</td>' +
        '<td><div class="thumb-wrap">' + thumb + '<span class="badge badge-kind">' + esc(kindText(t.kind)) + '</span></div></td>' +
        '<td><span class="badge badge-' + statusClass(t.status) + '">' + esc(statusText(t.status)) + '</span></td>' +
        '<td>' + esc(sourceText(t.source)) + '</td>' +
        '<td class="cell-strong">' + esc(t.character || '—') + '</td>' +
        '<td class="cell-prompt"><div class="p" title="' + esc(t.prompt) + '">' + esc(t.prompt || '—') + '</div></td>' +
        '<td class="cell-num">' + esc(fmtTimeShort(t.created_at)) + '</td>' +
        '<td class="cell-num">' + esc(taskDuration(t)) + '</td>' +
        '<td><button type="button" class="btn sm ghost" data-detail="' + esc(t.id) + '">详情</button></td>' +
        '</tr>';
    }).join('');

    box.innerHTML = '<div class="table-wrap"><table class="table"><thead><tr>' +
      '<th>任务 ID</th><th>类型</th><th>状态</th><th>来源</th><th>角色</th><th>Prompt</th><th>创建</th><th>耗时</th><th></th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table></div>';

    $$('[data-thumb]', box).forEach(function (img) {
      img.addEventListener('error', function () {
        var wrap = img.closest('.thumb-wrap');
        if (wrap) {
          var ph = document.createElement('span');
          ph.className = 'file';
          ph.title = '缩略图加载失败：Gateway 未启动或 host.docker.internal 不可达';
          ph.textContent = '无图';
          img.parentNode.replaceChild(ph, img);
        }
      });
    });
  }

  /* ---------- 任务详情抽屉 ---------- */

  function openTaskDetail(id) {
    if (!id) return;
    state.drawerTaskId = id;
    var root = $('#drawer-root');
    root.innerHTML =
      '<div class="drawer-overlay" id="drawer-overlay"></div>' +
      '<aside class="drawer" id="task-drawer" role="dialog" aria-label="任务详情">' +
      '<header class="drawer-head"><span class="badge badge-muted">任务详情</span>' +
      '<h3 id="drawer-title">…</h3><span class="drawer-sub" id="drawer-sub"></span>' +
      '<button type="button" class="icon-btn close-btn" id="drawer-close" aria-label="关闭">×</button></header>' +
      '<div class="drawer-body" id="drawer-body">' + detailSkeleton() + '</div>' +
      '<footer class="drawer-foot" id="drawer-foot" hidden></footer>' +
      '</aside>';
    var overlay = $('#drawer-overlay');
    var drawer = $('#task-drawer');
    $('#drawer-close').addEventListener('click', closeTaskDetail);
    overlay.addEventListener('click', closeTaskDetail);
    requestAnimationFrame(function () {
      overlay.classList.add('open');
      drawer.classList.add('open');
    });
    loadTaskDetail(id);
  }

  function closeTaskDetail() {
    var overlay = $('#drawer-overlay');
    var drawer = $('#task-drawer');
    if (!drawer) return;
    if (overlay) overlay.classList.remove('open');
    drawer.classList.remove('open');
    state.drawerTaskId = null;
    setTimeout(function () {
      var root = $('#drawer-root');
      if (root && state.drawerTaskId === null) root.innerHTML = '';
    }, 220);
  }

  function detailSkeleton() {
    var out = '<div class="sk sk-row" style="width:45%;height:18px"></div>';
    out += '<div class="meta-grid" style="margin-top:14px">';
    for (var i = 0; i < 6; i++) out += '<div><div class="sk sk-row" style="width:34%;height:9px"></div><div class="sk sk-row" style="width:70%"></div></div>';
    out += '</div>' + skeletonRows(6);
    return out;
  }

  function loadTaskDetail(id) {
    var body = $('#drawer-body');
    if (!body) return;
    body.innerHTML = detailSkeleton();
    var title = $('#drawer-title');
    if (title) title.textContent = id;
    api('/api/tasks/' + encodeURIComponent(id)).then(function (data) {
      var task = data && data.task ? data.task : data;
      if (!task || typeof task !== 'object') throw new ApiError('任务详情为空', 0, data, '/api/tasks/' + id);
      paintTaskDetail(task);
    }, function (err) {
      var b = $('#drawer-body');
      if (!b) return;
      b.innerHTML = errorBoxHTML(err, '任务详情加载失败', '重试');
      bindRetry(b, function () { loadTaskDetail(id); });
      toast('任务详情加载失败：' + errorText(err), 'error');
    });
  }

  function paintTaskDetail(raw) {
    var t = normTasks({ tasks: [raw] })[0];
    if (!t) return;
    var body = $('#drawer-body');
    if (!body) return;
    var title = $('#drawer-title');
    if (title) title.textContent = t.id;
    var sub = $('#drawer-sub');
    if (sub) sub.textContent = fmtTs(t.created_at);

    var html = '' +
      '<div class="detail-statusline">' +
      '<span class="badge badge-' + statusClass(t.status) + '">' + esc(statusText(t.status)) + '</span>' +
      '<span class="badge badge-kind">' + esc(kindText(t.kind)) + '</span>' +
      '<span class="badge badge-muted">' + esc(sourceText(t.source)) + '</span>' +
      '<span class="hint" style="margin-left:auto">创建于 ' + esc(relTime(t.created_at)) + '</span>' +
      '</div>' +
      '<div class="meta-grid">' +
      metaItem('任务 ID', t.id) +
      metaItem('用户 user_id', t.user_id) +
      metaItem('会话 session_id', t.session_id) +
      metaItem('角色 character', t.character) +
      metaItem('远端 task remote_id', t.remote_id) +
      metaItem('耗时', taskDuration(t)) +
      '</div>' +
      '<div class="section-label">Prompt</div>' +
      '<div class="prompt-block">' + esc(t.prompt || '（无）') + '</div>' +
      '<div class="section-label">参数 params</div>' +
      (t.params === undefined || t.params === null
        ? '<div class="hint">（无参数）</div>'
        : '<pre class="json">' + esc(jsonText(t.params)) + '</pre>') +
      '<div class="section-label">结果 result</div>' +
      resultHtml(t) +
      '<div class="section-label">错误 error</div>' +
      (t.error === null || t.error === undefined || t.error === ''
        ? '<div class="hint">（无错误）</div>'
        : '<div class="err-block">' + esc(errorText(t.error)) + '</div>') +
      '<div class="section-label">时间线</div>' +
      '<div class="timing-line">' +
      '<span>创建 ' + esc(fmtTs(t.created_at)) + '</span><span class="t-sep">→</span>' +
      '<span>开始 ' + esc(fmtTs(t.started_at)) + '</span><span class="t-sep">→</span>' +
      '<span>结束 ' + esc(fmtTs(t.finished_at)) + '</span>' +
      '</div>';

    body.innerHTML = html;

    var foot = $('#drawer-foot');
    if (foot) {
      var canCancel = t.status === 'queued' || t.status === 'running';
      foot.hidden = false;
      foot.innerHTML = '<button type="button" class="btn sm" id="drawer-reload"><span class="refresh-glyph">↻</span> 刷新</button>' +
        (canCancel ? '<button type="button" class="btn sm danger" id="drawer-cancel">取消任务</button>' : '') +
        '<span class="hint" style="margin-left:auto">所有界面共享同一任务 ID</span>';
      var reload = $('#drawer-reload');
      if (reload) reload.addEventListener('click', function () { loadTaskDetail(t.id); });
      var cancel = $('#drawer-cancel');
      if (cancel) cancel.addEventListener('click', function () { cancelTask(t.id); });
    }

    var img = body.querySelector('[data-result-img]');
    if (img) {
      img.addEventListener('error', function () {
        var fallback = img.parentNode.querySelector('.result-fallback');
        if (fallback) fallback.insertAdjacentHTML('beforebegin', '<div class="err-block">图片加载失败：Media Gateway 可能未启动，或 host.docker.internal 在浏览器侧不可达（已尝试重写为 127.0.0.1）。</div>');
      });
    }
  }

  function metaItem(key, value) {
    return '<div class="meta-item"><span class="k">' + esc(key) + '</span>' +
      '<span class="v">' + esc(value === undefined || value === null || value === '' ? '—' : value) + '</span></div>';
  }

  function resultHtml(t) {
    var r = t.result;
    if (!r) return '<div class="hint">（无产物）</div>';
    var html = '';
    var url = mediaUrl(r.url || '');
    if (t.kind === 'image' && url) {
      html += '<div class="result-media"><img data-result-img src="' + esc(url) + '" alt="生成图片" referrerpolicy="no-referrer">' +
        '<div class="result-fallback">文件 ' + esc(r.filename || '—') +
        ' · <a href="' + esc(url) + '" target="_blank" rel="noopener">打开原图</a></div></div>';
    } else if (t.kind === 'voice' && (url || r.filename)) {
      var audioUrl = url || mediaUrl(r.filename);
      html += '<div class="result-media"><audio controls preload="none" src="' + esc(audioUrl) + '"></audio>' +
        '<div class="result-fallback">文件 ' + esc(r.filename || '—') +
        ' · <a href="' + esc(audioUrl) + '" target="_blank" rel="noopener">打开音频</a></div></div>';
    }
    html += '<pre class="json">' + esc(jsonText(r)) + '</pre>';
    return html;
  }

  function cancelTask(id) {
    confirmDialog({
      title: '取消任务',
      html: '确认取消任务 <strong>' + esc(id) + '</strong>？<br><span class="hint">排队中的任务会从队列移除；运行中的任务会中断并释放 GPU。</span>',
      okText: '取消任务',
      danger: true
    }).then(function (ok) {
      if (!ok) return;
      return api('/api/tasks/' + encodeURIComponent(id) + '/cancel', { method: 'POST' }).then(function (data) {
        toast('取消指令已提交' + (data && data.ok === false ? '（接口返回失败：' + jsonText(data) + '）' : ''), data && data.ok === false ? 'error' : 'success');
        loadTaskDetail(id);
        if (state.view === 'tasks') loadTasks();
      }, function (err) {
        toast('取消失败：' + errorText(err), 'error');
      });
    });
  }

  /* ============================================================
   * 角色视图
   * ============================================================ */

  function renderCharacters(root) {
    root.innerHTML = charactersShell();
    bindCharacters();
    return loadCharacters();
  }

  function charactersShell() {
    return '' +
      '<div class="view">' +
      '<div class="view-head"><div class="vh-main">' +
      '<h2>角色档案 <span class="hint">/api/characters</span></h2>' +
      '<p class="view-sub">生图 checkpoint / LoRA 链 / workflow / prompt / 语音与媒体策略 · 保存后热生效</p></div>' +
      '<div class="view-actions"><button type="button" class="btn sm" id="chars-refresh"><span class="refresh-glyph">↻</span> 刷新</button></div>' +
      '</div>' +
      '<div class="char-layout">' +
      '<aside class="card char-list" id="char-list">' + skeletonRows(6, [30, 30, 30, 30]) + '</aside>' +
      '<section class="card" id="char-editor"><div class="card-body">' + skeletonRows(10) + '</div></section>' +
      '<aside class="card constitution-card" id="constitution">' +
      '<div class="card-head"><h3>角色宪法</h3><span class="card-sub">只读 · 仅 Console 可写</span>' +
      '<div class="card-actions"><button type="button" class="btn sm ghost" id="const-refresh" title="重新加载宪法">↻</button></div></div>' +
      '<div class="card-body" id="const-body"><div class="hint">选择角色后加载</div></div>' +
      '</aside>' +
      '</div>' +
      '</div>';
  }

  function bindCharacters() {
    var refresh = $('#chars-refresh');
    if (refresh) {
      refresh.addEventListener('click', function () {
        refresh.classList.add('spinning');
        loadCharacters().then(function () { refresh.classList.remove('spinning'); });
      });
    }
    var constRefresh = $('#const-refresh');
    if (constRefresh) {
      constRefresh.addEventListener('click', function () {
        if (state.characters.selected) loadConstitution(state.characters.selected);
      });
    }
    var listBox = $('#char-list');
    if (listBox) {
      listBox.addEventListener('click', function (e) {
        var item = e.target.closest ? e.target.closest('[data-char-id]') : null;
        if (item) selectCharacter(item.getAttribute('data-char-id'));
      });
    }
  }

  function loadCharacters() {
    var listBox = $('#char-list');
    return api('/api/characters').then(function (data) {
      var list = asList(data, ['characters']);
      state.characters.list = list;
      paintCharList();
      var ids = list.map(function (c) { return String(c && c.id); });
      var target = state.characters.selected && ids.indexOf(state.characters.selected) >= 0
        ? state.characters.selected
        : (ids.length ? ids[0] : null);
      if (target) {
        return selectCharacter(target);
      }
      var editor = $('#char-editor');
      if (editor) editor.innerHTML = emptyHTML('暂无角色档案', '在 mobius/data/characters/ 下添加 <id>.json 后可在此编辑');
      var constBody = $('#const-body');
      if (constBody) constBody.innerHTML = '<div class="hint">暂无角色</div>';
      return null;
    }, function (err) {
      if (listBox) {
        listBox.innerHTML = errorBoxHTML(err, '角色列表加载失败', '重试');
        bindRetry(listBox, loadCharacters);
      }
      var editor = $('#char-editor');
      if (editor) editor.innerHTML = '<div class="card-body">' + errorBoxHTML(err, '无法加载角色', '重试') + '</div>';
      bindRetry($('#char-editor'), loadCharacters);
      toast('角色列表加载失败：' + errorText(err), 'error');
      return null;
    });
  }

  function paintCharList() {
    var box = $('#char-list');
    if (!box) return;
    var list = state.characters.list;
    if (!list.length) {
      box.innerHTML = emptyHTML('暂无角色');
      return;
    }
    box.innerHTML = list.map(function (c) {
      var id = String(c && c.id ? c.id : '');
      var name = (c && c.name) || id || '未命名';
      var initial = String(name).trim().charAt(0) || '?';
      return '<div class="char-item' + (id === state.characters.selected ? ' active' : '') + '" data-char-id="' + esc(id) + '">' +
        '<div class="char-avatar">' + esc(initial) + '</div>' +
        '<div class="ci-text"><span class="ci-name">' + esc(name) + '</span><span class="ci-id">' + esc(id) + '</span></div>' +
        '</div>';
    }).join('');
  }

  function selectCharacter(id) {
    state.characters.selected = id;
    $$('#char-list [data-char-id]').forEach(function (node) {
      node.classList.toggle('active', node.getAttribute('data-char-id') === id);
    });
    var editor = $('#char-editor');
    if (editor) editor.innerHTML = '<div class="card-body">' + skeletonRows(12) + '</div>';
    var constBody = $('#const-body');
    if (constBody) constBody.innerHTML = skeletonRows(8);

    var detailP = api('/api/characters/' + encodeURIComponent(id)).then(function (data) {
      var ch = data && data.character ? data.character : data;
      if (!ch || typeof ch !== 'object') throw new ApiError('角色档案为空', 0, data, '/api/characters/' + id);
      state.characters.raw = ch;
      paintCharEditor(ch);
      return ch;
    }, function (err) {
      var fallback = state.characters.list.filter(function (c) { return String(c.id) === id; })[0];
      if (fallback) {
        state.characters.raw = fallback;
        paintCharEditor(fallback);
        toast('角色详情接口失败，显示列表数据：' + errorText(err), 'error');
      } else if (editor) {
        editor.innerHTML = '<div class="card-body">' + errorBoxHTML(err, '角色详情加载失败', '重试') + '</div>';
        bindRetry(editor, function () { selectCharacter(id); });
        toast('角色详情加载失败：' + errorText(err), 'error');
      }
      return null;
    });

    loadConstitution(id);
    return detailP;
  }

  function paintCharEditor(ch) {
    var editor = $('#char-editor');
    if (!editor) return;
    var voice = ch.voice || {};
    var policy = ch.media_policy || {};
    var size = Array.isArray(ch.size) ? ch.size : [];
    var aliases = Array.isArray(ch.aliases) ? ch.aliases.join(', ') : '';

    editor.innerHTML = '' +
      '<form id="char-form" autocomplete="off">' +
      '<div class="form-head">' +
      '<div class="fh-title"><h3>' + esc(ch.name || ch.id) + '</h3><span class="fh-id">' + esc(ch.id) + '</span></div>' +
      '<div class="fh-actions">' +
      '<span class="hint" id="char-save-state"></span>' +
      '<button type="button" class="btn sm" id="char-reset">重置</button>' +
      '<button type="submit" class="btn primary sm" id="char-save">保存</button>' +
      '</div></div>' +
      '<div class="form-body"><div class="form-grid">' +
      '<section class="form-section"><h4>基础</h4>' +
      field('显示名', '<input class="input" id="c-name" value="' + esc(ch.name || '') + '">') +
      field('别名（英文逗号分隔）', '<input class="input" id="c-aliases" value="' + esc(aliases) + '">') +
      field('Checkpoint', '<input class="input mono" id="c-checkpoint" value="' + esc(ch.checkpoint || '') + '">') +
      field('Workflow', '<input class="input" id="c-workflow" value="' + esc(ch.workflow || '') + '">') +
      field('尺寸 size（宽 × 高）', '<div class="field-row">' +
        '<input type="number" class="input mono" id="c-w" style="width:96px" min="64" step="64" value="' + esc(size[0] !== undefined ? size[0] : 832) + '">' +
        '<span class="suffix">×</span>' +
        '<input type="number" class="input mono" id="c-h" style="width:96px" min="64" step="64" value="' + esc(size[1] !== undefined ? size[1] : 1216) + '">' +
        '</div>') +
      '</section>' +
      '<section class="form-section"><h4>语音与媒体策略</h4>' +
      '<div class="grid-3">' +
      field('引擎 engine', '<select class="input" id="c-voice-engine">' +
        ['kokoro', 'f5'].map(function (v) {
          return '<option value="' + v + '"' + (String(voice.engine || 'kokoro') === v ? ' selected' : '') + '>' + v + '</option>';
        }).join('') + '</select>') +
      field('音色 voice', '<input class="input mono" id="c-voice-name" value="' + esc(voice.voice || '') + '">') +
      field('语速 speed', '<input type="number" step="0.05" min="0.5" max="2" class="input mono" id="c-voice-speed" value="' + esc(voice.speed !== undefined ? voice.speed : 1.0) + '">') +
      '</div>' +
      '<div class="field"><label>媒体策略 media_policy</label><div class="field-row" style="gap:16px;flex-wrap:wrap">' +
      '<label class="check"><input type="checkbox" id="c-allow-image"' + (policy.allow_image !== false ? ' checked' : '') + '> 允许生图</label>' +
      '<label class="check"><input type="checkbox" id="c-allow-voice"' + (policy.allow_voice !== false ? ' checked' : '') + '> 允许语音</label>' +
      '<label class="check"><input type="checkbox" id="c-allow-clone"' + (policy.allow_clone ? ' checked' : '') + '> 允许克隆</label>' +
      '</div></div>' +
      '<div class="field"><label>constitution_id</label><div class="mono small dim">' + esc(ch.constitution_id || '—') + '</div></div>' +
      '</section>' +
      '<section class="form-section span-2"><h4>LoRA 链 <span class="hint">顺序即注入顺序 · 权重 0–1.5</span></h4>' +
      '<table class="lora-table"><thead><tr><th>LoRA 文件名</th><th style="width:104px">权重</th><th></th></tr></thead>' +
      '<tbody id="lora-body"></tbody></table>' +
      '<button type="button" class="btn sm ghost" id="lora-add">+ 添加 LoRA</button>' +
      '</section>' +
      '<section class="form-section span-2"><h4>风格 Prompt</h4>' +
      '<div class="field"><textarea class="input" id="c-style" rows="5" placeholder="masterpiece, best quality, ...">' + esc(ch.style_prompt || '') + '</textarea></div>' +
      '</section>' +
      '<section class="form-section span-2"><h4>反向 Prompt</h4>' +
      '<div class="field"><textarea class="input" id="c-negative" rows="3" placeholder="lowres, bad anatomy, ...">' + esc(ch.negative_prompt || '') + '</textarea></div>' +
      '</section>' +
      '</div></div></form>';

    var loras = Array.isArray(ch.loras) ? ch.loras : [];
    loras.forEach(addLoraRow);
    if (!loras.length) addLoraRow({});

    $('#lora-add').addEventListener('click', function () { addLoraRow({}); });
    $('#char-reset').addEventListener('click', function () { selectCharacter(state.characters.selected); });
    $('#char-form').addEventListener('submit', function (e) {
      e.preventDefault();
      saveCharacter();
    });
  }

  function field(label, control) {
    return '<div class="field"><label>' + esc(label) + '</label>' + control + '</div>';
  }

  function addLoraRow(lora) {
    var body = $('#lora-body');
    if (!body) return;
    var tr = document.createElement('tr');
    var weight = lora && lora.weight !== undefined && lora.weight !== null ? lora.weight : 0.75;
    tr.innerHTML = '<td><input class="input lora-name" placeholder="xxxx.safetensors" value="' + esc((lora && lora.name) || '') + '"></td>' +
      '<td><input type="number" class="input lora-weight" min="0" max="1.5" step="0.05" value="' + esc(weight) + '"></td>' +
      '<td><button type="button" class="icon-btn lora-del" title="删除该 LoRA">×</button></td>';
    tr.querySelector('.lora-del').addEventListener('click', function () { tr.remove(); });
    body.appendChild(tr);
  }

  function saveCharacter() {
    var raw = state.characters.raw;
    if (!raw) { toast('没有可保存的角色', 'error'); return; }
    var stateEl = $('#char-save-state');
    var saveBtn = $('#char-save');
    var width = Number($('#c-w').value);
    var height = Number($('#c-h').value);
    if (!isFinite(width) || !isFinite(height) || width <= 0 || height <= 0) {
      toast('尺寸必须是正整数', 'error');
      return;
    }
    var loras = [];
    var clamped = false;
    $$('#lora-body tr').forEach(function (tr) {
      var name = tr.querySelector('.lora-name').value.trim();
      if (!name) return;
      var weight = Number(tr.querySelector('.lora-weight').value);
      if (!isFinite(weight)) weight = 0.75;
      if (weight < 0) { weight = 0; clamped = true; }
      if (weight > 1.5) { weight = 1.5; clamped = true; }
      loras.push({ name: name, weight: weight });
    });
    if (clamped) toast('部分 LoRA 权重超出 0–1.5，已按契约截断', 'info');

    var payload = Object.assign({}, raw, {
      id: raw.id,
      name: $('#c-name').value.trim(),
      aliases: $('#c-aliases').value.split(',').map(function (s) { return s.trim(); }).filter(Boolean),
      checkpoint: $('#c-checkpoint').value.trim(),
      loras: loras,
      workflow: $('#c-workflow').value.trim(),
      size: [Math.round(width), Math.round(height)],
      style_prompt: $('#c-style').value.trim(),
      negative_prompt: $('#c-negative').value.trim(),
      voice: {
        engine: $('#c-voice-engine').value,
        voice: $('#c-voice-name').value.trim(),
        speed: numberOr($('#c-voice-speed').value, 1.0)
      },
      media_policy: {
        allow_image: $('#c-allow-image').checked,
        allow_voice: $('#c-allow-voice').checked,
        allow_clone: $('#c-allow-clone').checked
      }
    });

    if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = '保存中…'; }
    if (stateEl) stateEl.textContent = 'PUT /api/characters/' + raw.id;
    api('/api/characters/' + encodeURIComponent(raw.id), { method: 'PUT', body: payload }).then(function () {
      state.characters.raw = payload;
      var item = state.characters.list.filter(function (c) { return String(c.id) === String(raw.id); })[0];
      if (item) Object.assign(item, payload);
      paintCharList();
      toast('角色「' + (payload.name || payload.id) + '」已保存，热生效', 'success');
      if (stateEl) stateEl.textContent = '已保存 · ' + new Date().toLocaleTimeString('zh-CN', { hour12: false });
    }, function (err) {
      if (stateEl) stateEl.textContent = '保存失败';
      toast('保存失败：' + errorText(err), 'error');
    }).then(function () {
      if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = '保存'; }
    });
  }

  function loadConstitution(id) {
    var box = $('#const-body');
    if (!box) return;
    box.innerHTML = skeletonRows(9);
    api('/api/characters/' + encodeURIComponent(id) + '/constitution').then(function (data) {
      var target = $('#const-body');
      if (!target) return;
      target.innerHTML = renderConstitution(data);
    }, function (err) {
      var target = $('#const-body');
      if (!target) return;
      target.innerHTML = errorBoxHTML(err, '角色宪法加载失败', '重试');
      bindRetry(target, function () { loadConstitution(id); });
    });
  }

  function renderConstitution(data) {
    if (data === null || data === undefined) {
      return emptyHTML('该角色暂无宪法', 'GET /api/characters/{id}/constitution 返回空');
    }

    // 形态 ②：Core 返回 {active, versions, fallback}
    if (typeof data === 'object' && !Array.isArray(data)) {
      var hasEnvelope = Object.prototype.hasOwnProperty.call(data, 'active') ||
        Object.prototype.hasOwnProperty.call(data, 'fallback') ||
        Array.isArray(data.versions);
      if (hasEnvelope) {
        var active = data.active;
        var versions = Array.isArray(data.versions) ? data.versions : null;
        var fallback = typeof data.fallback === 'string' ? data.fallback : '';
        var doc = null;
        if (active && typeof active === 'object') doc = active;
        else if (typeof active === 'string' && active.trim()) doc = { text: active };
        if (doc) {
          var docText = doc.text || doc.content || doc.body || doc.markdown || '';
          var metaParts = [];
          if (doc.version !== undefined) metaParts.push('<span class="badge badge-version">v' + esc(doc.version) + '</span>');
          if (doc.created_at) metaParts.push('<span>生效于 ' + esc(fmtTs(doc.created_at)) + '</span>');
          if (doc.note) metaParts.push('<span>note: ' + esc(doc.note) + '</span>');
          if (versions) metaParts.push('<span>历史版本 ' + esc(versions.length) + '</span>');
          return '<div class="const-meta">' + metaParts.join('') + '</div>' +
            '<div class="const-content">' + renderLiteMarkdown(docText || '（宪法内容为空）') + '</div>';
        }
        if (fallback.trim()) {
          return '<div class="const-meta"><span class="badge badge-muted">未配置</span>' +
            '<span>角色宪法未写入，当前使用 Core 通用 fallback（只读）</span>' +
            (versions && versions.length ? '<span>历史版本 ' + esc(versions.length) + '</span>' : '') + '</div>' +
            '<div class="const-content">' + renderLiteMarkdown(fallback) + '</div>';
        }
        return emptyHTML('该角色暂无宪法', 'Core 未返回 active / fallback 内容');
      }
    }

    // 形态 ①：单对象 {content|text|...} 或纯字符串
    var content = data;
    var metaLine = '';
    if (typeof data === 'object') {
      var contentKeys = ['content', 'text', 'body', 'markdown', 'constitution', 'value', 'content_md', 'fallback'];
      for (var i = 0; i < contentKeys.length; i++) {
        if (typeof data[contentKeys[i]] === 'string') { content = data[contentKeys[i]]; break; }
      }
      var metaParts2 = [];
      if (data.version !== undefined) metaParts2.push('<span class="badge badge-version">v' + esc(data.version) + '</span>');
      if (data.updated_at) metaParts2.push('<span>更新于 ' + esc(fmtTs(data.updated_at)) + '</span>');
      if (data.updated_by) metaParts2.push('<span>by ' + esc(data.updated_by) + '</span>');
      if (data.id) metaParts2.push('<span class="mono">' + esc(data.id) + '</span>');
      if (metaParts2.length) metaLine = '<div class="const-meta">' + metaParts2.join('') + '</div>';
    }
    var body;
    if (typeof content === 'string') {
      body = '<div class="const-content">' + renderLiteMarkdown(content) + '</div>';
    } else {
      body = '<pre class="json">' + esc(jsonText(content)) + '</pre>';
    }
    var extra = '';
    if (typeof data === 'object' && typeof content === 'string') {
      var rest = {};
      Object.keys(data).forEach(function (k) {
        if (k !== 'content' && k !== 'text' && k !== 'body' && k !== 'markdown' && k !== 'constitution' && k !== 'value' &&
          k !== 'version' && k !== 'updated_at' && k !== 'updated_by' && k !== 'id' && k !== 'fallback') {
          rest[k] = data[k];
        }
      });
      if (Object.keys(rest).length) {
        extra = '<details class="raw" style="margin-top:10px"><summary>其他字段</summary>' +
          '<div class="raw-body"><pre class="json">' + esc(jsonText(rest)) + '</pre></div></details>';
      }
    }
    return metaLine + body + extra;
  }

  function renderLiteMarkdown(text) {
    var lines = String(text).split(/\r?\n/);
    var out = [];
    var inList = false;
    var closeList = function () { if (inList) { out.push('</ul>'); inList = false; } };
    lines.forEach(function (line) {
      var t = line.trim();
      if (!t) { closeList(); return; }
      var heading = t.match(/^#{1,6}\s+(.*)$/);
      if (heading) { closeList(); out.push('<h4>' + esc(heading[1]) + '</h4>'); return; }
      var bullet = t.match(/^[-*]\s+(.*)$/);
      if (bullet) {
        if (!inList) { out.push('<ul>'); inList = true; }
        out.push('<li>' + esc(bullet[1]) + '</li>');
        return;
      }
      closeList();
      out.push('<p>' + esc(t) + '</p>');
    });
    closeList();
    return out.join('');
  }

  /* ============================================================
   * 记忆视图
   * ============================================================ */

  function renderMemory(root) {
    root.innerHTML = memoryShell();
    bindMemory();
    return Promise.all([loadCandidates(), loadMemoryUsers(), refreshPendingBadge()]).then(function () {
      addTimer(refreshPendingBadge, PENDING_POLL_MS);
    });
  }

  function memoryShell() {
    return '' +
      '<div class="view">' +
      '<div class="view-head"><div class="vh-main">' +
      '<h2>记忆 <span class="hint">/api/memory</span></h2>' +
      '<p class="view-sub">四层记忆：用户档案 / 时间线 / 最近轮次 · 候选记忆审批后生效</p></div>' +
      '<div class="view-actions"><button type="button" class="btn sm" id="mem-refresh"><span class="refresh-glyph">↻</span> 刷新</button></div>' +
      '</div>' +
      '<div class="mem-layout">' +
      '<aside class="card mem-users">' +
      '<div class="card-head"><h3>用户</h3><span class="card-sub" id="mem-users-src"></span></div>' +
      '<div class="mem-user-list" id="mem-user-list">' + skeletonRows(6, [17]) + '</div>' +
      '<div class="manual"><label class="hint" for="mem-manual">手动输入 user_id</label>' +
      '<input class="input mono" id="mem-manual" placeholder="qq:123 / web:u1">' +
      '<button type="button" class="btn sm" id="mem-load-btn">加载用户</button></div>' +
      '</aside>' +
      '<section class="mem-main">' +
      '<section class="card candidates-card">' +
      '<div class="card-head"><h3>候选记忆待审</h3><span class="cand-count" id="cand-count" hidden>0</span>' +
      '<span class="card-sub">模型只能写候选 · 确认后生效</span>' +
      '<div class="card-actions"><button type="button" class="btn sm ghost" id="cand-refresh" title="刷新候选">↻</button></div></div>' +
      '<div class="cand-list" id="cand-list">' + skeletonRows(3) + '</div>' +
      '</section>' +
      '<div class="mem-cols">' +
      '<section class="card"><div class="card-head"><h3>用户档案</h3><span class="card-sub">user_memory</span></div>' +
      '<div class="mem-scroll" id="mem-profile"><div class="empty">选择用户后加载</div></div></section>' +
      '<section class="card"><div class="card-head"><h3>时间线</h3><span class="card-sub">timeline</span></div>' +
      '<div class="mem-scroll" id="mem-timeline"><div class="empty">选择用户后加载</div></div></section>' +
      '<section class="card"><div class="card-head"><h3>最近轮次</h3><span class="card-sub">turns · TTL</span></div>' +
      '<div class="mem-scroll" id="mem-turns"><div class="empty">选择用户后加载</div></div></section>' +
      '</div>' +
      '</section>' +
      '</div>' +
      '</div>';
  }

  function bindMemory() {
    var refresh = $('#mem-refresh');
    if (refresh) {
      refresh.addEventListener('click', function () {
        refresh.classList.add('spinning');
        Promise.all([loadCandidates(), loadMemoryUsers(), refreshPendingBadge()]).then(function () {
          refresh.classList.remove('spinning');
        });
      });
    }
    var candRefresh = $('#cand-refresh');
    if (candRefresh) {
      candRefresh.addEventListener('click', function () {
        candRefresh.classList.add('spinning');
        loadCandidates().then(function () { candRefresh.classList.remove('spinning'); });
      });
    }
    var loadBtn = $('#mem-load-btn');
    var manual = $('#mem-manual');
    if (loadBtn && manual) {
      loadBtn.addEventListener('click', function () {
        var id = manual.value.trim();
        if (!id) { toast('请输入 user_id', 'error'); return; }
        selectUser(id);
      });
      manual.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); loadBtn.click(); }
      });
    }
    var userList = $('#mem-user-list');
    if (userList) {
      userList.addEventListener('click', function (e) {
        var item = e.target.closest ? e.target.closest('[data-user-id]') : null;
        if (item) selectUser(item.getAttribute('data-user-id'));
      });
    }
    var candList = $('#cand-list');
    if (candList) {
      candList.addEventListener('click', function (e) {
        var btn = e.target.closest ? e.target.closest('[data-cand-action]') : null;
        if (!btn) return;
        handleCandidateAction(btn.getAttribute('data-cand-id'), btn.getAttribute('data-cand-action'), btn);
      });
    }
    var profile = $('#mem-profile');
    if (profile) {
      profile.addEventListener('click', function (e) {
        var btn = e.target.closest ? e.target.closest('[data-forget-key]') : null;
        if (!btn) return;
        forgetMemory(btn.getAttribute('data-forget-key'));
      });
    }
  }

  function loadCandidates() {
    var box = $('#cand-list');
    return api('/api/memory/candidates?status=pending').then(function (data) {
      var list = asList(data, ['candidates']).filter(function (c) { return c && typeof c === 'object'; });
      state.memory.candidates = list;
      paintCandidates(list);
      setPendingBadge(list.length);
      return list;
    }, function (err) {
      if (box) {
        box.innerHTML = '<div style="padding:12px 14px">' + errorBoxHTML(err, '候选记忆加载失败', '重试') + '</div>';
        bindRetry(box, loadCandidates);
      }
      toast('候选记忆加载失败：' + errorText(err), 'error');
      return null;
    });
  }

  function paintCandidates(list) {
    var box = $('#cand-list');
    if (!box) return;
    var countEl = $('#cand-count');
    if (countEl) {
      countEl.textContent = String(list.length);
      countEl.hidden = list.length === 0;
    }
    if (!list.length) {
      box.innerHTML = emptyHTML('没有待审候选记忆', '模型写入的候选会出现在这里，确认后才进入用户档案');
      return;
    }
    box.innerHTML = list.map(function (c) {
      var confidence = numberOr(c.confidence, 0);
      var width = Math.max(0, Math.min(100, confidence * 100));
      return '<div class="cand" data-cand="' + esc(c.id) + '">' +
        '<div class="cand-main">' +
        '<div class="cand-key"><span class="k-name">' + esc(c.key || '（无 key）') + '</span>' +
        '<span class="k-user">' + esc(c.user_id || '') + (c.character ? ' · ' + esc(c.character) : '') + '</span>' +
        '<span class="conf-bar" title="置信度 ' + esc(confidence) + '">' +
        '<span class="conf-track"><span class="conf-fill" style="width:' + width + '%"></span></span>' +
        '<span class="mono" style="font-size:10px;color:var(--faint)">' + esc(confidence.toFixed(2)) + '</span></span>' +
        '</div>' +
        '<div class="cand-value">' + esc(c.value || '') + '</div>' +
        '<div class="cand-meta"><span>source ' + esc(c.source || '—') + '</span><span>' + esc(relTime(c.created_at)) + '</span>' +
        (c.status ? '<span>status ' + esc(c.status) + '</span>' : '') + '</div>' +
        (c.evidence ? '<div class="cand-evidence">' + esc(c.evidence) + '</div>' : '') +
        '</div>' +
        '<div class="cand-actions">' +
        '<button type="button" class="btn sm ok" data-cand-action="approve" data-cand-id="' + esc(c.id) + '">确认</button>' +
        '<button type="button" class="btn sm danger" data-cand-action="reject" data-cand-id="' + esc(c.id) + '">拒绝</button>' +
        '</div></div>';
    }).join('');
  }

  function handleCandidateAction(id, action, btn) {
    if (!id || !action) return;
    var verb = action === 'approve' ? '确认' : '拒绝';
    var card = btn.closest ? btn.closest('.cand') : null;
    var buttons = card ? $$('button', card) : [btn];
    buttons.forEach(function (b) { b.disabled = true; });
    api('/api/memory/candidates/' + encodeURIComponent(id) + '/' + action, { method: 'POST' }).then(function () {
      toast('候选记忆已' + verb, 'success');
      if (card && card.parentNode) card.parentNode.removeChild(card);
      return Promise.all([loadCandidates(), state.memory.selected ? loadMemoryUser(state.memory.selected) : null]);
    }, function (err) {
      toast(verb + '候选失败：' + errorText(err), 'error');
      buttons.forEach(function (b) { b.disabled = false; });
    });
  }

  function loadMemoryUsers() {
    var listBox = $('#mem-user-list');
    var srcEl = $('#mem-users-src');
    return api('/api/memory/users').then(function (data) {
      var raw = asList(data, ['users', 'items']);
      var users = raw.map(normalizeUserEntry).filter(function (u) { return u && u.user_id; });
      state.memory.usersFromApi = true;
      if (srcEl) srcEl.textContent = 'GET /api/memory/users';
      return applyUsers(users);
    }, function () {
      state.memory.usersFromApi = false;
      if (srcEl) srcEl.textContent = '聚合 · 手动';
      return aggregateUsers().then(applyUsers);
    });
  }

  function normalizeUserEntry(entry, source) {
    if (!entry) return null;
    if (typeof entry === 'string') return { user_id: entry, count: 0, source: source };
    var id = entry.user_id || entry.id || entry.user || entry.uid;
    if (!id) return null;
    return {
      user_id: String(id),
      count: numberOr(entry.count !== undefined ? entry.count
        : (entry.n !== undefined ? entry.n
          : (entry.memories !== undefined ? entry.memories : entry.total)), 0),
      source: source
    };
  }

  function aggregateUsers() {
    var seen = Object.create(null);
    var users = [];
    function add(id, source) {
      if (!id) return;
      var key = String(id);
      if (!seen[key]) {
        seen[key] = { user_id: key, count: 0, source: source };
        users.push(seen[key]);
      }
      seen[key].count += 1;
    }
    state.memory.candidates.forEach(function (c) { add(c.user_id, 'candidates'); });
    return Promise.all([
      api('/api/audit?limit=100').then(function (d) {
        asList(d, ['audit', 'items']).forEach(function (a) { add(a.actor, 'audit'); });
      }, function () { return null; }),
      api('/api/tasks?limit=50').then(function (d) {
        normTasks(d).forEach(function (t) { add(t.user_id, 'tasks'); });
      }, function () { return null; })
    ]).then(function () {
      users.sort(function (a, b) { return b.count - a.count; });
      return users;
    });
  }

  function applyUsers(users) {
    state.memory.users = users || [];
    paintUserList();
    var ids = state.memory.users.map(function (u) { return u.user_id; });
    if (state.memory.selected && ids.indexOf(state.memory.selected) < 0) {
      state.memory.selected = null;
    }
    var target = state.memory.selected || (ids.length ? ids[0] : null);
    if (target) {
      return selectUser(target);
    }
    paintMemoryPlaceholders('没有可展示的用户', state.memory.usersFromApi
      ? '接口未返回用户列表'
      : '可从候选记忆 / 审计 / 任务聚合，或在左侧手动输入 user_id');
    return null;
  }

  function paintUserList() {
    var box = $('#mem-user-list');
    if (!box) return;
    var users = state.memory.users;
    if (!users.length) {
      box.innerHTML = emptyHTML('暂无用户', '手动输入 user_id 或等待新任务产生');
      return;
    }
    box.innerHTML = users.map(function (u) {
      return '<div class="mem-user' + (u.user_id === state.memory.selected ? ' active' : '') + '" data-user-id="' + esc(u.user_id) + '">' +
        '<span class="mu-id" title="' + esc(u.user_id) + '">' + esc(u.user_id) + '</span>' +
        (u.count ? '<span class="mu-count">' + esc(String(u.count)) + '</span>' : '') +
        '</div>';
    }).join('');
  }

  function paintMemoryPlaceholders(title, sub) {
    ['#mem-profile', '#mem-timeline', '#mem-turns'].forEach(function (sel) {
      var box = $(sel);
      if (box) box.innerHTML = emptyHTML(title, sub);
    });
  }

  function selectUser(userId) {
    state.memory.selected = userId;
    $$('#mem-user-list [data-user-id]').forEach(function (node) {
      node.classList.toggle('active', node.getAttribute('data-user-id') === userId);
    });
    var manual = $('#mem-manual');
    if (manual) manual.value = userId;
    ['#mem-profile', '#mem-timeline', '#mem-turns'].forEach(function (sel) {
      var box = $(sel);
      if (box) box.innerHTML = skeletonRows(6);
    });
    return loadMemoryUser(userId);
  }

  function loadMemoryUser(userId) {
    return api('/api/memory/user/' + encodeURIComponent(userId)).then(function (data) {
      var payload = data || {};
      paintMemoryColumn('#mem-profile', 'profile', payload.profile);
      paintMemoryColumn('#mem-timeline', 'timeline', payload.timeline);
      paintMemoryColumn('#mem-turns', 'turns', payload.turns);
      return payload;
    }, function (err) {
      ['#mem-profile', '#mem-timeline', '#mem-turns'].forEach(function (sel) {
        var box = $(sel);
        if (box) {
          box.innerHTML = errorBoxHTML(err, '用户记忆加载失败', '重试');
          bindRetry(box, function () { loadMemoryUser(userId); });
        }
      });
      toast('用户记忆加载失败：' + errorText(err), 'error');
      return null;
    });
  }

  function paintMemoryColumn(sel, kind, payload) {
    var box = $(sel);
    if (!box) return;
    var list = [];
    if (Array.isArray(payload)) {
      list = payload;
    } else if (payload && typeof payload === 'object') {
      list = Object.keys(payload).map(function (k) {
        var v = payload[k];
        if (v && typeof v === 'object') return Object.assign({ key: k }, v);
        return { key: k, value: v };
      });
    }
    if (!list.length) {
      box.innerHTML = emptyHTML('暂无' + (kind === 'profile' ? '用户档案' : kind === 'timeline' ? '时间线' : '轮次'));
      return;
    }

    if (kind === 'profile') {
      box.innerHTML = list.map(function (item) {
        var key = item.key !== undefined ? item.key : (item.k !== undefined ? item.k : item.name);
        var value = item.value !== undefined ? item.value : (item.v !== undefined ? item.v : item.content);
        var confidence = item.confidence !== undefined ? numberOr(item.confidence, 0) : null;
        var status = item.status || '';
        var updated = item.updated_at || item.created_at || item.ts;
        return '<div class="profile-item">' +
          '<div class="pi-main"><div class="pi-key">' + esc(key || '（无 key）') +
          (confidence !== null ? ' <span class="conf-track"><span class="conf-fill" style="width:' + Math.max(0, Math.min(100, confidence * 100)) + '%"></span></span> <span class="mono" style="font-size:10px">' + esc(confidence.toFixed(2)) + '</span>' : '') +
          (status ? ' <span class="badge badge-' + (status === 'active' ? 'succeeded' : 'muted') + '" style="height:15px;font-size:10px">' + esc(status) + '</span>' : '') +
          '</div>' +
          '<div class="pi-val">' + esc(value === undefined ? jsonText(item) : value) + '</div>' +
          '<div class="pi-meta">' + esc(updated ? relTime(updated) + ' · ' : '') + esc(item.source || '') + '</div></div>' +
          (key ? '<button type="button" class="btn sm danger" data-forget-key="' + esc(key) + '">遗忘</button>' : '') +
          '</div>';
      }).join('');
    } else if (kind === 'timeline') {
      box.innerHTML = '<ul class="timeline-list">' + list.map(function (item) {
        var text = item.text || item.summary || item.content || item.event || item.value || '';
        var ts = item.ts || item.created_at || item.time;
        return '<li class="timeline-item">' +
          '<div class="tl-time">' + esc(fmtTs(ts)) + '</div>' +
          '<div class="tl-text">' + esc(text) + '</div>' +
          '<div class="tl-meta">' + esc(item.character || '') + (item.kind || item.type ? ' · ' + esc(item.kind || item.type) : '') +
          (item.source ? ' · ' + esc(item.source) : '') + '</div></li>';
      }).join('') + '</ul>';
    } else {
      box.innerHTML = '<div class="turn-list">' + list.map(function (item) {
        var role = String(item.role || item.speaker || 'user').toLowerCase();
        var roleCls = role === 'assistant' || role === 'ai' ? 'assistant' : (role === 'system' ? 'system' : 'user');
        var text = item.text || item.content || item.message || (item.value !== undefined ? item.value : '');
        var ts = item.ts || item.created_at || item.time;
        return '<div class="turn"><div class="turn-head">' +
          '<span class="turn-role ' + roleCls + '">' + esc(role) + '</span>' +
          '<span class="turn-time">' + esc(ts ? relTime(ts) : '') + '</span></div>' +
          '<div class="turn-text">' + esc(text) + '</div></div>';
      }).join('') + '</div>';
    }
  }

  function forgetMemory(key) {
    var userId = state.memory.selected;
    if (!userId) { toast('尚未选择用户', 'error'); return; }
    confirmDialog({
      title: '遗忘记忆',
      html: '确认对用户 <strong>' + esc(userId) + '</strong> 遗忘键 <strong>' + esc(key) + '</strong>？<br>' +
        '<span class="hint">POST /api/memory/forget · 软删除，审计中留痕。</span>',
      okText: '遗忘',
      danger: true
    }).then(function (ok) {
      if (!ok) return;
      return api('/api/memory/forget', { method: 'POST', body: { user_id: userId, key: key } }).then(function () {
        toast('已遗忘「' + key + '」', 'success');
        return Promise.all([loadMemoryUser(userId), loadCandidates()]);
      }, function (err) {
        toast('遗忘失败：' + errorText(err), 'error');
      });
    });
  }

  /* ============================================================
   * 审计视图
   * ============================================================ */

  function renderAudit(root) {
    root.innerHTML = auditShell();
    bindAudit();
    return loadAudit();
  }

  function auditShell() {
    var tabs = [['', '全部'], ['image', 'image'], ['voice', 'voice'], ['memory', 'memory'], ['tool', 'tool']].map(function (pair) {
      return '<button type="button" class="tab' + (state.audit.kind === pair[0] ? ' active' : '') + '" data-kind="' + esc(pair[0]) + '">' + esc(pair[1]) + '</button>';
    }).join('');
    return '' +
      '<div class="view">' +
      '<div class="view-head"><div class="vh-main">' +
      '<h2>审计 <span class="hint">GET /api/audit</span></h2>' +
      '<p class="view-sub">媒体生成 / 记忆写入 / 工具调用留痕 · 结果与错误</p></div>' +
      '<div class="view-actions"><button type="button" class="btn sm" id="audit-refresh"><span class="refresh-glyph">↻</span> 刷新</button></div>' +
      '</div>' +
      '<div class="card toolbar">' +
      '<div class="tabs" id="audit-tabs">' + tabs + '</div>' +
      '<div class="sep"></div>' +
      '<label class="hint" for="audit-limit">条数</label>' +
      '<select class="input" id="audit-limit" style="width:88px">' +
      [100, 200, 500].map(function (n) {
        return '<option value="' + n + '"' + (state.audit.limit === n ? ' selected' : '') + '>' + n + '</option>';
      }).join('') + '</select>' +
      '<div class="result-count" id="audit-count">加载中…</div>' +
      '</div>' +
      '<div class="card" id="audit-table">' + tableSkeleton(10) + '</div>' +
      '</div>';
  }

  function bindAudit() {
    var tabs = $('#audit-tabs');
    if (tabs) {
      tabs.addEventListener('click', function (e) {
        var btn = e.target.closest ? e.target.closest('[data-kind]') : null;
        if (!btn) return;
        state.audit.kind = btn.getAttribute('data-kind');
        $$('.tab', tabs).forEach(function (t) { t.classList.toggle('active', t === btn); });
        loadAudit();
      });
    }
    var limit = $('#audit-limit');
    if (limit) {
      limit.addEventListener('change', function () {
        state.audit.limit = Number(limit.value) || 100;
        loadAudit();
      });
    }
    var refresh = $('#audit-refresh');
    if (refresh) {
      refresh.addEventListener('click', function () {
        refresh.classList.add('spinning');
        loadAudit().then(function () { refresh.classList.remove('spinning'); });
      });
    }
    var tableBox = $('#audit-table');
    if (tableBox) {
      tableBox.addEventListener('click', function (e) {
        var link = e.target.closest ? e.target.closest('[data-goto-task]') : null;
        if (link) {
          e.preventDefault();
          state.pendingTaskId = link.getAttribute('data-goto-task');
          location.hash = '#/tasks';
          return;
        }
        var row = e.target.closest ? e.target.closest('[data-audit-idx]') : null;
        if (!row) return;
        var idx = row.getAttribute('data-audit-idx');
        var detailRow = tableBox.querySelector('[data-audit-detail="' + idx + '"]');
        if (detailRow) detailRow.hidden = !detailRow.hidden;
      });
    }
  }

  function loadAudit() {
    var box = $('#audit-table');
    var count = $('#audit-count');
    if (box) box.innerHTML = tableSkeleton(10);
    if (count) count.textContent = '加载中…';
    var qs = new URLSearchParams();
    qs.set('limit', String(state.audit.limit));
    if (state.audit.kind) qs.set('kind', state.audit.kind);
    return api('/api/audit?' + qs.toString()).then(function (data) {
      var list = asList(data, ['audit', 'records']).filter(function (a) { return a && typeof a === 'object'; });
      state.audit.items = list;
      paintAudit(list);
      if (count) count.textContent = list.length + ' 条';
    }, function (err) {
      if (box) {
        box.innerHTML = '<div class="card-body">' + errorBoxHTML(err, '审计加载失败', '重试') + '</div>';
        bindRetry(box, loadAudit);
      }
      if (count) count.textContent = '加载失败';
      toast('审计加载失败：' + errorText(err), 'error');
    });
  }

  function paintAudit(list) {
    var box = $('#audit-table');
    if (!box) return;
    if (!list.length) {
      box.innerHTML = emptyHTML('暂无审计记录', 'kind = ' + (state.audit.kind || 'all'));
      return;
    }
    var rows = list.map(function (a, idx) {
      var result = String(a.result || '').toLowerCase();
      var ok = result === 'ok' || result === 'success';
      var detail = a.detail !== undefined && a.detail !== null ? jsonText(a.detail) : '';
      return '<tr class="clickable" data-audit-idx="' + idx + '">' +
        '<td class="cell-num">' + esc(fmtTimeShort(a.ts)) + '</td>' +
        '<td class="cell-id">' + esc(a.actor || '—') + '</td>' +
        '<td>' + esc(a.character || '—') + '</td>' +
        '<td class="cell-strong mono" style="font-size:11px">' + esc(a.action || '—') + '</td>' +
        '<td><span class="badge badge-' + (ok ? 'ok' : (result ? 'fail' : 'muted')) + '">' + esc(a.result || '—') + '</span></td>' +
        '<td class="cell-id">' + (a.task_id ? esc(a.task_id) : '—') + '</td>' +
        '<td><span class="hint">' + (a.error ? '错误' : (detail ? '展开' : '—')) + '</span></td>' +
        '</tr>' +
        '<tr class="audit-detail-row" data-audit-detail="' + idx + '" hidden><td colspan="7">' +
        (a.error ? '<div class="err-block" style="margin-bottom:8px">' + esc(errorText(a.error)) + '</div>' : '') +
        (detail ? '<pre class="json">' + esc(detail) + '</pre>' : '<div class="hint">无 detail</div>') +
        (a.task_id ? '<div style="margin-top:8px"><a href="#/tasks" data-goto-task="' + esc(a.task_id) + '">查看任务 ' + esc(a.task_id) + ' →</a></div>' : '') +
        '</td></tr>';
    }).join('');

    box.innerHTML = '<div class="table-wrap"><table class="table"><thead><tr>' +
      '<th>时间</th><th>Actor</th><th>角色</th><th>Action</th><th>结果</th><th>任务</th><th></th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table></div>';
  }

  /* ============================================================
   * 设置视图
   * ============================================================ */

  function renderSettings(root) {
    root.innerHTML = settingsShell();
    bindSettings();
    return loadSettings();
  }

  function settingsShell() {
    return '' +
      '<div class="view">' +
      '<div class="view-head"><div class="vh-main">' +
      '<h2>设置 <span class="hint">PUT /api/media/config</span></h2>' +
      '<p class="view-sub">媒体闲置休眠、启动预热与 Core 连接信息</p></div>' +
      '<div class="view-actions"><button type="button" class="btn sm" id="set-refresh"><span class="refresh-glyph">↻</span> 刷新</button></div>' +
      '</div>' +
      '<div id="set-error"></div>' +
      '<div class="settings-grid">' +
      '<section class="card">' +
      '<div class="card-head"><h3>媒体配置</h3><span class="card-sub">PUT /api/media/config</span></div>' +
      '<div class="card-body">' +
      '<form id="settings-form">' +
      '<div class="field"><label for="set-idle">闲置休眠 idle_sleep_sec</label>' +
      '<div class="field-row"><input type="number" class="input mono" id="set-idle" min="0" step="30" style="width:150px" value="600">' +
      '<span class="suffix">秒</span></div>' +
      '<div class="field-hint">on_demand 模式下 ComfyUI 闲置多久后休眠，契约默认 600。</div></div>' +
      '<div class="field"><label class="check"><input type="checkbox" id="set-prewarm"> 启动时预热媒体服务（prewarm_on_start）</label>' +
      '<div class="field-hint">写入 Core 配置，下次启动生效。<span id="prewarm-note"></span></div></div>' +
      '<div class="form-actions">' +
      '<button type="submit" class="btn primary" id="set-save">保存配置</button>' +
      '<span class="save-state" id="set-state"></span>' +
      '</div></form>' +
      '</div></section>' +
      '<section class="card">' +
      '<div class="card-head"><h3>Core 连接</h3><span class="card-sub">同源 /api/*</span></div>' +
      '<div class="card-body"><table class="kv-table" id="conn-table">' +
      '<tr><td class="k">页面地址</td><td class="v" id="conn-page">—</td></tr>' +
      '<tr><td class="k">API 前缀</td><td class="v">/api/*</td></tr>' +
      '<tr><td class="k">鉴权</td><td class="v">回环地址免鉴权（Bearer 仅远程）</td></tr>' +
      '<tr><td class="k">Core 状态</td><td class="v" id="conn-core">—</td></tr>' +
      '<tr><td class="k">Core 运行时长</td><td class="v" id="conn-uptime">—</td></tr>' +
      '<tr><td class="k">媒体模式</td><td class="v" id="conn-mode">—</td></tr>' +
      '<tr><td class="k">idle_sleep_sec</td><td class="v" id="conn-idle">—</td></tr>' +
      '<tr><td class="k">LangBot</td><td class="v" id="conn-langbot">—</td></tr>' +
      '</table></div>' +
      '</section>' +
      '</div>' +
      '<details class="raw"><summary>原始 /api/status 响应</summary>' +
      '<div class="raw-body"><pre class="json" id="raw-status">加载中…</pre></div></details>' +
      '</div>';
  }

  function bindSettings() {
    var refresh = $('#set-refresh');
    if (refresh) {
      refresh.addEventListener('click', function () {
        refresh.classList.add('spinning');
        loadSettings().then(function () { refresh.classList.remove('spinning'); });
      });
    }
    var form = $('#settings-form');
    if (form) {
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        saveSettings();
      });
    }
  }

  function loadSettings() {
    return api('/api/status').then(function (data) {
      var st = normStatus(data);
      state.status = st;
      paintChrome(st);
      var idle = $('#set-idle');
      if (idle) idle.value = String(st.idleSleep);
      var prewarm = $('#set-prewarm');
      if (prewarm) prewarm.checked = !!st.prewarmOnStart;
      var note = $('#prewarm-note');
      if (note) {
        note.textContent = st.hasPrewarm
          ? '当前值：' + (st.prewarmOnStart ? 'true' : 'false')
          : '（/api/status 未返回该字段，按 false 显示；保存后以 Core 响应为准）';
      }
      var raw = $('#raw-status');
      if (raw) raw.textContent = jsonText(data);
      fillConnTable(st);
      var errBox = $('#set-error');
      if (errBox) errBox.innerHTML = '';
    }, function (err) {
      var errBox = $('#set-error');
      if (errBox) {
        errBox.innerHTML = errorBoxHTML(err, 'Core 配置读取失败', '重试');
        bindRetry(errBox, loadSettings);
      }
      var raw = $('#raw-status');
      if (raw) raw.textContent = '加载失败：' + errorText(err);
      fillConnTable(null);
      toast('设置读取失败：' + errorText(err), 'error');
    });
  }

  function fillConnTable(st) {
    var page = $('#conn-page');
    if (page) page.textContent = location.origin + location.pathname + location.search;
    var core = $('#conn-core');
    if (core) core.textContent = st ? (st.coreOk ? 'ok · 在线' : 'ok=false · 异常') : '不可达';
    var uptime = $('#conn-uptime');
    if (uptime) uptime.textContent = st ? fmtUptime(st.uptime) : '—';
    var mode = $('#conn-mode');
    if (mode) mode.textContent = st ? st.mode + (MODE_META[st.mode] ? '（' + MODE_META[st.mode].name + '）' : '') : '—';
    var idle = $('#conn-idle');
    if (idle) idle.textContent = st ? st.idleSleep + ' 秒' : '—';
    var langbot = $('#conn-langbot');
    if (langbot) langbot.textContent = st ? (st.langbot === null ? '未返回' : (st.langbot ? '可达' : '不可达')) : '—';
  }

  function saveSettings() {
    var idleInput = $('#set-idle');
    var prewarmInput = $('#set-prewarm');
    var idle = Number(idleInput ? idleInput.value : NaN);
    if (!isFinite(idle) || idle < 0) {
      toast('idle_sleep_sec 必须是非负整数', 'error');
      return;
    }
    var body = {
      idle_sleep_sec: Math.round(idle),
      prewarm_on_start: !!(prewarmInput && prewarmInput.checked)
    };
    var btn = $('#set-save');
    var stateEl = $('#set-state');
    if (btn) { btn.disabled = true; btn.textContent = '保存中…'; }
    if (stateEl) stateEl.textContent = 'PUT /api/media/config';
    api('/api/media/config', { method: 'PUT', body: body }).then(function (data) {
      toast('媒体配置已保存' + (data && data.ok === false ? '（接口返回失败：' + jsonText(data) + '）' : ''), data && data.ok === false ? 'error' : 'success');
      if (stateEl) stateEl.textContent = '已保存 · ' + new Date().toLocaleTimeString('zh-CN', { hour12: false });
      return loadSettings();
    }, function (err) {
      if (stateEl) stateEl.textContent = '保存失败';
      toast('媒体配置保存失败：' + errorText(err), 'error');
      var errBox = $('#set-error');
      if (errBox) errBox.innerHTML = errorBoxHTML(err, 'PUT /api/media/config 失败', '重试');
      bindRetry($('#set-error'), saveSettings);
    }).then(function () {
      if (btn) { btn.disabled = false; btn.textContent = '保存配置'; }
    });
  }

  /* ============================================================
   * 路由
   * ============================================================ */

  var RENDERERS = {
    overview: renderOverview,
    tasks: renderTasks,
    characters: renderCharacters,
    memory: renderMemory,
    audit: renderAudit,
    settings: renderSettings
  };

  function currentRoute() {
    var raw = String(location.hash || '').replace(/^#\/?/, '');
    var name = raw.split('?')[0].split('/')[0];
    return RENDERERS[name] ? name : 'overview';
  }

  function setActiveNav(view) {
    $$('#nav .nav-item').forEach(function (a) {
      a.classList.toggle('active', a.getAttribute('data-view') === view);
    });
  }

  function route() {
    var view = currentRoute();
    state.view = view;
    clearTimers();
    if (view !== 'overview') closeTaskDetail();
    setActiveNav(view);
    var meta = VIEW_META[view] || VIEW_META.overview;
    var title = $('#page-title');
    var crumb = $('#crumb');
    if (title) title.textContent = meta.title;
    if (crumb) crumb.textContent = meta.crumb;
    document.title = meta.title + ' · Möbius Console';

    var root = $('#view-root');
    root.scrollTop = 0;
    root.innerHTML = '';
    var result;
    try {
      result = RENDERERS[view](root);
    } catch (err) {
      console.error(err);
      root.innerHTML = pageErrorHTML(err);
      bindRetry(root, route);
      return;
    }
    Promise.resolve(result).catch(function (err) {
      console.error(err);
      root.innerHTML = pageErrorHTML(err);
      bindRetry(root, route);
      toast('页面加载失败：' + errorText(err), 'error');
    });
  }

  /* ============================================================
   * 初始化
   * ============================================================ */

  function bindStatic() {
    var refreshBtn = $('#global-refresh');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', function () {
        refreshBtn.classList.add('spinning');
        route();
        setTimeout(function () { refreshBtn.classList.remove('spinning'); }, 600);
      });
    }
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape') return;
      if (state.drawerTaskId) closeTaskDetail();
    });
    window.addEventListener('hashchange', route);

    var root = $('#view-root');
    if (root) {
      root.addEventListener('error', function (e) {
        var target = e.target;
        if (target && target.tagName === 'IMG') {
          target.style.visibility = 'hidden';
        }
      }, true);
    }
  }

  function init() {
    bindStatic();

    var clock = $('#clock');
    var tickClock = function () {
      if (!clock) return;
      var d = new Date();
      clock.textContent = pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds());
    };
    tickClock();
    state.clockTimer = setInterval(tickClock, 1000);

    route();
    if (state.view !== 'overview') refreshChrome(false);
    refreshPendingBadge();

    state.chromeTimer = setInterval(function () {
      if (state.view === 'overview') return;
      refreshChrome(true);
    }, CHROME_POLL_MS);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
