"""Source-shaped, synthetic fixtures for overseas ranking semantics."""
import unittest
from unittest.mock import patch
from urllib.request import Request

from fagao.collectors import CollectorError
from fagao.overseas_readers import (collect_readers, parse_royalroad,
                                    parse_scribblehub, parse_webnovel, _EnglishLanguage)


def webnovel(ident="123456", rank=1, more=True):
    return f'''<div class="j_rank_wrapper"><section class="df g_hr">
      <i class="ff_number">{rank:03d}</i><a href="/book/test_{ident}">
      <img data-original="//book-pic.webnovel.com/bookcover/{ident}"></a>
      <h3><a href="/book/test_{ident}">A &amp; B</a></h3>
      <p class="c_s">First line.\nSecond line.</p><p>
      <strong class="ff_number"><span>123</span></strong>
      <a href="/stories/novel-fantasy-male">Fantasy</a>
      <a href="/tags/system-novel"># SYSTEM</a>
      <strong class="c_l">English Author</strong></p></section></div>
      <div class="j_rank_loading {'_on' if more else ''}"></div>'''


ROYALROAD = '''<div class="fiction-list-item row"><img data-type="cover"
src="https://www.royalroadcdn.com/public/covers-large/42.webp"></img>
<h2 class="fiction-title"><a href="/fiction/42/example">Example &amp; Magic</a></h2>
<span class="label">ONGOING</span><a class="fiction-tag">LitRPG</a>
<div class="stats"><div><span>1,234 Followers</span></div>
<div aria-label="Rating: 4.56 out of 5"></div><div><span>3,000 Views</span></div>
<div><span>40 Chapters</span></div><div><span>300 Pages</span></div>
<div id="description-42"><p>English synopsis.</p><p>Keep this second paragraph.</p></div>
</div></div>'''


def scribble(ident="42", rank=1, page=1, total=2):
    return f'''<div class="search_main_box"><div class="search_img">
<img src="https://cdn.scribblehub.com/{ident}.jpg"><div class="search_ratings">(4.8)</div></div>
<div class="search_body"><div class="search_title"><span class="genre_rank">#{rank}</span>
<a href="https://www.scribblehub.com/series/{ident}/example/">The English Book</a></div>
<div class="search_stats"><span class="nl_stat">3.81M Views</span>
<span class="nl_stat">1.85M Words</span><span class="nl_stat">8 Chapters/Week</span>
<span class="nl_stat">172 Chapters</span><span class="a_un_st">Writer</span></div>
<div class="search_genre"><a class="fic_genre">Fantasy</a></div>
Opening sentence.<span class="dots">...</span><span class="morelink">more&gt;&gt;</span>
<span class="testhide"><p>The rest of the English synopsis.</p>
<span class="morelink">&lt;&lt;less</span></span></div></div>
<script>$('#pagination').pagination({{items: {total},itemsOnPage: 1,currentPage: '{page}'}});</script>'''


class ReaderParserTests(unittest.TestCase):
    def test_request_uses_english_catalogue_even_on_chinese_computer(self):
        request = Request('https://www.webnovel.com/ranking/novel/monthly/power_rank',
                          headers={'Accept-Language': 'zh-CN,zh;q=0.9'})
        _EnglishLanguage().http_request(request)
        self.assertEqual(request.get_header('Accept-language'), 'en-US,en;q=0.9')

    def test_webnovel_rank_identity_metric_and_original_text(self):
        books, more = parse_webnovel(webnovel())
        self.assertTrue(more)
        self.assertEqual(len(books), 1)
        book = books[0]
        self.assertEqual((book['id'], book['rank']), ('123456', 1))
        self.assertEqual(book['title'], 'A & B')
        self.assertEqual(book['summary'], 'First line.\nSecond line.')
        self.assertEqual(book['metrics'], [{'name': '能量石', 'value': '123', 'unit': '票'}])
        self.assertEqual(book['author'], 'English Author')
        self.assertEqual(book['tags'], ['奇幻', '系统'])
        self.assertEqual(book['source_tags'], ['Fantasy', 'SYSTEM'])
        self.assertTrue(book['cover_url'].startswith('https://'))

    def test_royalroad_list_position_is_not_fabricated_rank(self):
        books, more = parse_royalroad(ROYALROAD)
        self.assertFalse(more)
        book = books[0]
        self.assertIsNone(book['rank'])
        self.assertEqual(book['source_position'], 1)
        self.assertEqual(book['rank_semantics'], 'source_list_position')
        self.assertEqual(book['status'], '连载中')
        self.assertIn('Keep this second paragraph.', book['summary'])
        self.assertEqual(book['metrics'][0], {'name': '关注', 'value': '1,234', 'unit': '人'})
        self.assertEqual(book['metrics'][-1], {'name': '评分', 'value': '4.56', 'unit': '/5'})

    def test_royalroad_synopsis_numbers_do_not_become_metrics(self):
        books, _ = parse_royalroad(ROYALROAD.replace('<div><span>1,234 Followers</span></div>', '')
                                  .replace('English synopsis.', 'I have 9000 Followers in this story.'))
        self.assertNotIn('关注', [m['name'] for m in books[0]['metrics']])

    def test_scribble_rank_rounded_words_and_unfolded_english_summary(self):
        books, more = parse_scribblehub(scribble(rank=26, page=2))
        self.assertFalse(more)
        book = books[0]
        self.assertEqual(book['rank'], 26)
        self.assertEqual(book['title'], 'The English Book')
        self.assertIn('Opening sentence.', book['summary'])
        self.assertIn('The rest of the English synopsis.', book['summary'])
        self.assertNotIn('more>>', book['summary'])
        self.assertNotIn('Writer', book['summary'])
        self.assertIn({'name': '英文词数', 'value': '1.85M', 'unit': '词'}, book['metrics'])
        self.assertIn({'name': '周更新', 'value': '8', 'unit': '章/周'}, book['metrics'])

    def test_non_ranking_navigation_does_not_create_books(self):
        for parser in (parse_webnovel, parse_royalroad, parse_scribblehub):
            self.assertEqual(parser('<html><a href="/book/123">Sign in</a></html>')[0], [])

    def test_script_text_is_never_executed_or_included(self):
        books, _ = parse_webnovel(webnovel().replace('First line.', '<script>throw new Error("bad")</script>First line.'))
        self.assertEqual(books[0]['summary'], 'First line.\nSecond line.')


class ReaderCollectionTests(unittest.TestCase):
    @patch('fagao.overseas_readers._fetch')
    def test_first_page_published_before_second_request(self, fetch):
        fetch.side_effect = [webnovel(more=True), webnovel('987654', rank=21, more=False)]
        checkpoints = []
        def progress(message, **kwargs):
            if 'snapshot' in kwargs:
                checkpoints.append((fetch.call_count, len(kwargs['snapshot']['books']), kwargs['has_more']))
        result = collect_readers('webnovel', {'max_pages': 2}, progress=progress)
        self.assertEqual(checkpoints, [(1, 1, True), (2, 2, False)])
        self.assertEqual(result['pagination']['pages_fetched'], 2)
        self.assertEqual(result['channel'], '男频')
        self.assertEqual(result['period'], '周榜')
        self.assertIn('30 天', result['provenance']['scope'])
        self.assertIn('pageIndex=2', fetch.call_args[0][0])
        self.assertIn('timeType=3', fetch.call_args[0][0])

    @patch('fagao.overseas_readers._fetch')
    def test_scribble_paginates_with_pg_and_preserves_rank(self, fetch):
        fetch.side_effect = [scribble(), scribble('43', rank=26, page=2)]
        result = collect_readers('scribblehub', {'max_pages': 2})
        self.assertEqual([b['rank'] for b in result['books']], [1, 26])
        self.assertIn('&pg=2', fetch.call_args[0][0])
        self.assertTrue(result['complete'])

    @patch('fagao.overseas_readers._fetch')
    def test_later_failure_retains_available_books(self, fetch):
        fetch.side_effect = [webnovel(), CollectorError('HTTP 503', 'network_error')]
        result = collect_readers('webnovel', {'max_pages': 2})
        self.assertEqual(len(result['books']), 1)
        self.assertEqual(result['pagination']['stop_reason'], 'page_error')
        self.assertFalse(result['complete'])
        self.assertEqual(result['warnings'], ['HTTP 503'])

    @patch('fagao.overseas_readers._fetch', return_value='<html>Login</html>')
    def test_first_page_without_books_is_failure(self, fetch):
        with self.assertRaises(CollectorError):
            collect_readers('royalroad', {})

    @patch('fagao.overseas_readers._fetch')
    def test_rank_movement_stops_combination(self, fetch):
        fetch.side_effect = [webnovel(), webnovel('987654', rank=1)]
        result = collect_readers('webnovel', {'max_pages': 2})
        self.assertEqual(len(result['books']), 1)
        self.assertEqual(result['pagination']['stop_reason'], 'rank_conflict')

    @patch('fagao.overseas_readers._fetch')
    def test_same_id_with_changed_rank_stops_combination(self, fetch):
        fetch.side_effect = [webnovel(), webnovel(rank=2)]
        result = collect_readers('webnovel', {'max_pages': 2})
        self.assertEqual(result['pagination']['stop_reason'], 'rank_conflict')

    @patch('fagao.overseas_readers._fetch')
    def test_repeated_page_does_not_loop(self, fetch):
        fetch.return_value = webnovel()
        result = collect_readers('webnovel', {'max_pages': 100})
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(result['pagination']['stop_reason'], 'repeated_page')

    @patch('fagao.overseas_readers._fetch', return_value=ROYALROAD)
    def test_royalroad_single_public_list_and_unknown_date(self, fetch):
        result = collect_readers('royalroad', {'max_pages': 100})
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(result['pagination']['stop_reason'], 'source_end')
        self.assertIsNone(result['data_date'])
        self.assertTrue(result['complete'])

    def test_unsupported_filters_are_not_silently_ignored(self):
        for options in ({'board': '畅销榜'}, {'category': '狼人'}, {'period': '月榜'}):
            with self.assertRaises(CollectorError):
                collect_readers('webnovel', options)


if __name__ == '__main__':
    unittest.main()
