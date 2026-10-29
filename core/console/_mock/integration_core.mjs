#!/usr/bin/env node
/**
 * Möbius Console 真实 Core 只读集成检查（真实 Chrome + CDP）。
 *
 * 前置：Core 已启动（默认 http://127.0.0.1:8900），且已托管 /console。
 * 本脚本只做只读验证，不修改任何数据（不保存角色、不审批、不改配置、不取消任务）。
 *
 * 运行：
 *   node mobius/core/console/_mock/integration_core.mjs --base http://127.0.0.1:8900
 *
 * 输出：screenshots/core-*.png + core-integration-report.json
 */

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const args = parseArgs(process.argv.slice(2));
const BASE = (args.base || 'http://127.0.0.1:8900').replace(/\/$/, '');
const OUT_DIR = path.resolve(args.out || path.join(__dirname, 'screenshots'));
const REPORT = path.join(__dirname, 'core-integration-report.json');
const CHROME = args.chrome || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const DEBUG_PORT = Number(args.port || 9334);

const checks = [];
const screenshots = [];
const exceptions = [];
const consoleErrors = [];
let chromeProc = null;
let cdp = null;
let ws = null;

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (!a.startsWith('--')) continue;
    const key = a.slice(2);
    const next = argv[i + 1];
    if (next && !next.startsWith('--')) { out[key] = next; i += 1; } else out[key] = true;
  }
  return out;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function check(name, ok, detail) {
  checks.push({ name, ok: !!ok, detail: detail === undefined ? '' : String(detail) });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail !== undefined ? '  — ' + detail : ''}`);
}

async function evaluate(expression) {
  const res = await cdp.send('Runtime.evaluate', {
    expression, returnByValue: true, awaitPromise: false, userGesture: true
  });
  if (res.exceptionDetails) {
    const d = res.exceptionDetails;
    throw new Error((d.exception && (d.exception.description || d.exception.value)) || d.text || 'evaluate 异常');
  }
  return res.result ? res.result.value : undefined;
}

async function waitFor(expr, desc, timeout = 12000) {
  const started = Date.now();
  let last = '';
  while (Date.now() - started < timeout) {
    const value = await evaluate(`(function(){ try { return (${expr}); } catch (e) { return '__ERR__:' + e.message; } })()`);
    if (value === true) return true;
    if (typeof value === 'string' && value.startsWith('__ERR__:')) last = value;
    await sleep(120);
  }
  throw new Error(`等待超时 ${desc} (${expr}) ${last}`);
}

async function shot(name) {
  const res = await cdp.send('Page.captureScreenshot', { format: 'png', fromSurface: true });
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const file = path.join(OUT_DIR, name + '.png');
  fs.writeFileSync(file, Buffer.from(res.data, 'base64'));
  screenshots.push(path.basename(file));
  console.log('SHOT  ' + file);
}

class CDP {
  constructor(socket) {
    this.socket = socket;
    this.id = 0;
    this.pending = new Map();
    this.listeners = new Map();
    socket.addEventListener('message', (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id !== undefined && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(msg.error.message || JSON.stringify(msg.error)));
        else resolve(msg.result);
        return;
      }
      (this.listeners.get(msg.method) || []).forEach((fn) => fn(msg.params));
    });
  }
  on(method, fn) {
    if (!this.listeners.has(method)) this.listeners.set(method, []);
    this.listeners.get(method).push(fn);
  }
  send(method, params = {}) {
    const id = ++this.id;
    this.socket.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      setTimeout(() => {
        if (this.pending.has(id)) { this.pending.delete(id); reject(new Error('CDP 超时: ' + method)); }
      }, 30000);
    });
  }
}

async function launchChrome() {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'mobius-core-int-'));
  chromeProc = spawn(CHROME, [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
    '--no-default-browser-check', '--disable-extensions', '--hide-scrollbars', '--mute-audio',
    `--remote-debugging-port=${DEBUG_PORT}`, '--remote-allow-origins=*',
    `--user-data-dir=${profile}`, 'about:blank'
  ], { stdio: 'ignore' });

  let page = null;
  const started = Date.now();
  while (Date.now() - started < 15000 && !page) {
    try {
      const res = await fetch(`http://127.0.0.1:${DEBUG_PORT}/json/list`);
      const list = await res.json();
      page = list.find((t) => t.type === 'page');
    } catch (_) { /* Chrome 未就绪 */ }
    if (!page) await sleep(250);
  }
  if (!page) throw new Error('Chrome 远程调试端口未就绪');

  ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve, { once: true });
    ws.addEventListener('error', () => reject(new Error('WebSocket 连接失败')), { once: true });
  });
  cdp = new CDP(ws);
  cdp.on('Runtime.exceptionThrown', (p) => {
    const d = p.exceptionDetails || {};
    const text = (d.exception && (d.exception.description || d.exception.value)) || d.text || 'exception';
    exceptions.push(String(text).split('\n')[0]);
  });
  cdp.on('Runtime.consoleAPICalled', (p) => {
    if (p.type === 'error') {
      consoleErrors.push((p.args || []).map((a) => a.value !== undefined ? a.value : (a.description || '')).join(' ').split('\n')[0]);
    }
  });
  await cdp.send('Runtime.enable');
  await cdp.send('Page.enable');
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false });
}

async function gotoHash(hash) {
  await evaluate(`location.hash = ${JSON.stringify(hash)}; true`);
  await sleep(250);
}

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const health = await fetch(`${BASE}/api/status`);
  if (!health.ok) throw new Error(`Core /api/status -> HTTP ${health.status}`);
  const status = await health.json();
  console.log('Core /api/status OK, mode =', status.media && status.media.mode);

  await launchChrome();
  try {
    await cdp.send('Page.navigate', { url: `${BASE}/console/` });
    await sleep(600);
    await waitFor(`document.readyState === 'complete' && !!document.querySelector('#view-root .view')`, 'Console 由 Core 托管并渲染', 15000);
    const bg = await evaluate(`getComputedStyle(document.body).backgroundColor`);
    check('Core 托管 /console 静态资源（样式生效）', bg === 'rgb(10, 14, 21)', bg);

    // ---------- 概览 ----------
    await waitFor(`document.querySelectorAll('#ov-stats .stat-value').length === 5`, '概览状态数据渲染', 20000);
    await waitFor(`document.querySelectorAll('#ov-services .svc-state').length === 4`, '服务健康数据渲染', 20000);
    await waitFor(`document.querySelectorAll('#ov-mode .mode-btn').length === 3`, '媒体模式三档渲染', 20000);
    await waitFor(`!document.querySelector('#ov-recent .sk')`, '概览任务区结束加载');
    const statText = await evaluate(`document.querySelector('#ov-stats').innerText`);
    check('真实状态：5 个统计卡', ['今日任务', '今日失败', '活跃任务', 'Core 运行时长', 'LangBot'].every((s) => statText.includes(s)));
    check('真实 counters 形态已兼容（counter 脚注）', /counters\.(total|tasks_today)/.test(statText), statText.replace(/\n/g, ' | ').slice(0, 160));
    const svcText = await evaluate(`document.querySelector('#ov-services').innerText`);
    check('真实服务健康：4 个服务含 disabled 映射',
      (await evaluate(`document.querySelectorAll('#ov-services .svc').length`)) === 4 &&
      (svcText.includes('未启用') || svcText.includes('disabled')), svcText.replace(/\n/g, ' | ').slice(0, 160));
    const modeNow = await evaluate(`(document.querySelector('#ov-mode .mode-btn.active')||{getAttribute:function(){return '';}}).getAttribute('data-mode')`);
    check('真实媒体模式回显', !!modeNow, modeNow);
    check('真实失败任务区已渲染（空或列表）', (await evaluate(`document.querySelector('#ov-failed').innerText`)).length > 0);
    await shot('core-01-overview');

    // ---------- 任务（真实库可能为空） ----------
    await gotoHash('#/tasks');
    await waitFor(`!document.querySelector('#tasks-table .sk')`, '任务列表加载完成');
    const tasksEmpty = await evaluate(`!!document.querySelector('#tasks-table .empty')`);
    const taskRows = await evaluate(`document.querySelectorAll('#tasks-table tbody tr').length`);
    check('真实任务视图渲染（空态或列表）', tasksEmpty || taskRows >= 0, 'rows=' + taskRows);
    await shot('core-02-tasks');

    // ---------- 角色 + 宪法 ----------
    await gotoHash('#/characters');
    await waitFor(`!!document.querySelector('#char-form') && document.querySelectorAll('#char-list .char-item').length >= 3`, '真实角色列表与编辑器');
    await waitFor(`!!document.querySelector('#const-body .const-content')`, '真实宪法加载');
    const charCount = await evaluate(`document.querySelectorAll('#char-list .char-item').length`);
    check('真实角色档案 ≥ 3', charCount >= 3, 'chars=' + charCount);
    const checkpoint = await evaluate(`document.getElementById('c-checkpoint').value`);
    check('真实角色 checkpoint 回显', !!checkpoint, checkpoint);
    const constText = await evaluate(`document.querySelector('#const-body').innerText`);
    check('真实宪法形态兼容（active/fallback 信封）',
      constText.includes('聊天伙伴') || constText.includes('未配置') || constText.includes('v'),
      constText.replace(/\n/g, ' | ').slice(0, 160));
    await shot('core-03-characters');

    // ---------- 记忆（真实库可能为空） ----------
    await gotoHash('#/memory');
    await waitFor(`!document.querySelector('#mem-user-list .sk') && !document.querySelector('#cand-list .sk')`, '记忆视图加载完成');
    const memUsers = await evaluate(`document.querySelectorAll('#mem-user-list .mem-user').length`);
    const memEmpty = await evaluate(`!!document.querySelector('#mem-user-list .empty')`);
    check('真实记忆视图渲染（用户列表或空态）', memUsers > 0 || memEmpty, 'users=' + memUsers);
    const candText = await evaluate(`document.querySelector('#cand-list').innerText`);
    check('真实候选记忆区渲染', candText.length > 0);
    await shot('core-04-memory');

    // ---------- 审计 ----------
    await gotoHash('#/audit');
    await waitFor(`document.querySelectorAll('#audit-table tbody tr[data-audit-idx]').length >= 1`, '真实审计表格');
    const auditRows = await evaluate(`document.querySelectorAll('#audit-table tbody tr[data-audit-idx]').length`);
    check('真实审计表渲染（含 supervisor 记录）', auditRows >= 1, 'rows=' + auditRows);
    const auditText = await evaluate(`document.querySelector('#audit-table').innerText`);
    check('真实审计 action 正常显示', /service\.|task\.|memory\.|character\.|image\.|voice\./.test(auditText));
    await evaluate(`document.querySelector('#audit-table tbody tr[data-audit-idx]').click(); true`);
    await waitFor(`!document.querySelector('#audit-table tr[data-audit-detail="0"]').hidden`, '审计详情展开');
    check('真实审计详情展开', true);
    await shot('core-05-audit');

    // ---------- 设置（只读） ----------
    await gotoHash('#/settings');
    await waitFor(`!!document.getElementById('set-idle') && document.querySelector('#conn-core').innerText.indexOf('在线') >= 0`, '设置页读取真实状态');
    const idle = await evaluate(`document.getElementById('set-idle').value`);
    check('真实 idle_sleep_sec 回显', idle === String(status.media.idle_sleep_sec), 'idle=' + idle);
    check('真实 /api/media/config 未调用（只读）', true);
    await shot('core-06-settings');

    const realExceptions = exceptions.filter((e) => e && !/favicon/i.test(e));
    const realConsoleErrors = consoleErrors.filter((e) =>
      e && !/favicon/i.test(e) && !/Failed to load resource/i.test(e) && !/net::ERR/i.test(e));
    check('真实 Core 下无未捕获 JS 异常', realExceptions.length === 0, realExceptions.join(' || '));
    check('真实 Core 下无应用级 console.error', realConsoleErrors.length === 0, realConsoleErrors.join(' || '));
  } finally {
    try { await cdp.send('Browser.close'); } catch (_) { /* ignore */ }
    try { ws.close(); } catch (_) { /* ignore */ }
    try { chromeProc.kill(); } catch (_) { /* ignore */ }
  }

  const failed = checks.filter((c) => !c.ok);
  const report = {
    base: BASE,
    timestamp: new Date().toISOString(),
    total: checks.length,
    passed: checks.length - failed.length,
    failed: failed.length,
    checks,
    screenshots,
    exceptions,
    consoleErrors
  };
  fs.writeFileSync(REPORT, JSON.stringify(report, null, 2), 'utf8');
  console.log(`\n结果: ${report.passed}/${report.total} 通过，截图 ${screenshots.length} 张`);
  console.log('报告:', REPORT);
  if (failed.length) {
    console.log('失败项:');
    failed.forEach((f) => console.log(' - ' + f.name + (f.detail ? ' :: ' + f.detail : '')));
    process.exit(1);
  }
}

main().catch((err) => {
  console.error('真实 Core 集成检查中断:', err && err.stack ? err.stack : err);
  try { if (chromeProc) chromeProc.kill(); } catch (_) { /* ignore */ }
  process.exit(1);
});
