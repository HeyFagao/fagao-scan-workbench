// 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM, VirtualConsole} = require(process.env.FAGAO_QA_NODE_MODULES
  ? path.join(process.env.FAGAO_QA_NODE_MODULES, 'jsdom') : 'jsdom');
const root = path.resolve(__dirname, '../web');
const origin = 'http://127.0.0.1:18376';
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function eventually(predicate, message) {
  const deadline = Date.now() + 3000;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await pause(10);
  }
  assert.fail(message);
}
function member(id, active = true) {
  const now = Date.now();
  return {allowed:active, reason:active?'active':'expired', can_read_history:!active,
    access_mode:active?'active':'read_only', account:{id,display_name:id}, generation:'generation-'+id,
    server_time:new Date(now).toISOString(), identity_expires_at:new Date(now+600000).toISOString(),
    membership_expires_at:new Date(now+(active?86400000:-86400000)).toISOString(), permit_expires_at:active?new Date(now+600000).toISOString():null};
}
const response = (data, status=200) => ({ok:status<400, status, json:async()=>structuredClone(data)});
function source(id, name, mode, defaults, extra={}) {
  return {id,name,mode,status:'available',channels:[defaults.channel],categories:[defaults.category],
    boards:[defaults.board],max_limit:100,default_options:{limit:100,max_pages:5,...defaults},...extra};
}
function makeState(account) {
  const platforms = [
    source('fanqie','番茄小说','long',{channel:'女频',category:'古言脑洞',board:'新书榜'}),
    source('qimao','七猫中文网','long',{channel:'男频',category:'全部',board:'大热榜',period:'日榜',limit:50},{max_limit:50,periods:['日榜','月榜']}),
    source('shuqi','书旗小说','long',{channel:'综合',category:'全部',board:'热销榜'}),
    source('heiyan','黑岩故事会','short',{channel:'全部',category:'书库',board:'书库'}),
    source('hongguo_official','红果短剧','short',{channel:'综合',category:'全部',board:'综合热播榜'},
      {group:'drama',boards:['综合热播榜','真人热播榜','漫剧热播榜','AI热播榜']}),
    source('fanqie_recommend','番茄短篇推荐','short',{channel:'短篇',category:'现实情感',board:'题材推荐'},
      {categories:['现实情感','悬疑'] }),
    source('dianzhong','点众短篇','short',{channel:'男频',category:'精品故事',board:'畅销榜'}),
  ];
  const snapshots = platforms.filter(p=>p.id!=='heiyan').map(p=>({
    id:'snap-'+p.id,platform:p.id,mode:p.mode,...p.default_options,
    collected_at:new Date().toISOString(),data_date:p.id==='qimao'?null:'2026-11-16',complete:true,
    books:[{id:'work-'+p.id,title:account+'的'+p.name,author:p.id==='hongguo_official'?'':'作者甲',
      summary:p.id==='hongguo_official'?'红果真实简介 <script>不能执行</script>':'作品简介',
      cover_url:'https://example.invalid/cover-'+p.id+'.jpg',rank:1,
      rank_semantics:p.id==='fanqie_recommend'?'source_list_position':'original_rank',
      tags:['悬疑'],metrics:[{name:p.id==='qimao'?'热度':'在读',value:'120',unit:'万'}]}],
  }));
  snapshots.push({...structuredClone(snapshots.find(s=>s.platform==='fanqie_recommend')),
    id:'snap-recommend-suspense',category:'悬疑',books:[{id:'suspense',title:'悬疑故事',summary:'悬疑简介',rank:1,rank_semantics:'source_list_position'}]});
  return {active_mode:'long',platforms,snapshots,
    views:{long:{platform:'fanqie',snapshot_id:'snap-fanqie'},short:{platform:'heiyan'}},
    favorites:[],reports:[],jobs:[],history:[],texts:[],config:{}};
}
async function fixture(t,{active=true,storedOptions=null}={}) {
  const errors=[];
  const vc=new VirtualConsole();vc.on('jsdomError',error=>errors.push(error.message));
  const dom=new JSDOM(fs.readFileSync(path.join(root,'index.html'),'utf8'),{
    url:origin+'/',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
  const w=dom.window,d=w.document;
  const ctx={auth:member('账号甲',active),states:new Map(),collections:[],feedCalls:[],insightCalls:[],holdInsights:null,collectionError:false,singleObservation:false};
  const state=()=>{const id=ctx.auth.account.id;if(!ctx.states.has(id))ctx.states.set(id,makeState(id));return ctx.states.get(id)};
  const headers=init=>{assert.equal(init.headers['X-Fagao-Account'],ctx.auth.account.id);assert.equal(init.headers['X-Fagao-Generation'],ctx.auth.generation)};
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true};
  w.HTMLDialogElement.prototype.close=function(){this.open=false};
  w.fetch=async(input,init={})=>{
    const url=new URL(input,origin);assert.equal(url.origin,origin);
    const pathname=url.pathname, body=init.body?JSON.parse(init.body):null;
    if(pathname.startsWith('/api/membership/'))return response(ctx.auth);
    headers(init);
    if(pathname==='/api/legacy-data/status')return response({decision_required:false,available:false,can_claim:false,can_skip:true,original_preserved:true,counts:{}});
    if(pathname==='/api/state')return response(state());
    if(pathname==='/api/view'){
      const current=state();current.active_mode=body.mode;current.views[body.mode]={...current.views[body.mode],...body};
      return response(current);
    }
    if(pathname==='/api/overview')return response({platforms:[]});
    if(pathname==='/api/overview/refresh')return response({status:'fresh',retry_after_seconds:900});
    if(pathname.startsWith('/api/snapshots/'))return response(state().snapshots.find(s=>s.id===decodeURIComponent(pathname.split('/').pop())));
    if(pathname==='/api/collect'){
      assert(ctx.auth.allowed,'Expired account attempted collection');ctx.collections.push(body);
      if(ctx.collectionError)return response({error:{code:'source_unavailable',message:'来源暂时无法连接，已保留保存的资料。'}},503);
      const saved=state().snapshots.find(s=>s.platform===body.platform && s.category===body.options.category && s.board===body.options.board);
      return response({status:'fresh',snapshot_id:saved?.id,retry_after_seconds:900});
    }
    if(pathname==='/api/daily-boards' || pathname==='/api/daily-boards/sync'){
      const board=body?.board || url.searchParams.get('board');ctx.feedCalls.push(board);
      return response({board,snapshot:{board,columns:['排名','剧名'],rows:[['1','原来保存的红果榜']],scope:'历史样本',source_date:'2026-11-11',observed_at:'2026-11-11T10:00:00Z'},history:[]});
    }
    if(pathname==='/api/insights'){
      ctx.insightCalls.push(body);const owner=ctx.auth.account.id;
      if(ctx.holdInsights)await ctx.holdInsights;
      if(ctx.singleObservation)return response({trends:{basis:'collection_time',basis_label:'采集日观察',metric_label:'热度',items:[{book_id:'work-qimao',title:owner+'的七猫中文网',rank:1,rank_delta:null,metric_delta:null,points:[{snapshot_id:'snap-qimao',at:'2026-11-16T10:00:00Z',rank:1,metric_value:1200000}]}]}});
      return response({sample:{books:1},changes:{available:false},trends:{basis:'collection_time',basis_label:'采集日观察',metric_label:'热度',metric_unit:'',baseline_at:'2026-11-15T10:00:00Z',current_at:'2026-11-16T10:00:00Z',items:[{book_id:'work-qimao',title:owner+'的七猫中文网',rank:1,previous_rank:4,rank_delta:3,metric_value:1200000,metric_delta:200000,points:[{snapshot_id:'prior',at:'2026-11-15T10:00:00Z',rank:4,metric_value:1000000},{snapshot_id:'snap-qimao',at:'2026-11-16T10:00:00Z',rank:1,metric_value:1200000}]}],cross_board:{items:[]}}});
    }
    throw Error('Unexpected local path '+pathname);
  };
  ctx.d=d;ctx.w=w;ctx.state=state;
  ctx.click=selector=>{const node=d.querySelector(selector);assert(node,'Missing '+selector);assert(!node.disabled,'Disabled '+selector);node.click()};
  ctx.open=async id=>{if(!d.querySelector(`[data-action="platform"][data-platform="${id}"]`)){
    ctx.click('[data-action="mode"][data-mode="short"]');await eventually(()=>d.querySelector(`[data-action="platform"][data-platform="${id}"]`),'Short source missing');}
    ctx.click(`[data-action="platform"][data-platform="${id}"]`);await eventually(()=>d.querySelector('#content h1')?.textContent.startsWith(state().platforms.find(p=>p.id===id).name),'Source not opened');
    await eventually(()=>d.querySelector('.book-row'),'No book list');await pause(50);};
  t.after(()=>{dom.window.close();assert.deepEqual(errors,[])});
  if(storedOptions){w.localStorage.setItem('fagao-collection-options:'+encodeURIComponent(ctx.auth.account.id),JSON.stringify(storedOptions));w.localStorage.setItem('fagao-options-version:'+encodeURIComponent(ctx.auth.account.id),'3');}
  for(const script of d.querySelectorAll('script[src]'))w.eval(fs.readFileSync(path.join(root,path.basename(script.src)),'utf8'));
  await eventually(()=>d.querySelector('.overview-tabs'),'Workbench did not open').catch(error=>{throw Error(error.message+' '+d.querySelector('#content').textContent+' '+d.querySelector('#membership-account').textContent+' '+errors.join(' | '));});
  return ctx;
}

test('official red-drama source has one sidebar entry and renders real metadata safely',async t=>{
  const ctx=await fixture(t);const d=ctx.d;
  assert.equal(d.querySelectorAll('#drama-platforms [data-platform="hongguo_official"]').length,1);
  assert.equal(d.querySelector('#drama-platforms [data-board="hongguo"]'),null);
  await ctx.open('hongguo_official');
  assert.equal(d.querySelector('#platforms [data-platform="hongguo_official"]'),null);
  assert.equal(d.querySelector('#board-kind').options.length,4);
  assert(d.querySelector('.book-row .cover-image').src.includes('cover-hongguo_official'));
  assert.match(d.querySelector('.row-summary').textContent,/红果真实简介 <script>/);
  assert.equal(d.querySelector('.row-summary script'),null);
  assert(!d.querySelector('.book-row').textContent.includes('作者未提供'));
  assert.match(d.querySelector('.surface-head .meta').textContent,/1 部作品/);
  ctx.click('[data-action="select-book"]');await eventually(()=>d.querySelector('.detail[data-book="work-hongguo_official"] .summary'),'Details missing');
  assert.match(d.querySelector('.detail .summary').textContent,/红果真实简介/);
  ctx.click('#content [data-action="daily-board"][data-board="hongguo"]');
  await eventually(()=>d.querySelector('.daily-table-wrap'),'Old red feed unavailable');
  assert.match(d.querySelector('#content').textContent,/原来保存的红果榜/);
  ctx.click('#content [data-platform="hongguo_official"]');
  await eventually(()=>d.querySelector('.drama-content .book-row'),'Official source not restored');
});

test('new short recommendations retain the selected topic and recommendation wording',async t=>{
  const ctx=await fixture(t);const d=ctx.d;await ctx.open('fanqie_recommend');
  assert.match(d.querySelector('.surface-head').textContent,/题材推荐/);
  assert.equal(d.querySelector('.list-key span:last-child').textContent,'推荐顺序');
  assert.match(d.querySelector('.rank').textContent,/推荐/);
  const category=d.querySelector('#board-category');category.value='悬疑';category.dispatchEvent(new ctx.w.Event('change',{bubbles:true}));
  await eventually(()=>d.querySelector('.book-title')?.textContent==='悬疑故事','Topic did not load matching saved scope');
  assert.equal(d.querySelector('#board-category').value,'悬疑');
  ctx.click('[data-action="mode"][data-mode="long"]');await pause(60);
  ctx.click('[data-action="mode"][data-mode="short"]');
  await eventually(()=>d.querySelector('#content h1')?.textContent.startsWith('番茄短篇推荐'),'Short mode lost new source');
  assert.equal(d.querySelector('#board-category').value,'悬疑');
  assert(d.querySelector('#platforms [data-platform="dianzhong"]'));
});

test('qimao scope and collection form cap old saved preferences at fifty',async t=>{
  const ctx=await fixture(t,{storedOptions:{qimao:{limit:100}}});const d=ctx.d;
  await ctx.open('qimao');
  await eventually(()=>ctx.collections.some(c=>c.platform==='qimao'),'Qimao not checked');
  assert.equal(ctx.collections.find(c=>c.platform==='qimao').options.limit,50);
  ctx.click('[data-action="collection-options"]');await eventually(()=>d.querySelector('#limit'),'Form missing');
  assert.equal(d.querySelector('#limit').max,'50');assert.equal(d.querySelector('#limit').value,'50');
});

test('single-book trend shows actual observation time, rank trajectory and heat changes',async t=>{
  const ctx=await fixture(t);const d=ctx.d;await ctx.open('qimao');
  ctx.click('[data-action="select-book"]');await eventually(()=>d.querySelector('[data-action="book-trend"]'),'Trend entry missing');
  ctx.click('[data-action="book-trend"]');await eventually(()=>d.querySelector('.trend-chart'),'Trend chart missing');
  assert.equal(d.querySelector('.dialog-head h2').textContent,'追榜记录');
  assert.match(d.querySelector('.trend-heading').textContent,/采集日观察/);
  assert.match(d.querySelector('.trend-summary').textContent,/\+3/);
  assert.match(d.querySelector('.trend-summary').textContent,/200,000/);
  assert.equal(d.querySelectorAll('.trend-history tbody tr').length,2);
  assert.match(d.querySelector('.trend-chart title').textContent,/4 → 1/);
  assert.equal(ctx.insightCalls[0].snapshot_id,'snap-qimao');
});

test('a single saved observation does not invent rank changes or a trajectory',async t=>{
  const ctx=await fixture(t);const d=ctx.d;ctx.singleObservation=true;await ctx.open('qimao');
  ctx.click('[data-action="select-book"]');await eventually(()=>d.querySelector('[data-action="book-trend"]'),'Trend entry missing');
  ctx.click('[data-action="book-trend"]');await eventually(()=>d.querySelector('.trend-history'),'Saved observation missing');
  assert.equal(d.querySelector('.trend-chart'),null);
  assert.match(d.querySelector('.insights-empty').textContent,/保存下一次记录/);
  assert.match(d.querySelector('.trend-summary').textContent,/名次变化\s*—/);
  assert.equal(d.querySelectorAll('.trend-history tbody tr').length,1);
});

test('a temporary source failure preserves saved drama metadata and the selected work',async t=>{
  const ctx=await fixture(t);const d=ctx.d;await ctx.open('hongguo_official');
  ctx.click('[data-action="select-book"]');await eventually(()=>d.querySelector('.detail .summary'),'Details missing');
  const before=d.querySelector('.detail h2').textContent;ctx.collectionError=true;
  ctx.click('[data-action="collect"]');await eventually(()=>d.querySelector('#notice').textContent.includes('来源暂时无法连接'),'Failure notice missing');
  assert.equal(d.querySelector('.detail h2').textContent,before);
  assert.match(d.querySelector('.detail .summary').textContent,/红果真实简介/);
  assert.equal(d.querySelectorAll('.book-row').length,1);
  assert.equal(ctx.state().views.short.selected_id,'work-hongguo_official');
});

test('expired accounts can view saved red metadata and saved trends without collecting',async t=>{
  const ctx=await fixture(t,{active:false});const d=ctx.d;await ctx.open('hongguo_official');
  assert.equal(ctx.collections.length,0);assert(d.querySelector('[data-action="collect"]').disabled);
  ctx.click('[data-action="select-book"]');await eventually(()=>d.querySelector('.detail[data-book="work-hongguo_official"] .summary'),'Saved details missing');
  assert.match(d.querySelector('.detail .summary').textContent,/红果真实简介/);
  assert(d.querySelector('.favorite-button').disabled);
});

test('a delayed trend response from the previous account cannot reopen its data',async t=>{
  const ctx=await fixture(t);const d=ctx.d;await ctx.open('qimao');
  ctx.click('[data-action="select-book"]');await eventually(()=>d.querySelector('[data-action="book-trend"]'),'Trend entry missing');
  let release;ctx.holdInsights=new Promise(resolve=>release=resolve);
  ctx.click('[data-action="book-trend"]');await eventually(()=>ctx.insightCalls.length===1,'Trend request missing');
  ctx.auth=member('账号乙');ctx.click('[data-action="membership-refresh"]');
  await eventually(()=>d.querySelector('#membership-account').textContent.includes('账号乙'),'Account did not change');
  release();await pause(150);
  assert(!d.querySelector('#dialog').open || !d.querySelector('#dialog').textContent.includes('账号甲'));
});
