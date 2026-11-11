# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
import json
import unittest
from unittest.mock import patch

from fagao.collectors import CollectorError
from fagao.overseas import (OVERSEAS_PLATFORMS, collect_overseas,
                            parse_goodnovel, parse_dreame, translated_tags)


def good_book(ident, **extra):
    return {'bookId':str(ident),'bookName':'An English Title '+str(ident),
            'pseudonym':'Author Example','introduction':'First line.\nSecond line: unchanged.',
            'bookResourceUrl':'An-English-Title_'+str(ident),
            'cover':'https://acf.goodnovel.com/book/example.jpg','genreNames':['Werewolf'],
            'newTagsNames':['Revenge','Unmapped Tag'],'totalWords':18527,'chapterCount':28,
            'ratings':9.7,'viewCountDisplay':'2.7M','writeStatus':'COMPLETE',**extra}


def good_page(ids, pages=2, short=False, **extra):
    if short:
        data={'Browse':{'bookList':[good_book(i) for i in ids], 'totalPage':pages,
                        'total':40,'popular':'POPULAR', **extra}}
    else:
        data={'HomeDataModule':{'books':[good_book(i) for i in ids], 'totals':pages,**extra}}
    return '<script>window.__INITIAL_STATE__='+json.dumps(data)+';</script>'


def dreame_page(ids, page=1, total=20, size=10):
    data={'props':{'pageProps':{'info':{'list':[{'id':i,'name':'Original Title '+str(i),
        'descr':'Original English description.','author_name':'Author','cover_url':'https://assets.dreame.com/example.webp',
        'read_num':200,'follow_num':10} for i in ids],
        'pager':{'pageno':page,'totalpage':total,'pagesize':size}}}}}
    return '<script id="__NEXT_DATA__" type="application/json">'+json.dumps(data)+'</script>'+''.join(
        '<a href="/story/'+str(i)+'-original-title">Title</a>' for i in ids)


class OverseasCatalogueTests(unittest.TestCase):
    def test_registry_and_vocabulary_are_chinese_navigation(self):
        self.assertEqual({p['id'] for p in OVERSEAS_PLATFORMS},
                         {'goodnovel','goodnovel_short','meganovel','dreame','webnovel','royalroad','scribblehub'})
        for platform in OVERSEAS_PLATFORMS:
            self.assertEqual(platform['mode'],'overseas')
            for key in ('category','board','channel'):
                self.assertRegex(platform['default_options'][key],r'[\u4e00-\u9fff]')
        self.assertEqual(translated_tags(['Werewolf','Revenge','Unmapped Tag','Revenge']),['狼人','复仇'])

    def test_original_title_summary_and_metric_precision_preserved(self):
        books,pages,total=parse_goodnovel(good_page([101]))
        self.assertEqual(books[0]['title'],'An English Title 101')
        self.assertEqual(books[0]['summary'],'First line.\nSecond line: unchanged.')
        self.assertEqual(books[0]['tags'],['狼人','复仇'])
        self.assertEqual(books[0]['source_tags'],['Werewolf','Revenge','Unmapped Tag'])
        metrics={m['name']:m for m in books[0]['metrics']}
        self.assertEqual(metrics['阅读量']['value'],'2.7M')
        self.assertEqual(metrics['英文词数'],{'name':'英文词数','value':18527,'unit':'词'})
        self.assertIsNone(books[0]['rank'])
        self.assertEqual(pages,2)
        self.assertIsNone(total)

    def test_short_library_has_no_fabricated_official_rank(self):
        rows,pages,total=parse_goodnovel(good_page([301],pages=5,short=True),'goodnovel_short','全部')
        self.assertIsNone(rows[0]['rank'])
        self.assertEqual(rows[0]['rank_semantics'],'source_list_position')
        self.assertEqual((pages,total),(5,40))

    def test_source_slug_identity_mismatch_rejected(self):
        html=good_page([101]).replace('An-English-Title_101','An-English-Title_999')
        with self.assertRaises(CollectorError):parse_goodnovel(html)

    def test_dreame_uses_record_total_and_source_page(self):
        books,pages,total=parse_dreame(dreame_page([11,12],page=2,total=50),page=2)
        self.assertEqual((pages,total),(5,50))
        self.assertEqual([b['rank'] for b in books],[11,12])
        self.assertEqual(books[0]['url'],'https://www.dreame.com/story/11-original-title')
        with self.assertRaises(CollectorError):parse_dreame(dreame_page([11],page=1),page=2)

    def test_goodnovel_publishes_first_page_before_loading_second(self):
        events=[]
        def fetch(url,**kw):
            events.append(('fetch',url))
            return good_page([201,202]) if 'page=2' in url else good_page([101,102])
        def progress(message,**kw):
            if kw:events.append(('preview',len(kw['snapshot']['books']),kw['has_more']))
        with patch('fagao.collectors._fetch',side_effect=fetch),patch('fagao.collectors.time.sleep'):
            data=collect_overseas('goodnovel',{'max_pages':2},None,progress)
        self.assertEqual(len(data['books']),4)
        self.assertEqual([b['source_position'] for b in data['books']],[1,2,11,12])
        self.assertTrue(all(b['rank'] is None for b in data['books']))
        self.assertEqual([e[0] for e in events],['fetch','preview','fetch','preview'])
        self.assertEqual(events[1],('preview',2,True))
        self.assertTrue(data['complete'])

    def test_meganovel_uses_page_index(self):
        with patch('fagao.collectors._fetch',side_effect=[good_page([1]),good_page([2])]) as fetch,patch('fagao.collectors.time.sleep'):
            data=collect_overseas('meganovel',{'max_pages':2},None)
        self.assertTrue(fetch.call_args_list[1].args[0].endswith('?pageIndex=2'))
        self.assertIn('meganovel.com/book/',data['books'][0]['url'])

    def test_requested_limit_retains_only_requested_count(self):
        with patch('fagao.collectors._fetch',return_value=good_page([1,2,3])) as fetch:
            data=collect_overseas('goodnovel',{'limit':2},None)
        self.assertEqual(len(data['books']),2)
        self.assertTrue(data['complete'])
        self.assertEqual(data['pagination']['stop_reason'],'requested_limit')
        self.assertEqual(fetch.call_count,1)

    def test_page_limit_does_not_claim_source_end(self):
        with patch('fagao.collectors._fetch',return_value=good_page([1],pages=5)):
            data=collect_overseas('goodnovel',{'max_pages':1},None)
        self.assertFalse(data['complete'])
        self.assertEqual(data['pagination']['stop_reason'],'max_pages')

    def test_repeated_page_stops_without_duplicate_or_fabricated_rank(self):
        with patch('fagao.collectors._fetch',return_value=good_page([1,2])),patch('fagao.collectors.time.sleep'):
            data=collect_overseas('goodnovel',{'max_pages':2},None)
        self.assertEqual(len(data['books']),2)
        self.assertFalse(data['complete'])
        self.assertEqual(data['pagination']['stop_reason'],'repeated_page')

    def test_later_network_failure_preserves_usable_first_page(self):
        with patch('fagao.collectors._fetch',side_effect=[good_page([1]),CollectorError('HTTP 503','network_error')]),patch('fagao.collectors.time.sleep'):
            data=collect_overseas('goodnovel',{'max_pages':2},None)
        self.assertEqual(len(data['books']),1)
        self.assertFalse(data['complete'])
        self.assertIn('HTTP 503',data['warnings'][0])

    def test_empty_or_malformed_first_page_raises(self):
        for source in ('<p>Blocked</p>',good_page([])):
            with self.subTest(source=source),patch('fagao.collectors._fetch',return_value=source):
                with self.assertRaises(CollectorError):collect_overseas('goodnovel',{},None)

    def test_unsupported_navigation_never_fetches(self):
        with patch('fagao.collectors._fetch') as fetch:
            with self.assertRaises(CollectorError):collect_overseas('goodnovel',{'category':'假分类'},None)
            fetch.assert_not_called()

    def test_callback_cancellation_is_not_swallowed(self):
        def progress(message,**kw):
            if kw:raise RuntimeError('cancelled by consumer')
        with patch('fagao.collectors._fetch',return_value=good_page([1])) as fetch:
            with self.assertRaisesRegex(RuntimeError,'cancelled'):collect_overseas('goodnovel',{},None,progress)
            self.assertEqual(fetch.call_count,1)


if __name__=='__main__':unittest.main()
