# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Exercise first-time board downloads over isolated urllib transports."""
from email.message import Message
from io import BytesIO
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request
import urllib.response

from fagao import daily_feeds as feeds
from fagao.store import AppError, Store
from test_daily_feeds import TEST_KEY, grant


# A synthetic one-row Hongguo snapshot encrypted independently with Fernet.
TOKEN = (
    b'gAAAAABlU_EAJSIp_HktbiYolDOoJdPXTtDit1jR08Y5nW8BXqxS4Vxpm2267hEqFM6g2mXuoFQCz0r8PoSfdX6gX4F'
    b'lddbZdj-7QQPO4QcLiH3mMlwpj08cmnW1CUY3CmBxN-FhyX6hh6kXil7pECbvi6d9fFHYTV88cfMp1oHgwNriYjG_pdj'
    b'5_BPhYKGmrsmD3u29ZWzQHBYvdPVgIR3CgedTJjLV8u_dBc5EpbiFSi0FY3TmWVaOl3SvpaUH5jeeESto46lscg9vxSM4'
    b'MkvlI8sE8enI2CkoSII06-ij4yzgq17qjvbkhoGMO2A5QkCVDhwiWjxFjOjSmCGY2w7Te2sKJabjYQ=='
)


class OfflineHTTPS(urllib.request.HTTPSHandler):
    def __init__(self, respond):
        super().__init__()
        self.respond = respond

    def https_open(self, request):
        status, headers, data = self.respond(request)
        fields = Message()
        fields['Content-Length'] = str(len(data))
        for key, value in headers.items():
            fields[key] = value
        response = urllib.response.addinfourl(BytesIO(data), fields, request.full_url, status)
        response.msg = 'fixture'
        return response


class DailyFeedMirrorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(Path(temp.name))
        self.ctx = SimpleNamespace(store=self.store, account_id='test-member',
                                   generation='test-session', operation='use', require=Mock())
        self.membership = Mock()
        self.permission = grant(board='hongguo', token=TOKEN)
        self.membership.feed_grant.return_value = self.permission
        manifest = self.permission['manifest']
        path = '/gh/ShiQi-Han03/fagao-ranking-feed@' + '1' * 40 + '/objects/hongguo/' + manifest['sha256'] + '.bin'
        self.urls = [manifest['artifact_url'], 'https://cdn.jsdelivr.net' + path,
                     'https://gcore.jsdelivr.net' + path]
        self.visited = []

    def serve(self, responses):
        def respond(request):
            url = request.full_url
            self.assertIn(url, self.urls)
            self.assertEqual(request.get_method(), 'GET')
            self.assertIsNone(request.data)
            headers = {key.lower(): value for key, value in request.header_items()}
            self.assertTrue({'authorization', 'cookie', 'proxy-authorization'}.isdisjoint(headers))
            self.assertNotIn(TEST_KEY, str(headers))
            self.visited.append(url)
            result = responses[url]
            if isinstance(result, Exception):
                raise result
            if callable(result):
                result = result()
            return (200, {}, result) if isinstance(result, bytes) else result

        def make_opener(*handlers):
            return urllib.request.build_opener(urllib.request.ProxyHandler({}), OfflineHTTPS(respond), *handlers)

        transport = patch.object(feeds.network, 'build_opener', side_effect=make_opener)
        transport.start()
        self.addCleanup(transport.stop)

    def sync(self):
        return feeds.sync_board(self.ctx, self.membership, 'hongguo')

    def test_cold_cache_gets_verified_hongguo_when_raw_is_unreachable(self):
        self.serve({self.urls[0]: TimeoutError(), self.urls[1]: TOKEN})
        result = self.sync()
        self.assertEqual(result['status'], 'updated')
        self.assertEqual(result['snapshot']['rows'], [['1', '合成红果短剧']])
        self.assertEqual(self.sync()['status'], 'unchanged')
        self.assertEqual(self.visited, self.urls[:2])

    def test_valid_last_mirror_recovers_after_same_length_corrupt_copy(self):
        self.serve({self.urls[0]: TimeoutError(), self.urls[1]: b'!' * len(TOKEN), self.urls[2]: TOKEN})
        self.assertEqual(self.sync()['status'], 'updated')
        self.assertEqual(self.visited, self.urls)

    def test_corrupt_copies_are_never_saved(self):
        self.serve(dict.fromkeys(self.urls, b'!' * len(TOKEN)))
        with self.assertRaises(AppError) as err:
            self.sync()
        self.assertEqual(err.exception.code, 'daily_feed_invalid')
        self.assertEqual(self.visited, self.urls)
        self.assertEqual(feeds.read_cache(self.store, 'hongguo')['history'], [])

    def test_all_paths_unreachable_preserves_empty_cache(self):
        self.serve(dict.fromkeys(self.urls, urllib.error.URLError('fixture connection failure')))
        with self.assertRaises(AppError) as err:
            self.sync()
        self.assertEqual(err.exception.code, 'daily_feed_download')
        self.assertEqual(self.visited, self.urls)
        self.assertIsNone(feeds.read_cache(self.store, 'hongguo')['snapshot'])

    def test_changed_account_stops_before_the_next_mirror(self):
        def lose_access():
            self.ctx.require.side_effect = AppError('membership_account_changed', '账号已切换', 403)
            raise TimeoutError()

        self.serve({self.urls[0]: lose_access})
        with self.assertRaises(AppError) as err:
            self.sync()
        self.assertEqual(err.exception.code, 'membership_account_changed')
        self.assertEqual(self.visited, self.urls[:1])
        self.assertEqual(feeds.read_cache(self.store, 'hongguo')['history'], [])

    def test_redirect_to_untrusted_host_is_not_followed(self):
        self.serve({self.urls[0]: (302, {'Location': 'https://untrusted.invalid/fixture'}, b''),
                    self.urls[1]: TOKEN})
        self.assertEqual(self.sync()['status'], 'updated')
        self.assertEqual(self.visited, self.urls[:2])

    def test_only_valid_immutable_grants_may_start_object_requests(self):
        self.serve({})
        original = self.urls[0]
        for bad in (original.replace('1' * 40, 'main'), self.urls[1], original + '?token=unexpected'):
            with self.subTest(bad=bad), self.assertRaises(AppError):
                self.permission['manifest']['artifact_url'] = bad
                self.sync()
            with self.subTest(download=bad), self.assertRaises(AppError):
                feeds._download(bad, len(TOKEN))
        self.assertEqual(self.visited, [])

    def test_elapsed_download_budget_stops_later_requests(self):
        clock = Mock(return_value=100.0)
        def timeout():
            clock.return_value += 120.0
            raise TimeoutError()

        self.serve({self.urls[0]: timeout})
        with patch.object(feeds.time, 'monotonic', clock), self.assertRaises(AppError) as err:
            self.sync()
        self.assertEqual(err.exception.code, 'daily_feed_download')
        self.assertEqual(self.visited, self.urls[:1])
