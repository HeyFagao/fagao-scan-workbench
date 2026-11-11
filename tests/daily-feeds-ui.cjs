'use strict';
// Exercise the real page, including its account-bound HTTP wrapper. The server
// below serves synthetic local responses only; no credentials or remote data.
const cases = [];
function test(name, run) {cases.push({name, run});}
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM, VirtualConsole} = require(process.env.FAGAO_QA_NODE_MODULES
  ? path.join(process.env.FAGAO_QA_NODE_MODULES, 'jsdom') : 'jsdom');
const web = path.resolve(__dirname, '../web');
const origin = 'http://127.0.0.1:18376';
const entry = '[data-section="daily-boards"]';
const boardButton = id => `[data-action="daily-board"][data-board="${id}"]`;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
function deferred() {
  let resolve;
  const promise = new Promise(done => {resolve = done;});
  return {promise, resolve};
}

async function eventually(predicate, message) {
  const deadline = Date.now() + 2500;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await pause(5);
  }
  assert.fail(message);
}

function membership(id, active = true) {
  const now = Date.now();
  return {
    allowed: active, account: {id, display_name: id}, generation: 'session-' + id,
    reason: active ? 'active' : 'expired', access_mode: active ? 'active' : 'read_only',
    can_read_history: !active, server_time: new Date(now).toISOString(),
    membership_expires_at: new Date(now + (active ? 86400000 : -86400000)).toISOString(),
    permit_expires_at: active ? new Date(now + 600000).toISOString() : null,
    identity_expires_at: new Date(now + 600000).toISOString(),
  };
}

const payload = (board, title, overrides = {}) => ({board, status: 'cached', snapshot: {
  board, source_date: '2026-11-08', observed_at: '2026-11-11T06:00:00Z',
  scope: '合成范围', source_url: 'https://www.wangwendashuju.com/douyin',
  columns: ['原榜排名', '剧名', '新增播放'], rows: [['11', title, '5821.3万']], ...overrides,
}});

function response(value, status = 200) {
  return {ok: status >= 200 && status < 300, status, json: async () => structuredClone(value)};
}

async function fixture(t, active, onFeed) {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', error => errors.push(error.message));
  const dom = new JSDOM(fs.readFileSync(path.join(web, 'index.html'), 'utf8'), {
    url: origin + '/', runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole: vc,
  });
  const w = dom.window;
  const d = w.document;
  const ctx = {membership: membership('member-a', active), calls: [], d, content: () => d.querySelector('#content').textContent};
  w.HTMLDialogElement.prototype.close = function () {this.open = false;};
  w.HTMLDialogElement.prototype.showModal = function () {this.open = true;};
  w.fetch = async (input, init = {}) => {
    try {
      const {pathname, searchParams, origin: target} = new URL(input, origin);
      assert.equal(target, origin, 'The client must not send the account binding to an external site.');
      if (pathname === '/api/membership/status' || pathname === '/api/membership/refresh') return response(ctx.membership);
      if (pathname === '/api/legacy-data/status') return response({available: false, decision_required: false, can_claim: false, can_skip: true, original_preserved: true, counts: {}});
      if (pathname === '/api/state') return response({active_mode: 'long', views: {long: {platform: 'fanqie'}}, platforms: []});
      if (pathname === '/api/overview') return response({platforms: []});
      if (pathname === '/api/overview/refresh') return response({status: 'fresh', retry_after_seconds: 900});
      assert(['/api/daily-boards', '/api/daily-boards/sync'].includes(pathname), 'Unexpected request to ' + pathname);
      const headers = new Headers(init.headers);
      assert.equal(headers.get('X-Fagao-Account'), ctx.membership.account.id);
      assert.equal(headers.get('X-Fagao-Generation'), ctx.membership.generation);
      const method = init.method || 'GET';
      const board = method === 'POST' ? JSON.parse(init.body).board : searchParams.get('board');
      if (method === 'POST') assert.equal(headers.get('X-Fagao-Request'), '1');
      assert.equal(pathname, method === 'POST' ? '/api/daily-boards/sync' : '/api/daily-boards');
      assert(['douyin', 'hongguo', 'zhihu', 'fanqie_story', 'adapt'].includes(board));
      const call = {board, method, account: ctx.membership.account.id, revision: searchParams.get('revision')};
      ctx.calls.push(call);
      return await onFeed(call);
    } catch (error) {
      errors.push(error.message);
      throw error;
    }
  };
  t.after(() => {dom.window.close(); assert.deepEqual(errors, []);});
  // Execute local scripts in page order, just as the actual entry point does.
  for (const script of d.querySelectorAll('script[src]')) {
    const file = path.join(web, path.basename(script.getAttribute('src')));
    w.eval(fs.readFileSync(file, 'utf8'));
  }
  await eventually(() => d.querySelector('.overview-tabs'), 'Account did not reach the workbench');
  ctx.click = selector => {
    const element = d.querySelector(selector);
    assert(element, 'Missing control ' + selector);
    element.click();
  };
  return ctx;
}

test('opening shows the account cache before updating and offers all five boards', async t => {
  const pending = deferred();
  const ctx = await fixture(t, true, async ({board, method}) => method === 'GET'
    ? response(payload(board, '已存作品')) : pending.promise);
  assert.deepEqual(ctx.calls, []);
  ctx.click(entry);
  await eventually(() => ctx.calls.length === 2 && ctx.content().includes('已存作品'), 'No cache while syncing');
  assert.deepEqual(ctx.calls.map(({method, board}) => [method, board]), [['GET', 'douyin'], ['POST', 'douyin']]);
  for (const id of ['douyin', 'hongguo', 'zhihu', 'fanqie_story', 'adapt']) assert(ctx.d.querySelector(boardButton(id)), id);
  pending.resolve(response(payload('douyin', '刚发布的作品')));
  await eventually(() => ctx.content().includes('刚发布的作品'), 'New snapshot was not displayed');
  assert(!ctx.content().includes('已存作品'));
});

test('pagination keeps source ranks, literal titles, units, and source/observation dates', async t => {
  const unsafeTitle = '<img data-injected="yes" src="x">';
  const rows = Array.from({length: 52}, (_, i) => [String(11 + 4 * i), i === 0 ? unsafeTitle : `样本-${i}`, '3.6亿']);
  const ctx = await fixture(t, true, ({board}) => response(payload(board, '', {rows})));
  ctx.click(entry);
  await eventually(() => ctx.d.querySelectorAll('#content tbody tr').length === 50, 'Expected first 50 rows');
  const cells = () => [...ctx.d.querySelector('#content tbody tr').querySelectorAll('td')].map(cell => cell.textContent);
  assert.deepEqual(cells(), ['11', unsafeTitle, '3.6亿']);
  assert.equal(ctx.d.querySelector('[data-injected]'), null);
  assert.match(ctx.content(), /11[-/月]0?8/);
  assert.match(ctx.content(), /11[-/月]11/);
  ctx.click('[data-action="daily-page"][data-page="2"]');
  await eventually(() => ctx.d.querySelectorAll('#content tbody tr').length === 2, 'Last two rows were not accessible');
  assert.deepEqual(cells(), ['211', '样本-50', '3.6亿']);
});

test('local filtering retains the source rank given to each result', async t => {
  const ctx = await fixture(t, false, ({board}) => response(payload(board, '', {
    columns: ['排名', '剧名', '视频类型'],
    rows: [['11', '未来之城', '3D动态漫'], ['24', '海边来信', 'AI真人演绎剧'], ['39', '古城来信', '3D动态漫']],
  })));
  ctx.click(entry);
  await eventually(() => ctx.d.querySelector('#daily-query') && ctx.d.querySelectorAll('#content tbody tr').length === 3,
    'Missing filter for loaded board');
  const search = ctx.d.querySelector('#daily-query');
  search.value = '来信';
  search.dispatchEvent(new ctx.d.defaultView.Event('input', {bubbles: true}));
  const type = ctx.d.querySelector('#daily-video-type');
  assert(type, 'Missing video-type filter on the Douyin board');
  type.value = '3D动态漫';
  type.dispatchEvent(new ctx.d.defaultView.Event('change', {bubbles: true}));
  await eventually(() => ctx.d.querySelectorAll('#content tbody tr').length === 1, 'Filters did not combine');
  const cells = [...ctx.d.querySelectorAll('#content tbody tr td')].map(cell => cell.textContent);
  assert.deepEqual(cells, ['39', '古城来信', '3D动态漫']);
  assert.equal(ctx.calls.length, 1, 'Local filters requested a remote refresh');
});

test('expired members can switch saved boards without sending a sync', async t => {
  const ctx = await fixture(t, false, ({board}) => response(payload(board, board + '-历史')));
  ctx.click(entry);
  await eventually(() => ctx.content().includes('douyin-历史'), 'Read-only account cannot read saved board');
  ctx.click(boardButton('zhihu'));
  await eventually(() => ctx.content().includes('zhihu-历史'), 'Saved short story board did not open');
  const refresh = ctx.d.querySelector('[data-action="daily-refresh"]');
  if (refresh) {assert(refresh.disabled || refresh.hidden);refresh.click();}
  assert.deepEqual(ctx.calls.map(({method, board}) => [method, board]), [['GET', 'douyin'], ['GET', 'zhihu']]);
});

test('sync failure leaves the cached snapshot and exposes a recovery status', async t => {
  const ctx = await fixture(t, true, ({method, board}) => method === 'GET'
    ? response(payload(board, '最后一份可读资料'))
    : response({error: {code: 'feed_unavailable', message: '暂时无法读取榜单。'}}, 503));
  ctx.click(entry);
  await eventually(() => ctx.calls.length === 2 && ctx.content().includes('最后一份可读资料')
    && /暂时无法|上次|未更新|缓存|已保存/.test(ctx.content()), 'No cached fallback status');
});

test('a board with no published or cached snapshot shows an empty state', async t => {
  const ctx = await fixture(t, false, ({board}) => response({board, status: 'empty', snapshot: null}));
  ctx.click(entry);
  await eventually(() => ctx.calls.length === 1 && /还没有|暂无|尚无|尚未/.test(ctx.content()), 'Missing empty state');
  assert.equal(ctx.d.querySelector('#content tbody tr'), null);
  assert(!ctx.calls.some(call => call.method === 'POST'));
});

test('a late sync for the previous board cannot replace the current table', async t => {
  const pending = deferred();
  const ctx = await fixture(t, true, ({method, board}) => method === 'POST' && board === 'douyin'
    ? pending.promise : response(payload(board, '作品-' + board)));
  ctx.click(entry);
  await eventually(() => ctx.calls.some(c => c.board === 'douyin' && c.method === 'POST'), 'No initial sync request');
  ctx.click(boardButton('hongguo'));
  await eventually(() => ctx.content().includes('作品-hongguo'), 'Current board did not load');
  pending.resolve(response(payload('douyin', '迟到的抖音作品')));
  await pause(15);
  assert(ctx.content().includes('作品-hongguo'));
  assert(!ctx.content().includes('迟到的抖音作品'));
});

test('account switching drops previous board state even if the old transport ignores cancellation', async t => {
  const pending = deferred();
  const ctx = await fixture(t, true, ({method, account, board}) => account === 'member-a' && method === 'POST'
    ? pending.promise : response(payload(board, account + '专属历史')));
  ctx.click(entry);
  await eventually(() => ctx.calls.some(c => c.account === 'member-a' && c.method === 'POST'), 'Initial sync missing');
  ctx.membership = membership('member-b');
  ctx.click('[data-action="membership-refresh"]');
  await eventually(() => ctx.d.querySelector('.overview-tabs'), 'New account did not reset old view');
  ctx.click(entry);
  await eventually(() => ctx.content().includes('member-b专属历史'), 'New account data missing');
  pending.resolve(response(payload('douyin', '旧账号迟到资料')));
  await pause(15);
  assert(!ctx.content().includes('member-a专属历史'));
  assert(!ctx.content().includes('旧账号迟到资料'));
  assert(ctx.content().includes('member-b专属历史'));
});


test('switching to an older revision makes an account-bound history read only', async t => {
  const oldRevision = '1'.repeat(64), currentRevision = '2'.repeat(64);
  const ctx = await fixture(t, false, ({board, revision}) => {
    const result = payload(board, revision === oldRevision ? '早期版本作品' : '最近版本作品');
    result.snapshot.revision = revision || currentRevision;
    result.history = [{revision: currentRevision, source_date: '2026-11-08', observed_at: '2026-11-11T06:00:00Z', row_count: 1},
      {revision: oldRevision, source_date: '2026-11-07', observed_at: '2026-11-07T06:00:00Z', row_count: 1}];
    return response(result);
  });
  ctx.click(entry);
  await eventually(() => ctx.content().includes('最近版本作品'), 'Current saved edition unavailable');
  const select = ctx.d.querySelector('#daily-history');
  assert(select && [...select.options].some(option => option.value === oldRevision), 'Historical edition not selectable');
  select.value = oldRevision;
  select.dispatchEvent(new ctx.d.defaultView.Event('change', {bubbles: true}));
  await eventually(() => ctx.content().includes('早期版本作品'), 'Did not render selected edition');
  assert(ctx.calls.some(call => call.revision === oldRevision && call.method === 'GET'));
  assert(!ctx.calls.some(call => call.method === 'POST'));
});

(async () => {
  let passed = 0;
  for (const {name, run} of cases) {
    const cleanup = [];
    try {
      try {await run({after: callback => cleanup.push(callback)});}
      finally {for (const action of cleanup.reverse()) await action();}
      passed++;
    } catch (error) {
      console.error(name, error);
    }
  }
  console.log(JSON.stringify({suite: 'daily-boards-ui', passed, failed: cases.length - passed,
    synthetic_accounts: true, external_requests: 0}));
  if (passed !== cases.length) process.exitCode = 1;
})();
