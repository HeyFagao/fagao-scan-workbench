// 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
'use strict';

// This acceptance runs against an installed Windows workbench and its packaged
// personal editor. Both browser buttons must open a visible native window.
const assert = require('node:assert/strict');
const {execFileSync, spawn} = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {parseArgs} = require('node:util');
const {chromium} = require('playwright');

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const draft = {work_title: '发糕 Windows 试投稿', pen_name: '测试笔名',
  subject: '短篇投稿｜本地验收', body: '合成稿第一段。\n第二段用于核对换行与中文。'};

async function waitFor(check, message) {
  const end = Date.now() + 35000;
  while (Date.now() < end) {
    try {
      const result = await check();
      if (result) return result;
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
    await delay(150);
  }
  throw new Error(message);
}

async function readJSON(file) {
  return JSON.parse(await fs.readFile(file, 'utf8'));
}

async function insideFixture(directory) {
  const actual = await fs.realpath(directory);
  for (const temp of [process.env.RUNNER_TEMP, os.tmpdir()].filter(Boolean)) {
    const relative = path.relative(await fs.realpath(temp), actual);
    if (relative && !relative.startsWith('..' + path.sep) && relative !== '..' && !path.isAbsolute(relative)) {
      return actual;
    }
  }
  throw new Error('Use an isolated fixture directory inside a temporary directory.');
}

function nativeProcesses(executable, stop = false) {
  const script = `
    $ErrorActionPreference='Stop'
    $items=@(Get-Process -Name FagaoSubmission -ErrorAction SilentlyContinue |
      Where-Object { $_.Path -eq $env:FAGAO_ACCEPTANCE_EXE })
    if ($env:FAGAO_ACCEPTANCE_STOP -eq '1') {
      foreach ($entry in $items) {
        if (!$entry.HasExited) {
          try { $entry.Kill($true) } catch { if (!$entry.HasExited) { throw } }
          if (!$entry.WaitForExit(12000)) { throw 'Isolated editor did not stop' }
        }
      }
    }
    ConvertTo-Json -InputObject @($items | ForEach-Object { $_.Id }) -Compress
  `;
  return JSON.parse(execFileSync('pwsh.exe', ['-NoProfile', '-NonInteractive', '-Command', script],
    {encoding: 'utf8', timeout: 30000, env: {...process.env,
      FAGAO_ACCEPTANCE_EXE: executable, FAGAO_ACCEPTANCE_STOP: stop ? '1' : '0'}}).trim());
}

async function readRecipientCounts(profile) {
  const state = await readJSON(path.join(profile, 'novel-submission-state.json'));
  assert.equal(state.accounts.length, 0);
  assert.equal(state.jobs.length, 0);
  assert.equal(state.recipients.length, 226);
  assert.equal(state.recipients.filter(editor => editor.source === 'seed').length, 226);
  return state.recipients.filter(editor => editor.enabled).length;
}

// The renderer can acknowledge a handoff even if its window is hidden. Check
// the OS window owned by this exact fixture process. Use its normal close
// button behavior (WM_CLOSE) to exercise the app's own hide-to-tray handler.
function checkNativeWindow(executable, pid, action = 'visible') {
  assert(Number.isInteger(pid) && pid > 0);
  assert(['visible', 'minimize', 'close-to-tray'].includes(action));
  const script = `
    $ErrorActionPreference = 'Stop'
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class FixtureWindow {
  [DllImport("user32")] public static extern bool IsWindowVisible(IntPtr window);
  [DllImport("user32")] public static extern bool IsIconic(IntPtr window);
  [DllImport("user32")] public static extern IntPtr MonitorFromWindow(IntPtr window, uint flags);
  [DllImport("user32")] public static extern bool ShowWindowAsync(IntPtr window, int command);
  [DllImport("user32", ExactSpelling=true)] public static extern bool PostMessageW(IntPtr window, uint msg, UIntPtr w, IntPtr l);
}
'@
    $editor = Get-Process -Id ([int]$env:FAGAO_ACCEPTANCE_PID)
    if ($editor.Path -ne $env:FAGAO_ACCEPTANCE_EXE) { throw 'Unexpected editor process path' }
    $until = [DateTime]::UtcNow.AddSeconds(8)
    do {
      $editor.Refresh()
      $window = $editor.MainWindowHandle
      if ($window -ne [IntPtr]::Zero -and [FixtureWindow]::IsWindowVisible($window) -and
          ![FixtureWindow]::IsIconic($window) -and [FixtureWindow]::MonitorFromWindow($window, 0) -ne [IntPtr]::Zero) { break }
      if ([DateTime]::UtcNow -ge $until) { throw 'No visible, restored editor window on a display' }
      Start-Sleep -Milliseconds 120
    } while ($true)
    if ($env:FAGAO_ACCEPTANCE_WINDOW -eq 'visible') { exit 0 }
    if ($env:FAGAO_ACCEPTANCE_WINDOW -eq 'minimize') {
      [void][FixtureWindow]::ShowWindowAsync($window, 6)
    } elseif (![FixtureWindow]::PostMessageW($window, 0x10, [UIntPtr]::Zero, [IntPtr]::Zero)) {
      throw 'The native close request was rejected'
    }
    $until = [DateTime]::UtcNow.AddSeconds(8)
    do {
      if ($editor.HasExited) { throw 'Editor exited instead of remaining available' }
      if ($env:FAGAO_ACCEPTANCE_WINDOW -eq 'minimize') {
        if ([FixtureWindow]::IsIconic($window)) { exit 0 }
      } elseif (![FixtureWindow]::IsWindowVisible($window)) { exit 0 }
      Start-Sleep -Milliseconds 120
    } while ([DateTime]::UtcNow -lt $until)
    throw 'Could not put the editor window in the requested state'
  `;
  execFileSync('pwsh.exe', ['-NoProfile', '-NonInteractive', '-Command', script],
    {encoding:'utf8', timeout:25000, env:{...process.env, FAGAO_ACCEPTANCE_EXE:executable,
      FAGAO_ACCEPTANCE_PID:String(pid), FAGAO_ACCEPTANCE_WINDOW:action}});
}

async function openEditor(page, withDraft = false) {
  const responsePromise = page.waitForResponse(response => (
    new URL(response.url()).pathname === '/api/submissions/open' && response.request().method() === 'POST'
  ), {timeout: 85000});
  await page.locator(withDraft ? '#open-draft' : '#open-assistant').click();
  const response = await responsePromise;
  assert.equal(Boolean(response.request().postDataJSON()?.id), withDraft);
  assert.equal(response.status(), 200);
  assert.equal((await response.json()).opened, true);
  await page.locator(withDraft ? '#editor-feedback' : '#assistant-hint').filter({hasText: '已打开投稿助手'}).waitFor();
}

async function run() {
  const options = parseArgs({options: {'data-dir': {type: 'string'}, url: {type: 'string'},
    receipt: {type: 'string'}}}).values;
  assert.equal(process.platform, 'win32');
  const origin = new URL(options.url);
  assert.equal(origin.hostname, '127.0.0.1');
  assert.equal(origin.protocol, 'http:');
  assert.equal(origin.pathname, '/');
  const data = await insideFixture(options['data-dir']);
  const profile = path.join(data, 'submission-native-profile');
  const handoffs = path.join(data, 'submission-handoff');
  const directory = path.join(data, 'components', 'submission');
  const executable = await fs.realpath(path.join(directory, 'FagaoSubmission', 'FagaoSubmission.exe'));
  const marker = path.join(profile, 'workbench-runtime.json');
  const debugInfo = path.join(profile, 'DevToolsActivePort');
  const installReceipt = await readJSON(path.join(directory, 'receipt.json'));
  const report = {platform: process.platform, arch: process.arch, status: 'failed',
    component_asar_sha256: installReceipt.asar_sha256, checks: {}};
  const receipt = path.resolve(options.receipt);
  let browser, desktop;
  // The fixture owns the entire install path and begins with an unused profile.
  assert.deepEqual(nativeProcesses(executable), []);
  await assert.rejects(fs.stat(path.join(profile, 'novel-submission-state.json')), {code: 'ENOENT'});
  try {
    const health = await (await fetch(new URL('/api/health', origin))).json();
    assert.equal((await fs.realpath(health.data_dir)).toLowerCase(), data.toLowerCase());
    const {asar_sha256} = installReceipt;
    assert.equal(crypto.createHash('sha256').update(await fs.readFile(path.join(
      path.dirname(executable), 'resources', 'app.asar'))).digest('hex'), asar_sha256);
    browser = await chromium.launch({channel: 'msedge', headless: false});
    const page = await browser.newPage();
    await page.route('**/*', route => {
      const u = new URL(route.request().url());
      return u.origin === origin.origin ? route.continue() : route.abort();
    });
    await page.goto(origin.href);
    await page.getByRole('link', {name: '小说投稿', exact: true}).click();
    await page.waitForURL(new URL('/submissions.html', origin).href);
    report.checks.browser_opened_from_workbench_home = true;
    await openEditor(page);
    const coldPid = (await readJSON(marker)).pid;
    checkNativeWindow(executable, coldPid);
    report.checks.real_browser_empty_cold_launch = true;
    report.checks.real_browser_cold_launch_renderer_ack = true;
    for (const [action, field] of [['minimize', 'minimized_editor_restored'], ['close-to-tray', 'tray_editor_restored']]) {
      checkNativeWindow(executable, coldPid, action);
      await openEditor(page);
      assert.equal((await readJSON(marker)).pid, coldPid);
      checkNativeWindow(executable, coldPid);
      report.checks[field] = true;
    }
    for (const [name, value] of Object.entries(draft)) {
      await page.locator('[name="' + name + '"]').fill(value);
    }
    await page.locator('#save-draft').click();
    await page.locator('#draft-status').filter({hasText: '已保存到本机'}).waitFor();
    await page.reload();
    await page.locator('#draft-list button').filter({hasText: draft.work_title}).click();
    await page.waitForFunction(fields => Object.entries(fields).every(([name, value]) =>
      document.querySelector('[name="' + name + '"]')?.value === value), draft);
    report.checks.browser_draft_persisted = true;
    await openEditor(page, true);
    checkNativeWindow(executable, coldPid);
    assert(nativeProcesses(executable).includes(coldPid));
    assert.equal(await readRecipientCounts(profile), 226);

    // Relaunch only this installed synthetic editor with a loopback debug port
    // so rendered values can be read without changing the workbench's real launcher.
    nativeProcesses(executable, true);
    assert.deepEqual(nativeProcesses(executable), []);
    const proc = spawn(executable, ['--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0',
      '--disable-background-networking'], {cwd: path.dirname(executable), stdio: 'ignore',
      env: {...process.env, FAGAO_SUBMISSION_PROFILE_DIR: profile, FAGAO_SUBMISSION_HANDOFF_DIR: handoffs}});
    await new Promise((resolve, reject) => { proc.once('spawn', resolve); proc.once('error', reject); });
    const debugPort = await waitFor(async () => {
      assert.equal(proc.exitCode, null, 'Packaged editor exited during readback');
      const port = Number((await fs.readFile(debugInfo, 'utf8')).split('\n')[0]);
      return Number.isInteger(port) && port > 0 && port < 65536 && port;
    }, 'No loopback endpoint for the isolated editor');
    desktop = await chromium.connectOverCDP('http://127.0.0.1:' + debugPort, {timeout: 25000});
    const nativePage = await waitFor(() => desktop.contexts().flatMap(ctx => ctx.pages())
      .find(p => p.url().startsWith('novel-assistant://')), 'The packaged composer did not appear');
    const pid = (await readJSON(marker)).pid;
    assert.equal(pid, proc.pid);
    assert(nativeProcesses(executable).includes(pid));
    await openEditor(page, true);
    await nativePage.waitForFunction(expected => {
      const values = Array.from(document.querySelectorAll('input,textarea'), el => el.value);
      return expected.every(value => values.includes(value));
    }, Object.values(draft), {timeout: 25000});
    assert.match(await nativePage.locator('.selection-count').innerText(), /已选\s*0/);
    assert.match(await nativePage.locator('.quick-select-row').innerText(), /226\s*位可选/);
    assert.equal(await nativePage.locator('.recipient-picker-list input:checked').count(), 0);
    assert.equal(await readRecipientCounts(profile), 226);
    report.checks.native_four_fields_match = true;
    report.checks.native_all_226_recipients_enabled = true;
    report.checks.native_zero_selected_recipients = true;
    await nativePage.getByRole('button', {name: '检查并发送自检', exact: true}).click();
    await nativePage.getByText('请先选择本次投稿使用的 QQ 发件账号。', {exact: true}).waitFor();
    assert.equal(await nativePage.getByRole('button', {name: /开始正式投稿/}).count(), 0);
    report.checks.no_formal_submission_before_self_check = true;
    // Preserve one explicit local choice to verify that reinstalling never re-enables it.
    await nativePage.locator('button.nav-item').filter({hasText: '编辑名单'}).click();
    await nativePage.getByRole('switch', {checked: true}).first().click();
    await waitFor(async () => (await readRecipientCounts(profile)) === 225, 'Editor preference did not persist');
    report.checks.one_user_disabled_recipient_persisted = true;
    report.checks.no_sender_accounts_or_delivery_jobs = true;
    report.checks.handoffs_consumed = (await fs.readdir(handoffs)).length === 0;
    assert.equal(report.checks.handoffs_consumed, true);
    report.browser = browser.version();
    report.status = 'passed';
  } catch (error) {
    report.stage_error = String(error.message).split('\n')[0];
    throw error;
  } finally {
    try {
      if (desktop) await desktop.close();
      if (browser) await browser.close();
    } finally {
      try {
        nativeProcesses(executable, true);
        assert.deepEqual(nativeProcesses(executable), []);
        report.checks.fixture_native_processes_closed = true;
      } finally {
        report.checked_utc = new Date().toISOString();
        await fs.writeFile(receipt, JSON.stringify(report, null, 2) + '\n');
      }
    }
  }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
