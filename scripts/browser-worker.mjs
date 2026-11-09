#!/usr/bin/env node
// JSON-lines worker, private persistent Playwright context, no CDP listener.
// Endpoint/field reference: Oh Story 7e26aa0708984ce81a41cd77ebfbbdf467c500b0 (MIT).
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import {createRequire} from 'node:module';

const require = createRequire(import.meta.url);
const root = process.env.FAGAO_DATA_DIR;
if (!root || !path.isAbsolute(root)) throw new Error('FAGAO_DATA_DIR must be absolute');
const profile = path.join(root, 'browser', 'heiyan');
const marker = path.join(root, 'browser', 'heiyan-connected.json');
const pageUrl = 'https://manage.zhangwenpindu.cn/books/booklist';
const apiUrl = 'https://ms.zhangwenpindu.cn';
let context, page;
let lastSuccess = fs.existsSync(marker);
for (const signal of ['SIGTERM', 'SIGINT']) {
  process.on(signal, async () => {
    try { if (context) await context.close(); } catch {}
    process.exit(0);
  });
}

function browserModule() {
  try { return require('playwright'); }
  catch {
    if (process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES) {
      try { return require(path.join(process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES, 'playwright')); } catch {}
    }
    throw new Error('缺少浏览器依赖，请让 WorkBuddy 在工程目录运行 npm install 和 npx playwright install chromium。');
  }
}

async function ensureBrowser(show = false) {
  if (context) {
    if (!page || page.isClosed()) page = await context.newPage();
    if (show) await page.bringToFront();
    return;
  }
  fs.mkdirSync(profile, {recursive: true, mode: 0o700});
  try { fs.chmodSync(path.join(root, 'browser'), 0o700); fs.chmodSync(profile, 0o700); } catch {}
  const {chromium} = browserModule();
  const headed = process.platform !== 'linux' || Boolean(process.env.DISPLAY || process.env.WAYLAND_DISPLAY);
  if (show && !headed) throw new Error('当前环境没有桌面显示，无法打开真实登录窗口。请在用户电脑上的 WorkBuddy 启动工作台。');
  const settings = {headless: !headed, viewport: {width: 1200, height: 820}};
  // Explicit executable override supports an installed system browser without copying its user profile.
  if (process.env.FAGAO_BROWSER_EXECUTABLE) settings.executablePath = process.env.FAGAO_BROWSER_EXECUTABLE;
  try { context = await chromium.launchPersistentContext(profile, settings); }
  catch {
    throw new Error('专用浏览器未能启动。请关闭此前打开的发糕专用浏览器后重试，或让 WorkBuddy 运行 npx playwright install chromium。');
  }
  context.on('close', () => { context = undefined; page = undefined; });
  page = context.pages()[0] || await context.newPage();
}

async function openLogin() {
  await ensureBrowser(true);
  try {
    await page.goto(pageUrl, {waitUntil: 'domcontentloaded', timeout: 25000});
    await page.bringToFront();
  } catch {
    return {state: 'network_error', message: '已打开专用浏览器，但黑岩页面连接失败，请检查网络后重试。'};
  }
  return {state: 'logging_in', message: '请在已打开的黑岩窗口完成登录，完成后点击“检查连接”。'};
}

async function requestList(token, pageNum = 1) {
  const response = await context.request.post(apiUrl + '/manage/book/list', {
    headers: {Authorization: 'Bearer ' + token},
    form: {pageNum: String(pageNum), pageSize: '20', language: 'zh_TW'}, timeout: 20000,
  });
  let body;
  try { body = await response.json(); } catch { body = null; }
  return {http: response.status(), body};
}

function resultState(result) {
  if ([401].includes(result.http) || Number(result.body?.code) === 401)
    return {state: 'expired', message: '黑岩登录已过期，请重新登录。'};
  if ([403].includes(result.http) || Number(result.body?.code) === 403)
    return {state: 'forbidden', message: '账号已登录，但没有访问此书库的资格。请换用可访问后台的账号。'};
  if (result.http < 200 || result.http >= 300)
    return {state: 'network_error', message: `黑岩返回 HTTP ${result.http}，请稍后重试。`};
  const rows = result.body?.data?.rows;
  if (Array.isArray(rows)) return {state: 'connected', message: '书库访问已确认。'};
  return {state: 'unavailable', message: '黑岩没有返回书库列表，接口可能已变化；未将登录页面当成连接成功。'};
}

async function check() {
  if (!context && !fs.existsSync(profile))
    return {state: 'logged_out', message: '请先使用能够访问黑岩书库的账号登录。'};
  await ensureBrowser(false);
  const cookies = await context.cookies('https://manage.zhangwenpindu.cn');
  const cookie = cookies.find(item => item.name === 'Admin-Token');
  if (!cookie?.value) return {state: lastSuccess ? 'expired' : 'logged_out',
    message: lastSuccess ? '黑岩登录已过期，请重新登录。' : '尚未登录，请在专用窗口完成黑岩登录。'};
  let token = cookie.value;
  try { token = decodeURIComponent(token); } catch {}
  let probe;
  try { probe = await requestList(token); }
  catch { return {state: 'network_error', message: '暂时无法检查黑岩书库，请检查网络后重试。'}; }
  const status = resultState(probe);
  if (status.state === 'connected') {
    lastSuccess = true;
    fs.writeFileSync(marker, JSON.stringify({last_connected_at: new Date().toISOString()}), {mode: 0o600});
  }
  // token and response stay exclusively inside this process.
  return {status, token, firstPage: probe, ...status};
}

async function collect(options) {
  const auth = await check();
  if (auth.state !== 'connected') return {state: auth.state, message: auth.message};
  const limit = Math.max(1, Math.min(200, Number(options.limit) || 30));
  const channel = ({male: '男频', female: '女频', all: '全部'})[options.channel] || options.channel || '全部';
  if (!['全部', '男频', '女频'].includes(channel)) throw new Error('黑岩频道请选择全部、男频或女频。');
  const books = [], seen = new Set(), warnings = [];
  let complete = true;
  const deadline = Date.now() + 115000;
  // At most 10 source pages. Filtering does not silently imply a whole-library census.
  for (let p = 1; p <= 10 && books.length < limit; p++) {
    let result;
    try { result = p === 1 ? auth.firstPage : await requestList(auth.token, p); }
    catch {
      if (!books.length) return {state: 'network_error', message: '黑岩读取失败，请稍后重试。'};
      warnings.push(`第 ${p} 页连接失败，已保留此前有效书目。`); complete = false; break;
    }
    const status = resultState(result);
    if (status.state !== 'connected') {
      if (!books.length) return status;
      warnings.push(status.message); complete = false; break;
    }
    const rows = result.body.data.rows;
    if (!rows.length) break;
    for (const row of rows) {
      if (Date.now() > deadline) { warnings.push('本次读取已达到时间上限，保留已取得的书目。'); complete = false; break; }
      if (channel !== '全部' && row.classifyStr !== channel) continue;
      const id = row.id == null ? '' : String(row.id);
      if (!/^[a-zA-Z0-9_-]{1,100}$/.test(id) || !row.name || seen.has(id)) continue;
      seen.add(id);
      let detail = {};
      try {
        const response = await context.request.get(apiUrl + '/manage/book/' + encodeURIComponent(id), {
          headers: {Authorization: 'Bearer ' + auth.token}, timeout: 15000,
        });
        if (response.ok()) {
          const data = await response.json();
          if (data.data && (data.data.id == null || String(data.data.id) === id)) detail = data.data;
        }
      } catch {}
      const tags = Array.isArray(detail.tags) ? detail.tags.filter(t => typeof t === 'string') : [];
      const metrics = [];
      if (/^\d+$/.test(String(row.words))) metrics.push({name: '字数', value: Number(row.words), unit: '字'});
      books.push({id, title: String(row.name), author: String(row.userName || ''),
        summary: String(detail.description || row.description || ''), rank: null,
        url: pageUrl, tags: [...new Set([row.classifyStr, row.typeDesc, ...tags].filter(Boolean))], metrics});
      if (books.length >= limit) break;
    }
    if (Date.now() > deadline) break;
    if (p === 10 && books.length < limit) {
      warnings.push('本次最多读取 10 页，筛选后的数量不足目标数。'); complete = false;
    }
    if (p < 10 && books.length < limit) await new Promise(resolve => setTimeout(resolve, 400));
  }
  if (!books.length) return {state: 'unavailable', message: '已连接书库，但没有取得符合条件的有效书目。'};
  warnings.push('书库列表不包含阅读热度，列表位置不是爆款名次。');
  return {state: 'connected', channel, books, complete, warnings};
}

const input = readline.createInterface({input: process.stdin});
for await (const line of input) {
  try {
    const request = JSON.parse(line);
    let result;
    if (request.action === 'login') result = await openLogin();
    else if (request.action === 'check') {
      const status = await check(); result = {state: status.state, message: status.message};
    } else if (request.action === 'collect') result = await collect(request.options || {});
    else throw new Error('未知浏览器操作。');
    process.stdout.write(JSON.stringify(result) + '\n');
  } catch (error) {
    // Avoid serializing raw request errors: they can contain Authorization headers.
    const safe = String(error.message || '').startsWith('缺少浏览器依赖') || String(error.message || '').startsWith('专用浏览器未能启动') || String(error.message || '').startsWith('当前环境没有桌面')
      ? error.message : '浏览器操作未完成，请检查专用窗口或重新启动连接。';
    process.stdout.write(JSON.stringify({error: safe, state: 'unavailable'}) + '\n');
  }
}
if (context) await context.close();
