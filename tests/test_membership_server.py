# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Isolated HTTP, identity, callback and running-job authorization regressions."""
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fagao.runtime import WorkbenchRuntime
from fagao.server import WorkbenchServer
from fagao.store import AppError, Store


class SyntheticMembership:
    """A deliberately injected test identity; never used by an executable path."""
    def __init__(self, root):
        self.root = Path(root)
        self.account_id = None
        self.generation = 'anonymous-0'
        self.reason = 'not_authenticated'
        self.counter = 0
        self.pending = None

    def login(self, account='A'):
        self.counter += 1
        self.account_id = account
        self.generation = str(self.counter)
        self.reason = 'active'

    def status(self, refresh=False):
        return {'allowed': self.reason == 'active', 'reason': self.reason,
                'access_mode': 'active' if self.reason == 'active' else 'read_only' if self.reason == 'expired' else 'locked',
                'can_read_history': self.reason in ('active', 'expired'),
                'account_id': self.account_id, 'generation': self.generation,
                'account': {'id': self.account_id, 'display_name': '测试' + self.account_id} if self.account_id else None,
                'membership_expires_at': '2099-01-01T00:00:00+00:00',
                'permit_expires_at': '2099-01-01T00:00:00+00:00',
                'server_time': '2026-11-09T00:00:00+00:00', 'policy_version': 'synthetic'}

    def require(self, account_id=None, operation='use', generation=None):
        if (account_id is not None and account_id != self.account_id) or (generation is not None and generation != self.generation):
            raise AppError('membership_account_changed', '账号已切换。', 403)
        if self.reason != 'active' and not (self.reason == 'expired' and operation in ('read', 'history')):
            raise AppError('membership_' + self.reason, '会员验证未通过。', 403)
        return self.status()

    def account_directory(self, account_id):
        return self.root / 'accounts' / hashlib.sha256(account_id.encode()).hexdigest()

    def check_expected(self, expected_account, expected_generation):
        if expected_generation is not None and (expected_generation != self.generation or expected_account != self.account_id):
            raise AppError('membership_account_changed', '账号已切换。', 403)

    def begin_authorization(self, redirect_uri, *, expected_account=None, expected_generation=None):
        self.check_expected(expected_account, expected_generation)
        self.pending = (redirect_uri, 'single-use-state')
        return {'authorization_url': 'https://www.fagaochuangzuo.com/workbench/authorize?state=single-use-state',
                'state': 'single-use-state'}

    def complete_authorization(self, code, state, redirect_uri):
        if self.pending != (redirect_uri, state) or code != 'single-use-code':
            raise AppError('membership_invalid_callback', '授权回调无效。', 403)
        self.pending = None
        self.login()
        return self.status()

    def cancel_authorization(self, state, redirect_uri):
        if self.pending != (redirect_uri, state):
            raise AppError('membership_invalid_callback', '授权回调无效。', 403)
        self.pending = None
        return {**self.status(), 'authorization_cancelled': True}

    def logout(self, *, expected_account=None, expected_generation=None):
        self.check_expected(expected_account, expected_generation)
        self.account_id = None
        self.counter += 1
        self.generation = 'anonymous-' + str(self.counter)
        self.reason = 'not_authenticated'
        return self.status()


class MembershipServerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.member = SyntheticMembership(self.temp.name)
        self.runtime = WorkbenchRuntime(self.temp.name, membership=self.member)
        self.server = WorkbenchServer(('127.0.0.1', 0), runtime=self.runtime)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self):
        self.runtime.stop_all_jobs()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, method='GET', body=None, headers=None):
        request_headers = dict(headers or {})
        request_headers.setdefault('X-Fagao-Generation', self.member.generation)
        if self.member.account_id:
            request_headers.setdefault('X-Fagao-Account', self.member.account_id)
            request_headers.setdefault('X-Fagao-Generation', self.member.generation)
        if method == 'POST':
            request_headers.setdefault('Content-Type', 'application/json')
            request_headers.setdefault('X-Fagao-Request', '1')
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        connection.request(method, path, body=json.dumps(body or {}) if method == 'POST' else None,
                           headers=request_headers)
        response = connection.getresponse()
        data = response.read()
        status, result_headers = response.status, dict(response.getheaders())
        connection.close()
        try: data = json.loads(data)
        except (ValueError, UnicodeError): pass
        return status, data, result_headers

    def seed(self, account='A'):
        self.member.login(account)
        context = self.runtime.capture()
        snapshot = context.store.save_snapshot({'platform': 'fanqie', 'channel': '女频', 'category': '古言脑洞',
            'board': '新书榜', 'books': [{'id': 'private-book-' + account, 'title': '仅账号' + account + '可见'}]}, imported=True)
        return context, snapshot

    def wait_job(self, context, job_id):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            job = context.store.job(job_id)
            if job['status'] not in ('pending', 'running', 'cancelling'):
                return job
            time.sleep(.01)
        self.fail('job did not finish')

    def test_unconnected_boot_does_not_open_anonymous_or_account_database(self):
        self.assertEqual(self.request('/')[0], 200)
        status, body, _ = self.request('/api/health')
        self.assertEqual(status, 200)
        self.assertEqual(body['data_dir'], str(Path(self.temp.name).resolve()))
        self.assertEqual(self.request('/api/network')[0], 200)
        self.assertFalse(self.request('/api/membership/status')[1]['allowed'])
        self.assertEqual(list(Path(self.temp.name).rglob('*.sqlite3')), [])

    def test_all_content_routes_are_protected_before_dispatch(self):
        gets = ['/api/state', '/api/overview', '/api/context', '/api/snapshots/id', '/api/tasks/id',
                '/api/jobs/id', '/api/reports/id', '/api/texts/id', '/api/texts/id/download']
        posts = ['/api/collect', '/api/overview/refresh', '/api/jobs/more', '/api/catalog', '/api/fetch-text',
                 '/api/translation-context', '/api/insights', '/api/view', '/api/favorite', '/api/import/snapshot',
                 '/api/import/overseas-html', '/api/import/text', '/api/resolve', '/api/prepare', '/api/reports', '/api/config', '/api/auth']
        for path in gets:
            with self.subTest(path=path): self.assertEqual(self.request(path)[0], 403)
        for path in posts:
            with self.subTest(path=path): self.assertEqual(self.request(path, 'POST')[0], 403)
        self.assertEqual(list(Path(self.temp.name).rglob('*.sqlite3')), [])

    def test_existing_cross_site_and_host_protection_covers_membership_routes(self):
        for path in ('/', '/api/membership/status', '/api/health'):
            self.assertEqual(self.request(path, headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertEqual(self.request('/api/membership/connect', 'POST', headers={'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('/api/membership/logout', 'POST', headers={'X-Fagao-Request': ''})[0], 403)
        self.assertEqual(self.request('/api/health', headers={'Host': 'evil.example'})[0], 403)

    def test_browser_cross_site_redirect_can_load_only_top_level_static_shell(self):
        navigation = {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate',
                      'Sec-Fetch-Dest': 'document', 'Origin': 'https://www.fagaochuangzuo.com'}
        for path in ('/', '/index.html', '/?authorization=cancelled'):
            status, body, headers = self.request(path, headers=navigation)
            self.assertEqual(status, 200, path)
            self.assertIn('text/html', headers['Content-Type'])
            self.assertNotIn('Access-Control-Allow-Origin', headers)
        for path in ('/api/state', '/api/membership/status', '/api/health', '/api/texts/id/download', '/app.js', '/styles.css'):
            self.assertEqual(self.request(path, headers=navigation)[0], 403, path)
        self.assertEqual(self.request('/', 'POST', headers=navigation)[0], 403)
        self.assertEqual(self.request('/', headers={**navigation, 'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('/', headers={**navigation, 'Sec-Fetch-Dest': 'iframe'})[0], 403)
        self.assertEqual(self.request('/', headers={**navigation, 'Sec-Fetch-Mode': 'cors'})[0], 403)

    def test_both_callback_redirect_chains_accept_browser_navigation_metadata(self):
        navigation = {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document'}
        for callback in ('/auth/callback?code=single-use-code&state=single-use-state',
                         '/auth/callback?error=access_denied&state=single-use-state'):
            self.request('/api/membership/connect', 'POST')
            status, _, headers = self.request(callback, headers=navigation)
            self.assertEqual(status, 303)
            final_status, body, final_headers = self.request(headers['Location'], headers=navigation)
            self.assertEqual(final_status, 200)
            self.assertIn('text/html', final_headers['Content-Type'])
            self.assertNotIn(b'"error"', body)

    def test_connect_ignores_supplied_redirect_and_callback_is_narrow(self):
        status, _, _ = self.request('/api/membership/connect', 'POST', {'redirect_uri': 'https://evil.example/cb'})
        self.assertEqual(status, 200)
        self.assertEqual(self.member.pending[0], f'http://127.0.0.1:{self.port}/auth/callback')
        callback = '/auth/callback?code=single-use-code&state=single-use-state'
        self.assertEqual(self.request(callback, headers={'Host': f'localhost:{self.port}', 'Sec-Fetch-Site': 'cross-site'})[0], 403)
        status, body, headers = self.request(callback, headers={'Sec-Fetch-Site': 'cross-site', 'Origin': 'https://www.fagaochuangzuo.com'})
        self.assertEqual(status, 303)
        self.assertEqual(headers['Location'], '/')
        self.assertNotIn(b'single-use', body)
        self.assertEqual(self.request(callback, headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertEqual(self.request('/api/state', headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)

    def test_duplicate_callback_parameters_rejected(self):
        self.request('/api/membership/connect', 'POST')
        status, _, _ = self.request('/auth/callback?code=single-use-code&state=single-use-state&state=second',
                                   headers={'Sec-Fetch-Site': 'cross-site'})
        self.assertEqual(status, 400)
        self.assertIsNone(self.member.account_id)

    def test_cancel_callback_consumes_pending_and_preserves_connected_account(self):
        self.member.login('A')
        generation = self.member.generation
        self.request('/api/membership/connect', 'POST')
        denied = '/auth/callback?error=access_denied&state=single-use-state&error_description=untrusted-message'
        status, body, headers = self.request(denied, headers={'Sec-Fetch-Site': 'cross-site'})
        self.assertEqual(status, 303)
        self.assertEqual(headers['Location'], '/?authorization=cancelled')
        self.assertNotIn(b'untrusted-message', body)
        self.assertEqual(self.member.account_id, 'A')
        self.assertEqual(self.member.generation, generation)
        self.assertIsNone(self.member.pending)
        self.assertEqual(self.request(denied, headers={'Sec-Fetch-Site': 'cross-site'})[0], 403)

    def test_cancel_callback_rejects_wrong_state_host_and_mixed_success(self):
        self.request('/api/membership/connect', 'POST')
        for suffix, expected in (
                ('error=access_denied&state=wrong', 403),
                ('error=access_denied&state=single-use-state&state=duplicate', 400),
                ('error=access_denied&state=single-use-state&code=single-use-code', 400),
                ('error=unknown&state=single-use-state', 400)):
            self.assertEqual(self.request('/auth/callback?' + suffix, headers={'Sec-Fetch-Site': 'cross-site'})[0], expected)
        denied = '/auth/callback?error=access_denied&state=single-use-state'
        self.assertEqual(self.request(denied, headers={'Host': f'localhost:{self.port}', 'Sec-Fetch-Site': 'cross-site'})[0], 403)
        self.assertIsNotNone(self.member.pending)

    def test_account_databases_and_direct_resource_reads_are_isolated(self):
        context_a, snapshot_a = self.seed()
        text = context_a.store.import_text(snapshot_a['id'], 'private-book-A', '账号A正文' * 10, '测试片段')
        task = context_a.store.prepare_task({'snapshot_id': snapshot_a['id'], 'book_id': 'private-book-A'}, '测试')
        report = context_a.store.save_report(task['id'], '账号A报告' * 10)
        context_b, snapshot_b = self.seed('B')
        self.assertNotEqual(context_a.store.directory, context_b.store.directory)
        state = self.request('/api/state')[1]
        self.assertEqual([s['id'] for s in state['snapshots']], [snapshot_b['id']])
        for path in (f"/api/snapshots/{snapshot_a['id']}", f"/api/texts/{text['id']}",
                     f"/api/texts/{text['id']}/download", f"/api/tasks/{task['id']}", f"/api/reports/{report['id']}"):
            with self.subTest(path=path): self.assertEqual(self.request(path)[0], 404)
        with self.assertRaises(AppError): context_a.require()

    def test_same_account_relogin_invalidates_previous_task_generation(self):
        context, _ = self.seed()
        self.member.logout()
        self.member.login('A')
        with self.assertRaises(AppError): context.require()
        new = self.runtime.capture()
        self.assertNotEqual(new.generation, context.generation)
        self.assertEqual(new.store.directory, context.store.directory)

    def test_stale_window_cannot_read_mutate_cancel_or_logout_other_account(self):
        context, snapshot = self.seed()
        old_headers = {'X-Fagao-Account': context.account_id, 'X-Fagao-Generation': context.generation}
        context_b, snapshot_b = self.seed('B')
        for path, method, payload in (
                ('/api/state', 'GET', None),
                ('/api/view', 'POST', {'mode': 'long', 'platform': 'fanqie', 'snapshot_id': snapshot_b['id']}),
                ('/api/jobs/cancel', 'POST', {'job_id': 'unknown'}),
                ('/api/membership/logout', 'POST', {}),
                ('/api/membership/connect', 'POST', {})):
            with self.subTest(path=path):
                self.assertEqual(self.request(path, method, payload, old_headers)[0], 403)
        self.assertEqual(self.member.account_id, 'B')
        self.assertIsNone(self.member.pending)

    def test_missing_or_old_generation_is_rejected_including_download(self):
        context, snapshot = self.seed()
        text = context.store.import_text(snapshot['id'], 'private-book-A', '隔离正文' * 10, '测试片段')
        for path in ('/api/state', f"/api/texts/{text['id']}/download"):
            self.assertEqual(self.request(path, headers={'X-Fagao-Generation': ''})[0], 403)
            self.assertEqual(self.request(path, headers={'X-Fagao-Account': ''})[0], 403)
        old_generation = context.generation
        self.member.login('A')
        self.assertEqual(self.request('/api/state', headers={'X-Fagao-Generation': old_generation})[0], 403)

    def test_response_rechecks_identity_after_in_flight_account_change(self):
        context, _ = self.seed()
        original = context.store.state
        def delayed():
            result = original()
            self.member.login('B')
            return result
        with patch.object(context.store, 'state', side_effect=delayed):
            status, body, _ = self.request('/api/state')
        self.assertEqual(status, 403)
        self.assertNotIn('snapshots', body)

    def test_download_rechecks_identity_before_sending_text(self):
        context, snapshot = self.seed()
        text = context.store.import_text(snapshot['id'], 'private-book-A', '不能泄露的正文' * 10, '测试片段')
        original = context.store.text_record
        def delayed(text_id):
            result = original(text_id)
            self.member.login('B')
            return result
        with patch.object(context.store, 'text_record', side_effect=delayed):
            status, body, _ = self.request(f"/api/texts/{text['id']}/download")
        self.assertEqual(status, 403)
        self.assertNotIn('不能泄露', str(body))

    def test_expired_member_may_cancel_own_job_without_content_response(self):
        context, _ = self.seed()
        context.store.save_job({'id': 'safe-cancel', 'kind': 'collect', 'status': 'running',
                                'scope_key': 'test', 'title': '隐藏资料', 'created_at': '2026-11-09T00:00:00+00:00'})
        self.member.reason = 'expired'
        self.assertEqual(self.request('/api/state')[0], 200)
        self.assertEqual(self.request('/api/membership/status')[1]['account_id'], 'A')
        status, body, _ = self.request('/api/jobs/cancel', 'POST', {'job_id': 'safe-cancel'})
        self.assertEqual(status, 200)
        self.assertEqual(body, {'id': 'safe-cancel', 'status': 'cancelled'})

    def test_collection_expiry_retains_partial_and_stops_next_page(self):
        context, _ = self.seed()
        calls = []
        def collect(platform, options, directory, progress):
            calls.append(1)
            raw = {'platform': platform, 'channel': options['channel'], 'category': options['category'],
                   'board': options['board'], 'books': [{'id': 'partial', 'title': '已取得'}]}
            progress('第一页', snapshot=raw, has_more=True)
            self.member.reason = 'expired'
            progress('第二页之前')
            calls.append(2)
            return raw
        with patch('fagao.collectors.collect', side_effect=collect):
            result = context.jobs.start('fanqie', {'channel': '女频', 'category': '古言脑洞', 'board': '新书榜'})
            job = self.wait_job(context, result['job_id'])
        self.assertEqual(calls, [1])
        self.assertEqual(job['status'], 'interrupted')
        self.assertEqual(job['error_code'], 'membership_expired')
        self.assertEqual(context.store.snapshot(job['partial_snapshot_id'])['books'][0]['id'], 'partial')

    def test_queued_worker_rechecks_before_invocation(self):
        context, _ = self.seed()
        job = {'id': 'queued', 'status': 'pending', 'scope_key': 'test', 'created_at': '2026-11-09T00:00:00+00:00'}
        context.store.save_job(job)
        self.member.reason = 'expired'
        invoked = []
        context.jobs._run_authorized(lambda item: invoked.append(item), job)
        self.assertEqual(invoked, [])
        self.assertEqual(context.store.job('queued')['status'], 'interrupted')

    def test_last_in_flight_chapter_is_saved_after_membership_expires(self):
        from test_server import fragmented_single_chapter
        context, snapshot = self.seed()
        def fetch(*args, **kwargs):
            self.member.reason = 'expired'
            return fragmented_single_chapter(book_id='private-book-A', title='仅账号A可见')
        with patch('fagao.fanqie_content.fetch_book_text', side_effect=fetch):
            result = context.jobs.start_text(snapshot['id'], 'private-book-A', chapters=1)
            job = self.wait_job(context, result['job_id'])
        self.assertEqual(job['status'], 'interrupted')
        self.assertEqual(job['error_code'], 'membership_expired')
        record = context.store.text_record(job['text_id'])
        self.assertEqual(record['content'], '文' * 4439)
        self.assertFalse(record['complete'])
        self.assertFalse(record['coverage_complete'])
        self.assertEqual(self.request(job['download_url'])[0], 200)

    def test_expired_job_cannot_be_reused_or_extended(self):
        context, _ = self.seed()
        self.member.reason = 'expired'
        with self.assertRaises(AppError): context.jobs.start('fanqie', {})
        with self.assertRaises(AppError): context.jobs.start_overview()
        with self.assertRaises(AppError): context.jobs.more('unknown')
        self.assertEqual(context.store.state()['jobs'], [])

    def test_expired_history_reads_navigation_and_saved_statistics_remain_available(self):
        context, snapshot = self.seed()
        text = context.store.import_text(snapshot['id'], 'private-book-A', '历史正文' * 10, '测试片段')
        task = context.store.prepare_task({'snapshot_id': snapshot['id'], 'book_id': 'private-book-A'}, '测试')
        report = context.store.save_report(task['id'], '历史报告' * 10)
        self.member.reason = 'expired'
        paths = ['/api/state', '/api/context', '/api/overview', f"/api/snapshots/{snapshot['id']}",
                 f"/api/texts/{text['id']}", f"/api/texts/{text['id']}/download", f"/api/tasks/{task['id']}",
                 f"/api/reports/{report['id']}"]
        for path in paths:
            with self.subTest(path=path): self.assertEqual(self.request(path)[0], 200)
        self.assertEqual(self.request('/api/view', 'POST', {'mode': 'long', 'platform': 'fanqie',
            'snapshot_id': snapshot['id'], 'selected_id': 'private-book-A'})[0], 200)
        self.assertEqual(self.request('/api/insights', 'POST', {'snapshot_id': snapshot['id']})[0], 200)
        self.assertEqual(self.request('/api/resolve', 'POST', {'query': '分析刚选的这本'})[0], 200)
        self.assertEqual(context.store.state()['jobs'], [])

    def test_expired_history_context_never_authorizes_new_jobs_or_writes(self):
        context, snapshot = self.seed()
        self.member.reason = 'expired'
        read_context = self.runtime.capture(operation='read')
        self.assertTrue(read_context.require()['can_read_history'])
        self.assertIs(read_context.jobs, context.jobs)
        with self.assertRaises(AppError): read_context.jobs.start_overview()
        with self.assertRaises(AppError): read_context.jobs.start('fanqie', {})
        for path in ('/api/collect', '/api/overview/refresh', '/api/jobs/more', '/api/catalog',
                     '/api/fetch-text', '/api/translation-context', '/api/prepare', '/api/import/snapshot',
                     '/api/import/text', '/api/import/overseas-html', '/api/favorite', '/api/reports'):
            with self.subTest(path=path): self.assertEqual(self.request(path, 'POST')[0], 403)
        # A fresh server/CLI must be able to open saved history after expiry,
        # while its newly created scheduler still requires active membership.
        other = WorkbenchRuntime(self.temp.name, membership=self.member).capture(operation='history')
        self.assertEqual(other.store.snapshot(snapshot['id'])['id'], snapshot['id'])
        with self.assertRaises(AppError): other.jobs.start_overview()

    def test_nonexpiry_denial_does_not_receive_history_privilege(self):
        self.seed()
        for reason in ('suspended', 'device_revoked', 'not_eligible', 'verification_unavailable'):
            self.member.reason = reason
            with self.subTest(reason=reason): self.assertEqual(self.request('/api/state')[0], 403)

    def test_logout_and_connect_pass_atomic_expected_identity_to_client(self):
        for action in ('logout', 'begin_authorization'):
            self.member.login('A')
            captured = {'X-Fagao-Account': 'A', 'X-Fagao-Generation': self.member.generation}
            original = getattr(self.member, action)
            def concurrent_switch(*args, **kwargs):
                self.member.login('B')
                return original(*args, **kwargs)
            with patch.object(self.member, action, side_effect=concurrent_switch):
                path = '/api/membership/logout' if action == 'logout' else '/api/membership/connect'
                self.assertEqual(self.request(path, 'POST', headers=captured)[0], 403)
            self.assertEqual(self.member.account_id, 'B')

    def test_stale_anonymous_window_cannot_replace_current_authorization(self):
        anonymous_generation = self.member.generation
        self.member.login('B')
        headers = {'X-Fagao-Account': '', 'X-Fagao-Generation': anonymous_generation}
        for path in ('/api/membership/connect', '/api/membership/logout'):
            self.assertEqual(self.request(path, 'POST', headers=headers)[0], 403)
        self.assertEqual(self.member.account_id, 'B')
        self.assertIsNone(self.member.pending)

    def test_database_symlink_is_rejected_before_opening_or_reading(self):
        context_a, _ = self.seed()
        context_b, _ = self.seed('B')
        context_a.store.db.unlink()
        context_a.store.db.symlink_to(context_b.store.db)
        self.member.login('A')
        with self.assertRaises(AppError) as caught:
            self.runtime.capture(operation='read')
        self.assertEqual(caught.exception.code, 'membership_storage_error')
        self.assertEqual(self.request('/api/state')[0], 503)

    def seed_legacy(self):
        legacy = Store(self.temp.name)
        snapshot = legacy.save_snapshot({'platform': 'fanqie', 'channel': '女频', 'category': '古言脑洞',
            'board': '新书榜', 'books': [{'id': 'anonymous-book', 'title': '待认领旧资料'}]}, imported=True)
        self.member.login('A')
        return legacy, snapshot

    def test_legacy_inspection_precedes_store_and_blocks_automatic_occupation(self):
        legacy, _ = self.seed_legacy()
        target = self.member.account_directory('A') / 'workbench.sqlite3'
        status, body, _ = self.request('/api/legacy-data/status')
        self.assertEqual(status, 200)
        self.assertTrue(body['decision_required'])
        self.assertEqual(body['counts']['snapshots'], 1)
        self.assertFalse(target.exists())
        for path in ('/api/overview/refresh', '/api/collect', '/api/import/snapshot', '/api/prepare', '/api/view'):
            status, body, _ = self.request(path, 'POST', {'automatic': True})
            self.assertEqual(status, 409, path)
            self.assertEqual(body['error']['code'], 'legacy_decision_required')
        context = self.runtime.capture(operation='read')
        with self.assertRaises(AppError): context.jobs.start_overview(automatic=True)
        self.assertEqual(context.store.state()['snapshots'], [])
        self.assertEqual(context.store.state()['jobs'], [])
        self.assertEqual(len(legacy.state()['snapshots']), 1)

    def test_explicit_legacy_claim_copies_once_and_masks_it_from_other_account(self):
        legacy, snapshot = self.seed_legacy()
        self.assertEqual(self.request('/api/legacy-data/claim', 'POST', {'confirmed': 'true'})[0], 400)
        status, receipt, _ = self.request('/api/legacy-data/claim', 'POST', {'confirmed': True})
        self.assertEqual(status, 200)
        self.assertTrue(receipt['original_preserved'])
        self.assertEqual(self.request('/api/state')[1]['snapshots'][0]['id'], snapshot['id'])
        self.assertFalse(self.request('/api/legacy-data/status')[1]['decision_required'])
        repeated = self.request('/api/legacy-data/claim', 'POST', {'confirmed': True})[1]
        self.assertTrue(repeated['repeated'])
        self.member.login('B')
        other = self.request('/api/legacy-data/status')[1]
        self.assertFalse(other['available'])
        self.assertEqual(other['counts'], {})
        self.assertFalse(other['decision_required'])
        self.assertEqual(self.request('/api/state')[1]['snapshots'], [])
        self.assertEqual(legacy.snapshot(snapshot['id'])['books'][0]['id'], 'anonymous-book')

    def test_legacy_skip_is_explicit_persisted_and_account_scoped(self):
        legacy, snapshot = self.seed_legacy()
        self.assertEqual(self.request('/api/legacy-data/skip', 'POST', {})[0], 400)
        self.assertEqual(self.request('/api/legacy-data/skip', 'POST', {'confirmed': True})[0], 200)
        self.assertFalse(self.request('/api/legacy-data/status')[1]['decision_required'])
        another = WorkbenchRuntime(self.temp.name, membership=self.member)
        self.assertEqual(another.capture().store.state()['snapshots'], [])
        self.member.login('B')
        self.assertTrue(self.request('/api/legacy-data/status')[1]['decision_required'])
        self.assertEqual(legacy.snapshot(snapshot['id'])['id'], snapshot['id'])

    def test_legacy_endpoints_require_active_bound_identity(self):
        self.seed_legacy()
        generation = self.member.generation
        self.member.reason = 'expired'
        for path, method in (('/api/legacy-data/status', 'GET'), ('/api/legacy-data/claim', 'POST'), ('/api/legacy-data/skip', 'POST')):
            self.assertEqual(self.request(path, method, {'confirmed': True})[0], 403)
        self.member.login('B')
        stale = {'X-Fagao-Account': 'A', 'X-Fagao-Generation': generation}
        self.assertEqual(self.request('/api/legacy-data/claim', 'POST', {'confirmed': True}, stale)[0], 403)
        self.assertFalse((self.member.account_directory('B') / 'workbench.sqlite3').exists())

    def test_legacy_claim_rechecks_captured_identity_inside_claim(self):
        self.seed_legacy()
        from fagao.legacy_claim import claim_legacy
        def raced(runtime, *args, **kwargs):
            self.member.login('B')
            return claim_legacy(runtime, *args, **kwargs)
        with patch('fagao.legacy_claim.claim_legacy', side_effect=raced):
            self.assertEqual(self.request('/api/legacy-data/claim', 'POST', {'confirmed': True})[0], 403)
        self.assertFalse((self.member.account_directory('B') / 'workbench.sqlite3').exists())

    def test_cli_runtime_creation_does_not_recover_live_http_jobs(self):
        context, _ = self.seed()
        context.store.save_job({'id': 'live', 'status': 'running', 'kind': 'collect', 'scope_key': 'test',
                                'created_at': '2026-11-09T00:00:00+00:00'})
        cli = WorkbenchRuntime(self.temp.name, membership=self.member)
        cli.capture(operation='context')
        self.assertEqual(context.store.job('live')['status'], 'running')


if __name__ == '__main__':
    unittest.main()
