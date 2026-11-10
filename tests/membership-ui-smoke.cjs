// Membership DOM acceptance with synthetic accounts only. No HTTP server,
// platform requests, website credentials, or browser profile are used.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM, VirtualConsole} = require(process.env.FAGAO_QA_NODE_MODULES
  ? path.join(process.env.FAGAO_QA_NODE_MODULES, 'jsdom') : 'jsdom');
const ROOT = path.resolve(__dirname, '..');
const BASE = 'http://127.0.0.1:18376/';
const clone = value => JSON.parse(JSON.stringify(value));
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const response = (body, status = 200) => ({ok: status >= 200 && status < 300, status,
  json: async () => clone(body), blob: async () => new Blob([typeof body === 'string' ? body : JSON.stringify(body)])});
async function wait(predicate, message) {
  for (let attempt = 0; attempt < 150; attempt++) {
    if (await predicate()) return;
    await pause(10);
  }
  throw new Error(message);
}
function member(id = 'account-a', overrides = {}) {
  const now = Date.now();
  return {
    allowed: true, reason: 'active', account: {id, display_name: id === 'account-a' ? '甲会员' : '乙会员'},
    membership_expires_at: new Date(now + 86400000).toISOString(),
    permit_expires_at: new Date(now + 15 * 60000).toISOString(),
    server_time: new Date(now).toISOString(), policy_version: 'synthetic-test-policy',
    generation: id + '-generation-1', ...overrides,
  };
}
function denied(reason = 'not_authenticated', account = null) {
  return {allowed: false, reason, account, membership_expires_at: null, permit_expires_at: null,
    server_time: new Date().toISOString(), policy_version: 'synthetic-test-policy',
    generation: account ? reason + '-generation' : 'anonymous-generation-1'};
}
function historyMember(id = 'account-a', overrides = {}) {
  const now = Date.now();
  return member(id, {allowed: false, reason: 'expired', can_read_history: true, access_mode: 'read_only',
    server_time: new Date(now).toISOString(), membership_expires_at: new Date(now - 1000).toISOString(),
    permit_expires_at: new Date(now - 1000).toISOString(),
    identity_expires_at: new Date(now + 15 * 60000).toISOString(), ...overrides});
}
function mixedHistoryMember(overrides = {}) {
  const now = Date.now();
  return member('account-a', {allowed: false, reason: 'not_eligible', can_read_history: true,
    access_mode: 'read_only', history_eligible: true,
    history_membership_expires_at: new Date(now - 86400000).toISOString(),
    membership_expires_at: new Date(now + 86400000).toISOString(), permit_expires_at: null,
    identity_expires_at: new Date(now + 15 * 60000).toISOString(), ...overrides});
}
function legacyStatus(overrides = {}) {
  return {available: false, counts: {}, claim_status: 'unclaimed', decision: null,
    decision_required: false, can_skip: true, original_preserved: true, ...overrides,
    can_claim: overrides.can_claim ?? !!(overrides.available && overrides.decision_required)};
}
function accountData(id) {
  const label = id === 'account-a' ? '甲私有作品' : '乙私有作品';
  const snap = {id: id + '-snapshot', mode: 'long', platform: 'fanqie', channel: '女频',
    category: '古言脑洞', board: '新书榜', collected_at: new Date().toISOString(),
    books: [{id: id + '-book', title: label, author: '合成作者', rank: 1, summary: label + '简介，仅用于隔离测试。', tags: [], metrics: []}]};
  const report = {id: id + '-report', book_id: snap.books[0].id, snapshot_id: snap.id,
    title: label + '报告', content: label + '报告正文', scope: '合成测试', platform: 'fanqie'};
  const text = {id: id + '-text', book_id: snap.books[0].id, snapshot_id: snap.id,
    title: label, platform: 'fanqie', scope: '用户导入的测试正文', acquisition: 'user_import', complete: false, characters: 20};
  const state = {active_mode: 'long',
    views: {long: {platform: 'fanqie', snapshot_id: snap.id, selected_id: snap.books[0].id,
      page: 1, page_size: 20, filters: {query: '', sort: 'source', favorites: false}}, short: {}, overseas: {}},
    snapshots: [{...snap, books_count: 1}], favorites: [{title: label, book_id: snap.books[0].id,
      snapshot_id: snap.id, platform: 'fanqie'}], reports: [report], jobs: [], texts: [text], history: [],
    config: {main_site: 'https://www.fagaochuangzuo.com/'}, auth: {},
    platforms: [{id: 'fanqie', name: '番茄小说', mode: 'long', status: 'available'}]};
  return {state, snap, report, text};
}
function hold() {
  let release;
  const promise = new Promise(resolve => {release = resolve;});
  return {promise, release, captured: false};
}
async function open(initialMembership = denied(), options = {}) {
  const errors = [], vc = new VirtualConsole();
  vc.on('jsdomError', error => errors.push(String(error)));
  const dom = new JSDOM(fs.readFileSync(path.join(ROOT, 'web/index.html'), 'utf8'), {
    url: options.url || BASE, runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole: vc,
  });
  const control = {membership: clone(initialMembership), calls: [], errors, timers: new Map(), timerId: 0,
    data: {'account-a': accountData('account-a'), 'account-b': accountData('account-b')},
    held: new Map(), overrides: new Map(), logoutCalls: 0, staleAccountRejects: [], missingBindings: [],
    downloadBlobs: [], savedDownloads: [], readonlyMutationAttempts: [], legacyActions: [],
    legacy: {'account-a': legacyStatus(), 'account-b': legacyStatus()},
    holdNext(url) {const pending = hold(); this.held.set(url, pending); return pending;},
    protectedCalls() {return this.calls.filter(call => !call.url.startsWith('/api/membership/'));},
    async runTimers(predicate = () => true) {
      const ready = [...this.timers.entries()].filter(([, timer]) => predicate(timer));
      for (const [id, timer] of ready) {
        if (!this.timers.has(id)) continue;
        this.timers.delete(id);
        await timer.callback();
      }
      await pause(0);
    },
  };
  dom.control = control;
  dom.window.setTimeout = (callback, delay) => {const id = ++control.timerId; control.timers.set(id, {callback, delay}); return id;};
  dom.window.clearTimeout = id => control.timers.delete(id);
  dom.window.HTMLDialogElement.prototype.showModal = function () {this.open = true;};
  dom.window.HTMLDialogElement.prototype.close = function () {this.open = false;};
  dom.window.URL.createObjectURL = blob => {control.downloadBlobs.push(blob); return 'blob:' + BASE + 'synthetic-download';};
  dom.window.URL.revokeObjectURL = () => {};
  dom.window.HTMLAnchorElement.prototype.click = function () {
    if (this.hasAttribute('download')) control.savedDownloads.push({href: this.href, filename: this.download});
    else throw new Error('Unexpected anchor navigation in synthetic test.');
  };
  dom.window.IntersectionObserver = class {
    observe() {}
    disconnect() {control.observerDisconnects = (control.observerDisconnects || 0) + 1;}
  };
  dom.window.fetch = async (input, init = {}) => {
    const url = new URL(input, BASE).pathname;
    const body = init.body ? JSON.parse(init.body) : undefined;
    const headers = new Headers(init.headers || {});
    const binding = {account: headers.get('X-Fagao-Account'), generation: headers.get('X-Fagao-Generation')};
    control.calls.push({url, body, method: init.method || 'GET', binding});
    if (init.method === 'POST') assert.equal(init.headers?.['X-Fagao-Request'], '1', 'Local mutation omitted CSRF request marker.');
    if (!url.startsWith('/api/membership/') || url === '/api/membership/logout' || url === '/api/membership/connect') {
      const expectedAccount = control.membership.account?.id || null;
      if (!binding.generation || (expectedAccount && !binding.account)) control.missingBindings.push(url);
      if (binding.account !== expectedAccount || binding.generation !== String(control.membership.generation)) {
        control.staleAccountRejects.push({url, binding});
        return response({error: {code: 'membership_account_changed', message: '当前账号已变化，请重新确认账号。'}}, 403);
      }
    }
    if (!url.startsWith('/api/membership/') && !control.membership.allowed) {
      const readRoute = (init.method || 'GET') === 'GET'
        ? /^\/api\/(state|overview|snapshots\/|reports\/|texts\/|jobs\/|tasks\/)/.test(url)
        : ['/api/view', '/api/insights', '/api/resolve'].includes(url);
      if (!control.membership.can_read_history || !readRoute) {
        control.readonlyMutationAttempts.push(url);
        return response({error: {code: 'membership_expired', message: '会员到期后仅可查看已保存资料。'}}, 403);
      }
    }
    if (control.overrides.has(url)) {
      const override = control.overrides.get(url);
      if (override instanceof Error) throw override;
      return response(override.body, override.status);
    }
    if (url === '/api/membership/status' || url === '/api/membership/refresh') {
      const payload = clone(control.membership), pending = control.held.get(url);
      if (pending && !pending.captured) {pending.captured = true; await pending.promise;}
      return response(payload);
    }
    if (url === '/api/membership/connect') return response({authorization_url: 'https://www.fagaochuangzuo.com/workbench/authorize?state=synthetic'});
    if (url === '/api/membership/logout') {
      control.logoutCalls++;
      control.membership = denied();
      return response(control.membership);
    }
    const id = control.membership.account?.id || 'account-a';
    const data = control.data[id];
    if (!data) throw new Error('Unexpected synthetic account: ' + id);
    let payload;
    if (url === '/api/legacy-data/status') payload = clone(control.legacy[id]);
    else if (url === '/api/legacy-data/claim' || url === '/api/legacy-data/skip') {
      assert.deepEqual(body, {confirmed: true}, 'Legacy decision was not explicitly confirmed.');
      const action = url.endsWith('/claim') ? 'claim' : 'skip';
      control.legacyActions.push({account: id, action, body});
      control.legacy[id] = {...control.legacy[id], decision_required: false,
        claim_status: action === 'claim' ? 'claimed' : 'unclaimed', decision: action === 'claim' ? 'claimed' : 'skipped'};
      payload = {status: action === 'claim' ? 'claimed' : 'skipped'};
    }
    else if (url === '/api/state') payload = clone(data.state);
    else if (url === '/api/view') {
      const mode = body.mode || data.state.active_mode;
      data.state.active_mode = mode;
      data.state.views[mode] = {...data.state.views[mode], ...body};
      payload = clone(data.state);
    } else if (url === '/api/overview') payload = {platforms: [], update: null};
    else if (url === '/api/overview/refresh' || url === '/api/collect') payload = {status: 'fresh', retry_after_seconds: 900};
    else if (url === '/api/snapshots/' + data.snap.id) payload = data.snap;
    else if (url === '/api/reports/' + data.report.id) payload = data.report;
    else if (url === '/api/texts/' + data.text.id + '/download') payload = data.text.title + '合成正文';
    else if (url === '/api/favorite') {
      data.state.favorites = body.enabled ? [{title: data.snap.books[0].title, book_id: body.book_id,
        snapshot_id: body.snapshot_id, platform: 'fanqie'}] : [];
      payload = clone(data.state);
    }
    else throw new Error('Unexpected mocked endpoint (real network forbidden): ' + url);
    const pending = control.held.get(url);
    if (pending && !pending.captured) {
      pending.captured = true;
      // Deliberately ignore AbortSignal: the UI must reject a late response even
      // when a transport completes after logout or account switch.
      await pending.promise;
    }
    return response(payload);
  };
  if (options.setup) options.setup(dom);
  dom.window.eval(fs.readFileSync(path.join(ROOT, 'web/app.js'), 'utf8'));
  await wait(() => control.calls.length > 0, 'The app did not request membership status.');
  return dom;
}
function click(dom, action) {
  const button = dom.window.document.querySelector(`[data-action="${action}"]`);
  assert(button, `Missing ${action} control.`);
  button.click();
}
async function recheck(dom) {
  const before = dom.control.calls.length;
  await wait(() => !dom.window.document.querySelector('[data-action="membership-refresh"]')?.disabled,
    'Membership recheck remained busy.');
  const button = dom.window.document.querySelector('[data-action="membership-refresh"]');
  if (button) button.click();
  else dom.window.document.dispatchEvent(new dom.window.Event('visibilitychange'));
  await wait(() => dom.control.calls.slice(before).some(call => call.url.startsWith('/api/membership/')),
    'Recheck did not query membership.');
  await pause(20);
}
async function unlocked(dom) {
  await wait(() => dom.window.document.querySelector('.overview-tabs'), 'Allowed member did not reach the workbench.');
  assert.equal(dom.window.document.querySelector('#membership-card'), null, 'Allowed member remains locked.');
  assert.deepEqual(dom.control.missingBindings, [], 'Protected calls omitted their displayed account or generation.');
}
async function locked(dom) {
  await wait(() => dom.window.document.querySelector('#membership-card [data-action="membership-refresh"]'),
    'Resolved membership gate did not render.');
}

(async () => {
  let passed = 0;
  const cases = [
    ['unauthenticated startup does not read or collect protected data', async () => {
      const dom = await open();
      try {
        await locked(dom);
        assert.equal(dom.control.calls[0].url, '/api/membership/status');
        assert(dom.window.document.querySelector('[data-action="membership-connect"]'));
        await dom.control.runTimers();
        assert.equal(dom.control.protectedCalls().length, 0);
        assert.equal(dom.window.document.querySelector('#bridge').textContent, '');
      } finally {dom.window.close();}
    }],
    ['malformed allowed status fails closed', async () => {
      for (const malformed of [{allowed: true}, {...member(), account: null}, {...member(), permit_expires_at: 'invalid'},
        {...member(), generation: undefined}, {...member(), generation: ''}]) {
        const dom = await open(malformed);
        try {await locked(dom); assert.equal(dom.control.protectedCalls().length, 0);}
        finally {dom.window.close();}
      }
    }],
    ['account grants show a quiet connected state without invented expiry or internal labels', async () => {
      const expiries = [null, new Date(Date.now() - 86400000).toISOString(), new Date(Date.now() + 86400000).toISOString()];
      for (const [index, expiry] of expiries.entries()) {
        const account = index === 1 ? 'account-b' : 'account-a';
        const dom = await open(member(account, {access_basis: 'experimental_account', membership_expires_at: expiry}));
        try {
          await unlocked(dom);
          const label = dom.window.document.querySelector('#membership-account').textContent;
          assert.match(label, /已连接/);
          assert.doesNotMatch(label, /会员有效|到期|永久|\d{4}[\/年-]/, 'Account exemption displayed a membership expiry or permanence promise.');
          assert.doesNotMatch(dom.window.document.body.textContent, /实验|管理员|永久/, 'Internal authorization details leaked into the customer UI.');
          assert.equal(dom.window.document.querySelector('#membership-notice').hidden, true, 'A usable account received an unnecessary status banner.');
          assert.equal(dom.control.calls.some(call => call.url === '/api/state'), true);
        } finally {dom.window.close();}
      }
    }],
    ['current membership displays its actual expiry without experiment labels or banners', async () => {
      const grant = member('account-b', {access_basis: 'experimental_membership'});
      const dom = await open(grant);
      try {
        await unlocked(dom);
        const label = dom.window.document.querySelector('#membership-account').textContent;
        assert.match(label, /会员有效/);
        assert.match(label, /到期/);
        assert(label.includes(String(new Date(grant.membership_expires_at).getFullYear())), 'Experimental membership omitted its actual expiry.');
        assert.doesNotMatch(dom.window.document.body.textContent, /实验|管理员|永久/, 'Internal authorization details leaked into the customer UI.');
        assert.equal(dom.window.document.querySelector('#membership-notice').hidden, true, 'A usable membership received an unnecessary status banner.');
      } finally {dom.window.close();}
    }],
    ['active grants reject unknown bases and permits outside their finite policy bounds', async () => {
      const base = member();
      const server = Date.parse(base.server_time);
      const iso = delta => new Date(server + delta).toISOString();
      const invalid = [
        {...base, access_basis: 'unknown_admin_grant'}, {...base, access_basis: null}, {...base, access_basis: ''},
        {...base, access_basis: 'paid_membership', membership_expires_at: null},
        {...base, access_basis: 'experimental_membership', membership_expires_at: null},
        {...base, access_basis: 'experimental_membership', membership_expires_at: iso(-1000)},
        {...base, access_basis: 'experimental_membership', membership_expires_at: iso(10000), permit_expires_at: iso(20000)},
        {...base, access_basis: 'paid_membership', membership_expires_at: iso(10000), permit_expires_at: iso(20000)},
        ...['paid_membership', 'experimental_membership', 'experimental_account'].map(access_basis =>
          ({...base, access_basis, permit_expires_at: iso(900001)})),
        {...base, access_basis: 'experimental_account', membership_expires_at: null, permit_expires_at: iso(0)},
        {...base, access_basis: 'experimental_account', membership_expires_at: null, permit_expires_at: 'invalid'},
        {...base, access_basis: 'experimental_account', membership_expires_at: 'invalid'},
      ];
      for (const grant of invalid) {
        const dom = await open(grant);
        try {await locked(dom); assert.equal(dom.control.protectedCalls().length, 0);}
        finally {dom.window.close();}
      }
    }],
    ['experimental account permits still lock at their deadline while offline', async () => {
      const dom = await open(member('account-a', {access_basis: 'experimental_account', membership_expires_at: null}));
      try {
        await unlocked(dom);
        dom.window.document.querySelector('[data-action="mode"][data-mode="long"]').click();
        await wait(() => dom.window.document.querySelector('.detail h2')?.textContent === '甲私有作品', 'Experimental fixture selection missing.');
        dom.control.overrides.set('/api/membership/refresh', new Error('Synthetic offline verification'));
        const count = dom.control.protectedCalls().length;
        await dom.control.runTimers(timer => timer.delay > 60000);
        await locked(dom);
        assert.doesNotMatch(dom.window.document.querySelector('#main').textContent, /甲私有作品/);
        assert.equal(dom.control.protectedCalls().length, count);
      } finally {dom.window.close();}
    }],
    ['closing an experiment prevents a late protected response from restoring data', async () => {
      const dom = await open(member('account-a', {access_basis: 'experimental_account', membership_expires_at: null}));
      let pending;
      try {
        await unlocked(dom);
        pending = dom.control.holdNext('/api/state');
        await recheck(dom);
        await wait(() => pending.captured, 'Experimental state response was not held.');
        dom.control.membership = {...dom.control.membership, allowed: false, reason: 'suspended', permit_expires_at: null};
        await recheck(dom); await locked(dom);
        pending.release(); await pause(20);
        assert.equal(dom.window.document.querySelector('.overview-tabs'), null, 'Closed experiment was reopened by an old response.');
        assert.doesNotMatch(dom.window.document.querySelector('#main').textContent, /甲私有作品/);
        const count = dom.control.protectedCalls().length;
        await dom.control.runTimers(timer => timer.delay <= 20000);
        assert.equal(dom.control.protectedCalls().length, count);
      } finally {pending?.release(); dom.window.close();}
    }],
    ['an old membership refresh cannot reopen an experiment after a newer protected denial', async () => {
      const dom = await open(member('account-a', {access_basis: 'experimental_account', membership_expires_at: null}));
      let pending;
      try {
        await unlocked(dom);
        dom.window.document.querySelector('[data-action="mode"][data-mode="long"]').click();
        await wait(() => dom.window.document.querySelector('[data-action="select-book"]'), 'Experimental book selection missing.');
        pending = dom.control.holdNext('/api/membership/refresh');
        await recheck(dom);
        await wait(() => pending.captured, 'Old allowed membership response was not captured.');
        dom.control.overrides.set('/api/view', {status: 403, body: {error: {
          code: 'membership_not_eligible', message: '实验授权已关闭，当前账号暂无工作台权益。',
        }}});
        click(dom, 'select-book');
        await locked(dom);
        dom.control.membership = {...dom.control.membership, allowed: false, reason: 'not_eligible',
          can_read_history: false, access_mode: 'none', permit_expires_at: null};
        const count = dom.control.protectedCalls().length;
        pending.release(); await pause(30);
        assert(dom.window.document.querySelector('#membership-card'), 'Old allowed status reopened a newly denied experiment.');
        assert.equal(dom.window.document.querySelector('.overview-tabs'), null);
        assert.doesNotMatch(dom.window.document.querySelector('#main').textContent, /甲私有作品/);
        assert.doesNotMatch(dom.window.document.querySelector('#membership-account').textContent, /实验授权 · 管理员关闭后停用/);
        assert.equal(dom.control.protectedCalls().length, count, 'Old allowed status restarted protected data requests.');
      } finally {pending?.release(); dom.window.close();}
    }],
    ['experimental membership cannot continue after its true membership cutoff', async () => {
      const grant = member('account-a', {access_basis: 'experimental_membership'});
      grant.membership_expires_at = grant.permit_expires_at = new Date(Date.parse(grant.server_time) + 10000).toISOString();
      const dom = await open(grant);
      try {
        await unlocked(dom);
        dom.control.overrides.set('/api/membership/refresh', new Error('Synthetic offline verification'));
        const count = dom.control.protectedCalls().length;
        await dom.control.runTimers(timer => timer.delay >= 9000 && timer.delay <= 11000);
        await locked(dom);
        assert.equal(dom.window.document.querySelector('.overview-tabs'), null);
        assert.equal(dom.control.protectedCalls().length, count);
      } finally {dom.window.close();}
    }],
    ['legacy data is inspected before any active-account data request', async () => {
      const dom = await open(member());
      try {
        await unlocked(dom);
        const inspected = dom.control.calls.findIndex(call => call.url === '/api/legacy-data/status');
        const firstData = dom.control.calls.findIndex(call => !call.url.startsWith('/api/membership/') && !call.url.startsWith('/api/legacy-data/'));
        assert(inspected >= 0 && inspected < firstData, 'Active account read data before inspecting legacy ownership.');
        assert.equal(dom.control.legacyActions.length, 0, 'No-data startup silently claimed or skipped data.');
      } finally {dom.window.close();}
    }],
    ['legacy ownership requires an explicit checked claim before data opens', async () => {
      const dom = await open(member(), {setup(dom) {
        dom.control.legacy['account-a'] = legacyStatus({available: true, counts: {snapshots: 2, texts: 1, reports: 1}, decision_required: true});
      }});
      try {
        await wait(() => dom.window.document.querySelector('#legacy-claim-card [data-action="legacy-claim"]'), 'Legacy ownership choice did not render.');
        const dataCalls = () => dom.control.calls.filter(call => !call.url.startsWith('/api/membership/') && !call.url.startsWith('/api/legacy-data/'));
        assert.equal(dataCalls().length, 0, 'Data loading started before an explicit legacy decision.');
        assert.equal(dom.control.legacyActions.length, 0, 'First account automatically claimed legacy data.');
        const button = dom.window.document.querySelector('[data-action="legacy-claim"]');
        assert(button?.disabled, 'Claim should require the ownership checkbox.');
        button.click(); await pause(0);
        assert.equal(dom.control.legacyActions.length, 0);
        const checkbox = dom.window.document.querySelector('#legacy-confirm');
        assert(checkbox, 'Explicit legacy ownership checkbox missing.');
        checkbox.checked = true; checkbox.dispatchEvent(new dom.window.Event('change', {bubbles: true}));
        click(dom, 'legacy-claim');
        await unlocked(dom);
        assert.deepEqual(dom.control.legacyActions, [{account: 'account-a', action: 'claim', body: {confirmed: true}}]);
        const claimed = dom.control.calls.findIndex(call => call.url === '/api/legacy-data/claim');
        const reinspection = dom.control.calls.findIndex((call, index) => index > claimed && call.url === '/api/legacy-data/status');
        const opened = dom.control.calls.findIndex(call => call.url === '/api/state');
        assert(reinspection > claimed && opened > reinspection, 'Claim was not read back before opening account data.');
        assert.equal(dom.control.legacy['account-a'].original_preserved, true);
      } finally {dom.window.close();}
    }],
    ['skipping legacy ownership is explicit and does not claim or delete originals', async () => {
      const dom = await open(member(), {setup(dom) {
        dom.control.legacy['account-a'] = legacyStatus({available: true, counts: {snapshots: 2}, decision_required: true});
      }});
      try {
        await wait(() => dom.window.document.querySelector('[data-action="legacy-skip"]'), 'Explicit legacy skip action missing.');
        assert.equal(dom.control.calls.some(call => call.url === '/api/state'), false);
        click(dom, 'legacy-skip'); await unlocked(dom);
        assert.deepEqual(dom.control.legacyActions, [{account: 'account-a', action: 'skip', body: {confirmed: true}}]);
        assert.equal(dom.control.legacy['account-a'].claim_status, 'unclaimed');
        assert.equal(dom.control.legacy['account-a'].original_preserved, true);
        const skipped = dom.control.calls.findIndex(call => call.url === '/api/legacy-data/skip');
        assert(dom.control.calls.slice(skipped + 1).some(call => call.url === '/api/legacy-data/status'), 'Skip decision was not read back.');
      } finally {dom.window.close();}
    }],
    ['unreadable legacy data disables claim and permits only explicit preservation and skip', async () => {
      const dom = await open(member(), {setup(dom) {
        dom.control.legacy['account-a'] = legacyStatus({available: true, decision: 'inspection_unavailable',
          decision_required: true, can_skip: true, can_claim: false, counts: {},
          inspection_error: {code: 'legacy_unreadable', message: '旧资料暂时无法安全读取；可保留原资料并跳过，认领需先检查。'}});
      }});
      try {
        await wait(() => dom.window.document.querySelector('[data-action="legacy-skip"]'), 'Unreadable legacy data has no explicit skip.');
        assert.match(dom.window.document.querySelector('#legacy-claim-card').textContent, /暂时无法安全读取/);
        const checkbox = dom.window.document.querySelector('#legacy-confirm');
        const claim = dom.window.document.querySelector('[data-action="legacy-claim"]');
        assert(checkbox.disabled && claim.disabled, 'Unreadable data offered a claim.');
        checkbox.checked = true; checkbox.dispatchEvent(new dom.window.Event('change', {bubbles: true}));
        assert(claim.disabled, 'Programmatic checkbox change enabled an unavailable claim.');
        claim.click(); assert.equal(dom.control.legacyActions.length, 0);
        assert.equal(dom.control.calls.some(call => ['/api/state', '/api/overview', '/api/collect', '/api/overview/refresh'].includes(call.url)), false);
        click(dom, 'legacy-skip'); await unlocked(dom);
        assert.deepEqual(dom.control.legacyActions, [{account: 'account-a', action: 'skip', body: {confirmed: true}}]);
        assert.equal(dom.control.legacy['account-a'].original_preserved, true);
        assert.equal(dom.control.calls.some(call => call.url === '/api/legacy-data/claim'), false);
      } finally {dom.window.close();}
    }],
    ['failed or malformed legacy inspection holds data until a successful retry', async () => {
      for (const failure of [new Error('Synthetic inspection failure'), {status: 200, body: {available: true}}]) {
        const dom = await open(member(), {setup(dom) {dom.control.overrides.set('/api/legacy-data/status', failure);}});
        try {
          await wait(() => dom.window.document.querySelector('[data-action="legacy-retry"]'), 'Legacy inspection error has no retry.');
          assert.equal(dom.control.calls.some(call => ['/api/state', '/api/overview', '/api/collect', '/api/overview/refresh'].includes(call.url)), false,
            'Failed legacy inspection allowed data or automatic work.');
          assert.equal(dom.control.legacyActions.length, 0);
          dom.control.overrides.delete('/api/legacy-data/status');
          click(dom, 'legacy-retry'); await unlocked(dom);
        } finally {dom.window.close();}
      }
    }],
    ['late legacy inspection from another account cannot replace the current account', async () => {
      let pending;
      const dom = await open(member(), {setup(dom) {
        dom.control.legacy['account-a'] = legacyStatus({available: true, counts: {snapshots: 2}, decision_required: true});
        pending = dom.control.holdNext('/api/legacy-data/status');
      }});
      try {
        await wait(() => pending.captured, 'Initial legacy inspection was not held.');
        assert.equal(dom.control.calls.some(call => call.url === '/api/state'), false);
        dom.control.membership = member('account-b');
        await recheck(dom); await unlocked(dom);
        pending.release(); await pause(20);
        assert.equal(dom.window.document.querySelector('#legacy-claim-card'), null, 'Old account legacy prompt replaced the new account.');
        assert.match(dom.window.document.querySelector('#membership-account').textContent, /乙会员/);
        assert.equal(dom.control.legacyActions.length, 0);
      } finally {pending.release(); dom.window.close();}
    }],
    ['read-only access skips legacy mutation and renewal requires ownership inspection', async () => {
      const dom = await open(historyMember(), {setup(dom) {
        dom.control.legacy['account-a'] = legacyStatus({available: true, counts: {snapshots: 2}, decision_required: true});
      }});
      try {
        await unlocked(dom);
        assert.equal(dom.control.calls.some(call => call.url.startsWith('/api/legacy-data/')), false, 'Expired history tried to inspect or claim anonymous data.');
        const before = dom.control.calls.length;
        dom.control.membership = member(); await recheck(dom);
        await wait(() => dom.window.document.querySelector('#legacy-claim-card [data-action="legacy-skip"]'), 'Renewal omitted the pending ownership choice.');
        assert.equal(dom.control.calls.slice(before).some(call => ['/api/state', '/api/overview', '/api/collect', '/api/overview/refresh'].includes(call.url)), false);
        click(dom, 'legacy-skip'); await unlocked(dom);
        assert.equal(dom.control.legacyActions[0].action, 'skip');
      } finally {dom.window.close();}
    }],
    ['cancelled authorization shows a safe notice and removes its URL marker', async () => {
      const dom = await open(denied(), {url: BASE + '?authorization=cancelled&error_description=UNTRUSTED_PROVIDER_TEXT'});
      try {
        await locked(dom);
        await wait(() => !dom.window.document.querySelector('#notice').hidden && /取消/.test(dom.window.document.querySelector('#notice').textContent),
          'Cancelled authorization was not explained.');
        assert.equal(new URL(dom.window.location.href).searchParams.has('authorization'), false);
        assert.doesNotMatch(dom.window.document.body.textContent, /UNTRUSTED_PROVIDER_TEXT/);
        assert.equal(dom.control.legacyActions.length, 0);
      } finally {dom.window.close();}
    }],
    ['expiry removes visible private content and stops data polling', async () => {
      const dom = await open(member());
      try {
        await unlocked(dom);
        dom.window.document.querySelector('[data-action="mode"][data-mode="long"]').click();
        await wait(() => dom.window.document.querySelector('.detail h2')?.textContent === '甲私有作品', 'Fixture selection missing.');
        assert.match(dom.window.document.querySelector('#bridge').textContent, /甲私有作品/);
        click(dom, 'report');
        await wait(() => dom.window.document.querySelector('.report-text'), 'Fixture report did not open.');
        assert(dom.window.document.querySelector('#dialog').open);
        const preserved = JSON.stringify(dom.control.data['account-a']);
        dom.control.membership = denied('expired', member().account);
        await recheck(dom); await locked(dom);
        assert.equal(dom.window.document.querySelector('#dialog').open, false);
        assert.equal(dom.window.document.querySelector('#dialog-body').textContent, '');
        assert.equal(dom.window.document.querySelector('#bridge').textContent, '');
        assert.doesNotMatch(dom.window.document.querySelector('#main').textContent, /甲私有作品/);
        const count = dom.control.protectedCalls().length;
        await dom.control.runTimers(); await dom.control.runTimers();
        assert.equal(dom.control.protectedCalls().length, count, 'Expired workbench continued data polling.');
        assert.equal(JSON.stringify(dom.control.data['account-a']), preserved, 'Locking changed saved data.');
      } finally {dom.window.close();}
    }],
    ['renewal restores the existing account data', async () => {
      const dom = await open(denied('expired', member().account));
      try {
        await locked(dom);
        dom.control.membership = member();
        await recheck(dom); await unlocked(dom);
        assert.equal(dom.window.document.querySelector('#favorite-count').textContent, '1');
        dom.window.document.querySelector('[data-section="favorites"]').click();
        assert.match(dom.window.document.querySelector('#content').textContent, /甲私有作品/);
        assert(dom.control.calls.some(call => call.url === '/api/state'));
      } finally {dom.window.close();}
    }],
    ['expired history access reads reports and TXT without starting new work', async () => {
      const dom = await open(historyMember());
      try {
        await unlocked(dom);
        const notice = dom.window.document.querySelector('#membership-notice');
        assert(notice && !notice.hidden, 'Read-only membership notice missing.');
        assert.match(notice.textContent, /到期|续费/);
        dom.window.document.querySelector('[data-action="mode"][data-mode="long"]').click();
        await wait(() => dom.window.document.querySelector('.detail h2')?.textContent === '甲私有作品', 'Saved book was not readable after expiry.');
        for (const action of ['collect', 'catalog', 'favorite', 'import-text', 'import-snapshot', 'translation-context', 'context']) {
          for (const button of dom.window.document.querySelectorAll(`[data-action="${action}"]`)) {
            assert(button.disabled, `Read-only ${action} control remained active.`);
            button.click();
          }
        }
        assert.doesNotMatch(dom.window.document.querySelector('#bridge').textContent, /分析我刚选|翻译我刚选/);
        click(dom, 'report');
        await wait(() => dom.window.document.querySelector('.report-text'), 'Saved report was not readable after expiry.');
        assert.match(dom.window.document.querySelector('.report-text').textContent, /甲私有作品报告正文/);
        click(dom, 'close-dialog');
        click(dom, 'download-text');
        await wait(() => dom.control.savedDownloads.length === 1, 'Saved TXT was not downloadable after expiry.');
        await dom.control.runTimers(timer => timer.delay <= 20000);
        assert.deepEqual(dom.control.readonlyMutationAttempts, [], 'Read-only UI attempted a new or mutating task.');
        assert.equal(dom.control.calls.some(call => ['/api/collect', '/api/overview/refresh', '/api/jobs/more', '/api/catalog', '/api/translation-context', '/api/favorite'].includes(call.url)), false);
        dom.control.membership = member();
        await recheck(dom);
        await wait(() => !dom.window.document.querySelector('[data-action="collect"]')?.disabled,
          'Renewal did not restore collection controls.');
        assert.equal(dom.window.document.querySelector('.detail h2').textContent, '甲私有作品', 'Renewal lost the selected saved book.');
        assert(!dom.window.document.querySelector('[data-action="favorite"]').disabled);
      } finally {dom.window.close();}
    }],
    ['history access without an offline identity lease still uses authenticated reads', async () => {
      const dom = await open(historyMember('account-a', {identity_expires_at: undefined}));
      try {
        await unlocked(dom);
        dom.window.document.querySelector('[data-section="reports"]').click();
        click(dom, 'report');
        await wait(() => dom.window.document.querySelector('.report-text'), 'Online-only history identity could not read its report.');
        const request = dom.control.calls.find(call => call.url === '/api/reports/account-a-report');
        assert.deepEqual(request.binding, {account: 'account-a', generation: 'account-a-generation-1'});
        assert.deepEqual(dom.control.readonlyMutationAttempts, []);
      } finally {dom.window.close();}
    }],
    ['malformed or revoked history claims fail closed', async () => {
      for (const invalid of [historyMember('account-a', {generation: ''}),
        historyMember('account-a', {reason: 'device_revoked'}),
        historyMember('account-a', {membership_expires_at: 'invalid'}),
        historyMember('account-a', {access_mode: undefined})]) {
        const dom = await open(invalid);
        try {await locked(dom); assert.equal(dom.control.protectedCalls().length, 0);}
        finally {dom.window.close();}
      }
    }],
    ['a current gift period only restores verified paid-history reading', async () => {
      const dom = await open(mixedHistoryMember());
      try {
        await unlocked(dom);
        assert.doesNotMatch(dom.window.document.querySelector('#membership-account').textContent, /会员有效/,
          'A future gift expiry was described as active paid membership.');
        assert.match(dom.window.document.querySelector('#membership-notice').textContent, /历史资料/);
        dom.window.document.querySelector('[data-section="reports"]').click();
        click(dom, 'report');
        await wait(() => dom.window.document.querySelector('.report-text'), 'Eligible paid history did not restore its saved report.');
        assert.match(dom.window.document.querySelector('.report-text').textContent, /甲私有作品报告正文/);
        await dom.control.runTimers(timer => timer.delay <= 20000);
        assert.deepEqual(dom.control.readonlyMutationAttempts, []);
        assert.equal(dom.control.calls.some(call => ['/api/collect', '/api/overview/refresh', '/api/translation-context'].includes(call.url)), false,
          'Current non-paid status started new work through its history permission.');
      } finally {dom.window.close();}
    }],
    ['unverified mixed-history or expired identity claims stay locked', async () => {
      const invalidClaims = [
        {history_eligible: undefined}, {history_eligible: false},
        {history_membership_expires_at: new Date(Date.now() + 86400000).toISOString()},
        {history_membership_expires_at: 'invalid'}, {identity_expires_at: undefined},
        {identity_expires_at: new Date(Date.now() - 1000).toISOString()}, {reason: 'device_revoked'},
      ];
      for (const invalid of invalidClaims) {
        const dom = await open(mixedHistoryMember(invalid));
        try {await locked(dom); assert.equal(dom.control.protectedCalls().length, 0);}
        finally {dom.window.close();}
      }
    }],
    ['quiet permit renewal preserves the selected book and open report', async () => {
      const short = member('account-a', {permit_expires_at: new Date(Date.now() + 45000).toISOString()});
      const dom = await open(short);
      try {
        await unlocked(dom);
        dom.window.document.querySelector('[data-action="mode"][data-mode="long"]').click();
        await wait(() => dom.window.document.querySelector('.detail h2')?.textContent === '甲私有作品', 'Selected book missing before renewal.');
        click(dom, 'report');
        await wait(() => dom.window.document.querySelector('.report-text'), 'Open report missing before renewal.');
        dom.control.membership = member();
        const before = dom.control.calls.length;
        await dom.control.runTimers(timer => timer.delay <= 30000);
        await wait(() => dom.control.calls.slice(before).some(call => call.url === '/api/membership/refresh'),
          'Permit was not refreshed before its deadline.');
        assert.equal(dom.window.document.querySelector('#membership-card'), null);
        assert.equal(dom.window.document.querySelector('.detail h2').textContent, '甲私有作品');
        assert(dom.window.document.querySelector('#dialog').open, 'Quiet renewal closed the current report.');
        assert.match(dom.window.document.querySelector('.report-text').textContent, /甲私有作品报告正文/);
      } finally {dom.window.close();}
    }],
    ['the in-page permit deadline locks data even when verification is offline', async () => {
      const dom = await open(member());
      try {
        await unlocked(dom);
        dom.control.overrides.set('/api/membership/refresh', new Error('Synthetic offline verification'));
        const count = dom.control.protectedCalls().length;
        // Only expire the current permit; leave the unrelated content and
        // membership polling clocks alone.
        await dom.control.runTimers(timer => timer.delay > 60000);
        await locked(dom);
        assert.doesNotMatch(dom.window.document.querySelector('#main').textContent, /甲私有作品/);
        assert.equal(dom.control.protectedCalls().length, count);
      } finally {dom.window.close();}
    }],
    ['logout and account switch discard a delayed response from the previous account', async () => {
      const dom = await open(member());
      try {
        await unlocked(dom);
        const pending = dom.control.holdNext('/api/state');
        await recheck(dom);
        await wait(() => pending.captured, 'Could not hold the previous account state response.');
        click(dom, 'membership-logout');
        await locked(dom);
        assert.equal(dom.control.logoutCalls, 1);
        dom.control.membership = member('account-b');
        await recheck(dom); await unlocked(dom);
        pending.release(); await pause(30);
        assert.match(dom.window.document.querySelector('#membership-account').textContent, /乙会员/);
        dom.window.document.querySelector('[data-section="favorites"]').click();
        await wait(() => dom.window.document.querySelector('#content').textContent.includes('乙私有作品'), 'New account data missing.');
        assert.doesNotMatch(dom.window.document.querySelector('#main').textContent, /甲私有作品/);
      } finally {for (const pending of dom.control.held.values()) pending.release(); dom.window.close();}
    }],
    ['a membership API denial locks the same data gate', async () => {
      const dom = await open(member());
      try {
        await unlocked(dom);
        dom.control.overrides.set('/api/state', {status: 403, body: {error: {code: 'membership_expired', message: '会员已到期'}}});
        await recheck(dom); await locked(dom);
        assert.equal(dom.window.document.querySelector('#bridge').textContent, '');
      } finally {dom.window.close();}
    }],
    ['a stale window cannot mutate the account selected in another window', async () => {
      const dom = await open(member());
      try {
        await unlocked(dom);
        dom.window.document.querySelector('[data-action="mode"][data-mode="long"]').click();
        await wait(() => dom.window.document.querySelector('[data-action="select-book"]'), 'Old account view missing.');
        const preserved = JSON.stringify(dom.control.data['account-b']);
        // Another browser window has switched the server session to B. This
        // window still displays A and has not queried membership again.
        dom.control.membership = member('account-b');
        click(dom, 'select-book');
        await locked(dom);
        const rejected = dom.control.staleAccountRejects.find(call => call.url === '/api/view');
        assert(rejected, 'Old window did not send a bound mutation for rejection.');
        assert.deepEqual(rejected.binding, {account: 'account-a', generation: 'account-a-generation-1'});
        assert.equal(JSON.stringify(dom.control.data['account-b']), preserved, 'Old account action modified account B.');
        assert.doesNotMatch(dom.window.document.querySelector('#main').textContent, /乙私有作品/);
        assert.equal(dom.control.calls.some(call => call.url.includes('/account-b-snapshot')), false, 'Stale window fetched account B data.');
        assert.deepEqual(dom.control.missingBindings, []);
      } finally {dom.window.close();}
    }],
    ['a stale window logout cannot revoke the replacement account', async () => {
      const dom = await open(member());
      try {
        await unlocked(dom);
        dom.control.membership = member('account-b');
        click(dom, 'membership-logout');
        await locked(dom);
        await wait(() => dom.control.staleAccountRejects.some(call => call.url === '/api/membership/logout'), 'Stale logout was not rejected.');
        await wait(() => !dom.window.document.querySelector('[data-action="membership-refresh"]')?.disabled,
          'Rejected logout did not finish rendering.');
        assert.equal(dom.control.logoutCalls, 0, 'Logout revoked the wrong account.');
        assert.equal(dom.control.membership.account.id, 'account-b');
        assert.deepEqual(dom.control.staleAccountRejects.find(call => call.url === '/api/membership/logout').binding,
          {account: 'account-a', generation: 'account-a-generation-1'});
        assert.deepEqual(dom.control.missingBindings, []);
      } finally {dom.window.close();}
    }],
    ['TXT download uses the displayed account binding before saving', async () => {
      const dom = await open(member());
      try {
        await unlocked(dom);
        dom.window.document.querySelector('[data-action="mode"][data-mode="long"]').click();
        await wait(() => dom.window.document.querySelector('[data-action="download-text"]'), 'Bound TXT download button missing.');
        click(dom, 'download-text');
        await wait(() => dom.control.savedDownloads.length === 1, 'Authorized TXT content was not saved.');
        const request = dom.control.calls.find(call => call.url === '/api/texts/account-a-text/download');
        assert.deepEqual(request.binding, {account: 'account-a', generation: 'account-a-generation-1'});
        assert.equal(await dom.control.downloadBlobs[0].text(), '甲私有作品合成正文');
        assert.deepEqual(dom.control.missingBindings, []);
      } finally {dom.window.close();}
    }],
    ['a TXT response completing after an account switch is not saved', async () => {
      const dom = await open(member());
      try {
        await unlocked(dom);
        dom.window.document.querySelector('[data-action="mode"][data-mode="long"]').click();
        await wait(() => dom.window.document.querySelector('[data-action="download-text"]'), 'Bound TXT download button missing.');
        const pending = dom.control.holdNext('/api/texts/account-a-text/download');
        click(dom, 'download-text');
        await wait(() => pending.captured, 'TXT response was not held.');
        dom.control.membership = member('account-b');
        await recheck(dom); await unlocked(dom);
        pending.release(); await pause(20);
        assert.equal(dom.control.downloadBlobs.length, 0, 'Previous account TXT was exposed as an object URL.');
        assert.equal(dom.control.savedDownloads.length, 0, 'Previous account TXT was saved after account switch.');
      } finally {for (const pending of dom.control.held.values()) pending.release(); dom.window.close();}
    }],
    ['preferences are separated by account and anonymous preferences are not adopted', async () => {
      const anonymous = JSON.stringify({fanqie: {channel: '男频', category: '都市脑洞', board: '阅读榜'}});
      const dom = await open(member(), {setup(dom) {
        const storage = dom.window.localStorage;
        storage.setItem('fagao-collection-options', anonymous);
        storage.setItem('fagao-collection-options:account-a', JSON.stringify({fanqie: {
          channel: '男频', category: '玄幻脑洞', board: '阅读榜', limit: 10000, max_pages: 100,
        }}));
        storage.setItem('fagao-options-version:account-a', '3');
      }});
      try {
        await unlocked(dom);
        dom.window.document.querySelector('[data-action="mode"][data-mode="long"]').click();
        await wait(() => dom.window.document.querySelector('#board-channel')?.value === '男频', 'Account A preferences not restored.');
        const board = dom.window.document.querySelector('#board-kind');
        board.value = '新书榜'; board.dispatchEvent(new dom.window.Event('change', {bubbles: true}));
        await wait(() => JSON.parse(dom.window.localStorage.getItem('fagao-collection-options:account-a')).fanqie.board === '新书榜', 'Account A preference was not saved in its namespace.');
        click(dom, 'membership-logout'); await locked(dom);
        dom.control.membership = member('account-b');
        await recheck(dom); await unlocked(dom);
        dom.window.document.querySelector('[data-action="mode"][data-mode="long"]').click();
        await wait(() => dom.window.document.querySelector('#board-channel'), 'Account B board did not render.');
        assert.equal(dom.window.document.querySelector('#board-channel').value, '女频', 'Account B inherited another account or anonymous preferences.');
        assert.equal(dom.window.localStorage.getItem('fagao-collection-options'), anonymous, 'Anonymous preferences were overwritten.');
        assert.equal(JSON.parse(dom.window.localStorage.getItem('fagao-collection-options:account-a')).fanqie.board, '新书榜');
      } finally {dom.window.close();}
    }],
    ['source access denial is not reported as a membership failure', async () => {
      const dom = await open(member(), {setup(dom) {
        dom.control.overrides.set('/api/overview', {status: 403, body: {error: {code: 'source_access_denied', message: '来源暂时拒绝读取'}}});
      }});
      try {
        await unlocked(dom);
        await wait(() => dom.window.document.querySelector('#content').textContent.includes('来源暂时拒绝读取'), 'Source failure was not shown.');
        assert.equal(dom.window.document.querySelector('#membership-card'), null);
        assert.match(dom.window.document.querySelector('#membership-account').textContent, /甲会员/);
      } finally {dom.window.close();}
    }],
    ['member names render as text and not executable markup', async () => {
      const dom = await open(member('account-a', {account: {id: 'account-a', display_name: '<img src=x onerror=unsafe()>'}}));
      try {
        await unlocked(dom);
        const header = dom.window.document.querySelector('#membership-account');
        assert.match(header.textContent, /<img src=x onerror=unsafe\(\)>/);
        assert.equal(header.querySelector('img'), null);
      } finally {dom.window.close();}
    }],
    ['different membership denial reasons have distinct visible explanations', async () => {
      const titles = [];
      for (const reason of ['not_authenticated', 'not_eligible', 'expired', 'suspended', 'device_revoked', 'device_limit', 'verification_unavailable']) {
        const dom = await open(denied(reason));
        try {
          await locked(dom);
          titles.push(dom.window.document.querySelector('#membership-title').textContent);
          assert.equal(dom.control.protectedCalls().length, 0);
        } finally {dom.window.close();}
      }
      assert.equal(new Set(titles).size, titles.length, 'Different membership failures reused the same explanation.');
    }],
    ['a non-official authorization URL is rejected before navigation', async () => {
      const dom = await open();
      try {
        await locked(dom);
        dom.control.overrides.set('/api/membership/connect', {status: 200, body: {authorization_url: 'https://untrusted.invalid/workbench/authorize'}});
        click(dom, 'membership-connect');
        await wait(() => dom.window.document.querySelector('#notice').textContent.includes('授权'), 'Invalid authorization destination was not rejected.');
        assert.equal(dom.window.location.href, BASE);
        assert.equal(dom.control.errors.length, 0);
        assert.equal(dom.control.protectedCalls().length, 0);
      } finally {dom.window.close();}
    }],
  ];
  for (const [name, check] of cases) {
    await check(); passed++; console.log('PASS ' + name);
  }
  console.log(JSON.stringify({suite: 'membership-ui', passed, synthetic_accounts: true, network_requests: 0}));
})().catch(error => {console.error(error.stack || error); process.exitCode = 1;});
