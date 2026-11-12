// 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
(() => {
  'use strict';

  const byId = id => document.getElementById(id);
  const form = byId('draft-form');
  const fields = ['work_title', 'pen_name', 'subject', 'body'];
  const blank = Object.fromEntries(fields.map(key => [key, '']));
  let current = {...blank};
  let drafts = [];
  let busy = false;
  let ready = false;
  let componentPlatform = '';
  let launchAttempted = false;
  let conflict = false;

  async function request(route, payload) {
    const options = {headers: {'X-Fagao-Request': '1'}, credentials: 'same-origin', cache: 'no-store'};
    if (payload !== undefined) {
      options.method = 'POST';
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(payload);
    }
    let response;
    try {
      response = await fetch('/api/submissions/' + route, options);
    } catch {
      throw new Error('连不上本地工作台。请确认工作台已启动，再试一次。');
    }
    const result = await response.json();
    if (!response.ok || result.error) {
      const error = new Error(result.error?.message || '操作没有完成，请重试。');
      error.code = result.error?.code;
      error.status = response.status;
      throw error;
    }
    return result;
  }

  function values() {
    return Object.fromEntries(fields.map(key => [key, form.elements.namedItem(key).value]));
  }

  function hasContent(draft) {
    return fields.some(key => draft[key].trim());
  }

  function isDirty() {
    return fields.some(key => form.elements.namedItem(key).value !== current[key]);
  }

  function updateControls() {
    const changed = isDirty();
    const filled = hasContent(values());
    byId('draft-fields').disabled = busy || !ready;
    byId('new-draft').disabled = busy || !ready;
    byId('delete-draft').hidden = !current.id;
    byId('delete-draft').disabled = busy || conflict;
    byId('restore-draft').hidden = !current.id || !changed || conflict;
    byId('restore-draft').disabled = busy;
    byId('save-draft').disabled = busy || !ready || !changed || !filled || conflict;
    byId('open-draft').disabled = busy || !ready || !filled || conflict;
    byId('open-assistant').disabled = busy;
    byId('save-copy').disabled = busy || !filled;
    byId('load-latest').disabled = busy;
    byId('retry-loading').disabled = busy;
    byId('draft-list').querySelectorAll('button').forEach(button => { button.disabled = busy || !ready; });
    byId('draft-status').textContent = changed ? '有未保存的修改' : current.id ? '已保存到本机' : '';
  }

  function renderList() {
    const list = byId('draft-list');
    const fragment = document.createDocumentFragment();
    for (const draft of drafts) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'draft-option';
      button.disabled = busy || !ready;
      button.dataset.id = draft.id;
      button.setAttribute('aria-current', String(draft.id === current.id));
      const title = document.createElement('strong');
      title.textContent = draft.work_title || draft.subject || '未命名草稿';
      button.append(title);
      if (draft.updated_at) {
        const date = new Date(draft.updated_at);
        if (Number.isFinite(date.getTime())) {
          const time = document.createElement('small');
          time.textContent = date.toLocaleString('zh-CN', {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'});
          button.append(time);
        }
      }
      fragment.append(button);
    }
    list.replaceChildren(fragment);
    const empty = byId('shelf-empty');
    empty.hidden = drafts.length > 0;
    empty.textContent = '还没有草稿';
  }

  function hideNotice() {
    conflict = false;
    byId('submission-notice').hidden = true;
    byId('conflict-actions').hidden = true;
    byId('retry-loading').hidden = true;
  }

  function showError(error) {
    conflict = ['submission_conflict', 'submission_not_found'].includes(error.code) && Boolean(current.id);
    byId('notice-message').textContent = conflict
      ? '这份草稿已在其他窗口修改或删除。你写的内容仍在这里。'
      : error.message;
    byId('submission-notice').hidden = false;
    byId('conflict-actions').hidden = !conflict;
    byId('retry-loading').hidden = ready;
  }

  async function run(operation) {
    if (busy) return;
    busy = true;
    updateControls();
    byId('editor-feedback').textContent = '';
    try {
      await operation();
    } catch (error) {
      showError(error);
    } finally {
      busy = false;
      updateControls();
    }
  }

  function showDraft(draft) {
    current = draft || {...blank};
    fields.forEach(key => { form.elements.namedItem(key).value = current[key]; });
    hideNotice();
    renderList();
    updateControls();
  }

  const draftUrl = id => 'draft?id=' + encodeURIComponent(id);

  async function save(asCopy = false) {
    const draft = values();
    if (!asCopy && current.id && !isDirty()) return current;
    if (!hasContent(draft)) throw new Error('请先写一点内容，或使用「删除草稿」移除这份草稿。');
    if (!asCopy && current.id) Object.assign(draft, {id: current.id, revision: current.revision});
    const {draft: saved} = await request('save', draft);
    drafts = [saved, ...drafts.filter(item => item.id !== saved.id)];
    showDraft(saved);
    return saved;
  }

  async function saveBeforeLeaving() {
    if (isDirty()) {
      if (!current.id && !hasContent(values())) return;
      await save();
    }
  }

  async function selectDraft(id) {
    if (id === current.id && id) return;
    await saveBeforeLeaving();
    if (!id) {
      showDraft(null);
      return;
    }
    try {
      showDraft((await request(draftUrl(id))).draft);
    } catch (error) {
      if (error.code !== 'submission_not_found') throw error;
      await getList();
      throw new Error('选中的草稿已在其他窗口删除。');
    }
  }

  async function getList() {
    const result = await request('drafts');
    drafts = result.drafts;
    renderList();
  }

  async function componentStatus() {
    const hint = byId('assistant-hint');
    let message;
    try {
      const result = await request('component');
      componentPlatform = result.platform;
      message = result.available ? '' : result.message || '投稿组件需要检查，请点「打开投稿助手」重试。';
    } catch {
      message = '未能检查投稿组件，请点「打开投稿助手」重试。';
    }
    // An optional preflight may finish after the user has already opened the window.
    if (!launchAttempted) {
      hint.textContent = message;
      hint.hidden = !message;
    }
  }

  async function initialize() {
    hideNotice();
    await getList();
    if (drafts.length) {
      try {
        showDraft((await request(draftUrl(drafts[0].id))).draft);
      } catch (error) {
        if (error.code !== 'submission_not_found') throw error;
        await getList();
        showDraft(null);
      }
    }
    ready = true;
    updateControls();
  }

  async function openAssistant(useDraft) {
    launchAttempted = true;
    const button = byId(useDraft ? 'open-draft' : 'open-assistant');
    const label = button.firstElementChild;
    const originalLabel = label.textContent;
    const hint = byId('assistant-hint');
    const feedback = useDraft ? byId('editor-feedback') : hint;
    hint.hidden = useDraft;
    feedback.hidden = false;
    feedback.textContent = '正在打开本机投稿窗口…';
    label.textContent = '正在打开…';
    button.setAttribute('aria-busy', 'true');
    const payload = {appearance: document.documentElement.dataset.themeMode || 'system'};
    try {
      if (useDraft) {
        const saved = await save();
        Object.assign(payload, {id: saved.id, revision: saved.revision});
      }
      const result = await request('open', payload);
      if (result.opened !== true) throw new Error('未收到投稿窗口的确认，请重新打开。');
      feedback.textContent = componentPlatform === 'win32'
        ? '已打开投稿助手。可从任务栏切换到「发糕投稿助手」。'
        : '已打开投稿助手，请切换到「发糕投稿助手」窗口。';
    } catch (error) {
      if (useDraft && !payload.id) {
        feedback.textContent = '';
        throw error;  // Saving conflicts use the editor's recovery actions.
      }
      feedback.textContent = error.message;
    } finally {
      label.textContent = originalLabel;
      button.removeAttribute('aria-busy');
    }
  }

  form.addEventListener('input', () => {
    byId('editor-feedback').textContent = '';
    updateControls();
  });
  form.addEventListener('submit', event => {
    event.preventDefault();
    if (!byId('save-draft').disabled) run(() => save());
  });
  byId('new-draft').addEventListener('click', () => run(async () => {
    await selectDraft(null);
    requestAnimationFrame(() => byId('work-title').focus());
  }));
  byId('draft-list').addEventListener('click', event => {
    const button = event.target.closest('button[data-id]');
    if (button) run(() => selectDraft(button.dataset.id));
  });
  byId('restore-draft').addEventListener('click', () => {
    if (window.confirm('放弃本页修改，恢复上次保存的内容？')) showDraft(current);
  });
  byId('delete-draft').addEventListener('click', () => {
    if (!current.id) return;
    const name = current.work_title || current.subject || '这份草稿';
    if (!window.confirm('删除「' + name + '」和本页对它的修改？')) return;
    run(async () => {
      await request('delete', {id: current.id, revision: current.revision});
      drafts = drafts.filter(item => item.id !== current.id);
      showDraft(null);
      byId('editor-feedback').textContent = '草稿已删除。';
    });
  });
  byId('save-copy').addEventListener('click', () => run(async () => {
    await save(true);
    await getList();
    byId('editor-feedback').textContent = '修改已另存为一份新草稿。';
  }));
  byId('load-latest').addEventListener('click', () => {
    if (isDirty() && !window.confirm('放弃本页修改，读取这份草稿在本机保存的最新版本？')) return;
    run(async () => {
      await getList();
      const id = current.id;
      showDraft(drafts.some(item => item.id === id) ? (await request(draftUrl(id))).draft : null);
    });
  });
  byId('open-draft').addEventListener('click', () => run(() => openAssistant(true)));
  byId('open-assistant').addEventListener('click', () => run(() => openAssistant(false)));
  byId('retry-loading').addEventListener('click', () => run(initialize));

  window.addEventListener('beforeunload', event => {
    if (!isDirty()) return;
    event.preventDefault();
    event.returnValue = '';
  });
  document.querySelectorAll('a[href="/"]').forEach(link => {
    link.addEventListener('click', event => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || event.button !== 0) return;
      if (!isDirty() && !busy) return;
      event.preventDefault();
      run(async () => {
        await saveBeforeLeaving();
        location.assign(link.href);
      });
    });
  });

  componentStatus();
  // The standalone assistant can open while the draft list loads.
  initialize().catch(showError);
})();
