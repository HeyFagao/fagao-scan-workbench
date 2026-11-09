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
  const maxPages = Math.max(1, Math.min(100, Math.trunc(Number(options.max_pages) || 100)));
  const limit = Math.max(1, Math.trunc(Number(options.limit) || maxPages * 20));
  const channel = ({male: '男频', female: '女频', all: '全部'})[options.channel] || options.channel || '全部';
  if (!['全部', '男频', '女频'].includes(channel)) throw new Error('黑岩频道请选择全部、男频或女频。');
  const books = [], seen = new Set(), sourceSeen = new Set(), warnings = [];
  let stopReason = 'max_pages', pagesFetched = 0, finalAuth = {state: 'connected', message: auth.message};
  const sourceTotal = auth.firstPage.body?.data?.total ?? null;
  const deadline = Date.now() + Math.max(60, Math.min(1800, Number(options.timeout_seconds) || 1200)) * 1000;
  const metadataDir = path.join(root, 'cache', 'metadata', 'heiyan');
  fs.mkdirSync(metadataDir, {recursive: true, mode: 0o700});
  const progress = message => process.stdout.write(JSON.stringify({event: 'progress', message}) + '\n');
  const safeCover = value => {
    if (typeof value !== 'string') return '';
    try { const url = new URL(value.startsWith('//') ? 'https:' + value : value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : ''; } catch { return ''; }
  };
  for (let p = 1; p <= maxPages && books.length < limit; p++) {
    progress(`正在读取黑岩第 ${p} 页，已取得 ${books.length} 本…`);
    let result;
    try { result = p === 1 ? auth.firstPage : await requestList(auth.token, p); }
    catch {
      if (!books.length) return {state: 'network_error', message: '黑岩读取失败，请稍后重试。'};
      finalAuth = {state: 'network_error', message: '黑岩后续分页连接失败，请稍后重试。'};
      warnings.push(`第 ${p} 页连接失败，已保留此前有效书目。`); stopReason = 'request_failed'; break;
    }
    const status = resultState(result);
    if (status.state !== 'connected') {
      if (!books.length) return status;
      finalAuth = status;
      warnings.push(status.message); stopReason = 'session_failed'; break;
    }
    const rows = result.body.data.rows;
    pagesFetched++;
    if (!rows.length) { stopReason = 'empty_page'; break; }
    const sourceIds = rows.map(row => String(row.id ?? '')).filter(Boolean);
    if (sourceIds.length && sourceIds.every(id => sourceSeen.has(id))) {
      stopReason = 'repeated_page'; warnings.push('书库分页重复返回已取得的书目，已停止。'); break;
    }
    sourceIds.forEach(id => sourceSeen.add(id));
    for (const row of rows) {
      if (Date.now() > deadline) { warnings.push('本次读取已达到时间上限，保留已取得的书目。'); stopReason = 'time_budget'; break; }
      if (channel !== '全部' && row.classifyStr !== channel) continue;
      const id = row.id == null ? '' : String(row.id);
      if (!/^[a-zA-Z0-9_-]{1,100}$/.test(id) || !row.name || seen.has(id)) continue;
      seen.add(id);
      let detail = {};
      let cached = false;
      const cacheFile = path.join(metadataDir, id + '.json');
      try {
        const value = JSON.parse(fs.readFileSync(cacheFile, 'utf8'));
        if (value.id === id && String(value.source_update || '') === String(row.updateTime || '') && Date.now() - value.cached_at >= 0 && Date.now() - value.cached_at < 86400000) {
          detail = value.detail || {}; cached = true;
        }
      } catch {}
      try {
        if (!cached) {
        const response = await context.request.get(apiUrl + '/manage/book/' + encodeURIComponent(id), {
          headers: {Authorization: 'Bearer ' + auth.token}, timeout: 15000,
        });
        if (response.ok()) {
          const data = await response.json();
          if (data.data && (data.data.id == null || String(data.data.id) === id)) {
            detail = {tags: data.data.tags, description: data.data.description, cover: data.data.cover, coverUrl: data.data.coverUrl, coverImg: data.data.coverImg};
            const temporary = cacheFile + '.' + process.pid + '.tmp';
            fs.writeFileSync(temporary, JSON.stringify({id, cached_at: Date.now(), source_update: String(row.updateTime || ''), detail}), {mode: 0o600});
            fs.renameSync(temporary, cacheFile);
          }
        }
        }
      } catch {}
      const tags = Array.isArray(detail.tags) ? detail.tags.filter(t => typeof t === 'string') : [];
      const metrics = [];
      if (/^\d+$/.test(String(row.words))) metrics.push({name: '字数', value: Number(row.words), unit: '字'});
      books.push({id, title: String(row.name), author: String(row.userName || ''),
        summary: String(detail.description || row.description || ''), rank: null,
        url: pageUrl, cover_url: safeCover(row.coverUrl || row.cover || row.coverImg || detail.coverUrl || detail.cover || detail.coverImg),
        tags: [...new Set([row.classifyStr, row.typeDesc, ...tags].filter(Boolean))], metrics});
      if (books.length % 10 === 0) progress(`正在整理黑岩书目，已取得 ${books.length} 本…`);
      if (books.length >= limit) break;
    }
    if (Date.now() > deadline) break;
    if (p < maxPages && books.length < limit) await new Promise(resolve => setTimeout(resolve, 400));
  }
  if (!books.length) return {state: 'unavailable', message: '已连接书库，但没有取得符合条件的有效书目。'};
  warnings.push('书库列表不包含阅读热度，列表位置不是爆款名次。');
  if (books.length >= limit && options.limit) stopReason = 'requested_limit';
  const complete = ['empty_page', 'requested_limit'].includes(stopReason);
  if (stopReason === 'max_pages') warnings.push(`已达到本次 ${maxPages} 页的读取上限。`);
  return {state: 'connected', auth_state: finalAuth.state, auth_message: finalAuth.message,
    channel, books, complete, warnings, requested_count: limit, source_total: sourceTotal,
    pagination: {pages_fetched: pagesFetched, max_pages: maxPages, stop_reason: stopReason, source_total: sourceTotal, page_size: 20}};
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
