// 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
'use strict';
// Appearance is a device preference, independent of account and collection data.
// This small, synchronous script runs before the stylesheet and first paint.
(() => {
  const STORAGE_KEY = 'fagao:appearance';
  const labels = {light: '浅色', 'eye-care': '护眼', dark: '深色', system: '跟随系统'};
  const normalize = value => Object.prototype.hasOwnProperty.call(labels, value) ? value : 'system';
  const root = document.documentElement;
  let picker = null;
  let trigger = null;
  let options = [];
  let media = null;
  try {
    if (typeof window.matchMedia === 'function') media = window.matchMedia('(prefers-color-scheme: dark)');
  } catch { /* Browsers without system-theme detection use the light palette. */ }

  function readPreference(fallback = 'system') {
    try { return normalize(window.localStorage.getItem(STORAGE_KEY)); }
    catch { return fallback; }
  }
  let preference = readPreference();

  function apply() {
    const resolved = preference === 'system' ? (media?.matches ? 'dark' : 'light') : preference;
    root.dataset.theme = resolved;
    root.dataset.themeMode = preference;
    root.style.colorScheme = resolved === 'dark' ? 'dark' : 'light';
    for (const input of options) {
      input.checked = input.value === preference;
      input.closest('.appearance-choice').dataset.selected = String(input.checked);
    }
    if (trigger) {
      trigger.setAttribute('aria-label', `外观，当前${labels[preference]}`);
      trigger.title = `外观：${labels[preference]}`;
    }
  }

  function choose(value) {
    preference = normalize(value);
    apply();
    try { window.localStorage.setItem(STORAGE_KEY, preference); }
    catch { /* The current page can still change appearance if storage is blocked. */ }
  }

  apply();
  const followSystem = () => { if (preference === 'system') apply(); };
  if (typeof media?.addEventListener === 'function') media.addEventListener('change', followSystem);
  else if (typeof media?.addListener === 'function') media.addListener(followSystem);

  window.addEventListener('storage', event => {
    if (event.key !== null && event.key !== STORAGE_KEY) return;
    try {
      if (event.storageArea && event.storageArea !== window.localStorage) return;
    } catch { return; }
    preference = readPreference(preference);
    apply();
  });
  window.addEventListener('pageshow', event => {
    if (!event.persisted) return;
    preference = readPreference(preference);
    apply();
  });

  function bindControls() {
    picker = document.getElementById('appearance-picker');
    if (!picker) return;
    trigger = picker.querySelector('summary');
    options = Array.from(picker.querySelectorAll('input[name="fagao-appearance"]'));
    apply();

    const syncExpanded = () => trigger.setAttribute('aria-expanded', String(picker.open));
    const close = () => { picker.open = false; syncExpanded(); };
    syncExpanded();
    picker.addEventListener('toggle', syncExpanded);
    picker.addEventListener('change', event => {
      if (!options.includes(event.target) || !event.target.checked) return;
      choose(event.target.value);
    });
    picker.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || !picker.open) return;
      event.preventDefault();
      event.stopPropagation();
      close();
      trigger.focus();
    });
    document.addEventListener('pointerdown', event => {
      if (picker.open && !picker.contains(event.target)) close();
    });
    picker.addEventListener('focusout', event => {
      if (event.relatedTarget && !picker.contains(event.relatedTarget)) close();
      else if (!event.relatedTarget) window.setTimeout(() => {
        if (!picker.contains(document.activeElement)) close();
      }, 0);
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bindControls, {once: true});
  else bindControls();
})();
