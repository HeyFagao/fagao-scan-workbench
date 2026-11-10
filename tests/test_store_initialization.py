import concurrent.futures
import multiprocessing
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from fagao.store import Store


def _initialize_and_write(directory, start, queue, worker):
    queue.put({'ready': worker})
    if not start.wait(10):
        queue.put({'error': 'start timed out'})
        return
    try:
        for iteration in range(8):
            store = Store(directory)
            with store.connect() as connection:
                connection.execute('INSERT INTO kv VALUES (?, ?)',
                                   (f'worker-{worker}-{iteration}', '"preserved"'))
        queue.put({'done': worker})
    except Exception as exc:
        queue.put({'error': type(exc).__name__ + ': ' + str(exc)})


class StoreInitializationTests(unittest.TestCase):
    def test_schema_and_defaults_roll_back_if_initialization_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(Store, '_repair_download_integrity', side_effect=RuntimeError('migration interrupted')):
                with self.assertRaisesRegex(RuntimeError, 'migration interrupted'):
                    Store(directory)
            with sqlite3.connect(Path(directory) / 'workbench.sqlite3') as connection:
                self.assertEqual(connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [])
            # A subsequent normal launch can initialize the same file.
            self.assertEqual(Store(directory).state()['snapshots'], [])

    def test_wal_transition_waits_for_existing_writer_without_losing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'workbench.sqlite3'
            writer = sqlite3.connect(path)
            try:
                writer.execute('CREATE TABLE retained(value TEXT)')
                writer.execute('INSERT INTO retained VALUES (?)', ('owner original data',))
                blocked = threading.Event()
                release = threading.Event()

                def wait_for_release(_seconds):
                    blocked.set()
                    if not release.wait(5):
                        raise RuntimeError('writer release timed out')

                with patch('fagao.store.time.sleep', side_effect=wait_for_release), \
                        concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    pending = pool.submit(Store, directory)
                    try:
                        self.assertTrue(blocked.wait(5), 'expected a real WAL lock conflict')
                        self.assertFalse(pending.done())
                        writer.commit()
                    finally:
                        release.set()
                    store = pending.result(timeout=5)
                with store.connect() as connection:
                    self.assertEqual(connection.execute('SELECT value FROM retained').fetchone()[0], 'owner original data')
                    self.assertEqual(connection.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            finally:
                writer.close()

    def test_non_busy_database_errors_are_not_retried_or_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'workbench.sqlite3'
            original = b'not a sqlite database; preserve for owner recovery'
            path.write_bytes(original)
            with patch('fagao.store.time.sleep') as pause:
                with self.assertRaises(sqlite3.DatabaseError):
                    Store(directory)
                pause.assert_not_called()
            self.assertEqual(path.read_bytes(), original)

    def test_wal_busy_retries_stop_at_deadline_and_close_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = MagicMock()
            failure = sqlite3.OperationalError('database is locked')
            failure.sqlite_errorcode = 5
            connection.execute.side_effect = failure
            with patch('fagao.store.sqlite3.connect', return_value=connection), \
                    patch('fagao.store.time.monotonic', side_effect=[100, 115]), \
                    patch('fagao.store.time.sleep') as pause:
                with self.assertRaises(sqlite3.OperationalError) as caught:
                    Store(directory)
                self.assertIs(caught.exception, failure)
                self.assertEqual(connection.execute.call_count, 1)
                pause.assert_not_called()
                connection.close.assert_called_once()

    def test_concurrent_processes_initialize_new_and_existing_databases(self):
        for existing in (False, True):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as directory:
                snapshot = None
                if existing:
                    snapshot = Store(directory).save_snapshot({
                        'platform': 'fanqie', 'channel': '女频', 'category': '测试',
                        'board': '新书榜', 'books': [{'id': 'original', 'title': '保留原作品'}]}, imported=True)
                context = multiprocessing.get_context('spawn')
                start = context.Event()
                queue = context.Queue()
                workers = [context.Process(target=_initialize_and_write,
                           args=(directory, start, queue, index)) for index in range(4)]
                try:
                    for worker in workers:
                        worker.start()
                    ready = [queue.get(timeout=10) for _ in workers]
                    self.assertEqual({item.get('ready') for item in ready}, set(range(4)), ready)
                    start.set()
                    results = [queue.get(timeout=20) for _ in workers]
                    for worker in workers:
                        worker.join(timeout=5)
                        self.assertEqual(worker.exitcode, 0)
                    self.assertEqual({item.get('done') for item in results}, set(range(4)), results)
                    store = Store(directory)
                    with store.connect() as connection:
                        self.assertEqual(connection.execute("SELECT count(*) FROM kv WHERE key LIKE 'worker-%'").fetchone()[0], 32)
                        self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                    if snapshot:
                        self.assertEqual(store.snapshot(snapshot['id']), snapshot)
                finally:
                    start.set()
                    for worker in workers:
                        if worker.is_alive():
                            worker.terminate()
                        worker.join(timeout=3)
                    queue.close()
                    queue.join_thread()


if __name__ == '__main__':
    unittest.main()
