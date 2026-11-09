// DOM integration against the real local API. Platform collection is intercepted;
// this test never sends requests to a novel platform and does not verify pixels.
const assert = require('node:assert/strict');
const {JSDOM, VirtualConsole} = require(process.env.FAGAO_QA_NODE_MODULES ? process.env.FAGAO_QA_NODE_MODULES+'/jsdom' : 'jsdom');
const fs = require('node:fs');
const base=process.env.FAGAO_TEST_URL;
if (!base) throw new Error('Start tests/run_ui_smoke.py rather than running this directly.');
const errors=[], collections=[];
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function wait(fn,msg){for(let i=0;i<150;i++){if(await fn())return;await pause(20)}throw new Error(msg)}
async function api(path,body){const r=await fetch(base+path,body?{method:'POST',headers:{'Content-Type':'application/json','X-Fagao-Request':'1'},body:JSON.stringify(body)}:{});const j=await r.json();assert(!j.error,JSON.stringify(j));return j;}
async function open({stale=false,freshSnapshot=null,history=false,expiredHeiyan=false,holdCollection=false,forceConnectedHeiyan=false,collectionResponse=null,contentFixture=false,storedOptions=null}={}){
  const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(String(e)));
  const dom=new JSDOM(await (await fetch(base+'/')).text(),{url:base+'/',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
  const makeHold=()=>{let release;const promise=new Promise(resolve=>{release=resolve});return {promise,release,captured:false}};
  let mockAuth=null;
  const control={pending:null,viewPending:null,collectionPending:holdCollection?makeHold():null,stateRequests:0,holdNextState(){return this.pending=makeHold()},holdNextView(){return this.viewPending=makeHold()}};dom.control=control;
  control.catalogRequests=[];control.textRequests=[];control.textAssets=[];control.textJob=null;control.textPolls=0;control.cancelCalls=0;
  control.finishText=({cancelled=false}={})=>{
    const job=control.textJob, asset={id:'qa-asset-'+control.textRequests.length,snapshot_id:job.snapshot_id,book_id:job.book_id,title:job.title,platform:'fanqie',scope:'第 1—3 章；实际取得第 1、3 章，缺失第 2 章',chapters:[{index:1,title:'第一章'},{index:3,title:'第三章'}],missing:[{index:2,chapter:'第二章',reason:'平台暂未提供正文'}],coverage_complete:false,complete:false,cancelled,characters:66,created_at:'2026-11-09T12:00:00Z',acquisition:'public_download'};
    control.textAssets.unshift(asset);control.textJob={...job,status:cancelled?'cancelled':'completed',message:cancelled?'已暂停，已取得内容已保存':'正文已保存，缺失 1 章',partial:true,finished_at:'2026-11-09T12:00:00Z',text_id:asset.id,text:asset};
  };
  dom.window.fetch=async(path,init)=>{
    const pathname=new URL(path,base).pathname;
    if(pathname==='/api/view' && control.viewPending && !control.viewPending.captured){const held=control.viewPending;held.captured=true;await held.promise;}
    if(pathname==='/api/collect'){
      if(control.collectionPending && !control.collectionPending.captured){const held=control.collectionPending;held.captured=true;await held.promise;}
      const body=JSON.parse(init.body);collections.push(body);
      if(collectionResponse?.auth)mockAuth=collectionResponse.auth;
      return Response.json(collectionResponse || {status:'fresh',snapshot_id:freshSnapshot,retry_after_seconds:900});
    }
    if(pathname==='/api/catalog'){
      if(!contentFixture)throw new Error('Catalog must be intercepted in UI tests.');
      const body=JSON.parse(init.body);control.catalogRequests.push(body);
      return Response.json({...body,platform:'fanqie',title:'测试作品乙',total:12,catalog_chapters:12,collected_at:'2026-11-09T12:00:00Z',source_url:'https://fanqienovel.com/page/fixture',chapters:Array.from({length:12},(_,i)=>({index:i+1,item_id:'chapter-'+(i+1),title:'第 '+(i+1)+' 章'+(i===0?' <script>unsafe</script>':''),is_locked:i===6}))});
    }
    if(pathname==='/api/fetch-text'){
      if(!contentFixture)throw new Error('Text downloads must be intercepted in UI tests.');
      const body=JSON.parse(init.body);control.textRequests.push(body);
      control.textJob={id:'qa-text-'+control.textRequests.length,kind:'text',platform:'fanqie',snapshot_id:body.snapshot_id,book_id:body.book_id,title:body.book_id==='qa-b'?'测试作品乙':'测试作品甲',options:body,status:'pending',message:'正在读取选定作品的正文'};
      return Response.json({status:'started',job_id:control.textJob.id,job:control.textJob});
    }
    if(pathname.startsWith('/api/jobs/qa-text-')){control.textPolls++;return Response.json(control.textJob);}
    if(pathname==='/api/jobs/cancel' && contentFixture){control.cancelCalls++;control.textJob={...control.textJob,status:'cancelling',message:'正在暂停正文下载，已取得内容将保留。'};return Response.json(control.textJob);}
    if(pathname.startsWith('/api/texts/qa-asset-'))return Response.json({...control.textAssets.find(item=>pathname.endsWith('/'+item.id)),content:'这是实际取得部分正文的测试夹具。<script>unsafe()</script>'});
    if(pathname==='/api/auth')throw new Error('UI smoke must not start a platform login or browser.');
    const response=await fetch(new URL(path,base),init);
    if(pathname==='/api/state' || pathname==='/api/view'){
      if(pathname==='/api/state')control.stateRequests++;
      const data=await response.json();if(contentFixture){data.jobs=[...(control.textJob?[control.textJob]:[]),...(data.jobs || [])];data.texts=[...control.textAssets,...(data.texts || [])];}if(stale)data.snapshots.forEach(item=>{item.collected_at='2020-01-01T00:00:00Z';});if(expiredHeiyan)data.auth={...data.auth,heiyan:{state:'expired',message:'测试登录已过期'}};if(forceConnectedHeiyan)data.auth={...data.auth,heiyan:mockAuth || {state:'connected'}};
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
  if(history){const s=await api('/api/state');dom.window.localStorage.setItem('fagao-history-views',JSON.stringify({long:s.views.long.snapshot_id}));}
  if(storedOptions){dom.window.localStorage.setItem('fagao-collection-options',JSON.stringify(storedOptions));dom.window.localStorage.setItem('fagao-options-version','2');}
  dom.window.eval(fs.readFileSync('web/app.js','utf8'));
  await wait(()=>dom.window.document.querySelector('[data-action="select-book"]'),'books did not render');return dom;
}
(async()=>{
 let dom=await open(), d=dom.window.document;
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
 d.querySelector('[data-action="favorite"]').click();await wait(()=>d.querySelector('#favorite-count').textContent==='1','favorite failed');
 d.querySelector('#sort').value='rank_desc';d.querySelector('#sort').dispatchEvent(new dom.window.Event('change',{bubbles:true}));
 await wait(async()=>((await api('/api/state')).views.long.filters.sort==='rank_desc'),'sort not saved');
 r=await api('/api/resolve',{query:'番茄新书榜第14名'});assert.equal(r.book_id,'qa-b');
 const q=d.querySelector('#query');q.value='独有简介';q.dispatchEvent(new dom.window.Event('input',{bubbles:true}));
 await wait(()=>d.querySelectorAll('[data-action="select-book"]').length===1,'summary filter failed');
 d.querySelector('[data-mode="short"]').click();await wait(()=>d.querySelector('.login-card'),'heiyan login missing');
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
 assert(d.querySelector('.reconnect-card'),'saved library must still show reconnect action');assert.match(d.querySelector('.reconnect-card').textContent,/过期/);assert.equal(d.querySelectorAll('.book-row').length,1);assert.equal(collections.length,beforeLibrary,'expired library should not auto collect');dom.window.close();
 dom=await open({forceConnectedHeiyan:true,collectionResponse:{status:'cooldown',requires_login:true,auth:{state:'expired',message:'采集时发现登录已过期'},retry_after_seconds:300}});d=dom.window.document;
 await wait(()=>d.querySelector('.reconnect-card'),'auth cooldown did not show reconnect card');assert.match(d.querySelector('.reconnect-card').textContent,/过期/);assert.equal(d.querySelectorAll('.book-row').length,1);dom.window.close();
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
 assert.match(d.querySelector('.saved-text-assets').textContent,/实际取得/);assert.match(d.querySelector('.saved-text-assets a').href,/\/api\/texts\/qa-asset-1\/download$/);
 d.querySelector('[data-action="catalog"]').click();await wait(()=>d.querySelector('.chapter-saved-result') && d.querySelector('#chapter-download-form'),'saved result not visible');
 assert.match(d.querySelector('.chapter-saved-meta').textContent,/实际取得 2 章/);assert.match(d.querySelector('.chapter-saved-meta').textContent,/缺失 1 章/);assert(!d.querySelector('.chapter-saved-meta').textContent.includes('完整正文'),'partial result claimed full text');
 d.querySelector('[data-action="chapter-all"]').click();d.querySelector('#chapter-download-form').dispatchEvent(new dom.window.Event('submit',{bubbles:true,cancelable:true}));await wait(()=>dom.control.textRequests.length===2 && d.querySelector('[data-action="cancel-text-job"]'),'explicit full catalog task not started');
 assert.deepEqual(dom.control.textRequests[1],{snapshot_id:original,book_id:'qa-b',all:true});
 d.querySelector('[data-action="cancel-text-job"]').click();await wait(()=>dom.control.cancelCalls===1 && d.querySelector('[data-action="cancel-text-job"]')?.disabled,'cancel did not enter finalizing state');
 const textPolls=dom.control.textPolls;await wait(()=>dom.control.textPolls>textPolls,'cancelling task stopped polling before saving content');
 dom.control.finishText({cancelled:true});await wait(()=>d.querySelector('.text-job')?.textContent.includes('已暂停，已取得内容已保存'),'cancelled partial text not displayed');
 assert.match(d.querySelector('.chapter-result-actions a').href,/qa-asset-2\/download$/);
 d.querySelector('.chapter-result-actions [data-action="preview-text"]').click();await wait(()=>d.querySelector('.saved-text-preview'),'saved text preview not opened');assert.match(d.querySelector('.saved-text-preview').textContent,/<script>/);assert(!d.querySelector('#dialog script'),'saved text executed HTML');dom.window.close();
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
 assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,checks:46,layout_verified:false,external_platform_requests:0}));
})().catch(e=>{console.error(e);process.exitCode=1;process.exit(1)});
