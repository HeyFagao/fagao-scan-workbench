# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fagao import qimao_rank as qimao
from fagao.collectors import CollectorError


class QimaoRankTests(unittest.TestCase):
    def fetch(self, url, **kwargs):
        if '/qimaoapi/' not in url:
            path = urlsplit(url).path
            self.assertEqual(path, '/paihang/girl/new/month/')
            cards = ''.join(f'<li class="rank-list-item"><span class="rank-number">{i}</span>'
                            f'<a class="s-book-title" href="{qimao.ORIGIN}/shuku/{i}/">作品{i}</a></li>'
                            for i in range(1, 21))
            return (f'<title>七猫小说新书榜</title><span class="item init active">女生榜</span>'
                    f'<a class="tab active" href="{path}">月榜</a><ul class="rank-list">{cards}</ul>')
        args = parse_qs(urlsplit(url).query)
        self.assertEqual([args[k][0] for k in ('is_girl', 'rank_type', 'date_type')], ['1', '2', '2'])
        n = int(args['page'][0])
        if n > 1:
            self.assertEqual(args['date'], ['202610'])
        self.pages.append(n)
        rows = [{'book_id': str(i), 'title': f'作品{i}', 'author': '作者', 'book_url': f'{qimao.ORIGIN}/shuku/{i}/',
                 'number': '12.7', 'unit': '万', 'words_num': '5.05万字', 'is_over': '0'}
                for i in range(1+(n-1)*20, min(n*20, self.total) + 1)]
        data = {'table_data': rows, 'page_data': {'page': str(n), 'page_size': '20',
                                                  'count': str(self.total), 'data_month': '2026-10'}}
        self.mutate(data, n)
        return json.dumps({'data': data}, ensure_ascii=False)

    def setUp(self):
        self.pages = []
        self.total = 114
        self.mutate = lambda data, page: None
        self.enter = patch.object(qimao, 'fetch_page', self.fetch)
        self.enter.start()
        self.addCleanup(self.enter.stop)
        pace = patch.object(qimao, 'pause_before_page')
        pace.start()
        self.addCleanup(pace.stop)

    def collect(self, **options):
        return qimao.collect({'channel': '女频', 'board': '新书榜', 'period': '月榜', **options})

    def test_official_scope_top100_and_numeric_units(self):
        snap = self.collect(limit=450)
        self.assertEqual(self.pages, [1, 2, 3, 4, 5])
        self.assertEqual([x['rank'] for x in snap['books']], list(range(1, 101)))
        self.assertEqual(snap['source_total'], 114)
        self.assertEqual(snap['data_date'], '2026-10')
        self.assertEqual(snap['books'][0]['metrics'], [
            {'name': '热度', 'value': '12.7', 'unit': '万'},
            {'name': '字数', 'value': '5.05', 'unit': '万字'}])

    def test_last_page_and_smaller_user_limit(self):
        self.total = 23
        self.assertEqual(len(self.collect()['books']), 23)
        self.pages.clear()
        self.assertEqual(len(self.collect(limit=4)['books']), 4)
        self.assertEqual(self.pages, [1])

    def test_wrong_chart_wrong_page_and_duplicate_ids_are_rejected(self):
        for fail in (lambda d, n: d['table_data'].reverse() if n == 1 else None,
                     lambda d, n: d['page_data'].update(page='1') if n == 2 else None,
                     lambda d, n: d['page_data'].update(data_month='2026-09') if n == 2 else None,
                     lambda d, n: d['table_data'][0].update(book_id='1', book_url=qimao.ORIGIN+'/shuku/1/')
                     if n == 2 else None):
            with self.subTest(fail=fail):
                self.mutate = fail
                with self.assertRaises(CollectorError):
                    self.collect()

    def test_wrong_selected_channel_is_not_published(self):
        with patch.object(qimao, 'fetch_page', return_value='<title>新书榜</title><span class="active">男生榜</span>'):
            with self.assertRaisesRegex(CollectorError, '频道'):
                self.collect()


if __name__ == '__main__':
    unittest.main()
