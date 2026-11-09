import http.client
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fagao.overseas import OVERSEAS_PLATFORMS
from fagao.server import WorkbenchServer, collection_scope
from fagao.store import AppError, Store


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

    def post(self, path, data, marker=True):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        headers = {'Content-Type': 'application/json'}
        if marker:
            headers['X-Fagao-Request'] = '1'
        connection.request('POST', path, json.dumps(data), headers)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

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


if __name__ == '__main__':
    unittest.main()
