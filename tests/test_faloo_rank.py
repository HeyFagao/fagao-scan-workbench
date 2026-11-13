# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
import unittest
from unittest.mock import patch

from fagao import faloo_rank as platform
from fagao.collectors import CollectorError


def row(book, rank, *, host='b.faloo.com'):
    return f'''<div class="c_td_d_data"><div class="c_td_d_d_number2">{rank:02d}</div>
      <div class="c_td_d_d_img"><img src="http://img.faloo.com/Novel/166x235/1/100/000.jpg"></div>
      <div class="c_td_d_d_title"><a href="//{host}/{book}.html" title="书名{book}">书名...</a></div>
      <div class="c_td_d_d_time">2026-01-16 21:00</div><div class="c_td_d_d_count">18767</div>
      <div class="c_td_d_d_tag"><a title="古代">古..</a></div>
      <div class="c_td_d_d_other"><a class="c_td_d_d_author">笔名</a>|<a class="c_td_d_d_class">历史</a></div></div>'''


def page(rows):
    return f'''<div class="c_tab"><a class="hover" href="//b.faloo.com/SR_1.html">小说销售排行榜-天榜</a>
      <a href="//b.faloo.com/SR_2.html">小说订阅人数排行榜-天榜</a></div>
      <div class="c_tabdetail">{rows}</div><aside>{row(5,1)}</aside>'''


class FalooRankTests(unittest.TestCase):
    def test_independent_mobile_genre_uses_source_positions_and_only_its_list(self):
        html = '''<span class="page_title"><a>玄幻榜</a></span><ul class="novelList"><li>
          <a href="/888.html"><img class="cover" src="//img.faloo.com/Novel/332x470/1/2/3.jpg"></a>
          <div class="bl_r1_tit"><a href="/888.html" title="远方的钟">远方…</a></div>
          <p class="bl_r1_into"><a href="/888.html">开篇简介</a></p>
          <div class="nl_r1_author"><a>测试作者</a><a>异世大陆</a></div></li></ul>'''
        with patch.object(platform, 'fetch_page', return_value=html) as fetch:
            snap = platform.collect({'board':'玄幻榜'})
        fetch.assert_called_once_with('https://wap.faloo.com/bookrank_xuanhuan.html', encoding='gb18030')
        self.assertEqual((snap['books'][0]['title'], snap['books'][0]['summary'], snap['books'][0]['tags']),
                         ('远方的钟','开篇简介',['异世大陆']))
        self.assertEqual((snap['books'][0]['source_position'], snap['books'][0]['rank_semantics']),
                         (1, 'source_list_position'))
        self.assertFalse(snap['complete'])
        self.assertEqual(snap['pagination']['stop_reason'], 'first_page_only')
        with self.assertRaises(CollectorError):
            platform._mobile_books(html,'都市榜')
        data = html.replace('玄幻榜','小说书库')
        marker = '<a class="select" href="y_54_92_0_0_0_1.html">周点击</a>'
        with self.assertRaises(CollectorError):
            platform._mobile_books(data + marker, '女生周点击 · 女尊天下')
        with patch.object(platform,'fetch_page',return_value=data + marker):
            female = platform.collect({'board':'女生周点击 · 都市言情','limit':1})
        self.assertEqual((female['period'], female['books'][0]['id'], female['complete']), ('周榜','888',True))

    def test_preserves_original_positions_scope_and_public_metadata(self):
        with patch.object(platform, 'fetch_page', return_value=page(row(11,1)+row(12,3))) as fetch:
            snap = platform.collect({'limit': 80})
        fetch.assert_called_once_with('https://b.faloo.com/SR_1.html', encoding='gb18030')
        self.assertEqual([r['rank'] for r in snap['books']], [1,3])
        self.assertEqual(snap['books'][0], {'id':'11','rank':1,'title':'书名11','author':'笔名',
                         'url':'https://b.faloo.com/11.html','cover_url':'https://img.faloo.com/Novel/166x235/1/100/000.jpg',
                         'tags':['历史','古代'],'summary':'','metrics':[]})
        self.assertIsNone(snap['data_date'])
        self.assertEqual(snap['period'],'24小时')

    def test_rejects_wrong_tab_or_damaged_ranking(self):
        with self.assertRaises(CollectorError):
            platform.parse_page(page(row(11,1)), '订阅人数天榜')
        for rows in ('', row(1,1)+row(2,1), row(1,1)+row(1,2), row(1,1,host='outside.invalid')):
            with self.subTest(rows=rows[:36]), self.assertRaises(CollectorError):
                platform.parse_page(page(rows), '24小时热销榜')

    def test_user_limit_and_hard_cap_keep_the_original_rows(self):
        html = page(''.join(row(i,i) for i in range(1,106)))
        for requested, expected in ((1,1),(1000,100)):
            with self.subTest(requested=requested), patch.object(platform, 'fetch_page',return_value=html):
                snap = platform.collect({'limit':requested})
                self.assertEqual([r['rank'] for r in snap['books']], list(range(1,expected+1)))
                self.assertTrue(snap['complete'])
                self.assertEqual(snap['source_total'], 105)
                self.assertEqual(snap['pagination']['stop_reason'], 'requested_limit')


if __name__ == '__main__':
    unittest.main()
