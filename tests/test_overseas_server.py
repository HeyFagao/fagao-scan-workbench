import http.client
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fagao.collectors import CollectorError
from fagao.overseas import OVERSEAS_PLATFORMS
from fagao.server import JobManager, WorkbenchServer, collection_scope
from fagao.store import AppError, Store
from tests.test_scribble_import import saved_page


class OverseasServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.server = WorkbenchServer(('127.0.0.1', 0), self.store)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        for job in self.store.state()['jobs']:
            self.server.jobs.cancel(job['id'])
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def post(self, path, data, marker=True, origin=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        headers = {'Content-Type': 'application/json'}
        if marker:
            headers['X-Fagao-Request'] = '1'
        if origin:
            headers['Origin'] = origin
        connection.request('POST', path, json.dumps(data), headers)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def wait_job(self, jid):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            job = self.store.job(jid)
            if job['status'] in ('completed', 'failed', 'cancelled'):
                # The worker records its outcome before releasing the scope.
                with self.server.jobs.lock:
                    if self.server.jobs.active.get(job.get('scope_key')) != jid:
                        return self.store.job(jid)
            time.sleep(.01)
        self.fail(f'Collection did not finish: {job}')

    def saved_scribble(self):
        return self.store.save_snapshot({
            'platform': 'scribblehub', 'channel': '全部', 'category': '全部',
            'board': '周收藏榜', 'period': '周榜',
            'books': [{'id': 'saved-1', 'title': 'The Cached Book',
                       'summary': 'The saved English synopsis.', 'rank': 1}],
        })

    def test_defaults_and_explicit_options_share_scope(self):
        for platform in OVERSEAS_PLATFORMS:
            with self.subTest(platform=platform['id']):
                implicit, effective = collection_scope(platform['id'], {})
                explicit, same = collection_scope(platform['id'], platform['default_options'])
                self.assertEqual(implicit, explicit)
                self.assertEqual(effective, same)
                self.assertEqual(effective['mode'], 'overseas')

    def test_wrong_mode_and_unsupported_categories_are_rejected(self):
        for options in ({'mode': 'long'}, {'category': 'unknown'}, {'channel': 'unknown'}, {'board': 'unknown'}):
            with self.subTest(options=options), self.assertRaises(AppError):
                collection_scope('goodnovel', options)

    def test_translation_endpoint_pins_ids_and_keeps_english(self):
        snapshot = self.store.save_snapshot({'platform': 'goodnovel', 'books': [
            {'id': 'original-1', 'title': 'The Original Title', 'summary': 'An English synopsis.', 'rank': None}
        ]})
        self.store.set_view({'mode': 'overseas', 'platform': 'goodnovel', 'snapshot_id': snapshot['id'], 'selected_id': 'original-1'})
        status, result = self.post('/api/translation-context', {'snapshot_id': snapshot['id'], 'book_id': 'original-1'})
        self.assertEqual(status, 200, result)
        self.assertEqual(result['original']['summary'], 'An English synopsis.')
        self.assertEqual(result['book_id'], 'original-1')
        self.assertIn(snapshot['id'], result['prompt'])
        self.assertEqual(self.store.snapshot(snapshot['id'])['books'][0]['title'], 'The Original Title')
        self.assertEqual(self.post('/api/translation-context', {}, marker=False)[0], 403)

    def test_first_page_visible_while_next_page_waits_for_scroll(self):
        def collect(platform, options, directory, progress):
            def snapshot(size):
                return {'platform': platform, 'mode': 'overseas', 'channel': options['channel'],
                        'category': options['category'], 'board': options['board'],
                        'books': [{'id': str(n), 'title': f'Book {n}', 'rank': n} for n in range(1, size+1)]}
            progress('首批已到达', snapshot=snapshot(10), has_more=True)
            return snapshot(20)
        with patch('fagao.collectors.collect', side_effect=collect):
            status, response = self.post('/api/collect', {'platform': 'dreame', 'interactive': True})
            self.assertEqual(status, 200)
            jid = response['job_id']
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                job = self.store.job(jid)
                if job.get('waiting_for_more'):
                    break
                time.sleep(.01)
            self.assertTrue(job.get('waiting_for_more'), job)
            self.assertEqual(self.store.snapshot(job['partial_snapshot_id'])['books_count'], 10)
            self.post('/api/jobs/more', {'job_id': jid, 'count': 20})
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                job = self.store.job(jid)
                if job['status'] == 'completed':
                    break
                time.sleep(.01)
            self.assertEqual(job['status'], 'completed', job)
            self.assertEqual(self.store.snapshot(job['snapshot_id'])['books_count'], 20)

    def test_access_denial_is_persisted_and_preserves_cached_selection(self):
        saved = self.saved_scribble()
        self.store.set_view({'mode': 'overseas', 'platform': 'scribblehub',
                             'snapshot_id': saved['id'], 'selected_id': 'saved-1'})
        with patch('fagao.collectors.collect', side_effect=CollectorError(
                'Scribble Hub 返回 HTTP 403。', 'source_access_denied')):
            status, response = self.post('/api/collect', {
                'platform': 'scribblehub', 'automatic': True})
            self.assertEqual(status, 200, response)
            job = self.wait_job(response['job_id'])
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(job['error_code'], 'source_access_denied')
        self.assertNotIn('snapshot_id', job)
        self.assertEqual(self.store.snapshot(saved['id'])['books'], saved['books'])
        state = self.store.state()
        self.assertEqual(len(state['snapshots']), 1)
        self.assertEqual(state['views']['overseas']['snapshot_id'], saved['id'])
        self.assertEqual(state['views']['overseas']['selected_id'], 'saved-1')

    def test_denied_automatic_retry_requires_action_across_restart(self):
        with patch('fagao.collectors.collect', side_effect=CollectorError(
                'Source rejected access.', 'source_access_denied')) as collect:
            _, response = self.post('/api/collect', {
                'platform': 'scribblehub', 'automatic': True})
            failed = self.wait_job(response['job_id'])
            # Access refusal remains actionable after normal transient-error
            # cooldown has expired; restarting must not trigger another request.
            failed['finished_at'] = '2000-01-01T00:00:00+00:00'
            self.store.save_job(failed)
            restarted = JobManager(Store(self.temp.name))
            for manager in (self.server.jobs, restarted):
                response = manager.start('scribblehub', {}, automatic=True)
                self.assertEqual(response['status'], 'cooldown')
                self.assertTrue(response['requires_action'])
                self.assertEqual(response['job']['id'], failed['id'])
            self.assertEqual(collect.call_count, 1)
            self.assertEqual(len(self.store.state()['jobs']), 1)

    def test_manual_retry_after_access_denial_can_recover(self):
        recovered = {
            'platform': 'scribblehub', 'channel': '全部', 'category': '全部',
            'board': '周收藏榜', 'period': '周榜',
            'books': [{'id': 'new-2', 'title': 'A Fresh English Book', 'rank': 2}],
        }
        with patch('fagao.collectors.collect', side_effect=[
                CollectorError('Source rejected access.', 'source_access_denied'),
                recovered,
        ]) as collect:
            _, first = self.post('/api/collect', {
                'platform': 'scribblehub', 'automatic': True})
            self.assertEqual(self.wait_job(first['job_id'])['status'], 'failed')
            status, retry = self.post('/api/collect', {
                'platform': 'scribblehub', 'automatic': False})
            self.assertEqual(status, 200, retry)
            self.assertEqual(retry['status'], 'started')
            success = self.wait_job(retry['job_id'])
            self.assertEqual(success['status'], 'completed', success)
            self.assertEqual(collect.call_count, 2)
            self.assertEqual(self.store.snapshot(success['snapshot_id'])['books'][0]['id'], 'new-2')
            _, fresh = self.post('/api/collect', {
                'platform': 'scribblehub', 'automatic': True})
            self.assertEqual(fresh['status'], 'fresh')
            self.assertEqual(collect.call_count, 2)

    def test_second_page_denial_preserves_first_page_and_pauses_automatic_retry(self):
        # Run the real collector and progress publication, replacing only HTTP.
        with patch('fagao.overseas_readers._fetch', side_effect=[
                saved_page(count=2), CollectorError('HTTP 403', 'forbidden'),
        ]) as fetch:
            status, started = self.post('/api/collect', {
                'platform': 'scribblehub', 'automatic': True, 'interactive': False,
                'options': {'max_pages': 2},
            })
            self.assertEqual(status, 200, started)
            failed = self.wait_job(started['job_id'])
            self.assertEqual(failed['status'], 'failed', failed)
            self.assertTrue(failed['partial'])
            self.assertEqual(failed['error_code'], 'source_access_denied')
            self.assertIn('已保存 2 本', failed['message'])
            self.assertIn('HTTP 403', failed['message'])
            first = self.store.snapshot(failed['partial_snapshot_id'])
            retained = self.store.snapshot(failed['snapshot_id'])
            self.assertEqual(first['books_count'], 2)
            self.assertEqual(retained['books'], first['books'])
            self.assertFalse(retained['complete'])
            self.assertEqual(retained['pagination']['pages_fetched'], 1)
            self.assertEqual(retained['pagination']['stop_reason'], 'page_error')
            self.assertIn('HTTP 403', ' '.join(retained['warnings']))
            _, retry = self.post('/api/collect', {
                'platform': 'scribblehub', 'automatic': True,
                'options': {'max_pages': 2},
            })
            self.assertEqual(retry['status'], 'cooldown')
            self.assertTrue(retry['requires_action'])
            self.assertEqual(fetch.call_count, 2)

    def test_saved_html_import_keeps_originals_without_scripts_or_network(self):
        html = saved_page(page=2).replace('<body>', '<body><nav>private-navigation-sentinel</nav>')
        html = html.replace('Original English synopsis.',
                            'Original English synopsis.<script>throw new Error("script-sentinel")</script>')
        with patch('fagao.overseas_readers._fetch', side_effect=AssertionError('HTML import must remain offline')) as fetch:
            status, imported = self.post('/api/import/overseas-html', {
                'platform': 'scribblehub', 'html': html,
                'source_url': 'https://www.scribblehub.com/series-ranking/?order=2&sort=2&pg=2',
            })
        self.assertEqual(status, 200, imported)
        self.assertTrue(imported['imported'])
        self.assertEqual(imported['source_id'], 'browser-import')
        self.assertEqual(imported['platform'], 'scribblehub')
        self.assertEqual(imported['mode'], 'overseas')
        self.assertEqual(imported['period'], '周榜')
        self.assertIsNone(imported['data_date'])
        self.assertFalse(imported['complete'])
        self.assertEqual([book['rank'] for book in imported['books']], [26, 27])
        self.assertEqual(imported['books'][0]['title'], 'English Book 26')
        self.assertEqual(imported['books'][0]['summary'], 'Original English synopsis.')
        self.assertEqual(imported['books'][0]['tags'], ['奇幻'])
        self.assertEqual(imported['books'][0]['source_tags'], ['Fantasy'])
        self.assertEqual(imported['provenance']['source_name'], 'Scribble Hub 官网 · 用户保存页面')
        persisted = self.store.snapshot(imported['id'])
        self.assertEqual(persisted, imported)
        serialized = json.dumps(persisted)
        for sentinel in ('private-navigation-sentinel', 'script-sentinel', '<html', '<script'):
            self.assertNotIn(sentinel, serialized)
        fetch.assert_not_called()

    def test_saved_html_import_invalid_page_keeps_existing_snapshot(self):
        saved = self.saved_scribble()
        for html in ('<html>Just a moment...</html>', '', None):
            with self.subTest(html=html):
                status, result = self.post('/api/import/overseas-html', {
                    'platform': 'scribblehub', 'html': html,
                })
                self.assertEqual(status, 400, result)
                self.assertEqual(result['error']['code'], 'invalid_ranking_html')
        self.assertEqual(len(self.store.state()['snapshots']), 1)
        self.assertEqual(self.store.snapshot(saved['id']), saved)

    def test_saved_html_import_rejects_wrong_platform_and_requires_request_marker(self):
        with patch('fagao.scribble_import.parse_saved_ranking') as parse:
            for platform in ('royalroad', 'fanqie', None):
                with self.subTest(platform=platform):
                    status, result = self.post('/api/import/overseas-html', {
                        'platform': platform, 'html': saved_page(),
                    })
                    self.assertEqual(status, 400, result)
                    self.assertEqual(result['error']['code'], 'unsupported_platform')
            status, result = self.post('/api/import/overseas-html', {
                'platform': 'scribblehub', 'html': saved_page(),
            }, marker=False)
            self.assertEqual(status, 403, result)
            status, result = self.post('/api/import/overseas-html', {
                'platform': 'scribblehub', 'html': saved_page(),
            }, origin='https://untrusted.example')
            self.assertEqual(status, 403, result)
            parse.assert_not_called()
        self.assertEqual(self.store.state()['snapshots'], [])


if __name__ == '__main__':
    unittest.main()
