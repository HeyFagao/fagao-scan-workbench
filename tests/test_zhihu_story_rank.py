# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
"""Story metadata regressions based on the public billboard's observed structure."""
import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from fagao import zhihu_story_rank as zh
from fagao.collectors import CollectorError


def story(position=1, long=False):
    well, section = str(1800000000000000000 + position), str(2100000000000000000 + position)
    return {'business_id': well if long else section, 'business_type': 'PaidColumn',
            'title': '长篇样本' if long else '短篇样本' + str(position),
            'url': ('https://www.zhihu.com/xen/market/remix/paid_column/' + well if long
                    else 'https://www.zhihu.com/market/paid_column/' + well + '/section/' + section),
            'space_type': 'LongSpace' if long else 'ShortSpace',
            'artwork': 'https://picx.zhimg.com/sample_200x0.jpg?source=example',
            'description': '<p>来源提供的简介。</p>', 'subtitle': '970.2 万热度',
            'label_text': '言情 · 学霸'}


def feed(board='热度榜', category='全部', rows=None):
    token, key = zh.BOARDS[board], zh.CATEGORY_FILTERS[category]
    link = 'https://story.zhihu.com/vip-ranking?channel=female&tab=' + token
    if key != '0':
        link += '&filter_key=' + key
    head = {'title': board, 'type': token, 'button_url': link,
            'filters': [{'key': zh.CATEGORY_FILTERS[x], 'name': x} for x in zh.CATEGORIES_BY_BOARD[board]]}
    return {'data': [{'module_type': 'billboard', 'module_data': {'data': {'data': [
        {'head': head, 'content_list': rows if rows is not None else [story(1), story(2, long=True), story(3)]}]}}}],
        'paging': {'total': 20000, 'limit': 10, 'offset': 0, 'is_end': False,
                   'next': 'https://evil.invalid/VIP-text?offset=10'}}


def board_row(payload):
    return payload['data'][0]['module_data']['data']['data'][0]


class Response(io.BytesIO):
    def __init__(self, data, url, headers=None):
        super().__init__(json.dumps(data).encode())
        self.status, self.url, self.headers = 200, url, headers or {}

    def geturl(self):
        return self.url


class ZhihuStoryParsingTests(unittest.TestCase):
    def test_short_identity_cover_description_and_source_positions_survive(self):
        snapshot = zh.parse_result(feed(), '热度榜', '全部')
        self.assertEqual([b['rank'] for b in snapshot['books']], [1, 3])
        self.assertEqual([b['source_position'] for b in snapshot['books']], [1, 3])
        book = snapshot['books'][0]
        self.assertEqual(book['id'], '1800000000000000001/2100000000000000001')
        self.assertEqual(book['title'], '短篇样本1')
        self.assertEqual(book['author'], '')
        self.assertEqual(book['summary'], '来源提供的简介。')
        self.assertTrue(book['cover_url'].startswith('https://picx.zhimg.com/'))
        self.assertEqual(book['metrics'], [{'name': '热度', 'value': '970.2', 'unit': '万'}])
        self.assertEqual(book['rank_semantics'], 'source_list_position')
        self.assertEqual(snapshot['source_rows_seen'], 3)
        self.assertEqual(snapshot['available_count'], 2)
        self.assertIsNone(snapshot['data_date'])

    def test_feed_pagination_does_not_become_story_total_or_continuation(self):
        snapshot = zh.parse_result(feed(), '热度榜', '全部')
        self.assertIsNone(snapshot['source_total'])
        self.assertFalse(snapshot['complete'])
        self.assertEqual(snapshot['pagination'], {'pages_fetched': 1, 'max_pages': 1,
                                                 'source_total': None, 'stop_reason': 'source_first_batch'})
        self.assertNotIn('evil.invalid', json.dumps(snapshot))

    def test_board_and_filter_are_bound_to_the_source_header(self):
        for board, categories in zh.CATEGORIES_BY_BOARD.items():
            for category in categories:
                with self.subTest(board=board, category=category):
                    data = feed(board, category, [story()])
                    snapshot = zh.parse_result(data, board, category)
                    self.assertEqual((snapshot['board'], snapshot['category'], snapshot['channel']),
                                     (board, category, '女频'))
                    board_row(data)['head']['button_url'] = 'https://story.zhihu.com/vip-ranking?channel=male&tab=hot'
                    with self.assertRaises(CollectorError):
                        zh.parse_result(data, board, category)

    def test_cannot_silently_use_another_filter_or_board(self):
        data = feed()
        with self.assertRaises(CollectorError):
            zh.parse_result(data, '热度榜', '悬疑')
        with self.assertRaises(CollectorError):
            zh.parse_result(data, '口碑榜', '全部')
        board_row(data)['head']['filters'] = [{'key': '0', 'name': '不相符'}]
        with self.assertRaises(CollectorError):
            zh.parse_result(data, '热度榜', '全部')

    def test_likes_keep_the_source_unit_and_unknown_metrics_stay_missing(self):
        row = story()
        row['subtitle'] = '2.7 万赞'
        self.assertEqual(zh.parse_result(feed(rows=[row]), '热度榜', '全部')['books'][0]['metrics'],
                         [{'name': '点赞', 'value': '2.7', 'unit': '万'}])
        row['subtitle'] = '受欢迎'
        self.assertEqual(zh.parse_result(feed(rows=[row]), '热度榜', '全部')['books'][0]['metrics'], [])

    def test_unverified_identity_urls_and_duplicate_records_are_rejected(self):
        for value in ('https://evil.invalid/market/paid_column/1/section/2',
                      'https://www.zhihu.com/market/paid_column/1/section/2?token=private',
                      'https://www.zhihu.com/question/123', '//www.zhihu.com/market/paid_column/1/section/2'):
            row = story()
            row['url'] = value
            with self.subTest(url=value), self.assertRaises(CollectorError):
                zh.parse_result(feed(rows=[row]), '热度榜', '全部')
        for changes in ({'business_id': '999'}, {'business_type': 'Question'}, {'title': ''}, {'space_type': 'Unknown'}):
            row = {**story(), **changes}
            with self.subTest(changes=changes), self.assertRaises(CollectorError):
                zh.parse_result(feed(rows=[row]), '热度榜', '全部')
        with self.assertRaises(CollectorError):
            zh.parse_result(feed(rows=[story(), story()]), '热度榜', '全部')

    def test_image_sources_are_restricted_and_do_not_trigger_downloads(self):
        for value in ('http://picx.zhimg.com/a.jpg', 'https://zhimg.com.evil.invalid/a.jpg',
                      'https://user:password@picx.zhimg.com/a.jpg', 'https://127.0.0.1/a.jpg',
                      'https://picx.zhimg.com:444/a.jpg', 'https://picx.zhimg.com/a.jpg\n'):
            row = {**story(), 'artwork': value}
            with self.subTest(value=value):
                self.assertEqual(zh.parse_result(feed(rows=[row]), '热度榜', '全部')['books'][0]['cover_url'], '')

    def test_empty_or_long_only_is_distinct_from_malformed_source(self):
        for rows in ([], [story(long=True)]):
            with self.assertRaises(CollectorError) as caught:
                zh.parse_result(feed(rows=rows), '热度榜', '全部')
            self.assertEqual(caught.exception.state, 'empty_source')
        for data in (None, {'data': None}, {'data': []}, {'data': [{'module_type': 'billboard', 'module_data': None}]}):
            with self.assertRaises(CollectorError) as caught:
                zh.parse_result(data, '热度榜', '全部')
            self.assertEqual(caught.exception.state, 'source_changed')

    def test_requested_count_is_bounded_without_renumbering(self):
        data = feed(rows=[story(i) for i in range(1, 105)])
        snapshot = zh.parse_result(data, '热度榜', '全部', 1000)
        self.assertEqual(len(snapshot['books']), 100)
        self.assertEqual(snapshot['requested_count'], 100)
        self.assertTrue(snapshot['complete'])
        small = zh.parse_result(feed(), '热度榜', '全部', 1)
        self.assertEqual(len(small['books']), 1)
        self.assertEqual(small['pagination']['stop_reason'], 'requested_limit')


class ZhihuStoryCollectionTests(unittest.TestCase):
    def test_collection_requests_only_one_fixed_anonymous_billboard(self):
        url = zh.source_url('热度榜', '全部')
        with patch.object(zh.network, 'build_opener') as build:
            build.return_value.open.return_value = Response(feed(), url)
            snapshot = zh.collect({'limit': 100, 'max_pages': 100})
            call = build.return_value.open
            call.assert_called_once()
            req = call.call_args.args[0]
            self.assertEqual(req.full_url, url)
            self.assertNotIn('Cookie', req.headers)
            self.assertNotIn('Authorization', req.headers)
            self.assertEqual(parse_qs(urlsplit(req.full_url).query)['offset'], ['0'])
            self.assertEqual(snapshot['available_count'], 2)

    def test_options_are_rejected_before_network_activity(self):
        invalid = [None, {'board': {}}, {'category': []}, {'channel': '男频'}, {'board': '长篇榜'}, {'board': '推荐榜', 'category': '校园'},
                   {'category': '3'}, {'limit': 0}, {'limit': True}, {'limit': '100'}]
        with patch.object(zh, '_fetch') as fetch:
            for options in invalid:
                with self.subTest(options=options), self.assertRaises(CollectorError):
                    zh.collect(options)
            fetch.assert_not_called()

    def test_membership_is_checked_before_and_during_transport_and_before_publication(self):
        def expired():
            raise RuntimeError('membership_expired')
        with zh.network.authorized_requests(expired), patch.object(zh.network, 'build_opener') as build:
            with self.assertRaisesRegex(RuntimeError, 'membership_expired'):
                zh.collect({})
            build.assert_not_called()
        events = []
        with patch.object(zh, '_fetch', return_value=feed()), patch.object(zh.network, 'check_authorization', side_effect=expired):
            with self.assertRaisesRegex(RuntimeError, 'membership_expired'):
                zh.collect({}, events.append)
        self.assertEqual(events, [])
        checks = []
        def expire_while_reading():
            checks.append(True)
            if len(checks) == 3:
                raise RuntimeError('membership_expired')
        with patch.object(zh.network, 'check_authorization', side_effect=expire_while_reading), \
                patch.object(zh.network, 'build_opener') as build:
            build.return_value.open.return_value = Response(feed(), zh.source_url('热度榜', '全部'))
            with self.assertRaisesRegex(RuntimeError, 'membership_expired'):
                zh.collect({})
        self.assertEqual(len(checks), 3)

    def test_http_permissions_rate_limits_and_network_errors_remain_distinct(self):
        url = zh.source_url('热度榜', '全部')
        for status, state in ((401, 'forbidden'), (403, 'forbidden'), (429, 'rate_limited'),
                              (404, 'not_found'), (503, 'network_error')):
            with self.subTest(status=status), patch.object(zh.network, 'build_opener') as build:
                build.return_value.open.side_effect = HTTPError(url, status, 'test', {}, io.BytesIO())
                with self.assertRaises(CollectorError) as caught:
                    zh.collect({})
                self.assertEqual(caught.exception.state, state)
                build.return_value.open.assert_called_once()
        with patch.object(zh.network, 'build_opener') as build:
            build.return_value.open.side_effect = URLError('offline')
            with self.assertRaises(CollectorError) as caught:
                zh.collect({})
            self.assertEqual(caught.exception.state, 'network_error')

    def test_application_errors_do_not_turn_into_empty_boards(self):
        for code, state in ((401, 'forbidden'), (403, 'forbidden'), (429, 'rate_limited'), (500, 'source_changed')):
            with self.subTest(code=code), self.assertRaises(CollectorError) as caught:
                zh.parse_result({'error': {'code': code}}, '热度榜', '全部')
            self.assertEqual(caught.exception.state, state)

    def test_redirects_and_oversized_response_stop_without_following_another_url(self):
        url = zh.source_url('热度榜', '全部')
        for response in (Response(feed(), 'https://evil.invalid/'),
                         Response(feed(), url, {'Content-Length': str(zh.MAX_BYTES + 1)})):
            with patch.object(zh.network, 'build_opener') as build:
                build.return_value.open.return_value = response
                with self.assertRaises(CollectorError) as caught:
                    zh.collect({})
                self.assertEqual(caught.exception.state, 'source_changed')
        with self.assertRaises(CollectorError):
            zh._NoRedirect().redirect_request(None, io.BytesIO(), 302, '', {}, 'https://evil.invalid/')


if __name__ == '__main__':
    unittest.main()
