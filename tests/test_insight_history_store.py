# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Trend history does not load unrelated books or every incremental page preview."""
from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fagao.store import AppError, Store


class InsightHistoryStoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(Path(directory.name))

    @staticmethod
    def row(ident, day, **changes):
        stamp = day + 'T10:00:00+00:00'
        return {'id': ident, 'platform': 'qimao', 'mode': 'long', 'channel': '男频',
                'category': '全部', 'board': '大热榜', 'period': '日榜', 'source_id': 'direct',
                'data_date': day, 'collected_at': stamp, 'created_at': stamp,
                'books': [{'id': 'book-1', 'title': '真实作品'}], **changes}

    def insert(self, *records):
        with self.store.connect() as connection:
            for record in records:
                metadata = {key: value for key, value in record.items() if key != 'books'}
                connection.execute('INSERT INTO snapshots VALUES(?,?,?)',
                                   (record['id'], record['created_at'], json.dumps(record)))
                connection.execute('INSERT INTO snapshot_metadata VALUES(?,?,?)',
                                   (record['id'], record['created_at'], json.dumps(metadata)))

    def test_other_platform_and_other_account_bodies_are_never_decoded(self):
        target = self.row('current', '2026-11-16')
        old = self.row('old', '2026-11-15')
        self.insert(target, old)
        # A malformed body makes accidental decoding of an unrelated platform observable.
        with self.store.connect() as connection:
            foreign = self.row('foreign', '2026-11-15', platform='qidian')
            foreign.pop('books')
            connection.execute('INSERT INTO snapshots VALUES(?,?,?)', ('foreign', foreign['created_at'], 'bad body'))
            connection.execute('INSERT INTO snapshot_metadata VALUES(?,?,?)',
                               ('foreign', foreign['created_at'], json.dumps(foreign)))
        with patch.object(self.store, '_snapshot', wraps=self.store._snapshot) as load:
            result = self.store.insight_history(target)
        self.assertEqual({row['id'] for row in result}, {'current', 'old'})
        self.assertNotIn('foreign', [call.args[1] for call in load.call_args_list])
        other_store = Store(self.store.directory / 'other-account')
        self.assertEqual(other_store.insight_history(target), [])

    def test_many_same_day_previews_do_not_hide_earlier_daily_observations(self):
        target = self.row('current', '2026-11-16', collected_at='2026-11-16T23:00:00Z')
        records = []
        for days_ago in range(1, 12):
            day = (date(2026, 11, 16) - timedelta(days=days_ago)).isoformat()
            for preview in range(25):
                stamp = f'{day}T10:{preview:02}:00Z'
                records.append(self.row(f'{day}-{preview}', day, created_at=stamp, collected_at=stamp))
        records.append(self.row('cross-board', '2026-11-16', board='新书榜'))
        records.append(self.row('wrong-source', '2026-11-15', source_id='third-party'))
        records.append(self.row('future', '2026-11-17'))
        self.insert(target, *records)
        with patch.object(self.store, '_snapshot', wraps=self.store._snapshot) as load:
            result = self.store.insight_history(target, limit=100)
        ids = {record['id'] for record in result}
        self.assertIn('2026-11-05-24', ids)
        self.assertIn('cross-board', ids)
        self.assertNotIn('wrong-source', ids)
        self.assertNotIn('future', ids)
        self.assertTrue(all(record['id'] in ('current', 'cross-board') or record['id'].endswith('-24')
                            for record in result))
        self.assertLessEqual(load.call_count, 100)
        self.assertLessEqual(len(self.store.insight_history(target, limit=5)), 5)
        for value in (0, True, 201):
            with self.assertRaises(AppError):
                self.store.insight_history(target, limit=value)


if __name__ == '__main__':
    unittest.main()
