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
async function open({stale=false,freshSnapshot=null,history=false,expiredHeiyan=false,holdCollection=false,forceConnectedHeiyan=false,collectionResponse=null}={}){
  const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(String(e)));
  const dom=new JSDOM(await (await fetch(base+'/')).text(),{url:base+'/',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
  const makeHold=()=>{let release;const promise=new Promise(resolve=>{release=resolve});return {promise,release,captured:false}};
  let mockAuth=null;
  const control={pending:null,viewPending:null,collectionPending:holdCollection?makeHold():null,stateRequests:0,holdNextState(){return this.pending=makeHold()},holdNextView(){return this.viewPending=makeHold()}};dom.control=control;
  dom.window.fetch=async(path,init)=>{
    const pathname=new URL(path,base).pathname;
    if(pathname==='/api/view' && control.viewPending && !control.viewPending.captured){const held=control.viewPending;held.captured=true;await held.promise;}
    if(pathname==='/api/collect'){
      if(control.collectionPending && !control.collectionPending.captured){const held=control.collectionPending;held.captured=true;await held.promise;}
      const body=JSON.parse(init.body);collections.push(body);
      if(collectionResponse?.auth)mockAuth=collectionResponse.auth;
      return Response.json(collectionResponse || {status:'fresh',snapshot_id:freshSnapshot,retry_after_seconds:900});
    }
    if(pathname==='/api/auth')throw new Error('UI smoke must not start a platform login or browser.');
    const response=await fetch(new URL(path,base),init);
    if(pathname==='/api/state'){
      control.stateRequests++;
      const data=await response.json();if(stale)data.snapshots.forEach(item=>{item.collected_at='2020-01-01T00:00:00Z';});if(expiredHeiyan)data.auth={...data.auth,heiyan:{state:'expired',message:'测试登录已过期'}};if(forceConnectedHeiyan)data.auth={...data.auth,heiyan:mockAuth || {state:'connected'}};
      if(control.pending && !control.pending.captured){const pending=control.pending;pending.captured=true;await pending.promise;}
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
  dom.window.eval(fs.readFileSync('web/app.js','utf8'));
  await wait(()=>dom.window.document.querySelector('[data-action="select-book"]'),'books did not render');return dom;
}
(async()=>{
 let dom=await open(), d=dom.window.document;
 await wait(()=>collections.length===1,'recent limited snapshot wrongly suppressed automatic scope check');assert.equal(collections[0].automatic,true);assert.equal(collections[0].options.limit,1000);
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
 assert.equal(collections.at(-1).automatic,true);assert.equal(collections.at(-1).options.limit,1000);assert.equal(collections.at(-1).options.max_pages,100);
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
 assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,checks:30,layout_verified:false,external_platform_requests:0}));
})().catch(e=>{console.error(e);process.exitCode=1;process.exit(1)});
