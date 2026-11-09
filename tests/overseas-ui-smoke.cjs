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
 let scribbleJob=null;const moreRequests=[];
 let releaseTranslation;
 const translationGate=new Promise(resolve=>releaseTranslation=resolve);
 w.fetch=async(path,init)=>{
  const pathname=new URL(path,base).pathname;
  if(pathname==='/api/collect'){
   const request=JSON.parse(init.body);collections.push(request);
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
   data.jobs=[{id:'qa-failed-live',platform:'goodnovel',kind:'collect',status:'failed',message:'当前更新暂时无法连接',options:{mode:'overseas',channel:'全部',category:'狼人',board:'分类榜',limit:10000,max_pages:100}},...(data.jobs||[])];
   if(scribbleJob)data.jobs=[scribbleJob,...(data.jobs||[])];
   return Response.json(data);
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
 d.querySelector('[data-platform="webnovel"]').click();
 await wait(()=>d.querySelector('.page-head h1')?.textContent==='Webnovel · 新书能量榜','Webnovel did not use registry default');
 assert.equal(d.querySelector('#board-channel').value,'男频');
 const channel=d.querySelector('#board-channel');channel.value='女频';channel.dispatchEvent(new w.Event('change',{bubbles:true}));
 await wait(()=>collections.some(item=>item.platform==='webnovel'&&item.options.channel==='女频'),'Webnovel channel change not collected');
 const changed=collections.find(item=>item.platform==='webnovel'&&item.options.channel==='女频');
 assert.equal(changed.options.category,'全部','Webnovel channel switched to domestic category');
 assert.equal(changed.options.period,'周榜');
 d.querySelector('[data-platform="scribblehub"]').click();
 await wait(()=>d.querySelector('.page-head h1')?.textContent==='Scribble Hub · 周收藏榜','Scribble weekly board did not open');
 await wait(()=>d.querySelectorAll('.book-row').length===10,'First overseas source page did not become visible');
 assert.equal(collections.find(item=>item.platform==='scribblehub').options.period,'周榜','Initial scope omitted actual source period');
 await wait(()=>d.querySelector('[data-action="load-more"]'),'Period changed after snapshot and lost continuation');
 d.querySelector('[data-action="load-more"]').click();
 await wait(()=>moreRequests.length===1,'Scrolling did not request next overseas source page');
 assert.equal(moreRequests[0].job_id,'qa-scribble');
 await wait(()=>d.querySelectorAll('.book-row').length===30,'Weekly source continuation failed after snapshot period arrived');
 assert.deepEqual(errors,[]);
 dom.window.close();
 console.log('Overseas UI passed: navigation, Chinese registry filters, original English/cover, cache+scroll, pinned translation, favorites, domestic return and weekly channel scope.');
})().catch(error=>{console.error(error);process.exit(1)});
