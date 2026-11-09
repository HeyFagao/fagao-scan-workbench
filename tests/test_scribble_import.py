"""Import only source-identifiable saved weekly Favorites ranking pages."""
import unittest
from unittest.mock import patch

from fagao.collectors import CollectorError
from fagao.scribble_import import parse_saved_ranking


def saved_page(page=1, count=2):
    rows = []
    for index in range(count):
        rank = (page - 1) * 25 + index + 1
        rows.append(f'''<div class="search_main_box"><img src="https://cdn.scribblehub.com/{rank}.jpg">
<div class="search_body"><div class="search_title"><span class="genre_rank">#{rank}</span>
<a href="https://www.scribblehub.com/series/{rank}/example/">English Book {rank}</a></div>
<div class="search_stats"><span class="nl_stat">1.85M Words</span><span class="a_un_st">Writer</span></div>
<div class="search_genre"><a class="fic_genre">Fantasy</a></div>Original English synopsis.</div></div>''')
    return f'''<!doctype html><html><head><link rel="canonical" href="https://www.scribblehub.com/series-ranking/"></head><body>
<a class="linkactive" href="/series-ranking/?sort=2&amp;order=2">Favorites</a>
<a class="linkactive" href="/series-ranking/?sort=2&amp;order=2">Weekly</a>
<span class="sfc_genre" value="0"></span>{''.join(rows)}
<script>$('#pagination').pagination({{items: 3, itemsOnPage: 1, currentPage: '{page}'}});</script>
</body></html>'''


class ScribbleImportTests(unittest.TestCase):
    def test_real_shape_preserves_originals_and_explicit_partial_provenance(self):
        result = parse_saved_ranking(saved_page())
        self.assertEqual([book['rank'] for book in result['books']], [1, 2])
        self.assertEqual(result['books'][0]['title'], 'English Book 1')
        self.assertEqual(result['books'][0]['summary'], 'Original English synopsis.')
        self.assertEqual(result['books'][0]['tags'], ['奇幻'])
        self.assertEqual(result['books'][0]['source_tags'], ['Fantasy'])
        self.assertEqual(result['source_id'], 'browser-import')
        self.assertIsNone(result['data_date'])
        self.assertFalse(result['complete'])
        self.assertNotIn('collected_at', result)
        self.assertEqual(result['pagination']['pages_fetched'], 0)
        self.assertEqual(result['pagination']['pages_imported'], 1)
        self.assertNotIn('<html', str(result))

    def test_second_page_keeps_original_ranks_without_synthetic_prefix(self):
        result = parse_saved_ranking(saved_page(2), 'https://www.scribblehub.com/series-ranking/?order=2&sort=2&pg=2')
        self.assertEqual([book['rank'] for book in result['books']], [26, 27])
        self.assertEqual(result['pagination']['source_page'], 2)

    def test_optional_url_must_match_but_cannot_replace_missing_canonical(self):
        for html, url in ((saved_page(2), 'https://www.scribblehub.com/series-ranking/?order=2&sort=2&pg=1'),
                          (saved_page().replace('rel="canonical"', 'rel="alternate"'), 'https://www.scribblehub.com/series-ranking/?order=2&sort=2')):
            with self.subTest(url=url), self.assertRaises(CollectorError):
                parse_saved_ranking(html, url)

    def test_rejects_wrong_platform_canonical_or_ambiguous_scope(self):
        for html in (saved_page().replace('href="https://www.scribblehub.com/series-ranking/"', 'href="https://example.org/series-ranking/"'),
                     saved_page().replace('Weekly', 'Monthly'),
                     saved_page().replace('sort=2&amp;order=2', 'sort=2&amp;order=3'),
                     saved_page().replace('<span class="sfc_genre" value="0">', '<span class="sfc_genre" value="1">'),
                     saved_page().replace('class="linkactive"', 'class="notactive"')):
            with self.subTest(html=html[:120]), self.assertRaises(CollectorError):
                parse_saved_ranking(html)

    def test_rejects_unrelated_filters_credentials_and_duplicate_query(self):
        for query in ('order=2&sort=2&sort=3', 'order=2&sort=2&status=1', 'order=2&sort=2&pg=0'):
            with self.subTest(query=query), self.assertRaises(CollectorError):
                parse_saved_ranking(saved_page(), 'https://www.scribblehub.com/series-ranking/?' + query)
        with self.assertRaises(CollectorError):
            parse_saved_ranking(saved_page(), 'https://user:pass@www.scribblehub.com/series-ranking/?order=2&sort=2')

    def test_rejects_validation_page_and_bad_pagination_without_network(self):
        cases = ['<html>Just a moment...</html>', saved_page().replace('items: 3', 'items: 0'),
                 saved_page().replace('pagination(', 'no_pagination('),
                 saved_page(2).replace("currentPage: '2'", "currentPage: '1'"),
                 saved_page().replace('genre_rank">#2', 'genre_rank">#4'), saved_page(count=26)]
        with patch('fagao.overseas_readers._fetch', side_effect=AssertionError('must not fetch')):
            for html in cases:
                with self.subTest(html=html[:120]), self.assertRaises(CollectorError):
                    parse_saved_ranking(html)

    def test_no_script_or_signed_in_navigation_leaks_into_snapshot(self):
        html = saved_page().replace('<body>', '<body><nav>Private user name</nav><script>dangerousSecret()</script>')
        result = parse_saved_ranking(html)
        self.assertNotIn('Private user', str(result))
        self.assertNotIn('dangerousSecret', str(result))

    def test_missing_local_cover_warns_without_fabricating_remote_url(self):
        result = parse_saved_ranking(saved_page().replace('https://cdn.scribblehub.com/1.jpg', './saved_files/1.jpg'))
        self.assertFalse(result['books'][0]['cover_url'])
        self.assertEqual(len(result['warnings']), 2)

    def test_size_bound_prevents_unbounded_import(self):
        with patch('fagao.scribble_import.MAX_HTML_BYTES', 10), self.assertRaises(CollectorError):
            parse_saved_ranking(saved_page())

    def test_rejects_invalid_encoding_and_duplicate_identity(self):
        for html in (saved_page() + '\ud800', saved_page().replace('/series/2/', '/series/1/')):
            with self.subTest(html=repr(html[:100])), self.assertRaises(CollectorError):
                parse_saved_ranking(html)


if __name__ == '__main__':
    unittest.main()
