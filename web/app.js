'use strict';
(() => {
  const $ = (selector) => document.querySelector(selector);
  const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const safeURL = (value) => { try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : ''; } catch { return ''; } };
  const modes = {long:'长篇',short:'短篇',overseas:'海外网文'};
  const firstPlatform = {long:'fanqie',short:'heiyan',overseas:'goodnovel'};
  let domesticMode='long';
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
  let searchTimer, activeRequest = false, savedOptions = {}, pendingSnapshotImport = null;
  const AUTO_TTL = 15 * 60 * 1000;
  const nextAutoAt = new Map(), autoMessages = new Map(), latestResults = new Map(), snapshotJobs=new Map();
  let historyViews = {};
  const MEMBER_SITE='https://www.fagaochuangzuo.com/';
  const WORKBENCH_SITE=MEMBER_SITE+'?module=rankings';
  const WORKBENCH_MASCOT=MEMBER_SITE+'growth/redemption/fagao-check.png';
  let membership={allowed:false,reason:'checking',account:null}, accountEpoch=0, membershipRequest=0;
  let membershipTimer=null, membershipDeadlineTimer=null, membershipBusy=false, logoutPending=false, storageAccount=null;
  let foregroundMembershipBusy=false, foregroundMembershipAt=-Infinity, foregroundMembershipGeneration=0;
  let legacy={checked:false,checking:false,decision_required:false,counts:{},error:''},legacyBusy=false,legacyConfirmed=false,legacyRequest=0;
  let callbackNotice='';
  const readable=()=>membership.allowed || membership.can_read_history===true;
  const legacyBlocked=()=>membership.allowed && (!legacy.checked || legacy.decision_required || !!legacy.error);
  const readOnlyPosts=new Set(['/api/view','/api/insights','/api/resolve']);
  const writeActions=new Set(['collect','overview-refresh','overview-retry','collection-options','translation-context','favorite','import-snapshot','import-ranking-html','import-text','choose-text','catalog','reload-catalog','chapter-preset','chapter-all','choose-chapter','context']);
  const dataRequests=new Set(), downloadURLs=new Set();
  const membershipCopy={
    checking:['正在连接你的工作台','正在确认发糕账号与使用状态。'],
    not_authenticated:['连接发糕账号','使用发糕写作的现有账号，在官网确认后即可打开工作台。'],
    not_eligible:['和发糕娘一起开启专属扫榜','前往官网开启体验，发现热门作品，寻找创作灵感。'],
    expired:['这次扫榜体验结束啦','你的收藏和资料都还在，继续和发糕娘一起找灵感吧。'],
    suspended:['工作台暂时停用','当前账号的工作台权益已停用，请联系发糕写作客服。'],
    device_revoked:['这台电脑已退出授权','重新连接发糕账号，确认这台电脑后继续使用。'],
    device_limit:['已达到设备数量上限','请先在发糕写作移除不再使用的电脑，再重新连接。'],
    account_changed:['账号已在其他窗口更改','请重新确认当前账号，再继续打开工作台。'],
    verification_unavailable:['暂时无法确认使用状态','请检查网络后重试。已有榜单、正文和报告仍保存在本机。']
  };
  function storageKey(name){return storageAccount===null?null:`${name}:${encodeURIComponent(storageAccount)}`;}
  function savePreference(name,value){const key=storageKey(name);if(key)try{localStorage.setItem(key,value);}catch{}}
  function loadPreferences(accountID){
    storageAccount=accountID;historyViews={};savedOptions={};
    try{const value=JSON.parse(localStorage.getItem(storageKey('fagao-history-views')) || '{}');if(value && typeof value==='object' && !Array.isArray(value))historyViews=value;}catch{}
    try{
      const value=JSON.parse(localStorage.getItem(storageKey('fagao-collection-options')) || '{}');
      if(value && typeof value==='object' && !Array.isArray(value))savedOptions=value;
      if(localStorage.getItem(storageKey('fagao-options-version'))!=='3'){
        for(const value of Object.values(savedOptions)){if(!value || typeof value!=='object')continue;if(value.limit==null || value.limit===1000)value.limit=10000;value.max_pages ??= 100;}
        savePreference('fagao-options-version','3');savePreference('fagao-collection-options',JSON.stringify(savedOptions));
      }
    }catch{savedOptions={};}
  }
  function staleAccount(){const error=new Error('');error.code='stale_account';return error;}
  function assertAccount(epoch=accountEpoch,write=false){if(epoch!==accountEpoch || !readable() || (write && !membership.allowed))throw staleAccount();}
  function freezeAccountWrites(){
    accountEpoch++;viewGeneration++;refreshGeneration++;loadingVersion++;navigationGeneration++;overviewRequest++;contentPanelSequence++;
    for(const controller of dataRequests)controller.abort();dataRequests.clear();
    clearTimeout(pollTimer);clearTimeout(searchTimer);pollTimer=null;
    saving=Promise.resolve();pendingViewMutations=0;activeRequest=false;loadingMore=false;
    overviewLoading=false;overviewRefreshing=false;pendingText=null;pendingSnapshotImport=null;
    contentPanel=null;
    legacyRequest++;legacy={checked:false,checking:false,decision_required:false,counts:{},error:''};legacyBusy=false;legacyConfirmed=false;
    if($('#dialog').dataset.kind==='text' || $('#dialog').querySelector('form')){$('#dialog').close();$('#dialog-body').replaceChildren();}
  }
  function clearAccountView(){
    accountEpoch++;viewGeneration++;refreshGeneration++;loadingVersion++;navigationGeneration++;overviewRequest++;contentPanelSequence++;
    for(const controller of dataRequests)controller.abort();dataRequests.clear();
    for(const url of downloadURLs)URL.revokeObjectURL(url);downloadURLs.clear();
    clearTimeout(pollTimer);clearTimeout(searchTimer);clearTimeout(toastTimer);clearTimeout(membershipDeadlineTimer);pollTimer=null;
    listObserver?.disconnect();listObserver=null;
    state=null;snapshot=null;pendingText=null;pendingSnapshotImport=null;contentPanel=null;
    saving=Promise.resolve();pendingViewMutations=0;activeRequest=false;loadingMore=false;
    overview=null;overviewLoading=false;overviewError='';overviewRefreshing=false;overviewNextAt=0;overviewPollKey='';
    section='overview';domesticMode='long';overviewPlatform='fanqie';overviewChannel='全部';dashboardCategoryMetric='primary_median';
    listWindow={key:'',count:20};lastListScrollAt=0;
    nextAutoAt.clear();autoMessages.clear();latestResults.clear();snapshotJobs.clear();
    historyViews={};savedOptions={};storageAccount=null;
    legacyRequest++;legacy={checked:false,checking:false,decision_required:false,counts:{},error:''};legacyBusy=false;legacyConfirmed=false;
    $('#bridge').replaceChildren();$('#content').replaceChildren();$('#content').classList.remove('overseas-content');
    $('#dialog').close();$('#dialog-body').replaceChildren();$('#dialog').dataset.kind='';$('#dialog').dataset.snapshot='';
    $('#notice').hidden=true;$('#notice').replaceChildren();$('#toast').hidden=true;$('#toast').textContent='';
    $('#platforms').replaceChildren();$('#overseas-platforms').replaceChildren();$('#overseas-platforms').hidden=true;
    $('#favorite-count').textContent='0';$('#report-count').textContent='0';$('#snapshot-file').value='';$('#text-file').value='';
    $('#main-site').href=MEMBER_SITE;$('#points-entry').innerHTML='<button class="soon" disabled>积分站 <span>即将上线</span></button>';
  }
  function normalizedMembership(value){
    if(!value || typeof value!=='object' || typeof value.allowed!=='boolean')return {allowed:false,reason:'verification_unavailable',account:null};
    const reason=typeof value.reason==='string'?value.reason.replace(/^membership_/,''):'';
    const account=value.account && typeof value.account.id==='string' && value.account.id.length>0 && value.account.id.length<=256
      ? {id:value.account.id,display_name:typeof value.account.display_name==='string'?value.account.display_name.slice(0,160):'已连接账号'}:null;
    const basis=value.access_basis===undefined?'paid_membership':value.access_basis;
    const knownBasis=['paid_membership','experimental_membership','experimental_account','free_trial','word_pack'].includes(basis);
    const experienceBasis=basis==='free_trial' || basis==='word_pack';
    const hasAccess=Object.prototype.hasOwnProperty.call(value,'access_expires_at');
    const accessEnd=hasAccess?value.access_expires_at:value.membership_expires_at;
    const hasHistoryAccess=Object.prototype.hasOwnProperty.call(value,'history_access_expires_at');
    const historyEnd=hasHistoryAccess?value.history_access_expires_at:value.history_membership_expires_at;
    const result={allowed:false,can_read_history:false,access_mode:'none',access_basis:knownBasis?basis:null,reason:membershipCopy[reason]?reason:'verification_unavailable',account,generation:typeof value.generation==='string' || typeof value.generation==='number'?String(value.generation):'',membership_expires_at:value.membership_expires_at,access_expires_at:accessEnd,permit_expires_at:value.permit_expires_at,identity_expires_at:value.identity_expires_at,history_eligible:value.history_eligible===true,history_membership_expires_at:value.history_membership_expires_at,history_access_expires_at:historyEnd,server_time:value.server_time,policy_version:value.policy_version};
    const time=raw=>typeof raw==='string' && /(?:Z|[+-]\d{2}:\d{2})$/.test(raw)?Date.parse(raw):NaN;
    const validDates=['membership_expires_at','access_expires_at','history_membership_expires_at','history_access_expires_at','identity_expires_at','permit_expires_at'].every(key=>value[key]==null || Number.isFinite(time(value[key])));
    const memberBasis=basis==='paid_membership' || basis==='experimental_membership';
    const extendedMemberClock=memberBasis && hasAccess && accessEnd!=null && value.membership_expires_at!=null && time(accessEnd)>time(value.membership_expires_at);
    if(!validDates || extendedMemberClock){result.reason='verification_unavailable';return result;}
    if(value.allowed){
      const expires=time(value.membership_expires_at),access=time(accessEnd),permit=time(value.permit_expires_at),server=time(value.server_time);
      // A trial or purchase grants workbench time, never a synthetic membership.
      // Every basis still needs a short finite lease within its own deadline.
      const validMembership=basis==='experimental_account' || experienceBasis
        ? (value.membership_expires_at==null || Number.isFinite(expires))
        : (Number.isFinite(expires) && permit<=expires);
      const validAccess=basis==='experimental_account'
        ? (!hasAccess || accessEnd===null || (Number.isFinite(access) && permit<=access))
        : ((!experienceBasis || hasAccess) && Number.isFinite(access) && permit<=access);
      const identity=value.identity_expires_at==null?null:time(value.identity_expires_at);
      const validIdentity=identity===null || (Number.isFinite(identity) && identity>server && identity-server<=900000);
      if(account && result.generation && knownBasis && reason==='active' && [permit,server].every(Number.isFinite) && server<permit && permit-server<=900000 && validMembership && validAccess && validIdentity){result.allowed=true;result.access_mode='full';result.reason='active';}
      else result.reason='verification_unavailable';
    }else if(value.can_read_history===true && value.access_mode==='read_only' && reason==='expired'){
      const expires=time(accessEnd),server=time(value.server_time),identity=value.identity_expires_at==null?null:time(value.identity_expires_at);
      const validIdentity=identity===null?!experienceBasis:(Number.isFinite(identity) && identity>server && identity-server<=900000);
      if(account && result.generation && knownBasis && (!experienceBasis || hasAccess) && [expires,server].every(Number.isFinite) && expires<=server && validIdentity){result.can_read_history=true;result.access_mode='read_only';}
      else result.reason='verification_unavailable';
    }else if(value.can_read_history===true && value.access_mode==='read_only' && reason==='not_eligible'){
      const history=time(historyEnd),server=time(value.server_time),identity=time(value.identity_expires_at);
      if(account && result.generation && knownBasis && (!experienceBasis || hasHistoryAccess) && value.history_eligible===true && [history,server,identity].every(Number.isFinite) && history<=server && identity>server && identity-server<=900000){result.can_read_history=true;result.access_mode='read_only';}
      else result.reason='verification_unavailable';
    }
    return result;
  }
  function applyReadOnlyControls(){
    document.querySelectorAll('[data-action]').forEach(button=>{
      if(button.tagName!=='BUTTON' || !writeActions.has(button.dataset.action))return;
      if(membership.can_read_history){
        if(!button.hasAttribute('data-readonly-disabled'))button.dataset.readonlyDisabled=String(button.disabled);
        button.disabled=true;button.title='恢复扫榜使用权益后可继续操作';
      }else if(button.hasAttribute('data-readonly-disabled')){
        button.disabled=button.dataset.readonlyDisabled==='true';delete button.dataset.readonlyDisabled;button.removeAttribute('title');
      }
    });
  }
  function renderMembership(){
    const info=membershipCopy[membership.reason] || membershipCopy.verification_unavailable, account=membership.account;
    document.body.classList.toggle('membership-locked',!readable());document.body.classList.toggle('membership-read-only',membership.can_read_history===true);document.body.classList.toggle('legacy-pending',legacyBlocked());
    const sidebar=$('.sidebar');sidebar.inert=!readable() || legacyBlocked();sidebar.setAttribute('aria-disabled',String(sidebar.inert));
    const experience=membership.access_basis==='free_trial' || membership.access_basis==='word_pack';
    const expiry=account && membership.access_expires_at?new Date(membership.access_expires_at):null;
    const expiryLabel=expiry && Number.isFinite(expiry.getTime())?expiry.toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):'';
    const accountExperiment=membership.allowed && membership.access_basis==='experimental_account';
    const activeLabel=accountExperiment?'已连接':experience
      ? (membership.access_basis==='free_trial'?'体验中':'扫榜可用')+(expiryLabel?' · 使用至 '+expiryLabel:'')
      : '会员有效'+(expiryLabel?' · '+expiryLabel+' 到期':'');
    const historyOnlyLabel='使用已到期 · 历史资料只读';
    const mascot=`<img class="membership-mascot" src="${WORKBENCH_MASCOT}" alt="发糕娘" referrerpolicy="no-referrer" decoding="async">`;
    const renewActions=`<a class="button primary small" href="${WORKBENCH_SITE}&amp;purchase=membership" target="_blank" rel="noopener noreferrer">开通会员</a><a class="button secondary small" href="${WORKBENCH_SITE}&amp;purchase=word_pack" target="_blank" rel="noopener noreferrer">购买字数包，续用 15 天</a>`;
    $('#membership-account').innerHTML=account?`<div class="membership-account-text"><strong>${escape(account.display_name || '已连接账号')}</strong><small>${membership.allowed?escape(activeLabel):membership.can_read_history?historyOnlyLabel:escape(info[0])}</small></div><button class="quiet-button membership-check" data-action="membership-refresh" aria-label="重新确认使用状态" ${membershipBusy?'disabled':''}>↻</button><button class="quiet-button" data-action="membership-logout" ${membershipBusy?'disabled':''}>退出</button>`:'<span class="membership-unconnected">'+(membership.reason==='checking'?'正在确认账号':'尚未连接账号')+'</span>';
    $('#membership-notice').hidden=!membership.can_read_history;
    $('#membership-notice').innerHTML=membership.can_read_history?`${mascot}<div class="membership-renew-copy"><strong>这次扫榜体验结束啦</strong><span>历史资料可以继续查看，和发糕娘一起寻找新的灵感吧。</span><div class="membership-renew-actions">${renewActions}</div></div>`:'';
    applyReadOnlyControls();
    if(readable())return;
    const checking=membership.reason==='checking', reconnect=['not_authenticated','device_revoked','device_limit'].includes(membership.reason);
    const expired=membership.reason==='expired';
    const action=expired?renewActions:reconnect?`<button class="button primary" data-action="membership-connect" ${membershipBusy?'disabled':''}>${membershipBusy?'正在连接…':'连接发糕账号'}</button>`:`<a class="button primary" href="${WORKBENCH_SITE}" target="_blank" rel="noopener noreferrer">${membership.reason==='not_eligible'?'前往官网开始体验':'前往发糕写作'} ↗</a>`;
    $('#content').innerHTML=`<section id="membership-card" class="membership-card membership-welcome" aria-labelledby="membership-title">${checking?'<div class="membership-symbol" aria-hidden="true"><span class="spinner"></span></div>':mascot}<span class="membership-eyebrow">发糕扫榜工作台</span><h1 id="membership-title">${escape(info[0])}</h1><p>${escape(info[1])}</p>${account?`<div class="membership-current">${escape(account.display_name || '已连接账号')}</div>`:''}<div class="membership-actions">${checking?'':action}${checking?'':`<button class="button ${reconnect || expired?'quiet-button':'secondary'}" data-action="membership-refresh" ${membershipBusy?'disabled':''}>${membershipBusy?'正在确认…':'重新确认状态'}</button>`}</div><small>${reconnect?'将在官网完成登录，工作台不会索取你的密码。':'账号恢复权益后可继续使用，无需重新安装。'}</small></section>`;
  }
  function renderLegacy(){
    if(!membership.allowed || !legacyBlocked())return;
    $('#bridge').replaceChildren();$('#content').classList.remove('overseas-content');
    const account=membership.account?.display_name || '当前账号';
    if(legacy.error){
      $('#content').innerHTML=`<section id="legacy-claim-card" class="membership-card"><div class="membership-symbol" aria-hidden="true">▤</div><h1>旧版资料暂时无法确认</h1><p>${escape(legacy.error)}</p><p>旧资料与当前账号资料均保留。在确认完成前，工作台暂不自动采集。</p><div class="membership-actions"><button class="button primary" data-action="legacy-retry">重新检查</button></div></section>`;
      return;
    }
    if(!legacy.checked || legacy.checking){
      $('#content').innerHTML='<section id="legacy-claim-card" class="membership-card"><div class="membership-symbol" aria-hidden="true"><span class="spinner"></span></div><h1>正在检查本机旧资料</h1><p>先确认旧版资料的归属，再打开当前账号的工作台。</p></section>';
      return;
    }
    const labels={snapshots:'榜单',favorites:'收藏',texts:'正文',reports:'报告'};
    const counts=Object.entries(labels).filter(([key])=>Number.isSafeInteger(legacy.counts?.[key]) && legacy.counts[key]>0).map(([key,label])=>`<li><strong>${legacy.counts[key].toLocaleString()}</strong><span>${label}</span></li>`).join('');
    const canClaim=legacy.can_claim===true,inspectionMessage=typeof legacy.inspection_error?.message==='string'?legacy.inspection_error.message.slice(0,500):'旧资料暂时无法安全读取；可保留原资料并跳过，认领需先检查。';
    $('#content').innerHTML=`<section id="legacy-claim-card" class="membership-card legacy-claim-card"><div class="membership-symbol" aria-hidden="true">▤</div><span class="membership-eyebrow">旧版资料归属</span><h1>${canClaim?'这台电脑留有尚未关联账号的资料':'旧版资料暂时无法安全读取'}</h1><p>${canClaim?`请确认是否将这些旧资料复制到 <strong>${escape(account)}</strong>。资料不会因为首次登录而自动归入这个账号。`:escape(inspectionMessage)}</p>${counts?`<ul class="legacy-counts" aria-label="旧资料数量">${counts}</ul>`:''}<p class="legacy-preservation">${canClaim?'认领前会创建可恢复备份，原资料保留。选择跳过后，使用当前账号的独立资料。':'选择跳过后，旧资料仍留在原处，当前账号使用独立资料。'}</p><label class="legacy-confirm"><input id="legacy-confirm" type="checkbox" ${legacyConfirmed && canClaim?'checked':''} ${legacyBusy || !canClaim?'disabled':''}><span>我确认这些旧资料属于当前账号，并同意复制。</span></label><div class="membership-actions"><button class="button primary" data-action="legacy-claim" ${!canClaim || !legacyConfirmed || legacyBusy?'disabled':''}>${legacyBusy?'正在处理…':legacy.decision==='resume_required'?'继续认领':'认领并保留原件'}</button>${legacy.can_skip?`<button class="button secondary" data-action="legacy-skip" ${legacyBusy?'disabled':''}>跳过，开始使用</button>`:''}</div><small>${legacy.can_skip?'跳过不会删除原资料，也不会将其分配给其他账号。':'已有认领正在恢复，请完成当前认领后继续。'}</small></section>`;
  }
  async function ensureLegacy({force=false}={}){
    if(!membership.allowed)return true;
    if(legacyBusy && !force)return false;
    if(legacy.checked && !legacy.decision_required && !legacy.error && !force)return true;
    const epoch=accountEpoch,request=++legacyRequest;
    legacy={...legacy,checking:!legacy.checked,error:''};renderMembership();renderLegacy();
    try{
      const result=await api('/api/legacy-data/status');assertAccount(epoch,true);
      if(request!==legacyRequest)return false;
      if(!result || typeof result.available!=='boolean' || typeof result.decision_required!=='boolean'
          || typeof result.can_skip!=='boolean' || typeof result.can_claim!=='boolean' || result.original_preserved!==true
          || !result.counts || typeof result.counts!=='object' || Array.isArray(result.counts))throw new Error('旧资料检查结果不完整，请重新检查或让维护助手处理。');
      legacy={...result,checked:true,checking:false,error:''};
      renderMembership();if(legacyBlocked())renderLegacy();
      return !legacyBlocked();
    }catch(error){
      if(epoch!==accountEpoch || request!==legacyRequest)return false;
      legacy={...legacy,checked:false,checking:false,error:error.message || '旧资料检查未完成，请重试。'};renderMembership();renderLegacy();return false;
    }
  }
  async function decideLegacy(action){
    if(legacyBusy || !membership.allowed || !legacy.checked || !legacy.decision_required)return;
    if(action==='claim' && (!legacyConfirmed || legacy.can_claim!==true))return;
    if(action==='skip' && !legacy.can_skip)return;
    const epoch=accountEpoch;legacyBusy=true;renderLegacy();
    try{
      await api('/api/legacy-data/'+action,{confirmed:true});assertAccount(epoch,true);
      legacyConfirmed=false;legacy.checked=false;
      if(await ensureLegacy({force:true})){assertAccount(epoch,true);await refresh();assertAccount(epoch,true);notice(action==='claim'?'旧版资料已复制到当前账号，原件与认领备份均已保留。':'已保留旧版资料，当前账号使用独立的工作台资料。');}
    }catch(error){if(epoch===accountEpoch){legacy.error=error.message || '资料处理未完成，请重新检查后继续。';renderLegacy();}}
    finally{if(epoch===accountEpoch){legacyBusy=false;renderMembership();if(legacyBlocked())renderLegacy();}}
  }
  function applyMembership(value){
    const next=normalizedMembership(value), oldID=membership.account?.id, nextID=next.account?.id;
    const changed=oldID!==nextID || membership.generation!==next.generation;
    const wasReadable=readable(),nextReadable=next.allowed || next.can_read_history;
    const readOnlyChange=membership.can_read_history!==next.can_read_history;
    if(changed || (wasReadable && !nextReadable))clearAccountView();
    else if(membership.allowed && next.can_read_history)freezeAccountWrites();
    const unlocked=(nextReadable && (!wasReadable || changed)) || (next.allowed && !membership.allowed);
    membership=next;
    if(nextReadable && storageAccount!==nextID)loadPreferences(nextID);
    renderMembership();clearTimeout(membershipDeadlineTimer);
    if(readOnlyChange && state)render();
    const deadline=next.allowed?next.permit_expires_at:next.can_read_history?next.identity_expires_at:null;
    if(deadline){
      const remaining=Math.max(0,Date.parse(deadline)-Date.parse(next.server_time));
      const epoch=accountEpoch;
      membershipDeadlineTimer=setTimeout(()=>{
        if(epoch!==accountEpoch || !readable())return;
        const expired=next.allowed && next.access_basis!=='experimental_account' && Date.parse(next.access_expires_at)<=Date.parse(next.permit_expires_at);
        applyMembership({...membership,allowed:false,can_read_history:false,access_mode:'none',reason:expired?'expired':'verification_unavailable'});
        checkMembership({force:true}).catch(()=>{});
      },Math.min(remaining,2147483647));
    }
    return unlocked;
  }
  function scheduleMembership(){
    clearTimeout(membershipTimer);
    if(logoutPending)return;
    const lease=membership.allowed?membership.permit_expires_at:membership.can_read_history?membership.identity_expires_at:null;
    const remaining=lease?Date.parse(lease)-Date.parse(membership.server_time):Infinity;
    const renewSoon=Number.isFinite(remaining) && remaining<=45000;
    membershipTimer=setTimeout(()=>checkMembership({force:renewSoon}).catch(()=>{}),renewSoon?Math.max(1,Math.min(15000,remaining/2)):15000);
  }
  function refreshForegroundMembership({force=false}={}){
    if(document.hidden || logoutPending || foregroundMembershipBusy)return;
    const now=performance.now();
    // Returning from checkout must refresh an expired account's server grant,
    // rather than waiting for the still-valid history identity lease to expire.
    // Browsers may emit focus, visibilitychange and pageshow together.
    if(now-foregroundMembershipAt<3000)return;
    foregroundMembershipAt=now;foregroundMembershipBusy=true;
    const generation=++foregroundMembershipGeneration;
    checkMembership({force:force || !!membership.account && !membership.allowed,reload:true})
      .catch(()=>{}).finally(()=>{if(generation===foregroundMembershipGeneration)foregroundMembershipBusy=false;});
  }
  async function checkMembership({force=false,reload=false}={}){
    if(logoutPending && !force)return;
    if(force)logoutPending=false;
    const request=++membershipRequest;
    try{
      const value=await api(force?'/api/membership/refresh':'/api/membership/status',force?{}:undefined);
      if(request!==membershipRequest)return;
      const unlocked=applyMembership(value);
      $('#server-dot').classList.add('online');$('#server-label').textContent='本地工作台已连接';
      if(membership.allowed && !await ensureLegacy())return;
      if(request!==membershipRequest)return;
      if(readable() && (unlocked || reload || !state))await refresh();
    }catch(error){
      if(request!==membershipRequest || error.code==='stale_account')return;
      applyMembership({...membership,allowed:false,can_read_history:false,access_mode:'none',reason:'verification_unavailable'});
    }finally{if(request===membershipRequest){scheduleMembership();if(callbackNotice){notice(callbackNotice);callbackNotice='';}}}
  }
  async function connectMembership(){
    if(membershipBusy)return;
    membershipBusy=true;membershipRequest++;renderMembership();
    try{
      const result=await api('/api/membership/connect',{}, {binding:membership}), url=new URL(result.authorization_url);
      if(url.origin!==new URL(MEMBER_SITE).origin || url.username || url.password || url.pathname!=='/workbench/authorize')throw new Error('未取得有效的官网授权入口，请重试。');
      window.location.assign(url.href);
    }catch(error){notice(error.message || '暂时无法连接官网，请稍后重试。');}
    finally{membershipBusy=false;renderMembership();scheduleMembership();}
  }
  async function logoutMembership(){
    if(membershipBusy)return;
    const binding=membership;
    membershipBusy=true;logoutPending=true;membershipRequest++;clearTimeout(membershipTimer);
    clearAccountView();membership={allowed:false,reason:'not_authenticated',account:null};renderMembership();
    try{const result=await api('/api/membership/logout',{}, {binding});applyMembership(result);logoutPending=false;}
    catch(error){membership={allowed:false,reason:error.code==='membership_account_changed'?'account_changed':'verification_unavailable',account:null};notice('退出尚未完成，请重新确认当前账号状态。');}
    finally{membershipBusy=false;renderMembership();scheduleMembership();}
  }
  const mode = () => Object.hasOwn(modes,state?.active_mode) ? state.active_mode : 'long';
  const view = () => state?.views?.[mode()] || {};
  const platformID = () => view().platform || firstPlatform[mode()];
  const platforms = () => state?.platforms?.length ? state.platforms : fallbackPlatforms;
  const platform = () => platforms().find((p) => p.id === platformID()) || fallbackPlatforms.find((p) => p.id === platformID()) || fallbackPlatforms[0];
  const platformMode = (id) => platforms().find(p=>p.id===id)?.mode || 'long';
  const defaultsFor = (id) => ({limit:10000,max_pages:100,...defaults[id],...platforms().find(p=>p.id===id)?.default_options});
  const channelsFor = (id) => platforms().find(p=>p.id===id)?.channels || (id==='fanqie'?['女频','男频']:[defaultsFor(id).channel || '全部']);
  const nameOf = (id) => platforms().find((p) => p.id === id)?.name || fallbackPlatforms.find((p) => p.id === id)?.name || id;
  const favorite = (book, snap = snapshot) => (state?.favorites || []).some((f) => f.book_id === book.id && f.platform === snap?.platform);
  const date = (value) => { if (!value) return '未提供'; const d = new Date(value); return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}); };
  const snapshotDate = (snap) => snap?.collected_at || snap?.created_at;
  const currentJob = () => membership.allowed?(state?.jobs || []).find((j) => !['text','overview'].includes(j.kind) && j.platform === platformID() && (!j.options || scopeKey(j.platform,j.options)===scopeKey()) && ['queued','running','pending'].includes(j.status)):null;
  const latestCollectionJob = () => (state?.jobs || []).find(j=>!['text','overview'].includes(j.kind) && j.platform===platformID() && (!j.options || scopeKey(j.platform,j.options)===scopeKey()));
  const accessRestricted = job => ['failed','interrupted'].includes(job?.status) && ['forbidden','source_access_denied','verification_required'].includes(job.error_code);
  function sourceActions(p) {
    const official=safeURL(p.source_url), alternate=safeURL(p.alternate_source_url);
    return `${official?`<a class="button secondary small" href="${escape(official)}" target="_blank" rel="noopener noreferrer">打开官网榜单 ↗</a>`:''}${alternate?`<a class="button secondary small" href="${escape(alternate)}" target="_blank" rel="noopener noreferrer">打开公开备用源 ↗</a>`:''}${p.id==='scribblehub'?'<button class="button secondary small" data-action="import-ranking-html">导入已打开的榜单</button>':''}`;
  }
  function optionsFor(id=platformID()) {
    const targetMode=platformMode(id), stored=state?.snapshots?.find(item=>item.id===state.views?.[targetMode]?.snapshot_id && item.platform===id);
    const previous=stored ? {channel:stored.channel,category:stored.category,board:stored.board,...(stored.period?{period:stored.period}:{})} : {};
    const preferences=stored && historyViews[targetMode]===stored.id ? {} : savedOptions[id];
    return {...defaultsFor(id),...previous,...preferences,mode:targetMode};
  }
  function scopeKey(id=platformID(),options=optionsFor(id)) { return [id,options.mode || platformMode(id),options.channel,options.category,options.board,options.period || (id==='ciweimao'?'月榜':''),options.limit ?? '',options.max_pages ?? 100,options.detail_budget ?? ''].join('|'); }
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
    savePreference('fagao-history-views',JSON.stringify(historyViews));
  }
  function boardNames(id=platformID()) { return platforms().find(p=>p.id===id)?.boards || (id==='fanqie'?['新书榜','阅读榜']:id==='qidian'?['月票榜','签约新书榜','公众新书榜','新人新书榜','畅销榜']:id==='ciweimao'?['月票榜']:id==='heiyan'?['书库']:['热门故事']); }
  const stopReasons={loading:'当前作品已载入，后续页面会继续补充',waiting_for_more:'向下浏览时继续读取后续作品',paused_for_demand:'已暂停读取，继续向下浏览时恢复',last_page:'已读取平台最后一页',source_end:'已到来源末页',awaiting_demand:'向下浏览时继续读取后续作品',empty_page:'平台已无更多书目',requested_limit:'已达到本次采集数量',max_pages:'已达到本次页数上限',repeated_page:'平台返回重复页面，已停止避免重复',version_changed:'榜单版本发生变化，已保留本次结果',invalid_response:'平台返回的数据暂不可读',wrong_page:'平台返回的页码不一致',request_failed:'后续页面暂时无法连接',session_required:'后续页面需要平台登录',pagination_unverified:'后续翻页方式仍待核实',rank_conflict:'不同页面出现名次冲突',book_conflict:'不同页面出现作品信息冲突',invalid_next_page:'平台未提供可用的下一页'};
  function readableWarning(value){return String(value).replace(/[a-z]+(?:_[a-z]+)+/g,key=>stopReasons[key] || key);}
  function paginationSummary(item){
    const page=item?.pagination;
    if(page?.stop_reason==='imported_page')return `${Number.isInteger(page.source_page) && page.source_page>0?`已导入第 ${page.source_page} 页`:'已导入保存的榜单页'} · 本页 ${item.books?.length ?? item.books_count ?? 0} 本 · ${item.data_date?'来源日期：'+item.data_date:'来源更新时间未知'}`;
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
  function toast(message) { if(!message || !membership.allowed)return;clearTimeout(toastTimer); $('#toast').textContent = message; $('#toast').hidden = false; toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 4200); }
  function notice(message) { if(!message)return;$('#notice').innerHTML = `<span>${escape(message)}</span><button data-action="dismiss-notice" aria-label="关闭提示">×</button>`; $('#notice').hidden = false; }
  async function api(path, body, options={}) {
    const publicRequest=path.startsWith('/api/membership/'), epoch=accountEpoch, controller=new AbortController();
    const write=body!==undefined && !readOnlyPosts.has(path);
    if(!publicRequest){assertAccount(epoch,write);if(legacyBlocked() && !path.startsWith('/api/legacy-data/'))throw staleAccount();dataRequests.add(controller);}
    try{
      const binding=publicRequest?options.binding:membership;
      const headers=body===undefined?{}:{'Content-Type':'application/json','X-Fagao-Request':'1'};
      if(binding?.account?.id)headers['X-Fagao-Account']=binding.account.id;
      if(binding?.generation)headers['X-Fagao-Generation']=binding.generation;
      const init=body===undefined?{cache:'no-store',headers}:{method:'POST',headers,body:JSON.stringify(body)};
      const response=await fetch(path,{...init,signal:controller.signal});
      if(options.blob && response.ok){const result=await response.blob();assertAccount(epoch);return result;}
      let result;try{result=await response.json();}catch{if(!publicRequest)assertAccount(epoch);throw new Error('工作台返回了无法读取的数据，请重新启动后重试。');}
      if(!publicRequest)assertAccount(epoch,write);
      if(!response.ok || result?.error){
        const error=new Error(result?.error?.message || result?.message || `请求未完成（${response.status}）`);
        error.code=result?.error?.code || 'request_failed';error.status=response.status;
        if(!publicRequest && error.code.startsWith('membership_')){
          // A newer protected denial supersedes any status response in flight.
          membershipRequest++;
          applyMembership({...membership,allowed:false,can_read_history:false,access_mode:'none',reason:error.code.slice('membership_'.length)});scheduleMembership();
        }
        if(!publicRequest && error.code==='legacy_decision_required'){
          freezeAccountWrites();renderMembership();renderLegacy();queueMicrotask(()=>checkMembership({reload:true}).catch(()=>{}));
        }
        throw error;
      }
      return result;
    }catch(error){if(!publicRequest && (epoch!==accountEpoch || controller.signal.aborted))throw staleAccount();throw error;}
    finally{dataRequests.delete(controller);}
  }
  function acceptState(next) {
    if(!readable())return;
    if (next?.views) {
      const retained=(state?.jobs || []).filter(job=>(['queued','running','pending','cancelling'].includes(job.status) || job.paused_for_demand) && !(next.jobs || []).some(item=>item.id===job.id));
      state=next;state.jobs=[...(next.jobs || []),...retained];
    }
    if (!state) return;
    state.views ||= {}; state.snapshots ||= []; state.favorites ||= []; state.history ||= []; state.reports ||= []; state.jobs ||= []; state.texts ||= [];
  }
  async function refresh({silent = false} = {}) {
    const epoch=accountEpoch;assertAccount(epoch);
    if(legacyBlocked()){renderLegacy();return;}
    while(pendingViewMutations>0)await saving.catch(()=>{});
    assertAccount(epoch);
    const viewVersion=viewGeneration, requestVersion=++refreshGeneration;
    const next = await api('/api/state');
    if(pendingViewMutations>0 || viewVersion!==viewGeneration || requestVersion!==refreshGeneration)return;
    acceptState(next);
    $('#server-dot').classList.add('online'); $('#server-label').textContent = '本地工作台已连接';
    if (!silent) await loadSnapshot(); else render();
    assertAccount(epoch);
    syncContentPanel();
    if(section==='overview')await loadOverview();
    assertAccount(epoch);
    schedulePoll();
    queueMicrotask(()=>{maybeAutoCollect().catch(()=>{});maybeAutoOverview().catch(()=>{});});
  }
  async function loadSnapshot() {
    assertAccount();
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
    const epoch=accountEpoch;assertAccount(epoch);
    const targetMode = patch.mode || mode(), version=++viewGeneration;
    pendingViewMutations++;
    saving = saving.catch(() => {}).then(async () => {
      try{
        assertAccount(epoch);
        const next=await api('/api/view', {mode:targetMode,...patch});
        if(version!==viewGeneration)return;
        acceptState(next);await loadSnapshot();
      } finally {if(epoch===accountEpoch)pendingViewMutations--;}
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
    if(mode()!=='overseas')domesticMode=mode();
    document.querySelectorAll('[data-action="mode"]').forEach((el) => el.setAttribute('aria-pressed',String(el.dataset.mode === mode())));
    const platformButton=p=>`<button class="platform-button" data-action="platform" data-platform="${escape(p.id)}" aria-current="${section === 'browse' && platformID() === p.id}"><span class="platform-mark">${escape((p.name || '')[0])}</span><span class="platform-name">${escape(p.name)}${['planned','unavailable','pending'].includes(p.status) ? '<small>待接入 · 支持导入</small>' : ''}</span></button>`;
    $('#platforms').innerHTML = platforms().filter(p=>p.mode===domesticMode).map(platformButton).join('');
    const overseasActive=mode()==='overseas';
    $('#overseas-platforms').hidden=!overseasActive;
    $('#overseas-platforms').innerHTML=overseasActive ? platforms().filter(p=>p.mode==='overseas').map(platformButton).join('') : '';
    $('#overseas-navigation').setAttribute('aria-expanded',String(overseasActive));
    $('#overseas-navigation').setAttribute('aria-current',String(overseasActive && section==='browse'));
    $('#content').classList.toggle('overseas-content',overseasActive && section==='browse');
    document.querySelectorAll('[data-action="section"]').forEach((el) => el.setAttribute('aria-current',String(el.dataset.section === section)));
    $('#favorite-count').textContent = state.favorites.length; $('#report-count').textContent = state.reports.length;
    const mainURL = safeURL(state.config?.main_site) || 'https://www.fagaochuangzuo.com/'; $('#main-site').href = mainURL;
    const pointsURL = safeURL(state.config?.points_site);
    $('#points-entry').innerHTML = pointsURL ? `<a class="quiet-button" href="${escape(pointsURL)}" target="_blank" rel="noopener noreferrer">返回积分站 ↗</a>` : '<button class="soon" disabled>积分站 <span>即将上线</span></button>';
  }
  function scopeOf(snap = snapshot) { return [snap?.channel,snap?.category,snap?.board,snap?.period].filter(Boolean).join(' · '); }
  function render() {
    if(!readable()){renderMembership();return;}
    if(legacyBlocked()){renderLegacy();return;}
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
    applyReadOnlyControls();
    if(focusedQuery && $('#query')){
      const input=$('#query');input.value=focusedQuery.value;input.focus({preventScroll:true});
      try{input.setSelectionRange(focusedQuery.start,focusedQuery.end);}catch{}
    }
  }
  function pageHead(title, sub, actions='') { return `<div class="page-head"><div><div class="eyebrow">${section==='browse' ? (mode()==='overseas'?'海外网文':modes[mode()]+'作品') : '我的工作台'}<span class="dot">/</span> ${section==='browse' ? escape(nameOf(platformID())) : '保存在本机'}</div><h1>${escape(title)}</h1><p>${escape(sub)}</p></div>${actions ? `<div class="head-actions">${actions}</div>` : ''}</div>`; }
  function renderBrowse() {
    const p = platform(), job = viewingHistory() ? null : currentJob();
    const recentJob=latestCollectionJob(), failed=!viewingHistory() && !job && ['failed','interrupted'].includes(recentJob?.status), restricted=failed && accessRestricted(recentJob);
    const isLibrary = p.id === 'heiyan', unsupported = ['planned','unavailable'].includes(p.status) && p.id === 'fanqie_short';
    const options=optionsFor(), key=scopeKey();
    const title = isLibrary ? '黑岩故事会 · 选题书单' : `${nameOf(p.id)} · ${options.board || '作品发现'}`;
    const sub = membership.can_read_history ? '查看这台电脑上已保存的榜单与作品资料。' : isLibrary ? '直接打开腾讯文档书单，在原页面登录后查看。' : mode()==='overseas' ? '英文书名与简介 · 中文分类浏览 · 选中后可回 WorkBuddy 翻译或分析' : '持续更新平台榜单，发现值得阅读与研究的作品。';
    const action = isLibrary ? '' : `${snapshot && mode()!=='overseas'?'<button class="button secondary" data-action="insights">榜单观察</button>':''}<button class="button secondary" data-action="collection-options" aria-label="调整采集设置">采集设置</button><button class="button secondary" data-action="collect" ${job || activeRequest || unsupported ? 'disabled' : ''}>${job ? (job.waiting_for_more?'已加载 · 向下继续':'<span class="spinner"></span>正在读取') : '↻ '+(isLibrary?'更新书库':'更新榜单')}</button>`;
    let body = pageHead(title,sub,action)+(isLibrary ? heiyanBooklistCard(!!snapshot) : renderBoardControls());
    const freshResult=latestResults.get(key);
    if(freshResult && freshResult!==snapshot?.id)body+=`<div class="new-results"><span>新的榜单已更新。当前选中的作品仍保留在原记录中。</span><button data-action="show-latest" data-id="${escape(freshResult)}">查看最新榜单 →</button></div>`;
    if(isLibrary && !snapshot){$('#content').innerHTML=body;return;}
    const related = state.snapshots.filter((s) => s.platform === p.id && s.mode === mode());
    body += '<section class="surface" aria-label="作品列表"><div class="surface-head"><div><strong>'+escape(snapshot ? scopeOf() || '本次作品' : membership.can_read_history?'暂无已保存榜单':failed?'榜单更新未完成':'正在同步'+(isLibrary?'书库':'榜单'))+'</strong><span class="badge">'+(isLibrary?'书库资料':'平台榜单')+'</span>';
    body += `<div class="meta">${snapshot ? `${(snapshot.books || []).length} 本作品 · 采集于 ${escape(date(snapshotDate(snapshot)))}` : membership.can_read_history?'会员到期期间仅查看已保存资料':'自动保存每次榜单，更新不会改变已选中的作品'}</div></div>`;
    if(snapshot?.provenance?.source_name)body+=`<div class="meta snapshot-source">来源：${escape(snapshot.provenance.source_name)} · 数据日期：${escape(snapshot.data_date || '来源未提供')}</div>`;
    if(snapshot?.pagination)body+=`<div class="meta pagination-summary">${escape(paginationSummary(snapshot))}</div>`;
    if (related.length > 1) body += `<select class="snapshot-select" id="snapshot-select" aria-label="切换历史榜单">${related.map((s) => `<option value="${escape(s.id)}" ${s.id===snapshot?.id?'selected':''}>${escape(date(snapshotDate(s)))} · ${escape(s.category || s.board || '')} · ${s.books_count ?? s.count ?? '?'} 本</option>`).join('')}</select>`;
    body += '</div>';
    if (job) body += `<div class="job-strip" role="status">${job.waiting_for_more?'<span class="status-dot online"></span>':'<span class="spinner"></span>'}<span>${escape(job.message || '正在读取平台…')}</span><button data-action="cancel-job" data-id="${escape(job.id)}">取消</button></div>`;
    if(failed && snapshot)body+=`<div class="job-strip job-warning"><span>${escape(recentJob.message || '上次采集未完成，已有结果保留。')}</span>${sourceActions(p)}</div>`;

    if (!snapshot) {
      if(membership.can_read_history)body+=empty('这里还没有历史榜单','可以查看其他已保存的平台与记录，续费后恢复榜单更新。','','▤');
      else if (unsupported) body += empty('这个来源还在接入中',p.message || '可以先导入已有的书目文件，继续选书和分析。','<button class="button secondary" data-action="import-snapshot">导入书目 JSON</button>','▤');
      else if(failed)body+=empty(restricted?'来源暂不接受工作台读取':'本次榜单读取未完成', (recentJob.message || '请稍后重试。')+(p.id==='scribblehub'?' 也可导入在浏览器中保存的榜单 HTML。':''),sourceActions(p)+'<button class="button secondary small" data-action="collect">重试读取</button>','▤','source-failure');
      else body += empty(job ? '正在读取平台榜单' : '正在同步作品',job ? '作品会在完成后自动显示。你可以先浏览其他平台。' : '工作台会自动取得当前榜单。已有资料也可以直接导入。','<button class="button secondary" data-action="import-snapshot">导入已有书目</button>','▤');
    } else {
      body += renderToolbar();
      const books = orderedBooks(), size = Math.max(1,Number(view().page_size) || 20), page = Math.max(1,Number(view().page) || 1), pages = Math.max(1,Math.ceil(books.length/size));
      const windowKey=[snapshot.platform,snapshot.channel,snapshot.category,snapshot.board,viewingHistory()?snapshot.id:'live',JSON.stringify(view().filters || {}),page,size].join('|');
      if(listWindow.key!==windowKey)listWindow={key:windowKey,count:size};
      const start = (Math.min(page,pages)-1)*size, end=Math.min(books.length,start+(page===1?listWindow.count:size)), sliced = books.slice(start,end);
      body += '<div class="book-layout"><div class="book-list"><div class="list-key"><span>列表序号 / 作品</span><span>'+ (isLibrary ? '资料来源' : mode()==='overseas'?'来源名次':'原榜名次') +'</span></div>';
      if (!books.length) body += empty('没有匹配的作品','换个关键词，或清除筛选条件。','<button class="button secondary small" data-action="clear-filters">清除筛选</button>','⌕','compact');
      else body += sliced.map((b,i) => `<button class="book-row" data-action="select-book" data-book="${escape(b.id)}" aria-pressed="${b.id===view().selected_id}"><span class="list-number">${String(start+i+1).padStart(2,'0')}</span>${cover(b)}<span class="row-content"><span class="book-title">${escape(b.title)}${favorite(b) ? '<span class="star" aria-label="已收藏"> ♡</span>' : ''}</span><span class="book-meta">${escape(b.author || '作者未提供')}${(b.tags?.length) ? '<span>·</span>'+escape(Array.isArray(b.tags)?b.tags.slice(0,2).join(' · '):b.tags) : ''}${b.status?'<span>·</span>'+escape(b.status):''}</span><span class="row-summary">${escape(b.summary || '本次来源未提供简介')}</span><span class="row-metrics">${metricsHTML(b)}</span></span><span class="rank">${isLibrary ? '书库' : Number.isFinite(b.rank) ? '# '+b.rank : '—'}</span></button>`).join('');
      const continuation=membership.allowed?continuationJob():null;
      if(page===1 && (end<books.length || continuation))body+=`<div class="list-load-more" id="list-end"><button class="button secondary" data-action="load-more">${end<books.length?`加载更多作品（已显示 ${end} / ${books.length}）`:'继续读取后续作品'}</button><small>${end<books.length?'向下浏览时继续显示后续作品':'已取得的作品可先浏览，继续向下时再读取后续页面'}</small></div>`;
      if (books.length) body += `<div class="pagination"><span>共 ${books.length} 本 · 序号跨页连续</span><div><button data-action="page" data-page="${page-1}" aria-label="上一页" ${page<=1?'disabled':''}>‹</button><span>${Math.min(page,pages)} / ${pages}</span><button data-action="page" data-page="${page+1}" aria-label="下一页" ${page>=pages?'disabled':''}>›</button></div></div>`;
      body += '</div>'+renderDetail(books)+'</div>';
      if(snapshot.warnings?.length)body+=`<div class="source-warnings">${snapshot.warnings.map(w=>`<p>${escape(readableWarning(w))}</p>`).join('')}</div>`;
      const source = safeURL(snapshot.source_url);
      body += `<div class="source-note"><span>${snapshot.provenance?.source_name?'来源：'+escape(snapshot.provenance.source_name)+' · ':''}来源数据日期：${escape(snapshot.data_date || '来源未提供')} · ${isLibrary?'书库顺序不代表热度排名':mode()==='overseas'?((snapshot.books || []).some(book=>Number.isFinite(book.rank))?'名次沿用来源榜单；列表序号用于选书':'原站未提供连续名次；列表序号用于选书'):snapshot.source_id && snapshot.source_id!=='direct'?'来源列表位置不代表原榜名次':'名次以本次原始榜单为准'}</span>${source?`<a href="${escape(source)}" target="_blank" rel="noopener noreferrer">查看来源 ↗</a>`:''}</div>`;
    }
    body += '</section>';
    if (p.message && !isLibrary) body += `<p class="collection-hint">${escape(p.message)}</p>`;
    $('#content').innerHTML = body;
  }
  function overviewJob(){return overview?.update || (state?.jobs || []).find(job=>job.kind==='overview') || null;}
  function overviewJobActive(job){return ['queued','running','pending','cancelling'].includes(job?.status) || job?.public_source?.status==='running';}
  function overviewRunning(){return membership.allowed && overviewJobActive(overviewJob());}
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
    html+=`<div class="overview-collection-status" role="status"><span class="status-dot ${overviewError?'':'online'}"></span><span>${escape(running?(job.public_source?.status==='running' && !['queued','running','pending','cancelling'].includes(job.status)?'正在补充公开榜单快照，已有数据可继续浏览':job.message || '正在更新，已有数据可继续查看'):overviewError || (overview?(membership.can_read_history?'本机历史数据已载入 · 更新已暂停':'本机数据已载入 · 自动检查更新'):'正在读取本机保存的榜单…'))}</span>${running?`<button data-action="cancel-job" data-id="${escape(job.id)}">暂停</button>`:`<small>${coverage.latest_collected_at?date(coverage.latest_collected_at):''}</small>`}</div>`;
    html+=`<nav class="overview-tabs data-platform-tabs" aria-label="总览平台">${[{id:'fanqie',name:'番茄小说',board:'男频 · 女频新书榜'},{id:'qidian',name:'起点中文网',board:'月票榜'},{id:'ciweimao',name:'刺猬猫',board:'月票榜'}].map(item=>`<button data-action="overview-platform" data-platform="${item.id}" aria-pressed="${overviewPlatform===item.id}"><strong>${item.name}</strong><span>${item.board}</span><small>${statNumber(panels.find(p=>p.platform===item.id)?.summary?.books || 0)} 本</small></button>`).join('')}</nav>`;
    html+=`<div class="overview-scope dashboard-scope"><span>${overviewPlatform==='fanqie'?'仅新书榜 · 阅读榜不参与汇总':'月票榜 · 使用本平台原始指标'}</span>${overviewPlatform==='fanqie'?`<div class="chart-switch channel-switch" aria-label="汇总频道">${channels.map(value=>`<button data-action="dashboard-channel" data-channel="${escape(value)}" aria-pressed="${channel===value}">${value==='全部'?'全部频道':escape(value)}</button>`).join('')}</div>`:''}</div>`;
    if(!panel?.summary?.books || !data){
      html+=`<section class="overview-empty"><h2>${membership.can_read_history?'暂无历史榜单数据':running?'正在整理第一批数据':'等待可用的榜单数据'}</h2><p>${membership.can_read_history?'当前平台没有已保存的汇总，续费后恢复更新。':(overviewPlatform==='fanqie'?'自动汇总男频、女频新书榜各分类。':'自动读取本平台月票榜。')+'取得数据后即显示统计。'}</p><div class="overview-empty-sections"><span>分类对比</span><span>字数分布</span><span>黑马雷达</span></div>${overviewError?'<button class="button secondary" data-action="overview-retry">重新读取</button>':''}</section>`;$('#content').innerHTML=html;return;
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
    if(!readable() || overviewLoading)return;
    const epoch=accountEpoch;
    overviewLoading=true;const request=++overviewRequest;
    try{const data=await api('/api/overview');if(request!==overviewRequest)return;overview=data;overviewError='';}
    catch(error){if(epoch===accountEpoch)overviewError=error.message;}
    finally{if(epoch===accountEpoch){overviewLoading=false;if(section==='overview')render();}}
  }
  async function maybeAutoOverview(){
    if(!membership.allowed || legacyBlocked() || !state || document.hidden || section!=='overview' || overviewRefreshing || overviewRunning() || Date.now()<overviewNextAt)return;
    await refreshOverview(true);
  }
  async function refreshOverview(automatic=true){
    if(!membership.allowed || legacyBlocked() || overviewRefreshing || overviewRunning())return;
    const epoch=accountEpoch;
    overviewRefreshing=true;overviewNextAt=Date.now()+60000;if(section==='overview')render();
    try{
      const result=await api('/api/overview/refresh',{automatic});
      assertAccount(epoch);
      overviewNextAt=Date.now()+(result.retry_after_seconds || (result.status==='cooldown'?300:900))*1000;
      if(result.job){overview ||= {platforms:[]};overview.update=result.job;state.jobs=[result.job,...state.jobs.filter(job=>job.id!==result.job.id)];}
      await loadOverview();
      assertAccount(epoch);
      if(!automatic && result.status==='fresh')toast('总览已是最新。');
    }catch(error){if(epoch===accountEpoch){overviewError=error.message;overviewNextAt=Date.now()+300000;if(!automatic)notice(error.message);}}
    finally{if(epoch===accountEpoch){overviewRefreshing=false;if(section==='overview')render();}}
  }
  async function pollOverview(){
    const epoch=accountEpoch;assertAccount(epoch);
    const job=overviewJob();if(!overviewJobActive(job))return;
    const result=await api(`/api/jobs/${encodeURIComponent(job.id)}`), next=result.job || result;
    assertAccount(epoch);
    if(overview)overview.update=next;
    state.jobs=state.jobs.map(item=>item.id===next.id?next:item);
    const key=JSON.stringify([next.id,next.status,next.progress,next.partial_snapshot_id,next.message,next.public_source?.status,next.public_source?.snapshot_count,next.public_source?.updated_at,next.public_source?.finished_at,next.updated_at]);
    if(key!==overviewPollKey){overviewPollKey=key;await loadOverview();}
    assertAccount(epoch);
    if(!overviewJobActive(next)){overviewNextAt=Date.now()+(['failed','cancelled','interrupted'].includes(next.status)?300000:AUTO_TTL);await refresh();}
    else if(section==='overview')render();
  }
  function continuationJob(){return !viewingHistory() && (state?.jobs || []).find(job=>!['text','overview'].includes(job.kind) && job.platform===platformID() && (!job.options || scopeKey(job.platform,job.options)===scopeKey()) && (job.waiting_for_more || job.paused_for_demand) && (!view().selected_id || !job.partial_snapshot_id || job.partial_snapshot_id===snapshot?.id));}
  let loadingMore=false;
  async function loadMoreBooks(){
    if(!readable() || loadingMore)return;
    const epoch=accountEpoch;
    const active=currentJob();if(active && !active.waiting_for_more && Number(active.demand_count)>Number(active.partial_count) && listWindow.count>=orderedBooks().length)return;
    const books=orderedBooks(), job=membership.allowed?continuationJob():null;
    listWindow.count+=Math.max(20,Number(view().page_size) || 20);
    if(listWindow.count>books.length && job){
      loadingMore=true;
      try{const result=await api('/api/jobs/more',{job_id:job.id,count:100});assertAccount(epoch);if(result.job)state.jobs=[result.job,...state.jobs.filter(item=>item.id!==result.job.id)];}
      finally{if(epoch===accountEpoch)loadingMore=false;}
    }
    render();
  }
  function observeListEnd(){
    listObserver?.disconnect();listObserver=null;
    if(!readable())return;
    const sentinel=$('#list-end');if(!sentinel || typeof IntersectionObserver==='undefined')return;
    listObserver=new IntersectionObserver(entries=>{
      if(Date.now()-lastListScrollAt<2000 && entries.some(entry=>entry.isIntersecting) && $('#list-end')===sentinel){listObserver.disconnect();loadMoreBooks().catch(error=>notice(error.message));}
    },{rootMargin:'240px 0px'});listObserver.observe(sentinel);
  }
  function categoryNames(options){
    if(mode()==='overseas')return [...new Set([options.category,...(platform().categories || ['全部'])].filter(Boolean))];
    if(platformID()!=='fanqie')return [options.category || '全部'];
    const verifiedFemale=['古风世情','科幻末世','游戏体育','女频衍生','玄幻言情','种田','年代','现言脑洞','宫斗宅斗','悬疑脑洞','古言脑洞','快穿','青春甜宠','星光璀璨','女频悬疑','职场婚恋','豪门总裁','民国言情'];
    const source=snapshot?.platform==='fanqie' && snapshot.channel===options.channel && snapshot.categories?.length ? snapshot.categories : options.channel==='女频'?verifiedFemale:['玄幻脑洞'];
    return [...new Set([options.category,...source].filter(Boolean))];
  }
  function renderBoardControls(){
    const id=platformID(), options=optionsFor(), key=scopeKey(), supported=id!=='fanqie_short';
    const status=membership.can_read_history?'历史资料只读 · 更新与新任务已暂停':viewingHistory()?'查看历史记录 · 自动更新已暂停':currentJob()?(currentJob().waiting_for_more?'已载入首批 · 向下浏览继续读取':'后台自动更新中'):accessRestricted(latestCollectionJob())?'来源拒绝读取 · 自动重试已暂停':autoMessages.get(key) || (supported?'自动更新 · 每 15 分钟检查':'此来源待接入');
    const channels=channelsFor(id);
    const categories=categoryNames(options), frequent=(options.channel==='女频'?['古言脑洞','现言脑洞','豪门总裁','年代','种田','快穿']:[options.category]).filter(value=>categories.includes(value));
    return `<div class="board-controls"><label>频道<select id="board-channel" ${channels.length===1?'disabled':''}>${channels.map(value=>`<option ${value===options.channel?'selected':''}>${escape(value)}</option>`).join('')}</select></label><label>榜单<select id="board-kind">${boardNames(id).map(value=>`<option ${value===options.board?'selected':''}>${escape(value)}</option>`).join('')}</select></label><label>分类<select id="board-category" aria-label="榜单分类" ${categories.length===1?'disabled':''}>${categories.map(value=>`<option ${value===options.category?'selected':''}>${escape(value)}</option>`).join('')}</select></label><div class="auto-status">${currentJob() && !currentJob().waiting_for_more?'<span class="spinner"></span>':`<span class="status-dot${!viewingHistory() && accessRestricted(latestCollectionJob())?'':' online'}"></span>`}<span>${escape(status)}</span>${viewingHistory()?'<button data-action="resume-auto">恢复自动更新</button>':''}</div></div>${(id==='fanqie' || mode()==='overseas') && (mode()==='overseas'?categories:frequent).length>1?`<div class="category-shortcuts" aria-label="常用分类"><span>${mode()==='overseas'?'分类':'常用'}</span>${(mode()==='overseas'?categories:frequent).map(value=>`<button data-action="category" data-category="${escape(value)}" aria-pressed="${value===options.category}">${escape(value)}</button>`).join('')}</div>`:''}`;
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
    return `<aside class="detail" aria-label="作品详情" data-book="${escape(b.id)}" data-snapshot="${escape(snapshot.id)}"><div class="detail-top"><span>已选中的作品</span><button class="favorite-button" data-action="favorite" data-book="${escape(b.id)}" aria-pressed="${favorite(b)}">${favorite(b)?'♥ 已收藏':'♡ 收藏'}</button></div>${cover(b,true)}<h2>${escape(b.title)}</h2><p class="author">${escape(b.author || '作者未提供')}${b.status?' · '+escape(b.status):''}</p><div class="positions"><span>${position?'列表第 '+position+' 本':'已被当前筛选隐藏'}</span>${snapshot.platform!=='heiyan' && Number.isFinite(b.rank)?`<span class="original">${mode()==='overseas'?'来源榜单':'原榜'}第 ${b.rank} 名</span>`:''}</div>${mode()==='overseas' && membership.allowed?`<div class="translation-card"><span class="translation-mark" aria-hidden="true">译</span><div><strong>回 WorkBuddy 说“翻译这本”</strong><p>翻译所选作品的英文书名与简介。</p><button class="button secondary small" data-action="translation-context">复制翻译请求</button></div></div>`:''}<p class="summary">${escape(b.summary || '本次来源未提供简介，可在作品原页查看。')}</p>${metrics.length?`<div class="metrics">${metrics.map((m)=>`<span>${escape(m.name)} <strong>${escape(m.value)}</strong>${escape(m.unit || '')}</span>`).join('')}</div>`:''}<div class="detail-actions">${url?`<a class="source-link" href="${escape(url)}" target="_blank" rel="noopener noreferrer">打开作品原页 ↗</a>`:''}${snapshot.platform==='fanqie'?'<button class="button primary small" data-action="catalog">目录与正文</button>':''}<button class="button secondary small" data-action="import-text">导入正文</button></div><p class="text-status">${membership.can_read_history?'当前查看已保存资料，续费后恢复分析与正文获取。':'已选对象已保存，可回到 WorkBuddy 继续分析。'}</p>${renderTextAssets(b)}${reports.map((r)=>`<button class="report-item" data-action="report" data-id="${escape(r.id)}">▤ ${escape(r.title || '拆书报告')}<br><small>${escape(r.scope || '')}</small></button>`).join('')}</aside>`;
  }
  function empty(title,text,actions='',symbol='▤',extra='') { return `<div class="empty ${extra}"><div class="empty-symbol" aria-hidden="true">${symbol}</div><h2>${escape(title)}</h2><p>${escape(text)}</p>${actions?`<div class="actions">${actions}</div>`:''}</div>`; }
  function heiyanBooklistCard(compact=false){
    return `<section class="heiyan-booklist${compact?' heiyan-booklist-compact':''}" aria-label="黑岩腾讯文档书单"><div class="booklist-icon" aria-hidden="true">▤</div><span class="overview-kicker">黑岩故事会 · 腾讯文档</span><h2>从书单里，找到下一篇灵感</h2><p>打开书单后，在腾讯文档页面完成登录即可查看。已在浏览器登录的账号可继续使用。</p><a class="button primary" id="heiyan-booklist" href="${HEIYAN_BOOKLIST}" target="_blank" rel="noopener noreferrer">打开黑岩书单 <span aria-hidden="true">↗</span></a><small>书单在新标签页打开 · 不需要额外安装浏览器</small>${compact?'<p class="booklist-saved-note">以下是此前保存在本机的书目，继续保留供你查阅。</p>':''}</section>`;
  }
  function renderBridge() {
    if(membership.can_read_history){$('#bridge').innerHTML='';return;}
    const b = snapshot?.books?.find((x)=>x.id===view().selected_id);
    if (section !== 'browse' || !b) { $('#bridge').innerHTML = ''; return; }
    $('#bridge').innerHTML = `<section class="bridge" aria-label="WorkBuddy 对话提示"><div class="bridge-main"><div><div class="bridge-label">回到 WorkBuddy，可以说</div><p>${mode()==='overseas'?'“翻译我刚选的这本书名和简介，再分析它的故事卖点。”':'“分析我刚选的这本，重点看开头怎么吸引读者。”'}</p></div><button data-action="context">查看引用 ⌄</button></div><small>已保存：${escape(b.title)} · ${escape(scopeOf())} · ${escape(date(snapshotDate(snapshot)))}</small></section>`;
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
      html+=state.reports.length?'<div class="saved-list">'+state.reports.map((r)=>savedRow(r.title||'拆书报告',r.scope||'阅读范围见报告',r.created_at,'report',{'id':r.id})).join('')+'</div>':empty('还没有拆书报告',membership.can_read_history?'这个账号尚未保存拆书报告。续费后可继续开始新的分析。':'在工作台选一本书，再回到 WorkBuddy 说“分析我刚选的这本”。完成分析并保存报告后，就能在这里继续查看。');
    }
    $('#content').innerHTML=html+'</section>';
  }
  function savedRow(title,sub,time,action,attrs) { return `<div class="saved-row"><button class="saved-open" data-action="${action}" ${Object.entries(attrs).filter(([,v])=>v).map(([k,v])=>`data-${k}="${escape(v)}"`).join(' ')}><strong>${escape(title)}</strong><small>${escape(sub)}</small></button><span class="saved-date">${escape(date(time))}</span></div>`; }
  function showDialog(title,content,subtitle='') {
    if(!readable())return;
    $('#dialog').dataset.kind='';
    $('#dialog-body').innerHTML=`<div class="dialog-head"><div><h2>${escape(title)}</h2>${subtitle?`<p>${escape(subtitle)}</p>`:''}</div><button class="close-dialog" data-action="close-dialog" aria-label="关闭窗口">×</button></div><div class="dialog-content">${content}</div>`;
    if(!$('#dialog').open) $('#dialog').showModal();
    applyReadOnlyControls();
  }
  function collectionOptions() {
    const id=platformID(), current=optionsFor(id), categories=categoryNames(current);
    const select=(name,values)=>`<select id="${name}" name="${name}">${values.map(x=>`<option ${current[name]===x?'selected':''}>${escape(x)}</option>`).join('')}</select>`;
    const category=mode()==='overseas'?select('category',categories):`<input id="category" name="category" value="${escape(current.category || '全部')}" required maxlength="40">`;
    showDialog('采集设置',`<form id="options-form"><p>设置保存后自动更新。日常使用无需再次填写。</p><div class="form-grid"><div class="field"><label for="channel">频道</label>${select('channel',channelsFor(id))}</div><div class="field"><label for="category">分类</label>${category}</div><div class="field"><label for="board">榜单</label>${select('board',boardNames(id))}</div><div class="field"><label for="limit">最大采集数量</label><input id="limit" name="limit" type="number" min="1" max="10000" value="${current.limit || 10000}" required></div></div><p class="help-inline">先显示首批作品，向下浏览时继续读取；最多读取 100 页，以来源实际提供数量为准。</p><div class="form-actions"><button class="button secondary" type="button" data-action="close-dialog">取消</button><button class="button primary" type="submit">保存条件</button></div></form>`);
  }
  async function copyTranslationContext(){
    const epoch=accountEpoch;assertAccount(epoch);
    const book=snapshot?.books?.find(item=>item.id===view().selected_id);
    if(!book)return;
    const pinned={snapshot_id:snapshot.id,book_id:book.id};
    const result=await api('/api/translation-context',pinned);
    assertAccount(epoch);
    if(typeof result.prompt!=='string' || !result.prompt.trim())throw new Error('未取得这本作品的翻译引用，请重试。');
    try{
      if(!navigator.clipboard?.writeText)throw new Error('clipboard unavailable');
      await navigator.clipboard.writeText(result.prompt);assertAccount(epoch);toast('已复制。粘贴到 WorkBuddy，即可翻译这本作品。');
    }catch{
      assertAccount(epoch);
      showDialog('复制到 WorkBuddy',`<p>这段请求已固定到所选作品，粘贴后即可翻译。</p><textarea class="translation-request" readonly aria-label="WorkBuddy 翻译请求">${escape(result.prompt)}</textarea>`);
      const field=$('.translation-request');field.focus();field.select();
    }
  }
  async function showLatest(id){
    setHistory(null);latestResults.delete(scopeKey());
    await updateView({snapshot_id:id,selected_id:null,page:1});
  }
  async function receiveSnapshot(id,platformId,options,jobId=null){
    const epoch=accountEpoch;assertAccount(epoch);
    while(pendingViewMutations>0)await saving.catch(()=>{});
    assertAccount(epoch);
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
    if(!membership.allowed || legacyBlocked() || !state || document.hidden || section!=='browse' || viewingHistory() || activeRequest)return;
    const id=platformID(), p=platform(), options=optionsFor(), key=scopeKey(id,options);
    if(id==='fanqie_short' || id==='heiyan')return;
    if(['planned','unavailable'].includes(p.status) && id!=='heiyan')return;
    if(currentJob() || accessRestricted(latestCollectionJob()) || Date.now()<(nextAutoAt.get(key) || 0))return;
    // Freshness depends on the complete collection scope (including limits).
    // Ask the backend even when a visible snapshot was collected recently.
    nextAutoAt.set(key,Date.now()+60000);
    await collect(true);
  }
  async function collect(automatic=false) {
    if(!membership.allowed || legacyBlocked() || currentJob() || activeRequest)return;
    const epoch=accountEpoch;
    const id=platformID(), options=optionsFor(), key=scopeKey(id,options);
    if(id==='heiyan')return;
    if(!automatic){$('#notice').hidden=true;setHistory(null);}
    activeRequest=true;autoMessages.set(key,'正在检查更新…');render();
    try {
      const result=await api('/api/collect',{platform:id,options,automatic,interactive:true});
      assertAccount(epoch);
      if(result.status==='fresh'){
        nextAutoAt.set(key,Date.now()+(result.retry_after_seconds || 900)*1000);
        autoMessages.set(key,'已是最新 · 每 15 分钟自动检查');
        await refresh();assertAccount(epoch);if(result.snapshot_id)await receiveSnapshot(result.snapshot_id,id,options);return;
      }
      if(result.status==='cooldown'){
        if(result.requires_action){
          if(result.job?.id)state.jobs=[result.job,...state.jobs.filter(job=>job.id!==result.job.id)];
          else await refresh();
          assertAccount(epoch);
          nextAutoAt.set(key,Infinity);
          autoMessages.set(key,'来源拒绝读取 · 自动重试已暂停');
          render();return;
        }
        nextAutoAt.set(key,Date.now()+(result.retry_after_seconds || 300)*1000);
        autoMessages.set(key,'暂未取得更新，稍后自动重试');
        render();return;
      }
      nextAutoAt.set(key,Date.now()+AUTO_TTL);
      autoMessages.set(key,'后台更新中，已有内容可继续查看');
      await refresh();
      assertAccount(epoch);
      const job=state.jobs.find(item=>item.id===(result.job_id || result.job?.id));
      if(job?.status==='completed' && job.snapshot_id){
        await receiveSnapshot(job.snapshot_id,id,options,job.id);
        assertAccount(epoch);
        autoMessages.set(key,'已更新 · 每 15 分钟自动检查');
      } else if(job?.status==='failed'){
        autoMessages.set(key,'暂未取得更新，保留上次结果');
        if(!automatic)notice(job.message || '采集未完成，已有结果保留。');
      } else if(!automatic)toast('正在更新，完成后会自动保存。');
    } catch(error){
      if(epoch!==accountEpoch)return;
      nextAutoAt.set(key,Date.now()+5*60*1000);autoMessages.set(key,'更新暂不可用，稍后自动重试');
      if(!automatic)throw error;
    } finally {if(epoch===accountEpoch){activeRequest=false;render();}}
  }
  async function changeOptions(patch){
    const epoch=accountEpoch;assertAccount(epoch);
    const id=platformID(), options={...optionsFor(id),...patch};
    savedOptions[id]=options;savePreference('fagao-collection-options',JSON.stringify(savedOptions));
    setHistory(null);latestResults.delete(scopeKey());
    const matching=bestSnapshotForScope(id,options);
    await updateView({snapshot_id:matching?.id || null,selected_id:null,page:1,filters:{query:'',sort:'source',favorites:false}});
    assertAccount(epoch);
    await maybeAutoCollect();
  }
  function schedulePoll() {
    clearTimeout(pollTimer);
    if(!membership.allowed || legacyBlocked())return;
    const epoch=accountEpoch;
    pollTimer=setTimeout(async()=>{
      try {
        assertAccount(epoch);
        const jobs=state?.jobs || [], running=jobs.filter(j=>!['text','overview'].includes(j.kind) && ['queued','running','pending'].includes(j.status));
        if(running.length) {
          const results=await Promise.all(running.map(j=>api(`/api/jobs/${encodeURIComponent(j.id)}`)));
          assertAccount(epoch);
          let ended=false;
          for(let i=0;i<results.length;i++) {
            const job=results[i].job || results[i], idx=state.jobs.findIndex(j=>j.id===job.id);
            if(idx>=0)state.jobs[idx]=job;
            if(job.partial_snapshot_id && section==='browse'){await receiveSnapshot(job.partial_snapshot_id,job.platform,job.options || optionsFor(job.platform),job.id);}
            assertAccount(epoch);
            if(!['queued','running','pending'].includes(job.status)){
              ended=true;
              if(['failed','error','cancelled'].includes(job.status)){nextAutoAt.set(scopeKey(job.platform,job.options || optionsFor(job.platform)),Date.now()+5*60*1000);autoMessages.set(scopeKey(job.platform,job.options || optionsFor(job.platform)),'更新未完成，已有结果保留');if(!job.automatic)notice(job.message || '采集未完成。已有书目仍然保留。');}
              else if(!job.automatic)toast(job.message || '新的采集结果已保存。');
            }
          }
          if(ended) {
            const completed=results.map(r=>r.job || r).find(j=>j.status==='completed' && j.snapshot_id && j.platform===platformID() && (!j.options || scopeKey(j.platform,j.options)===scopeKey()) && section==='browse');
            await refresh();
            assertAccount(epoch);
            if(completed){const jobOptions=completed.options || optionsFor(completed.platform);await receiveSnapshot(completed.snapshot_id,completed.platform,jobOptions,completed.id);assertAccount(epoch);autoMessages.set(scopeKey(completed.platform,jobOptions),'已更新 · 每 15 分钟自动检查');}
          } else render();
        }
        await pollTextJobs();
        assertAccount(epoch);
        await pollOverview();
        assertAccount(epoch);
        await maybeAutoCollect();
        assertAccount(epoch);
        await maybeAutoOverview();
      } catch(error) { if(epoch===accountEpoch)notice(error.message); }
      finally { if(epoch===accountEpoch)schedulePoll(); }
    },2200);
  }
  async function openRecord(el) {
    const navigation=++navigationGeneration;
    const meta=state.snapshots.find(s=>s.id===el.dataset.snapshot);
    const snap=meta || await api(`/api/snapshots/${encodeURIComponent(el.dataset.snapshot)}`);
    if(navigation!==navigationGeneration)return;
    section='browse';setHistory(el.dataset.snapshot,snap.mode || platformMode(snap.platform));
    await updateView({mode:snap.mode || platformMode(snap.platform),platform:snap.platform,snapshot_id:el.dataset.snapshot,selected_id:el.dataset.book || null,page:1,filters:{query:'',sort:'source',favorites:false}});
  }
  async function report(id) { const epoch=accountEpoch,r=await api(`/api/reports/${encodeURIComponent(id)}`);assertAccount(epoch);showDialog(r.title || '拆书报告',`<pre class="report-text">${escape(r.content || r.report?.content || '')}</pre>`,[r.scope,date(r.created_at)].filter(Boolean).join(' · ')); }
  const textJobActive=job=>job && ['pending','running','queued','cancelling'].includes(job.status);
  function textAssetsFor(bookId,platformId=snapshot?.platform){
    return (state?.texts || []).filter(item=>item.book_id===bookId && item.platform===platformId);
  }
  async function downloadText(id){
    const epoch=accountEpoch;assertAccount(epoch);
    const blob=await api(`/api/texts/${encodeURIComponent(id)}/download`,undefined,{blob:true});
    assertAccount(epoch);
    const url=URL.createObjectURL(blob);downloadURLs.add(url);
    const link=document.createElement('a');link.href=url;link.download='发糕正文-'+String(id).replace(/[^a-zA-Z0-9_-]/g,'').slice(0,48)+'.txt';
    link.hidden=true;document.body.append(link);link.click();link.remove();
    setTimeout(()=>{URL.revokeObjectURL(url);downloadURLs.delete(url);},1000);
  }
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
    return `<section class="saved-text-assets" aria-label="已保存的正文"><h3>正文资料 <span>${assets.length} 份</span></h3>${assets.slice(0,3).map(asset=>{const integrity=textIntegrity(asset);return `<div class="saved-text-item"><div><strong>${escape(asset.scope || '已保存正文')}</strong><small>${escape(integrity.label)} · ${Number(asset.characters || 0).toLocaleString()} 字符 · ${escape(date(asset.created_at || asset.collected_at))}</small>${integrity.detail?`<small>${escape(integrity.detail)}</small>`:''}</div><div class="text-item-actions"><button data-action="preview-text" data-id="${escape(asset.id)}">查看</button><button data-action="download-text" data-id="${escape(asset.id)}">TXT ↓</button></div></div>`;}).join('')}</section>`;
  }
  function captureTextRange(){
    if(!contentPanel || $('#dialog').dataset.kind!=='text')return;
    const form=$('#chapter-download-form');if(!form)return;
    contentPanel.range={mode:$('#chapter-range-mode')?.value || 'first',count:Number($('#chapter-count')?.value || 3),start:Number($('#chapter-start')?.value || 1),end:Number($('#chapter-end')?.value || 3)};
  }
  function catalogTotal(catalog){return Math.max(0,Number(catalog?.total ?? catalog?.catalog_chapters ?? catalog?.chapters?.length) || 0);}
  async function openCatalog(){
    const epoch=accountEpoch;assertAccount(epoch);
    while(pendingViewMutations>0)await saving.catch(()=>{});
    assertAccount(epoch);
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
      body+=`<section class="chapter-saved-result"><h3>正文已保存</h3><p class="chapter-saved-scope">${escape(asset.scope || '阅读范围见正文资料')}</p><div class="chapter-saved-meta"><span>${escape(integrity.label)}</span>${acquired!==null?`<span>${chapterLabel} ${acquired} 章</span>`:''}<span>${Number(asset.characters || 0).toLocaleString()} 字符</span>${missing.length?`<span>缺失 ${missing.length} 章</span>`:''}</div>${integrity.detail?`<p class="chapter-integrity" role="note">${escape(integrity.detail)}</p>`:''}${missing.length?`<details class="chapter-missing"><summary>查看未取得的章节</summary><ul>${missing.slice(0,100).map(item=>`<li>${Number.isFinite(item.index)?'第 '+item.index+' 章 · ':''}${escape(item.title || item.chapter || '')} ${escape(item.reason || '未取得正文')}</li>`).join('')}</ul>${missing.length>100?'<p>其余缺章记录已保存在正文资料中。</p>':''}</details>`:''}<div class="chapter-result-actions"><button class="button secondary" data-action="download-text" data-id="${escape(asset.id)}">导出 TXT ↓</button><button class="button secondary" data-action="preview-text" data-id="${escape(asset.id)}">查看已保存正文</button></div><p class="chapter-workbuddy-note">回到 WorkBuddy 可以说：“分析刚才保存的《${escape(panel.title)}》正文。”</p></section>`;
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
    const epoch=accountEpoch;assertAccount(epoch);
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
      assertAccount(epoch);
      const job=response.job || (response.job_id?await api(`/api/jobs/${encodeURIComponent(response.job_id)}`):null);
      assertAccount(epoch);
      if(contentPanel?.sequence===panel.sequence)panel.job=job;
      await refresh();assertAccount(epoch);toast(`《${panel.title}》的正文任务已开始，结果会保存在这本作品下。`);
    } catch(error){if(contentPanel?.sequence===panel.sequence)panel.error=error.message;}
    finally{if(contentPanel?.sequence===panel.sequence){panel.submitting=false;renderContentDialog();}}
  }
  async function pollTextJobs(){
    const epoch=accountEpoch;assertAccount(epoch);
    const running=(state?.jobs || []).filter(job=>job.kind==='text' && textJobActive(job));
    if(!running.length)return;
    let finished=false;
    for(const previous of running){
      const response=await api(`/api/jobs/${encodeURIComponent(previous.id)}`), job=response.job || response;
      assertAccount(epoch);
      const index=state.jobs.findIndex(item=>item.id===job.id);if(index>=0)state.jobs[index]=job;
      if(!textJobActive(job)){
        finished=true;
        if(!$('#dialog').open || $('#dialog').dataset.kind!=='text' || contentPanel?.job?.id!==job.id)toast(`${job.title || '作品'}：${job.message || (job.text_id?'正文已保存':'取文已结束')}`);
      }
    }
    if(finished)await refresh();else syncContentPanel();
  }
  async function previewText(id){
    const epoch=accountEpoch;
    const text=await api(`/api/texts/${encodeURIComponent(id)}`);
    assertAccount(epoch);
    const integrity=textIntegrity(text);
    showDialog(text.title || '已保存正文',`<div class="text-preview-actions"><span>${escape(text.scope || '阅读范围见正文资料')}</span><button class="button secondary small" data-action="download-text" data-id="${escape(id)}">导出 TXT ↓</button></div><p class="chapter-integrity">${escape(integrity.label)}${integrity.detail?' · '+escape(integrity.detail):''}</p><pre class="report-text saved-text-preview">${escape(text.content || '')}</pre>`,'已保存正文 · 不会重新下载');
  }
  const statNumber=value=>Number.isFinite(value)?value.toLocaleString('zh-CN',{maximumFractionDigits:2}):'未提供';
  const signedNumber=value=>Number.isFinite(value)?(value>0?'+':'')+statNumber(value):'—';
  async function openInsights(){
    const epoch=accountEpoch;assertAccount(epoch);
    const sid=snapshot?.id;if(!sid)return;
    const subtitle=[nameOf(snapshot.platform),scopeOf(snapshot),snapshot.data_date || '来源日期未提供'].filter(Boolean).join(' · ');
    showDialog('榜单观察','<div class="chapter-loading" role="status"><span class="spinner"></span>正在整理这份榜单的统计…</div>',subtitle);
    $('#dialog').dataset.kind='insights';$('#dialog').dataset.snapshot=sid;
    try{
      const data=await api('/api/insights',{snapshot_id:sid});
      assertAccount(epoch);
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
    }catch(error){if(epoch===accountEpoch && $('#dialog').open && $('#dialog').dataset.kind==='insights' && $('#dialog').dataset.snapshot===sid){showDialog('榜单观察',`<p role="alert">${escape(error.message)}</p>`,subtitle);$('#dialog').dataset.kind='insights';}}
  }
  function textImport() {
    const b=snapshot?.books?.find(x=>x.id===view().selected_id); if(!b)return;
    pendingText={snapshot_id:snapshot.id,book_id:b.id,title:b.title,text:''};
    showDialog('导入作品正文',`<form id="text-import-form"><p>正文将关联到《${escape(b.title)}》。保留实际阅读范围，方便 WorkBuddy 准确分析。</p><button type="button" class="button secondary" data-action="choose-text">选择 TXT / Markdown 文件</button><div class="file-label" id="text-file-label">尚未选择文件</div><div class="field" style="margin-top:18px"><label for="text-scope">这份文本包含哪些内容</label><input id="text-scope" name="scope" placeholder="例如：第 1—3 章，或完整短篇正文" required maxlength="200"></div><label class="checkbox-label"><input type="checkbox" id="text-complete">我确认这份文件包含作品完整正文</label><div class="form-actions"><button type="button" class="button secondary" data-action="close-dialog">取消</button><button type="submit" class="button primary">保存正文</button></div></form>`);
  }
  async function syncNavigationState(){
    const epoch=accountEpoch,request=++navigationGeneration;assertAccount(epoch);
    while(pendingViewMutations>0)await saving.catch(()=>{});
    assertAccount(epoch);
    const generation=viewGeneration, next=await api('/api/state');
    if(request!==navigationGeneration || generation!==viewGeneration)return false;
    acceptState(next);return true;
  }
  async function dispatch(el) {
    const a=el.dataset.action,epoch=accountEpoch;
    if(a==='dismiss-notice'){$('#notice').hidden=true;return;}
    if(a==='close-dialog'){$('#dialog').close();return;}
    if(a==='membership-connect'){await connectMembership();return;}
    if(a==='membership-refresh'){await checkMembership({force:true,reload:true});return;}
    if(a==='membership-logout'){await logoutMembership();return;}
    if(a==='retry'){await checkMembership({force:true,reload:true});return;}
    if(a==='legacy-claim' || a==='legacy-skip'){await decideLegacy(a==='legacy-claim'?'claim':'skip');return;}
    if(a==='legacy-retry'){if(await ensureLegacy({force:true}))await refresh();return;}
    if(!readable())return;
    if(legacyBlocked()){renderLegacy();return;}
    if(membership.can_read_history && writeActions.has(a)){notice('当前权益仅支持查看历史资料，账号恢复工作台权益后可更新与开始新任务。');return;}
    if(a==='mode') {
      if(!await syncNavigationState())return;assertAccount(epoch);section='browse';const target=el.dataset.mode, previous=state.views[target] || {}, id=previous.platform || firstPlatform[target];
      const matching=bestSnapshotForScope(id,{...defaultsFor(id),...savedOptions[id]});
      await updateView(previous.snapshot_id?{mode:target}:{mode:target,platform:id,snapshot_id:matching?.id || null,selected_id:null});await maybeAutoCollect();return;
    }
    if(a==='platform') {
      if(!await syncNavigationState())return;
      assertAccount(epoch);
      section='browse';const id=el.dataset.platform,targetMode=platformMode(id),existing=bestSnapshotForScope(id,optionsFor(id));setHistory(null,targetMode);
      if(id===platformID() && view().snapshot_id){await loadSnapshot();await maybeAutoCollect();return;}
      await updateView({mode:targetMode,platform:id,snapshot_id:existing?.id || null,selected_id:null,page:1,filters:{query:'',sort:'source',favorites:false}});await maybeAutoCollect();return;
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
    if(a==='translation-context'){await copyTranslationContext();return;}
    if(a==='collection-options'){collectionOptions();return;}
    if(a==='category'){if(el.dataset.category!==optionsFor().category)await changeOptions({category:el.dataset.category});return;}
    if(a==='collect'){await collect(false);return;}
    if(a==='show-latest'){await showLatest(el.dataset.id);return;}
    if(a==='resume-auto'){setHistory(null);render();await maybeAutoCollect();return;}
    if(a==='cancel-job'){await api('/api/jobs/cancel',{job_id:el.dataset.id});assertAccount(epoch);await refresh();assertAccount(epoch);toast('已取消本次采集，已有结果保留。');return;}
    if(a==='import-snapshot' || a==='import-ranking-html'){
      pendingSnapshotImport={platform:platformID(),htmlOnly:a==='import-ranking-html'};
      const input=$('#snapshot-file');input.accept=pendingSnapshotImport.htmlOnly?'text/html,.html,.htm':platformID()==='scribblehub'?'application/json,.json,text/html,.html,.htm':'application/json,.json';input.click();return;
    }
    if(a==='import-text'){textImport();return;}
    if(a==='catalog'){await openCatalog();return;}
    if(a==='insights'){await openInsights();return;}
    if(a==='insight-book'){$('#dialog').close();await openRecord(el);return;}
    if(a==='reload-catalog'){await loadCatalog();return;}
    if(a==='preview-text'){await previewText(el.dataset.id);return;}
    if(a==='download-text'){await downloadText(el.dataset.id);return;}
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
    if(!readable())return;
    const el=event.target;
    if(el.id==='legacy-confirm'){legacyConfirmed=legacy.can_claim===true && !!el.checked;const button=$('[data-action="legacy-claim"]');if(button)button.disabled=!legacyConfirmed || legacyBusy || legacy.can_claim!==true;return;}
    if(legacyBlocked())return;
    if(el.id==='chapter-range-mode'){captureTextRange();renderContentDialog();return;}
    if(el.id==='snapshot-select'){setHistory(el.value);updateView({snapshot_id:el.value,selected_id:null,page:1}).catch(e=>notice(e.message));}
    if(el.id==='board-channel'){changeOptions({channel:el.value,...(platformID()==='fanqie'?{category:el.value==='男频'?'玄幻脑洞':'古言脑洞'}:{})}).catch(e=>notice(e.message));}
    if(el.id==='board-kind')changeOptions({board:el.value}).catch(e=>notice(e.message));
    if(el.id==='board-category' && el.value.trim())changeOptions({category:el.value.trim()}).catch(e=>notice(e.message));
    if(el.id==='sort' || el.id==='favorites-filter'){
      const filters={...(view().filters || {}),sort:$('#sort')?.value || 'source',favorites:!!$('#favorites-filter')?.checked};
      updateView({filters,page:1}).catch(e=>notice(e.message));
    }
  });
  document.addEventListener('input',(event)=>{
    if(!readable() || legacyBlocked())return;
    if(event.target.id!=='query')return;
    const value=event.target.value;clearTimeout(searchTimer);
    searchTimer=setTimeout(()=>{
      const cursor=$('#query')?.selectionStart;
      updateView({filters:{...(view().filters || {}),query:value},page:1}).then(()=>{const input=$('#query'); if(input){input.focus();try{input.setSelectionRange(cursor,cursor);}catch{}}}).catch(e=>notice(e.message));
    },350);
  });
  document.addEventListener('submit',(event)=>{
    if(!membership.allowed || legacyBlocked()){event.preventDefault();return;}
    if(event.target.id==='chapter-download-form'){event.preventDefault();startTextDownload().catch(error=>notice(error.message));return;}
    if(event.target.id==='search-form'){event.preventDefault();return;}
    if(event.target.id==='options-form'){
      event.preventDefault();const form=new FormData(event.target);
      const options={channel:String(form.get('channel')),category:String(form.get('category')).trim(),board:String(form.get('board')),limit:Number(form.get('limit')),max_pages:100};
      $('#dialog').close();changeOptions(options).catch(error=>notice(error.message));return;
    }
    if(event.target.id==='text-import-form'){
      event.preventDefault();
      const epoch=accountEpoch;
      if(!pendingText?.text){toast('请先选择正文文件。');return;}
      const scope=$('#text-scope').value.trim();if(!scope)return;
      const payload={snapshot_id:pendingText.snapshot_id,book_id:pendingText.book_id,text:pendingText.text,scope,complete:$('#text-complete').checked};
      const submit=event.target.querySelector('[type="submit"]');submit.disabled=true;
      api('/api/import/text',payload).then(()=>{assertAccount(epoch);$('#dialog').close();pendingText=null;toast('正文已保存，可回到 WorkBuddy 继续分析。');return refresh();}).catch(e=>{if(epoch===accountEpoch){notice(e.message);submit.disabled=false;}});
    }
  });
  $('#snapshot-file').addEventListener('change',async(event)=>{
    const epoch=accountEpoch,file=event.target.files[0], target=pendingSnapshotImport;event.target.value='';pendingSnapshotImport=null;if(!file || !membership.allowed)return;
    try{
      if(file.size>8*1024*1024)throw new Error('书目文件超过 8 MB，请拆分后导入。');
      const html=/\.html?$/i.test(file.name) || file.type==='text/html';
      if(html && target?.platform!=='scribblehub')throw new Error('请从 Scribble Hub 榜单页选择要导入的 HTML。');
      if(!html && target?.htmlOnly)throw new Error('请选择浏览器保存的榜单 HTML 文件。');
      const content=await file.text();
      assertAccount(epoch);
      const parsed=html?null:JSON.parse(content);
      const result=html?await api('/api/import/overseas-html',{platform:target.platform,html:content}):await api('/api/import/snapshot',{snapshot:parsed.snapshot || parsed});
      assertAccount(epoch);await refresh();assertAccount(epoch);section='browse';
      const snap=result.snapshot || result;
      if(snap.id){setHistory(snap.id,snap.mode || mode());await updateView({mode:snap.mode || mode(),platform:snap.platform || platformID(),snapshot_id:snap.id,selected_id:null,page:1});}
      else render();assertAccount(epoch);toast('书目已导入，来源信息与原榜名次会一起保留。');
    }catch(e){notice(e instanceof SyntaxError?'这不是有效的 JSON 书目文件。':e.message);}
  });
  $('#text-file').addEventListener('change',async(event)=>{
    const epoch=accountEpoch,target=pendingText,file=event.target.files[0];event.target.value='';if(!file || !target || !membership.allowed)return;
    try{if(file.size>12*1024*1024)throw new Error('正文文件超过 12 MB，请按卷拆分导入。');const bytes=await file.arrayBuffer();assertAccount(epoch);if(pendingText!==target)return;let decoded=new TextDecoder('utf-8').decode(bytes);if(decoded.includes('\uFFFD')){try{decoded=new TextDecoder('gb18030',{fatal:true}).decode(bytes);}catch{}}pendingText.text=decoded;$('#text-file-label').textContent=`${file.name} · ${pendingText.text.length.toLocaleString()} 字符`;}
    catch(e){notice(e.message);}
  });
  $('#dialog').addEventListener('click',(event)=>{if(event.target===$('#dialog')){const r=$('#dialog').getBoundingClientRect();if(event.clientX<r.left || event.clientX>r.right || event.clientY<r.top || event.clientY>r.bottom)$('#dialog').close();}});
  window.addEventListener('scroll',()=>{lastListScrollAt=Date.now();const sentinel=$('#list-end');if(sentinel && sentinel.getBoundingClientRect().top<window.innerHeight+240)loadMoreBooks().catch(error=>notice(error.message));},{passive:true});
  document.addEventListener('keydown',event=>{if((event.key==='Enter' || event.key===' ') && event.target.matches?.('.scatter-dot')){event.preventDefault();dispatch(event.target).catch(error=>notice(error.message));}});
  document.addEventListener('visibilitychange',refreshForegroundMembership);
  window.addEventListener('focus',refreshForegroundMembership);
  window.addEventListener('pagehide',()=>{membershipRequest++;foregroundMembershipGeneration++;foregroundMembershipBusy=false;foregroundMembershipAt=-Infinity;clearTimeout(membershipTimer);clearAccountView();membership={allowed:false,reason:'checking',account:null};renderMembership();});
  window.addEventListener('pageshow',event=>{if(event.persisted)refreshForegroundMembership({force:true});});
  const callbackURL=new URL(window.location.href);
  if(callbackURL.searchParams.getAll('authorization').length===1 && callbackURL.searchParams.get('authorization')==='cancelled'){
    callbackNotice='已取消这次官网授权。资料未改变，需要时可重新连接发糕账号。';
    callbackURL.searchParams.delete('authorization');window.history.replaceState(null,'',callbackURL.pathname+callbackURL.search+callbackURL.hash);
  }
  renderMembership();checkMembership().catch(()=>{});
})();
