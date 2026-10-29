#!/usr/bin/env node
/**
 * Möbius Console 冒烟测试（真实 Chrome + CDP，无 npm 依赖）。
 *
 * 前置：先启动 mock 服务器
 *   python mobius/core/console/_mock/server.py --port 8911
 * 错误态（可选）：
 *   python mobius/core/console/_mock/server.py --port 8912 --fail-api
 *
 * 运行：
 *   node mobius/core/console/_mock/smoke.mjs --base http://127.0.0.1:8911
 *
 * 覆盖：概览 / 任务 / 角色 / 记忆 / 审计 / 设置 六视图渲染、数据填充、模式切换、
 * 唤醒、任务详情抽屉、过滤、角色保存回读、候选审批、遗忘确认、审计展开与 kind 过滤、
 * 配置保存回读；另外验证 --fail-api 服务下的可见错误提示。
 * 截图输出到 _mock/screenshots/，报告写到 _mock/smoke-report.json。
 */

import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const args = parseArgs(process.argv.slice(2));
const BASE = (args.base || 'http://127.0.0.1:8911').replace(/\/$/, '');
const FAIL_BASE = (args['fail-base'] || 'http://127.0.0.1:8912').replace(/\/$/, '');
const OUT_DIR = path.resolve(args.out || path.join(__dirname, 'screenshots'));
const REPORT = path.join(__dirname, 'smoke-report.json');
const CHROME = args.chrome || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const DEBUG_PORT = Number(args.port || 9333);

const checks = [];
const screenshots = [];
const exceptions = [];
const consoleErrors = [];
let ws = null;
let cdp = null;
let chromeProc = null;
let targetId = null;

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (!a.startsWith('--')) continue;
    const key = a.slice(2);
    const next = argv[i + 1];
    if (next && !next.startsWith('--')) { out[key] = next; i += 1; }
    else out[key] = true;
  }
  return out;
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

function check(name, ok, detail) {
  checks.push({ name, ok: !!ok, detail: detail === undefined ? '' : String(detail) });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail !== undefined ? '  — ' + detail : ''}`);
}

async function waitFor(expr, desc, timeout = 9000) {
  const started = Date.now();
  let lastError = '';
  while (Date.now() - started < timeout) {
    try {
      const value = await evaluate(`(function(){ try { return (${expr}); } catch (e) { return '__ERR__:' + e.message; } })()`);
      if (value === true) return true;
      if (typeof value === 'string' && value.startsWith('__ERR__:')) lastError = value;
    } catch (err) {
      lastError = err.message;
    }
    await sleep(120);
  }
  throw new Error(`等待超时 ${desc} (${expr}) ${lastError ? '· ' + lastError : ''}`);
}

async function evaluate(expression) {
  if (!cdp) throw new Error('CDP 未连接');
  const res = await cdp.send('Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise: false,
    userGesture: true
  });
  if (res.exceptionDetails) {
    const d = res.exceptionDetails;
    throw new Error((d.exception && (d.exception.description || d.exception.value)) || d.text || 'evaluate 异常');
  }
  return res.result ? res.result.value : undefined;
}

async function waitToast(text, timeout) {
  return waitFor(
    `(function(){var t=Array.from(document.querySelectorAll('.toast'));return t.some(function(x){return x.innerText.indexOf(${JSON.stringify(text)})>=0;});})()`,
    `toast 包含「${text}」`,
    timeout || 9000
  );
}

async function waitNoToasts(timeout = 8000) {
  try {
    await waitFor(`document.querySelectorAll('.toast').length === 0`, 'toast 全部消失', timeout);
  } catch (_) {
    /* 截图允许残留，不视为失败 */
  }
}

async function shot(name) {
  const res = await cdp.send('Page.captureScreenshot', { format: 'png', fromSurface: true });
  const file = path.join(OUT_DIR, name + '.png');
  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.writeFileSync(file, Buffer.from(res.data, 'base64'));
  screenshots.push(path.relative(path.join(__dirname, 'screenshots'), file));
  console.log(`SHOT  ${file}`);
}

async function gotoHash(hash) {
  await evaluate(`location.hash = ${JSON.stringify(hash)}; true`);
  await sleep(180);
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
      const handlers = this.listeners.get(msg.method) || [];
      handlers.forEach((fn) => { try { fn(msg.params); } catch (_) { /* ignore */ } });
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
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`CDP 超时: ${method}`));
        }
      }, 30000);
    });
  }
}

async function httpJson(url, options) {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(`${url} -> HTTP ${res.status}`);
  return res.json();
}

async function findPageTarget() {
  const started = Date.now();
  while (Date.now() - started < 15000) {
    try {
      const list = await httpJson(`http://127.0.0.1:${DEBUG_PORT}/json/list`);
      const page = list.find((t) => t.type === 'page');
      if (page) return page;
    } catch (_) { /* chrome 还没起来 */ }
    await sleep(250);
  }
  throw new Error('Chrome 远程调试端口未就绪');
}

async function launchChrome() {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'mobius-smoke-'));
  chromeProc = spawn(CHROME, [
    '--headless=new',
    '--disable-gpu',
    '--no-sandbox',
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-extensions',
    '--hide-scrollbars',
    '--mute-audio',
    `--remote-debugging-port=${DEBUG_PORT}`,
    '--remote-allow-origins=*',
    `--user-data-dir=${profile}`,
    'about:blank'
  ], { stdio: 'ignore' });
  chromeProc.on('error', (err) => { console.error('Chrome 启动失败:', err.message); });

  const page = await findPageTarget();
  targetId = page.id;
  ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve, { once: true });
    ws.addEventListener('error', () => reject(new Error('WebSocket 连接失败')), { once: true });
  });
  cdp = new CDP(ws);

  cdp.on('Runtime.exceptionThrown', (params) => {
    const d = params.exceptionDetails || {};
    const text = (d.exception && (d.exception.description || d.exception.value)) || d.text || 'unknown exception';
    exceptions.push(String(text).split('\n')[0]);
  });
  cdp.on('Runtime.consoleAPICalled', (params) => {
    if (params.type === 'error') {
      const text = (params.args || []).map((a) => a.value !== undefined ? a.value : (a.description || '')).join(' ');
      consoleErrors.push(String(text).split('\n')[0]);
    }
  });

  await cdp.send('Runtime.enable');
  await cdp.send('Page.enable');
  await cdp.send('Log.enable');
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false
  });
  await cdp.send('Emulation.setScrollbarsHidden', { hidden: false }).catch(() => {});
}

async function navigate(url) {
  await cdp.send('Page.navigate', { url });
  await sleep(500);
  await waitFor(`document.readyState === 'complete'`, 'document.readyState=complete', 15000);
}

async function waitApp() {
  await waitFor(`!!document.querySelector('#view-root .view')`, 'SPA 视图渲染完成', 15000);
}

async function runOverview() {
  console.log('\n== 概览 ==');
  await waitApp();
  await waitFor(`document.querySelectorAll('#ov-stats .stat').length === 5 && !!document.querySelector('#ov-recent table tbody tr')`, '概览数据渲染');
  const statsText = await evaluate(`document.querySelector('#ov-stats').innerText`);
  check('概览 5 个统计卡', (statsText.match(/今日任务/g) || []).length === 1, statsText.replace(/\n/g, ' | '));
  check('概览统计含 今日任务/今日失败/活跃任务/Core 运行时长/LangBot',
    ['今日任务', '今日失败', '活跃任务', 'Core 运行时长', 'LangBot'].every((s) => statsText.includes(s)));
  const svcCount = await evaluate(`document.querySelectorAll('#ov-services .svc').length`);
  check('概览 4 个服务健康灯', svcCount === 4, 'svc=' + svcCount);
  const modeButtons = await evaluate(`Array.from(document.querySelectorAll('#ov-mode .mode-btn')).map(function(b){return b.getAttribute('data-mode');}).join(',')`);
  check('媒体模式三档按钮', modeButtons === 'off,on_demand,prewarm', modeButtons);
  const activeMode = await evaluate(`(document.querySelector('#ov-mode .mode-btn.active')||{getAttribute:function(){return '';}}).getAttribute('data-mode')`);
  check('当前模式高亮 on_demand', activeMode === 'on_demand', activeMode);
  const recentRows = await evaluate(`document.querySelectorAll('#ov-recent tbody tr').length`);
  check('最近任务 ≥ 10 行', recentRows >= 10, 'rows=' + recentRows);
  const failedText = await evaluate(`document.querySelector('#ov-failed').innerText`);
  check('失败任务显示错误原因', failedText.includes('comfyui_unavailable') && failedText.includes('kokoro_unreachable'), failedText.replace(/\n/g, ' | ').slice(0, 120));
  const cssBg = await evaluate(`getComputedStyle(document.body).backgroundColor`);
  check('深色主题样式已加载', cssBg === 'rgb(10, 14, 21)', cssBg);
  await shot('01-overview');

  // 模式切换
  await evaluate(`document.querySelector('#ov-mode .mode-btn[data-mode="prewarm"]').click(); true`);
  await waitFor(`document.querySelector('#foot-mode').innerText.indexOf('prewarm') >= 0`, '侧栏回显 prewarm', 12000);
  const footMode = await evaluate(`document.querySelector('#foot-mode').innerText`);
  check('切换 prewarm 后侧栏回显', footMode.includes('prewarm'), footMode);
  await evaluate(`document.querySelector('#ov-mode .mode-btn[data-mode="on_demand"]').click(); true`);
  await waitFor(`document.querySelector('#foot-mode').innerText.indexOf('on_demand') >= 0`, '侧栏回显 on_demand', 12000);
  check('切回 on_demand 成功', true);

  // 唤醒
  await evaluate(`document.querySelector('#ov-wake').click(); true`);
  await waitToast('已唤醒', 12000);
  await waitFor(`document.querySelector('#ov-services .svc .svc-state').innerText.indexOf('ready') >= 0`, 'ComfyUI 状态变 ready', 9000);
  check('一键唤醒后 ComfyUI ready', true);
  await shot('02-overview-awake');

  // 休眠
  await evaluate(`document.querySelector('#ov-sleep').click(); true`);
  await waitToast('已发送休眠指令');
  await waitFor(`document.querySelector('#ov-services .svc .svc-state').innerText.indexOf('down') >= 0`, 'ComfyUI 状态变 down');
  check('休眠后 ComfyUI down', true);

  // off 模式下唤醒应被拒绝（前端预检 + Core 409 文案）
  await evaluate(`document.querySelector('#ov-mode .mode-btn[data-mode="off"]').click(); true`);
  await waitFor(`document.querySelector('#foot-mode').innerText.indexOf('off') >= 0`, '侧栏回显 off', 12000);
  await evaluate(`document.querySelector('#ov-wake').click(); true`);
  await waitToast('唤醒会被 Core 拒绝', 5000);
  check('off 模式下唤醒有明确拒绝提示', true);
  await evaluate(`document.querySelector('#ov-mode .mode-btn[data-mode="on_demand"]').click(); true`);
  await waitFor(`document.querySelector('#foot-mode').innerText.indexOf('on_demand') >= 0`, '恢复 on_demand', 12000);
}

async function runTasks() {
  console.log('\n== 任务 ==');
  await waitNoToasts();
  await gotoHash('#/tasks');
  await waitFor(`!!document.querySelector('#tasks-table table tbody tr')`, '任务表格渲染');
  const rows = await evaluate(`document.querySelectorAll('#tasks-table tbody tr').length`);
  check('任务列表 ≥ 10 行', rows >= 10, 'rows=' + rows);
  const firstId = await evaluate(`document.querySelector('#tasks-table tbody tr').getAttribute('data-task-id')`);
  const countText = await evaluate(`document.querySelector('#tasks-count').innerText`);
  check('任务总数显示', /\d+ 条/.test(countText), countText);
  await shot('03-tasks');

  // 详情抽屉
  await evaluate(`document.querySelector('#tasks-table tbody tr').click(); true`);
  await waitFor(`!!document.querySelector('#task-drawer.open') && !!document.querySelector('#drawer-body .detail-statusline')`, '任务详情抽屉打开');
  const drawerTitle = await evaluate(`document.querySelector('#drawer-title').innerText`);
  check('抽屉标题 = 点击的任务 ID', drawerTitle === firstId, drawerTitle + ' / ' + firstId);
  const drawerText = await evaluate(`document.querySelector('#drawer-body').innerText`);
  check('详情含 Prompt / 参数 / 结果 / 时间线', ['Prompt', '参数 params', '结果 result', '时间线'].every((s) => drawerText.includes(s)));
  await shot('04-task-detail');
  await evaluate(`document.querySelector('#drawer-close').click(); true`);
  await waitFor(`!document.querySelector('#task-drawer')`, '抽屉关闭');

  // 过滤 failed
  await evaluate(`(function(){var el=document.getElementById('f-status'); el.value='failed'; el.dispatchEvent(new Event('change',{bubbles:true})); return true;})()`);
  await waitFor(`document.querySelectorAll('#tasks-table tbody tr').length > 0 && Array.from(document.querySelectorAll('#tasks-table tbody tr')).every(function(tr){return tr.innerText.indexOf('失败')>=0;})`, '任务过滤 failed');
  const failedRows = await evaluate(`document.querySelectorAll('#tasks-table tbody tr').length`);
  check('status=failed 过滤生效', failedRows >= 1 && failedRows < rows, 'failed rows=' + failedRows);
  await shot('05-tasks-filtered');
  // 恢复全部
  await evaluate(`(function(){var el=document.getElementById('f-status'); el.value=''; el.dispatchEvent(new Event('change',{bubbles:true})); return true;})()`);
  await waitFor(`document.querySelectorAll('#tasks-table tbody tr').length === ${rows}`, '恢复全部任务');

  // 取消排队中的任务（POST /api/tasks/{id}/cancel）
  await evaluate(`document.querySelector('#tasks-table tbody tr[data-task-id="t_9012cd34"]').click(); true`);
  await waitFor(`!!document.querySelector('#task-drawer.open') && !!document.querySelector('#drawer-cancel')`, '排队任务详情打开（含取消按钮）');
  await evaluate(`document.querySelector('#drawer-cancel').click(); true`);
  await waitFor(`!!document.querySelector('.modal-overlay')`, '取消任务确认弹窗');
  await evaluate(`document.querySelector('[data-modal="ok"]').click(); true`);
  await waitToast('取消指令已提交', 12000);
  await waitFor(`document.querySelector('#drawer-body .detail-statusline').innerText.indexOf('已取消') >= 0`, '任务状态变为已取消', 12000);
  check('取消任务（POST /api/tasks/{id}/cancel）生效', true);
  await evaluate(`document.querySelector('#drawer-close').click(); true`);
  await waitFor(`!document.querySelector('#task-drawer')`, '抽屉关闭');
}

async function runCharacters() {
  console.log('\n== 角色 ==');
  await waitNoToasts();
  await gotoHash('#/characters');
  await waitFor(`!!document.querySelector('#char-form') && document.querySelectorAll('#char-list .char-item').length >= 3`, '角色列表与编辑器渲染');
  await waitFor(`!!document.querySelector('#const-body .const-content')`, '角色宪法加载');
  const charCount = await evaluate(`document.querySelectorAll('#char-list .char-item').length`);
  check('角色列表 ≥ 3', charCount >= 3, 'chars=' + charCount);
  const constText = await evaluate(`document.querySelector('#const-body').innerText`);
  check('角色宪法只读展示', constText.includes('核心人格') || constText.includes('角色规则'), constText.slice(0, 90).replace(/\n/g, ' | '));
  const loraRows = await evaluate(`document.querySelectorAll('#lora-body tr').length`);
  check('LoRA 行渲染', loraRows >= 1, 'loras=' + loraRows);
  await shot('06-characters');

  // LoRA 增删
  await evaluate(`document.querySelector('#lora-add').click(); true`);
  const afterAdd = await evaluate(`document.querySelectorAll('#lora-body tr').length`);
  check('添加 LoRA 行', afterAdd === loraRows + 1, 'rows=' + afterAdd);
  await evaluate(`document.querySelector('#lora-body tr:last-child .lora-del').click(); true`);
  const afterDel = await evaluate(`document.querySelectorAll('#lora-body tr').length`);
  check('删除 LoRA 行', afterDel === loraRows, 'rows=' + afterDel);

  // 保存并回读
  await evaluate(`document.getElementById('c-checkpoint').value = 'SmokeTest-v1.safetensors'; document.querySelector('#char-save').click(); true`);
  await waitToast('已保存，热生效', 12000);
  await gotoHash('#/tasks');
  await waitFor(`!!document.querySelector('#tasks-table table tbody tr')`, '离开角色视图');
  await gotoHash('#/characters');
  await waitFor(`!!document.querySelector('#char-form') && document.getElementById('c-checkpoint').value === 'SmokeTest-v1.safetensors'`, '保存后回读 checkpoint', 12000);
  check('角色 PUT 保存并回读成功', true);
  await shot('07-characters-saved');
}

async function runMemory() {
  console.log('\n== 记忆 ==');
  await waitNoToasts();
  await gotoHash('#/memory');
  await waitFor(`document.querySelectorAll('#mem-user-list .mem-user').length >= 3 && document.querySelectorAll('#cand-list .cand').length >= 1`, '用户列表与候选记忆渲染');
  const src = await evaluate(`document.querySelector('#mem-users-src').innerText`);
  check('用户列表降级来源提示（契约无 users 接口）', src.includes('聚合'), src);
  const userCount = await evaluate(`document.querySelectorAll('#mem-user-list .mem-user').length`);
  check('用户列表 ≥ 3', userCount >= 3, 'users=' + userCount);
  const candCount = await evaluate(`document.querySelectorAll('#cand-list .cand').length`);
  check('候选记忆 ≥ 1', candCount >= 1, 'candidates=' + candCount);
  await shot('08-memory');

  // 选用户
  await evaluate(`document.querySelector('#mem-user-list [data-user-id="qq:123"]').click(); true`);
  await waitFor(`document.querySelectorAll('#mem-profile .profile-item').length >= 3`, '用户 profile 渲染');
  const profileCount = await evaluate(`document.querySelectorAll('#mem-profile .profile-item').length`);
  const timelineCount = await evaluate(`document.querySelectorAll('#mem-timeline .timeline-item').length`);
  const turnCount = await evaluate(`document.querySelectorAll('#mem-turns .turn').length`);
  check('三栏：profile / timeline / turns', profileCount >= 3 && timelineCount >= 3 && turnCount >= 5,
    `profile=${profileCount} timeline=${timelineCount} turns=${turnCount}`);
  await shot('09-memory-user');

  // 审批候选
  const before = await evaluate(`document.querySelectorAll('#cand-list .cand').length`);
  await evaluate(`document.querySelector('#cand-list [data-cand-action="approve"]').click(); true`);
  await waitToast('已确认', 9000);
  await waitFor(`document.querySelectorAll('#cand-list .cand').length === ${before - 1}`, '候选审批后列表减少', 9000);
  check('候选记忆确认后移出待审', true);
  // 审批通过后，当前用户档案应追加一条（等前端刷新完成再取基线）
  await waitFor(`document.querySelectorAll('#mem-profile .profile-item').length >= 4`, '审批后档案刷新', 9000);

  // 遗忘
  const beforeForget = await evaluate(`document.querySelectorAll('#mem-profile .profile-item').length`);
  await evaluate(`document.querySelector('#mem-profile [data-forget-key]').click(); true`);
  await waitFor(`!!document.querySelector('.modal-overlay')`, '遗忘确认弹窗');
  await shot('10-memory-forget-confirm');
  await evaluate(`document.querySelector('[data-modal="ok"]').click(); true`);
  await waitToast('已遗忘', 9000);
  await waitFor(`document.querySelectorAll('#mem-profile .profile-item').length === ${beforeForget - 1}`, '遗忘后 profile 减少', 9000);
  check('遗忘（POST /api/memory/forget）生效', true);
}

async function runAudit() {
  console.log('\n== 审计 ==');
  await waitNoToasts();
  await gotoHash('#/audit');
  await waitFor(`document.querySelectorAll('#audit-table tbody tr[data-audit-idx]').length >= 10`, '审计表格渲染');
  const rows = await evaluate(`document.querySelectorAll('#audit-table tbody tr[data-audit-idx]').length`);
  check('审计列表渲染', rows >= 10, 'rows=' + rows);
  await shot('11-audit');

  await evaluate(`document.querySelector('#audit-table tbody tr[data-audit-idx]').click(); true`);
  await waitFor(`!document.querySelector('#audit-table tr[data-audit-detail="0"]').hidden`, '审计详情展开');
  check('审计行点击展开 detail', true);

  await evaluate(`document.querySelector('#audit-tabs [data-kind="voice"]').click(); true`);
  await waitFor(`document.querySelectorAll('#audit-table tbody tr[data-audit-idx]').length > 0 && Array.from(document.querySelectorAll('#audit-table tbody tr[data-audit-idx]')).every(function(tr){return tr.children[3].innerText.indexOf('voice')===0;})`, '审计 kind=voice 过滤');
  check('审计 kind 过滤生效', true);
  await shot('12-audit-voice');
  await evaluate(`document.querySelector('#audit-tabs [data-kind=""]').click(); true`);
}

async function runSettings() {
  console.log('\n== 设置 ==');
  await waitNoToasts();
  await gotoHash('#/settings');
  await waitFor(`!!document.getElementById('set-idle') && document.querySelector('#conn-core').innerText.indexOf('在线') >= 0`, '设置页数据加载');
  const idle = await evaluate(`document.getElementById('set-idle').value`);
  check('设置页回显 idle_sleep_sec', idle === '600', 'idle=' + idle);
  const connText = await evaluate(`document.querySelector('#conn-table').innerText`);
  check('Core 连接信息展示', connText.includes('127.0.0.1') && connText.includes('回环地址免鉴权'), connText.replace(/\n/g, ' | ').slice(0, 130));
  await shot('13-settings');

  await evaluate(`document.getElementById('set-idle').value = '300'; document.querySelector('#settings-form').requestSubmit(); true`);
  await waitToast('媒体配置已保存', 12000);
  await waitFor(`document.querySelector('#conn-idle').innerText.indexOf('300') >= 0`, '配置保存后回显 300 秒', 12000);
  check('PUT /api/media/config 保存并回显', true);
  await shot('14-settings-saved');
}

async function runErrorState() {
  console.log('\n== 后端不可用错误态（可选）==');
  let reachable = false;
  try {
    const res = await fetch(`${FAIL_BASE}/api/status`);
    reachable = res.status === 500;
  } catch (_) {
    reachable = false;
  }
  if (!reachable) {
    console.log('SKIP  未检测到 --fail-api mock 服务（' + FAIL_BASE + '），跳过错误态测试');
    return;
  }
  await navigate(`${FAIL_BASE}/console/`);
  await waitApp();
  await waitFor(`!!document.querySelector('#ov-error .error-box')`, '概览错误框出现', 12000);
  const toastText = await evaluate(`Array.from(document.querySelectorAll('.toast.error')).map(function(t){return t.innerText;}).join(' ')`);
  check('后端失败时顶部 toast 报错', toastText.includes('无法获取') || toastText.includes('状态获取失败'), toastText.slice(0, 100));
  check('后端失败时概览内联错误框', true);
  await shot('15-error-state');

  // 记忆候选失败也应有可见错误
  await gotoHash('#/memory');
  await waitFor(`!!document.querySelector('#cand-list .error-box')`, '候选加载失败错误框', 12000);
  check('记忆视图失败可见错误', true);
  await shot('16-error-memory');
}

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  console.log('启动 Chrome:', CHROME);
  await launchChrome();
  try {
    await navigate(`${BASE}/console/`);
    await runOverview();
    await runTasks();
    await runCharacters();
    await runMemory();
    await runAudit();
    await runSettings();
    await runErrorState();
  } finally {
    try { await cdp.send('Browser.close'); } catch (_) { /* ignore */ }
    try { ws.close(); } catch (_) { /* ignore */ }
    if (chromeProc) {
      try { chromeProc.kill(); } catch (_) { /* ignore */ }
    }
  }

  const realExceptions = exceptions.filter((e) => e && !/favicon/i.test(e));
  const realConsoleErrors = consoleErrors.filter((e) =>
    e && !/favicon/i.test(e) && !/Failed to load resource/i.test(e) && !/net::ERR/i.test(e));

  check('无未捕获 JS 异常', realExceptions.length === 0, realExceptions.join(' || '));
  check('无应用级 console.error', realConsoleErrors.length === 0, realConsoleErrors.join(' || '));

  const failed = checks.filter((c) => !c.ok);
  const report = {
    base: BASE,
    failBase: FAIL_BASE,
    timestamp: new Date().toISOString(),
    total: checks.length,
    passed: checks.length - failed.length,
    failed: failed.length,
    checks,
    screenshots,
    exceptions: realExceptions,
    consoleErrors: realConsoleErrors
  };
  fs.writeFileSync(REPORT, JSON.stringify(report, null, 2), 'utf8');
  console.log(`\n结果: ${report.passed}/${report.total} 通过，截图 ${screenshots.length} 张`);
  console.log('报告:', REPORT);
  if (failed.length) {
    console.log('\n失败项:');
    failed.forEach((f) => console.log(' - ' + f.name + (f.detail ? ' :: ' + f.detail : '')));
    process.exit(1);
  }
}

main().catch((err) => {
  console.error('冒烟测试中断:', err && err.stack ? err.stack : err);
  try { if (chromeProc) chromeProc.kill(); } catch (_) { /* ignore */ }
  process.exit(1);
});

