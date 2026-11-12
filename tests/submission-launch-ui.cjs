// 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
// Exercise the published page in Chromium, delaying local replies to test launch and retry.
'use strict';
const assert = require('node:assert/strict');
const {EventEmitter, once} = require('node:events');
const path = require('node:path');
const {chromium} = require('playwright');
const web = path.resolve(__dirname, '../web');
const origin = 'http://127.0.0.1:18376';

(async () => {
  const browser = await chromium.launch(process.platform === 'win32' ? {channel:'msedge'} : {});
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(8000);
    const pending = new Map();
    const arrivals = new EventEmitter();
    const launches = [];
    let saved;
    await page.route('**/*', async route => {
      const {origin:host, pathname} = new URL(route.request().url());
      assert.equal(host, origin);
      if (!pathname.startsWith('/api/')) {
        const filename = pathname.slice(1);
        assert(['submissions.html','styles.css','submissions.css','submissions.js','theme.js','favicon.svg'].includes(filename));
        return route.fulfill({path:path.join(web,filename)});
      }
      const action = pathname.slice('/api/submissions/'.length);
      if (action === 'save') {
        saved = {...route.request().postDataJSON(), id:'demo', revision:1};
        return route.fulfill({json:{draft:saved}});
      }
      if (action === 'open') launches.push(route.request().postDataJSON());
      assert(['component','drafts','open'].includes(action));
      assert(!pending.has(action));
      pending.set(action, route);
      arrivals.emit(action);
    });
    async function answer(action, json, status=200) {
      if (!pending.has(action)) await once(arrivals, action, {signal:AbortSignal.timeout(8000)});
      const route = pending.get(action);
      pending.delete(action);
      await route.fulfill({json,status});
    }
    await page.goto(origin+'/submissions.html', {waitUntil:'domcontentloaded'});
    const open = page.locator('#open-assistant');
    const transfer = page.locator('#open-draft');
    const hint = page.locator('#assistant-hint');
    const feedback = page.locator('#editor-feedback');
    await open.click();
    assert.equal(await open.getAttribute('aria-busy'),'true');
    await hint.filter({hasText:'正在打开'}).waitFor();
    await answer('component',{platform:'win32',available:false,message:'上次检查发现组件缺失'});
    await answer('drafts',{drafts:[]});
    await hint.filter({hasText:'正在打开'}).waitFor();
    await answer('open',{error:{code:'submission_handoff_failed',message:'Windows 无法启动投稿窗口。'}},503);
    await hint.filter({hasText:'Windows 无法启动投稿窗口。'}).waitFor();
    await open.click();
    await answer('open',{opened:true});
    await hint.filter({hasText:'任务栏'}).waitFor();
    await page.locator('#work-title').fill('离线交互测试');
    await transfer.click();
    await feedback.filter({hasText:'正在打开'}).waitFor();
    await answer('open',{opened:true});
    await feedback.filter({hasText:'已打开投稿助手'}).waitFor();
    assert.equal(saved.work_title,'离线交互测试');
    assert.deepEqual(launches.map(x=>x.id || null),[null,null,'demo']);
    assert.equal(launches[2].revision,1);
    await page.reload({waitUntil:'domcontentloaded'});
    await answer('drafts',{drafts:[]});
    await answer('component', {error:{code:'unavailable',message:'组件暂时无法检查'}}, 503);
    await hint.filter({hasText:'未能检查投稿组件'}).waitFor();
    await open.click();
    await answer('open',{opened:true});
    await hint.filter({hasText:'已打开投稿助手'}).waitFor();
    console.log('Passed: standalone launch during loading, visible progress, same-page retry, saved draft handoff and failed status preflight.');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error);process.exitCode=1;});
