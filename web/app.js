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
    fanqie:{channel:'女频',category:'古言脑洞',board:'新书榜',limit:1000,max_pages:100},
    qidian:{channel:'男频',category:'全部',board:'月票榜',limit:1000,max_pages:100},
    ciweimao:{channel:'综合',category:'全部',board:'月票榜',limit:1000,max_pages:100},
    heiyan:{channel:'全部',category:'书库',board:'书库',limit:1000,max_pages:100},
    fanqie_short:{channel:'短篇',category:'全部',board:'热门故事',limit:1000,max_pages:100}
  };
  let state = null, snapshot = null, section = 'browse', loadingVersion = 0, pendingText = null, viewGeneration = 0, refreshGeneration = 0, pendingViewMutations = 0;
  let saving = Promise.resolve(), pollTimer = null, toastTimer = null, authPending = false, continueAfterLogin = false;
  let searchTimer, activeRequest = false, savedOptions = {}, watchingAuth = false, lastAuthCheck = 0;
  const AUTO_TTL = 15 * 60 * 1000;
  const nextAutoAt = new Map(), autoMessages = new Map(), latestResults = new Map();
  let historyViews = {};
  try { historyViews = JSON.parse(localStorage.getItem('fagao-history-views') || '{}'); } catch {}
  try {
    savedOptions = JSON.parse(localStorage.getItem('fagao-collection-options') || '{}');
    if(localStorage.getItem('fagao-options-version')!=='2'){
      for(const value of Object.values(savedOptions)){value.limit=1000;value.max_pages=100;}
      localStorage.setItem('fagao-options-version','2');
      localStorage.setItem('fagao-collection-options',JSON.stringify(savedOptions));
    }
  } catch { savedOptions = {}; }
  const mode = () => state?.active_mode === 'short' ? 'short' : 'long';
  const view = () => state?.views?.[mode()] || {};
  const platformID = () => view().platform || (mode() === 'long' ? 'fanqie' : 'heiyan');
  const platforms = () => state?.platforms?.length ? state.platforms : fallbackPlatforms;
  const platform = () => platforms().find((p) => p.id === platformID()) || fallbackPlatforms.find((p) => p.id === platformID()) || fallbackPlatforms[0];
  const nameOf = (id) => platforms().find((p) => p.id === id)?.name || fallbackPlatforms.find((p) => p.id === id)?.name || id;
  const favorite = (book, snap = snapshot) => (state?.favorites || []).some((f) => f.book_id === book.id && f.platform === snap?.platform);
  const date = (value) => { if (!value) return '未提供'; const d = new Date(value); return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}); };
  const snapshotDate = (snap) => snap?.collected_at || snap?.created_at;
  const currentJob = () => (state?.jobs || []).find((j) => j.platform === platformID() && (!j.options || scopeKey(j.platform,j.options)===scopeKey()) && ['queued','running','pending'].includes(j.status));
  function optionsFor(id=platformID()) {
    const stored=state?.snapshots?.find(item=>item.id===(state.views?.[mode()]?.snapshot_id) && item.platform===id);
    const previous=stored ? {channel:stored.channel,category:stored.category,board:stored.board} : {};
    const preferences=stored && historyViews[mode()]===stored.id ? {} : savedOptions[id];
    return {...defaults[id],...previous,...preferences,mode:id==='heiyan'||id==='fanqie_short'?'short':'long'};
  }
  function scopeKey(id=platformID(),options=optionsFor(id)) { return [id,options.mode || (id==='heiyan'||id==='fanqie_short'?'short':'long'),options.channel,options.category,options.board,options.period || (id==='ciweimao'?'月榜':''),options.limit ?? '',options.max_pages ?? 100,options.detail_budget ?? ''].join('|'); }
  function sameScope(item,id=platformID(),options=optionsFor(id)) { return item?.platform===id && ['channel','category','board'].every(key=>String(item[key] || '')===String(options[key] || '')); }
  function viewingHistory(){return historyViews[mode()]===view().snapshot_id && !!view().snapshot_id;}
  function setHistory(snapshotId,targetMode=mode()){
    if(snapshotId)historyViews[targetMode]=snapshotId;else delete historyViews[targetMode];
    try{localStorage.setItem('fagao-history-views',JSON.stringify(historyViews));}catch{}
  }
  function boardNames(id=platformID()) { return id==='fanqie'?['新书榜','阅读榜']:id==='qidian'?['月票榜','签约新书榜','公众新书榜','新人新书榜','畅销榜']:id==='ciweimao'?['月票榜']:id==='heiyan'?['书库']:['热门故事']; }
  const stopReasons={last_page:'已读取平台最后一页',empty_page:'平台已无更多书目',requested_limit:'已达到本次采集数量',max_pages:'已达到本次页数上限',repeated_page:'平台返回重复页面，已停止避免重复',version_changed:'榜单版本发生变化，已保留本次结果',invalid_response:'平台返回的数据暂不可读',wrong_page:'平台返回的页码不一致',request_failed:'后续页面暂时无法连接',session_required:'后续页面需要平台登录',pagination_unverified:'后续翻页方式仍待核实',rank_conflict:'不同页面出现名次冲突',book_conflict:'不同页面出现作品信息冲突',invalid_next_page:'平台未提供可用的下一页'};
  function readableWarning(value){return String(value).replace(/[a-z]+(?:_[a-z]+)+/g,key=>stopReasons[key] || key);}
  function paginationSummary(item){
    const page=item?.pagination;
    if(!page || !Number.isFinite(page.pages_fetched))return '';
    return `已读取 ${page.pages_fetched} 页${Number.isFinite(page.max_pages)?`（单次最多 ${page.max_pages} 页）`:''}${page.stop_reason?' · '+(stopReasons[page.stop_reason] || '本轮读取已停止，详情见来源说明'):''}`;
  }
  function cover(book,large=false){
    const url=safeURL(book.cover_url);
    return `<span class="book-cover${large?' cover-large':''}"><span class="cover-fallback" aria-hidden="true">${escape(book.title)}</span>${url?`<img class="cover-image" src="${escape(url)}" alt="${escape(book.title)}封面" loading="lazy" decoding="async" referrerpolicy="no-referrer">`:''}</span>`;
  }
  function metricsHTML(book,limit=3){
    const metrics=Array.isArray(book.metrics)?book.metrics:[];
    return metrics.slice(0,limit).map(metric=>`<span>${escape(metric.name)} <strong>${escape(metric.value)}</strong>${escape(metric.unit || '')}</span>`).join('');
  }
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
    while(pendingViewMutations>0)await saving.catch(()=>{});
    const viewVersion=viewGeneration, requestVersion=++refreshGeneration;
    const next = await api('/api/state');
    if(pendingViewMutations>0 || viewVersion!==viewGeneration || requestVersion!==refreshGeneration)return;
    acceptState(next);
    $('#server-dot').classList.add('online'); $('#server-label').textContent = '本地工作台已连接';
    if (!silent) await loadSnapshot(); else render();
    schedulePoll();
    queueMicrotask(()=>maybeAutoCollect().catch(()=>{}));
  }
  async function loadSnapshot() {
    const version = ++loadingVersion, viewVersion=viewGeneration;
    const id = view().snapshot_id;
    if (!id) { snapshot = null; render(); return; }
    if (snapshot?.id === id) { render(); return; }
    const next = await api(`/api/snapshots/${encodeURIComponent(id)}`);
    if (version !== loadingVersion || viewVersion!==viewGeneration) return;
    snapshot = next.snapshot || next;
    render();
  }
  function updateView(patch) {
    const targetMode = patch.mode || mode(), version=++viewGeneration;
    pendingViewMutations++;
    saving = saving.catch(() => {}).then(async () => {
      try{
        const next=await api('/api/view', {mode:targetMode,...patch});
        if(version!==viewGeneration)return;
        acceptState(next);await loadSnapshot();
      } finally {pendingViewMutations--;}
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
    const options=optionsFor(), key=scopeKey();
    const title = isLibrary ? '黑岩故事会 · 书库选题' : `${nameOf(p.id)} · ${options.board || '作品发现'}`;
    const sub = isLibrary ? '书库资料按来源保存，登录后自动更新。' : '持续更新平台榜单，发现值得阅读与研究的作品。';
    const action = `<button class="button secondary" data-action="collection-options" aria-label="调整采集设置">采集设置</button><button class="button secondary" data-action="collect" ${job || activeRequest || unsupported ? 'disabled' : ''}>${job ? '<span class="spinner"></span>正在采集' : '↻ '+(isLibrary?'更新书库':'更新榜单')}</button>`;
    let body = pageHead(title,sub,action)+renderBoardControls();
    const freshResult=latestResults.get(key);
    if(freshResult && freshResult!==snapshot?.id)body+=`<div class="new-results"><span>新的榜单已更新。当前选中的作品仍保留在原记录中。</span><button data-action="show-latest" data-id="${escape(freshResult)}">查看最新榜单 →</button></div>`;
    const related = state.snapshots.filter((s) => s.platform === p.id && s.mode === mode());
    body += '<section class="surface" aria-label="作品列表"><div class="surface-head"><div><strong>'+escape(snapshot ? scopeOf() || '本次作品' : '正在同步'+(isLibrary?'书库':'榜单'))+'</strong><span class="badge">'+(isLibrary?'书库资料':'平台榜单')+'</span>';
    body += `<div class="meta">${snapshot ? `${(snapshot.books || []).length} 本作品 · 采集于 ${escape(date(snapshotDate(snapshot)))}` : '自动保存每次榜单，更新不会改变已选中的作品'}</div></div>`;
    if(snapshot?.pagination)body+=`<div class="meta pagination-summary">${escape(paginationSummary(snapshot))}</div>`;
    if (related.length > 1) body += `<select class="snapshot-select" id="snapshot-select" aria-label="切换历史榜单">${related.map((s) => `<option value="${escape(s.id)}" ${s.id===snapshot?.id?'selected':''}>${escape(date(snapshotDate(s)))} · ${escape(s.category || s.board || '')} · ${s.books_count ?? s.count ?? '?'} 本</option>`).join('')}</select>`;
    body += '</div>';
    if (job) body += `<div class="job-strip" role="status"><span class="spinner"></span><span>${escape(job.message || '正在读取平台…')}</span><button data-action="cancel-job" data-id="${escape(job.id)}">取消</button></div>`;
    const recentJob=state.jobs.find(j=>j.platform===p.id && (!j.options || scopeKey(j.platform,j.options)===scopeKey()));
    if(!job && recentJob && ['failed','interrupted'].includes(recentJob.status))body+=`<div class="job-strip job-warning"><span>${escape(recentJob.message || '上次采集未完成，已有结果保留。')}</span></div>`;
    if(snapshot && isLibrary && auth.state!=='connected')body+=loginCard(auth,true);
    if (!snapshot) {
      if (isLibrary && auth.state !== 'connected') body += loginCard(auth);
      else if (unsupported) body += empty('这个来源还在接入中',p.message || '可以先导入已有的书目文件，继续选书和分析。','<button class="button secondary" data-action="import-snapshot">导入书目 JSON</button>','▤');
      else body += empty(job ? '正在读取平台榜单' : '正在同步作品',job ? '作品会在完成后自动显示。你可以先浏览其他平台。' : '工作台会自动取得当前榜单。已有资料也可以直接导入。','<button class="button secondary" data-action="import-snapshot">导入已有书目</button>','▤');
    } else {
      body += renderToolbar();
      const books = orderedBooks(), size = Math.max(1,Number(view().page_size) || 20), page = Math.max(1,Number(view().page) || 1), pages = Math.max(1,Math.ceil(books.length/size));
      const start = (Math.min(page,pages)-1)*size, sliced = books.slice(start,start+size);
      body += '<div class="book-layout"><div class="book-list"><div class="list-key"><span>列表序号 / 作品</span><span>'+ (isLibrary ? '资料来源' : '原榜名次') +'</span></div>';
      if (!books.length) body += empty('没有匹配的作品','换个关键词，或清除筛选条件。','<button class="button secondary small" data-action="clear-filters">清除筛选</button>','⌕','compact');
      else body += sliced.map((b,i) => `<button class="book-row" data-action="select-book" data-book="${escape(b.id)}" aria-pressed="${b.id===view().selected_id}"><span class="list-number">${String(start+i+1).padStart(2,'0')}</span>${cover(b)}<span class="row-content"><span class="book-title">${escape(b.title)}${favorite(b) ? '<span class="star" aria-label="已收藏"> ♡</span>' : ''}</span><span class="book-meta">${escape(b.author || '作者未提供')}${(b.tags?.length) ? '<span>·</span>'+escape(Array.isArray(b.tags)?b.tags.slice(0,2).join(' · '):b.tags) : ''}${b.status?'<span>·</span>'+escape(b.status):''}</span><span class="row-summary">${escape(b.summary || '原站暂未提供简介')}</span><span class="row-metrics">${metricsHTML(b)}</span></span><span class="rank">${isLibrary ? '书库' : Number.isFinite(b.rank) ? '# '+b.rank : '—'}</span></button>`).join('');
      if (books.length) body += `<div class="pagination"><span>共 ${books.length} 本 · 序号跨页连续</span><div><button data-action="page" data-page="${page-1}" aria-label="上一页" ${page<=1?'disabled':''}>‹</button><span>${Math.min(page,pages)} / ${pages}</span><button data-action="page" data-page="${page+1}" aria-label="下一页" ${page>=pages?'disabled':''}>›</button></div></div>`;
      body += '</div>'+renderDetail(books)+'</div>';
      if(snapshot.warnings?.length)body+=`<div class="source-warnings">${snapshot.warnings.map(w=>`<p>${escape(readableWarning(w))}</p>`).join('')}</div>`;
      const source = safeURL(snapshot.source_url);
      body += `<div class="source-note"><span>来源数据日期：${escape(snapshot.data_date || '原站未提供')} · ${isLibrary?'书库顺序不代表热度排名':'名次以本次原始榜单为准'}</span>${source?`<a href="${escape(source)}" target="_blank" rel="noopener noreferrer">查看来源 ↗</a>`:''}</div>`;
    }
    body += '</section>';
    if (p.message) body += `<p class="collection-hint">${escape(p.message)}</p>`;
    $('#content').innerHTML = body;
  }
  function categoryNames(options){
    if(platformID()!=='fanqie')return [options.category || '全部'];
    const verifiedFemale=['古风世情','科幻末世','游戏体育','女频衍生','玄幻言情','种田','年代','现言脑洞','宫斗宅斗','悬疑脑洞','古言脑洞','快穿','青春甜宠','星光璀璨','女频悬疑','职场婚恋','豪门总裁','民国言情'];
    const source=snapshot?.platform==='fanqie' && snapshot.channel===options.channel && snapshot.categories?.length ? snapshot.categories : options.channel==='女频'?verifiedFemale:['玄幻脑洞'];
    return [...new Set([options.category,...source].filter(Boolean))];
  }
  function renderBoardControls(){
    const id=platformID(), options=optionsFor(), key=scopeKey(), supported=id!=='fanqie_short';
    const status=viewingHistory()?'查看历史记录 · 自动更新已暂停':currentJob()?'后台自动更新中':autoMessages.get(key) || (supported?'自动更新 · 每 15 分钟检查':'此来源待接入');
    const channels=id==='fanqie'?['女频','男频']:[defaults[id]?.channel || '全部'];
    const categories=categoryNames(options), frequent=(options.channel==='女频'?['古言脑洞','现言脑洞','豪门总裁','年代','种田','快穿']:[options.category]).filter(value=>categories.includes(value));
    return `<div class="board-controls"><label>频道<select id="board-channel" ${channels.length===1?'disabled':''}>${channels.map(value=>`<option ${value===options.channel?'selected':''}>${escape(value)}</option>`).join('')}</select></label><label>榜单<select id="board-kind">${boardNames(id).map(value=>`<option ${value===options.board?'selected':''}>${escape(value)}</option>`).join('')}</select></label><label>分类<select id="board-category" aria-label="榜单分类" ${categories.length===1?'disabled':''}>${categories.map(value=>`<option ${value===options.category?'selected':''}>${escape(value)}</option>`).join('')}</select></label><div class="auto-status">${currentJob()?'<span class="spinner"></span>':'<span class="status-dot online"></span>'}<span>${escape(status)}</span>${viewingHistory()?'<button data-action="resume-auto">恢复自动更新</button>':''}</div></div>${id==='fanqie' && frequent.length>1?`<div class="category-shortcuts" aria-label="常用分类"><span>常用</span>${frequent.map(value=>`<button data-action="category" data-category="${escape(value)}" aria-pressed="${value===options.category}">${escape(value)}</button>`).join('')}</div>`:''}`;
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
    return `<aside class="detail" aria-label="作品详情"><div class="detail-top"><span>已选中的作品</span><button class="favorite-button" data-action="favorite" data-book="${escape(b.id)}" aria-pressed="${favorite(b)}">${favorite(b)?'♥ 已收藏':'♡ 收藏'}</button></div>${cover(b,true)}<h2>${escape(b.title)}</h2><p class="author">${escape(b.author || '作者未提供')}${b.status?' · '+escape(b.status):''}</p><div class="positions"><span>${position?'列表第 '+position+' 本':'已被当前筛选隐藏'}</span>${snapshot.platform!=='heiyan' && Number.isFinite(b.rank)?`<span class="original">原榜第 ${b.rank} 名</span>`:''}</div><p class="summary">${escape(b.summary || '本次来源未提供简介，可在作品原页查看。')}</p>${metrics.length?`<div class="metrics">${metrics.map((m)=>`<span>${escape(m.name)} <strong>${escape(m.value)}</strong>${escape(m.unit || '')}</span>`).join('')}</div>`:''}<div class="detail-actions">${url?`<a class="source-link" href="${escape(url)}" target="_blank" rel="noopener noreferrer">打开作品原页 ↗</a>`:''}<button class="button secondary small" data-action="import-text">导入正文</button></div><p class="text-status">已选对象已保存，可回到 WorkBuddy 继续分析。</p>${reports.map((r)=>`<button class="report-item" data-action="report" data-id="${escape(r.id)}">▤ ${escape(r.title || '拆书报告')}<br><small>${escape(r.scope || '')}</small></button>`).join('')}</aside>`;
  }
  function empty(title,text,actions='',symbol='▤',extra='') { return `<div class="empty ${extra}"><div class="empty-symbol" aria-hidden="true">${symbol}</div><h2>${escape(title)}</h2><p>${escape(text)}</p>${actions?`<div class="actions">${actions}</div>`:''}</div>`; }
  function loginCard(auth,compact=false) {
    const labels = {logged_out:['连接黑岩故事会','使用能够访问黑岩书库的账号登录。登录会在平台自己的窗口中完成。'],logging_in:['在打开的窗口中完成登录','完成后点击“检查连接”。连接成功后，将继续你已经发起的采集。'],expired:['黑岩登录已过期','之前的书目和选择仍然保留，重新登录即可继续更新。'],forbidden:['当前账号没有书库权限','请使用能够访问黑岩管理后台书库的账号。'],network_error:['暂时无法连接黑岩','请检查网络后重试，已有资料不会受到影响。'],unavailable:['还需要准备浏览器环境','回到 WorkBuddy，让它完成浏览器依赖安装，再从这里连接。']};
    const [title,text] = labels[auth.state] || labels.logged_out;
    return `<div class="empty login-card${compact?' reconnect-card':''}"><div class="empty-symbol" aria-hidden="true">↗</div><h2>${escape(title)}</h2><p>${escape(auth.message || text)}${compact?' 已保存的书目仍可浏览，重新连接后继续更新。':''}</p><div class="actions"><button class="button primary" data-action="login" ${authPending?'disabled':''}>${authPending?'<span class="spinner"></span>正在打开':'去黑岩登录'}</button><button class="button secondary" data-action="check-auth" ${authPending?'disabled':''}>我已登录，检查连接</button></div><small>登录资料仅保存在本机专用浏览器中</small></div>`;
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
    const id=platformID(), current=optionsFor(id);
    showDialog('采集设置',`<form id="options-form"><p>设置保存后自动更新。日常使用无需再次填写。</p><div class="form-grid"><div class="field"><label for="channel">频道</label><select id="channel" name="channel">${(id==='fanqie'?['女频','男频']:[defaults[id]?.channel || '全部']).map(x=>`<option ${current.channel===x?'selected':''}>${escape(x)}</option>`).join('')}</select></div><div class="field"><label for="category">分类</label><input id="category" name="category" value="${escape(current.category || '全部')}" required maxlength="40"></div><div class="field"><label for="board">榜单</label><select id="board" name="board">${(id==='fanqie'?['新书榜','阅读榜']:id==='qidian'?['月票榜','签约新书榜','公众新书榜','新人新书榜','畅销榜']:id==='ciweimao'?['月票榜']:id==='heiyan'?['书库']:['热门故事']).map(x=>`<option ${current.board===x?'selected':''}>${escape(x)}</option>`).join('')}</select></div><div class="field"><label for="limit">最大采集数量</label><input id="limit" name="limit" type="number" min="1" max="1000" value="${current.limit || 1000}" required></div></div><p class="help-inline">分类以平台真实名称为准。将持续翻页，最多读取 100 页；平台实际提供多少条，就保存多少条。</p><div class="form-actions"><button class="button secondary" type="button" data-action="close-dialog">取消</button><button class="button primary" type="submit">保存条件</button></div></form>`);
  }
  async function showLatest(id){
    const meta=state.snapshots.find(item=>item.id===id);
    if(!meta)return;
    setHistory(null);latestResults.delete(scopeKey());
    await updateView({snapshot_id:id,selected_id:null,page:1});
  }
  async function receiveSnapshot(id,platformId,options){
    while(pendingViewMutations>0)await saving.catch(()=>{});
    if(platformID()!==platformId || scopeKey(platformId,options)!==scopeKey() || section!=='browse')return;
    if(viewingHistory() || view().selected_id){
      if(id!==snapshot?.id)latestResults.set(scopeKey(platformId,options),id);
      render();return;
    }
    if(id!==snapshot?.id)await updateView({snapshot_id:id,selected_id:null,page:1});
  }
  async function maybeAutoCollect(){
    if(!state || document.hidden || section!=='browse' || viewingHistory() || activeRequest || authPending)return;
    const id=platformID(), p=platform(), options=optionsFor(), key=scopeKey(id,options);
    if(id==='fanqie_short' || (id==='heiyan' && state.auth?.heiyan?.state!=='connected'))return;
    if(['planned','unavailable'].includes(p.status) && id!=='heiyan')return;
    if(currentJob() || Date.now()<(nextAutoAt.get(key) || 0))return;
    // Freshness depends on the complete collection scope (including limits).
    // Ask the backend even when a visible snapshot was collected recently.
    nextAutoAt.set(key,Date.now()+60000);
    await collect(true);
  }
  async function collect(automatic=false) {
    if(currentJob() || activeRequest)return;
    const id=platformID(), options=optionsFor(), key=scopeKey(id,options);
    if(id==='heiyan' && state.auth?.heiyan?.state!=='connected'){
      if(!automatic){continueAfterLogin=true;await login();}return;
    }
    if(!automatic){$('#notice').hidden=true;setHistory(null);}
    activeRequest=true;autoMessages.set(key,'正在检查更新…');render();
    try {
      const result=await api('/api/collect',{platform:id,options,automatic});
      if(result.status==='fresh'){
        nextAutoAt.set(key,Date.now()+(result.retry_after_seconds || 900)*1000);
        autoMessages.set(key,'已是最新 · 每 15 分钟自动检查');
        await refresh();if(result.snapshot_id)await receiveSnapshot(result.snapshot_id,id,options);return;
      }
      if(result.status==='cooldown'){
        nextAutoAt.set(key,Date.now()+(result.retry_after_seconds || 300)*1000);
        if(result.requires_login && id==='heiyan'){
          state.auth ||= {};state.auth.heiyan=result.auth || {state:'expired',message:'黑岩登录已过期，请重新连接。'};
          autoMessages.set(key,'需要重新登录，已保存的书目仍可浏览');
          await refresh();
        } else autoMessages.set(key,'暂未取得更新，稍后自动重试');
        render();return;
      }
      nextAutoAt.set(key,Date.now()+AUTO_TTL);
      autoMessages.set(key,'后台更新中，已有内容可继续查看');
      await refresh();
      const job=state.jobs.find(item=>item.id===(result.job_id || result.job?.id));
      if(job?.status==='completed' && job.snapshot_id){
        await receiveSnapshot(job.snapshot_id,id,options);
        autoMessages.set(key,'已更新 · 每 15 分钟自动检查');
      } else if(job?.status==='failed'){
        autoMessages.set(key,'暂未取得更新，保留上次结果');
        if(!automatic)notice(job.message || '采集未完成，已有结果保留。');
      } else if(!automatic)toast('正在更新，完成后会自动保存。');
    } catch(error){
      nextAutoAt.set(key,Date.now()+5*60*1000);autoMessages.set(key,'更新暂不可用，稍后自动重试');
      if(!automatic)throw error;
    } finally {activeRequest=false;render();}
  }
  async function changeOptions(patch){
    const id=platformID(), options={...optionsFor(id),...patch};
    savedOptions[id]=options;try{localStorage.setItem('fagao-collection-options',JSON.stringify(savedOptions));}catch{}
    setHistory(null);latestResults.delete(scopeKey());
    const matching=state.snapshots.find(item=>sameScope(item,id,options));
    await updateView({snapshot_id:matching?.id || null,selected_id:null,page:1,filters:{query:'',sort:'source',favorites:false}});
    await maybeAutoCollect();
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
          await api('/api/collect',{platform:'heiyan',options:optionsFor('heiyan'),automatic:false});
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
              if(['failed','error','cancelled'].includes(job.status)){nextAutoAt.set(scopeKey(job.platform,job.options || optionsFor(job.platform)),Date.now()+5*60*1000);autoMessages.set(scopeKey(job.platform,job.options || optionsFor(job.platform)),'更新未完成，已有结果保留');if(!job.automatic)notice(job.message || '采集未完成。已有书目仍然保留。');}
              else if(!job.automatic)toast(job.message || '新的采集结果已保存。');
            }
          }
          if(ended) {
            const completed=results.map(r=>r.job || r).find(j=>j.status==='completed' && j.snapshot_id && j.platform===platformID() && (!j.options || scopeKey(j.platform,j.options)===scopeKey()) && section==='browse');
            await refresh();
            if(completed){const jobOptions=completed.options || optionsFor(completed.platform);await receiveSnapshot(completed.snapshot_id,completed.platform,jobOptions);autoMessages.set(scopeKey(completed.platform,jobOptions),'已更新 · 每 15 分钟自动检查');}
          } else render();
        }
        await maybeAutoCollect();
      } catch(error) { notice(error.message); }
      finally { schedulePoll(); }
    },2200);
  }
  async function openRecord(el) {
    const meta=state.snapshots.find(s=>s.id===el.dataset.snapshot);
    const snap=meta || await api(`/api/snapshots/${encodeURIComponent(el.dataset.snapshot)}`);
    section='browse';setHistory(el.dataset.snapshot,snap.mode || (snap.platform==='heiyan'?'short':'long'));
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
    if(a==='mode') {section='browse';await updateView({mode:el.dataset.mode});await maybeAutoCollect();return;}
    if(a==='platform') {
      section='browse';const id=el.dataset.platform, existing=state.snapshots.find(s=>sameScope(s,id,optionsFor(id)));setHistory(null);
      if(id===platformID()){render();await maybeAutoCollect();return;}
      await updateView({platform:id,snapshot_id:existing?.id || null,selected_id:null,page:1,filters:{query:'',sort:'source',favorites:false}});await maybeAutoCollect();return;
    }
    if(a==='section'){section=el.dataset.section;render();return;}
    if(a==='select-book'){await updateView({selected_id:el.dataset.book});return;}
    if(a==='page'){await updateView({page:Number(el.dataset.page)});return;}
    if(a==='clear-filters'){await updateView({filters:{query:'',sort:'source',favorites:false},page:1});return;}
    if(a==='favorite') { const b=snapshot.books.find(x=>x.id===el.dataset.book);await api('/api/favorite',{snapshot_id:snapshot.id,book_id:b.id,enabled:!favorite(b)}); await refresh();return; }
    if(a==='collection-options'){collectionOptions();return;}
    if(a==='category'){if(el.dataset.category!==optionsFor().category)await changeOptions({category:el.dataset.category});return;}
    if(a==='collect'){await collect(false);return;}
    if(a==='show-latest'){await showLatest(el.dataset.id);return;}
    if(a==='resume-auto'){setHistory(null);render();await maybeAutoCollect();return;}
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
  document.addEventListener('error',event=>{const image=event.target;if(image?.classList?.contains('cover-image')){image.hidden=true;image.closest('.book-cover')?.classList.add('cover-unavailable');}},true);
  document.addEventListener('click',(event)=>{
    const el=event.target.closest('[data-action]'); if(!el || el.disabled)return;
    dispatch(el).catch(error=>notice(error.message));
  });
  document.addEventListener('change',(event)=>{
    const el=event.target;
    if(el.id==='snapshot-select'){setHistory(el.value);updateView({snapshot_id:el.value,selected_id:null,page:1}).catch(e=>notice(e.message));}
    if(el.id==='board-channel'){changeOptions({channel:el.value,category:el.value==='男频'?'玄幻脑洞':'古言脑洞'}).catch(e=>notice(e.message));}
    if(el.id==='board-kind')changeOptions({board:el.value}).catch(e=>notice(e.message));
    if(el.id==='board-category' && el.value.trim())changeOptions({category:el.value.trim()}).catch(e=>notice(e.message));
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
      const options={channel:String(form.get('channel')),category:String(form.get('category')).trim(),board:String(form.get('board')),limit:Number(form.get('limit')),max_pages:100};
      $('#dialog').close();changeOptions(options).catch(error=>notice(error.message));return;
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
      if(snap.id){setHistory(snap.id,snap.mode || mode());await updateView({mode:snap.mode || mode(),platform:snap.platform || platformID(),snapshot_id:snap.id,selected_id:null,page:1});}
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
