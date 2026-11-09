// DOM integration against the real local API. This does not verify pixel layout.
const assert = require('node:assert/strict');
const {JSDOM, VirtualConsole} = require(process.env.FAGAO_QA_NODE_MODULES ? process.env.FAGAO_QA_NODE_MODULES+'/jsdom' : 'jsdom');
const fs = require('node:fs');
const base=process.env.FAGAO_TEST_URL;
if (!base) throw new Error('Start tests/run_ui_smoke.py rather than running this directly.');
const errors=[];
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function wait(fn,msg){for(let i=0;i<150;i++){if(await fn())return;await pause(20)}throw new Error(msg)}
async function api(path,body){const r=await fetch(base+path,body?{method:'POST',headers:{'Content-Type':'application/json','X-Fagao-Request':'1'},body:JSON.stringify(body)}:{});const j=await r.json();assert(!j.error,JSON.stringify(j));return j;}
async function open(){const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(String(e)));const dom=new JSDOM(await (await fetch(base+'/')).text(),{url:base+'/',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:vc});dom.window.fetch=(path,init)=>fetch(new URL(path,base),init);dom.window.HTMLDialogElement.prototype.showModal=function(){this.open=true};dom.window.HTMLDialogElement.prototype.close=function(){this.open=false};dom.window.eval(fs.readFileSync('web/app.js','utf8'));await wait(()=>dom.window.document.querySelector('[data-action="select-book"]'),'books did not render');return dom;}
(async()=>{
 let dom=await open(), d=dom.window.document;
 assert.equal(d.querySelector('#main-site').href,'https://www.fagaochuangzuo.com/');assert(d.querySelector('#points-entry button').disabled);
 const buttons=d.querySelectorAll('[data-action="select-book"]');assert.equal(buttons.length,3);buttons[1].click();
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
 assert.match(d.querySelector('.report-text').textContent,/仅简介/);assert(!d.querySelector('#dialog script'),'unsafe report markup');
 assert.deepEqual(errors,[]);dom.window.close();console.log(JSON.stringify({passed:true,checks:12,layout_verified:false}));
})().catch(e=>{console.error(e);process.exitCode=1;process.exit(1)});
