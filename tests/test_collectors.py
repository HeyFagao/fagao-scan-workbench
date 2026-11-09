import json
import tempfile
import unittest
from unittest.mock import patch

from fagao.collectors import CollectorError, _state, _clean_text, _fanqie, parse_qidian, parse_ciweimao, auth, collect, fetch_text


class CollectorTests(unittest.TestCase):
    def test_initial_state_is_json_not_executed_script(self):
        self.assertEqual(_state('<script>window.__INITIAL_STATE__={"rank":{}};window.attack()</script>'), {"rank": {}})
        with self.assertRaises(CollectorError):
            _state('<script>__INITIAL_STATE__=eval("bad")</script>')

    def test_font_encoded_text_is_never_used_as_real_title(self):
        self.assertEqual(_clean_text('标题\ue123'), '')
        self.assertEqual(_clean_text('  正常标题 '), '正常标题')

    def test_missing_js_field_normalized_without_touching_quotes(self):
        data = _state('<script>__INITIAL_STATE__={"description":undefined,"abstract":"undefined是原文"}</script>')
        self.assertIsNone(data['description'])
        self.assertEqual(data['abstract'], 'undefined是原文')

    def test_fanqie_rank_and_details_are_id_scoped(self):
        rows = [dict(bookId='123456', currentPos=14, read_count='0'), dict(bookId='123457', currentPos=15)]
        rank = '<a href="/rank/0_1_253">古言脑洞</a><script>__INITIAL_STATE__=' + json.dumps({'rank': {'book_list': rows}}) + '</script>'
        detail = '<script>__INITIAL_STATE__=' + json.dumps({'page': {'bookId':'123456','bookName':'实际作品','author':'作者','abstract':'作品简介','categoryV2':'[]','wordNumber':1234}}) + '</script>'
        wrong_detail = '<script>__INITIAL_STATE__=' + json.dumps({'page': {'bookId':'999999','bookName':'推荐作品'}}) + '</script>'
        def fetch(url, mobile=False):
            return rank if '/rank/' in url else detail if url.endswith('123456') else wrong_detail
        with patch('fagao.collectors._fetch', side_effect=fetch):
            snapshot = _fanqie({'category':'古言脑洞','limit':30}, None)
        self.assertEqual(len(snapshot['books']), 1)
        book = snapshot['books'][0]
        self.assertEqual((book['id'], book['rank'], book['title']), ('123456',14,'实际作品'))
        self.assertEqual(book['metrics'][0]['value'], 0)
        self.assertFalse(snapshot['complete'])
        self.assertTrue(any('15' in message for message in snapshot['warnings']))

    def test_fanqie_no_title_fallback_to_encrypted_list(self):
        rank = '<a href="/rank/0_1_253">古言脑洞</a><script>__INITIAL_STATE__=' + json.dumps({'rank': {'book_list':[{'bookId':'123456','currentPos':1,'bookName':'编码\ue111'}]}}) + '</script>'
        with patch('fagao.collectors._fetch', side_effect=[rank, CollectorError('详情失败')]):
            with self.assertRaises(CollectorError):
                _fanqie({}, None)

    def test_fanqie_retries_only_failed_identity_once(self):
        rank = '<a href="/rank/0_1_253">古言脑洞</a><script>__INITIAL_STATE__=' + json.dumps({'rank': {'book_list':[{'bookId':'123456','currentPos':1}]}}) + '</script>'
        detail = '<script>__INITIAL_STATE__=' + json.dumps({'page':{'bookId':'123456','bookName':'原作品','abstract':'简介'}}) + '</script>'
        with patch('fagao.collectors._fetch', side_effect=[rank,CollectorError('临时断线','network_error'),detail]) as fetch:
            result = _fanqie({'limit':1},None)
        self.assertEqual(len(result['books']),1)
        self.assertEqual(fetch.call_count,3)
        self.assertEqual(fetch.call_args_list[1],fetch.call_args_list[2])
        self.assertTrue(result['complete'])

    def test_qidian_uses_explicit_rank_not_array_position(self):
        state = {'pageContext':{'pageProps':{'pageData':{'gender':'male','total':1000,'records':[
            {'bid':'999991','rankNum':14,'bName':'第十四名','rankCnt':'2万月票'},
            {'bid':'999992','bName':'缺名次'},
        ]}}}}
        html = '<script id="vite-plugin-ssr_pageContext">'+json.dumps(state)+'</script>'
        books,total = parse_qidian(html,'月票榜')
        self.assertEqual(len(books),1)
        self.assertEqual(books[0]['rank'],14)
        self.assertEqual(total,1000)
        self.assertEqual(books[0]['metrics'][0]['value'],'2万月票')

    def test_challenge_page_cannot_become_empty_success(self):
        with self.assertRaises(CollectorError):
            parse_qidian('<html>请完成验证</html>', '月票榜')
        with self.assertRaises(CollectorError):
            parse_ciweimao('<html>404 not found</html>', '月票榜')

    def test_ciweimao_deep_list_ignores_fan_leaderboard(self):
        html = '''<html><head><title>小说月票榜 - 刺猬猫</title></head><body>
        <ol class="rank-list"><li>1 粉丝名称 99999</li></ol>
        <ol class="rank-book-list"><li data-book-id="123456">
        <i>14</i><h3 class="tit"><a href="https://www.ciweimao.com/book/123456">真实书名</a></h3>
        <p>小说作者：<a>甲</a></p><p class="desc">一段简介</p></li></ol></body></html>'''
        books = parse_ciweimao(html,'月票榜')
        self.assertEqual(len(books),1)
        self.assertEqual((books[0]['id'],books[0]['rank'],books[0]['author']),('123456',14,'甲'))
        self.assertEqual(books[0]['summary'],'一段简介')
        self.assertEqual(books[0]['metrics'],[])

    def test_heiyan_has_no_simulated_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(auth('heiyan','check',directory)['state'],'logged_out')

    def test_unimplemented_short_source_does_not_return_long_books(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(CollectorError):
                collect('fanqie_short',{},directory)

    def test_encoded_chapter_is_explicit_failure(self):
        page = {'page':{'bookId':'123456','chapterListWithVolume':[[{'itemId':'999991','title':'第一章','needPay':0}]]}}
        chapter = {'reader':{'chapterData':{'bookId':'123456','itemId':'999991','content':'<p>'+('编码\ue123'*100)+'</p>'}}}
        responses = ['<script>__INITIAL_STATE__='+json.dumps(v)+'</script>' for v in (page,chapter)]
        with patch('fagao.collectors._fetch',side_effect=responses):
            with self.assertRaisesRegex(CollectorError,'字体编码'):
                fetch_text('fanqie','123456','/unused')

    def test_paid_chapter_not_requested(self):
        page = {'page':{'bookId':'123456','chapterListWithVolume':[[{'itemId':'999991','title':'第一章','needPay':1}]]}}
        with patch('fagao.collectors._fetch',return_value='<script>__INITIAL_STATE__='+json.dumps(page)+'</script>') as fetch:
            with self.assertRaisesRegex(CollectorError,'权限'):
                fetch_text('fanqie','123456','/unused')
        self.assertEqual(fetch.call_count,1)


if __name__ == '__main__':
    unittest.main()
