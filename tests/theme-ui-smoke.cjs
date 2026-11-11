// 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
// Appearance behavior against the real page and script. All data is synthetic;
// no server, network, credentials, user browser profile, or real account is used.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM, VirtualConsole} = require(process.env.FAGAO_QA_NODE_MODULES
  ? path.join(process.env.FAGAO_QA_NODE_MODULES, 'jsdom') : 'jsdom');
const ROOT = path.resolve(__dirname, '..');
const HTML = fs.readFileSync(path.join(ROOT, 'web/index.html'), 'utf8');
const THEME = fs.readFileSync(path.join(ROOT, 'web/theme.js'), 'utf8');
const APP = fs.readFileSync(path.join(ROOT, 'web/app.js'), 'utf8');
const KEY = 'fagao:appearance';
const MODES = ['light', 'eye-care', 'dark', 'system'];
const LABELS = {light: '浅色', 'eye-care': '护眼', dark: '深色', system: '跟随系统'};
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function wait(predicate, message) {
  for (let i = 0; i < 80; i++) {if (predicate()) return; await pause(5);}
  throw new Error(message);
}
function storageValues(storage) {
  return Object.fromEntries(Array.from({length: storage.length}, (_, i) => storage.key(i))
    .map(key => [key, storage.getItem(key)]));
}
async function open(options = {}) {
  const errors = [], writes = [], calls = [], mediaListeners = new Set();
  const vc = new VirtualConsole();
  vc.on('jsdomError', error => errors.push(String(error)));
  const dom = new JSDOM(HTML, {url: 'http://127.0.0.1:18376/', runScripts: 'outside-only',
    pretendToBeVisual: true, virtualConsole: vc});
  const w = dom.window, d = w.document;
  w.addEventListener('error', event => errors.push(String(event.error || event.message)));
  const storage = w.localStorage;
  for (const [key, value] of Object.entries(options.values || {})) storage.setItem(key, value);
  if (options.preference !== undefined) storage.setItem(KEY, options.preference);
  const setItem = w.Storage.prototype.setItem;
  w.Storage.prototype.setItem = function (key, value) {
    if (this === storage) {
      writes.push({key: String(key), value: String(value)});
      if (options.quotaBlocked) throw new w.DOMException('Synthetic storage quota', 'QuotaExceededError');
    }
    return setItem.call(this, key, value);
  };
  if (options.storageBlocked) Object.defineProperty(w, 'localStorage', {
    configurable: true, get() {throw new w.DOMException('Synthetic denied storage', 'SecurityError');},
  });
  const media = {matches: !!options.systemDark, media: '(prefers-color-scheme: dark)',
    addEventListener(type, listener) {if (type === 'change') mediaListeners.add(listener);},
    removeEventListener(type, listener) {if (type === 'change') mediaListeners.delete(listener);},
  };
  if (options.legacyMedia) {
    delete media.addEventListener; delete media.removeEventListener;
    media.addListener = listener => mediaListeners.add(listener);
    media.removeListener = listener => mediaListeners.delete(listener);
  }
  if (!options.noMedia) w.matchMedia = query => {assert.equal(query, media.media); return media;};
  else w.matchMedia = undefined;
  w.fetch = async (input, init = {}) => {
    const url = new URL(input, w.location.href);
    assert.equal(url.origin, w.location.origin, 'A theme operation attempted external HTTP.');
    const call = {path: url.pathname, method: init.method || 'GET'};
    calls.push(call);
    assert(options.membership, 'Appearance alone must not make HTTP requests.');
    assert.equal(call.path, '/api/membership/status', 'Appearance requested protected data or mutated membership.');
    assert.equal(call.method, 'GET');
    return {ok: true, status: 200, json: async () => ({allowed: false,
      reason: options.membership, account: options.membership === 'expired'
        ? {id: 'synthetic-expired-account', display_name: '合成到期账号'} : null,
      membership_expires_at: options.membership === 'expired' ? '2020-01-01T00:00:00Z' : null,
      permit_expires_at: null, server_time: new Date().toISOString(), generation: 'synthetic-generation'})};
  };
  w.XMLHttpRequest = class {constructor() {throw new Error('XHR is forbidden in appearance tests.');}};
  w.WebSocket = class {constructor() {throw new Error('WebSocket is forbidden in appearance tests.');}};
  w.eval(THEME);
  const early = {mode: d.documentElement.dataset.themeMode, resolved: d.documentElement.dataset.theme};
  if (d.readyState === 'loading') await new Promise(resolve => d.addEventListener('DOMContentLoaded', resolve, {once: true}));
  if (options.membership) {
    // Membership polling is unrelated to this test; freeze its scheduled timer.
    const nativeTimeout = w.setTimeout.bind(w); let timer = -1;
    w.setTimeout = (callback, delay, ...args) => delay >= 10000 ? timer-- : nativeTimeout(callback, delay, ...args);
    w.HTMLDialogElement.prototype.showModal = function () {this.open = true;};
    w.HTMLDialogElement.prototype.close = function () {this.open = false;};
    w.eval(APP);
    await wait(() => d.querySelector('#membership-card [data-action="membership-refresh"]'), 'Membership gate did not settle.');
  }
  const picker = d.getElementById('appearance-picker'), trigger = picker?.querySelector('summary');
  assert(picker && trigger, 'Actual page lacks the appearance control.');
  const radios = Array.from(picker.querySelectorAll('input[type="radio"]'));
  const control = {dom, w, d, picker, trigger, radios, errors, writes, calls, early, storage,
    choose(mode) {
      picker.open = true;
      const input = radios.find(item => item.value === mode);
      assert(input, 'Missing appearance choice: ' + mode);
      input.click();
    },
    system(dark) {media.matches = dark; for (const listener of mediaListeners) listener({matches: dark, media: media.media});},
    otherTab(value, key = KEY, area = storage) {
      const oldValue = area.getItem(key);
      if (value === null) area.removeItem(key); else setItem.call(area, key, value);
      w.dispatchEvent(new w.StorageEvent('storage', {key, oldValue, newValue: value, storageArea: area}));
    },
    check(mode, resolved = mode) {
      assert.equal(d.documentElement.dataset.themeMode, mode);
      assert.equal(d.documentElement.dataset.theme, resolved);
      assert.equal(d.documentElement.style.colorScheme, resolved === 'dark' ? 'dark' : 'light');
      assert.deepEqual(radios.filter(input => input.checked).map(input => input.value), [mode]);
      assert(trigger.getAttribute('aria-label').includes(LABELS[mode]), 'Trigger does not announce selected mode.');
      assert.deepEqual(errors, []);
    },
    close() {assert.deepEqual(errors, []); dom.window.close();},
  };
  return control;
}
const cases = [];
function test(name, run) {cases.push([name, run]);}

test('early theme script precedes styles and executes synchronously', async () => {
  const c = await open({preference: 'dark'});
  try {
    const script = c.d.querySelector('head script[src="/theme.js"]');
    const css = c.d.querySelector('head link[rel="stylesheet"]');
    assert(script && css && !script.defer && !script.async);
    assert(script.compareDocumentPosition(css) & c.w.Node.DOCUMENT_POSITION_FOLLOWING);
    assert.deepEqual(c.early, {mode: 'dark', resolved: 'dark'});
    assert.equal(c.writes.length, 0, 'Loading the page must not rewrite the saved preference.');
    assert.equal(c.calls.length, 0);
  } finally {c.close();}
});

test('all four controls are labelled native radios outside locked content', async () => {
  const c = await open();
  try {
    assert.deepEqual(c.radios.map(input => input.value), MODES);
    assert.equal(new Set(c.radios.map(input => input.name)).size, 1);
    for (const input of c.radios) {
      assert(input.labels.length && input.labels[0].textContent.includes(LABELS[input.value]));
      assert(!input.disabled && !input.closest('[inert]'));
    }
    assert(c.picker.closest('.topbar'));
    assert.equal(c.picker.closest('#content, .sidebar'), null);
    c.check('system', 'light');
  } finally {c.close();}
});

for (const dark of [false, true]) test('fresh browser follows ' + (dark ? 'dark' : 'light') + ' OS preference', async () => {
  const c = await open({systemDark: dark});
  try {c.check('system', dark ? 'dark' : 'light'); assert.equal(c.storage.getItem(KEY), null);}
  finally {c.close();}
});
for (const mode of MODES) test(mode + ' selection survives a page reload', async () => {
  const initial = await open({systemDark: true}); let saved;
  try {
    if (mode === 'system') initial.choose('light');
    initial.choose(mode); initial.check(mode, mode === 'system' ? 'dark' : mode);
    assert.equal(initial.storage.getItem(KEY), mode);
    saved = storageValues(initial.storage);
    assert(initial.writes.every(item => item.key === KEY));
  } finally {initial.close();}
  const reloaded = await open({values: saved, systemDark: true});
  try {reloaded.check(mode, mode === 'system' ? 'dark' : mode); assert.equal(reloaded.writes.length, 0);}
  finally {reloaded.close();}
});

test('system changes remain live only while follow-system is selected', async () => {
  const c = await open();
  try {
    c.system(true); c.check('system', 'dark');
    c.system(false); c.check('system', 'light');
    for (const mode of ['light', 'eye-care', 'dark']) {
      c.choose(mode); c.system(true); c.check(mode); c.system(false); c.check(mode);
    }
    c.choose('system'); c.system(true); c.check('system', 'dark');
  } finally {c.close();}
});

test('legacy media listeners and absent matchMedia degrade safely', async () => {
  const legacy = await open({legacyMedia: true});
  try {legacy.system(true); legacy.check('system', 'dark');} finally {legacy.close();}
  const absent = await open({noMedia: true});
  try {absent.check('system', 'light'); absent.choose('dark'); absent.check('dark');} finally {absent.close();}
});

test('invalid persisted values fall back without interpreting object-property names', async () => {
  for (const value of ['bad', 'toString', '__proto__', '{"theme":"dark"}', '']) {
    const c = await open({preference: value, systemDark: true});
    try {c.check('system', 'dark'); assert.equal(c.writes.length, 0);}
    finally {c.close();}
  }
});

test('cross-tab changes synchronize without writeback or unrelated-storage interference', async () => {
  const c = await open({preference: 'light', systemDark: true});
  try {
    c.otherTab('eye-care'); c.check('eye-care');
    c.otherTab('dark', 'unrelated-display-choice'); c.check('eye-care');
    c.otherTab('dark', KEY, c.w.sessionStorage); c.check('eye-care');
    c.otherTab('invalid'); c.check('system', 'dark');
    c.otherTab(null); c.check('system', 'dark');
    c.otherTab('light'); c.check('light');
    c.storage.clear();
    c.w.dispatchEvent(new c.w.StorageEvent('storage', {key: null, storageArea: c.storage}));
    c.check('system', 'dark'); assert.equal(c.writes.length, 0);
  } finally {c.close();}
});

test('restoring a cached page reloads the current browser preference', async () => {
  const c = await open({preference: 'dark'});
  try {
    c.storage.setItem(KEY, 'eye-care'); c.writes.length = 0;
    c.w.dispatchEvent(new c.w.PageTransitionEvent('pageshow', {persisted: true}));
    c.check('eye-care'); assert.equal(c.writes.length, 0);
  } finally {c.close();}
});

test('blocked storage access still allows all in-page choices', async () => {
  const c = await open({storageBlocked: true, systemDark: true});
  try {
    c.check('system', 'dark');
    for (const mode of MODES) {c.choose(mode); c.check(mode, mode === 'system' ? 'dark' : mode);}
    c.choose('eye-care');
    c.w.dispatchEvent(new c.w.PageTransitionEvent('pageshow', {persisted: true}));
    c.check('eye-care');
    assert.equal(c.calls.length, 0);
  } finally {c.close();}
});

test('quota errors do not revert the chosen appearance or touch other preferences', async () => {
  const seed = {'fagao-history-views:synthetic-account': '{"long":"synthetic-snapshot"}',
    'fagao-collection-options:synthetic-account': '{"limit":10000}'};
  const c = await open({quotaBlocked: true, values: seed});
  try {
    c.choose('eye-care'); c.check('eye-care');
    assert.deepEqual(storageValues(c.storage), seed);
    assert.deepEqual(c.writes.map(item => item.key), [KEY]);
  } finally {c.close();}
});

test('changing appearance preserves account-scoped reading and collection preferences', async () => {
  const seed = {'fagao-history-views:synthetic-account': '{"long":"synthetic-snapshot"}',
    'fagao-collection-options:synthetic-account': '{"limit":10000}', unrelated: 'keep'};
  const c = await open({values: seed});
  try {
    for (const mode of MODES) c.choose(mode);
    const actual = storageValues(c.storage); delete actual[KEY];
    assert.deepEqual(actual, seed);
    assert(c.writes.every(item => item.key === KEY));
  } finally {c.close();}
});

test('Escape closes the picker and returns focus to the trigger', async () => {
  const c = await open();
  try {
    c.picker.open = true; c.radios[0].focus();
    const event = new c.w.KeyboardEvent('keydown', {key: 'Escape', bubbles: true, cancelable: true});
    c.radios[0].dispatchEvent(event);
    assert(event.defaultPrevented); assert.equal(c.picker.open, false);
    assert.equal(c.d.activeElement, c.trigger);
    assert.equal(c.trigger.getAttribute('aria-expanded'), 'false');
  } finally {c.close();}
});

test('outside pointer and focus close the picker while inside interaction stays open', async () => {
  const c = await open();
  try {
    c.picker.open = true;
    c.radios[0].dispatchEvent(new c.w.Event('pointerdown', {bubbles: true}));
    assert.equal(c.picker.open, true);
    c.d.getElementById('main').dispatchEvent(new c.w.Event('pointerdown', {bubbles: true}));
    assert.equal(c.picker.open, false);
    c.picker.open = true; c.radios[0].focus(); c.d.getElementById('main-site').focus();
    assert.equal(c.picker.open, false);
    c.picker.open = true; c.radios[0].focus(); c.radios[1].focus();
    assert.equal(c.picker.open, true);
    await pause(0);
    assert.equal(c.trigger.getAttribute('aria-expanded'), 'true');
    c.radios[1].blur(); await pause(5);
    assert.equal(c.picker.open, false, 'Losing focus without a related target left the picker open.');
  } finally {c.close();}
});

for (const membership of ['not_authenticated', 'expired']) test('appearance remains usable for ' + membership + ' without membership or data mutations', async () => {
  const c = await open({membership});
  try {
    assert(c.d.body.classList.contains('membership-locked'));
    assert.equal(c.d.querySelector('.sidebar').inert, true);
    assert.equal(c.picker.closest('[inert]'), null);
    const before = JSON.stringify(c.calls);
    for (const mode of MODES) {c.choose(mode); c.check(mode, mode === 'system' ? 'light' : mode);}
    assert.equal(JSON.stringify(c.calls), before, 'Appearance interaction triggered an account/data request.');
    assert.equal(c.calls.length, 1, 'Unexpected startup network calls.');
    assert(c.d.querySelector('#membership-card'), 'Appearance changed the membership gate.');
  } finally {c.close();}
});

(async () => {
  let passed = 0;
  for (const [name, run] of cases) {
    try {await run(); passed++;}
    catch (error) {error.message = name + ': ' + error.message; throw error;}
  }
  console.log(JSON.stringify({suite: 'appearance-ui', passed, failed: 0,
    synthetic_accounts: true, network_requests: 0, real_browser_pixels_verified: false,
    native_radio_arrow_keys_verified: false}));
})().catch(error => {console.error(error); process.exitCode = 1;});
