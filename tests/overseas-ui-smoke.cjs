// 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
// Synthetic English fixtures; requests use the real API but never a novel host.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM,VirtualConsole}=require(process.env.FAGAO_QA_NODE_MODULES?process.env.FAGAO_QA_NODE_MODULES+'/jsdom':'jsdom');
const base=process.env.FAGAO_TEST_URL;
if(!base)throw new Error('Run tests/run_overseas_ui_smoke.py');
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function wait(fn,message){for(let i=0;i<300;i++){if(await fn())return;await pause(20);}throw new Error(message);}
(async()=>{
 const errors=[],collections=[],copies=[],translations=[];
 const vc=new VirtualConsole();vc.on('jsdomError',error=>errors.push(String(error)));
 const dom=new JSDOM(await(await fetch(base+'/')).text(),{url:base+'/',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});
 const w=dom.window,d=w.document;
 const membership=await(await fetch(base+'/api/membership/status')).json();
 assert.equal(membership.allowed,true,'The isolated fixture must provide an active synthetic member');
 let scribbleJob=null, royalPhase='blocked', crossTabDenial=null;const moreRequests=[],htmlImports=[];
 let releaseTranslation;
 const translationGate=new Promise(resolve=>releaseTranslation=resolve);
 w.fetch=async(path,init)=>{
  const requestURL=new URL(path,base),pathname=requestURL.pathname;
  assert.equal(requestURL.origin,new URL(base).origin,'DOM test attempted an external request');
  if(pathname.startsWith('/api/')&&!pathname.startsWith('/api/membership/')){
   const headers=new Headers(init?.headers);
   assert.equal(headers.get('X-Fagao-Account'),membership.account.id,'Protected request lost its account binding');
   assert.equal(headers.get('X-Fagao-Generation'),String(membership.generation),'Protected request lost its session generation');
  }
  if(pathname==='/api/collect'){
   const request=JSON.parse(init.body);collections.push(request);
   if(request.platform==='meganovel'){
    crossTabDenial={id:'qa-denied-in-other-tab',platform:'meganovel',kind:'collect',status:'failed',error_code:'forbidden',message:'其他窗口的榜单请求返回 HTTP 403。',options:request.options};
    return Response.json({status:'cooldown',requires_action:true,retry_after_seconds:86400,job:crossTabDenial});
   }
   if(request.platform==='royalroad'){
    royalPhase='cached';return Response.json({status:'fresh',retry_after_seconds:900,snapshot_id:process.env.FAGAO_TEST_ROYAL_SNAPSHOT});
   }
   if(request.platform==='scribblehub'){
    scribbleJob={id:'qa-scribble',platform:'scribblehub',kind:'collect',options:request.options,status:'running',automatic:true,interactive:true,partial_snapshot_id:process.env.FAGAO_TEST_SCRIBBLE_FIRST,partial_count:10,demand_count:10,waiting_for_more:true,message:'首批10本已载入'};
    return Response.json({status:'started',job_id:scribbleJob.id,job:scribbleJob});
   }
   return Response.json({status:'fresh',retry_after_seconds:900});
  }
  if(pathname==='/api/jobs/qa-scribble')return Response.json(scribbleJob);
  if(pathname==='/api/jobs/more'&&scribbleJob){
   moreRequests.push(JSON.parse(init.body));scribbleJob={...scribbleJob,partial_snapshot_id:process.env.FAGAO_TEST_SCRIBBLE_NEXT,partial_count:30,demand_count:110,waiting_for_more:true};
   return Response.json({status:'continued',job_id:scribbleJob.id,job:scribbleJob});
  }
  if(pathname==='/api/state'||pathname==='/api/view'){
   const data=await(await fetch(new URL(path,base),init)).json();
   if(pathname==='/api/state'&&!scribbleJob)data.snapshots=data.snapshots.filter(item=>item.platform!=='scribblehub');
   if(royalPhase==='blocked')data.snapshots=data.snapshots.filter(item=>item.platform!=='royalroad');
   const royalError={id:'qa-royal-denied',platform:'royalroad',kind:'collect',status:'failed',error_code:'forbidden',message:'Royal Road 返回 HTTP 403；本次读取被拒绝。',options:{mode:'overseas',channel:'全部',category:'全部',board:'新星榜',period:'当前',limit:10000,max_pages:100}};
   data.jobs=[royalError,...(data.jobs||[])];
   data.jobs=[{id:'qa-failed-live',platform:'goodnovel',kind:'collect',status:'failed',message:'当前更新暂时无法连接',options:{mode:'overseas',channel:'全部',category:'狼人',board:'分类榜',limit:10000,max_pages:100}},...(data.jobs||[])];
   if(scribbleJob)data.jobs=[scribbleJob,...(data.jobs||[])];
   if(crossTabDenial)data.jobs=[crossTabDenial,...(data.jobs||[])];
   return Response.json(data);
  }
  if(pathname==='/api/import/overseas-html'){
   htmlImports.push(JSON.parse(init.body));
   // The import result is a prebuilt, synthetic saved-page snapshot. Its real
   // API lookup must preserve the same displayed account/session as the import.
   return fetch(base+'/api/snapshots/'+process.env.FAGAO_TEST_SCRIBBLE_FIRST,{headers:init.headers,cache:'no-store'});
  }
  if(pathname==='/api/overview/refresh')return Response.json({status:'fresh',retry_after_seconds:900});
  if(pathname==='/api/translation-context'){translations.push(JSON.parse(init.body));await translationGate;}
  return fetch(new URL(path,base),init);
 };
 Object.defineProperty(w.navigator,'clipboard',{value:{writeText:async(text)=>copies.push(text)},configurable:true});
 w.HTMLDialogElement.prototype.showModal=function(){this.open=true};
 w.HTMLDialogElement.prototype.close=function(){this.open=false};
 w.eval(fs.readFileSync('web/app.js','utf8'));
 await wait(()=>d.querySelector('.overview-tabs'),'Initial dashboard did not render');
 const overseas=d.querySelector('#overseas-navigation'),favoriteNav=d.querySelector('[data-section="favorites"]');
 assert(overseas.compareDocumentPosition(favoriteNav)&w.Node.DOCUMENT_POSITION_FOLLOWING,'Overseas entry must precede favorites');
 assert.equal(d.querySelector('#overseas-platforms').hidden,true);
 overseas.click();
 await wait(()=>d.querySelector('.page-head h1')?.textContent==='GoodNovel · 分类榜','Overseas default did not open');
 await wait(()=>d.querySelectorAll('.book-row').length===20,'Cached first20 books were not shown');
 assert.equal(d.querySelector('#overseas-platforms').hidden,false);
 assert.equal(d.querySelectorAll('#overseas-platforms [data-platform]').length,7);
 assert(d.querySelector('#platforms [data-platform="fanqie"]'),'Domestic navigation disappeared');
 assert.equal(d.querySelector('#board-category').value,'狼人');
 assert.equal(d.querySelector('#board-kind').value,'分类榜');
 assert(d.querySelector('.pagination-summary').textContent.includes('已到来源末页'),'Source end reason is not readable');
 assert(d.querySelector('.job-warning')?.textContent.includes('当前更新暂时无法连接'),'Live refresh failure must remain visible');
 assert.equal(d.querySelector('.book-title').textContent,'The English Book 1');
 assert(d.querySelector('.book-row .cover-image'),'Source cover missing');
 assert.equal(d.querySelector('.rank').textContent,'—','Library position falsely became official rank');
 assert(d.querySelector('.row-summary').textContent.includes('<script>unsafe()</script>'));
 assert.equal(d.querySelector('#content script'),null,'Synopsis markup executed');
 await wait(()=>collections.some(item=>item.platform==='goodnovel'),'Automatic collection was not requested');
 const initialCollection=collections.find(item=>item.platform==='goodnovel');
 assert.equal(initialCollection.options.mode,'overseas');assert.equal(initialCollection.automatic,true);assert.equal(initialCollection.interactive,true);
 d.querySelector('[data-action="load-more"]').click();
 await wait(()=>d.querySelectorAll('.book-row').length===40,'Incremental list expansion failed');
 d.querySelector('[data-book="en-1"]').click();
 await wait(()=>d.querySelector('.detail h2')?.textContent==='The English Book 1','English selection not saved');
 assert.equal(d.querySelector('.detail .summary').textContent,'Original English synopsis 1. <script>unsafe()</script>');
 assert(d.querySelector('.translation-card').textContent.includes('翻译这本'));
 assert(d.querySelector('.translation-card').compareDocumentPosition(d.querySelector('.detail .summary'))&w.Node.DOCUMENT_POSITION_FOLLOWING,'Translation action is buried below synopsis');
 d.querySelector('[data-action="translation-context"]').click();
 await wait(()=>translations.length===1,'Translation endpoint not called');
 d.querySelector('[data-book="en-2"]').click();
 await wait(()=>d.querySelector('.detail h2')?.textContent==='The English Book 2','Second selection failed');
 releaseTranslation();
 await wait(()=>copies.length===1,'Translation request not copied');
 assert.equal(translations[0].book_id,'en-1');
 assert.equal(translations[0].snapshot_id,process.env.FAGAO_TEST_OVERSEAS_SNAPSHOT);
 assert(copies[0].includes('The English Book 1'),'Copy drifted with later selection');
 assert(copies[0].includes(process.env.FAGAO_TEST_OVERSEAS_SNAPSHOT),'Copy lacks fixed snapshot');
 d.querySelector('.favorite-button').click();
 await wait(()=>d.querySelector('#favorite-count').textContent==='1','Overseas favorite not saved');
 d.querySelector('#platforms [data-platform="fanqie"]').click();
 await wait(()=>d.querySelector('.page-head h1')?.textContent==='番茄小说 · 新书榜','Domestic navigation failed after overseas');
 assert.equal(d.querySelector('#overseas-platforms').hidden,true);
 d.querySelector('[data-section="favorites"]').click();
 await wait(()=>d.querySelector('.saved-open')?.textContent.includes('The English Book 2'),'Favorite original title missing');
 d.querySelector('.saved-open').click();
 await wait(()=>d.querySelector('.detail h2')?.textContent==='The English Book 2','Favorite did not restore overseas selection');
 assert.equal(d.querySelector('#overseas-navigation').getAttribute('aria-expanded'),'true');
 assert(d.querySelector('.auto-status').textContent.includes('历史记录'),'Saved favorite is not in historical view');
 assert.equal(d.querySelector('.job-warning'),null,'Historical snapshot is mislabeled with live update failure');
 d.querySelector('[data-platform="goodnovel_short"]').click();
 await wait(()=>d.querySelector('.book-title')?.textContent==='A Short English Story','Short overseas platform did not open');
 assert.equal(d.querySelector('#board-kind').value,'热门短篇');
 d.querySelector('[data-platform="dreame"]').click();
 await wait(()=>d.querySelector('.page-head h1')?.textContent==='Dreame · 新书榜','Dreame did not use registry default');
 assert.equal(d.querySelectorAll('#board-kind option').length,6);
 d.querySelector('[data-platform="meganovel"]').click();
 await wait(()=>d.querySelector('.source-failure')?.textContent.includes('其他窗口的榜单请求返回 HTTP 403。'),'Requires-action cooldown ignored a job created by another tab');
 assert(d.querySelector('.auto-status').textContent.includes('自动重试已暂停'),'Requires-action cooldown promises automatic retry');
 assert(!d.querySelector('#content').textContent.includes('正在同步作品'),'Requires-action cooldown retains false syncing state');
 assert.equal(collections.filter(item=>item.platform==='meganovel').length,1);
 d.querySelector('[data-platform="webnovel"]').click();
 await wait(()=>d.querySelector('.page-head h1')?.textContent==='Webnovel · 新书能量榜','Webnovel did not use registry default');
 assert.equal(d.querySelector('#board-channel').value,'男频');
 const channel=d.querySelector('#board-channel');channel.value='女频';channel.dispatchEvent(new w.Event('change',{bubbles:true}));
 await wait(()=>collections.some(item=>item.platform==='webnovel'&&item.options.channel==='女频'),'Webnovel channel change not collected');
 const changed=collections.find(item=>item.platform==='webnovel'&&item.options.channel==='女频');
 assert.equal(changed.options.category,'全部','Webnovel channel switched to domestic category');
 assert.equal(changed.options.period,'周榜');
 d.querySelector('[data-platform="royalroad"]').click();
 await wait(()=>d.querySelector('.source-failure h2')?.textContent==='来源暂不接受工作台读取','First403 did not show access failure');
 assert(!d.querySelector('#content').textContent.includes('正在同步作品'),'403 left a misleading syncing state');
 assert(d.querySelector('.source-failure').textContent.includes('HTTP 403'));
 assert.equal(d.querySelector('.source-failure a')?.href,'https://www.royalroad.com/fictions/rising-stars');
 assert(d.querySelector('.auto-status').textContent.includes('自动重试已暂停'));
 assert.equal(collections.filter(item=>item.platform==='royalroad').length,0,'A known403 automatically retried');
 d.querySelector('.source-failure [data-action="collect"]').click();
 await wait(()=>d.querySelector('.book-title')?.textContent==='The RST English Book','Public snapshot did not render');
 assert.equal(collections.find(item=>item.platform==='royalroad').automatic,false,'Explicit retry lost manual intent');
 assert(d.querySelector('.job-warning').textContent.includes('HTTP 403'),'Cached403 did not retain failure notice');
 assert.equal(d.querySelectorAll('.book-row').length,1,'Cached403 erased existing books');
 assert(d.querySelector('.snapshot-source').textContent.includes('RST 公开榜单快照'));
 assert(d.querySelector('.snapshot-source').textContent.includes('2026-11-09T09:40:00Z'));
 assert.equal(d.querySelector('.list-key').textContent,'列表序号 / 作品来源名次');
 assert.equal(d.querySelector('.row-summary').textContent,'本次来源未提供简介');
 assert.equal(d.querySelector('.source-note a').href,'https://rst.doomlabs.net/');
 d.querySelector('[data-platform="scribblehub"]').click();
 await wait(()=>d.querySelector('.page-head h1')?.textContent==='Scribble Hub · 周收藏榜','Scribble weekly board did not open');
 await wait(()=>d.querySelectorAll('.book-row').length===10,'First overseas source page did not become visible');
 assert.equal(collections.find(item=>item.platform==='scribblehub').options.period,'周榜','Initial scope omitted actual source period');
 await wait(()=>d.querySelector('[data-action="load-more"]'),'Period changed after snapshot and lost continuation');
 d.querySelector('[data-action="load-more"]').click();
 await wait(()=>moreRequests.length===1,'Scrolling did not request next overseas source page');
 assert.equal(moreRequests[0].job_id,'qa-scribble');
 await wait(()=>d.querySelectorAll('.book-row').length===30,'Weekly source continuation failed after snapshot period arrived');
 scribbleJob={...scribbleJob,status:'failed',error_code:'source_access_denied',message:'Scribble Hub 返回 HTTP 403。'};
 d.querySelector('[data-platform="goodnovel"]').click();
 await wait(()=>d.querySelector('.page-head h1')?.textContent==='GoodNovel · 分类榜','Return before import test failed');
 d.querySelector('[data-platform="scribblehub"]').click();
 await wait(()=>d.querySelector('.job-warning [data-action="import-ranking-html"]'),'Denied source lacks saved-page import');
 d.querySelector('[data-action="import-ranking-html"]').click();
 const importInput=d.querySelector('#snapshot-file');
 assert(importInput.accept.includes('.html'),'HTML picker format missing');
 d.querySelector('[data-platform="goodnovel"]').click();
 await wait(()=>d.querySelector('.page-head h1')?.textContent==='GoodNovel · 分类榜','Switch while picker open failed');
 Object.defineProperty(importInput,'files',{value:[{name:'ranking.html',type:'text/html',size:25,text:async()=>'<html>saved ranking</html>'}],configurable:true});
 importInput.dispatchEvent(new w.Event('change',{bubbles:true}));
 await wait(()=>htmlImports.length===1,'Saved HTML was not sent to dedicated import endpoint');
 assert.equal(htmlImports[0].platform,'scribblehub','HTML import followed later page instead of chosen platform');
 assert.equal(htmlImports[0].html,'<html>saved ranking</html>');
 await wait(()=>d.querySelector('.book-title')?.textContent==='Scribble Fixture 1','Imported source did not open');
 assert(d.querySelector('.auto-status').textContent.includes('历史记录'),'Saved-page import should stay a historical snapshot');
 assert.equal(d.querySelector('.pagination-summary').textContent,'已导入第 1 页 · 本页 10 本 · 来源更新时间未知','HTML import falsely reports zero fetched pages');
 assert.deepEqual(errors,[]);
 dom.window.close();
 console.log('Overseas UI passed: navigation, Chinese registry filters, original English/cover, cache+scroll, pinned translation, favorites, domestic return weekly channel scope, denied-source states, source provenance and pinned HTML import.');
})().catch(error=>{console.error(error);process.exit(1)});
