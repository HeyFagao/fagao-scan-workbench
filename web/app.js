'use strict';
(() => {
  const $ = (selector) => document.querySelector(selector);
  const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const safeURL = (value) => { try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : ''; } catch { return ''; } };
  const modes = {long:'长篇',short:'短篇'};
  const fallbackPlatforms = [
    {id:'fanqie',name:'番茄小说',mode:'long'}, {id:'qidian',name:'起点中文网',mode:'long'},
    {id:'ciweimao',name:'刺猬猫',mode:'long'}, {id:'heiyan',name:'黑岩故事会',mode:'short'},
    {id:'fanqie_short',name:'番茄短故事',mode:'short'}
  ];
  const defaults = {
    fanqie:{channel:'女频',category:'古言脑洞',board:'新书榜',limit:30},
    qidian:{channel:'男频',category:'全部',board:'月票榜',limit:30},
    ciweimao:{channel:'综合',category:'全部',board:'月票榜',limit:10},
    heiyan:{channel:'全部',category:'书库',board:'书库',limit:30},
    fanqie_short:{channel:'短篇',category:'全部',board:'热门故事',limit:30}
  };
  let state = null, snapshot = null, section = 'browse', loadingVersion = 0, pendingText = null;
  let saving = Promise.resolve(), pollTimer = null, toastTimer = null, authPending = false, continueAfterLogin = false;
  let searchTimer, activeRequest = false, seenJobs = new Set(), savedOptions = {}, watchingAuth = false, lastAuthCheck = 0;
  try { savedOptions = JSON.parse(localStorage.getItem('fagao-collection-options') || '{}'); } catch { savedOptions = {}; }
  const mode = () => state?.active_mode === 'short' ? 'short' : 'long';
  const view = () => state?.views?.[mode()] || {};
  const platformID = () => view().platform || (mode() === 'long' ? 'fanqie' : 'heiyan');
  const platforms = () => state?.platforms?.length ? state.platforms : fallbackPlatforms;
  const platform = () => platforms().find((p) => p.id === platformID()) || fallbackPlatforms.find((p) => p.id === platformID()) || fallbackPlatforms[0];
  const nameOf = (id) => platforms().find((p) => p.id === id)?.name || fallbackPlatforms.find((p) => p.id === id)?.name || id;
  const favorite = (book, snap = snapshot) => (state?.favorites || []).some((f) => f.book_id === book.id && f.platform === snap?.platform);
  const date = (value) => { if (!value) return '未提供'; const d = new Date(value); return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}); };
  const snapshotDate = (snap) => snap?.collected_at || snap?.created_at;
  const currentJob = () => (state?.jobs || []).find((j) => j.platform === platformID() && ['queued','running','pending'].includes(j.status));
  function toast(message) { clearTimeout(toastTimer); $('#toast').textContent = message; $('#toast').hidden = false; toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 4200); }
  function notice(message) { $('#notice').innerHTML = `<span>${escape(message)}</span><button data-action="dismiss-notice" aria-label="关闭提示">×</button>`; $('#notice').hidden = false; }
  async function api(path, body) {
    const response = await fetch(path, body === undefined ? {cache:'no-store'} : {method:'POST',headers:{'Content-Type':'application/json','X-Fagao-Request':'1'},body:JSON.stringify(body)});
    let result; try { result = await response.json(); } catch { throw new Error('工作台返回了无法读取的数据，请重新启动后重试。'); }
    if (!response.ok || result.error) throw new Error(result.error?.message || result.message || `请求未完成（${response.status}）`);
    return result;
  }
  function acceptState(next) {
    if (next?.views) state = next;
    if (!state) return;
    state.views ||= {}; state.snapshots ||= []; state.favorites ||= []; state.history ||= []; state.reports ||= []; state.jobs ||= [];
  }
  async function refresh({silent = false} = {}) {
    const next = await api('/api/state'); acceptState(next);
    $('#server-dot').classList.add('online'); $('#server-label').textContent = '本地工作台已连接';
    if (!silent) await loadSnapshot(); else render();
    schedulePoll();
  }
  async function loadSnapshot() {
    const version = ++loadingVersion;
    const id = view().snapshot_id;
    if (!id) { snapshot = null; render(); return; }
    if (snapshot?.id === id) { render(); return; }
    const next = await api(`/api/snapshots/${encodeURIComponent(id)}`);
    if (version !== loadingVersion) return;
    snapshot = next.snapshot || next;
    render();
  }
  function updateView(patch) {
    const targetMode = patch.mode || mode();
    saving = saving.catch(() => {}).then(async () => {
      acceptState(await api('/api/view', {mode:targetMode,...patch}));
      await loadSnapshot();
    });
    return saving;
  }
  function orderedBooks() {
    if (!snapshot) return [];
    const books = snapshot.books || [], v = view();
    if (Array.isArray(v.ordered_ids)) {
      const index = new Map(books.map((b) => [b.id,b]));
      return v.ordered_ids.map((id) => index.get(id)).filter(Boolean);
    }
    const filters = v.filters || {}, query = String(filters.query || '').trim().toLowerCase();
    let result = books.filter((b) => !query || [b.title,b.author,b.summary,...(Array.isArray(b.tags)?b.tags:[b.tags || ''])].join(' ').toLowerCase().includes(query));
    if (filters.favorites) result = result.filter((b) => favorite(b));
    if (filters.sort === 'title') result.sort((a,b) => a.title.localeCompare(b.title,'zh-CN'));
    if (filters.sort === 'rank_desc') result.sort((a,b) => (b.rank ?? -1)-(a.rank ?? -1));
    if (filters.sort === 'rank' || filters.sort === 'rank_asc') result.sort((a,b) => (a.rank ?? Infinity)-(b.rank ?? Infinity));
    return result;
  }
  function renderNavigation() {
    document.querySelectorAll('[data-action="mode"]').forEach((el) => el.setAttribute('aria-pressed',String(el.dataset.mode === mode())));
    $('#platforms').innerHTML = platforms().filter((p) => p.mode === mode()).map((p) => `<button class="platform-button" data-action="platform" data-platform="${escape(p.id)}" aria-current="${section === 'browse' && platformID() === p.id}"><span class="platform-mark">${escape((p.name || '')[0])}</span><span class="platform-name">${escape(p.name)}${['planned','unavailable','pending'].includes(p.status) ? '<small>待接入 · 支持导入</small>' : ''}</span></button>`).join('');
    document.querySelectorAll('[data-action="section"]').forEach((el) => el.setAttribute('aria-current',String(el.dataset.section === section)));
    $('#favorite-count').textContent = state.favorites.length; $('#report-count').textContent = state.reports.length;
    const mainURL = safeURL(state.config?.main_site) || 'https://www.fagaochuangzuo.com/'; $('#main-site').href = mainURL;
    const pointsURL = safeURL(state.config?.points_site);
    $('#points-entry').innerHTML = pointsURL ? `<a class="quiet-button" href="${escape(pointsURL)}" target="_blank" rel="noopener noreferrer">返回积分站 ↗</a>` : '<button class="soon" disabled>积分站 <span>即将上线</span></button>';
  }
  function scopeOf(snap = snapshot) { return [snap?.channel,snap?.category,snap?.board,snap?.period].filter(Boolean).join(' · '); }
  function render() {
    if (!state) return;
    const focusedQuery=document.activeElement?.id==='query' ? {value:document.activeElement.value,start:document.activeElement.selectionStart,end:document.activeElement.selectionEnd} : null;
    renderNavigation();
    if (section === 'browse') renderBrowse(); else renderSaved();
    renderBridge();
    if(focusedQuery && $('#query')){
      const input=$('#query');input.value=focusedQuery.value;input.focus({preventScroll:true});
      try{input.setSelectionRange(focusedQuery.start,focusedQuery.end);}catch{}
    }
  }
  function pageHead(title, sub, actions='') { return `<div class="page-head"><div><div class="eyebrow">${section==='browse' ? modes[mode()]+'作品' : '我的工作台'}<span class="dot">/</span> ${section==='browse' ? escape(nameOf(platformID())) : '保存在本机'}</div><h1>${escape(title)}</h1><p>${escape(sub)}</p></div>${actions ? `<div class="head-actions">${actions}</div>` : ''}</div>`; }
  function renderBrowse() {
    const p = platform(), job = currentJob(), auth = state.auth?.[p.id] || {state:'logged_out'};
    const isLibrary = p.id === 'heiyan', unsupported = ['planned','unavailable'].includes(p.status) && p.id === 'fanqie_short';
    const title = isLibrary ? '黑岩故事会 · 书库选题' : `${nameOf(p.id)} · ${snapshot?.board || defaults[p.id]?.board || '作品发现'}`;
    const sub = isLibrary ? '从书库里发现好故事，收藏值得研究的题材与写法。' : '从榜单发现作品，把感兴趣的故事留给下一次创作。';
    const action = `<button class="button secondary" data-action="collection-options" aria-label="调整榜单条件">榜单条件</button><button class="button primary" data-action="collect" ${job || activeRequest || unsupported ? 'disabled' : ''}>${job ? '<span class="spinner"></span>正在采集' : '↻ '+(isLibrary?'更新书库':'更新榜单')}</button>`;
    let body = pageHead(title,sub,action);
    const related = state.snapshots.filter((s) => s.platform === p.id && s.mode === mode());
    body += '<section class="surface" aria-label="作品列表"><div class="surface-head"><div><strong>'+escape(snapshot ? scopeOf() || '本次作品' : '准备你的第一份'+(isLibrary?'书库':'榜单'))+'</strong><span class="badge">'+(isLibrary?'书库资料':'榜单快照')+'</span>';
    body += `<div class="meta">${snapshot ? `${(snapshot.books || []).length} 本作品 · 采集于 ${escape(date(snapshotDate(snapshot)))}` : '每次采集独立保存，更新不会覆盖之前的选择'}</div></div>`;
    if (related.length > 1) body += `<select class="snapshot-select" id="snapshot-select" aria-label="切换历史榜单">${related.map((s) => `<option value="${escape(s.id)}" ${s.id===snapshot?.id?'selected':''}>${escape(date(snapshotDate(s)))} · ${escape(s.category || s.board || '')} · ${s.books_count ?? s.count ?? '?'} 本</option>`).join('')}</select>`;
    body += '</div>';
    if (job) body += `<div class="job-strip" role="status"><span class="spinner"></span><span>${escape(job.message || '正在读取平台…')}</span><button data-action="cancel-job" data-id="${escape(job.id)}">取消</button></div>`;
    const recentJob=state.jobs.find(j=>j.platform===p.id);
    if(!job && recentJob && ['failed','interrupted'].includes(recentJob.status))body+=`<div class="job-strip job-warning"><span>${escape(recentJob.message || '上次采集未完成，已有结果保留。')}</span></div>`;
    if (!snapshot) {
      if (isLibrary && auth.state !== 'connected') body += loginCard(auth);
      else if (unsupported) body += empty('这个来源还在接入中',p.message || '可以先导入已有的书目文件，继续选书和分析。','<button class="button secondary" data-action="import-snapshot">导入书目 JSON</button>','▤');
      else body += empty(job ? '正在取得第一份结果' : '从一次扫榜开始',job ? '采集完成后，作品会出现在这里。你也可以先浏览其他平台。' : '选择一次榜单条件，以后点击更新即可。我们会为你记住上次的选择。',job ? '' : '<button class="button primary" data-action="collect">'+(isLibrary?'采集书库':'开始扫榜')+'</button><button class="button secondary" data-action="import-snapshot">导入已有书目</button>','▤');
    } else {
      body += renderToolbar();
      const books = orderedBooks(), size = Math.max(1,Number(view().page_size) || 20), page = Math.max(1,Number(view().page) || 1), pages = Math.max(1,Math.ceil(books.length/size));
      const start = (Math.min(page,pages)-1)*size, sliced = books.slice(start,start+size);
      body += '<div class="book-layout"><div class="book-list"><div class="list-key"><span>列表序号 / 作品</span><span>'+ (isLibrary ? '资料来源' : '原榜名次') +'</span></div>';
      if (!books.length) body += empty('没有匹配的作品','换个关键词，或清除筛选条件。','<button class="button secondary small" data-action="clear-filters">清除筛选</button>','⌕','compact');
      else body += sliced.map((b,i) => `<button class="book-row" data-action="select-book" data-book="${escape(b.id)}" aria-pressed="${b.id===view().selected_id}"><span class="list-number">${String(start+i+1).padStart(2,'0')}</span><span><span class="book-title">${escape(b.title)}${favorite(b) ? '<span class="star" aria-label="已收藏"> · ♡</span>' : ''}</span><span class="book-meta">${escape(b.author || '作者未提供')}${(b.tags?.length) ? '<span>·</span>'+escape(Array.isArray(b.tags)?b.tags.slice(0,2).join(' · '):b.tags) : ''}</span></span><span class="rank">${isLibrary ? '书库' : Number.isFinite(b.rank) ? '# '+b.rank : '—'}</span></button>`).join('');
      if (books.length) body += `<div class="pagination"><span>共 ${books.length} 本 · 序号跨页连续</span><div><button data-action="page" data-page="${page-1}" aria-label="上一页" ${page<=1?'disabled':''}>‹</button><span>${Math.min(page,pages)} / ${pages}</span><button data-action="page" data-page="${page+1}" aria-label="下一页" ${page>=pages?'disabled':''}>›</button></div></div>`;
      body += '</div>'+renderDetail(books)+'</div>';
      if(snapshot.warnings?.length)body+=`<div class="source-warnings">${snapshot.warnings.map(w=>`<p>${escape(w)}</p>`).join('')}</div>`;
      const source = safeURL(snapshot.source_url);
      body += `<div class="source-note"><span>来源数据日期：${escape(snapshot.data_date || '原站未提供')} · ${isLibrary?'书库顺序不代表热度排名':'名次以本次原始榜单为准'}</span>${source?`<a href="${escape(source)}" target="_blank" rel="noopener noreferrer">查看来源 ↗</a>`:''}</div>`;
    }
    body += '</section>';
    if (p.message) body += `<p class="collection-hint">${escape(p.message)}</p>`;
    $('#content').innerHTML = body;
  }
  function renderToolbar() {
    const f = view().filters || {};
    return `<form id="search-form" class="toolbar" role="search"><label class="search"><span aria-hidden="true">⌕</span><input id="query" type="search" name="query" aria-label="搜索书名、作者或简介" placeholder="搜索书名、作者或简介" value="${escape(f.query || '')}" autocomplete="off"></label><select id="sort" aria-label="作品排序"><option value="source" ${(!f.sort||f.sort==='source')?'selected':''}>原始顺序</option><option value="rank" ${['rank','rank_asc'].includes(f.sort)?'selected':''}>原榜名次 ↑</option><option value="rank_desc" ${f.sort==='rank_desc'?'selected':''}>原榜名次 ↓</option><option value="title" ${f.sort==='title'?'selected':''}>书名排序</option></select><label class="checkbox-label"><input id="favorites-filter" type="checkbox" ${f.favorites?'checked':''}>只看收藏</label></form>`;
  }
  function renderDetail(visible) {
    const b = (snapshot?.books || []).find((x) => x.id === view().selected_id);
    if (!b) return '<aside class="detail" aria-label="作品详情">'+empty('选一本，进一步了解','点击左侧作品，查看简介和准确的榜单位置。','','▤','compact')+'</aside>';
    const position = visible.findIndex((x) => x.id === b.id)+1, url = safeURL(b.url), metrics = Array.isArray(b.metrics) ? b.metrics : [];
    const reports = state.reports.filter((r) => r.book_id === b.id && (!r.platform || r.platform === snapshot.platform));
    return `<aside class="detail" aria-label="作品详情"><div class="detail-top"><span>已选中的作品</span><button class="favorite-button" data-action="favorite" data-book="${escape(b.id)}" aria-pressed="${favorite(b)}">${favorite(b)?'♥ 已收藏':'♡ 收藏'}</button></div><div class="book-jacket" aria-hidden="true">▤</div><h2>${escape(b.title)}</h2><p class="author">${escape(b.author || '作者未提供')}</p><div class="positions"><span>${position?'列表第 '+position+' 本':'已被当前筛选隐藏'}</span>${snapshot.platform!=='heiyan' && Number.isFinite(b.rank)?`<span class="original">原榜第 ${b.rank} 名</span>`:''}</div><p class="summary">${escape(b.summary || '本次来源未提供简介，可在作品原页查看。')}</p>${metrics.length?`<div class="metrics">${metrics.map((m)=>`<span>${escape(m.name)} <strong>${escape(m.value)}</strong>${escape(m.unit || '')}</span>`).join('')}</div>`:''}<div class="detail-actions">${url?`<a class="source-link" href="${escape(url)}" target="_blank" rel="noopener noreferrer">打开作品原页 ↗</a>`:''}<button class="button secondary small" data-action="import-text">导入正文</button></div><p class="text-status">已选对象已保存，可回到 WorkBuddy 继续分析。</p>${reports.map((r)=>`<button class="report-item" data-action="report" data-id="${escape(r.id)}">▤ ${escape(r.title || '拆书报告')}<br><small>${escape(r.scope || '')}</small></button>`).join('')}</aside>`;
  }
  function empty(title,text,actions='',symbol='▤',extra='') { return `<div class="empty ${extra}"><div class="empty-symbol" aria-hidden="true">${symbol}</div><h2>${escape(title)}</h2><p>${escape(text)}</p>${actions?`<div class="actions">${actions}</div>`:''}</div>`; }
  function loginCard(auth) {
    const labels = {logged_out:['连接黑岩故事会','使用能够访问黑岩书库的账号登录。登录会在平台自己的窗口中完成。'],logging_in:['在打开的窗口中完成登录','完成后点击“检查连接”。连接成功后，将继续你已经发起的采集。'],expired:['黑岩登录已过期','之前的书目和选择仍然保留，重新登录即可继续更新。'],forbidden:['当前账号没有书库权限','请使用能够访问黑岩管理后台书库的账号。'],network_error:['暂时无法连接黑岩','请检查网络后重试，已有资料不会受到影响。'],unavailable:['还需要准备浏览器环境','回到 WorkBuddy，让它完成浏览器依赖安装，再从这里连接。']};
    const [title,text] = labels[auth.state] || labels.logged_out;
    return `<div class="empty login-card"><div class="empty-symbol" aria-hidden="true">↗</div><h2>${escape(title)}</h2><p>${escape(auth.message || text)}</p><div class="actions"><button class="button primary" data-action="login" ${authPending?'disabled':''}>${authPending?'<span class="spinner"></span>正在打开':'去黑岩登录'}</button><button class="button secondary" data-action="check-auth" ${authPending?'disabled':''}>我已登录，检查连接</button></div><small>登录资料仅保存在本机专用浏览器中</small></div>`;
  }
  function renderBridge() {
    const b = snapshot?.books?.find((x)=>x.id===view().selected_id);
    if (section !== 'browse' || !b) { $('#bridge').innerHTML = ''; return; }
    $('#bridge').innerHTML = `<section class="bridge" aria-label="WorkBuddy 对话提示"><div class="bridge-main"><div><div class="bridge-label">回到 WorkBuddy，可以说</div><p>“分析我刚选的这本，重点看开头怎么吸引读者。”</p></div><button data-action="context">查看引用 ⌄</button></div><small>已保存：${escape(b.title)} · ${escape(scopeOf())} · ${escape(date(snapshotDate(snapshot)))}</small></section>`;
  }
  function renderSaved() {
    const labels={favorites:['我的收藏','有价值的参考，留待下一次创作。'],history:['最近记录','回到上次选中的作品，或者继续浏览之前的榜单。'],reports:['拆书报告','从 WorkBuddy 生成的分析结果，会在这里与作品关联。']};
    const [title,sub]=labels[section]; let html=pageHead(title,sub)+'<section class="surface">';
    if(section==='favorites') {
      const list=state.favorites;
      html+=list.length?'<div class="saved-list">'+list.map((f)=>savedRow(f.title||f.book_id,nameOf(f.platform),f.created_at,'open-record',{'snapshot':f.snapshot_id,'book':f.book_id})).join('')+'</div>':empty('还没有收藏','浏览作品时，点击详情里的“收藏”，就会保存在这里。');
    } else if(section==='history') {
      html+='<div class="surface-head"><strong>最近选中的作品</strong><span class="meta">选择固定到当时的榜单</span></div>';
      html+=state.history.length?'<div class="saved-list">'+state.history.slice(0,30).map((h)=>savedRow(h.title||h.book_title||h.book_id,[nameOf(h.platform),h.board,h.category].filter(Boolean).join(' · '),h.selected_at||h.created_at,'open-record',{'snapshot':h.snapshot_id,'book':h.book_id})).join('')+'</div>':empty('还没有选书记录','第一次选中作品后，它的名称与当时榜单会一起保存。','','◷','compact');
      html+='<div class="surface-head"><strong>历史榜单与书库</strong></div>';
      html+=state.snapshots.length?'<div class="saved-list">'+state.snapshots.map((s)=>savedRow([nameOf(s.platform),s.board].filter(Boolean).join(' · '),[s.channel,s.category,`${s.books_count ?? s.count ?? '?'} 本`].filter(Boolean).join(' · '),snapshotDate(s),'open-record',{'snapshot':s.id})).join('')+'</div>':empty('还没有采集记录','第一次采集成功后，完整书目将保存在这里。','','▤','compact');
    } else {
      html+=state.reports.length?'<div class="saved-list">'+state.reports.map((r)=>savedRow(r.title||'拆书报告',r.scope||'阅读范围见报告',r.created_at,'report',{'id':r.id})).join('')+'</div>':empty('还没有拆书报告','在工作台选一本书，再回到 WorkBuddy 说“分析我刚选的这本”。完成分析并保存报告后，就能在这里继续查看。');
    }
    $('#content').innerHTML=html+'</section>';
  }
  function savedRow(title,sub,time,action,attrs) { return `<div class="saved-row"><button class="saved-open" data-action="${action}" ${Object.entries(attrs).filter(([,v])=>v).map(([k,v])=>`data-${k}="${escape(v)}"`).join(' ')}><strong>${escape(title)}</strong><small>${escape(sub)}</small></button><span class="saved-date">${escape(date(time))}</span></div>`; }
  function showDialog(title,content,subtitle='') {
    $('#dialog-body').innerHTML=`<div class="dialog-head"><div><h2>${escape(title)}</h2>${subtitle?`<p>${escape(subtitle)}</p>`:''}</div><button class="close-dialog" data-action="close-dialog" aria-label="关闭窗口">×</button></div><div class="dialog-content">${content}</div>`;
    if(!$('#dialog').open) $('#dialog').showModal();
  }
  function collectionOptions() {
    const id=platformID(), current={...defaults[id],...savedOptions[id]};
    showDialog('榜单条件',`<form id="options-form"><p>设置会被记住，以后直接点击“更新${id==='heiyan'?'书库':'榜单'}”。</p><div class="form-grid"><div class="field"><label for="channel">频道</label><select id="channel" name="channel">${(id==='fanqie'?['女频','男频']:[defaults[id]?.channel || '全部']).map(x=>`<option ${current.channel===x?'selected':''}>${escape(x)}</option>`).join('')}</select></div><div class="field"><label for="category">分类</label><input id="category" name="category" value="${escape(current.category || '全部')}" required maxlength="40"></div><div class="field"><label for="board">榜单</label><select id="board" name="board">${(id==='fanqie'?['新书榜','阅读榜']:id==='qidian'?['月票榜','签约新书榜','公众新书榜','新人新书榜','畅销榜']:id==='ciweimao'?['月票榜']:id==='heiyan'?['书库']:['热门故事']).map(x=>`<option ${current.board===x?'selected':''}>${escape(x)}</option>`).join('')}</select></div><div class="field"><label for="limit">期望采集数量</label><input id="limit" name="limit" type="number" min="1" max="${id==='heiyan'?200:100}" value="${current.limit || 30}" required></div></div><p class="help-inline">分类以平台真实名称为准。若来源只提供部分作品，将保留实际取得的数量。</p><div class="form-actions"><button class="button secondary" type="button" data-action="close-dialog">取消</button><button class="button primary" type="submit">保存条件</button></div></form>`);
  }
  async function collect() {
    if(currentJob() || activeRequest) return;
    $('#notice').hidden=true;
    if(platformID()==='heiyan' && state.auth?.heiyan?.state!=='connected') { continueAfterLogin=true; await login(); return; }
    activeRequest=true; render();
    try {
      const result=await api('/api/collect',{platform:platformID(),options:{...defaults[platformID()],...savedOptions[platformID()],mode:mode()}});
      if(result.job_id) seenJobs.add(result.job_id);
      await refresh();
      const job=state.jobs.find(j=>j.id===result.job_id);
      if(job?.status==='completed' && job.snapshot_id && job.platform===platformID()){
        await updateView({snapshot_id:job.snapshot_id,selected_id:null,page:1});toast(job.message || '采集结果已保存。');
      } else if(job?.status==='failed')notice(job.message || '采集未完成，已有结果保留。');
      else toast('采集已开始，完成后自动显示结果。');
    } finally {activeRequest=false;render();}
  }
  async function login() {
    if(authPending)return; authPending=true; render();
    try { const auth=await api('/api/auth',{platform:'heiyan',action:'login'}); state.auth ||= {}; state.auth.heiyan=auth.auth || auth; watchingAuth=['logging_in','logged_out'].includes(state.auth.heiyan.state); lastAuthCheck=Date.now(); await refresh(); toast(auth.message || '请在打开的平台窗口中完成登录。'); }
    finally {authPending=false;render();}
  }
  async function checkAuth(silent=false) {
    if(authPending)return; authPending=true; if(!silent)render();
    try {
      const auth=await api('/api/auth',{platform:'heiyan',action:'check'}); state.auth ||= {}; state.auth.heiyan=auth.auth || auth;
      lastAuthCheck=Date.now();
      const actual=state.auth.heiyan;
      if(actual.state==='connected'){
        watchingAuth=false;
        if(continueAfterLogin){
          continueAfterLogin=false;
          await api('/api/collect',{platform:'heiyan',options:{...defaults.heiyan,...savedOptions.heiyan,mode:'short'}});
          toast('黑岩已连接，正在继续采集书库。');
        } else if(!silent)toast(actual.message || '已连接黑岩书库。');
      } else if(['forbidden','expired','unavailable'].includes(actual.state))watchingAuth=false;
      await refresh();
      if(!silent && actual.state!=='connected')toast(actual.message || '已检查连接状态。');
    } finally {authPending=false;render();}
  }
  function schedulePoll() {
    clearTimeout(pollTimer);
    pollTimer=setTimeout(async()=>{
      try {
        if(watchingAuth && !authPending && Date.now()-lastAuthCheck>=5000) await checkAuth(true);
        const jobs=state?.jobs || [], running=jobs.filter(j=>['queued','running','pending'].includes(j.status));
        if(running.length) {
          const results=await Promise.all(running.map(j=>api(`/api/jobs/${encodeURIComponent(j.id)}`)));
          let ended=false;
          for(let i=0;i<results.length;i++) {
            const job=results[i].job || results[i], idx=state.jobs.findIndex(j=>j.id===job.id);
            if(idx>=0)state.jobs[idx]=job;
            if(!['queued','running','pending'].includes(job.status)){
              ended=true;
              if(['failed','error','cancelled'].includes(job.status)) notice(job.message || '采集未完成。已有书目仍然保留。');
              else toast(job.message || '新的采集结果已保存。');
            }
          }
          if(ended) {
            const completed=results.map(r=>r.job || r).find(j=>j.status==='completed' && j.snapshot_id && j.platform===platformID() && section==='browse');
            await refresh();
            if(completed) await updateView({snapshot_id:completed.snapshot_id,selected_id:null,page:1});
          } else render();
        }
      } catch(error) { notice(error.message); }
      finally { schedulePoll(); }
    },2200);
  }
  async function openRecord(el) {
    const meta=state.snapshots.find(s=>s.id===el.dataset.snapshot);
    const snap=meta || await api(`/api/snapshots/${encodeURIComponent(el.dataset.snapshot)}`);
    section='browse';
    await updateView({mode:snap.mode || (snap.platform==='heiyan'?'short':'long'),platform:snap.platform,snapshot_id:el.dataset.snapshot,selected_id:el.dataset.book || null,page:1,filters:{query:'',sort:'source',favorites:false}});
  }
  async function report(id) { const r=await api(`/api/reports/${encodeURIComponent(id)}`); showDialog(r.title || '拆书报告',`<pre class="report-text">${escape(r.content || r.report?.content || '')}</pre>`,[r.scope,date(r.created_at)].filter(Boolean).join(' · ')); }
  function textImport() {
    const b=snapshot?.books?.find(x=>x.id===view().selected_id); if(!b)return;
    pendingText={snapshot_id:snapshot.id,book_id:b.id,title:b.title,text:''};
    showDialog('导入作品正文',`<form id="text-import-form"><p>正文将关联到《${escape(b.title)}》。保留实际阅读范围，方便 WorkBuddy 准确分析。</p><button type="button" class="button secondary" data-action="choose-text">选择 TXT / Markdown 文件</button><div class="file-label" id="text-file-label">尚未选择文件</div><div class="field" style="margin-top:18px"><label for="text-scope">这份文本包含哪些内容</label><input id="text-scope" name="scope" placeholder="例如：第 1—3 章，或完整短篇正文" required maxlength="200"></div><label class="checkbox-label"><input type="checkbox" id="text-complete">我确认这份文件包含作品完整正文</label><div class="form-actions"><button type="button" class="button secondary" data-action="close-dialog">取消</button><button type="submit" class="button primary">保存正文</button></div></form>`);
  }
  async function dispatch(el) {
    const a=el.dataset.action;
    if(a==='dismiss-notice'){$('#notice').hidden=true;return;}
    if(a==='close-dialog'){$('#dialog').close();return;}
    if(a==='mode') {section='browse';await updateView({mode:el.dataset.mode});return;}
    if(a==='platform') {
      section='browse';const id=el.dataset.platform, existing=state.snapshots.find(s=>s.platform===id && s.mode===mode());
      if(id===platformID()){render();return;}
      await updateView({platform:id,snapshot_id:existing?.id || null,selected_id:null,page:1,filters:{query:'',sort:'source',favorites:false}});return;
    }
    if(a==='section'){section=el.dataset.section;render();return;}
    if(a==='select-book'){await updateView({selected_id:el.dataset.book});return;}
    if(a==='page'){await updateView({page:Number(el.dataset.page)});return;}
    if(a==='clear-filters'){await updateView({filters:{query:'',sort:'source',favorites:false},page:1});return;}
    if(a==='favorite') { const b=snapshot.books.find(x=>x.id===el.dataset.book);acceptState(await api('/api/favorite',{snapshot_id:snapshot.id,book_id:b.id,enabled:!favorite(b)})); await refresh();return; }
    if(a==='collection-options'){collectionOptions();return;}
    if(a==='collect'){await collect();return;}
    if(a==='cancel-job'){await api('/api/jobs/cancel',{job_id:el.dataset.id});await refresh();toast('已取消本次采集，已有结果保留。');return;}
    if(a==='login'){await login();return;}
    if(a==='check-auth'){await checkAuth();return;}
    if(a==='import-snapshot'){$('#snapshot-file').click();return;}
    if(a==='import-text'){textImport();return;}
    if(a==='choose-text'){$('#text-file').click();return;}
    if(a==='open-record'){await openRecord(el);return;}
    if(a==='report'){await report(el.dataset.id);return;}
    if(a==='retry'){await refresh();return;}
    if(a==='context'){
      const book=snapshot?.books?.find(item=>item.id===view().selected_id);
      if(!book)return;
      const position=orderedBooks().findIndex(item=>item.id===book.id)+1;
      const source=safeURL(book.url);
      const rows=[
        ['作者',book.author || '原站未提供'],
        ['平台',nameOf(snapshot.platform)],
        ['频道 / 分类 / 榜单',scopeOf() || '原站未提供'],
        ['当前列表位置',position ? `第 ${position} 本` : '已被当前筛选隐藏'],
        ['原榜名次',snapshot.platform==='heiyan' ? '书库资料，不按热度排名' : Number.isFinite(book.rank) ? `第 ${book.rank} 名` : '原站未提供'],
        ['来源数据日期',snapshot.data_date || '原站未提供'],
        ['采集时间',date(snapshotDate(snapshot))]
      ];
      showDialog(book.title,`<p>WorkBuddy 已可读取这份选择。回到对话说“分析刚选的这本”，就能继续使用；引用固定到这次榜单，更新后不会换成其他作品。</p><dl style="margin:0;display:grid;grid-template-columns:minmax(90px,30%) minmax(0,1fr);gap:12px 16px;font-size:12px">${rows.map(([label,value])=>`<dt style="color:var(--muted)">${escape(label)}</dt><dd style="margin:0;overflow-wrap:anywhere">${escape(value)}</dd>`).join('')}</dl>${source?`<div class="form-actions"><a class="button secondary" href="${escape(source)}" target="_blank" rel="noopener noreferrer">打开作品原页 ↗</a></div>`:''}`,'当前作品引用');
      return;
    }
  }
  document.addEventListener('click',(event)=>{
    const el=event.target.closest('[data-action]'); if(!el || el.disabled)return;
    dispatch(el).catch(error=>notice(error.message));
  });
  document.addEventListener('change',(event)=>{
    const el=event.target;
    if(el.id==='snapshot-select')updateView({snapshot_id:el.value,selected_id:null,page:1}).catch(e=>notice(e.message));
    if(el.id==='sort' || el.id==='favorites-filter'){
      const filters={...(view().filters || {}),sort:$('#sort')?.value || 'source',favorites:!!$('#favorites-filter')?.checked};
      updateView({filters,page:1}).catch(e=>notice(e.message));
    }
  });
  document.addEventListener('input',(event)=>{
    if(event.target.id!=='query')return;
    const value=event.target.value;clearTimeout(searchTimer);
    searchTimer=setTimeout(()=>{
      const cursor=$('#query')?.selectionStart;
      updateView({filters:{...(view().filters || {}),query:value},page:1}).then(()=>{const input=$('#query'); if(input){input.focus();try{input.setSelectionRange(cursor,cursor);}catch{}}}).catch(e=>notice(e.message));
    },350);
  });
  document.addEventListener('submit',(event)=>{
    if(event.target.id==='search-form'){event.preventDefault();return;}
    if(event.target.id==='options-form'){
      event.preventDefault();const form=new FormData(event.target);
      savedOptions[platformID()]={channel:String(form.get('channel')),category:String(form.get('category')).trim(),board:String(form.get('board')),limit:Number(form.get('limit'))};
      try{localStorage.setItem('fagao-collection-options',JSON.stringify(savedOptions));}catch{}
      $('#dialog').close();toast('榜单条件已保存，下次更新将使用这些条件。');return;
    }
    if(event.target.id==='text-import-form'){
      event.preventDefault();
      if(!pendingText?.text){toast('请先选择正文文件。');return;}
      const scope=$('#text-scope').value.trim();if(!scope)return;
      const payload={snapshot_id:pendingText.snapshot_id,book_id:pendingText.book_id,text:pendingText.text,scope,complete:$('#text-complete').checked};
      const submit=event.target.querySelector('[type="submit"]');submit.disabled=true;
      api('/api/import/text',payload).then(()=>{$('#dialog').close();pendingText=null;toast('正文已保存，可回到 WorkBuddy 继续分析。');return refresh();}).catch(e=>{notice(e.message);submit.disabled=false;});
    }
  });
  $('#snapshot-file').addEventListener('change',async(event)=>{
    const file=event.target.files[0];event.target.value='';if(!file)return;
    try{
      if(file.size>8*1024*1024)throw new Error('书目文件超过 8 MB，请拆分后导入。');
      const parsed=JSON.parse(await file.text()); const result=await api('/api/import/snapshot',{snapshot:parsed.snapshot || parsed});
      await refresh();section='browse';
      const snap=result.snapshot || result;
      if(snap.id)await updateView({mode:snap.mode || mode(),platform:snap.platform || platformID(),snapshot_id:snap.id,selected_id:null,page:1});
      else render();toast('书目已导入，来源信息与原榜名次会一起保留。');
    }catch(e){notice(e instanceof SyntaxError?'这不是有效的 JSON 书目文件。':e.message);}
  });
  $('#text-file').addEventListener('change',async(event)=>{
    const file=event.target.files[0];event.target.value='';if(!file || !pendingText)return;
    try{if(file.size>12*1024*1024)throw new Error('正文文件超过 12 MB，请按卷拆分导入。');const bytes=await file.arrayBuffer();let decoded=new TextDecoder('utf-8').decode(bytes);if(decoded.includes('\uFFFD')){try{decoded=new TextDecoder('gb18030',{fatal:true}).decode(bytes);}catch{}}pendingText.text=decoded;$('#text-file-label').textContent=`${file.name} · ${pendingText.text.length.toLocaleString()} 字符`;}
    catch(e){notice(e.message);}
  });
  $('#dialog').addEventListener('click',(event)=>{if(event.target===$('#dialog')){const r=$('#dialog').getBoundingClientRect();if(event.clientX<r.left || event.clientX>r.right || event.clientY<r.top || event.clientY>r.bottom)$('#dialog').close();}});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden && state)refresh().catch(e=>notice(e.message));});
  refresh().catch(error=>{$('#server-label').textContent='本地服务未连接';$('#server-dot').classList.remove('online');$('#content').innerHTML=pageHead('欢迎回来','打开本地工作台后，就可以继续上次的选书。')+'<section class="surface">'+empty('暂时连接不上本地服务','请通过“发糕扫榜”启动入口重新打开，或让 WorkBuddy 帮你启动工作台。','<button class="button primary" data-action="retry">重新连接</button>','↻')+'</section>';notice(error.message);});
})();
