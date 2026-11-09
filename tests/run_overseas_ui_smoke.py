"""Offline overseas navigation integration against the real local API and DOM."""
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fagao.store import Store
from fagao.server import WorkbenchServer

with tempfile.TemporaryDirectory() as directory, patch('fagao.public_rankings.sync_public_rankings', return_value={'snapshots': []}):
    store = Store(directory)
    domestic = store.save_snapshot({'platform': 'fanqie', 'channel': '女频', 'category': '古言脑洞', 'board': '新书榜', 'books': [
        {'id': 'domestic-1', 'title': '本地测试作品', 'summary': '隔离测试用书目', 'rank': 1}]}, imported=True)
    overseas = store.save_snapshot({'platform': 'goodnovel', 'channel': '全部', 'category': '狼人', 'board': '分类榜', 'pagination': {'pages_fetched': 4, 'max_pages': 100, 'stop_reason': 'source_end'}, 'books': [
        {'id': f'en-{index}', 'title': f'The English Book {index}', 'author': 'Fixture Author',
         'summary': f'Original English synopsis {index}. <script>unsafe()</script>',
         'cover_url': 'https://example.invalid/cover.jpg', 'tags': ['狼人'], 'rank': None,
         'source_position': index, 'metrics': [{'name': '阅读', 'value': '1.2M', 'unit': ''}]}
        for index in range(1, 41)]}, imported=True)
    short = store.save_snapshot({'platform': 'goodnovel_short', 'channel': '全部', 'category': '全部', 'board': '热门短篇', 'books': [
        {'id': 'short-en-1', 'title': 'A Short English Story', 'summary': 'Original short synopsis.', 'rank': None}]}, imported=True)
    royal = store.save_snapshot({'platform': 'royalroad', 'channel': '全部', 'category': '全部', 'board': '新星榜', 'period': '当前',
        'source_id': 'rst_public', 'source_url': 'https://rst.doomlabs.net/', 'data_date': '2026-11-09T09:40:00Z',
        'provenance': {'source_name': 'RST 公开榜单快照', 'rank_semantics': 'source_snapshot_rank'},
        'books': [{'id': 'rr-fixture', 'title': 'The RST English Book', 'rank': 1, 'summary': ''}]}, imported=True)
    scribble_books = [{'id': f'sh-{index}', 'title': f'Scribble Fixture {index}', 'summary': 'English fixture.', 'rank': index} for index in range(1, 31)]
    scribble_first = store.save_snapshot({'platform': 'scribblehub', 'channel': '全部', 'category': '全部', 'board': '周收藏榜', 'period': '周榜', 'books': scribble_books[:10],
        'pagination': {'stop_reason': 'imported_page', 'pages_fetched': 0, 'pages_imported': 1, 'source_page': 1, 'source_pages': 20}}, imported=True)
    scribble_next = store.save_snapshot({'platform': 'scribblehub', 'channel': '全部', 'category': '全部', 'board': '周收藏榜', 'period': '周榜', 'books': scribble_books}, imported=True)
    store.set_view({'mode': 'long', 'platform': 'fanqie', 'snapshot_id': domestic['id']})
    server = WorkbenchServer(('127.0.0.1', 0), store)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = dict(os.environ, FAGAO_TEST_URL=f'http://127.0.0.1:{server.server_port}', FAGAO_TEST_OVERSEAS_SNAPSHOT=overseas['id'], FAGAO_TEST_ROYAL_SNAPSHOT=royal['id'], FAGAO_TEST_SCRIBBLE_FIRST=scribble_first['id'], FAGAO_TEST_SCRIBBLE_NEXT=scribble_next['id'])
    try:
        result = subprocess.run(['node', 'tests/overseas-ui-smoke.cjs'], env=env, cwd=Path(__file__).resolve().parents[1], timeout=35)
        code = result.returncode
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    sys.exit(code)
