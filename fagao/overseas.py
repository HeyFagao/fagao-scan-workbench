# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Anonymous overseas catalogues, with source metadata and Chinese navigation.

Adapters are independently implemented from the public site's JSON/HTML. Book
names and synopses remain original English; no model or translation API is used.
"""
from __future__ import annotations

from html import unescape
import json
import math
import re
from urllib.parse import quote, urlsplit

GOOD_CATEGORIES = {'狼人':'Werewolf', '言情':'Romance', '总裁':'CEO', '婚恋':'Marriage',
                   '超自然':'Paranormal', '青春':'Youth', '奇幻':'Fantasy'}
MEGA_CATEGORIES = {'奇幻':'Fantasy', '都市':'Urban', '科幻':'Sci-Fi',
                   '系统与游戏':'System-Games', '其他':'Other'}
SHORT_CATEGORIES = {'全部':'', '言情':'Romance', '现实情感':'Emotional-Realism', '狼人':'Werewolf',
                    '黑手党':'Mafia', '双男主言情':'MM-Romance', '吸血鬼':'Vampire', '神话':'Mythology',
                    '奇幻':'Fantasy', '校园':'Campus', '脑洞':'Imagination', '重生':'Rebirth',
                    '魅惑':'Fascination', '悬疑惊悚':'Mystery-Thriller', '民俗悬疑':'Folklore-Mystery',
                    '男性视角':'Male-POV'}
DREAME_BOARDS = {'新书榜':6115, '上升榜':6114, '月票榜':6112, '畅销榜':6113, '完结榜':6116, '必读榜':6117}


def _platform(identifier, name, categories, boards, description):
    return {'id':identifier, 'name':name, 'mode':'overseas', 'status':'available',
            'language':'en', 'message':description, 'channels':['全部'],
            'categories':list(categories), 'boards':list(boards),
            'default_options':{'channel':'全部', 'category':next(iter(categories)), 'board':next(iter(boards))}}


OVERSEAS_PLATFORMS = [
    _platform('goodnovel', 'GoodNovel', GOOD_CATEGORIES, ['分类榜'], '英文长篇分类榜'),
    _platform('goodnovel_short', 'GoodNovel 短篇', SHORT_CATEGORIES, ['热门短篇'], '英文热门短篇书库'),
    _platform('dreame', 'Dreame', ['全部'], DREAME_BOARDS, '新书、上升与畅销等公开榜单'),
    _platform('meganovel', 'MegaNovel', MEGA_CATEGORIES, ['分类榜'], '奇幻、都市、系统与游戏分类榜'),
    _platform('webnovel', 'Webnovel', ['全部'], ['新书能量榜'], '发表不超过 30 天作品的能量榜'),
    _platform('royalroad', 'Royal Road', ['全部'], ['新星榜'], '公开新星榜与作品数据'),
    _platform('scribblehub', 'Scribble Hub', ['全部'], ['周收藏榜'], '本周收藏榜与作品数据'),
]

_webnovel = next(item for item in OVERSEAS_PLATFORMS if item['id']=='webnovel')
_webnovel['channels'] = ['男频','女频']
_webnovel['default_options'].update(channel='男频',period='周榜')
for _identifier, _period in (('royalroad','当前'),('scribblehub','周榜')):
    next(item for item in OVERSEAS_PLATFORMS if item['id']==_identifier)['default_options']['period'] = _period
for _item in OVERSEAS_PLATFORMS:
    if _item['id'] == 'royalroad':
        _item.update(source_url='https://www.royalroad.com/fictions/rising-stars',
                     alternate_source_url='https://rst.doomlabs.net/')
    elif _item['id'] == 'scribblehub':
        _item['source_url'] = 'https://www.scribblehub.com/series-ranking/?order=2&sort=2'

# A vocabulary, not an AI-generated synopsis. Unknown tags are not relabelled.
TAG_ZH = {
    'werewolf':'狼人','romance':'言情','ceo':'总裁','marriage':'婚恋','paranormal':'超自然',
    'youth':'青春','fantasy':'奇幻','urban':'都市','sci-fi':'科幻','science fiction':'科幻',
    'system & games':'系统与游戏','other':'其他','emotional realism':'现实情感','mafia':'黑手党',
    'mm romance':'双男主言情','vampire':'吸血鬼','mythology':'神话','campus':'校园',
    'imagination':'脑洞','rebirth':'重生','fascination':'魅惑','mystery/thriller':'悬疑惊悚',
    'folklore mystery':'民俗悬疑','male pov':'男性视角','apocalypse':'末世','system':'系统',
    'games':'游戏','history':'历史','historical':'历史','historical fiction':'历史小说',
    'eastern':'东方玄幻','war':'战争','ya/teen':'青春','lgbtq+':'多元情感','lgbt':'多元情感',
    'action':'动作','adventure':'冒险','adventurous':'冒险','comedy':'喜剧','drama':'剧情',
    'mystery':'悬疑','thriller':'惊悚','horror':'恐怖','psychological':'心理','supernatural':'超自然',
    'tragedy':'悲剧','slice of life':'日常','school life':'校园生活','sports':'体育',
    'isekai':'异世界','litrpg':'游戏数值流','gamelit':'游戏世界','progression':'成长升级',
    'progression fantasy':'升级奇幻','cultivation':'修炼','xianxia':'仙侠','xuanhuan':'玄幻',
    'wuxia':'武侠','magic':'魔法','reincarnation':'转生','reborn':'重生','transmigration':'穿越',
    'time travel':'时空穿越','time loop':'时间循环','time-loop':'时间循环','alternate history':'架空历史',
    'dungeon':'地下城','dungeon core':'地下城核心','dungeon-core':'地下城核心','survival':'生存',
    'weak to strong':'弱者逆袭','weak-to-strong':'弱者逆袭','overpowered lead':'强者主角',
    'strong lead':'强者主角','female lead':'女主视角','male lead':'男主视角','non-human lead':'非人主角',
    'anti-hero lead':'反英雄主角','antihero':'反英雄','villainous lead':'反派主角',
    'revenge':'复仇','betrayal':'背叛','second chance':'第二次机会','second chance romance':'破镜重圆',
    'girl power':'女强','strong female lead':'女强','bully':'霸凌','warrior':'战士','beta':'副首领',
    'alpha':'阿尔法','luna':'露娜','lycan':'狼族','forbidden love':'禁忌之恋','rejected mate':'被拒伴侣',
    'fated mate':'命定伴侣','fated mates':'命定伴侣','mate':'伴侣','mates':'伴侣',
    'billionaire':'亿万富豪','millionaire':'百万富翁','rich':'豪门','business':'商战','contract marriage':'契约婚姻',
    'arranged marriage':'包办婚姻','marriage of convenience':'契约婚姻','divorce':'离婚',
    'pregnancy':'怀孕','secret baby':'隐婚萌宝','babies':'萌宝','baby':'萌宝','single mother':'单亲妈妈',
    'enemies to lovers':'冤家恋人','friends to lovers':'朋友变恋人','slow burn':'慢热感情',
    'love triangle':'三角恋','obsession':'执念','possessive':'占有欲','redemption':'救赎',
    'hidden identity':'隐藏身份','secret identity':'隐藏身份','found family':'新生家庭',
    'family':'家庭','politics':'权谋','strategy':'谋略','kingdom building':'建国经营',
    'kingdom-building':'建国经营','ruling class':'权贵阶层','aristocracy':'贵族',
    'prince':'王子','princess':'公主','king':'国王','queen':'女王','royalty':'王室',
    'heir/heirness':'继承人','god':'神明','gods':'神明','immortal':'长生','demon':'恶魔','demons':'恶魔',
    'angel':'天使','angels':'天使','monster':'怪物','monsters':'怪物','zombies':'丧尸',
    'space':'太空','space opera':'太空歌剧','cyberpunk':'赛博朋克','steampunk':'蒸汽朋克',
    'post apocalyptic':'末日之后','post-apocalyptic':'末日之后','sci fi':'科幻',
    'school':'校园','academy':'学院','intelligent':'智谋','intelligent mc':'智谋主角',
    'harem':'多角恋','reverse harem':'多男主','gender bender':'性别转换','gender-bender':'性别转换',
    'super heroes':'超级英雄','superheroes':'超级英雄','fanfiction':'同人','fan fiction':'同人',
    'martial arts':'武侠','mecha':'机甲','military':'军事','dark fantasy':'黑暗奇幻',
    'contemporary':'现代','urban fantasy':'都市奇幻','high fantasy':'高魔奇幻',
    'portal fantasy / isekai':'异世界穿越','artificial intelligence':'人工智能','technologically advanced':'科技文明',
    'dungeon crawler':'地下城冒险','attractive lead':'魅力主角','crafting':'制造养成',
    'lesbian romance':'女同言情','girls love':'女同言情','boys love':'男同言情',
    'ecchi':'轻情色','smut':'情色','myth':'神话','anotherworld':'异世界','feelinggood':'轻松爽感',
}


def translated_tags(values):
    if isinstance(values, str):
        values = [values]
    result = []
    for value in values if isinstance(values, (list,tuple)) else []:
        raw = str(value or '').strip()
        translated = TAG_ZH.get(raw.casefold()) or (raw if re.search(r'[\u4e00-\u9fff]', raw) else '')
        if translated and translated not in result:
            result.append(translated)
    return result[:30]


def _integer(value):
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
        return number if number >= 0 and str(value).strip() == str(number) else None
    except (ValueError, TypeError):
        return None


def _metric(name, value, unit=''):
    if value is None or isinstance(value, bool) or isinstance(value, (dict,list)) or str(value).strip() == '':
        return None
    return {'name':name, 'value':value, 'unit':unit}


def _metrics(*items):
    return [item for item in items if item is not None]


def _safe_source_url(base, path):
    from .collectors import CollectorError
    if not isinstance(path,str) or not path.startswith('/') or path.startswith('//'):
        raise CollectorError('作品来源链接格式已变化。')
    result = base + path
    if urlsplit(result).netloc != urlsplit(base).netloc:
        raise CollectorError('作品来源链接域名不一致。')
    return result


def parse_goodnovel(html, platform='goodnovel', category='狼人'):
    """Parse a single ranking/library response, without inventing global ranks."""
    from .collectors import CollectorError, _state, _cover_url
    state = _state(html)
    if not isinstance(state,dict):
        raise CollectorError('海外书目数据格式已变化。')
    short = platform == 'goodnovel_short'
    data = state.get('Browse' if short else 'HomeDataModule')
    rows = data.get('bookList' if short else 'books') if isinstance(data,dict) else None
    if not isinstance(rows,list):
        raise CollectorError('平台没有返回所选书目列表。')
    total_pages = _integer(data.get('totalPage' if short else 'totals'))
    if total_pages is None or total_pages < 1:
        raise CollectorError('平台没有返回可核对的书目分页信息。')
    if short and data.get('popular') not in (None,'POPULAR'):
        raise CollectorError('平台返回的短篇排序与热门短篇条件不一致。')
    base = 'https://www.meganovel.com' if platform == 'meganovel' else 'https://www.goodnovel.com'
    books = []
    for row in rows:
        if not isinstance(row,dict):
            raise CollectorError('平台书目字段格式已变化。')
        book_id = str(row.get('bookId') or '')
        title = row.get('bookName')
        if not re.fullmatch(r'\d{1,30}', book_id) or not isinstance(title,str) or not title.strip():
            raise CollectorError('平台书目缺少稳定作品 ID 或书名。')
        resource = row.get('bookResourceUrl')
        # URL comes from the site's canonical resource slug, not a guessed title.
        if not isinstance(resource,str) or not resource.endswith('_'+book_id) or '/' in resource:
            raise CollectorError('平台作品链接与作品 ID 不一致。')
        source_tags = [value for key in ('genreNames','newTagsNames')
                       for value in (row.get(key) if isinstance(row.get(key),list) else []) if isinstance(value,str)]
        tags = translated_tags(source_tags)
        if category != '全部' and category not in tags:
            tags.insert(0,category)
        books.append({'id':book_id, 'title':title, 'author':str(row.get('pseudonym') or ''),
                      'summary':str(row.get('introduction') or ''), 'rank':None,
                      'rank_semantics':'source_list_position',
                      'url':_safe_source_url(base,'/book/'+quote(resource, safe='-_.~')),
                      'cover_url':_cover_url(row.get('cover')), 'tags':tags, 'source_tags':source_tags,
                      'status':{'COMPLETE':'已完结','CONTINUE':'连载中','SERIAL':'连载中','ONGOING':'连载中'}.get(row.get('writeStatus'),''),
                      'metrics':_metrics(_metric('阅读量',row.get('viewCountDisplay')),
                                         _metric('英文词数',row.get('totalWords'),'词'),
                                         _metric('章节',row.get('chapterCount'),'章'),
                                         _metric('评分',row.get('ratings'),'分'),
                                         _metric('评论',row.get('commentCount'),'条'))})
    return books, total_pages, _integer(data.get('total')) if short else None


def parse_dreame(html, page=1):
    """Dreame's totalpage contains record count, not number of pages."""
    from .collectors import CollectorError, _cover_url
    match = re.search(r'<script\b[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',html,re.S)
    if not match:
        raise CollectorError('Dreame 页面没有榜单数据。')
    try:
        data = json.loads(match[1])['props']['pageProps']['info']
        rows, pager = data['list'], data['pager']
        actual_page, page_size, total = (_integer(pager.get(key)) for key in ('pageno','pagesize','totalpage'))
        if actual_page != page or page_size is None or page_size < 1 or total is None or not isinstance(rows,list):
            raise ValueError('pagination')
    except (ValueError,KeyError,TypeError,AttributeError) as exc:
        raise CollectorError('Dreame 榜单与请求页码不一致或分页格式已变化。') from exc
    # Use the actual source story href; do not derive an English title slug.
    links = {}
    for href in re.findall(r'href=["\']([^"\']+)["\']',html):
        path = unescape(href)
        found = re.fullmatch(r'/story/(\d+)-[^/]+',path)
        if found:
            links.setdefault(found[1],path)
    books = []
    for position,row in enumerate(rows,1):
        if not isinstance(row,dict):
            raise CollectorError('Dreame 书目字段格式已变化。')
        book_id = str(row.get('id') or '')
        title = row.get('name')
        if not re.fullmatch(r'\d{1,30}',book_id) or not isinstance(title,str) or not title.strip() or book_id not in links:
            raise CollectorError('Dreame 书目缺少可核对的作品 ID、书名或来源链接。')
        books.append({'id':book_id, 'title':title, 'author':str(row.get('author_name') or ''),
                      'summary':str(row.get('descr') or ''), 'rank':(page-1)*page_size+position,
                      'rank_semantics':'original_rank','source_position':(page-1)*page_size+position,
                      'url':_safe_source_url('https://www.dreame.com',links[book_id]),
                      'cover_url':_cover_url(row.get('cover_url')), 'tags':[], 'status':'',
                      'metrics':_metrics(_metric('阅读量',row.get('read_num')),
                                         _metric('关注',row.get('follow_num')))})
    return books, max(1,math.ceil(total/page_size)), total


def _validate(platform, options):
    from .collectors import CollectorError
    spec = next((item for item in OVERSEAS_PLATFORMS if item['id']==platform),None)
    if not spec:
        raise CollectorError('未接入这个海外平台。')
    effective = {**spec['default_options'], **options}
    for key, choices in (('channel',spec['channels']),('category',spec['categories']),('board',spec['boards'])):
        if effective.get(key) not in choices:
            raise CollectorError('请选择当前海外平台支持的'+{'channel':'频道','category':'分类','board':'榜单'}[key]+'。')
    return effective, spec


def collect_overseas(platform, options, data_dir, progress=None):
    from .collectors import CollectorError, _fetch, _notify, _notify_snapshot, _page_options, _page_pause, _result
    options, spec = _validate(platform,options)
    if platform in ('webnovel','royalroad','scribblehub'):
        from .overseas_readers import collect_readers
        return collect_readers(platform,options,data_dir,progress)
    category, board = options['category'],options['board']
    if platform == 'dreame':
        source = 'https://www.dreame.com/ranking/'+str(DREAME_BOARDS[board])+'.html'
        parse = lambda html,page:parse_dreame(html,page)
        page_key, page_size = 'page',10
    else:
        short = platform == 'goodnovel_short'
        mega = platform == 'meganovel'
        base = 'https://www.meganovel.com' if mega else 'https://www.goodnovel.com'
        if short:
            slug = SHORT_CATEGORIES[category]
            source = base+'/stories/'+(slug+'-' if slug else '')+'short-novels'
        else:
            source = (base+'/rankings' if platform=='goodnovel' and category=='狼人'
                      else base+'/rankings/'+(MEGA_CATEGORIES if mega else GOOD_CATEGORIES)[category])
        parse = lambda html,page:parse_goodnovel(html,platform,category)
        page_key, page_size = ('pageIndex' if mega else 'page'),(20 if short else 10)
    max_pages, limit = _page_options(options,page_size)
    books, seen, warnings = [],set(),[]
    pages_fetched, source_total, stop_reason = 0,None,'max_pages'
    def snapshot(reason, complete=False):
        return _result(platform,'overseas','全部',category,board,source,list(books),
                       categories=spec['categories'],warnings=list(warnings),complete=complete,
                       requested_count=limit,available_count=len(books),source_total=source_total,
                       pagination={'pages_fetched':pages_fetched,'max_pages':max_pages,'stop_reason':reason,'source_total':source_total},
                       provenance={'source_id':'direct','source_name':spec['name'],'source_url':source,
                                   'scope':category+' / '+board,
                                   'rank_semantics':'original_rank' if platform=='dreame' else 'source_list_position',
                                   'metric_unit':'来源英文词数按词计；阅读量保留来源显示精度'})
    for page in range(1,max_pages+1):
        _notify(progress,'正在读取 '+spec['name']+' 第 '+str(page)+' 页…')
        url = source + ('?'+page_key+'='+str(page) if page>1 else '')
        try:
            rows, total_pages, page_total = parse(_fetch(url,referer=source),page)
        except CollectorError as exc:
            if not books:
                raise
            warnings.append('后续页面读取停止：'+str(exc))
            stop_reason = 'request_failed'
            break
        pages_fetched += 1
        if page_total is not None:
            source_total = page_total
        if not rows:
            stop_reason = 'empty_page'
            break
        fresh = []
        conflict = False
        for row in rows:
            if row['id'] in seen:
                conflict = True
                continue
            seen.add(row['id'])
            row['source_position'] = (page-1)*page_size+len(fresh)+1 if platform!='dreame' else row['source_position']
            fresh.append(row)
        if conflict:
            warnings.append('来源分页出现重复作品，已停止合并，保留此前有效书目。')
            stop_reason = 'repeated_page'
            break
        if not fresh:
            stop_reason = 'repeated_page'
            break
        books.extend(fresh[:max(0,limit-len(books))])
        ended = page >= total_pages
        if len(books)>=limit:
            stop_reason = 'requested_limit' if options.get('limit') else ('source_end' if ended else 'max_pages')
        elif ended:
            stop_reason = 'source_end'
        else:
            stop_reason = 'max_pages' if page>=max_pages else 'loading'
        has_more = not ended and len(books)<limit and page<max_pages
        _notify_snapshot(progress,snapshot(stop_reason,stop_reason in ('requested_limit','source_end')),has_more)
        if not has_more:
            break
        _page_pause(progress,'继续读取 '+spec['name']+' 后续书目…')
    if not books:
        raise CollectorError('当前海外榜单没有返回有效书目，已有记录保留。')
    if stop_reason == 'max_pages':
        warnings.append('已读取 '+str(pages_fetched)+' 页；达到本次页数上限。')
    return snapshot(stop_reason,stop_reason in ('requested_limit','source_end','empty_page'))
