// DOM integration against the real local API. Platform collection is intercepted;
// this test never sends requests to a novel platform and does not verify pixels.
const assert = require('node:assert/strict');
const {JSDOM, VirtualConsole} = require(process.env.FAGAO_QA_NODE_MODULES ? process.env.FAGAO_QA_NODE_MODULES+'/jsdom' : 'jsdom');
const fs = require('node:fs');
const base=process.env.FAGAO_TEST_URL;
if (!base) throw new Error('Start tests/run_ui_smoke.py rather than running this directly.');
const errors=[], collections=[], overviewUpdates=[];
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function wait(fn,msg){for(let i=0;i<150;i++){if(await fn())return;await pause(20)}throw new Error(msg)}
function localFetch(path,init){const url=new URL(path,base);assert.equal(url.origin,new URL(base).origin,'DOM smoke attempted an external request');return fetch(url,init);}
async function member(){const status=await(await localFetch('/api/membership/status')).json();assert.equal(status.allowed,true,'Synthetic test member is not authorized');assert(status.account?.id && status.generation);return status;}
async function api(path,body){const identity=await member(),headers={'X-Fagao-Account':identity.account.id,'X-Fagao-Generation':identity.generation,...(body?{'Content-Type':'application/json','X-Fagao-Request':'1'}:{})};const r=await localFetch(path,body?{method:'POST',headers,body:JSON.stringify(body)}:{headers});const j=await r.json();assert(!j.error,JSON.stringify(j));return j;}
async function open({stale=false,freshSnapshot=null,history=false,expiredHeiyan=false,holdCollection=false,forceConnectedHeiyan=false,collectionResponse=null,contentFixture=false,storedOptions=null,startHome=false,collectionFixture=null,lateOverview=false}={}){
  const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(String(e)));
  const identity=await member();
  const dom=new JSDOM(await (await localFetch('/')).text(),{url:base+'/',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
  const makeHold=()=>{let release;const promise=new Promise(resolve=>{release=resolve});return {promise,release,captured:false}};
  let mockAuth=null;
  const control={pending:null,viewPending:null,collectionPending:holdCollection?makeHold():null,stateRequests:0,holdNextState(){return this.pending=makeHold()},holdNextView(){return this.viewPending=makeHold()}};dom.control=control;
  control.moreRequests=[];control.collectionJob=null;control.collectionPolls=0;
  control.overviewPolls=0;control.overviewReads=0;control.overviewJob=lateOverview?{id:'qa-overview-late',kind:'overview',status:'completed',message:'直采检查完成',public_source:{status:'running',snapshot_count:0,updated_at:'2026-11-09T12:00:00Z'}}:null;
  control.catalogRequests=[];control.textRequests=[];control.textAssets=[];control.textJob=null;control.textPolls=0;control.cancelCalls=0;control.downloadRequests=[];control.downloads=[];
  const contentOptions=typeof contentFixture==='object'?contentFixture:{};
  const blobs=new Map();
  dom.window.URL.createObjectURL=blob=>{const url='blob:'+base+'/synthetic-'+(blobs.size+1);blobs.set(url,blob);return url;};
  dom.window.URL.revokeObjectURL=url=>blobs.delete(url);
  const anchorClick=dom.window.HTMLAnchorElement.prototype.click;
  dom.window.HTMLAnchorElement.prototype.click=function(){if(this.href.startsWith('blob:')){control.downloads.push({filename:this.download,blob:blobs.get(this.href)});return;}return anchorClick.call(this);};
  control.finishText=({cancelled=false,fragmented=false}={})=>{
    const job=control.textJob, asset={id:'qa-asset-'+control.textRequests.length,snapshot_id:job.snapshot_id,book_id:job.book_id,title:job.title,platform:job.platform,scope:'第 1—3 章；实际取得第 1、3 章，缺失第 2 章',chapters:[{index:1,title:'第一章'},{index:3,title:'第三章'}],missing:[{index:2,chapter:'第二章',reason:'平台暂未提供正文'}],coverage_complete:false,complete:false,cancelled,characters:66,created_at:'2026-11-09T12:00:00Z',acquisition:'public_download',integrity:{version:1,status:'verified',actual_characters:66,declared_book_word_count:99,reason:'已核对取得章节字数。'}};
    if(fragmented)Object.assign(asset,{scope:'公开片段；目录第 1 项，共 1 章',chapters:[{index:1,title:'第一章',integrity:{version:1,status:'partial',declared_word_count:13325,actual_characters:4439,reason:'章节正文少于标注字数。'}}],missing:[],characters:4439,complete:true,coverage_complete:true,integrity:{version:1,status:'partial',actual_characters:4439,declared_book_word_count:13325,reason:'单章正文少于平台标注字数，已保存公开片段。'}});
    control.textAssets.unshift(asset);control.textJob={...job,status:cancelled?'cancelled':'completed',message:cancelled?'已暂停，已取得内容已保存':fragmented?'公开片段已保存，实际正文 4,439 字符':'正文已保存，缺失 1 章',partial:true,finished_at:'2026-11-09T12:00:00Z',text_id:asset.id,text:asset};
  };
  dom.window.fetch=async(path,init={})=>{
    assert.equal(new URL(path,base).origin,new URL(base).origin,'Frontend requested a nonlocal URL');
    const pathname=new URL(path,base).pathname;
    if(pathname.startsWith('/api/') && !pathname.startsWith('/api/membership/')){
      assert.equal(init.headers?.['X-Fagao-Account'],identity.account.id,'Protected UI request lacks account binding');
      assert.equal(init.headers?.['X-Fagao-Generation'],identity.generation,'Protected UI request lacks session binding');
    }
    if(pathname==='/api/view' && control.viewPending && !control.viewPending.captured){const held=control.viewPending;held.captured=true;await held.promise;}
    if(pathname==='/api/overview/refresh'){overviewUpdates.push(JSON.parse(init.body));return Response.json({status:'fresh',retry_after_seconds:900});}
    if(pathname==='/api/jobs/qa-overview-late'){control.overviewPolls++;return Response.json(control.overviewJob);}
    if(pathname==='/api/collect'){
      if(control.collectionPending && !control.collectionPending.captured){const held=control.collectionPending;held.captured=true;await held.promise;}
      const body=JSON.parse(init.body);collections.push(body);
      if(collectionFixture){control.collectionJob={id:'qa-collection',platform:'fanqie',kind:'collection',options:body.options,status:'running',automatic:body.automatic,interactive:true,partial_snapshot_id:collectionFixture.first,partial_count:10,demand_count:10,waiting_for_more:true,message:'已读取10本，向下浏览时继续'};return Response.json({status:'started',job_id:control.collectionJob.id,job:control.collectionJob});}
      if(collectionResponse?.auth)mockAuth=collectionResponse.auth;
      return Response.json(collectionResponse || {status:'fresh',snapshot_id:freshSnapshot,retry_after_seconds:900});
    }
    if(pathname==='/api/jobs/qa-collection'){control.collectionPolls++;return Response.json(control.collectionJob);}
    if(pathname==='/api/jobs/more' && collectionFixture){control.moreRequests.push(JSON.parse(init.body));control.collectionJob={...control.collectionJob,waiting_for_more:false,demand_count:110};return Response.json({status:'continued',job_id:control.collectionJob.id,job:control.collectionJob});}
    if(pathname==='/api/catalog'){
      if(!contentFixture)throw new Error('Catalog must be intercepted in UI tests.');
      const body=JSON.parse(init.body);control.catalogRequests.push(body);
      const total=contentOptions.total ?? 12;
      return Response.json({...body,platform:contentOptions.platform || 'fanqie',title:'测试作品乙',total,catalog_chapters:total,...(contentOptions.platform?{download_limit:10,download_start:1}:{}),collected_at:'2026-11-09T12:00:00Z',source_url:'https://example.invalid/catalog-fixture',chapters:Array.from({length:contentOptions.chapterCount ?? total},(_,i)=>({index:i+1,item_id:'chapter-'+(i+1),title:'第 '+(i+1)+' 章'+(i===0?' <script>unsafe</script>':''),is_locked:i===6,unavailable_reason:i===6?'此章节需要平台权限':''}))});
    }
    if(pathname==='/api/fetch-text'){
      if(!contentFixture)throw new Error('Text downloads must be intercepted in UI tests.');
      const body=JSON.parse(init.body);control.textRequests.push(body);
      control.textJob={id:'qa-text-'+control.textRequests.length,kind:'text',platform:contentOptions.platform || 'fanqie',snapshot_id:body.snapshot_id,book_id:body.book_id,title:contentOptions.title || (body.book_id==='qa-b'?'测试作品乙':'测试作品甲'),options:body,status:'pending',message:'正在读取选定作品的正文'};
      return Response.json({status:'started',job_id:control.textJob.id,job:control.textJob});
    }
    if(pathname.startsWith('/api/jobs/qa-text-')){control.textPolls++;return Response.json(control.textJob);}
    if(pathname==='/api/jobs/cancel' && contentFixture){control.cancelCalls++;control.textJob={...control.textJob,status:'cancelling',message:'正在暂停正文下载，已取得内容将保留。'};return Response.json(control.textJob);}
    if(pathname.startsWith('/api/texts/qa-asset-')){
      if(pathname.endsWith('/download')){control.downloadRequests.push({path:pathname,headers:init.headers});return new Response('这是实际取得部分正文的 TXT 测试夹具。',{headers:{'Content-Type':'text/plain; charset=utf-8'}});}
      return Response.json({...control.textAssets.find(item=>pathname.endsWith('/'+item.id)),content:'这是实际取得部分正文的测试夹具。<script>unsafe()</script>'});
    }
    if(pathname==='/api/auth')throw new Error('UI smoke must not start a platform login or browser.');
    const response=await localFetch(path,init);
    if(pathname==='/api/overview' && lateOverview){control.overviewReads++;const data=await response.json();data.update=control.overviewJob;return Response.json(data);}
    if(pathname==='/api/state' || pathname==='/api/view'){
      if(pathname==='/api/state')control.stateRequests++;
      const data=await response.json();if(control.collectionJob)data.jobs=[control.collectionJob,...(data.jobs || [])];if(contentFixture){data.jobs=[...(control.textJob?[control.textJob]:[]),...(data.jobs || [])];data.texts=[...control.textAssets,...(data.texts || [])];}if(stale)data.snapshots.forEach(item=>{item.collected_at='2020-01-01T00:00:00Z';});if(expiredHeiyan)data.auth={...data.auth,heiyan:{state:'expired',message:'测试登录已过期'}};if(forceConnectedHeiyan)data.auth={...data.auth,heiyan:mockAuth || {state:'connected'}};
      if(pathname==='/api/state' && control.pending && !control.pending.captured){const pending=control.pending;pending.captured=true;await pending.promise;}
      return Response.json(data,{status:response.status});
    }
    if(pathname.startsWith('/api/snapshots/')){
      const data=await response.json();
      data.pagination={pages_fetched:3,max_pages:100,stop_reason:'empty_page'};
      if(data.books?.[0]){data.books[0].cover_url='https://example.invalid/fixture-cover.jpg';data.books[0].status='连载中';}
      if(data.books?.[1])data.books[1].cover_url='javascript:alert(1)';
      return Response.json(data,{status:response.status});
    }
    return response;
  };
  dom.window.HTMLDialogElement.prototype.showModal=function(){this.open=true};
  dom.window.HTMLDialogElement.prototype.close=function(){this.open=false};
  const preference=name=>`${name}:${encodeURIComponent(identity.account.id)}`;
  if(history){const s=await api('/api/state');dom.window.localStorage.setItem(preference('fagao-history-views'),JSON.stringify({long:s.views.long.snapshot_id}));}
  if(storedOptions){dom.window.localStorage.setItem(preference('fagao-collection-options'),JSON.stringify(storedOptions));dom.window.localStorage.setItem(preference('fagao-options-version'),'2');}
  dom.window.eval(fs.readFileSync('web/app.js','utf8'));
  await wait(()=>dom.window.document.querySelector('.overview-tabs'),'homepage did not render');
  await wait(()=>!dom.window.document.querySelector('.overview-collection-status')?.textContent.includes('正在读取本机'),'homepage did not finish loading');
  if(startHome){await pause(100);return dom;}
  const initialState=await api('/api/state');dom.window.document.querySelector(`[data-action="mode"][data-mode="${initialState.active_mode}"]`).click();
  await wait(()=>dom.window.document.querySelector('[data-action="select-book"]') || dom.window.document.querySelector('#heiyan-booklist'),'books did not render');await pause(100);return dom;
}
(async()=>{
 let favoriteChecks=0,openingChecks=0;
 const favoriteCheck=check=>{check();favoriteChecks++;},openingCheck=check=>{check();openingChecks++;};
 let dom=await open({startHome:true}), d=dom.window.document;
 assert(d.querySelector('.overview-tabs'),'homepage overview missing');assert.equal(d.querySelector('[data-section="overview"]').getAttribute('aria-current'),'true');assert.equal(d.querySelectorAll('.overview-tabs button').length,3);assert.match(d.querySelector('.overview-scope').textContent,/仅新书榜/);assert.equal(collections.length,0,'homepage started category-specific scan');await wait(()=>overviewUpdates.length>0,'homepage did not request aggregate update');assert.equal(overviewUpdates[0].automatic,true);dom.window.close();
 dom=await open();d=dom.window.document;
 await wait(()=>collections.length===1,'recent limited snapshot wrongly suppressed automatic scope check');assert.equal(collections[0].automatic,true);assert.equal(collections[0].options.limit,10000);
 assert.equal(d.querySelector('#main-site').href,'https://www.fagaochuangzuo.com/');assert(d.querySelector('#points-entry button').disabled);
 const buttons=d.querySelectorAll('[data-action="select-book"]');assert.equal(buttons.length,3);
 assert(buttons[0].querySelector('img.cover-image'),'real cover not rendered');
 assert.equal(buttons[1].querySelector('img.cover-image'),null,'unsafe cover URL rendered');
 const cover=buttons[0].querySelector('img');cover.dispatchEvent(new dom.window.Event('error'));assert(cover.hidden,'failed cover must fall back to book title');
 assert.match(buttons[0].querySelector('.cover-fallback').textContent,/测试作品甲/);
 assert(buttons[0].querySelector('.row-summary'),'list summary missing');assert.match(buttons[0].querySelector('.book-meta').textContent,/连载中/);
 assert.match(d.querySelector('.pagination-summary').textContent,/已读取 3 页/);assert.match(d.querySelector('.pagination-summary').textContent,/最多 100 页/);assert.match(d.querySelector('.pagination-summary').textContent,/平台已无更多书目/);
 assert(d.querySelector('#board-channel') && d.querySelector('#board-kind') && d.querySelector('#board-category'),'visible board controls missing');
 buttons[1].click();
 await wait(()=>d.querySelector('.detail h2')?.textContent==='测试作品乙','select failed');
 assert.match(d.querySelector('.positions').textContent,/原榜第 14 名/);
 let r=await api('/api/resolve',{query:'分析第二本'});assert.equal(r.book_id,'qa-b');
 favoriteCheck(()=>{assert.match(d.querySelector('.detail .text-status').textContent,/先点右上角“收藏”/);assert.equal(d.querySelector('.bridge'),null,'unfavorited book claimed ready for WorkBuddy');});
 d.querySelector('[data-action="favorite"]').click();await wait(()=>d.querySelector('#favorite-count').textContent==='1','favorite failed');
 favoriteCheck(()=>{assert.match(d.querySelector('.detail .text-status').textContent,/已加入我的收藏，目前只有书目和简介/);assert.match(d.querySelector('.bridge').textContent,/分析我收藏的番茄小说《测试作品乙》书名和简介/);assert(!d.querySelector('.bridge').textContent.includes('开头'),'metadata-only favorite promised opening analysis');});
 r=await api('/api/resolve',{query:d.querySelector('.bridge-main p').textContent});favoriteCheck(()=>{assert.equal(r.book_id,'qa-b');assert.equal(r.book.title,'测试作品乙');});
 d.querySelector('[data-action="favorite"]').click();await wait(()=>d.querySelector('#favorite-count').textContent==='0','unfavorite failed');
 favoriteCheck(()=>{assert.match(d.querySelector('.detail .text-status').textContent,/先点右上角“收藏”/);assert.equal(d.querySelector('.bridge'),null,'unfavorited book retained saved-analysis prompt');});
 d.querySelector('[data-action="favorite"]').click();await wait(()=>d.querySelector('#favorite-count').textContent==='1','re-favorite failed');
 d.querySelector('#sort').value='rank_desc';d.querySelector('#sort').dispatchEvent(new dom.window.Event('change',{bubbles:true}));
 await wait(async()=>((await api('/api/state')).views.long.filters.sort==='rank_desc'),'sort not saved');
 r=await api('/api/resolve',{query:'番茄新书榜第14名'});assert.equal(r.book_id,'qa-b');
 const q=d.querySelector('#query');q.value='独有简介';q.dispatchEvent(new dom.window.Event('input',{bubbles:true}));
 await wait(()=>d.querySelectorAll('[data-action="select-book"]').length===1,'summary filter failed');
 d.querySelector('[data-mode="short"]').click();await wait(()=>d.querySelector('#heiyan-booklist'),'heiyan sheet entry missing');assert.equal(d.querySelector('#heiyan-booklist').href,'https://docs.qq.com/sheet/DYVNwaVJZV2ZxTWl3?tab=q1ans7');assert.equal(d.querySelector('#heiyan-booklist').target,'_blank');assert(!d.querySelector('[data-action="login"]'),'old backend login still exposed');
 assert(!d.querySelector('.positions'),'library cannot inherit rank');
 d.querySelector('[data-mode="long"]').click();await wait(()=>d.querySelector('.detail h2')?.textContent==='测试作品乙','long state not restored');
 dom.window.close();dom=await open();d=dom.window.document;
 assert.equal(d.querySelectorAll('[data-action="select-book"]').length,1);assert.equal(d.querySelector('.detail h2').textContent,'测试作品乙');assert.equal(d.querySelector('#favorite-count').textContent,'1');
 d.querySelector('[data-section="history"]').click();assert.match(d.querySelector('#content').textContent,/测试作品乙/);
 d.querySelector('[data-section="reports"]').click();await wait(()=>d.querySelector('[data-action="report"]'),'report not listed');d.querySelector('[data-action="report"]').click();await wait(()=>d.querySelector('.report-text'),'report not opened');
 assert.match(d.querySelector('.report-text').textContent,/仅简介/);assert(!d.querySelector('#dialog script'),'unsafe report markup');dom.window.close();
 // A late background /state response must not overwrite a newer book selection.
 const current=await api('/api/state');
 await api('/api/view',{mode:'long',snapshot_id:current.views.long.snapshot_id,selected_id:'qa-b',filters:{query:'',sort:'source',favorites:false}});
 dom=await open();d=dom.window.document;await pause(100);
 const held=dom.control.holdNextState();d.dispatchEvent(new dom.window.Event('visibilitychange'));
 await wait(()=>held.captured,'background refresh was not captured');
 d.querySelectorAll('.book-row')[0].click();
 await wait(async()=>((await api('/api/state')).views.long.selected_id==='qa-a'),'new selection not saved');
 await wait(()=>d.querySelector('.detail h2')?.textContent==='测试作品甲','new selection not rendered');
 held.release();await pause(100);
 assert.equal(d.querySelector('.detail h2').textContent,'测试作品甲','late state replaced newer selection');dom.window.close();
 await api('/api/view',{mode:'long',selected_id:'qa-b'});
 // A refresh that begins during a pending selection waits for the mutation.
 dom=await open();d=dom.window.document;await pause(100);
 const heldView=dom.control.holdNextView();d.querySelectorAll('.book-row')[0].click();
 await wait(()=>heldView.captured,'selection request did not enter pending state');
 const stateRequests=dom.control.stateRequests;d.dispatchEvent(new dom.window.Event('visibilitychange'));await pause(70);
 assert.equal(dom.control.stateRequests,stateRequests,'refresh read stale state during a pending selection');
 heldView.release();await wait(()=>d.querySelector('.detail h2')?.textContent==='测试作品甲','pending selection not restored');await pause(70);
 assert.equal(d.querySelector('.detail h2').textContent,'测试作品甲');dom.window.close();
 await api('/api/view',{mode:'long',selected_id:'qa-b'});
 // New data exists, but auto refresh must not replace a previously selected work.
 const s=await api('/api/state'), original=s.views.long.snapshot_id;
 const next=await api('/api/import/snapshot',{snapshot:{platform:'fanqie',channel:'女频',category:'古言脑洞',board:'新书榜',books:[{id:'qa-new',title:'更新后的测试作品',rank:1,summary:'测试新榜单，不是真实采集'}]}});
 const before=collections.length;dom=await open({stale:true,freshSnapshot:next.id});d=dom.window.document;
 await wait(()=>collections.length===before+1,'stale view did not automatically request an update');
 assert.equal(collections.at(-1).automatic,true);assert.equal(collections.at(-1).options.limit,10000);assert.equal(collections.at(-1).options.max_pages,100);
 await wait(()=>d.querySelector('[data-action="show-latest"]'),'new data prompt missing');
 assert.equal(d.querySelector('.detail h2').textContent,'测试作品乙','auto refresh replaced selected book');
 assert.equal((await api('/api/state')).views.long.snapshot_id,original,'selected snapshot was changed');
 const count=collections.length;await pause(100);assert.equal(collections.length,count,'automatic update was not throttled');dom.window.close();
 dom=await open({stale:true,freshSnapshot:next.id,history:true});d=dom.window.document;
 await pause(120);assert.equal(collections.length,count,'historical view auto-updated');assert.match(d.querySelector('.auto-status').textContent,/暂停/);dom.window.close();
 // An automatic snapshot arriving while selection is in flight must await it.
 await api('/api/view',{mode:'long',snapshot_id:original,selected_id:null,filters:{query:'',sort:'source',favorites:false}});
 dom=await open({stale:true,freshSnapshot:next.id,holdCollection:true});d=dom.window.document;
 await wait(()=>dom.control.collectionPending.captured,'automatic request not held');
 const selection=dom.control.holdNextView();d.querySelectorAll('.book-row')[1].click();await wait(()=>selection.captured,'selection not held');
 dom.control.collectionPending.release();await pause(80);
 assert.equal((await api('/api/state')).views.long.snapshot_id,original,'automatic result replaced pending selection before save');
 selection.release();await wait(()=>d.querySelector('.detail h2')?.textContent==='测试作品乙','pending selected book lost');
 await wait(()=>d.querySelector('[data-action="show-latest"]'),'pending selection should produce latest-data prompt');
 assert.equal((await api('/api/state')).views.long.snapshot_id,original);assert.equal((await api('/api/state')).views.long.selected_id,'qa-b');dom.window.close();
 const beforeLibrary=collections.length;
 const oldLibrary=await api('/api/import/snapshot',{snapshot:{platform:'heiyan',channel:'全部',category:'书库',board:'书库',books:[{id:'hy-fixture',title:'登录过期时保留的测试书目',summary:'隔离测试'}]}});
 await api('/api/view',{mode:'short',platform:'heiyan',snapshot_id:oldLibrary.id,selected_id:null,filters:{query:'',sort:'source',favorites:false}});
 dom=await open({expiredHeiyan:true});d=dom.window.document;
 assert(d.querySelector('#heiyan-booklist'),'saved book list must retain TencentDocs entry');assert(!d.querySelector('.reconnect-card'),'old backend dependency/login exposed');assert.equal(d.querySelectorAll('.book-row').length,1);assert.equal(collections.length,beforeLibrary,'Heiyan should not auto collect');dom.window.close();
 dom=await open({forceConnectedHeiyan:true,collectionResponse:{status:'cooldown',requires_login:true,auth:{state:'expired',message:'采集时发现登录已过期'},retry_after_seconds:300}});d=dom.window.document;
 assert(d.querySelector('#heiyan-booklist'),'connected old auth changed primary booklist');assert.equal(collections.length,beforeLibrary,'old connected auth triggered hidden browser');assert.equal(d.querySelectorAll('.book-row').length,1);dom.window.close();
 // Catalogs and chapter text remain fixed to the selected book, independent of collection.
 await api('/api/view',{mode:'long',platform:'fanqie',snapshot_id:original,selected_id:'qa-b',filters:{query:'',sort:'source',favorites:false}});
 dom=await open({contentFixture:true});d=dom.window.document;
 d.querySelector('[data-action="catalog"]').click();await wait(()=>d.querySelectorAll('.chapter-row').length===12,'catalog was not rendered');
 assert.deepEqual(dom.control.catalogRequests[0],{snapshot_id:original,book_id:'qa-b'});
 assert.equal(dom.control.textRequests.length,0,'opening catalog started downloading');
 assert.equal(d.querySelector('#chapter-range-mode').value,'first');assert.equal(d.querySelector('#chapter-count').value,'3');assert(!d.querySelector('#dialog script'),'catalog escaped markup failed');
 d.querySelector('[data-count="10"]').click();assert.equal(d.querySelector('#chapter-count').value,'10');
 d.querySelector('[data-action="chapter-all"]').click();assert.equal(d.querySelector('#chapter-range-mode').value,'all');assert.match(d.querySelector('.chapter-scope-note').textContent,/连载作品/);
 d.querySelector('[data-action="choose-chapter"][data-index="5"]').click();assert.equal(d.querySelector('#chapter-start').value,'5');
 d.querySelector('#chapter-end').value='3';d.querySelector('#chapter-download-form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
 assert.equal(dom.control.textRequests.length,0,'invalid chapter range was sent');assert.match(d.querySelector('.chapter-error').textContent,/结束章/);
 d.querySelector('[data-count="3"]').click();d.querySelector('#chapter-download-form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
 await wait(()=>dom.control.textRequests.length===1 && d.querySelector('.text-job'),'text task did not start');
 assert.deepEqual(dom.control.textRequests[0],{snapshot_id:original,book_id:'qa-b',start_chapter:1,chapters:3});
 assert(!d.querySelector('.job-strip'),'text job leaked into collection progress');assert(d.querySelector('[data-action="collect"]') && !d.querySelector('[data-action="collect"]').disabled,'text job blocked collection');
 d.querySelector('[data-action="close-dialog"]').click();d.querySelectorAll('.book-row')[0].click();await wait(()=>d.querySelector('.detail h2')?.textContent==='测试作品甲','switching work during text job failed');
 dom.control.finishText();await wait(()=>dom.control.textPolls>0,'background text task was not polled');await pause(120);
 assert.equal(d.querySelector('.detail h2').textContent,'测试作品甲','text completion replaced selected book');assert(!d.querySelector('.saved-text-assets'),'text asset attached to wrong book');
 d.querySelectorAll('.book-row')[1].click();await wait(()=>d.querySelector('.saved-text-assets'),'saved text did not attach to original work');
 assert.match(d.querySelector('.saved-text-assets').textContent,/实际取得/);
 favoriteCheck(()=>{assert.match(d.querySelector('.bridge').textContent,/分析我收藏的番茄小说《测试作品乙》已保存的正文，按实际保存的范围分析/);assert.match(d.querySelector('.detail .text-status').textContent,/已保存的正文/);});
 const firstDownload=d.querySelector('.saved-text-assets [data-action="download-text"]');assert.equal(firstDownload.dataset.id,'qa-asset-1');firstDownload.click();
 await wait(()=>dom.control.downloads.length===1,'Saved TXT was not downloaded through the authenticated API');assert.match(dom.control.downloadRequests[0].path,/\/api\/texts\/qa-asset-1\/download$/);assert.match(await dom.control.downloads[0].blob.text(),/实际取得部分正文/);assert.match(dom.control.downloads[0].filename,/qa-asset-1\.txt$/);
 d.querySelector('[data-action="catalog"]').click();await wait(()=>d.querySelector('.chapter-saved-result') && d.querySelector('#chapter-download-form'),'saved result not visible');
 assert.match(d.querySelector('.chapter-saved-meta').textContent,/实际取得 2 章/);assert.match(d.querySelector('.chapter-saved-meta').textContent,/缺失 1 章/);assert(!d.querySelector('.chapter-saved-meta').textContent.includes('完整正文'),'partial result claimed full text');
 d.querySelector('[data-action="chapter-all"]').click();d.querySelector('#chapter-download-form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await wait(()=>dom.control.textRequests.length===2 && d.querySelector('[data-action="cancel-text-job"]'),'explicit full catalog task not started');
 assert.deepEqual(dom.control.textRequests[1],{snapshot_id:original,book_id:'qa-b',all:true});
 d.querySelector('[data-action="cancel-text-job"]').click();await wait(()=>dom.control.cancelCalls===1 && d.querySelector('[data-action="cancel-text-job"]')?.disabled,'cancel did not enter finalizing state');
 const textPolls=dom.control.textPolls;await wait(()=>dom.control.textPolls>textPolls,'cancelling task stopped polling before saving content');
 dom.control.finishText({cancelled:true});await wait(()=>d.querySelector('.text-job')?.textContent.includes('已暂停，已取得内容已保存'),'cancelled partial text not displayed');
 const secondDownload=d.querySelector('.chapter-result-actions [data-action="download-text"]');assert.equal(secondDownload.dataset.id,'qa-asset-2');secondDownload.click();await wait(()=>dom.control.downloads.length===2,'Partial TXT result was not downloaded');assert.match(dom.control.downloadRequests[1].path,/qa-asset-2\/download$/);assert.match(await dom.control.downloads[1].blob.text(),/实际取得部分正文/);
 d.querySelector('.chapter-result-actions [data-action="preview-text"]').click();await wait(()=>d.querySelector('.saved-text-preview'),'saved text preview not opened');assert.match(d.querySelector('.saved-text-preview').textContent,/<script>/);assert(!d.querySelector('#dialog script'),'saved text executed HTML');
 // A single returned directory item may contain only its opening. Even stale
 // complete flags must not override the explicit body-length evidence.
 d.querySelector('[data-action="close-dialog"]').click();d.querySelector('[data-action="catalog"]').click();await wait(()=>d.querySelector('.chapter-row'),'fragment regression catalog missing');
 d.querySelector('[data-action="choose-chapter"][data-index="1"]').click();d.querySelector('#chapter-download-form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await wait(()=>dom.control.textRequests.length===3 && d.querySelector('.text-job'),'fragment regression task missing');
 dom.control.finishText({fragmented:true});await wait(()=>d.querySelector('.chapter-integrity')?.textContent.includes('13,325'),'single chapter integrity evidence missing');
 assert.match(d.querySelector('.chapter-saved-meta').textContent,/公开片段/);assert.match(d.querySelector('.chapter-saved-meta').textContent,/片段涉及 1 章/);assert(!d.querySelector('.chapter-saved-meta').textContent.includes('完整正文'),'single chapter fragment claimed full text');assert.match(d.querySelector('.chapter-integrity').textContent,/实际正文 4,439 字符（不含空白）/);
 assert.match(d.querySelector('.saved-text-assets').textContent,/公开片段/);assert.match(d.querySelector('.saved-text-assets').textContent,/13,325/);
 d.querySelector('.chapter-result-actions [data-action="preview-text"]').click();await wait(()=>d.querySelector('.saved-text-preview'),'fragment preview missing');assert.match(d.querySelector('.chapter-integrity').textContent,/公开片段/);assert.match(d.querySelector('.chapter-integrity').textContent,/4,439/);
 // Migrated assets count their saved headings because the original chapter
 // bodies cannot be reconstructed. That count must keep its actual basis.
 Object.assign(dom.control.textAssets[0],{scope:'旧下载文本，完整性待重新核验',complete:false,coverage_complete:false,integrity:{version:1,status:'unverified',actual_characters:4439,actual_characters_basis:'saved_text_including_headings',declared_book_word_count:null,reason:'旧记录未保存章节字数核验信息。'}});
 d.querySelector('[data-action="close-dialog"]').click();d.querySelector('.saved-text-item [data-action="preview-text"]').click();await wait(()=>d.querySelector('#dialog').open && d.querySelector('.chapter-integrity')?.textContent.includes('含章节标题'),'legacy preview missing');assert.match(d.querySelector('.chapter-integrity').textContent,/已保存文本 4,439 字符（不含空白，含章节标题）/);assert(!d.querySelector('.chapter-integrity').textContent.includes('实际正文'),'legacy stored text count claimed to exclude chapter headings');dom.window.close();
 // Both new readers expose only the opening. Even forged form controls cannot
 // request chapter 11, a later starting chapter, or a whole book.
 const openingSnapshots={};
 for(const platform of ['qidian','ciweimao'])openingSnapshots[platform]=await api('/api/import/snapshot',{snapshot:{platform,channel:platform==='qidian'?'男频':'综合',category:'全部',board:'月票榜',books:[{id:'qa-b',title:platform+' 测试作品',rank:1,summary:'仅用于前十章与收藏流程验收'},{id:'qa-a',title:platform+' 另一作品',rank:2,summary:'固定对象验收'}]}});
 for(const platform of ['qidian','ciweimao']){
   const pinned=openingSnapshots[platform],other=platform==='qidian'?'ciweimao':'qidian';
   await api('/api/view',{mode:'long',platform,snapshot_id:pinned.id,selected_id:'qa-b',filters:{query:'',sort:'source',favorites:false}});
   dom=await open({contentFixture:{platform,total:20,chapterCount:12,title:platform+' 测试作品'}});d=dom.window.document;
   openingCheck(()=>{assert.match(d.querySelector('.detail .text-status').textContent,/先点右上角“收藏”/);assert.equal(d.querySelector('.bridge'),null,'same ID on a different platform inherited a favorite');assert(d.querySelector('[data-action="catalog"]'),'new platform lacks catalog entry');});
   d.querySelector('[data-action="favorite"]').click();await wait(()=>d.querySelector('[data-action="favorite"]')?.getAttribute('aria-pressed')==='true','new platform favorite not saved');
   openingCheck(()=>{assert.match(d.querySelector('.detail .text-status').textContent,/目前只有书目和简介/);assert(d.querySelector('.bridge').textContent.includes(`《${platform} 测试作品》书名和简介`));});
   const favoriteRef=await api('/api/resolve',{query:d.querySelector('.bridge-main p').textContent});openingCheck(()=>{assert.equal(favoriteRef.book.title,platform+' 测试作品');assert.equal(favoriteRef.snapshot_id,pinned.id);assert.equal(favoriteRef.book_id,'qa-b');});
   d.querySelector('[data-action="catalog"]').click();await wait(()=>d.querySelectorAll('.chapter-row').length===10,'new platform did not cap directory at ten');
   openingCheck(()=>{assert.deepEqual(dom.control.catalogRequests[0],{snapshot_id:pinned.id,book_id:'qa-b'});assert.equal(dom.control.textRequests.length,0);assert.equal(d.querySelector('[data-action="chapter-all"]'),null);assert.equal(d.querySelector('[data-count="30"]'),null);assert.equal(d.querySelector('#chapter-start'),null);assert.equal(d.querySelector('#chapter-range-mode'),null);assert.equal(d.querySelector('[data-action="choose-chapter"]'),null);assert.match(d.querySelector('.chapter-scope-note').textContent,/不会自动购买/);assert.match(d.querySelector('.chapter-list').textContent,/此章节需要平台权限/);assert(!d.querySelector('#dialog script'));});
   for(const [id,value] of [['chapter-count','11'],['chapter-start','2'],['chapter-range-mode','all']]){
     const forged=d.createElement('input');forged.id=id;forged.value=value;d.querySelector('#chapter-download-form').append(forged);
     d.querySelector('#chapter-download-form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));
     openingCheck(()=>{assert.equal(dom.control.textRequests.length,0,`${platform} accepted forged ${id}`);assert.match(d.querySelector('.chapter-error').textContent,/最多读取前 10 章/);});
     const requests=dom.control.catalogRequests.length;d.querySelector('[data-action="reload-catalog"]').click();await wait(()=>dom.control.catalogRequests.length>requests && !d.querySelector('.chapter-loading'),'limited catalog did not reset');
   }
   for(const action of ['chapter-all','choose-chapter','chapter-preset']){
     const forged=d.createElement('button');forged.type='button';forged.dataset.action=action;forged.dataset.index='11';forged.dataset.count='30';d.querySelector('#chapter-download-form').append(forged);forged.click();
     openingCheck(()=>{assert.equal(dom.control.textRequests.length,0,`${platform} accepted forged ${action}`);assert.match(d.querySelector('.chapter-error').textContent,/最多读取前 10 章/);});
   }
   d.querySelector('#chapter-download-form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await wait(()=>dom.control.textRequests.length===1 && d.querySelector('.text-job'),'opening download did not start');
   openingCheck(()=>assert.deepEqual(dom.control.textRequests[0],{snapshot_id:pinned.id,book_id:'qa-b',start_chapter:1,chapters:10}));
   d.querySelector('[data-action="close-dialog"]').click();d.querySelector(`[data-action="platform"][data-platform="${other}"]`).click();await wait(()=>d.querySelector(`[data-action="platform"][data-platform="${other}"]`)?.getAttribute('aria-current')==='true' && d.querySelector('.book-row'),'cross-platform navigation did not finish');
   d.querySelector('[data-action="select-book"][data-book="qa-b"]').click();await wait(()=>d.querySelector('.detail h2')?.textContent===other+' 测试作品','cross-platform same ID was not selected');
   dom.control.finishText();await wait(()=>dom.control.textPolls>0,'opening task was not polled');await pause(120);
   openingCheck(()=>{assert.equal(d.querySelector('.detail h2').textContent,other+' 测试作品');assert.equal(d.querySelector('.saved-text-assets'),null,'completed text leaked to same book ID on a different platform');});
   d.querySelector(`[data-action="platform"][data-platform="${platform}"]`).click();await wait(()=>d.querySelector(`[data-action="platform"][data-platform="${platform}"]`)?.getAttribute('aria-current')==='true' && d.querySelector('.book-row'),'original platform did not reopen');d.querySelector('[data-action="select-book"][data-book="qa-b"]').click();await wait(()=>d.querySelector('.saved-text-assets'),'opening text did not remain with original platform');
   openingCheck(()=>{assert(d.querySelector('.bridge').textContent.includes(`《${platform} 测试作品》已保存的正文`));assert.match(d.querySelector('.saved-text-assets').textContent,/实际取得/);assert(!d.querySelector('.saved-text-assets').textContent.includes('完整正文'));});
   d.querySelector('[data-action="favorite"]').click();await wait(()=>d.querySelector('[data-action="favorite"]')?.getAttribute('aria-pressed')==='false','opening unfavorite failed');
   openingCheck(()=>{assert.equal(d.querySelector('.bridge'),null);assert.match(d.querySelector('.detail .text-status').textContent,/先点右上角“收藏”/);assert(d.querySelector('.saved-text-assets'),'unfavoriting deleted saved text');});
   d.querySelector('[data-action="catalog"]').click();await wait(()=>d.querySelector('.chapter-saved-result') && d.querySelector('#chapter-download-form') && !d.querySelector('.chapter-loading'),'unfavorited result did not reopen');openingCheck(()=>assert.match(d.querySelector('.chapter-workbuddy-note').textContent,/点右上角“收藏”/));
   dom.window.close();
 }
 await api('/api/view',{mode:'long',platform:'qidian',snapshot_id:openingSnapshots.qidian.id,selected_id:'qa-b',filters:{query:'',sort:'source',favorites:false}});
 dom=await open({contentFixture:{platform:'qidian',total:4}});d=dom.window.document;d.querySelector('[data-action="catalog"]').click();await wait(()=>d.querySelectorAll('.chapter-row').length===4,'short catalog missing');
 openingCheck(()=>assert.match(d.querySelector('#chapter-download-form [type="submit"]').textContent,/读取前 4 章/));d.querySelector('#chapter-download-form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await wait(()=>dom.control.textRequests.length===1 && d.querySelector('.text-job'),'short catalog task missing');await pause(100);openingCheck(()=>assert.deepEqual(dom.control.textRequests[0],{snapshot_id:openingSnapshots.qidian.id,book_id:'qa-b',start_chapter:1,chapters:4}));dom.window.close();
 await api('/api/view',{mode:'long',platform:'fanqie',snapshot_id:original,selected_id:'qa-b',filters:{query:'',sort:'source',favorites:false}});
 // A previously customized collection count survives the default expansion.
 const beforeCustom=collections.length;dom=await open({storedOptions:{fanqie:{limit:75,max_pages:25}}});await wait(()=>collections.length>beforeCustom,'custom collection options did not initialize');assert.equal(collections.at(-1).options.limit,75);assert.equal(collections.at(-1).options.max_pages,25);dom.window.close();
 const beforeMigration=collections.length;dom=await open({storedOptions:{fanqie:{limit:1000,max_pages:100}}});await wait(()=>collections.length>beforeMigration,'previous default did not initialize');assert.equal(collections.at(-1).options.limit,10000);dom.window.close();
 // Observations use the real local statistics API, including truthful empty states.
 dom=await open();d=dom.window.document;d.querySelector('[data-action="insights"]').click();await wait(()=>d.querySelector('.insights-summary'),'observations not rendered');assert.match(d.querySelector('#dialog-body').textContent,/没有同时提供在读/);assert.match(d.querySelector('#dialog-body').textContent,/没有有效的来源数据日期/);assert(!d.querySelector('.insights-table'),'missing metrics were invented');dom.window.close();
 await api('/api/import/snapshot',{snapshot:{platform:'fanqie',channel:'女频',category:'古言脑洞',board:'新书榜',data_date:'2026-11-01',books:[{id:'qa-observe',title:'穿越后，我成为女帝',rank:2,metrics:[{name:'在读',value:100},{name:'字数',value:10000}]}]}});
 const observation=await api('/api/import/snapshot',{snapshot:{platform:'fanqie',channel:'女频',category:'古言脑洞',board:'新书榜',data_date:'2026-11-04',books:[{id:'qa-observe',title:'穿越后，我成为女帝',rank:1,metrics:[{name:'在读',value:150},{name:'字数',value:10000}]}]}});
 await api('/api/view',{mode:'long',platform:'fanqie',snapshot_id:observation.id,selected_id:null});dom=await open();d=dom.window.document;
 d.querySelector('[data-action="insights"]').click();await wait(()=>d.querySelector('.insights-table'),'real metrics table missing');assert.match(d.querySelector('#dialog-body').textContent,/实际间隔 3 天/);assert.match(d.querySelector('#dialog-body').textContent,/\+50/);assert.match(d.querySelector('.insight-row').textContent,/穿越/);assert(!d.querySelector('#dialog-body').textContent.includes('7日增长'),'comparison invented an interval');
 d.querySelector('[data-action="insight-book"]').click();await wait(()=>d.querySelector('.detail h2')?.textContent==='穿越后，我成为女帝','observation reference did not open source work');assert(!d.querySelector('#dialog').open);assert.equal((await api('/api/state')).views.long.snapshot_id,observation.id);dom.window.close();
 // The default overview excludes reading-board books and opens exact source references.
 await api('/api/import/snapshot',{snapshot:{platform:'fanqie',channel:'男频',category:'玄幻脑洞',board:'阅读榜',data_date:'2026-11-09',books:[{id:'qa-excluded-reading',title:'阅读榜不应汇总的样本',rank:1,metrics:[{name:'在读',value:99999999},{name:'字数',value:10000}]}]}});
 dom=await open({startHome:true});d=dom.window.document;
 assert.match(d.querySelector('.overview-scope').textContent,/阅读榜不参与/);assert(!d.querySelector('#content').textContent.includes('阅读榜不应汇总的样本'),'reading board polluted homepage');
 assert(d.querySelector('.compact-bars'),'source-backed title distribution missing');assert(d.querySelector('.scatter-dot'),'numeric source sample missing');assert(!d.querySelector('.formula-card'),'large formula cards still dominate the homepage');
 const reference=d.querySelector('[data-action="overview-book"]'), referencedSnapshot=reference.dataset.snapshot, referencedBook=reference.dataset.book;
 reference.dispatchEvent(new dom.window.MouseEvent('click',{bubbles:true}));await wait(()=>d.querySelector('.detail h2'),'overview sample did not open');const exact=await api('/api/state');assert.equal(exact.views.long.snapshot_id,referencedSnapshot);assert.equal(exact.views.long.selected_id,referencedBook);await pause(80);dom.window.close();
 // A real source first batch is visible before a later network batch; a fixed
 // selection can follow a verified same-job prefix without changing old tasks.
 const firstBooks=Array.from({length:10},(_,i)=>({id:'qa-stream-'+i,title:'逐步加载作品'+i,rank:i+1,summary:'本地测试夹具'}));
 const baseSnapshot={platform:'fanqie',mode:'long',channel:'女频',category:'古言脑洞',board:'新书榜',data_date:'2026-11-09T05:10:00Z',complete:false};
 const firstStream=await api('/api/import/snapshot',{snapshot:{...baseSnapshot,books:firstBooks}});
 await api('/api/view',{mode:'long',platform:'fanqie',snapshot_id:null,selected_id:null,filters:{query:'',sort:'source',favorites:false}});
 dom=await open({collectionFixture:{first:firstStream.id}});d=dom.window.document;
 await wait(()=>d.querySelectorAll('.book-row').length===10,'first network batch did not render before full collection');assert.equal(dom.control.moreRequests.length,0,'first render fetched more without scrolling');assert(!d.querySelector('.job-strip .spinner'),'idle demand gate displayed loading spinner');
 await wait(()=>dom.control.collectionPolls>0,'first partial was not registered to its source job');
 const nextStream=await api('/api/import/snapshot',{snapshot:{...baseSnapshot,books:[...firstBooks,...Array.from({length:30},(_,i)=>({id:'qa-stream-'+(i+10),title:'逐步加载作品'+(i+10),rank:i+11}))]}});
 d.querySelectorAll('.book-row')[1].click();await wait(()=>d.querySelector('.detail h2')?.textContent==='逐步加载作品1','first-batch selection failed');
 const fixedTask=await api('/api/prepare',{query:'分析刚选的这本'});assert.equal(fixedTask.snapshot_id,firstStream.id);assert.equal(fixedTask.book_id,'qa-stream-1');
 d.querySelector('[data-action="load-more"]').click();await wait(()=>dom.control.moreRequests.length===1,'scroll-more did not request next network batch');
 dom.window.dispatchEvent(new dom.window.Event('scroll'));dom.window.dispatchEvent(new dom.window.Event('scroll'));await pause(50);assert.equal(dom.control.moreRequests.length,1,'repeated scroll grew demand before pending batch arrived');
 dom.control.collectionJob={...dom.control.collectionJob,partial_snapshot_id:nextStream.id,partial_count:40,demand_count:40,waiting_for_more:true,message:'已读取40本，向下浏览继续'};
 await wait(()=>d.querySelectorAll('.book-row').length>10,'same-job stream did not append after selection');assert.equal(d.querySelector('.detail h2').textContent,'逐步加载作品1');assert.equal((await api('/api/state')).views.long.selected_id,'qa-stream-1');assert.equal((await api('/api/resolve',{query:'分析刚选的这本'})).book_id,'qa-stream-1');assert.equal((await api('/api/tasks/'+fixedTask.id)).snapshot_id,firstStream.id,'stream append rewrote immutable prepared task');
 await pause(80);dom.window.close();
 // Overview seeding can add snapshots after /state was first read. Platform
 // navigation must fetch current metadata and render cache before network waits.
 await api('/api/view',{mode:'long',platform:'qidian',snapshot_id:null,selected_id:null});
 dom=await open({startHome:true,holdCollection:true});d=dom.window.document;
 const seeded=await api('/api/import/snapshot',{snapshot:{platform:'qidian',mode:'long',channel:'男频',category:'全部',board:'月票榜',books:[{id:'qa-seed',title:'首页后台已缓存的起点作品',rank:1}]}});
 d.querySelector('[data-action="platform"][data-platform="qidian"]').click();await wait(()=>d.querySelector('.book-row'),'same-platform cache was hidden behind network');
 assert.match(d.querySelector('.book-row').textContent,/首页后台已缓存/);await wait(()=>dom.control.collectionPending.captured,'background collection was not held');assert.equal((await api('/api/state')).views.long.snapshot_id,seeded.id);dom.control.collectionPending.release();await pause(150);dom.window.close();
 // Returning home wins over a slower platform-navigation metadata request.
 dom=await open({startHome:true});d=dom.window.document;const lateNavigation=dom.control.holdNextState();d.querySelector('[data-action="platform"][data-platform="fanqie"]').click();await wait(()=>lateNavigation.captured,'platform metadata request was not held');d.querySelector('[data-section="overview"]').click();lateNavigation.release();await pause(120);assert(d.querySelector('.overview-tabs'),'late platform navigation overrode return-home');assert.equal(d.querySelector('[data-section="overview"]').getAttribute('aria-current'),'true');dom.window.close();
 // Chart marks, channel changes and candidate cells are checked against the
 // actual API rather than a mocked dashboard response or decorative fixtures.
 let dashboardChecks=0;
 const dashboardCheck=check=>{check();dashboardChecks++;};
 const metricBook=(id,title,reads,words,rank=1)=>({id,title,rank,metrics:[{name:'在读',value:reads},{name:'字数',value:words}],status:'连载中'});
 const maleScope={platform:'fanqie',channel:'男频',category:'玄幻脑洞',board:'新书榜'};
 const publicScope={...maleScope,source_id:'github:scoful/FanqieRankTracker',provenance:{repo_url:'https://github.com/scoful/FanqieRankTracker',sample_limit:20,metric_precision:'source_display_rounded'}};
 await api('/api/import/snapshot',{snapshot:{...publicScope,data_date:'2026-11-03',books:[{id:'qa-public-growth',title:'公开快照增长样本',metrics:[{name:'在读',value:10000}]}]}});
 const publicSource=await api('/api/import/snapshot',{snapshot:{...publicScope,data_date:'2026-11-10',books:[{id:'qa-public-growth',title:'公开快照增长样本',metrics:[{name:'在读',value:30000}]}]}});
 await api('/api/import/snapshot',{snapshot:{...maleScope,data_date:'2026-11-08',books:[metricBook('qa-male-history','开局：有历史的男频样本',50000,100000),metricBook('qa-male-flat','重生：零增长样本',20000,100000,2)]}});
 const maleSource=await api('/api/import/snapshot',{snapshot:{...maleScope,data_date:'2026-11-10',books:[metricBook('qa-male-history','开局：有历史的男频样本',70000,100000),metricBook('qa-male-missing','系统：没有历史的样本',60000,120000,2),metricBook('qa-male-flat','重生：零增长样本',20000,100000,3),metricBook('qa-zero','零指标有效样本',0,0,4),metricBook('qa-large','大数值样本',10000000,2000000,5)]}});
 const femaleSource=await api('/api/import/snapshot',{snapshot:{platform:'fanqie',channel:'女频',category:'古言脑洞',board:'新书榜',data_date:'2026-11-10',books:[metricBook('qa-female-eight','穿越后：八万字样本',20000,80000),metricBook('qa-female-fifteen','女帝：十五万字样本',10000,150000,2)]}});
 await api('/api/import/snapshot',{snapshot:{platform:'qidian',channel:'男频',category:'全部',board:'月票榜',data_date:'2026-11-10',books:[{id:'qa-ticket',title:'起点月票样本',rank:1,metrics:[{name:'月票',value:50},{name:'字数',value:300000}]}]}});
 await api('/api/import/snapshot',{snapshot:{platform:'ciweimao',channel:'综合',category:'全部',board:'月票榜',period:'月榜',data_date:'2026-11-10',books:[{id:'qa-no-ticket',title:'穿越：来源无票数的样本',rank:1}]}});
 const dashboardResponse=await api('/api/overview'), fanqieDashboard=dashboardResponse.platforms.find(item=>item.platform==='fanqie').dashboard;
 dom=await open({startHome:true});d=dom.window.document;
 const kpiValue=name=>d.querySelector(`[data-kpi="${name}"] strong`).childNodes[0].textContent.replaceAll(',','');
 dashboardCheck(()=>assert.equal(Number(kpiValue('books')),fanqieDashboard.views['全部'].kpis.books));
 dashboardCheck(()=>assert.equal(d.querySelectorAll('.dashboard-chart-grid > .data-card').length,4));
 dashboardCheck(()=>{assert(d.querySelector('.scatter-chart'));assert.equal(d.querySelectorAll('.formula-card').length,0);assert(!d.querySelector('.overview-coverage').open);});
 d.querySelector('[data-action="dashboard-channel"][data-channel="男频"]').click();
 const maleView=fanqieDashboard.views['男频'];
 dashboardCheck(()=>{assert.equal(Number(kpiValue('books')),maleView.kpis.books);assert.equal(d.querySelector('[data-channel="男频"]').getAttribute('aria-pressed'),'true');});
 dashboardCheck(()=>{assert.equal(d.querySelectorAll('.scatter-dot').length,maleView.scatter.points.length);assert.equal(d.querySelectorAll('.scatter-dot.female').length,0);});
 dashboardCheck(()=>{const zero=d.querySelector('.scatter-dot[data-book="qa-zero"]');assert.equal(zero.dataset.words,'0');assert.equal(zero.dataset.primary,'0');assert.match(zero.getAttribute('aria-label'),/在读 0人/);});
 dashboardCheck(()=>{assert.match(d.querySelector('.scatter-card').textContent,/对数\(1\+数值\)/);for(const point of d.querySelectorAll('.scatter-dot')){assert(Number.isFinite(Number(point.getAttribute('cx'))));assert(Number.isFinite(Number(point.getAttribute('cy'))));}});
 dashboardCheck(()=>{const hist=d.querySelector('.histogram[aria-label^="字数分布"]');assert.deepEqual([...hist.querySelectorAll('[data-count]')].map(item=>Number(item.dataset.count)),maleView.word_bins.map(item=>item.count));assert.match(hist.textContent,/0 本/);});
 dashboardCheck(()=>{const hist=d.querySelector('.histogram[aria-label^="在读分布"]');assert.deepEqual([...hist.querySelectorAll('[data-count]')].map(item=>Number(item.dataset.count)),maleView.primary_bins.map(item=>item.count));});
 d.querySelector('[data-action="dashboard-category-metric"][data-metric="primary_median"]').click();
 dashboardCheck(()=>{assert.match(d.querySelector('.category-bar-chart').getAttribute('aria-label'),/中位在读/);assert.match(d.querySelector('.category-bar-value').textContent,/6万/);});
 dashboardCheck(()=>{const row=d.querySelector('.radar-book[data-book="qa-male-history"]').closest('tr');assert.match(row.textContent,/\+20,000/);assert.match(row.textContent,/间隔 2 天/);assert.equal(row.cells[5].textContent,'4,220');});
 dashboardCheck(()=>{const row=d.querySelector('.radar-book[data-book="qa-male-missing"]').closest('tr');assert.equal(row.cells[4].textContent,'—');assert.equal(row.cells[5].textContent,'—');assert.equal(row.querySelector('button').dataset.snapshot,maleSource.id);});
 dashboardCheck(()=>{const row=d.querySelector('.radar-book[data-book="qa-male-flat"]').closest('tr');assert.match(row.cells[4].textContent,/^0/);assert.equal(row.cells[5].textContent,'1,200');});
 dashboardCheck(()=>{assert.match(d.querySelector('.overview-coverage').textContent,/区间包含下界、不含上界/);assert.match(d.querySelector('.radar-method').textContent,/0.6/);});
 dashboardCheck(()=>{assert.match(d.querySelector('.dashboard-sources summary').textContent,/每分类最多 20 本公开样本，非全榜/);assert.match(d.querySelector('.dashboard-sources').textContent,/在读沿用来源显示精度/);assert(!d.querySelector('.dashboard-sources').textContent.includes('source_display_rounded'));});
 dashboardCheck(()=>{const row=d.querySelector('.growth-card [data-book="qa-public-growth"]').closest('tr');assert.equal(row.querySelector('button').dataset.snapshot,publicSource.id);assert.match(row.textContent,/原榜名次未提供/);assert.match(row.textContent,/\+20,000/);assert.match(row.textContent,/\+200%/);assert.match(row.textContent,/实际间隔 7 天/);});
 dashboardCheck(()=>{assert(!d.querySelector('.scatter-dot[data-book="qa-public-growth"]'));assert(!d.querySelector('.radar-card:not(.growth-card) [data-book="qa-public-growth"]'));assert.match(d.querySelector('.radar-method').textContent,/同一来源/);});
 d.querySelector('[data-action="dashboard-channel"][data-channel="女频"]').click();
 dashboardCheck(()=>{assert.equal(Number(kpiValue('books')),fanqieDashboard.views['女频'].kpis.books);assert.equal(d.querySelectorAll('.scatter-dot.male').length,0);assert.equal(d.querySelectorAll('.radar-table tbody tr').length,2);});
 dashboardCheck(()=>{const point=d.querySelector('.scatter-dot[data-book="qa-female-eight"]');assert.equal(point.dataset.snapshot,femaleSource.id);assert.match(point.querySelector('title').textContent,/80,000 字 · 在读 20,000人/);});
 d.querySelector('[data-action="overview-platform"][data-platform="ciweimao"]').click();
 dashboardCheck(()=>{assert(!d.querySelector('.scatter-chart'));assert(d.querySelector('.histogram[aria-label^="书名长度分布"]'));assert.match(d.querySelector('[data-kpi="primary_median"] strong').textContent,/—/);});
 dashboardCheck(()=>{assert.match(d.querySelector('.radar-card .data-card-head>span').textContent,/1 本/);assert.match(d.querySelector('.category-bar-chart').getAttribute('aria-label'),/样本量/);assert(d.querySelector('[data-metric="primary_median"]').disabled);});
 d.querySelector('[data-action="overview-platform"][data-platform="qidian"]').click();
 dashboardCheck(()=>{assert.match(d.querySelector('.scatter-chart').getAttribute('aria-label'),/月票/);assert.match(d.querySelector('.scatter-dot').getAttribute('aria-label'),/月票 50票/);assert(!d.querySelector('.radar-rule-line'));});
 d.querySelector('[data-action="overview-platform"][data-platform="fanqie"]').click();
 d.querySelector('.scatter-dot[data-book="qa-female-eight"]').dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));
 await wait(()=>d.querySelector('.detail h2')?.textContent==='穿越后：八万字样本','keyboard chart reference did not open exact work');
 const fromChart=await api('/api/state');dashboardCheck(()=>{assert.equal(fromChart.views.long.snapshot_id,femaleSource.id);assert.equal(fromChart.views.long.selected_id,'qa-female-eight');});await pause(80);dom.window.close();
 // Normal browsing selects the latest useful source, while explicit history
 // stays pinned. Later imports alone must not win over fuller same-day data.
 const directFull=await api('/api/import/snapshot',{snapshot:{...maleScope,data_date:'2026-11-12T06:00:00+08:00',complete:false,books:Array.from({length:297},(_,index)=>({id:'qa-full-'+index,title:'完整直采样本'+index,rank:index+1}))}});
 const publicBooks=Array.from({length:20},(_,index)=>({id:'qa-top-'+index,title:'公开来源样本'+index,source_position:index+1,metrics:[{name:'在读',value:1000+index}]}));
 const sameDayPublic=await api('/api/import/snapshot',{snapshot:{...publicScope,data_date:'2026-11-12T23:00:00+08:00',complete:false,books:publicBooks}});
 await api('/api/view',{mode:'long',platform:'qidian',snapshot_id:null,selected_id:null});
 dom=await open({startHome:true,storedOptions:{fanqie:{...maleScope}}});d=dom.window.document;
 d.querySelector('[data-action="platform"][data-platform="fanqie"]').click();await wait(()=>d.querySelector('.book-row')?.textContent.includes('完整直采样本'),'same-day public Top20 displaced fuller direct cache');
 dashboardCheck(()=>assert.equal(d.querySelector('#snapshot-select')?.value || d.querySelector('.snapshot-select')?.value,directFull.id));
 const newerPublic=await api('/api/import/snapshot',{snapshot:{...publicScope,data_date:'2026-11-13',complete:false,books:publicBooks}});
 d.querySelector('[data-action="platform"][data-platform="qidian"]').click();await wait(()=>d.querySelector('.book-row')?.textContent.includes('起点月票样本'),'comparison platform did not open');
 d.querySelector('[data-action="platform"][data-platform="fanqie"]').click();await wait(()=>d.querySelector('.book-row')?.textContent.includes('公开来源样本'),'newer public date did not take priority');
 const latestBrowsing=await api('/api/state');dashboardCheck(()=>{assert.equal(latestBrowsing.views.long.snapshot_id,newerPublic.id);assert.match(d.querySelector('.source-note').textContent,/来源列表位置不代表原榜名次/);assert(!d.querySelector('.source-note').textContent.includes('名次以本次原始榜单为准'));});
 d.querySelector('[data-section="history"]').click();d.querySelector(`[data-action="open-record"][data-snapshot="${sameDayPublic.id}"]`).click();await wait(()=>d.querySelector('.source-note')?.textContent.includes('2026-11-12T23:00:00+08:00'),'explicit public history was replaced by latest snapshot');
 const fixedPublic=await api('/api/state');dashboardCheck(()=>assert.equal(fixedPublic.views.long.snapshot_id,sameDayPublic.id));await pause(80);dom.window.close();
 // Public-source work may finish after the direct branch is already terminal.
 dom=await open({startHome:true,lateOverview:true});d=dom.window.document;
 await wait(()=>dom.control.overviewPolls>0,'terminal direct job stopped polling its running public branch');
 const readsBeforePublic=dom.control.overviewReads;
 await api('/api/import/snapshot',{snapshot:{...publicScope,data_date:'2026-11-14',books:[{id:'qa-late-public',title:'晚到的公开快照',metrics:[{name:'在读',value:5000}]}]}});
 dom.control.overviewJob={...dom.control.overviewJob,public_source:{status:'completed',snapshot_count:1,updated_at:'2026-11-09T12:00:10Z'}};
 await wait(()=>dom.control.overviewReads>readsBeforePublic && d.querySelector('[data-action="overview-refresh"]')?.disabled===false,'late public completion did not refresh the dashboard');
 const lateData=(await api('/api/overview')).platforms.find(item=>item.platform==='fanqie').dashboard.views['全部'];
 dashboardCheck(()=>{assert.equal(Number(d.querySelector('[data-kpi="books"] strong').childNodes[0].textContent.replaceAll(',','')),lateData.kpis.books);assert.equal(d.querySelector('[data-action="overview-refresh"]').disabled,false);});
 await pause(80);dom.window.close();
 assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,checks:82+dashboardChecks+favoriteChecks+openingChecks,dashboard_checks:dashboardChecks,favorite_checks:favoriteChecks,opening_checks:openingChecks,layout_verified:false,external_platform_requests:0}));
})().catch(e=>{console.error(e);process.exitCode=1;process.exit(1)});
