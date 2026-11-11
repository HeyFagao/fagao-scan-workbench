# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Exercise the publisher/client format with synthetic, independently encrypted rows."""
import base64
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from fagao import daily_feeds as feeds
from fagao.store import AppError, Store


# Public test vectors generated with cryptography.fernet.Fernet.encrypt_at_time.
# The old token timestamp is deliberate: access is evaluated by the grant service.
TEST_KEY = 'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8='
TOKENS = {
    10: (b'gAAAAABlU_EAz63RgpUHv5Pf57KZMedHwQhrlXIY5D8raAJNu03qFXN9Mww_aWwKb7Ncy6dHdIBd1Jrqm4chj3erVttVZdIzT06AC-OOLaeNE4hNJu6bqG_0QW4rHYKU_zUIlyKFBMq4XgK_4VKluzUj0AzWai0w4yTZK-6XkDj96qBW-_U_CK7UPwivm50PGpXgUXIPHbnjxVvmpkFJzGJQ2zIxXQFu4WmiudKFiD1lu7l0gnJ64QJQaaKkw9KBZS9NKHdKVfE_JDl2sDe0i6avsjSNiTz2rv7L-33GkHP9PWaduTyFQH3BFSVhVYKI-f0L2YlxuBhXM0VLIM0gk3abhFmDU4R5EA=='),
    11: (b'gAAAAABlU_EAYsswIsGrN_2yAHHOq583BJR6gW9ay4aNFzw4w8qpx-cwuN7-_HZ-Xk_dH1KTzncDI-sjeM7bnzqNkPaVGH1F-5JA29AfoXojJESR6xp1TcZyFqX1eZ5lX9O03-GtgA6qbYUfNRcLAx2X1ttcODdOmBHKb3ebBAnF_Bt91IlAvlQkoOHTO3tonRXKHnjGaGqYERnJ5sPqP4NZm4bbEwLkEF7tLUbHMZP_1w1YCS4e1IGFp-w1I2KtLsoT3RV-eBjf58dmMrfxJ7scYh5CaXG5tveIW_ar9bCJlEUPqLpm3CT_FD285R7SOYrLnjSSpY8uVuexdol_cydu8ukATNZk1w=='),
}
BAD_GZIP = (b'gAAAAABlU_EAsFAJOa3Y3SaEBF_2FQxmRwCZclmBxw17b6t4mhjC_ycW7azc7i_jgeDPA6N8I-PgvRjYmgxyFnLyskLLWk1F7eYY8vIfbEweTO9pr45pkJo=')


def grant(day=10, *, board='douyin', token=None):
    token = TOKENS[day] if token is None else token
    digest = hashlib.sha256(token).hexdigest()
    repository = 'https://raw.githubusercontent.com/ShiQi-Han03/fagao-ranking-feed'
    url = f'{repository}/{"1" * 40}/objects/{board}/{digest}.bin'
    return {'ok': True, 'key_b64': TEST_KEY, 'manifest': {
        'version': 1, 'board': board, 'format': 'json+gzip+fernet-v1',
        'artifact_url': url, 'byte_length': len(token), 'sha256': digest,
        'source_date': f'2026-11-{day}', 'observed_at': f'2026-11-{day}T02:00:00Z',
        'row_count': 1,
    }}


class DailyFeedCodecTests(unittest.TestCase):
    def test_standard_fernet_vector_decrypts_to_known_bytes(self):
        data = feeds.decrypt_bundle(TOKENS[10], TEST_KEY)
        self.assertEqual(hashlib.sha256(data).hexdigest(),
                         'f32c2c50edc7e5d7144c9d64b8e78c0d1c75692ab98ae07ca51642154c4483eb')

    def test_snapshot_retains_original_rank_and_source_date(self):
        snapshot = feeds.decode_snapshot(grant(), 'douyin', TOKENS[10])
        self.assertEqual(snapshot['board'], 'douyin')
        self.assertEqual(snapshot['source_date'], '2026-11-10')
        self.assertEqual(snapshot['columns'], ['排名', '剧名'])
        self.assertEqual(snapshot['rows'], [['11', '合成作品']])

    def test_authenticated_ciphertext_and_mac_mutations_fail_closed(self):
        for index in (0, 9, 25, -1):
            with self.subTest(index=index):
                damaged = bytearray(base64.urlsafe_b64decode(TOKENS[10]))
                damaged[index] ^= 1
                token = base64.urlsafe_b64encode(damaged)
                # This grant contains the *new* digest, so the file checksum alone passes.
                with self.assertRaises(AppError) as exc:
                    feeds.decode_snapshot(grant(token=token), 'douyin', token)
                self.assertNotIn(TEST_KEY, str(exc.exception))
                self.assertNotIn(token.decode(), str(exc.exception))

    def test_rejects_invalid_gzip_even_with_correct_signature_and_hash(self):
        with self.assertRaises(AppError):
            feeds.decode_snapshot(grant(token=BAD_GZIP), 'douyin', BAD_GZIP)

    def test_encrypted_payload_must_match_grant_identity(self):
        wrong_date = grant()
        wrong_date['manifest']['source_date'] = '2026-11-01'
        wrong_count = grant()
        wrong_count['manifest']['row_count'] = 2
        wrong_board = grant(board='hongguo')
        for label, permission, board in [('date', wrong_date, 'douyin'),
                                         ('count', wrong_count, 'douyin'),
                                         ('board', wrong_board, 'hongguo')]:
            with self.subTest(label=label), self.assertRaises(AppError):
                feeds.decode_snapshot(permission, board, TOKENS[10])

    def test_manifest_requires_pinned_official_path_board_and_digest(self):
        manifest = grant()['manifest']
        original = manifest['artifact_url']
        invalid_urls = [original.replace('raw.githubusercontent.com', 'localhost'),
                        original.replace('fagao-ranking-feed', 'an-unrelated-repository'),
                        original.replace('1' * 40, 'main'), original.replace('/douyin/', '/hongguo/'),
                        original.replace(manifest['sha256'], 'f' * 64), original + '?extra=yes']
        for url in invalid_urls:
            with self.subTest(url=url), self.assertRaises(AppError):
                feeds.validate_manifest(dict(manifest, artifact_url=url), 'douyin')

    def test_blob_length_and_receipt_limits(self):
        manifest = grant()['manifest']
        for value in (0, 16 * 1024 * 1024 + 1):
            with self.subTest(value=value), self.assertRaises(AppError):
                feeds.validate_manifest(dict(manifest, byte_length=value), 'douyin')
        with self.assertRaises(AppError):
            feeds.decode_snapshot(grant(), 'douyin', TOKENS[10] + b'=')


class DailyFeedCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'account-a')
        self.ctx = SimpleNamespace(store=self.store, account_id='synthetic-a',
                                   generation='session-a', operation='use', require=Mock())
        self.membership = Mock()
        self.membership.feed_grant.return_value = grant()
        self.download = Mock(return_value=TOKENS[10])

    def sync(self):
        return feeds.sync_board(self.ctx, self.membership, 'douyin', download=self.download)

    def test_first_sync_then_duplicate_reuses_verified_cache(self):
        self.assertIsNone(feeds.read_cache(self.store, 'douyin')['snapshot'])
        self.assertEqual(self.sync()['status'], 'updated')
        self.assertEqual(self.sync()['status'], 'unchanged')
        self.download.assert_called_once()
        cached = feeds.read_cache(self.store, 'douyin')
        self.assertEqual(cached['snapshot']['rows'], [['11', '合成作品']])
        self.assertNotIn(TEST_KEY, repr(cached))
        self.assertIsNone(feeds.read_cache(Store(self.root / 'account-b'), 'douyin')['snapshot'])

    def test_read_only_context_cannot_fetch_keys_or_new_versions(self):
        for role in ('read', 'history'):
            with self.subTest(role=role):
                self.ctx.operation = role
                with self.assertRaises(AppError) as exc:
                    self.sync()
                self.assertEqual(exc.exception.status, 403)
        self.membership.feed_grant.assert_not_called()
        self.download.assert_not_called()

    def test_slow_older_edition_is_archived_without_replacing_newer(self):
        self.membership.feed_grant.return_value = grant(11)
        self.download.return_value = TOKENS[11]
        self.sync()
        self.membership.feed_grant.return_value = grant(10)
        self.download.return_value = TOKENS[10]
        self.sync()
        latest = feeds.read_cache(self.store, 'douyin')
        self.assertEqual(latest['snapshot']['source_date'], '2026-11-11')
        self.assertEqual([item['source_date'] for item in latest['history']],
                         ['2026-11-11', '2026-11-10'])

    def test_bad_next_copy_preserves_last_valid_snapshot(self):
        self.sync()
        self.membership.feed_grant.return_value = grant(11)
        self.download.return_value = TOKENS[10]
        with self.assertRaises(AppError):
            self.sync()
        self.assertEqual(feeds.read_cache(self.store, 'douyin')['snapshot']['source_date'], '2026-11-10')

    def test_previously_saved_revision_stays_readable(self):
        self.sync()
        self.membership.feed_grant.return_value = grant(11)
        self.download.return_value = TOKENS[11]
        self.sync()
        self.assertEqual(feeds.read_cache(self.store, 'douyin')['snapshot']['source_date'], '2026-11-11')
        older = feeds.read_cache(self.store, 'douyin', grant()['manifest']['sha256'])
        self.assertEqual(older['snapshot']['source_date'], '2026-11-10')

    def test_loss_of_access_while_downloading_prevents_cache_update(self):
        def lose_access(*args):
            self.ctx.require.side_effect = AppError('membership_account_changed', '账号已切换', 403)
            return TOKENS[10]
        self.download.side_effect = lose_access
        with self.assertRaises(AppError):
            self.sync()
        self.assertIsNone(feeds.read_cache(self.store, 'douyin')['snapshot'])
