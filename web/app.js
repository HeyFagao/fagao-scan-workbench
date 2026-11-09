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
    fanqie:{channel:'女频',category:'古言脑洞',board:'新书榜',limit:10000,max_pages:100},
    qidian:{channel:'男频',category:'全部',board:'月票榜',limit:10000,max_pages:100},
    ciweimao:{channel:'综合',category:'全部',board:'月票榜',limit:10000,max_pages:100},
    heiyan:{channel:'全部',category:'书库',board:'书库',limit:10000,max_pages:100},
    fanqie_short:{channel:'短篇',category:'全部',board:'热门故事',limit:10000,max_pages:100}
  };
  let state = null, snapshot = null, section = 'overview', loadingVersion = 0, pendingText = null, viewGeneration = 0, refreshGeneration = 0, pendingViewMutations = 0;
  let saving = Promise.resolve(), pollTimer = null, toastTimer = null;
  let contentPanel=null, contentPanelSequence=0;
  let overviewChannel='全部', dashboardCategoryMetric='primary_median';
  let overview=null, overviewLoading=false, overviewError='', overviewPlatform='fanqie', overviewRequest=0, overviewNextAt=0, overviewRefreshing=false, overviewPollKey='';
  let listWindow={key:'',count:20}, listObserver=null, lastListScrollAt=0, navigationGeneration=0;
  const HEIYAN_BOOKLIST='https://docs.qq.com/sheet/DYVNwaVJZV2ZxTWl3?tab=q1ans7';
  let searchTimer, activeRequest = false, savedOptions = {};
  const AUTO_TTL = 15 * 60 * 1000;
  const nextAutoAt = new Map(), autoMessages = new Map(), latestResults = new Map(), snapshotJobs=new Map();
  let historyViews = {};
  try { historyViews = JSON.parse(localStorage.getItem('fagao-history-views') || '{}'); } catch {}
  try {
    savedOptions = JSON.parse(localStorage.getItem('fagao-collection-options') || '{}');
    if(localStorage.getItem('fagao-options-version')!=='3'){
      for(const value of Object.values(savedOptions)){if(value.limit==null || value.limit===1000)value.limit=10000;value.max_pages ??= 100;}
      localStorage.setItem('fagao-options-version','3');
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
  const currentJob = () => (state?.jobs || []).find((j) => !['text','overview'].includes(j.kind) && j.platform === platformID() && (!j.options || scopeKey(j.platform,j.options)===scopeKey()) && ['queued','running','pending'].includes(j.status));
  function optionsFor(id=platformID()) {
    const stored=state?.snapshots?.find(item=>item.id===(state.views?.[mode()]?.snapshot_id) && item.platform===id);
    const previous=stored ? {channel:stored.channel,category:stored.category,board:stored.board} : {};
    const preferences=stored && historyViews[mode()]===stored.id ? {} : savedOptions[id];
    return {...defaults[id],...previous,...preferences,mode:id==='heiyan'||id==='fanqie_short'?'short':'long'};
  }
  function scopeKey(id=platformID(),options=optionsFor(id)) { return [id,options.mode || (id==='heiyan'||id==='fanqie_short'?'short':'long'),options.channel,options.category,options.board,options.period || (id==='ciweimao'?'月榜':''),options.limit ?? '',options.max_pages ?? 100,options.detail_budget ?? ''].join('|'); }
  function sameScope(item,id=platformID(),options=optionsFor(id)) { return item?.platform===id && ['channel','category','board'].every(key=>String(item[key] || '')===String(options[key] || '')); }
  function bestSnapshotForScope(id=platformID(),options=optionsFor(id)){
    // Match overview._latest_current: choose a precise version per source,
    // then compare the source calendar day and actual coverage across sources.
    const count=item=>Array.isArray(item.books)?item.books.length:Number.isFinite(item.books_count)?item.books_count:Number.isFinite(item.count)?item.count:0;
    const compare=(left,right)=>{for(let index=0;index<left.length;index++){if(left[index]!==right[index])return left[index]>right[index]?1:-1;}return 0;};
    const sourceVersion=item=>{
      const value=typeof item.data_date==='string'?item.data_date.trim():'',match=value.match(/^(\d{4}-\d{2}-\d{2})(?:$|[T ])/);
      if(!match)return null;
      const day=Date.parse(match[1]+'T00:00:00Z');
      if(!Number.isFinite(day) || new Date(day).toISOString().slice(0,10)!==match[1])return null;
      let normalized=value.replace(' ','T');
      if(normalized.length>10 && !/(?:Z|[+-]\d{2}:?\d{2})$/i.test(normalized))normalized+='Z';
      const stamp=Date.parse(normalized);return Number.isFinite(stamp)?{stamp,day}:null;
    };
    const sourceKey=item=>{const version=sourceVersion(item),collected=Date.parse(item.collected_at || item.created_at || '');return [version?.stamp || 0,version?count(item):0,version && item.complete===true?1:0,Number.isFinite(collected)?collected:0,String(item.id || '')];};
    const bySource=new Map();
    for(const item of state?.snapshots || []){
      if(!sameScope(item,id,options))continue;
      const source=item.source_id || 'direct',previous=bySource.get(source);
      if(!previous || compare(sourceKey(item),sourceKey(previous))>0)bySource.set(source,item);
    }
    const priority=item=>[sourceVersion(item)?.day || 0,item.complete===true?1:0,count(item),(item.source_id || 'direct')==='direct'?1:0,...sourceKey(item)];
    return [...bySource.values()].reduce((best,item)=>!best || compare(priority(item),priority(best))>0?item:best,null);
  }
  function viewingHistory(){return historyViews[mode()]===view().snapshot_id && !!view().snapshot_id;}
  function setHistory(snapshotId,targetMode=mode()){
    if(snapshotId)historyViews[targetMode]=snapshotId;else delete historyViews[targetMode];
    try{localStorage.setItem('fagao-history-views',JSON.stringify(historyViews));}catch{}
  }
  function boardNames(id=platformID()) { return id==='fanqie'?['新书榜','阅读榜']:id==='qidian'?['月票榜','签约新书榜','公众新书榜','新人新书榜','畅销榜']:id==='ciweimao'?['月票榜']:id==='heiyan'?['书库']:['热门故事']; }
  const stopReasons={loading:'当前作品已载入，后续页面会继续补充',waiting_for_more:'向下浏览时继续读取后续作品',paused_for_demand:'已暂停读取，继续向下浏览时恢复',last_page:'已读取平台最后一页',empty_page:'平台已无更多书目',requested_limit:'已达到本次采集数量',max_pages:'已达到本次页数上限',repeated_page:'平台返回重复页面，已停止避免重复',version_changed:'榜单版本发生变化，已保留本次结果',invalid_response:'平台返回的数据暂不可读',wrong_page:'平台返回的页码不一致',request_failed:'后续页面暂时无法连接',session_required:'后续页面需要平台登录',pagination_unverified:'后续翻页方式仍待核实',rank_conflict:'不同页面出现名次冲突',book_conflict:'不同页面出现作品信息冲突',invalid_next_page:'平台未提供可用的下一页'};
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
    if (next?.views) {
      const retained=(state?.jobs || []).filter(job=>(['queued','running','pending','cancelling'].includes(job.status) || job.paused_for_demand) && !(next.jobs || []).some(item=>item.id===job.id));
      state=next;state.jobs=[...(next.jobs || []),...retained];
    }
    if (!state) return;
    state.views ||= {}; state.snapshots ||= []; state.favorites ||= []; state.history ||= []; state.reports ||= []; state.jobs ||= []; state.texts ||= [];
  }
  async function refresh({silent = false} = {}) {
    while(pendingViewMutations>0)await saving.catch(()=>{});
    const viewVersion=viewGeneration, requestVersion=++refreshGeneration;
    const next = await api('/api/state');
    if(pendingViewMutations>0 || viewVersion!==viewGeneration || requestVersion!==refreshGeneration)return;
    acceptState(next);
    $('#server-dot').classList.add('online'); $('#server-label').textContent = '本地工作台已连接';
    if (!silent) await loadSnapshot(); else render();
    syncContentPanel();
    if(section==='overview')await loadOverview();
    schedulePoll();
    queueMicrotask(()=>{maybeAutoCollect().catch(()=>{});maybeAutoOverview().catch(()=>{});});
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
    const previousDetail=$('.detail');
    const detailPosition=previousDetail?.dataset.book ? {book:previousDetail.dataset.book,snapshot:previousDetail.dataset.snapshot,top:previousDetail.scrollTop} : null;
    const focusedQuery=document.activeElement?.id==='query' ? {value:document.activeElement.value,start:document.activeElement.selectionStart,end:document.activeElement.selectionEnd} : null;
    renderNavigation();
    if(section==='overview')renderOverview();else if (section === 'browse') renderBrowse(); else renderSaved();
    const currentDetail=$('.detail');
    if(detailPosition && currentDetail?.dataset.book===detailPosition.book && currentDetail.dataset.snapshot===detailPosition.snapshot)currentDetail.scrollTop=detailPosition.top;
    observeListEnd();
    renderBridge();
    if(focusedQuery && $('#query')){
      const input=$('#query');input.value=focusedQuery.value;input.focus({preventScroll:true});
      try{input.setSelectionRange(focusedQuery.start,focusedQuery.end);}catch{}
    }
  }
  function pageHead(title, sub, actions='') { return `<div class="page-head"><div><div class="eyebrow">${section==='browse' ? modes[mode()]+'作品' : '我的工作台'}<span class="dot">/</span> ${section==='browse' ? escape(nameOf(platformID())) : '保存在本机'}</div><h1>${escape(title)}</h1><p>${escape(sub)}</p></div>${actions ? `<div class="head-actions">${actions}</div>` : ''}</div>`; }
  function renderBrowse() {
    const p = platform(), job = currentJob();
    const isLibrary = p.id === 'heiyan', unsupported = ['planned','unavailable'].includes(p.status) && p.id === 'fanqie_short';
    const options=optionsFor(), key=scopeKey();
    const title = isLibrary ? '黑岩故事会 · 选题书单' : `${nameOf(p.id)} · ${options.board || '作品发现'}`;
    const sub = isLibrary ? '直接打开腾讯文档书单，在原页面登录后查看。' : '持续更新平台榜单，发现值得阅读与研究的作品。';
    const action = isLibrary ? '' : `${snapshot?'<button class="button secondary" data-action="insights">榜单观察</button>':''}<button class="button secondary" data-action="collection-options" aria-label="调整采集设置">采集设置</button><button class="button secondary" data-action="collect" ${job || activeRequest || unsupported ? 'disabled' : ''}>${job ? (job.waiting_for_more?'已加载 · 向下继续':'<span class="spinner"></span>正在读取') : '↻ '+(isLibrary?'更新书库':'更新榜单')}</button>`;
    let body = pageHead(title,sub,action)+(isLibrary ? heiyanBooklistCard(!!snapshot) : renderBoardControls());
    const freshResult=latestResults.get(key);
    if(freshResult && freshResult!==snapshot?.id)body+=`<div class="new-results"><span>新的榜单已更新。当前选中的作品仍保留在原记录中。</span><button data-action="show-latest" data-id="${escape(freshResult)}">查看最新榜单 →</button></div>`;
    if(isLibrary && !snapshot){$('#content').innerHTML=body;return;}
    const related = state.snapshots.filter((s) => s.platform === p.id && s.mode === mode());
    body += '<section class="surface" aria-label="作品列表"><div class="surface-head"><div><strong>'+escape(snapshot ? scopeOf() || '本次作品' : '正在同步'+(isLibrary?'书库':'榜单'))+'</strong><span class="badge">'+(isLibrary?'书库资料':'平台榜单')+'</span>';
    body += `<div class="meta">${snapshot ? `${(snapshot.books || []).length} 本作品 · 采集于 ${escape(date(snapshotDate(snapshot)))}` : '自动保存每次榜单，更新不会改变已选中的作品'}</div></div>`;
    if(snapshot?.pagination)body+=`<div class="meta pagination-summary">${escape(paginationSummary(snapshot))}</div>`;
    if (related.length > 1) body += `<select class="snapshot-select" id="snapshot-select" aria-label="切换历史榜单">${related.map((s) => `<option value="${escape(s.id)}" ${s.id===snapshot?.id?'selected':''}>${escape(date(snapshotDate(s)))} · ${escape(s.category || s.board || '')} · ${s.books_count ?? s.count ?? '?'} 本</option>`).join('')}</select>`;
    body += '</div>';
    if (job) body += `<div class="job-strip" role="status">${job.waiting_for_more?'<span class="status-dot online"></span>':'<span class="spinner"></span>'}<span>${escape(job.message || '正在读取平台…')}</span><button data-action="cancel-job" data-id="${escape(job.id)}">取消</button></div>`;
    const recentJob=state.jobs.find(j=>!['text','overview'].includes(j.kind) && j.platform===p.id && (!j.options || scopeKey(j.platform,j.options)===scopeKey()));
    if(!job && recentJob && ['failed','interrupted'].includes(recentJob.status))body+=`<div class="job-strip job-warning"><span>${escape(recentJob.message || '上次采集未完成，已有结果保留。')}</span></div>`;

    if (!snapshot) {
      if (unsupported) body += empty('这个来源还在接入中',p.message || '可以先导入已有的书目文件，继续选书和分析。','<button class="button secondary" data-action="import-snapshot">导入书目 JSON</button>','▤');
      else body += empty(job ? '正在读取平台榜单' : '正在同步作品',job ? '作品会在完成后自动显示。你可以先浏览其他平台。' : '工作台会自动取得当前榜单。已有资料也可以直接导入。','<button class="button secondary" data-action="import-snapshot">导入已有书目</button>','▤');
    } else {
      body += renderToolbar();
      const books = orderedBooks(), size = Math.max(1,Number(view().page_size) || 20), page = Math.max(1,Number(view().page) || 1), pages = Math.max(1,Math.ceil(books.length/size));
      const windowKey=[snapshot.platform,snapshot.channel,snapshot.category,snapshot.board,viewingHistory()?snapshot.id:'live',JSON.stringify(view().filters || {}),page,size].join('|');
      if(listWindow.key!==windowKey)listWindow={key:windowKey,count:size};
      const start = (Math.min(page,pages)-1)*size, end=Math.min(books.length,start+(page===1?listWindow.count:size)), sliced = books.slice(start,end);
      body += '<div class="book-layout"><div class="book-list"><div class="list-key"><span>列表序号 / 作品</span><span>'+ (isLibrary ? '资料来源' : '原榜名次') +'</span></div>';
      if (!books.length) body += empty('没有匹配的作品','换个关键词，或清除筛选条件。','<button class="button secondary small" data-action="clear-filters">清除筛选</button>','⌕','compact');
      else body += sliced.map((b,i) => `<button class="book-row" data-action="select-book" data-book="${escape(b.id)}" aria-pressed="${b.id===view().selected_id}"><span class="list-number">${String(start+i+1).padStart(2,'0')}</span>${cover(b)}<span class="row-content"><span class="book-title">${escape(b.title)}${favorite(b) ? '<span class="star" aria-label="已收藏"> ♡</span>' : ''}</span><span class="book-meta">${escape(b.author || '作者未提供')}${(b.tags?.length) ? '<span>·</span>'+escape(Array.isArray(b.tags)?b.tags.slice(0,2).join(' · '):b.tags) : ''}${b.status?'<span>·</span>'+escape(b.status):''}</span><span class="row-summary">${escape(b.summary || '原站暂未提供简介')}</span><span class="row-metrics">${metricsHTML(b)}</span></span><span class="rank">${isLibrary ? '书库' : Number.isFinite(b.rank) ? '# '+b.rank : '—'}</span></button>`).join('');
      const continuation=continuationJob();
      if(page===1 && (end<books.length || continuation))body+=`<div class="list-load-more" id="list-end"><button class="button secondary" data-action="load-more">${end<books.length?`加载更多作品（已显示 ${end} / ${books.length}）`:'继续读取后续作品'}</button><small>${end<books.length?'向下浏览时继续显示后续作品':'已取得的作品可先浏览，继续向下时再读取后续页面'}</small></div>`;
      if (books.length) body += `<div class="pagination"><span>共 ${books.length} 本 · 序号跨页连续</span><div><button data-action="page" data-page="${page-1}" aria-label="上一页" ${page<=1?'disabled':''}>‹</button><span>${Math.min(page,pages)} / ${pages}</span><button data-action="page" data-page="${page+1}" aria-label="下一页" ${page>=pages?'disabled':''}>›</button></div></div>`;
      body += '</div>'+renderDetail(books)+'</div>';
      if(snapshot.warnings?.length)body+=`<div class="source-warnings">${snapshot.warnings.map(w=>`<p>${escape(readableWarning(w))}</p>`).join('')}</div>`;
      const source = safeURL(snapshot.source_url);
      body += `<div class="source-note"><span>来源数据日期：${escape(snapshot.data_date || '原站未提供')} · ${isLibrary?'书库顺序不代表热度排名':snapshot.source_id && snapshot.source_id!=='direct'?'来源列表位置不代表原榜名次':'名次以本次原始榜单为准'}</span>${source?`<a href="${escape(source)}" target="_blank" rel="noopener noreferrer">查看来源 ↗</a>`:''}</div>`;
    }
    body += '</section>';
    if (p.message && !isLibrary) body += `<p class="collection-hint">${escape(p.message)}</p>`;
    $('#content').innerHTML = body;
  }
  function overviewJob(){return overview?.update || (state?.jobs || []).find(job=>job.kind==='overview') || null;}
  function overviewJobActive(job){return ['queued','running','pending','cancelling'].includes(job?.status) || job?.public_source?.status==='running';}
  function overviewRunning(){return overviewJobActive(overviewJob());}
  function compactNumber(value){
    if(!Number.isFinite(value))return '—';
    return Math.abs(value)>=100000000?(value/100000000).toLocaleString('zh-CN',{maximumFractionDigits:1})+'亿':Math.abs(value)>=10000?(value/10000).toLocaleString('zh-CN',{maximumFractionDigits:1})+'万':value.toLocaleString('zh-CN',{maximumFractionDigits:1});
  }
  function overviewBookLink(book,label=book.title,extra=''){
    const sid=book.snapshot_id || book.selection?.snapshot_id, bid=book.book_id || book.selection?.book_id;
    return sid && bid ? `<button class="overview-book-link ${extra}" data-action="overview-book" data-snapshot="${escape(sid)}" data-book="${escape(bid)}">${escape(label)}</button>`:`<span>${escape(label)}</span>`;
  }
  function dashboardView(panel){
    const dashboard=panel?.dashboard, channels=dashboard?.channel_options || ['全部'];
    const channel=overviewPlatform==='fanqie' && channels.includes(overviewChannel)?overviewChannel:'全部';
    return {data:dashboard?.views?.[channel],channel,channels};
  }
  function chartCard(title,meta,body,extra=''){
    return `<section class="data-card ${extra}"><header class="data-card-head"><h2>${escape(title)}</h2><span>${escape(meta || '')}</span></header>${body}</section>`;
  }
  function distributionChart(items,label,validCount){
    const rows=(items || []).filter(item=>Number.isFinite(item.count) && item.count>=0), max=Math.max(0,...rows.map(item=>item.count));
    if(!max)return `<div class="chart-unavailable">来源暂未提供可统计的${escape(label)}<small>缺失值不记为零</small></div>`;
    return `<div class="histogram" role="img" aria-label="${escape(label)}分布，${statNumber(validCount)} 本有效样本"><div class="histogram-axis"><span>${compactNumber(max)}</span><span>${compactNumber(max/2)}</span><span>0 本</span></div><div class="histogram-plot"><div class="histogram-grid" aria-hidden="true"><i></i><i></i><i></i></div>${rows.map(item=>`<div class="histogram-column" title="${escape(item.label)}：${statNumber(item.count)} 本${Number.isFinite(item.primary_median)?'；指标中位数 '+statNumber(item.primary_median):''}" data-bin-label="${escape(item.label)}" data-count="${item.count}"><strong>${compactNumber(item.count)}</strong><div class="histogram-bar-area"><span class="histogram-bar" style="height:${item.count/max*100}%"></span></div><small>${escape(item.label)}</small></div>`).join('')}</div></div><div class="chart-caption">有效样本 <strong>${statNumber(validCount)}</strong> 本</div>`;
  }
  function categoryChart(data,metric){
    const byValue=dashboardCategoryMetric==='primary_median' && data.kpis.valid_primary_count>0;
    const rows=[...(data.categories || [])].filter(item=>Number.isFinite(byValue?item.primary_median:item.count)).sort((a,b)=>(byValue?b.primary_median-a.primary_median:b.count-a.count)).slice(0,8);
    const max=Math.max(0,...rows.map(item=>byValue?item.primary_median:item.count));
    const choice=`<div class="chart-switch" aria-label="分类统计指标"><button data-action="dashboard-category-metric" data-metric="count" aria-pressed="${!byValue}">样本量</button><button data-action="dashboard-category-metric" data-metric="primary_median" aria-pressed="${byValue}" ${data.kpis.valid_primary_count?'':'disabled'}>中位${escape(metric)}</button></div>`;
    if(!rows.length)return choice+`<div class="chart-unavailable">来源暂未提供${byValue?escape(metric):'分类'}数据<small>切换统计指标查看已有样本</small></div>`;
    return choice+`<div class="category-bar-chart" role="img" aria-label="分类${byValue?'中位'+escape(metric):'样本量'}横向条形图"><div class="category-bar-scale"><span>0</span><span>${compactNumber(max/2)}</span><span>${compactNumber(max)}${byValue?'':' 本'}</span></div>${rows.map(item=>{const value=byValue?item.primary_median:item.count;return `<div class="category-bar-row" title="${escape(item.channel)} · ${escape(item.category)}：${statNumber(item.count)} 本；中位${escape(metric)} ${statNumber(item.primary_median)}"><div class="category-bar-label"><strong>${escape(item.category)}</strong><small>${escape(item.channel==='男频'?'男':item.channel==='女频'?'女':item.channel)}</small></div><div class="category-bar-track"><span style="width:${max?value/max*100:0}%"></span></div><strong class="category-bar-value">${compactNumber(value)}</strong></div>`;}).join('')}</div><div class="chart-caption">显示前 ${rows.length} 个分类 · 各分类单独统计</div>`;
  }
  function axisTicks(max){
    if(max<=0)return [0,1];
    const power=Math.pow(10,Math.floor(Math.log10(max))), step=power*(max/power>5?2:max/power>2?1:.5);
    const result=[0];for(let value=step;value<=max && result.length<7;value+=step)result.push(value);
    if(result.length<2)result.push(max);return result;
  }
  function scatterChart(data,metric){
    const points=(data.scatter?.points || []).filter(item=>Number.isFinite(item.word_count) && item.word_count>=0 && Number.isFinite(item.primary_count) && item.primary_count>=0);
    if(!points.length)return `<div class="chart-unavailable">暂无同时具备字数与${escape(metric)}的样本<small>取得完整指标后显示散点，不补造数据</small></div>`;
    const maxX=Math.max(1,...points.map(item=>item.word_count)),maxY=Math.max(1,...points.map(item=>item.primary_count));
    const positive=points.map(item=>item.primary_count).filter(value=>value>0),minY=Math.min(...positive),logY=positive.length>1 && maxY/Math.max(1,minY)>100;
    const xTicks=axisTicks(maxX),yTicks=logY?[0,...[1,10,100,1000,10000,100000,1000000,10000000,100000000].filter(value=>value<=maxY).filter((value,index,all)=>index>=Math.max(0,all.length-5))]:axisTicks(maxY);
    const x=value=>57+value/maxX*350,y=value=>216-(logY?Math.log1p(value)/Math.log1p(maxY):value/maxY)*174;
    const metricUnit=overviewPlatform==='fanqie'?'人':'票';
    return `<div class="scatter-wrap"><svg class="scatter-chart" viewBox="0 0 438 258" role="group" aria-label="字数与${escape(metric)}关系散点图${logY?'，纵轴为对数(1+数值)刻度':''}"><title>每个点是一部作品；横轴字数，纵轴${escape(metric)}。点击查看原榜作品。</title>${yTicks.map(value=>`<line x1="57" x2="407" y1="${y(value)}" y2="${y(value)}" class="chart-grid-line"/><text x="48" y="${y(value)+4}" text-anchor="end" class="chart-tick">${compactNumber(value)}</text>`).join('')}${xTicks.map(value=>`<line x1="${x(value)}" x2="${x(value)}" y1="42" y2="216" class="chart-grid-line vertical"/><text x="${x(value)}" y="235" text-anchor="middle" class="chart-tick">${compactNumber(value)}</text>`).join('')}<text x="57" y="25" class="chart-axis-title">${escape(metric)} · ${metricUnit}${logY?' · log(1+x)':''}</text><text x="407" y="254" text-anchor="end" class="chart-axis-title">字数</text>${points.map(item=>{const sid=item.snapshot_id || item.selection?.snapshot_id,bid=item.book_id || item.selection?.book_id,label=`${item.title} · ${item.channel || ''} · ${statNumber(item.word_count)} 字 · ${metric} ${statNumber(item.primary_count)}${metricUnit}`;return `<circle class="scatter-dot ${item.channel==='女频'?'female':'male'}" cx="${x(item.word_count).toFixed(2)}" cy="${y(item.primary_count).toFixed(2)}" r="4.1" tabindex="0" role="button" aria-label="${escape(label)}" data-action="overview-book" data-snapshot="${escape(sid)}" data-book="${escape(bid)}" data-words="${item.word_count}" data-primary="${item.primary_count}"><title>${escape(label)}</title></circle>`;}).join('')}</svg></div><div class="chart-caption"><span>${overviewPlatform==='fanqie'?'<i class="legend-dot male"></i>男频 <i class="legend-dot female"></i>女频 · ':''}显示 ${points.length} / ${statNumber(data.scatter?.total)} 本${data.scatter?.sampled?' · 分位抽样':''}</span><span>${logY?'对数(1+数值) · ':''}点击看作品</span></div>`;
  }
  function compactBars(rows,label){
    const items=(rows || []).slice(0,8),max=Math.max(0,...items.map(item=>Number(item.count) || 0));
    if(!max)return `<div class="chart-unavailable compact">暂无${escape(label)}统计</div>`;
    return `<div class="compact-bars" role="img" aria-label="${escape(label)}分布">${items.map(item=>`<div title="${escape(item.label || item.word)}：${statNumber(item.count)} 本${Number.isFinite(item.share)?'，'+statNumber(item.share)+'%':''}"><span>${escape(item.label || item.word)}</span><i><b style="width:${item.count/max*100}%"></b></i><strong>${compactNumber(item.count)}<small>${Number.isFinite(item.share)?statNumber(item.share)+'%':'本'}</small></strong></div>`).join('')}</div>`;
  }
  function dashboardSources(data){
    const sources=data.sources || [];
    if(!sources.length)return '';
    const dates=[...new Set(sources.flatMap(source=>source.dates || []).map(value=>String(value).slice(0,10)))].sort();
    const sampleScopes=[...new Set(sources.filter(source=>source.source_id!=='direct').map(source=>source.sample_scope).filter(Boolean))];
    const dateRange=dates.length>1?dates[0]+'—'+dates.at(-1):dates[0] || '日期未提供';
    return `<details class="dashboard-sources"><summary><span>来源 · ${sources.map(source=>escape(source.label)).join(' + ')}</span><span>${escape(dateRange)}${sampleScopes.length?' · '+escape(sampleScopes.join('；')):''} · 查看口径</span></summary><ul>${sources.map(source=>{const url=safeURL(source.url),precision=source.metric_precision==='source_display_rounded'?'在读沿用来源显示精度':/^[a-z0-9_:-]+$/i.test(source.metric_precision || '')?'沿用来源指标精度':source.metric_precision;return `<li>${url?`<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">${escape(source.label)} ↗</a>`:`<strong>${escape(source.label)}</strong>`} · ${escape(source.scope || '')} · ${statNumber(source.snapshot_count)} 个分类 · ${escape(source.sample_scope || '')}${precision?` · ${escape(precision)}`:''}</li>`;}).join('')}</ul></details>`;
  }
  function growthTable(data){
    const growth=data.growth,items=(growth?.items || []).slice(0,10);
    if(!growth?.available || !items.length)return '';
    return `<section class="data-card radar-card growth-card"><header class="data-card-head"><div><h2>在读变化排行</h2><p>按同一来源与实际日期比较 · 来源快照可能与当前汇总范围不同</p></div><span>${statNumber(growth.compared_count)} 本可比较</span></header><div class="radar-table-wrap" tabindex="0" role="region" aria-label="在读变化数值表"><table class="radar-table"><thead><tr><th>作品 / 数据来源</th><th>来源快照在读</th><th>在读增量</th><th>变化率</th><th>比较日期</th></tr></thead><tbody>${items.map((item,index)=>{const sid=item.snapshot_id || item.selection?.snapshot_id,bid=item.book_id || item.selection?.book_id;return `<tr><td><button class="radar-book" data-action="overview-book" data-snapshot="${escape(sid)}" data-book="${escape(bid)}"><span class="radar-row-number">${String(index+1).padStart(2,'0')}</span>${cover({title:item.title,cover_url:item.cover_url})}<span><strong>${escape(item.title)}</strong><small>${escape(item.source_name || '')} · ${escape(item.category || '')}${Number.isFinite(item.rank)?' · 原榜 #'+item.rank:' · 原榜名次未提供'}</small></span></button></td><td>${compactNumber(item.primary_count ?? item.read_count)}</td><td class="${item.delta>0?'positive':''}">${signedNumber(item.delta)}</td><td>${Number.isFinite(item.delta_rate)?signedNumber(item.delta_rate)+'%':'—'}</td><td>${escape(String(item.baseline_data_date || '').slice(0,10))} → ${escape(String(item.data_date || '').slice(0,10))}<small>实际间隔 ${statNumber(item.interval_days)} 天</small></td></tr>`;}).join('')}</tbody></table></div><div class="chart-caption">展示增量前 ${items.length} 本 · 基期在读为 0 时不计算变化率</div></section>`;
  }
  function radarTable(data,panel){
    const radar=data.blackhorse || {},fanqie=overviewPlatform==='fanqie',items=radar.items || [],criteria=radar.criteria || [];
    const title=fanqie?'黑马候选雷达':'榜单头部样本';
    let body=`<section class="data-card radar-card"><header class="data-card-head"><div><h2>${title}</h2><p>${fanqie?'以字数窗口与在读筛选，按每万字在读排序':'按当前原榜名次排列'}</p></div><span>${statNumber(fanqie?radar.eligible_count:items.length)} 本${fanqie?'命中':''}</span></header>${fanqie?`<div class="radar-rule-line">${criteria.map(item=>`<span>${escape(item)}</span>`).join('')}<span class="radar-formula">E = 在读 ÷ 万字数</span></div>`:''}`;
    if(!items.length)body+=`<div class="chart-unavailable compact">${fanqie?'当前样本没有命中筛选条件的作品':'当前榜单尚无有效作品'}<small>${fanqie?'继续更新分类后自动补充；不添加示例书填充':''}</small></div>`;
    else body+=`<div class="radar-table-wrap" tabindex="0" role="region" aria-label="${title}数值表"><table class="radar-table"><thead><tr><th>作品 / 原榜</th><th>${escape(panel.metric_label)}</th><th>字数</th>${fanqie?'<th>每万字在读</th><th>在读增量</th><th>加权指数</th>':''}</tr></thead><tbody>${items.slice(0,10).map((item,index)=>{const sid=item.snapshot_id || item.selection?.snapshot_id,bid=item.book_id || item.selection?.book_id;return `<tr><td><button class="radar-book" data-action="overview-book" data-snapshot="${escape(sid)}" data-book="${escape(bid)}"><span class="radar-row-number">${String(index+1).padStart(2,'0')}</span>${cover({title:item.title,cover_url:item.cover_url})}<span><strong>${escape(item.title)}</strong><small>${escape(item.category || item.board)}${Number.isFinite(item.rank)?' · 原榜 #'+item.rank:' · 原榜名次未提供'}</small></span></button></td><td>${compactNumber(item.primary_count ?? item.read_count)}</td><td>${compactNumber(item.word_count)}</td>${fanqie?`<td class="radar-efficiency">${compactNumber(item.efficiency ?? item.reads_per_10k_words)}</td><td class="${Number.isFinite(item.delta)&&item.delta>0?'positive':''}">${Number.isFinite(item.delta)?signedNumber(item.delta):'—'}${Number.isFinite(item.interval_days)?`<small>间隔 ${statNumber(item.interval_days)} 天</small>`:''}</td><td>${Number.isFinite(item.score)?statNumber(item.score):'—'}</td>`:''}</tr>`;}).join('')}</tbody></table></div>`;
    if(fanqie)body+=`<details class="radar-method"><summary>黑马计算口径 <span>${statNumber(radar.scored_count)} 本有历史增量</span></summary><p>${escape(radar.formula || '')}</p><p>加权指数仅在同一来源、同榜、同作品有可比较历史时计算；空值表示缺少依据，筛选结果不代表收益预测。</p></details>`;
    return body+'</section>';
  }
  function renderOverview(){
    const panels=overview?.platforms || [],panel=panels.find(item=>item.platform===overviewPlatform),job=overviewJob(),running=overviewRunning();
    const {data,channel,channels}=dashboardView(panel),metric=panel?.metric_label || (overviewPlatform==='fanqie'?'在读':'月票'),coverage=panel?.coverage || {};
    let html=`<div class="page-head overview-head data-overview-head"><div><div class="eyebrow">发糕情报室 <span class="dot">/</span> 数据总览</div><h1>榜单数据总览</h1></div><div class="head-actions"><button class="button secondary" data-action="overview-refresh" ${running || overviewRefreshing?'disabled':''}>${running || overviewRefreshing?'<span class="spinner"></span>更新中':'↻ 更新数据'}</button></div></div>`;
    html+=`<div class="overview-collection-status" role="status"><span class="status-dot ${overviewError?'':'online'}"></span><span>${escape(running?(job.public_source?.status==='running' && !['queued','running','pending','cancelling'].includes(job.status)?'正在补充公开榜单快照，已有数据可继续浏览':job.message || '正在更新，已有数据可继续查看'):overviewError || (overview?'本机数据已载入 · 自动检查更新':'正在读取本机保存的榜单…'))}</span>${running?`<button data-action="cancel-job" data-id="${escape(job.id)}">暂停</button>`:`<small>${coverage.latest_collected_at?date(coverage.latest_collected_at):''}</small>`}</div>`;
    html+=`<nav class="overview-tabs data-platform-tabs" aria-label="总览平台">${[{id:'fanqie',name:'番茄小说',board:'男频 · 女频新书榜'},{id:'qidian',name:'起点中文网',board:'月票榜'},{id:'ciweimao',name:'刺猬猫',board:'月票榜'}].map(item=>`<button data-action="overview-platform" data-platform="${item.id}" aria-pressed="${overviewPlatform===item.id}"><strong>${item.name}</strong><span>${item.board}</span><small>${statNumber(panels.find(p=>p.platform===item.id)?.summary?.books || 0)} 本</small></button>`).join('')}</nav>`;
    html+=`<div class="overview-scope dashboard-scope"><span>${overviewPlatform==='fanqie'?'仅新书榜 · 阅读榜不参与汇总':'月票榜 · 使用本平台原始指标'}</span>${overviewPlatform==='fanqie'?`<div class="chart-switch channel-switch" aria-label="汇总频道">${channels.map(value=>`<button data-action="dashboard-channel" data-channel="${escape(value)}" aria-pressed="${channel===value}">${value==='全部'?'全部频道':escape(value)}</button>`).join('')}</div>`:''}</div>`;
    if(!panel?.summary?.books || !data){
      html+=`<section class="overview-empty"><h2>${running?'正在整理第一批数据':'等待可用的榜单数据'}</h2><p>${overviewPlatform==='fanqie'?'自动汇总男频、女频新书榜各分类。':'自动读取本平台月票榜。'}取得数据后即显示统计。</p><div class="overview-empty-sections"><span>分类对比</span><span>字数分布</span><span>黑马雷达</span></div>${overviewError?'<button class="button secondary" data-action="overview-retry">重新读取</button>':''}</section>`;$('#content').innerHTML=html;return;
    }
    const kpi=data.kpis || {},fanqie=overviewPlatform==='fanqie';
    html+=dashboardSources(data);
    html+=`<div class="dashboard-kpis" aria-label="${escape(channel)}统计指标"><div data-kpi="books"><span>已汇总作品</span><strong>${statNumber(kpi.books)}<small>本</small></strong><p>${escape(channel)} · 按作品 ID 去重</p></div><div data-kpi="primary_median"><span>${escape(metric)}中位数</span><strong>${compactNumber(kpi.primary_median)}<small>${fanqie?'人':'票'}</small></strong><p>P90 ${compactNumber(kpi.primary_p90)} · 有效 ${statNumber(kpi.valid_primary_count)} 本</p></div><div data-kpi="words_median"><span>字数中位数</span><strong>${compactNumber(kpi.words_median)}<small>字</small></strong><p>有效 ${statNumber(kpi.valid_words_count)} 本</p></div><div data-kpi="blackhorse_count"><span>${fanqie?'黑马候选':'覆盖榜单'}</span><strong>${fanqie?statNumber(kpi.blackhorse_count):statNumber(coverage.category_count)}<small>${fanqie?'本':'个'}</small></strong><p>${fanqie?'8–15 万字 · 在读 ≥ 1 万':panel.scope_label}</p></div></div>`;
    html+=`<div class="dashboard-chart-grid">${chartCard('赛道对比','TOP 8',categoryChart(data,metric),'category-card')}${data.scatter?.total?chartCard('字数 × '+metric,fanqie?'每个点代表一本新书':'每个点代表一本作品',scatterChart(data,metric),'scatter-card'):chartCard('书名长度分布','字符',distributionChart(data.title_length_bins,'书名长度',kpi.valid_title_count),'distribution-card')}${kpi.valid_words_count?chartCard('字数分布','本',distributionChart(data.word_bins,'字数',kpi.valid_words_count),'distribution-card'):chartCard('作品状态分布','来源状态',distributionChart(data.status,'作品状态',kpi.books),'distribution-card')}${kpi.valid_primary_count?chartCard(metric+'分布','本',distributionChart(data.primary_bins,metric,kpi.valid_primary_count),'distribution-card'):chartCard('书名关键词分布',metric+'未提供',compactBars(data.keywords,'书名元素'),'small-data-card')}</div>`;
    html+=radarTable(data,panel);
    if(fanqie)html+=growthTable(data);
    html+=`<div class="dashboard-secondary-grid">${chartCard('书名结构分布','可重叠命中',compactBars(data.patterns,'书名结构'),'small-data-card')}${chartCard('高频书名元素','命中作品数',compactBars(data.keywords,'书名元素'),'small-data-card')}${chartCard('作品状态','来源状态',compactBars(data.status,'作品状态'),'small-data-card')}</div>`;
    html+=`<details class="overview-coverage"><summary>数据覆盖与统计口径 <span>${statNumber(coverage.category_count)}${Number.isFinite(coverage.expected_category_count)?' / '+coverage.expected_category_count:''} 个分类 · ${coverage.complete?'当前范围已读取':'持续补充中'}</span></summary><p>来源日期：${escape((coverage.source_dates || []).join('、') || '原站未提供')}。统计仅覆盖已取得的作品，缺失指标不记为零。</p><ul>${[...new Set([...(data.methods || []),...(data.scatter?.sampled?[data.scatter.selection_method]:[]),...(panel.methods || [])])].map(method=>`<li>${escape(method)}</li>`).join('')}</ul></details>`;
    $('#content').innerHTML=html;
  }
  async function loadOverview(){
    if(overviewLoading)return;
    overviewLoading=true;const request=++overviewRequest;
    try{const data=await api('/api/overview');if(request!==overviewRequest)return;overview=data;overviewError='';}
    catch(error){overviewError=error.message;}
    finally{overviewLoading=false;if(section==='overview')render();}
  }
  async function maybeAutoOverview(){
    if(!state || document.hidden || section!=='overview' || overviewRefreshing || overviewRunning() || Date.now()<overviewNextAt)return;
    await refreshOverview(true);
  }
  async function refreshOverview(automatic=true){
    if(overviewRefreshing || overviewRunning())return;
    overviewRefreshing=true;overviewNextAt=Date.now()+60000;if(section==='overview')render();
    try{
      const result=await api('/api/overview/refresh',{automatic});
      overviewNextAt=Date.now()+(result.retry_after_seconds || (result.status==='cooldown'?300:900))*1000;
      if(result.job){overview ||= {platforms:[]};overview.update=result.job;state.jobs=[result.job,...state.jobs.filter(job=>job.id!==result.job.id)];}
      await loadOverview();
      if(!automatic && result.status==='fresh')toast('总览已是最新。');
    }catch(error){overviewError=error.message;overviewNextAt=Date.now()+300000;if(!automatic)notice(error.message);}
    finally{overviewRefreshing=false;if(section==='overview')render();}
  }
  async function pollOverview(){
    const job=overviewJob();if(!overviewJobActive(job))return;
    const result=await api(`/api/jobs/${encodeURIComponent(job.id)}`), next=result.job || result;
    if(overview)overview.update=next;
    state.jobs=state.jobs.map(item=>item.id===next.id?next:item);
    const key=JSON.stringify([next.id,next.status,next.progress,next.partial_snapshot_id,next.message,next.public_source?.status,next.public_source?.snapshot_count,next.public_source?.updated_at,next.public_source?.finished_at,next.updated_at]);
    if(key!==overviewPollKey){overviewPollKey=key;await loadOverview();}
    if(!overviewJobActive(next)){overviewNextAt=Date.now()+(['failed','cancelled','interrupted'].includes(next.status)?300000:AUTO_TTL);await refresh();}
    else if(section==='overview')render();
  }
  function continuationJob(){return !viewingHistory() && (state?.jobs || []).find(job=>!['text','overview'].includes(job.kind) && job.platform===platformID() && (!job.options || scopeKey(job.platform,job.options)===scopeKey()) && (job.waiting_for_more || job.paused_for_demand) && (!view().selected_id || !job.partial_snapshot_id || job.partial_snapshot_id===snapshot?.id));}
  let loadingMore=false;
  async function loadMoreBooks(){
    if(loadingMore)return;
    const active=currentJob();if(active && !active.waiting_for_more && Number(active.demand_count)>Number(active.partial_count) && listWindow.count>=orderedBooks().length)return;
    const books=orderedBooks(), job=continuationJob();
    listWindow.count+=Math.max(20,Number(view().page_size) || 20);
    if(listWindow.count>books.length && job){
      loadingMore=true;
      try{const result=await api('/api/jobs/more',{job_id:job.id,count:100});if(result.job)state.jobs=[result.job,...state.jobs.filter(item=>item.id!==result.job.id)];}
      finally{loadingMore=false;}
    }
    render();
  }
  function observeListEnd(){
    listObserver?.disconnect();listObserver=null;
    const sentinel=$('#list-end');if(!sentinel || typeof IntersectionObserver==='undefined')return;
    listObserver=new IntersectionObserver(entries=>{
      if(Date.now()-lastListScrollAt<2000 && entries.some(entry=>entry.isIntersecting) && $('#list-end')===sentinel){listObserver.disconnect();loadMoreBooks().catch(error=>notice(error.message));}
    },{rootMargin:'240px 0px'});listObserver.observe(sentinel);
  }
  function categoryNames(options){
    if(platformID()!=='fanqie')return [options.category || '全部'];
    const verifiedFemale=['古风世情','科幻末世','游戏体育','女频衍生','玄幻言情','种田','年代','现言脑洞','宫斗宅斗','悬疑脑洞','古言脑洞','快穿','青春甜宠','星光璀璨','女频悬疑','职场婚恋','豪门总裁','民国言情'];
    const source=snapshot?.platform==='fanqie' && snapshot.channel===options.channel && snapshot.categories?.length ? snapshot.categories : options.channel==='女频'?verifiedFemale:['玄幻脑洞'];
    return [...new Set([options.category,...source].filter(Boolean))];
  }
  function renderBoardControls(){
    const id=platformID(), options=optionsFor(), key=scopeKey(), supported=id!=='fanqie_short';
    const status=viewingHistory()?'查看历史记录 · 自动更新已暂停':currentJob()?(currentJob().waiting_for_more?'已载入首批 · 向下浏览继续读取':'后台自动更新中'):autoMessages.get(key) || (supported?'自动更新 · 每 15 分钟检查':'此来源待接入');
    const channels=id==='fanqie'?['女频','男频']:[defaults[id]?.channel || '全部'];
    const categories=categoryNames(options), frequent=(options.channel==='女频'?['古言脑洞','现言脑洞','豪门总裁','年代','种田','快穿']:[options.category]).filter(value=>categories.includes(value));
    return `<div class="board-controls"><label>频道<select id="board-channel" ${channels.length===1?'disabled':''}>${channels.map(value=>`<option ${value===options.channel?'selected':''}>${escape(value)}</option>`).join('')}</select></label><label>榜单<select id="board-kind">${boardNames(id).map(value=>`<option ${value===options.board?'selected':''}>${escape(value)}</option>`).join('')}</select></label><label>分类<select id="board-category" aria-label="榜单分类" ${categories.length===1?'disabled':''}>${categories.map(value=>`<option ${value===options.category?'selected':''}>${escape(value)}</option>`).join('')}</select></label><div class="auto-status">${currentJob() && !currentJob().waiting_for_more?'<span class="spinner"></span>':'<span class="status-dot online"></span>'}<span>${escape(status)}</span>${viewingHistory()?'<button data-action="resume-auto">恢复自动更新</button>':''}</div></div>${id==='fanqie' && frequent.length>1?`<div class="category-shortcuts" aria-label="常用分类"><span>常用</span>${frequent.map(value=>`<button data-action="category" data-category="${escape(value)}" aria-pressed="${value===options.category}">${escape(value)}</button>`).join('')}</div>`:''}`;
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
    return `<aside class="detail" aria-label="作品详情" data-book="${escape(b.id)}" data-snapshot="${escape(snapshot.id)}"><div class="detail-top"><span>已选中的作品</span><button class="favorite-button" data-action="favorite" data-book="${escape(b.id)}" aria-pressed="${favorite(b)}">${favorite(b)?'♥ 已收藏':'♡ 收藏'}</button></div>${cover(b,true)}<h2>${escape(b.title)}</h2><p class="author">${escape(b.author || '作者未提供')}${b.status?' · '+escape(b.status):''}</p><div class="positions"><span>${position?'列表第 '+position+' 本':'已被当前筛选隐藏'}</span>${snapshot.platform!=='heiyan' && Number.isFinite(b.rank)?`<span class="original">原榜第 ${b.rank} 名</span>`:''}</div><p class="summary">${escape(b.summary || '本次来源未提供简介，可在作品原页查看。')}</p>${metrics.length?`<div class="metrics">${metrics.map((m)=>`<span>${escape(m.name)} <strong>${escape(m.value)}</strong>${escape(m.unit || '')}</span>`).join('')}</div>`:''}<div class="detail-actions">${url?`<a class="source-link" href="${escape(url)}" target="_blank" rel="noopener noreferrer">打开作品原页 ↗</a>`:''}${snapshot.platform==='fanqie'?'<button class="button primary small" data-action="catalog">目录与正文</button>':''}<button class="button secondary small" data-action="import-text">导入正文</button></div><p class="text-status">已选对象已保存，可回到 WorkBuddy 继续分析。</p>${renderTextAssets(b)}${reports.map((r)=>`<button class="report-item" data-action="report" data-id="${escape(r.id)}">▤ ${escape(r.title || '拆书报告')}<br><small>${escape(r.scope || '')}</small></button>`).join('')}</aside>`;
  }
  function empty(title,text,actions='',symbol='▤',extra='') { return `<div class="empty ${extra}"><div class="empty-symbol" aria-hidden="true">${symbol}</div><h2>${escape(title)}</h2><p>${escape(text)}</p>${actions?`<div class="actions">${actions}</div>`:''}</div>`; }
  function heiyanBooklistCard(compact=false){
    return `<section class="heiyan-booklist${compact?' heiyan-booklist-compact':''}" aria-label="黑岩腾讯文档书单"><div class="booklist-icon" aria-hidden="true">▤</div><span class="overview-kicker">黑岩故事会 · 腾讯文档</span><h2>从书单里，找到下一篇灵感</h2><p>打开书单后，在腾讯文档页面完成登录即可查看。已在浏览器登录的账号可继续使用。</p><a class="button primary" id="heiyan-booklist" href="${HEIYAN_BOOKLIST}" target="_blank" rel="noopener noreferrer">打开黑岩书单 <span aria-hidden="true">↗</span></a><small>书单在新标签页打开 · 不需要额外安装浏览器</small>${compact?'<p class="booklist-saved-note">以下是此前保存在本机的书目，继续保留供你查阅。</p>':''}</section>`;
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
    $('#dialog').dataset.kind='';
    $('#dialog-body').innerHTML=`<div class="dialog-head"><div><h2>${escape(title)}</h2>${subtitle?`<p>${escape(subtitle)}</p>`:''}</div><button class="close-dialog" data-action="close-dialog" aria-label="关闭窗口">×</button></div><div class="dialog-content">${content}</div>`;
    if(!$('#dialog').open) $('#dialog').showModal();
  }
  function collectionOptions() {
    const id=platformID(), current=optionsFor(id);
    showDialog('采集设置',`<form id="options-form"><p>设置保存后自动更新。日常使用无需再次填写。</p><div class="form-grid"><div class="field"><label for="channel">频道</label><select id="channel" name="channel">${(id==='fanqie'?['女频','男频']:[defaults[id]?.channel || '全部']).map(x=>`<option ${current.channel===x?'selected':''}>${escape(x)}</option>`).join('')}</select></div><div class="field"><label for="category">分类</label><input id="category" name="category" value="${escape(current.category || '全部')}" required maxlength="40"></div><div class="field"><label for="board">榜单</label><select id="board" name="board">${(id==='fanqie'?['新书榜','阅读榜']:id==='qidian'?['月票榜','签约新书榜','公众新书榜','新人新书榜','畅销榜']:id==='ciweimao'?['月票榜']:id==='heiyan'?['书库']:['热门故事']).map(x=>`<option ${current.board===x?'selected':''}>${escape(x)}</option>`).join('')}</select></div><div class="field"><label for="limit">最大采集数量</label><input id="limit" name="limit" type="number" min="1" max="10000" value="${current.limit || 10000}" required></div></div><p class="help-inline">分类以平台真实名称为准。将持续翻页，最多读取 100 页；平台实际提供多少条，就保存多少条。</p><div class="form-actions"><button class="button secondary" type="button" data-action="close-dialog">取消</button><button class="button primary" type="submit">保存条件</button></div></form>`);
  }
  async function showLatest(id){
    setHistory(null);latestResults.delete(scopeKey());
    await updateView({snapshot_id:id,selected_id:null,page:1});
  }
  async function receiveSnapshot(id,platformId,options,jobId=null){
    while(pendingViewMutations>0)await saving.catch(()=>{});
    if(platformID()!==platformId || scopeKey(platformId,options)!==scopeKey() || section!=='browse')return;
    if(jobId)snapshotJobs.set(id,jobId);
    if(!viewingHistory() && view().selected_id && id!==snapshot?.id && jobId && snapshotJobs.get(snapshot?.id)===jobId){
      const previous=snapshot, selected=view().selected_id, generation=viewGeneration;
      const response=await api(`/api/snapshots/${encodeURIComponent(id)}`), next=response.snapshot || response;
      const sameVersion=['platform','mode','channel','category','board','period','data_date'].every(key=>String(previous[key] || '')===String(next[key] || ''));
      const prefix=(previous.books || []).length<=(next.books || []).length && (previous.books || []).every((book,index)=>book.id===next.books[index].id && book.rank===next.books[index].rank);
      if(generation===viewGeneration && snapshot?.id===previous.id && view().selected_id===selected && sameVersion && prefix && next.books.some(book=>book.id===selected)){
        latestResults.delete(scopeKey(platformId,options));await updateView({snapshot_id:id,selected_id:selected});return;
      }
    }
    if(viewingHistory() || view().selected_id){
      if(id!==snapshot?.id)latestResults.set(scopeKey(platformId,options),id);
      render();return;
    }
    if(id!==snapshot?.id)await updateView({snapshot_id:id,selected_id:null,page:1});
  }
  async function maybeAutoCollect(){
    if(!state || document.hidden || section!=='browse' || viewingHistory() || activeRequest)return;
    const id=platformID(), p=platform(), options=optionsFor(), key=scopeKey(id,options);
    if(id==='fanqie_short' || id==='heiyan')return;
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
    if(id==='heiyan')return;
    if(!automatic){$('#notice').hidden=true;setHistory(null);}
    activeRequest=true;autoMessages.set(key,'正在检查更新…');render();
    try {
      const result=await api('/api/collect',{platform:id,options,automatic,interactive:true});
      if(result.status==='fresh'){
        nextAutoAt.set(key,Date.now()+(result.retry_after_seconds || 900)*1000);
        autoMessages.set(key,'已是最新 · 每 15 分钟自动检查');
        await refresh();if(result.snapshot_id)await receiveSnapshot(result.snapshot_id,id,options);return;
      }
      if(result.status==='cooldown'){
        nextAutoAt.set(key,Date.now()+(result.retry_after_seconds || 300)*1000);
        autoMessages.set(key,'暂未取得更新，稍后自动重试');
        render();return;
      }
      nextAutoAt.set(key,Date.now()+AUTO_TTL);
      autoMessages.set(key,'后台更新中，已有内容可继续查看');
      await refresh();
      const job=state.jobs.find(item=>item.id===(result.job_id || result.job?.id));
      if(job?.status==='completed' && job.snapshot_id){
        await receiveSnapshot(job.snapshot_id,id,options,job.id);
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
    const matching=bestSnapshotForScope(id,options);
    await updateView({snapshot_id:matching?.id || null,selected_id:null,page:1,filters:{query:'',sort:'source',favorites:false}});
    await maybeAutoCollect();
  }
  function schedulePoll() {
    clearTimeout(pollTimer);
    pollTimer=setTimeout(async()=>{
      try {
        const jobs=state?.jobs || [], running=jobs.filter(j=>!['text','overview'].includes(j.kind) && ['queued','running','pending'].includes(j.status));
        if(running.length) {
          const results=await Promise.all(running.map(j=>api(`/api/jobs/${encodeURIComponent(j.id)}`)));
          let ended=false;
          for(let i=0;i<results.length;i++) {
            const job=results[i].job || results[i], idx=state.jobs.findIndex(j=>j.id===job.id);
            if(idx>=0)state.jobs[idx]=job;
            if(job.partial_snapshot_id && section==='browse'){await receiveSnapshot(job.partial_snapshot_id,job.platform,job.options || optionsFor(job.platform),job.id);}
            if(!['queued','running','pending'].includes(job.status)){
              ended=true;
              if(['failed','error','cancelled'].includes(job.status)){nextAutoAt.set(scopeKey(job.platform,job.options || optionsFor(job.platform)),Date.now()+5*60*1000);autoMessages.set(scopeKey(job.platform,job.options || optionsFor(job.platform)),'更新未完成，已有结果保留');if(!job.automatic)notice(job.message || '采集未完成。已有书目仍然保留。');}
              else if(!job.automatic)toast(job.message || '新的采集结果已保存。');
            }
          }
          if(ended) {
            const completed=results.map(r=>r.job || r).find(j=>j.status==='completed' && j.snapshot_id && j.platform===platformID() && (!j.options || scopeKey(j.platform,j.options)===scopeKey()) && section==='browse');
            await refresh();
            if(completed){const jobOptions=completed.options || optionsFor(completed.platform);await receiveSnapshot(completed.snapshot_id,completed.platform,jobOptions,completed.id);autoMessages.set(scopeKey(completed.platform,jobOptions),'已更新 · 每 15 分钟自动检查');}
          } else render();
        }
        await pollTextJobs();
        await pollOverview();
        await maybeAutoCollect();
        await maybeAutoOverview();
      } catch(error) { notice(error.message); }
      finally { schedulePoll(); }
    },2200);
  }
  async function openRecord(el) {
    const navigation=++navigationGeneration;
    const meta=state.snapshots.find(s=>s.id===el.dataset.snapshot);
    const snap=meta || await api(`/api/snapshots/${encodeURIComponent(el.dataset.snapshot)}`);
    if(navigation!==navigationGeneration)return;
    section='browse';setHistory(el.dataset.snapshot,snap.mode || (snap.platform==='heiyan'?'short':'long'));
    await updateView({mode:snap.mode || (snap.platform==='heiyan'?'short':'long'),platform:snap.platform,snapshot_id:el.dataset.snapshot,selected_id:el.dataset.book || null,page:1,filters:{query:'',sort:'source',favorites:false}});
  }
  async function report(id) { const r=await api(`/api/reports/${encodeURIComponent(id)}`); showDialog(r.title || '拆书报告',`<pre class="report-text">${escape(r.content || r.report?.content || '')}</pre>`,[r.scope,date(r.created_at)].filter(Boolean).join(' · ')); }
  const textJobActive=job=>job && ['pending','running','queued','cancelling'].includes(job.status);
  function textAssetsFor(bookId,platformId=snapshot?.platform){
    return (state?.texts || []).filter(item=>item.book_id===bookId && item.platform===platformId);
  }
  function textDownloadURL(id){return `/api/texts/${encodeURIComponent(id)}/download`;}
  function textIntegrity(asset){
    if(asset.acquisition!=='public_download')return {label:asset.complete?'用户标记为完整正文':'按用户导入范围保存',detail:''};
    const integrity=asset.integrity || {}, status=integrity.status;
    const label=status==='partial'?'公开片段':status!=='verified'?'完整性未确认':asset.complete?'已核实为作品完整正文':asset.coverage_complete?'本次请求范围已完整取得':'已保存实际可读部分';
    const details=[];
    if(Number.isInteger(integrity.actual_characters) && integrity.actual_characters>=0)details.push(integrity.actual_characters_basis==='saved_text_including_headings'?`已保存文本 ${integrity.actual_characters.toLocaleString('zh-CN')} 字符（不含空白，含章节标题）`:`实际正文 ${integrity.actual_characters.toLocaleString('zh-CN')} 字符（不含空白）`);
    if(Number.isInteger(integrity.declared_book_word_count) && integrity.declared_book_word_count>0)details.push(`作品标注 ${integrity.declared_book_word_count.toLocaleString('zh-CN')} 字`);
    if(integrity.reason)details.push(String(integrity.reason));
    return {label,detail:details.join('；')};
  }
  function renderTextAssets(book){
    const assets=textAssetsFor(book.id);
    if(!assets.length)return '';
    return `<section class="saved-text-assets" aria-label="已保存的正文"><h3>正文资料 <span>${assets.length} 份</span></h3>${assets.slice(0,3).map(asset=>{const integrity=textIntegrity(asset);return `<div class="saved-text-item"><div><strong>${escape(asset.scope || '已保存正文')}</strong><small>${escape(integrity.label)} · ${Number(asset.characters || 0).toLocaleString()} 字符 · ${escape(date(asset.created_at || asset.collected_at))}</small>${integrity.detail?`<small>${escape(integrity.detail)}</small>`:''}</div><div class="text-item-actions"><button data-action="preview-text" data-id="${escape(asset.id)}">查看</button><a href="${textDownloadURL(asset.id)}" download>TXT ↓</a></div></div>`;}).join('')}</section>`;
  }
  function captureTextRange(){
    if(!contentPanel || $('#dialog').dataset.kind!=='text')return;
    const form=$('#chapter-download-form');if(!form)return;
    contentPanel.range={mode:$('#chapter-range-mode')?.value || 'first',count:Number($('#chapter-count')?.value || 3),start:Number($('#chapter-start')?.value || 1),end:Number($('#chapter-end')?.value || 3)};
  }
  function catalogTotal(catalog){return Math.max(0,Number(catalog?.total ?? catalog?.catalog_chapters ?? catalog?.chapters?.length) || 0);}
  async function openCatalog(){
    while(pendingViewMutations>0)await saving.catch(()=>{});
    const book=snapshot?.books?.find(item=>item.id===view().selected_id);
    if(!book || snapshot.platform!=='fanqie')return;
    const panel={sequence:++contentPanelSequence,snapshot_id:snapshot.id,book_id:book.id,platform:'fanqie',title:book.title,loading:true,catalog:null,error:'',range:{mode:'first',count:3,start:1,end:3},visibleChapters:100,job:null,asset:null};
    panel.job=(state.jobs || []).find(job=>job.kind==='text' && job.snapshot_id===panel.snapshot_id && job.book_id===panel.book_id && textJobActive(job)) || null;
    panel.asset=textAssetsFor(book.id,'fanqie')[0] || null;
    contentPanel=panel;
    showDialog(panel.title,'','目录与正文');$('#dialog').dataset.kind='text';renderContentDialog();
    await loadCatalog(panel);
  }
  async function loadCatalog(panel=contentPanel){
    if(!panel)return;
    panel.loading=true;panel.error='';renderContentDialog();
    try{
      const catalog=await api('/api/catalog',{snapshot_id:panel.snapshot_id,book_id:panel.book_id});
      if(contentPanel?.sequence!==panel.sequence)return;
      panel.catalog=catalog;panel.visibleChapters=100;
    }catch(error){if(contentPanel?.sequence===panel.sequence)panel.error=error.message;}
    finally{if(contentPanel?.sequence===panel.sequence){panel.loading=false;renderContentDialog();}}
  }
  function renderContentDialog(){
    const panel=contentPanel;
    if(!panel || !$('#dialog').open || $('#dialog').dataset.kind!=='text')return;
    captureTextRange();
    const catalog=panel.catalog, chapters=Array.isArray(catalog?.chapters)?catalog.chapters:[], total=catalogTotal(catalog), range=panel.range;
    const running=panel.submitting || textJobActive(panel.job);
    const source=safeURL(catalog?.source_url || catalog?.source), asset=panel.asset;
    let body=`<div class="chapter-intro"><p>正文会保存在这本作品的资料中。取文完成后，可直接回 WorkBuddy 分析，也可以导出 TXT。</p>${total?`<div class="chapter-catalog-meta"><strong>目录共 ${total.toLocaleString()} 章</strong><span>读取于 ${escape(date(catalog.collected_at || catalog.created_at))}</span>${source?`<a href="${escape(source)}" target="_blank" rel="noopener noreferrer">目录来源 ↗</a>`:''}</div>`:''}</div>`;
    if(panel.loading)body+='<div class="chapter-loading" role="status"><span class="spinner"></span>正在读取作品目录…</div>';
    if(panel.error)body+=`<div class="chapter-error" role="alert">${escape(panel.error)}<button data-action="reload-catalog">重新读取目录</button></div>`;
    if(catalog?.warnings?.length)body+=`<div class="chapter-warning">${catalog.warnings.map(item=>`<p>${escape(readableWarning(item))}</p>`).join('')}</div>`;
    if(chapters.length){
      const max=Math.max(total,chapters.length), endLimit=Math.min(max,50000), countLimit=Math.min(max,5000);
      body+=`<form id="chapter-download-form"><div class="chapter-presets" aria-label="取文范围快捷选择">${[3,10,30].map(count=>`<button type="button" data-action="chapter-preset" data-count="${count}" aria-pressed="${range.mode==='first' && range.count===count}" ${running?'disabled':''}>前 ${count} 章</button>`).join('')}<button type="button" data-action="chapter-all" aria-pressed="${range.mode==='all'}" ${running?'disabled':''}>全部已发布章节</button></div><div class="chapter-range-fields"><label>范围<select id="chapter-range-mode" ${running?'disabled':''}><option value="first" ${range.mode==='first'?'selected':''}>前 N 章</option><option value="range" ${range.mode==='range'?'selected':''}>指定章节范围</option><option value="all" ${range.mode==='all'?'selected':''}>全部已发布章节</option></select></label><label ${range.mode!=='first'?'hidden':''}>前<input id="chapter-count" type="number" min="1" max="${countLimit}" value="${Math.min(countLimit,Math.max(1,range.count))}" ${running?'disabled':''}>章</label><label ${range.mode!=='range'?'hidden':''}>从第<input id="chapter-start" type="number" min="1" max="${endLimit}" value="${Math.min(endLimit,Math.max(1,range.start))}" ${running?'disabled':''}>章</label><label ${range.mode!=='range'?'hidden':''}>到第<input id="chapter-end" type="number" min="1" max="${endLimit}" value="${Math.min(endLimit,Math.max(1,range.end))}" ${running?'disabled':''}>章</label></div>${range.mode==='all'?'<p class="chapter-scope-note">将按当前目录读取已发布章节；连载作品不会标为“全书完结”。无法取得的章节会明确列出。</p>':''}<div class="chapter-list" aria-label="作品章节目录">${chapters.slice(0,panel.visibleChapters).map(chapter=>`<button type="button" class="chapter-row" data-action="choose-chapter" data-index="${Number(chapter.index)}" ${running?'disabled':''}><span>${Number(chapter.index)}</span><strong>${escape(chapter.title || '未提供章名')}</strong>${chapter.locked || chapter.is_locked || chapter.restricted || chapter.need_pay?'<small>可能需要平台权限</small>':''}</button>`).join('')}</div>${chapters.length>panel.visibleChapters?`<button class="chapter-more" type="button" data-action="more-chapters">展开后续目录（当前 ${panel.visibleChapters} / ${chapters.length}）</button>`:''}<div class="chapter-download-actions"><small>只保存实际取得的正文与来源信息</small><button class="button primary" type="submit" ${running?'disabled':''}>${running?'正在取得正文…':'取得正文并保存'}</button></div></form>`;
    }
    if(catalog && !chapters.length && !panel.loading)body+='<p class="insights-empty">平台暂未提供可读取的目录。可以稍后重试，或导入已有正文。</p>';
    if(panel.job){
      const job=panel.job, terminal=!textJobActive(job);
      body+=`<div class="text-job ${job.status==='failed'?'text-job-failed':''}" role="status">${terminal?'':'<span class="spinner"></span>'}<div><strong>${escape(job.title || panel.title)}</strong><p>${escape(job.message || (terminal?'本次取文已结束':'正在读取正文…'))}</p></div>${!terminal?`<button data-action="cancel-text-job" data-id="${escape(job.id)}" ${job.status==='cancelling'?'disabled':''}>${job.status==='cancelling'?'正在保存已取得内容…':'取消取文'}</button>`:''}</div>`;
    }
    if(asset){
      const missing=Array.isArray(asset.missing)?asset.missing:[], acquired=Array.isArray(asset.chapters)?asset.chapters.length:null;
      const integrity=textIntegrity(asset), chapterLabel=asset.integrity?.status==='partial'?'片段涉及':'实际取得';
      body+=`<section class="chapter-saved-result"><h3>正文已保存</h3><p class="chapter-saved-scope">${escape(asset.scope || '阅读范围见正文资料')}</p><div class="chapter-saved-meta"><span>${escape(integrity.label)}</span>${acquired!==null?`<span>${chapterLabel} ${acquired} 章</span>`:''}<span>${Number(asset.characters || 0).toLocaleString()} 字符</span>${missing.length?`<span>缺失 ${missing.length} 章</span>`:''}</div>${integrity.detail?`<p class="chapter-integrity" role="note">${escape(integrity.detail)}</p>`:''}${missing.length?`<details class="chapter-missing"><summary>查看未取得的章节</summary><ul>${missing.slice(0,100).map(item=>`<li>${Number.isFinite(item.index)?'第 '+item.index+' 章 · ':''}${escape(item.title || item.chapter || '')} ${escape(item.reason || '未取得正文')}</li>`).join('')}</ul>${missing.length>100?'<p>其余缺章记录已保存在正文资料中。</p>':''}</details>`:''}<div class="chapter-result-actions"><a class="button secondary" href="${textDownloadURL(asset.id)}" download>导出 TXT ↓</a><button class="button secondary" data-action="preview-text" data-id="${escape(asset.id)}">查看已保存正文</button></div><p class="chapter-workbuddy-note">回到 WorkBuddy 可以说：“分析刚才保存的《${escape(panel.title)}》正文。”</p></section>`;
    }
    const focused=document.activeElement?.id;
    $('#dialog-body').innerHTML=`<div class="dialog-head"><div><h2>${escape(panel.title)}</h2><p>目录与正文</p></div><button class="close-dialog" data-action="close-dialog" aria-label="关闭窗口">×</button></div><div class="dialog-content">${body}</div>`;
    if(focused && $('#'+focused))$('#'+focused).focus({preventScroll:true});
  }
  function syncContentPanel(){
    if(!contentPanel)return;
    const panel=contentPanel;
    if(panel.job){
      const job=state.jobs.find(item=>item.id===panel.job.id);
      if(job){panel.job=job;if(job.text_id)panel.asset=state.texts.find(item=>item.id===job.text_id) || job.text || panel.asset;}
    }
    renderContentDialog();
  }
  async function startTextDownload(){
    if(!contentPanel || contentPanel.submitting || textJobActive(contentPanel.job))return;
    captureTextRange();const panel=contentPanel, range={...panel.range}, total=catalogTotal(panel.catalog);
    const payload={snapshot_id:panel.snapshot_id,book_id:panel.book_id};
    if(range.mode==='all')payload.all=true;
    else {
      const start=range.mode==='range'?range.start:1, count=range.mode==='range'?range.end-range.start+1:range.count;
      if(!Number.isInteger(start) || !Number.isInteger(count) || start<1 || count<1 || count>5000 || (total && start+count-1>total)){
        panel.error='请输入目录内有效范围，结束章不能早于起始章。指定范围单次最多 5000 章；全部目录请使用“全部已发布章节”。';renderContentDialog();return;
      }
      payload.start_chapter=start;payload.chapters=count;
    }
    panel.error='';panel.submitting=true;renderContentDialog();
    try{
      const response=await api('/api/fetch-text',payload);
      const job=response.job || (response.job_id?await api(`/api/jobs/${encodeURIComponent(response.job_id)}`):null);
      if(contentPanel?.sequence===panel.sequence)panel.job=job;
      await refresh();toast(`《${panel.title}》的正文任务已开始，结果会保存在这本作品下。`);
    } catch(error){if(contentPanel?.sequence===panel.sequence)panel.error=error.message;}
    finally{if(contentPanel?.sequence===panel.sequence){panel.submitting=false;renderContentDialog();}}
  }
  async function pollTextJobs(){
    const running=(state?.jobs || []).filter(job=>job.kind==='text' && textJobActive(job));
    if(!running.length)return;
    let finished=false;
    for(const previous of running){
      const response=await api(`/api/jobs/${encodeURIComponent(previous.id)}`), job=response.job || response;
      const index=state.jobs.findIndex(item=>item.id===job.id);if(index>=0)state.jobs[index]=job;
      if(!textJobActive(job)){
        finished=true;
        if(!$('#dialog').open || $('#dialog').dataset.kind!=='text' || contentPanel?.job?.id!==job.id)toast(`${job.title || '作品'}：${job.message || (job.text_id?'正文已保存':'取文已结束')}`);
      }
    }
    if(finished)await refresh();else syncContentPanel();
  }
  async function previewText(id){
    const text=await api(`/api/texts/${encodeURIComponent(id)}`);
    const integrity=textIntegrity(text);
    showDialog(text.title || '已保存正文',`<div class="text-preview-actions"><span>${escape(text.scope || '阅读范围见正文资料')}</span><a class="button secondary small" href="${textDownloadURL(id)}" download>导出 TXT ↓</a></div><p class="chapter-integrity">${escape(integrity.label)}${integrity.detail?' · '+escape(integrity.detail):''}</p><pre class="report-text saved-text-preview">${escape(text.content || '')}</pre>`,'已保存正文 · 不会重新下载');
  }
  const statNumber=value=>Number.isFinite(value)?value.toLocaleString('zh-CN',{maximumFractionDigits:2}):'未提供';
  const signedNumber=value=>Number.isFinite(value)?(value>0?'+':'')+statNumber(value):'—';
  async function openInsights(){
    const sid=snapshot?.id;if(!sid)return;
    const subtitle=[nameOf(snapshot.platform),scopeOf(snapshot),snapshot.data_date || '来源日期未提供'].filter(Boolean).join(' · ');
    showDialog('榜单观察','<div class="chapter-loading" role="status"><span class="spinner"></span>正在整理这份榜单的统计…</div>',subtitle);
    $('#dialog').dataset.kind='insights';$('#dialog').dataset.snapshot=sid;
    try{
      const data=await api('/api/insights',{snapshot_id:sid});
      if(!$('#dialog').open || $('#dialog').dataset.kind!=='insights' || $('#dialog').dataset.snapshot!==sid)return;
      const sample=data.sample || {}, changes=data.changes || {};
      const bookLink=item=>`<button data-action="insight-book" data-snapshot="${escape(sid)}" data-book="${escape(item.book_id)}">${escape(item.title)}</button>`;
      const signals=(items,key)=>items?.length?items.map(item=>`<div class="insight-row"><div><strong>${escape(item[key])}</strong><small>${(item.examples || []).map(escape).join(' · ')}</small></div><span>${statNumber(item.count)} 本 <small>${statNumber(item.share)}%</small></span></div>`).join(''):'<p class="insights-empty">当前样本没有命中已定义的文字规则。</p>';
      let body=`<div class="insights-summary"><div class="insight-metric"><span>本次样本</span><strong>${statNumber(sample.books)}<small> 本</small></strong><small>可读书名 ${statNumber(sample.title_count ?? sample.books)} 本</small></div><div class="insight-metric"><span>有在读数据</span><strong>${statNumber(sample.read_count_books)}<small> 本</small></strong><small>仅统计来源已提供的指标</small></div><div class="insight-metric"><span>有字数数据</span><strong>${statNumber(sample.word_count_books)}<small> 本</small></strong><small>缺失值不计为零</small></div><div class="insight-metric"><span>平均书名长度</span><strong>${statNumber(sample.average_title_length)}<small> 字符</small></strong><small>仅统计可读书名</small></div></div><p class="insights-note">所有统计对应当前保存的榜单。关键词和句式可能重叠命中；点作品名可回到这份榜单选书。</p><div class="insights-grid"><section class="insights-section"><h3>书名关键词</h3><p>按预设词表统计含该词的书名数</p>${signals(data.keywords?.slice(0,12),'word')}</section><section class="insights-section"><h3>书名句式</h3><p>按明确文字规则计数</p>${signals(data.title_patterns,'pattern')}</section></div><section class="insights-section"><h3>每万字在读</h3><p>在读数 ÷（字数 ÷ 10000）。只作样本排序参考，不代表收益或转化率。</p>`;
      if(data.efficiency?.length)body+=`<div class="insights-table" tabindex="0" role="region" aria-label="每万字在读统计表"><table><thead><tr><th>作品</th><th>在读</th><th>字数</th><th>每万字在读</th></tr></thead><tbody>${data.efficiency.slice(0,20).map(item=>`<tr><td>${bookLink(item)}</td><td>${statNumber(item.read_count)}</td><td>${statNumber(item.word_count)}</td><td>${statNumber(item.reads_per_10k_words)}</td></tr>`).join('')}</tbody></table></div>`;else body+='<p class="insights-empty">当前榜单没有同时提供在读与有效字数的作品。</p>';
      body+='</section><section class="insights-section"><h3>与前次记录对照</h3>';
      if(changes.available){
        body+=`<p>${escape(changes.baseline_data_date)} → ${escape(changes.current_data_date || data.data_date)} · 实际间隔 ${statNumber(changes.interval_days)} 天 · 对齐 ${statNumber(changes.matched_books)} 本作品</p><p class="insights-note">其中 ${statNumber(changes.read_comparable_books)} 本可比较在读，${statNumber(changes.rank_comparable_books)} 本可比较名次。名次变化为正表示前进。</p><div class="insights-table" tabindex="0" role="region" aria-label="历史记录变化统计表"><table><thead><tr><th>作品</th><th>名次变化</th><th>在读增量</th><th>在读变化</th></tr></thead><tbody>${(changes.items || []).map(item=>`<tr><td>${bookLink(item)}</td><td>${signedNumber(item.rank_delta)}</td><td>${signedNumber(item.read_delta)}</td><td>${Number.isFinite(item.read_delta_rate)?signedNumber(item.read_delta_rate)+'%':'—'}</td></tr>`).join('')}</tbody></table></div>`;
      }else body+=`<p class="insights-empty">${escape(changes.reason || '暂无可比较的历史记录，持续保存后可查看真实变化。')}</p>`;
      body+=`</section><details class="insights-methods"><summary>统计口径与数据说明</summary><ul>${(data.methods || []).map(method=>`<li>${escape(method)}</li>`).join('')}</ul></details>`;
      showDialog('榜单观察',body,subtitle);$('#dialog').dataset.kind='insights';$('#dialog').dataset.snapshot=sid;
    }catch(error){if($('#dialog').open && $('#dialog').dataset.kind==='insights' && $('#dialog').dataset.snapshot===sid){showDialog('榜单观察',`<p role="alert">${escape(error.message)}</p>`,subtitle);$('#dialog').dataset.kind='insights';}}
  }
  function textImport() {
    const b=snapshot?.books?.find(x=>x.id===view().selected_id); if(!b)return;
    pendingText={snapshot_id:snapshot.id,book_id:b.id,title:b.title,text:''};
    showDialog('导入作品正文',`<form id="text-import-form"><p>正文将关联到《${escape(b.title)}》。保留实际阅读范围，方便 WorkBuddy 准确分析。</p><button type="button" class="button secondary" data-action="choose-text">选择 TXT / Markdown 文件</button><div class="file-label" id="text-file-label">尚未选择文件</div><div class="field" style="margin-top:18px"><label for="text-scope">这份文本包含哪些内容</label><input id="text-scope" name="scope" placeholder="例如：第 1—3 章，或完整短篇正文" required maxlength="200"></div><label class="checkbox-label"><input type="checkbox" id="text-complete">我确认这份文件包含作品完整正文</label><div class="form-actions"><button type="button" class="button secondary" data-action="close-dialog">取消</button><button type="submit" class="button primary">保存正文</button></div></form>`);
  }
  async function syncNavigationState(){
    const request=++navigationGeneration;
    while(pendingViewMutations>0)await saving.catch(()=>{});
    const generation=viewGeneration, next=await api('/api/state');
    if(request!==navigationGeneration || generation!==viewGeneration)return false;
    acceptState(next);return true;
  }
  async function dispatch(el) {
    const a=el.dataset.action;
    if(a==='dismiss-notice'){$('#notice').hidden=true;return;}
    if(a==='close-dialog'){$('#dialog').close();return;}
    if(a==='mode') {
      if(!await syncNavigationState())return;section='browse';const target=el.dataset.mode, previous=state.views[target] || {}, id=previous.platform || (target==='short'?'heiyan':'fanqie');
      const matching=bestSnapshotForScope(id,{...defaults[id],...savedOptions[id]});
      await updateView(previous.snapshot_id?{mode:target}:{mode:target,platform:id,snapshot_id:matching?.id || null,selected_id:null});await maybeAutoCollect();return;
    }
    if(a==='platform') {
      if(!await syncNavigationState())return;
      section='browse';const id=el.dataset.platform, existing=bestSnapshotForScope(id,optionsFor(id));setHistory(null);
      if(id===platformID() && view().snapshot_id){await loadSnapshot();await maybeAutoCollect();return;}
      await updateView({platform:id,snapshot_id:existing?.id || null,selected_id:null,page:1,filters:{query:'',sort:'source',favorites:false}});await maybeAutoCollect();return;
    }
    if(a==='section'){navigationGeneration++;section=el.dataset.section;render();if(section==='overview'){await loadOverview();await maybeAutoOverview();}return;}
    if(a==='overview-platform'){navigationGeneration++;overviewPlatform=el.dataset.platform;render();return;}
    if(a==='dashboard-channel'){overviewChannel=el.dataset.channel;render();return;}
    if(a==='dashboard-category-metric'){dashboardCategoryMetric=el.dataset.metric;render();return;}
    if(a==='overview-refresh'){await refreshOverview(false);return;}
    if(a==='overview-book'){await openRecord(el);return;}
    if(a==='overview-retry'){await loadOverview();await refreshOverview(false);return;}
    if(a==='load-more'){await loadMoreBooks();return;}
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
    if(a==='import-snapshot'){$('#snapshot-file').click();return;}
    if(a==='import-text'){textImport();return;}
    if(a==='catalog'){await openCatalog();return;}
    if(a==='insights'){await openInsights();return;}
    if(a==='insight-book'){$('#dialog').close();await openRecord(el);return;}
    if(a==='reload-catalog'){await loadCatalog();return;}
    if(a==='preview-text'){await previewText(el.dataset.id);return;}
    if(a==='chapter-preset'){
      if(!contentPanel)return;captureTextRange();contentPanel.range={mode:'first',count:Math.min(Number(el.dataset.count),catalogTotal(contentPanel.catalog)),start:1,end:3};
      const form=$('#chapter-download-form');if(form)form.remove();renderContentDialog();return;
    }
    if(a==='chapter-all'){
      if(!contentPanel)return;captureTextRange();contentPanel.range.mode='all';const form=$('#chapter-download-form');if(form)form.remove();renderContentDialog();return;
    }
    if(a==='choose-chapter'){
      if(!contentPanel)return;const index=Number(el.dataset.index);contentPanel.range={mode:'range',start:index,end:index,count:1};const form=$('#chapter-download-form');if(form)form.remove();renderContentDialog();return;
    }
    if(a==='more-chapters'){if(contentPanel){contentPanel.visibleChapters+=100;renderContentDialog();}return;}
    if(a==='cancel-text-job'){await api('/api/jobs/cancel',{job_id:el.dataset.id});await refresh();return;}
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
    if(el.id==='chapter-range-mode'){captureTextRange();renderContentDialog();return;}
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
    if(event.target.id==='chapter-download-form'){event.preventDefault();startTextDownload().catch(error=>notice(error.message));return;}
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
  window.addEventListener('scroll',()=>{lastListScrollAt=Date.now();const sentinel=$('#list-end');if(sentinel && sentinel.getBoundingClientRect().top<window.innerHeight+240)loadMoreBooks().catch(error=>notice(error.message));},{passive:true});
  document.addEventListener('keydown',event=>{if((event.key==='Enter' || event.key===' ') && event.target.matches?.('.scatter-dot')){event.preventDefault();dispatch(event.target).catch(error=>notice(error.message));}});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden && state)refresh().catch(e=>notice(e.message));});
  refresh().catch(error=>{$('#server-label').textContent='本地服务未连接';$('#server-dot').classList.remove('online');$('#content').innerHTML=pageHead('欢迎回来','打开本地工作台后，就可以继续上次的选书。')+'<section class="surface">'+empty('暂时连接不上本地服务','请通过“发糕扫榜”启动入口重新打开，或让 WorkBuddy 帮你启动工作台。','<button class="button primary" data-action="retry">重新连接</button>','↻')+'</section>';notice(error.message);});
})();
