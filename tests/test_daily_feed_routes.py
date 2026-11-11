# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Verify the normal account and HTTP entrypoints for the licensed feeds."""
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import Mock, patch

from fagao import membership
from fagao.membership import MembershipClient
from fagao.store import AppError
import test_daily_feeds as samples
import test_membership as auth_fixtures
import test_membership_server as http_fixtures


class DailyFeedRouteTests(unittest.TestCase):
    def setUp(self):
        self.http = http_fixtures.MembershipServerTest()
        self.http.setUp()
        self.addCleanup(self.http.tearDown)
        self.http.member.feed_grant = Mock(return_value=samples.grant())
        download = patch('fagao.daily_feeds._download', return_value=samples.TOKENS[10])
        self.fetch = download.start()
        self.addCleanup(download.stop)

    def read(self, board='douyin', **kwargs):
        return self.http.request('/api/daily-boards?board=' + board, **kwargs)

    def sync(self):
        return self.http.request('/api/daily-boards/sync', 'POST', {'board': 'douyin'})

    def test_expiry_reads_history_without_grant_or_download(self):
        self.assertEqual(self.read()[0], 403)
        self.assertEqual(self.sync()[0], 403)
        self.http.member.login('A')
        self.assertIsNone(self.read()[1]['snapshot'])
        status, saved, _ = self.sync()
        self.assertEqual((status, saved['status']), (200, 'updated'))
        self.assertNotIn(samples.TEST_KEY, json.dumps(saved))
        self.http.member.reason = 'expired'
        self.assertEqual(self.read()[1]['snapshot']['rows'], [['11', '合成作品']])
        self.assertEqual(self.sync()[0], 403)
        self.fetch.assert_called_once()
        self.http.member.feed_grant.assert_called_once()

    def test_swap_hides_saved_boards_and_rejects_stale_generation(self):
        self.http.member.login('A')
        old = self.http.member.generation
        self.assertEqual(self.sync()[0], 200)
        self.http.member.login('B')
        self.assertIsNone(self.read()[1]['snapshot'])
        self.assertEqual(self.read(headers={'X-Fagao-Account': 'A', 'X-Fagao-Generation': old})[0], 403)

    def test_bad_board_or_ambiguous_parameters_do_not_contact_source(self):
        self.http.member.login('A')
        for query in ('douyin&board=adapt', 'unknown', 'douyin&revision=', 'douyin&revision=a&revision=b'):
            with self.subTest(query=query):
                self.assertEqual(self.read(query)[0], 400)
        self.fetch.assert_not_called()
        self.http.member.feed_grant.assert_not_called()


class WebsiteFeedClientTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        clock = auth_fixtures.Clock()
        transport = auth_fixtures.Transport(clock)
        self.calls = []
        self.result = samples.grant()

        def send(method, url, payload, token):
            if url.endswith('/feeds/grant'):
                self.calls.append((url, payload, token))
                return self.result
            return transport(method, url, payload, token)

        self.client = MembershipClient(root.name, transport=send, clock=clock)
        self.peer = MembershipClient(root.name, transport=send, clock=clock)
        self.transport = transport
        redirect = 'http://127.0.0.1:13876/auth/callback'
        start = self.client.begin_authorization(redirect)
        self.status = self.client.complete_authorization('synthetic-code', start['state'], redirect)
        self.token = transport.token

    def request(self):
        return self.client.feed_grant('douyin', self.status['account_id'], self.status['generation'])

    def test_device_token_only_sent_to_website_with_captured_identity(self):
        result = self.request()
        self.assertIs(result, self.result)
        [(url, payload, token)] = self.calls
        self.assertEqual(url, 'https://www.fagaochuangzuo.com/api/workbench/feeds/grant')
        self.assertEqual(set(payload), {'device_id', 'client_version', 'board'})
        self.assertEqual((payload['board'], token), ('douyin', self.token))
        with self.assertRaises(AppError):
            self.client.feed_grant('douyin', 'foreign-account', self.status['generation'])
        self.assertEqual(len(self.calls), 1)

    def test_denied_and_missing_grants_have_no_key_in_error_response(self):
        for code in (401, 403, 404, 429, 503):
            with self.subTest(code=code):
                self.result = {'ok': False, 'http_status': code, 'reason': 'no_publication', 'key_b64': samples.TEST_KEY}
                with self.assertRaises(AppError) as err:
                    self.request()
                self.assertEqual(err.exception.status, 403 if code == 401 else code)
                self.assertNotIn(samples.TEST_KEY, str(err.exception))

    def test_missing_website_route_is_not_reported_as_an_empty_board(self):
        self.result = {'ok': False, 'http_status': 404}
        with self.assertRaises(AppError) as err:
            self.request()
        self.assertEqual(err.exception.code, 'daily_feed_unavailable')

    def test_definitive_grant_denial_invalidates_other_client_permit(self):
        self.assertTrue(self.peer.require()['allowed'])
        self.result = {'ok': False, 'http_status': 403}
        with self.assertRaises(AppError) as err:
            self.request()
        self.assertEqual(err.exception.code, 'daily_feed_denied')
        self.transport.failure = OSError('offline')
        for client in (self.client, self.peer):
            with self.assertRaises(AppError) as err:
                client.require()
            self.assertEqual(err.exception.code, 'membership_verification_unavailable')
        self.transport.failure = None
        self.assertTrue(self.client.status(refresh=True)['allowed'])
        self.assertTrue(self.peer.status(refresh=True)['allowed'])
        self.result = {'ok': False, 'http_status': 409, 'reason': 'permit_denied'}
        with self.assertRaises(AppError):
            self.request()
        self.transport.override = auth_fixtures.decision(self.transport.clock, allowed=False,
                                                       reason='device_revoked')
        with self.assertRaises(AppError) as err:
            self.peer.require()
        self.assertEqual(err.exception.code, 'membership_device_revoked')

    def test_unpublished_or_unavailable_feed_preserves_verified_short_lease(self):
        self.assertTrue(self.peer.require()['allowed'])
        self.transport.failure = OSError('offline')
        for code in (404, 429, 503):
            with self.subTest(status=code):
                self.result = {'ok': False, 'http_status': code, 'reason': 'no_publication'}
                with self.assertRaises(AppError):
                    self.request()
                self.assertTrue(self.client.require()['allowed'])
                self.assertTrue(self.peer.require()['allowed'])

    def test_inflight_permit_and_feed_denial_serialize_through_the_state_commit(self):
        self.assertTrue(self.peer.require()['allowed'])
        renewing = threading.Event()
        allow_reply = threading.Event()
        feed_waiting = threading.Event()
        self.result = {'ok': False, 'http_status': 403}
        original_lock = membership._file_lock

        def delayed_permit(*_):
            renewing.set()
            if not allow_reply.wait(5):
                raise TimeoutError('synthetic wait')
            return auth_fixtures.decision(self.transport.clock)

        @contextmanager
        def observed_lock(path):
            if path.name == 'request.lock' and renewing.is_set():
                feed_waiting.set()
            with original_lock(path):
                yield

        self.transport.permit_hook = delayed_permit
        with patch.object(membership, '_file_lock', observed_lock), ThreadPoolExecutor(max_workers=2) as pool:
            permit = pool.submit(self.peer.status, refresh=True)
            try:
                self.assertTrue(renewing.wait(3))
                grant = pool.submit(self.request)
                self.assertTrue(feed_waiting.wait(3))
                self.assertEqual(self.calls, [])
            finally:
                allow_reply.set()
            self.assertTrue(permit.result(3)['allowed'])
            with self.assertRaises(AppError):
                grant.result(3)
        self.transport.failure = OSError('offline')
        with self.assertRaises(AppError):
            self.peer.require()
