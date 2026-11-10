import json
import tempfile
import unittest
from unittest.mock import patch

from fagao.collectors import CollectorError, _state, _clean_text, _fanqie, _ciweimao, _qidian, _merge_page, _page_options, _cover_url, parse_qidian, parse_ciweimao, auth, collect, fetch_text
from fagao.fanqie_codec import decode_text, has_unmapped_pua


class CollectorTests(unittest.TestCase):
    def test_initial_state_is_json_not_executed_script(self):
        self.assertEqual(_state('<script>window.__INITIAL_STATE__={"rank":{}};window.attack()</script>'), {"rank": {}})
        with self.assertRaises(CollectorError):
            _state('<script>__INITIAL_STATE__=eval("bad")</script>')

    def test_font_encoded_text_is_never_used_as_real_title(self):
        self.assertEqual(_clean_text('标题\ue123'), '')
        self.assertEqual(_clean_text('  正常标题 '), '正常标题')

    def test_user_font_table_decodes_known_glyphs_and_preserves_unknown(self):
        self.assertEqual(decode_text('\ue521\ue40e化龙\ue436，\ue487\ue44a功\ue481\ue41e假\ue4f3？'), '我都化龙了，你说功法是假的？')
        self.assertEqual(decode_text('普通中文 English 123'), '普通中文 English 123')
        self.assertEqual(decode_text('未知\ue111'), '未知\ue111')
        self.assertTrue(has_unmapped_pua(decode_text('未知\ue111')))
        self.assertFalse(has_unmapped_pua(decode_text('\ue521\ue40e')))

    def test_fanqie_decodes_297_rows_with_zero_detail_requests(self):
        rows=[{'bookId':str(100000+i),'currentPos':i+1,'bookName':'\ue521的书'+str(i),
               'author':'甲','abstract':'\ue521的简介','creationStatus':'1',
               'thumbUri':'https://p3-novel-sign.byteimg.com/cover.jpg'} for i in range(297)]
        def fetch(url, **kwargs):
            if '/rank/0_' in url:
                return '<a href="/rank/0_1_253">古言脑洞</a><script>__INITIAL_STATE__='+json.dumps({'rank':{'book_list':rows[:10],'rankVersion':'1234567890','total_num':100}})+'</script>'
            if '/api/rank/' in url:
                from urllib.parse import parse_qs,urlsplit
                query=parse_qs(urlsplit(url).query)
                self.assertEqual(query['rankMold'],['1'])
                self.assertEqual(query['rank_version'],['1234567890'])
                offset=int(query['offset'][0]); limit=int(query['limit'][0])
                self.assertEqual(limit,100)
                return json.dumps({'code':0,'data':{'book_list':rows[offset:offset+limit],'rankVersion':'1234567890'}})
            self.fail('Decoded rank metadata must not request a per-book detail page')
        with patch('fagao.collectors._fetch',side_effect=fetch) as mocked,patch('fagao.collectors.time.sleep'):
            result=_fanqie({},None)
        self.assertEqual(mocked.call_count,5)
        self.assertEqual(len(result['books']),297)
        self.assertEqual(result['books'][13]['rank'],14)
        self.assertEqual(result['books'][13]['title'],'我的书13')
        self.assertEqual(result['pagination']['page_size'],100)
        self.assertEqual(result['pagination']['initial_page_size'],10)
        self.assertEqual(result['pagination']['stop_reason'],'empty_page')
        self.assertEqual(result['metadata_statistics'],{'rank':297,'cache':0,'detail':0,'missing':0})
        self.assertTrue(result['complete'])

    def test_fanqie_unmapped_rank_glyph_falls_back_to_same_book_detail(self):
        row={'bookId':'123456','currentPos':14,'bookName':'\ue111','author':'甲','abstract':'简介'}
        rank='<a href="/rank/0_1_253">古言脑洞</a><script>__INITIAL_STATE__='+json.dumps({'rank':{'book_list':[row]}})+'</script>'
        detail='<script>__INITIAL_STATE__='+json.dumps({'page':{'bookId':'123456','bookName':'公开详情书名','author':'甲','abstract':'简介'}})+'</script>'
        with patch('fagao.collectors._fetch',side_effect=[rank,detail]) as mocked:
            result=_fanqie({'limit':1},None)
        self.assertEqual(mocked.call_count,2)
        self.assertEqual(result['books'][0]['title'],'公开详情书名')
        self.assertEqual(result['books'][0]['rank'],14)
        self.assertEqual(result['metadata_statistics']['detail'],1)

    def test_fanqie_unmapped_author_is_not_silently_reported_complete(self):
        row={'bookId':'123456','currentPos':1,'bookName':'真实书名','author':'\ue111','abstract':'简介'}
        rank='<a href="/rank/0_1_253">古言脑洞</a><script>__INITIAL_STATE__='+json.dumps({'rank':{'book_list':[row]}})+'</script>'
        detail='<script>__INITIAL_STATE__='+json.dumps({'page':row})+'</script>'
        with patch('fagao.collectors._fetch',side_effect=[rank,detail,detail]):
            result=_fanqie({'limit':1},None)
        self.assertFalse(result['complete'])
        self.assertEqual(result['books'][0]['metadata_status'],'missing')
        self.assertEqual(result['metadata_statistics']['missing'],1)

    def test_fanqie_official_empty_page_without_version_is_source_end(self):
        row={'bookId':'123456','currentPos':1,'bookName':'真实书名','author':'甲','abstract':'简介'}
        rank='<a href="/rank/0_1_253">古言脑洞</a><script>__INITIAL_STATE__='+json.dumps({'rank':{'book_list':[row],'rankVersion':'1234567890'}})+'</script>'
        # Real official empty-page shape: code=0 and [] but no rankVersion key.
        empty=json.dumps({'code':0,'data':{'book_list':[],'rankTypeText':'','total_num':0},'message':'success'})
        with patch('fagao.collectors._fetch',side_effect=[rank,empty]),patch('fagao.collectors.time.sleep'):
            result=_fanqie({},None)
        self.assertEqual(result['pagination']['stop_reason'],'empty_page')
        self.assertTrue(result['complete'])
        self.assertEqual(result['warnings'],[])

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
        self.assertEqual(len(snapshot['books']), 2)
        book = snapshot['books'][0]
        self.assertEqual((book['id'], book['rank'], book['title']), ('123456',14,'实际作品'))
        self.assertEqual(book['metrics'][0]['value'], 0)
        self.assertEqual(book['status'], '')
        self.assertFalse(snapshot['complete'])
        self.assertTrue(any('15' in message for message in snapshot['warnings']))
        self.assertEqual(snapshot['books'][1]['metadata_status'],'missing')
        self.assertNotEqual(snapshot['books'][1]['title'],'推荐作品')

    def test_fanqie_no_title_fallback_to_encrypted_list(self):
        rank = '<a href="/rank/0_1_253">古言脑洞</a><script>__INITIAL_STATE__=' + json.dumps({'rank': {'book_list':[{'bookId':'123456','currentPos':1,'bookName':'编码\ue111'}]}}) + '</script>'
        with patch('fagao.collectors._fetch', side_effect=[rank, CollectorError('详情失败')]):
            result = _fanqie({}, None)
        self.assertEqual(result['books'][0]['id'],'123456')
        self.assertEqual(result['books'][0]['metadata_status'],'missing')
        self.assertNotIn('\ue111',result['books'][0]['title'])
        self.assertFalse(result['complete'])

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

    def test_text_adapter_preserves_fixed_identity_and_requested_range(self):
        progress = lambda message: None
        with patch('fagao.fanqie_content.fetch_book_text', return_value={'book_id':'123456'}) as fetch:
            result = fetch_text('fanqie','123456','/unused',max_chapters=None,start_chapter=2,progress=progress)
        fetch.assert_called_once_with('123456','/unused',max_chapters=None,start_chapter=2,progress=progress)
        self.assertEqual(result['book_id'],'123456')

    def test_unimplemented_text_platform_does_not_request_fanqie(self):
        with patch('fagao.fanqie_content.fetch_book_text') as fetch:
            with self.assertRaisesRegex(CollectorError,'尚未接入'):
                fetch_text('royalroad','123456','/unused')
        fetch.assert_not_called()

    def test_page_ceiling_defaults_to_100_without_fixed_book_cap(self):
        self.assertEqual(_page_options({},10),(100,1000))
        self.assertEqual(_page_options({},20),(100,2000))
        self.assertEqual(_page_options({'max_pages':900},10),(100,1000))
        self.assertEqual(_page_options({'max_pages':3,'limit':17},20),(3,17))
        self.assertEqual(_page_options({'max_pages':1,'limit':1000},10),(1,1000))

    def test_qidian_unreadable_source_rows_never_report_complete(self):
        data={'gender':'male','total':2,'isLast':1,'records':[
            {'bid':'999991','rankNum':1,'bName':'正常书名'},
            {'bid':'999992','rankNum':2,'bName':'不可读\ue111'}]}
        html='<script id="vite-plugin-ssr_pageContext">'+json.dumps({'pageContext':{'pageProps':{'pageData':data}}})+'</script>'
        with patch('fagao.collectors._fetch',return_value=html):
            result=_qidian({},None)
        self.assertEqual(len(result['books']),1)
        self.assertFalse(result['complete'])
        self.assertTrue(any('1 个源条目' in warning for warning in result['warnings']))

    def test_heiyan_partial_snapshot_preserves_expired_auth_and_pagination(self):
        from unittest.mock import Mock
        worker=Mock()
        worker.call.return_value={'state':'connected','auth_state':'expired','auth_message':'需要重新登录',
            'books':[{'id':'123456','title':'真实书目','rank':None}], 'complete':False,
            'pagination':{'pages_fetched':1,'max_pages':100,'stop_reason':'session_failed'}}
        with patch('fagao.collectors._worker',return_value=worker):
            result=collect('heiyan',{},'/unused')
        self.assertEqual(result['auth_state'],'expired')
        self.assertEqual(result['auth_message'],'需要重新登录')
        self.assertEqual(len(result['books']),1)
        self.assertEqual(result['pagination']['stop_reason'],'session_failed')

    def test_rank_conflicts_and_duplicates_are_distinguished(self):
        current=[{'id':'123456','rank':14}]
        self.assertEqual(_merge_page(current,[{'id':'123456','rank':14}]),([],None))
        self.assertEqual(_merge_page(current,[{'id':'999999','rank':14}]),([],'rank_conflict'))
        self.assertEqual(_merge_page(current,[{'id':'123456','rank':15}]),([],'rank_conflict'))

    def test_covers_reject_unsafe_or_placeholder_values(self):
        self.assertEqual(_cover_url('javascript:alert(1)'), '')
        self.assertEqual(_cover_url('data:image/png;base64,AAAA'), '')
        self.assertEqual(_cover_url('https://example.com/transparent.png'), '')
        self.assertEqual(_cover_url('//bookcover.yuewen.com/a.jpg'), 'https://bookcover.yuewen.com/a.jpg')

    def test_fanqie_more_than_30_and_empty_source_stops(self):
        rows=[{'bookId':str(100000+i),'currentPos':i+1,'thumbUri':'https://p3-reading-sign.fqnovelpic.com/a.jpg'} for i in range(40)]
        def fetch(url, **kwargs):
            if '/rank/0_' in url:
                return '<a href="/rank/0_1_253">古言脑洞</a><script>__INITIAL_STATE__='+json.dumps({'rank':{'book_list':rows[:10],'rankVersion':'1234567890','total_num':10}})+'</script>'
            if '/api/rank/' in url:
                from urllib.parse import parse_qs,urlsplit
                offset=int(parse_qs(urlsplit(url).query)['offset'][0])
                return json.dumps({'code':0,'data':{'book_list':rows[offset:offset+10],'rankVersion':'1234567890'}})
            bid=url.rsplit('/',1)[1]
            return '<script>__INITIAL_STATE__='+json.dumps({'page':{'bookId':bid,'bookName':'作品'+bid}})+'</script>'
        with patch('fagao.collectors._fetch',side_effect=fetch),patch('fagao.collectors.time.sleep'):
            result=_fanqie({'max_pages':100},None)
        self.assertEqual(len(result['books']),40)
        self.assertEqual(result['pagination']['pages_fetched'],5)
        self.assertEqual(result['pagination']['stop_reason'],'empty_page')
        self.assertTrue(result['complete'])
        self.assertEqual(result['books'][-1]['rank'],40)
        self.assertTrue(result['books'][-1]['cover_url'])

    def test_fanqie_metadata_cache_avoids_repeat_details(self):
        row={'bookId':'123456','currentPos':1,'lastChapterUpdateTime':'version1'}
        def fetch(url, **kwargs):
            if '/rank/0_' in url:
                return '<a href="/rank/0_1_253">古言脑洞</a><script>__INITIAL_STATE__='+json.dumps({'rank':{'book_list':[row]}})+'</script>'
            return '<script>__INITIAL_STATE__='+json.dumps({'page':{'bookId':'123456','bookName':'作品','thumbUri':'https://p3-reading-sign.fqnovelpic.com/a.jpg'}})+'</script>'
        with tempfile.TemporaryDirectory() as directory,patch('fagao.collectors._fetch',side_effect=fetch) as mocked:
            _fanqie({'limit':1},None,directory)
            _fanqie({'limit':1},None,directory)
            self.assertEqual(sum('/page/' in call.args[0] for call in mocked.call_args_list),1)
            row['lastChapterUpdateTime']='version2'
            _fanqie({'limit':1},None,directory)
            self.assertEqual(sum('/page/' in call.args[0] for call in mocked.call_args_list),2)

    def test_fanqie_version_change_never_merges_second_page(self):
        rank='<a href="/rank/0_1_253">古言脑洞</a><script>__INITIAL_STATE__='+json.dumps({'rank':{'book_list':[{'bookId':'123456','currentPos':1}],'rankVersion':'old'}})+'</script>'
        detail='<script>__INITIAL_STATE__='+json.dumps({'page':{'bookId':'123456','bookName':'原版本'}})+'</script>'
        other=json.dumps({'code':0,'data':{'book_list':[{'bookId':'999999','currentPos':2}],'rankVersion':'new'}})
        with patch('fagao.collectors._fetch',side_effect=[rank,other,detail]),patch('fagao.collectors.time.sleep'):
            result=_fanqie({},None)
        self.assertEqual([b['id'] for b in result['books']],['123456'])
        self.assertEqual(result['pagination']['stop_reason'],'version_changed')

    def test_ciweimao_follows_only_real_next_links_with_cover(self):
        def html(rank,next_page=None):
            bid=str(123450+rank)
            return '<title>小说月票榜 - 刺猬猫</title><ol class="rank-book-list"><li data-book-id="'+bid+'"><i>'+str(rank)+'</i><a class="cover"><img src="https://example.com/transparent.png" data-original="https://e1.kuangxiangit.com/cover.jpg"></a><h3 class="tit"><a href="https://www.ciweimao.com/book/'+bid+'">实际作品</a></h3></li></ol>'+('<a rel="next" href="https://www.ciweimao.com/rank-index/yp-month/'+str(next_page)+'">&gt;&gt;</a>' if next_page else '')
        with patch('fagao.collectors._fetch',side_effect=[html(1,2),html(11)]),patch('fagao.collectors.time.sleep'):
            result=_ciweimao({},None)
        self.assertEqual([b['rank'] for b in result['books']],[1,11])
        self.assertEqual(result['pagination']['stop_reason'],'last_page')
        self.assertEqual(result['books'][0]['cover_url'],'https://e1.kuangxiangit.com/cover.jpg')

    def test_progress_cancellation_propagates_before_network_next_page(self):
        rank='<a href="/rank/0_1_253">古言脑洞</a><script>__INITIAL_STATE__='+json.dumps({'rank':{'book_list':[{'bookId':'123456','currentPos':1}]}})+'</script>'
        class Cancelled(Exception): pass
        def progress(message):
            if '第 2 页' in message: raise Cancelled()
        with patch('fagao.collectors._fetch',return_value=rank) as fetch:
            with self.assertRaises(Cancelled): _fanqie({},progress)
        self.assertEqual(fetch.call_count,1)

    def test_ciweimao_page_budget_does_not_shorten_explicit_requested_count(self):
        items=''.join('<li data-book-id="'+str(123450+i)+'"><i>'+str(i)+'</i><h3 class="tit"><a href="https://www.ciweimao.com/book/'+str(123450+i)+'">真实书名</a></h3></li>' for i in range(1,11))
        html='<title>小说月票榜 - 刺猬猫</title><ol class="rank-book-list">'+items+'</ol><a rel="next" href="https://www.ciweimao.com/rank-index/yp-month/2">下一页</a>'
        with patch('fagao.collectors._fetch',return_value=html),patch('fagao.collectors.time.sleep'):
            result=_ciweimao({'limit':1000,'max_pages':1},None)
        self.assertEqual(result['requested_count'],1000)
        self.assertEqual(result['pagination']['stop_reason'],'max_pages')
        self.assertFalse(result['complete'])

    def test_fanqie_first_page_is_published_before_network_or_detail_wait(self):
        rows = [{'bookId': '123456', 'currentPos': 14, 'bookName': '\ue521的书',
                 'author': '甲', 'abstract': '简介', 'read_count': 700,
                 'thumbUri': 'https://example.com/real-cover.jpg'},
                {'bookId': '123457', 'currentPos': 15, 'bookName': '第二本',
                 'author': '乙', 'abstract': '简介'}]
        rank = '<a href="/rank/0_1_253">古言脑洞</a><script>__INITIAL_STATE__=' + json.dumps(
            {'rank': {'book_list': rows[:1], 'rankVersion': '1234567890'}}) + '</script>'
        second = json.dumps({'code': 0, 'data': {'book_list': rows[1:], 'rankVersion': '1234567890'}})
        events = []
        def progress(message, **kwargs):
            if 'snapshot' in kwargs:
                events.append((fetch.call_count, kwargs['snapshot'], kwargs['has_more']))
        with patch('fagao.collectors._fetch', side_effect=[rank, second, CollectorError('暂时断线')]) as fetch, patch('fagao.collectors.time.sleep'):
            result = _fanqie({}, progress)
        self.assertEqual([event[0] for event in events], [1, 2])
        self.assertEqual([len(event[1]['books']) for event in events], [1, 2])
        self.assertEqual(events[0][1]['books'][0]['rank'], 14)
        self.assertEqual(events[0][1]['books'][0]['title'], '我的书')
        self.assertEqual(events[0][1]['books'][0]['cover_url'], 'https://example.com/real-cover.jpg')
        self.assertEqual(events[0][1]['rank_version'], '1234567890')
        self.assertEqual(events[0][1]['data_date'], result['data_date'])
        self.assertFalse(events[0][1]['complete'])
        self.assertEqual(events[0][1]['pagination']['stop_reason'], 'loading')
        self.assertTrue(events[0][2])
        self.assertEqual(result['pagination']['stop_reason'], 'request_failed')

    def test_fanqie_preview_does_not_emit_unreadable_or_reassigned_identity(self):
        row = {'bookId': '123456', 'currentPos': 14, 'bookName': '实际书名', 'author': '甲', 'abstract': '简介'}
        rank = '<a href="/rank/0_1_253">古言脑洞</a><script>__INITIAL_STATE__=' + json.dumps(
            {'rank': {'book_list': [row], 'rankVersion': 'old'}}) + '</script>'
        changed = json.dumps({'code': 0, 'data': {'book_list': [{'bookId': '999999', 'currentPos': 15,
            'bookName': '其他时点的作品'}], 'rankVersion': 'new'}})
        events = []
        def progress(message, **kwargs):
            if 'snapshot' in kwargs: events.append(kwargs['snapshot'])
        with patch('fagao.collectors._fetch', side_effect=[rank, changed]), patch('fagao.collectors.time.sleep'):
            result = _fanqie({}, progress)
        self.assertEqual(len(events), 1)
        self.assertEqual([book['id'] for book in events[0]['books']], ['123456'])
        self.assertEqual(result['pagination']['stop_reason'], 'version_changed')

    def test_partial_callback_cancellation_stops_before_second_source_request(self):
        row = {'bookId': '123456', 'currentPos': 1, 'bookName': '真实书名', 'author': '甲', 'abstract': '简介'}
        rank = '<a href="/rank/0_1_253">古言脑洞</a><script>__INITIAL_STATE__=' + json.dumps(
            {'rank': {'book_list': [row]}}) + '</script>'
        def progress(message, **kwargs):
            if 'snapshot' in kwargs: raise CollectorError('用户取消')
        with patch('fagao.collectors._fetch', return_value=rank) as fetch:
            with self.assertRaisesRegex(CollectorError, '用户取消'):
                _fanqie({}, progress)
        self.assertEqual(fetch.call_count, 1)

    def test_qidian_first_page_precedes_pagination_and_uses_issued_session(self):
        from types import SimpleNamespace
        first = {'gender': 'male', 'total': 2, 'isLast': 0, 'records': [
            {'bid': '999991', 'rankNum': 1, 'bName': '首批书目'}]}
        second = {'gender': 'male', 'pageNum': 2, 'isLast': 1, 'records': [
            {'bid': '999992', 'rankNum': 2, 'bName': '后续书目'}]}
        html = '<script id="vite-plugin-ssr_pageContext">' + json.dumps(
            {'pageContext': {'pageProps': {'pageData': first}}}) + '</script>'
        events = []
        def progress(message, **kwargs):
            if 'snapshot' in kwargs: events.append((fetch.call_count, kwargs))
        cookie = SimpleNamespace(name='_csrfToken', value='issued-only-test', domain='m.qidian.com')
        with patch('fagao.collectors.CookieJar', return_value=[cookie]), patch('fagao.collectors._fetch',
                side_effect=[html, json.dumps({'code': 0, 'data': second})]) as fetch, patch('fagao.collectors.time.sleep'):
            result = _qidian({}, progress)
        self.assertEqual([event[0] for event in events], [1, 2])
        self.assertTrue(events[0][1]['has_more'])
        self.assertFalse(events[1][1]['has_more'])
        self.assertEqual(events[0][1]['snapshot']['books'][0]['rank'], 1)
        self.assertNotIn('issued-only-test', json.dumps(events))
        self.assertEqual(len(result['books']), 2)

    def test_ciweimao_first_page_is_visible_before_following_verified_link(self):
        def html(rank, next_page=None):
            bid = str(123450 + rank)
            return ('<title>小说月票榜 - 刺猬猫</title><ol class="rank-book-list"><li data-book-id="' + bid +
                    '"><i>' + str(rank) + '</i><h3 class="tit"><a href="https://www.ciweimao.com/book/' + bid +
                    '">真实作品</a></h3></li></ol>' + ('<a rel="next" href="https://www.ciweimao.com/rank-index/yp-month/' +
                    str(next_page) + '">下一页</a>' if next_page else ''))
        events = []
        def progress(message, **kwargs):
            if 'snapshot' in kwargs: events.append((fetch.call_count, kwargs))
        with patch('fagao.collectors._fetch', side_effect=[html(1, 2), html(2)]) as fetch, patch('fagao.collectors.time.sleep'):
            result = _ciweimao({}, progress)
        self.assertEqual([event[0] for event in events], [1, 2])
        self.assertEqual([event[1]['has_more'] for event in events], [True, False])
        self.assertEqual(len(events[0][1]['snapshot']['books']), 1)
        self.assertEqual(events[0][1]['snapshot']['period'], '月榜')
        self.assertEqual(result['pagination']['stop_reason'], 'last_page')


if __name__ == '__main__':
    unittest.main()
