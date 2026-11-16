#!/usr/bin/env node
// 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
// A fresh browser reads the public recommendation page as an ordinary visitor.
import {createRequire} from 'node:module';
import readline from 'node:readline';

const require = createRequire(import.meta.url);
const pageBase = 'https://wen1701.github.io/FanqieRankTracker/shorts.html';
let browser;
let closing = false;

async function close() {
  if (closing) return;
  closing = true;
  try { if (browser) await browser.close(); } catch {}
}
for (const signal of ['SIGTERM', 'SIGINT']) {
  process.on(signal, async () => { await close(); process.exit(0); });
}

function fail(message, state) {
  const error = new Error(message);
  error.state = state;
  throw error;
}

async function collect(options) {
  if (typeof options?.category !== 'string' || !options.category || options.category.length > 50
      || !Number.isInteger(options.limit) || options.limit < 1 || options.limit > 100) {
    fail('推荐条件无效。', 'invalid_options');
  }
  let chromium;
  try { ({chromium} = require(process.env.FAGAO_PLAYWRIGHT_MODULE || 'playwright')); }
  catch { fail('请让 WorkBuddy 完成推荐浏览器环境准备。', 'browser_setup_required'); }
  const settings = {headless: true, args: ['--blink-settings=imagesEnabled=false']};
  if (process.env.FAGAO_BROWSER_PROXY_MODE === 'direct') settings.args.push('--no-proxy-server');
  const proxy = JSON.parse(process.env.FAGAO_BROWSER_PROXY_JSON || '{}');
  if (proxy.server) settings.proxy = proxy;
  if (process.env.FAGAO_BROWSER_EXECUTABLE) settings.executablePath = process.env.FAGAO_BROWSER_EXECUTABLE;
  try { browser = await chromium.launch(settings); }
  catch { fail('推荐浏览器未能启动，请让 WorkBuddy 检查浏览器环境。', 'browser_setup_required'); }
  const context = await browser.newContext({acceptDownloads: false, viewport: {width: 1200, height: 900}});
  const page = await context.newPage();
  const url = new URL(pageBase);
  url.searchParams.set('tag', options.category);
  const sourceUrl = url.href;
  const response = await page.goto(sourceUrl, {waitUntil: 'domcontentloaded', timeout: 35000});
  if (!response || response.status() !== 200 || page.url() !== sourceUrl) {
    fail('公开短篇推荐页面暂时无法访问。', 'source_changed');
  }
  await page.waitForFunction(() => {
    const feed = document.querySelector('#short-feed');
    return feed?.getAttribute('aria-busy') === 'false';
  }, undefined, {timeout: 35000});
  if (await page.locator('.short-state-error').count()) {
    fail('公开推荐页面目前无法取得该题材，请稍后再看。', 'network_error');
  }
  const result = await page.evaluate(() => {
    const text = (root, selector) => root.querySelector(selector)?.textContent?.trim() || '';
    const feed = document.querySelector('#short-feed');
    const cards = Array.from(feed?.querySelectorAll('article.short-card') || []);
    return {
      category: document.querySelector('#short-feed-title')?.textContent?.trim() || '',
      items: cards.slice(0, 101).map(card => ({
        title: text(card, 'h3'), author: text(card, '.short-author'),
        summary: text(card, '.short-excerpt'), position: text(card, '.short-position'),
        url: card.querySelector('.short-card-footer a')?.href || '',
        cover_url: card.querySelector('.short-cover img')?.src || '',
        topics: Array.from(card.querySelectorAll('.short-topic-row span'), element => element.textContent.trim()),
        reading_meta: text(card, '.short-reading-meta'),
        metrics: Array.from(card.querySelectorAll('.short-metrics > div'), element => ({
          name: text(element, 'dt'), value: text(element, 'dd'),
        })),
      })),
    };
  });
  return {source_url: sourceUrl, ...result};
}

const input = readline.createInterface({input: process.stdin, terminal: false});
try {
  const line = await new Promise(resolve => { input.once('line', resolve); input.once('close', () => resolve('')); });
  input.close();
  const options = JSON.parse(line);
  const value = await collect(options);
  await close();
  process.stdout.write(JSON.stringify(value) + '\n');
} catch (error) {
  await close();
  const message = error.state ? error.message : '公开短篇推荐页面尚未完成读取，请稍后重试。';
  process.stdout.write(JSON.stringify({error: message, state: error.state || 'network_error'}) + '\n');
  process.exitCode = 1;
} finally {
  await close();
}
