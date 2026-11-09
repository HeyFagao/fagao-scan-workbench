import hashlib
import json
import multiprocessing
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fagao import legacy_claim
from fagao.legacy_claim import claim_legacy, inspect_legacy, legacy_decision, skip_legacy, require_legacy_decision
from fagao.store import AppError, Store


def _process_claim(root, queue):
    root = Path(root)
    account = 'account-a'
    target = Store(root / 'accounts' / hashlib.sha256(account.encode()).hexdigest())
    context = SimpleNamespace(account_id=account, generation='process-test', store=target,
        require=lambda: None, jobs=SimpleNamespace(lock=threading.RLock(), active={}))
    runtime = SimpleNamespace(root_dir=root, capture=lambda **kwargs: context)
    try:
        queue.put({'receipt': claim_legacy(runtime, True)})
    except Exception as exc:
        queue.put({'error': getattr(exc, 'code', type(exc).__name__)})


class ClaimTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='发糕-旧资料-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = None
        self.allowed = True
        self.account = 'account-a'
        self.generation = 'session-a'
        self.contexts = {}
        self.runtime = SimpleNamespace(root_dir=self.root, capture=self.capture,
                                       membership=SimpleNamespace(require=self.require_membership))

    def require_membership(self, account_id=None, generation=None, operation='use'):
        if not self.allowed:
            raise AppError('membership_expired', '已到期。', 403)
        if account_id not in (None, self.account) or generation not in (None, self.generation):
            raise AppError('membership_account_changed', '账号变化。', 403)
        return {'allowed': True, 'account': {'id': self.account}, 'generation': self.generation}

    def capture(self, operation='use', expected_account=None, expected_generation=None):
        self.assertEqual(operation, 'claim_legacy')
        self.require_membership(expected_account, expected_generation)
        key = self.account, self.generation
        if key not in self.contexts:
            account, generation = key
            def require():
                if not self.allowed:
                    raise AppError('membership_expired', '已到期。', 403)
                if (account, generation) != (self.account, self.generation):
                    raise AppError('membership_account_changed', '账号变化。', 403)
            path = self.root / 'accounts' / hashlib.sha256(account.encode()).hexdigest()
            self.contexts[key] = SimpleNamespace(account_id=account, generation=generation,
                store=Store(path), require=require,
                jobs=SimpleNamespace(lock=threading.RLock(), active={}))
        return self.contexts[key]

    def source_data(self):
        self.source = Store(self.root)
        snapshot = self.source.save_snapshot({'platform': 'fanqie', 'channel': '女频',
            'category': '古言脑洞', 'board': '新书榜',
            'books': [{'id': 'old-book', 'title': '旧资料里的作品', 'summary': '只用于测试'}]}, imported=True)
        self.source.import_text(snapshot['id'], 'old-book', '保留原文。' * 20, '第一章')
        task = self.source.prepare_task({'snapshot_id': snapshot['id'], 'book_id': 'old-book'}, '分析旧作品', 'old-chat')
        self.source.save_report(task['id'], '原报告内容。' * 20)
        self.source.auth_state('heiyan', {'state': 'logged_in', 'sensitive_fixture': 'DO-NOT-COPY'})
        self.source.save_job({'id': 'old-running-job', 'kind': 'collect', 'status': 'running',
                             'created_at': '2026-01-01T00:00:00Z', 'platform': 'fanqie'})
        return snapshot

    def test_inspect_missing_does_not_create_database_or_directory(self):
        missing = self.root / 'not-created'
        value = inspect_legacy(missing)
        self.assertFalse(value['available'])
        self.assertFalse(missing.exists())

    def test_inspect_only_counts_and_preserves_original(self):
        self.source_data()
        with self.source.connect() as connection:
            before = connection.execute('SELECT content FROM texts').fetchall()[0][0]
        result = inspect_legacy(self.root)
        self.assertTrue(result['available'])
        self.assertEqual(result['counts']['texts'], 1)
        self.assertNotIn('保留原文', json.dumps(result, ensure_ascii=False))
        self.assertNotIn('DO-NOT-COPY', json.dumps(result))
        with self.source.connect() as connection:
            self.assertEqual(connection.execute('SELECT content FROM texts').fetchone()[0], before)

    def test_confirmation_required_before_capture_or_writes(self):
        self.source_data()
        for value in (False, None, 'true', 1):
            with self.subTest(value=value), self.assertRaises(AppError) as caught:
                claim_legacy(self.runtime, value)
            self.assertEqual(caught.exception.code, 'legacy_confirmation_required')
        self.assertFalse((self.root / 'accounts').exists())
        self.assertFalse((self.root / legacy_claim.MANIFEST).exists())

    def test_expired_member_cannot_claim(self):
        self.source_data(); self.allowed = False
        with self.assertRaises(AppError) as caught:
            claim_legacy(self.runtime, True)
        self.assertEqual(caught.exception.code, 'membership_expired')
        self.assertFalse((self.root / 'accounts').exists())

    def test_claim_copies_content_preserves_original_and_excludes_auth(self):
        snapshot = self.source_data()
        receipt = claim_legacy(self.runtime, True)
        target = self.capture('claim_legacy').store
        self.assertEqual(target.snapshot(snapshot['id'])['books'][0]['title'], '旧资料里的作品')
        self.assertEqual(target.context('old-chat')['analysis_target']['book_id'], 'old-book')
        self.assertEqual(target.state()['auth'], {})
        self.assertEqual(target.job('old-running-job')['status'], 'interrupted')
        self.assertEqual(self.source.job('old-running-job')['status'], 'running')
        self.assertTrue(receipt['original_preserved'])
        self.assertEqual(receipt['counts']['reports'], 1)
        self.assertEqual(receipt['status'], 'completed')
        for path_key, hash_key in [('source_backup', 'source_sha256'), ('target_backup', 'target_sha256')]:
            backup = self.root / receipt[path_key]
            self.assertEqual(hashlib.sha256(backup.read_bytes()).hexdigest(), receipt[hash_key])
            with sqlite3.connect(backup) as connection:
                self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertFalse(receipt['later_legacy_writes_included'])

    def test_repeat_is_idempotent_and_other_account_cannot_claim(self):
        self.source_data()
        first = claim_legacy(self.runtime, True)
        repeated = claim_legacy(self.runtime, True)
        self.assertEqual(first['claim_id'], repeated['claim_id'])
        self.assertTrue(repeated['repeated'])
        self.account = 'account-b'; self.generation = 'session-b'
        with self.assertRaises(AppError) as caught:
            claim_legacy(self.runtime, True)
        self.assertEqual(caught.exception.code, 'legacy_already_claimed')
        self.assertEqual(self.capture('claim_legacy').store.state()['snapshots'], [])

    def test_existing_destination_is_never_overwritten(self):
        self.source_data()
        destination = self.capture('claim_legacy').store
        destination.save_snapshot({'platform': 'fanqie', 'books': [{'id': 'new', 'title': '新账号数据'}]}, imported=True)
        with self.assertRaises(AppError) as caught:
            claim_legacy(self.runtime, True)
        self.assertEqual(caught.exception.code, 'legacy_target_not_empty')
        self.assertFalse((self.root / legacy_claim.MANIFEST).exists())
        self.assertEqual(len(destination.state()['snapshots']), 1)

    def test_failure_mid_import_rolls_back_then_reuses_frozen_backup(self):
        old = self.source_data()
        original_copy = legacy_claim._copy_data
        def interrupted(*args):
            original_copy(*args)
            raise RuntimeError('simulated interruption')
        with patch.object(legacy_claim, '_copy_data', side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, 'simulated'):
                claim_legacy(self.runtime, True)
        target = self.capture('claim_legacy').store
        self.assertEqual(target.state()['snapshots'], [])
        self.assertEqual(json.loads((self.root / legacy_claim.MANIFEST).read_text())['status'], 'prepared')
        self.source.save_snapshot({'platform': 'fanqie', 'books': [{'id': 'later', 'title': '备份后新增资料'}]}, imported=True)
        receipt = claim_legacy(self.runtime, True)
        self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(len(target.state()['snapshots']), 1)
        self.assertEqual(target.state()['snapshots'][0]['id'], old['id'])

    def test_crash_after_commit_finishes_receipt_without_duplicate_import(self):
        self.source_data()
        original = legacy_claim._write_manifest
        def fail_final(root, manifest):
            if manifest['status'] == 'completed':
                raise OSError('simulated receipt write failure')
            return original(root, manifest)
        with patch.object(legacy_claim, '_write_manifest', side_effect=fail_final):
            with self.assertRaises(AppError):
                claim_legacy(self.runtime, True)
        receipt = claim_legacy(self.runtime, True)
        self.assertTrue(receipt['repeated'])
        self.assertEqual(len(self.capture('claim_legacy').store.state()['snapshots']), 1)
        self.assertEqual(json.loads((self.root / legacy_claim.MANIFEST).read_text())['status'], 'completed')

    def test_account_change_during_copy_rolls_back(self):
        self.source_data()
        context = self.capture('claim_legacy')
        original_copy = legacy_claim._copy_data
        def changed(*args):
            original_copy(*args)
            self.account = 'account-b'; self.generation = 'session-b'
        with patch.object(legacy_claim, '_copy_data', side_effect=changed):
            with self.assertRaises(AppError) as caught:
                claim_legacy(self.runtime, True)
        self.assertEqual(caught.exception.code, 'membership_account_changed')
        self.assertEqual(context.store.state()['snapshots'], [])

    def test_symlink_source_is_rejected(self):
        outside = self.root / 'outside.sqlite3'; outside.touch()
        (self.root / 'workbench.sqlite3').symlink_to(outside)
        with self.assertRaises(AppError) as caught:
            inspect_legacy(self.root)
        self.assertEqual(caught.exception.code, 'legacy_unsafe_path')

    def test_target_database_symlink_cannot_cross_accounts(self):
        self.source_data()
        context = self.capture('claim_legacy')
        self.account = 'account-b'; self.generation = 'session-b'
        other = self.capture('claim_legacy')
        self.account = 'account-a'; self.generation = 'session-a'
        context.store.db.unlink()
        context.store.db.symlink_to(other.store.db)
        with self.assertRaises(AppError) as caught:
            claim_legacy(self.runtime, True)
        self.assertEqual(caught.exception.code, 'legacy_unsafe_path')
        self.assertEqual(other.store.state()['snapshots'], [])

    def test_changed_frozen_backup_is_rejected_without_import(self):
        self.source_data()
        with patch.object(legacy_claim, '_copy_data', side_effect=RuntimeError('pause')):
            with self.assertRaises(RuntimeError):
                claim_legacy(self.runtime, True)
        manifest = json.loads((self.root / legacy_claim.MANIFEST).read_text())
        backup = self.root / manifest['source_backup']
        with backup.open('ab') as handle:
            handle.write(b'changed')
        with self.assertRaises(AppError) as caught:
            claim_legacy(self.runtime, True)
        self.assertEqual(caught.exception.code, 'legacy_backup_changed')
        self.assertEqual(self.capture('claim_legacy').store.state()['snapshots'], [])

    def test_legacy_wal_data_is_included_in_snapshot(self):
        self.source_data()
        writer = sqlite3.connect(self.source.db)
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute('INSERT INTO kv(key,body) VALUES (?,?)', ('analysis_target:wal', json.dumps({'book_id': 'wal-visible'})))
        writer.commit()
        try:
            self.assertTrue(Path(str(self.source.db) + '-wal').exists())
            claim_legacy(self.runtime, True)
        finally:
            writer.close()
        self.assertEqual(self.capture('claim_legacy').store.context('wal')['analysis_target']['book_id'], 'wal-visible')

    def test_unhashed_wal_overlay_on_frozen_backup_is_rejected(self):
        self.source_data()
        with patch.object(legacy_claim, '_copy_data', side_effect=RuntimeError('pause')):
            with self.assertRaises(RuntimeError):
                claim_legacy(self.runtime, True)
        manifest = json.loads((self.root / legacy_claim.MANIFEST).read_text())
        backup = self.root / manifest['source_backup']
        writer = sqlite3.connect(backup)
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute('INSERT INTO kv(key,body) VALUES (?,?)', ('analysis_target:injected', '{}'))
        writer.commit()
        try:
            with self.assertRaises(AppError) as caught:
                claim_legacy(self.runtime, True)
            self.assertEqual(caught.exception.code, 'legacy_backup_changed')
            self.assertEqual(self.capture('claim_legacy').store.state()['snapshots'], [])
        finally:
            writer.close()

    def test_concurrent_new_destination_write_prevents_import(self):
        self.source_data()
        target = self.capture('claim_legacy').store
        original = legacy_claim._write_manifest
        def concurrent_write(root, manifest):
            original(root, manifest)
            if manifest['status'] == 'prepared':
                target.save_snapshot({'platform': 'fanqie', 'books': [{'id': 'new', 'title': '并发新增'}]}, imported=True)
        with patch.object(legacy_claim, '_write_manifest', side_effect=concurrent_write):
            with self.assertRaises(AppError) as caught:
                claim_legacy(self.runtime, True)
        self.assertEqual(caught.exception.code, 'legacy_target_not_empty')
        self.assertEqual(len(target.state()['snapshots']), 1)

    def test_backup_change_during_copy_rolls_back_before_commit(self):
        self.source_data()
        original = legacy_claim._copy_data
        def changed(*args):
            original(*args)
            manifest = json.loads((self.root / legacy_claim.MANIFEST).read_text())
            with (self.root / manifest['source_backup']).open('ab') as handle:
                handle.write(b'changed-during-copy')
        with patch.object(legacy_claim, '_copy_data', side_effect=changed):
            with self.assertRaises(AppError) as caught:
                claim_legacy(self.runtime, True)
        self.assertEqual(caught.exception.code, 'legacy_backup_changed')
        self.assertEqual(self.capture('claim_legacy').store.state()['snapshots'], [])

    def test_import_rechecks_legacy_download_integrity_in_target_only(self):
        self.source_data()
        with self.source.connect() as connection:
            row = connection.execute('SELECT id,body FROM texts').fetchone()
            metadata = json.loads(row['body'])
            metadata.update(acquisition='public_download', complete=True, coverage_complete=True,
                            scope='历史误标全文完整', chapters=[])
            connection.execute('UPDATE texts SET body=? WHERE id=?', (json.dumps(metadata), row['id']))
        claim_legacy(self.runtime, True)
        target = self.capture('claim_legacy').store
        copied = target.text_record(row['id'])
        self.assertFalse(copied['complete'])
        self.assertFalse(copied['coverage_complete'])
        self.assertEqual(copied['integrity']['status'], 'unverified')
        self.assertTrue(self.source.text_record(row['id'])['complete'])

    def test_two_processes_claim_once_with_one_receipt(self):
        self.source_data()
        processes = multiprocessing.get_context('spawn')
        queue = processes.Queue()
        workers = [processes.Process(target=_process_claim, args=(str(self.root), queue)) for _ in range(2)]
        try:
            for worker in workers:
                worker.start()
            results = [queue.get(timeout=15) for _ in workers]
            for worker in workers:
                worker.join(timeout=15)
                self.assertEqual(worker.exitcode, 0)
            self.assertTrue(all('receipt' in result for result in results), results)
            receipts = [result['receipt'] for result in results]
            self.assertEqual(receipts[0]['claim_id'], receipts[1]['claim_id'])
            self.assertEqual(sorted(result['repeated'] for result in receipts), [False, True])
        finally:
            for worker in workers:
                if worker.is_alive():
                    worker.terminate(); worker.join(timeout=3)
            queue.close(); queue.join_thread()

    def test_authenticated_decision_does_not_initialize_account_database(self):
        self.source_data()
        status = legacy_decision(self.runtime)
        self.assertTrue(status['decision_required'])
        self.assertTrue(status['can_skip'])
        self.assertEqual(status['counts']['texts'], 1)
        self.assertFalse((self.root / 'accounts').exists())
        self.assertNotIn('旧资料里的作品', json.dumps(status, ensure_ascii=False))
        with self.assertRaises(AppError) as caught:
            require_legacy_decision(self.runtime, self.account, self.generation)
        self.assertEqual(caught.exception.code, 'legacy_decision_required')

    def test_expired_identity_cannot_inspect_skip_or_claim(self):
        self.source_data(); self.allowed = False
        for action in (lambda: legacy_decision(self.runtime),
                       lambda: skip_legacy(self.runtime, True),
                       lambda: claim_legacy(self.runtime, True)):
            with self.subTest(action=action), self.assertRaises(AppError) as caught:
                action()
            self.assertEqual(caught.exception.code, 'membership_expired')
        self.assertFalse((self.root / 'accounts').exists())
        self.assertFalse((self.root / legacy_claim.DECISIONS).exists())

    def test_skip_is_explicit_persistent_and_scoped_to_account(self):
        self.source_data()
        with self.assertRaises(AppError) as caught:
            skip_legacy(self.runtime)
        self.assertEqual(caught.exception.code, 'legacy_confirmation_required')
        first = skip_legacy(self.runtime, True)
        self.assertEqual(first['decision'], 'skipped')
        self.assertFalse(first['decision_required'])
        self.assertFalse((self.root / 'accounts').exists())
        saved = (self.root / legacy_claim.DECISIONS).read_bytes()
        repeated = skip_legacy(self.runtime, True)
        self.assertEqual(repeated, first)
        self.assertEqual((self.root / legacy_claim.DECISIONS).read_bytes(), saved)
        require_legacy_decision(self.runtime, self.account, self.generation)
        self.account = 'account-b'; self.generation = 'session-b'
        self.assertTrue(legacy_decision(self.runtime)['decision_required'])

    def test_skip_does_not_prevent_later_explicit_claim_into_empty_account(self):
        self.source_data()
        skip_legacy(self.runtime, True)
        receipt = claim_legacy(self.runtime, True)
        self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(legacy_decision(self.runtime)['decision'], 'claimed')

    def test_skipped_source_is_not_reopened_on_every_new_request(self):
        self.source_data()
        skip_legacy(self.runtime, True)
        with patch.object(legacy_claim, 'inspect_legacy', side_effect=AssertionError('old DB must not be reopened')):
            require_legacy_decision(self.runtime, self.account, self.generation)
            self.assertEqual(legacy_decision(self.runtime)['decision'], 'skipped')

    def test_corrupt_legacy_data_can_be_explicitly_skipped_without_modification(self):
        source = self.root / 'workbench.sqlite3'
        original = b'not a valid sqlite database'
        source.write_bytes(original)
        status = legacy_decision(self.runtime)
        self.assertEqual(status['decision'], 'inspection_unavailable')
        self.assertTrue(status['can_skip'])
        self.assertFalse(status['can_claim'])
        self.assertTrue(status['decision_required'])
        skipped = skip_legacy(self.runtime, True)
        self.assertEqual(skipped['decision'], 'skipped')
        self.assertFalse(skipped['decision_required'])
        require_legacy_decision(self.runtime, self.account, self.generation)
        self.assertEqual(source.read_bytes(), original)
        self.assertFalse((self.root / 'accounts').exists())

    def test_skipped_legacy_corruption_does_not_lock_browser_startup(self):
        self.source_data()
        skip_legacy(self.runtime, True)
        self.source.db.write_bytes(b'corrupted after skip')
        status = legacy_decision(self.runtime)
        self.assertEqual(status['decision'], 'skipped')
        self.assertFalse(status['decision_required'])
        self.assertEqual(status['counts'], {})

    def test_prepared_claim_requires_resume_and_cannot_be_skipped(self):
        self.source_data()
        with patch.object(legacy_claim, '_copy_data', side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError):
                claim_legacy(self.runtime, True)
        status = legacy_decision(self.runtime)
        self.assertEqual(status['decision'], 'resume_required')
        self.assertTrue(status['decision_required'])
        self.assertFalse(status['can_skip'])
        with self.assertRaises(AppError) as caught:
            skip_legacy(self.runtime, True)
        self.assertEqual(caught.exception.code, 'legacy_claim_in_progress')

    def test_claimed_other_account_does_not_expose_counts_or_block_new_work(self):
        self.source_data()
        claim_legacy(self.runtime, True)
        self.account = 'account-b'; self.generation = 'session-b'
        status = legacy_decision(self.runtime)
        self.assertFalse(status['available'])
        self.assertEqual(status['counts'], {})
        self.assertFalse(status['decision_required'])
        require_legacy_decision(self.runtime, self.account, self.generation)

    def test_stale_http_identity_cannot_skip_or_claim_new_account(self):
        self.source_data()
        for action in (lambda: skip_legacy(self.runtime, True, expected_account='old-account', expected_generation='old-session'),
                       lambda: claim_legacy(self.runtime, True, expected_account='old-account', expected_generation='old-session')):
            with self.subTest(action=action), self.assertRaises(AppError) as caught:
                action()
            self.assertEqual(caught.exception.code, 'membership_account_changed')
        self.assertFalse((self.root / legacy_claim.MANIFEST).exists())
        self.assertFalse((self.root / legacy_claim.DECISIONS).exists())


if __name__ == '__main__':
    unittest.main()
