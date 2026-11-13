# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
import unittest
from unittest.mock import patch

from fagao import zongheng_rank as z
from fagao.collectors import CollectorError


def fixture(board='新书榜', page=1, total=22, edition='2026-11-09', period='周榜', missing=()):
    if board == '月票榜':
        selector = '<div class="data-year" data-val="2026"></div><div class="data-month" data-val="11"></div>'
    else:
        selector = (f'<div class="data-date" data-val="{edition.replace("-", "")}"><span class="drop_tit">{edition}</span></div>'
                    '<div class="rankclass_nav"><a class="on">全部分类</a></div>')
    if board in ('点击榜', '推荐榜'):
        selector = ('<div class="rankclass_nav"><a class="on" catepid="0">全部分类</a></div>'
                    f'<div><a class="on" period="{z.PERIOD_IDS[period]}">{period}</a></div>')
    elif board not in ('月票榜', '新书榜'):
        selector = ''
    metric = {'月票榜':'98<span>月票</span>', '新书榜':'98<span>人气</span>',
              '点击榜':'98<span>点击数</span>', '推荐榜':'98<span>推荐票</span>'}.get(board,'')
    rows = []
    for rank in range((page - 1)*20 + 1, min(total,page*20) + 1):
        if rank in missing:
            continue
        bid = 1640000 + rank
        rows.append(f"""
            <div class="rank_d_list borderB_c_dsh clearfix" bookId="{bid}">
              <div class="rank_d_book_img"><img src="https://static.zongheng.com/upload/cover/sample.jpg"></div>
              <div class="rank_d_b_name"><a href="https://book.zongheng.com/book/{bid}.html">样本{rank}</a></div>
              <div class="rank_d_b_cate"><a>作者</a>|<a>科幻</a>|<a>连载</a></div>
              <div class="rank_d_b_info">简介</div>
              <div class="rank_d_b_num rank_d_icon">{rank}</div>
              <div class="rank_d_b_ticket">{metric}</div>
            </div>""")
    return (f'<div class="rank_main"><div class="rank_i_title_name"><span class="active">{board}</span></div>{selector}'
            f'<div class="rankpage_box">{"".join(rows)}<div class="rank_d_pagesize pagebar" page="{page}" '
            f'count="{(total+19)//20}" total="{total}"></div></div></div>')


class ZonghengRankTests(unittest.TestCase):
    def test_explicit_source_edition_ranks_titles_and_metrics(self):
        for board, edition, metric in [('新书榜','2026-11-09','人气'),('月票榜','2026-11','月票')]:
            with self.subTest(board=board), patch.object(z,'fetch_page',return_value=fixture(board)):
                snap = z.collect({'board':board,'limit':3})
                self.assertEqual(snap['data_date'],edition)
                self.assertEqual(snap['source_total'],22)
                self.assertEqual([b['rank'] for b in snap['books']], [1,2,3])
                self.assertEqual(snap['books'][2]['url'], 'https://book.zongheng.com/book/1640003.html')
                self.assertEqual(snap['books'][0]['metrics'][0]['name'],metric)
                self.assertEqual(snap['books'][0]['tags'], ['科幻'])

    def test_pagination_respects_request_and_checks_real_last_page(self):
        with patch.object(z,'fetch_page',side_effect=[fixture(),fixture(page=2)]) as get, patch.object(z,'pause_before_page') as pause:
            snap=z.collect({'limit':100})
        self.assertEqual(len(snap['books']),22)
        self.assertEqual(snap['books'][-1]['rank'],22)
        self.assertEqual(snap['pagination']['stop_reason'],'last_page')
        self.assertEqual(get.call_args_list[-1].args[0],z.BOARD_URLS['新书榜']+'&p=2')
        self.assertEqual(pause.call_count,1)

    def test_mismatched_board_book_url_or_source_rank_is_rejected(self):
        html=fixture()
        for changed in [html.replace('>新书榜<','>月票榜<'), html.replace('/book/1640001.html','/book/123.html'),
                        html.replace('rank_d_icon">1<','rank_d_icon">2<')]:
            with self.subTest(), self.assertRaises(CollectorError):
                z.parse_page(changed,'新书榜',1)

    def test_changed_edition_overlapping_ids_or_repeated_page_aborts(self):
        bad=[fixture(page=2,edition='2026-11-02'),fixture(page=2).replace('1640021','1640001'),fixture()]
        for html in bad:
            with self.subTest(), patch.object(z,'fetch_page',side_effect=[fixture(),html]), patch.object(z,'pause_before_page'):
                with self.assertRaises(CollectorError):
                    z.collect({'limit':100})

    def test_scoped_period_and_category_match_the_source_and_keep_missing_ranks(self):
        with patch.object(z, 'fetch_page', return_value=fixture('推荐榜', period='日榜', missing=(3,))) as get:
            snap = z.collect({'board':'推荐榜', 'period':'日榜', 'limit':5})
            self.assertEqual([b['rank'] for b in snap['books']], [1,2,4,5])
            self.assertEqual(snap['pagination']['stop_reason'], 'source_gaps')
            self.assertFalse(snap['complete'])
            self.assertIsNone(snap['data_date'])
            self.assertEqual(snap['books'][0]['metrics'][0]['name'], '推荐票')
            self.assertTrue(get.call_args.args[0].endswith('rt=6&d=1&i=1'))
            for altered in ({'period':'周榜'}, {'category':'奇幻.玄幻'}):
                with self.subTest(altered=altered), self.assertRaises(CollectorError):
                    z.collect({'board':'推荐榜', 'period':'日榜', **altered})

    def test_undated_rank_does_not_invent_numbers_or_read_a_nonexistent_page(self):
        with patch.object(z, 'fetch_page', return_value=fixture('新书订阅榜', total=11, missing=(6,))) as get:
            snap = z.collect({'board':'新书订阅榜', 'limit':100})
            self.assertEqual(get.call_count, 1)
            self.assertEqual(len(snap['books']), 10)
            self.assertEqual(snap['books'][-1]['rank'], 11)
            self.assertIsNone(snap['data_date'])
            self.assertEqual(snap['books'][0]['metrics'], [])
            self.assertFalse(snap['complete'])
