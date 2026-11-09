"""Transactional local records. Snapshots and analysis targets are immutable."""
from __future__ import annotations
import hashlib
import ipaddress
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

PLATFORMS = [
    {'id':'fanqie','name':'番茄小说','mode':'long','status':'available','message':'公开榜单；以实际采集结果为准'},
    {'id':'qidian','name':'起点中文网','mode':'long','status':'available','message':'公开移动榜单；以实际采集结果为准'},
    {'id':'ciweimao','name':'刺猬猫','mode':'long','status':'available','message':'公开榜单分页采集；实际覆盖以采集结果为准'},
    {'id':'heiyan','name':'黑岩故事会','mode':'short','status':'external','message':'打开腾讯文档书单，在原站完成登录','source_url':'https://docs.qq.com/sheet/DYVNwaVJZV2ZxTWl3?tab=q1ans7'},
    {'id':'fanqie_short','name':'番茄短故事','mode':'short','status':'unavailable','message':'热门故事入口待实机验证；可导入来源明确的书目'},
]

class AppError(Exception):
    def __init__(self, code, message, status=400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)

def now():
    return datetime.now(timezone.utc).isoformat()

def default_data_dir():
    if os.environ.get('FAGAO_DATA_DIR'):
        return Path(os.environ['FAGAO_DATA_DIR']).expanduser().resolve()
    if os.name == 'nt':
        return (Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData'/'Local'))) / 'FagaoWorkbench').resolve()
    return (Path.home() / '.fagao-workbench').resolve()

def dumps(value):
    return json.dumps(value, ensure_ascii=False)

def safe_url(value):
    value = str(value or '').strip()
    return value if urlparse(value).scheme in ('http','https') and urlparse(value).hostname else ''

def safe_cover_url(value):
    """Accept public HTTP(S) image locations without fetching or proxying them."""
    if not isinstance(value,str) or len(value)>4096: return ''
    value=value.strip()
    if not value or re.search(r'[\s\\\x00-\x1f\x7f]',value): return ''
    try:
        parsed=urlparse(value)
        host=(parsed.hostname or '').rstrip('.').lower()
        if parsed.scheme not in ('http','https') or not host or parsed.username is not None or parsed.password is not None: return ''
        if parsed.port is not None and not 1<=parsed.port<=65535: return ''
        if '%' in host or host=='localhost' or host.endswith(('.localhost','.local','.localdomain','.internal','.lan','.home')): return ''
        try:
            address=ipaddress.ip_address(host)
        except ValueError:
            # Single-label, numeric, hexadecimal and octal-style local host aliases
            # are not public CDN names. Do not resolve DNS from this process.
            if '.' not in host or re.fullmatch(r'[0-9.]+',host) or re.fullmatch(r'(?:[0-9]+|0x[0-9a-f]+)',host.rsplit('.',1)[-1]): return ''
            ascii_host=host.encode('idna').decode('ascii')
            if any(not part or len(part)>63 or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?',part) for part in ascii_host.split('.')): return ''
        else:
            if not address.is_global: return ''
    except (ValueError,UnicodeError): return ''
    return value


def clean_pagination(value):
    if value is None: return None
    if not isinstance(value,dict): raise AppError('invalid_pagination','分页采集信息必须是对象。')
    result={}
    for key in ('pages_fetched','max_pages','source_total'):
        item=value.get(key)
        if item is None:
            if key=='source_total': result[key]=None
            continue
        if isinstance(item,bool) or not isinstance(item,int) or item<0:
            raise AppError('invalid_pagination','分页数量必须是非负整数。')
        result[key]=item
    if 'stop_reason' in value: result['stop_reason']=str(value.get('stop_reason') or '')[:300]
    return result

def text(value, maximum=20000):
    return str(value or '')[:maximum]

class Store:
    def __init__(self, directory=None):
        self.directory = Path(directory or default_data_dir()).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        try: self.directory.chmod(0o700)
        except OSError: pass
        self.db = self.directory / 'workbench.sqlite3'
        with self.connect() as c:
            c.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY, created_at TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS snapshot_metadata(id TEXT PRIMARY KEY, created_at TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS favorites(platform TEXT, book_id TEXT, snapshot_id TEXT, created_at TEXT, PRIMARY KEY(platform,book_id));
                CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_id TEXT, book_id TEXT, selected_at TEXT);
                CREATE TABLE IF NOT EXISTS texts(id TEXT PRIMARY KEY, snapshot_id TEXT, book_id TEXT, created_at TEXT, body TEXT, content TEXT);
                CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, created_at TEXT, body TEXT);
                CREATE TABLE IF NOT EXISTS reports(id TEXT PRIMARY KEY, created_at TEXT, body TEXT, content TEXT);
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, created_at TEXT, body TEXT);
            ''')
            c.execute('INSERT OR IGNORE INTO kv VALUES (?,?)',('config',dumps({'main_site':'https://www.fagaochuangzuo.com/','points_site':''})))
            c.execute('INSERT OR IGNORE INTO kv VALUES (?,?)',('active_mode',dumps('long')))
        try: self.db.chmod(0o600)
        except OSError: pass

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute('BEGIN IMMEDIATE')
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def _kv(self, c, key, default=None):
        row = c.execute('SELECT body FROM kv WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _set(self,c,key,value):
        c.execute('INSERT INTO kv VALUES (?,?) ON CONFLICT(key) DO UPDATE SET body=excluded.body',(key,dumps(value)))

    def _snapshot(self,c,sid):
        row=c.execute('SELECT body FROM snapshots WHERE id=?',(str(sid),)).fetchone()
        if not row: raise AppError('snapshot_missing','找不到这份榜单记录。',404)
        return json.loads(row[0])

    def snapshot(self,sid):
        with self.connect() as c: return self._snapshot(c,sid)

    def _book(self,c,sid,bid):
        s=self._snapshot(c,sid)
        b=next((b for b in s['books'] if b['id']==str(bid)),None)
        if b is None: raise AppError('book_missing','这本作品不在指定榜单记录中。',404)
        return s,b

    def book(self,sid,bid):
        """Resolve an explicitly pinned source book without changing selection."""
        with self.connect() as c: return self._book(c,sid,bid)

    def save_snapshot(self, data, imported=False):
        if not isinstance(data,dict): raise AppError('invalid_snapshot','书目文件应为一个 JSON 对象。')
        platform=data.get('platform')
        p=next((p for p in PLATFORMS if p['id']==platform),None)
        if not p: raise AppError('invalid_platform','不支持的作品平台。')
        books=data.get('books')
        if not isinstance(books,list) or not books: raise AppError('empty_snapshot','没有有效书目，保留上次结果。')
        if len(books)>10000: raise AppError('too_many_books','单份书目最多导入 10000 条。')
        valid=[]; ids=set(); ranks=set()
        for b in books:
            if not isinstance(b,dict) or isinstance(b.get('id'),bool) or not isinstance(b.get('id'),(str,int)) or not str(b['id']).strip() or not isinstance(b.get('title'),str) or not b['title'].strip():
                raise AppError('invalid_book','每条书目必须包含稳定作品 ID 和书名。')
            bid=str(b['id']).strip()
            if len(bid)>250 or bid in ids: raise AppError('duplicate_id','书目 ID 重复或过长。')
            ids.add(bid)
            rank=b.get('rank')
            if rank is not None and (isinstance(rank,bool) or not isinstance(rank,int) or rank<1):
                raise AppError('invalid_rank','原榜名次应为正整数；书库书目应为空。')
            if platform=='heiyan': rank=None
            if rank is not None and rank in ranks: raise AppError('duplicate_rank','同一分类榜不能包含重复名次，请拆分不同分类。')
            if rank: ranks.add(rank)
            tags=b.get('tags',[])
            if isinstance(tags,str): tags=[t for t in re.split(r'[,，·|]',tags) if t.strip()]
            if not isinstance(tags,list): tags=[]
            metrics=b.get('metrics',[])
            if not isinstance(metrics,list): metrics=[]
            valid.append({'id':bid,'title':text(b['title'],500),'author':text(b.get('author'),500),'status':text(b.get('status'),60) if isinstance(b.get('status'),str) else '','summary':text(b.get('summary')),'rank':rank,'url':safe_url(b.get('url')),'cover_url':safe_cover_url(b.get('cover_url')),'tags':[text(t,100) for t in tags[:30]],'metrics':[{'name':text(m.get('name'),100),'value':text(m.get('value'),100),'unit':text(m.get('unit'),30)} for m in metrics[:10] if isinstance(m,dict)]})
        warnings=data.get('warnings') or []
        if not isinstance(warnings,list): raise AppError('invalid_warnings','warnings 必须是字符串数组。')
        collected=data.get('collected_at')
        if collected is not None:
            try: datetime.fromisoformat(collected.replace('Z','+00:00'))
            except (ValueError,TypeError,AttributeError): raise AppError('invalid_date','采集时间必须是 ISO 日期时间。')
        timestamp=now()
        snap={'id':uuid.uuid4().hex,'platform':platform,'mode':p['mode'],'channel':text(data.get('channel'),100),'category':text(data.get('category'),100),'board':text(data.get('board') or ('书库' if platform=='heiyan' else '未注明榜型'),100),'source_url':safe_url(data.get('source_url')),'data_date':text(data.get('data_date'),100) or None,'collected_at':data.get('collected_at') or timestamp,'created_at':timestamp,'imported':imported,'books':valid,'books_count':len(valid),'warnings':[text(x,1000) for x in warnings[:20]]}
        for key in ('requested_count','available_count','source_total'):
            if key in data and isinstance(data[key],int) and not isinstance(data[key],bool) and data[key]>=0: snap[key]=data[key]
        pagination=clean_pagination(data.get('pagination'))
        if pagination is not None: snap['pagination']=pagination
        snap['complete']=data.get('complete') if isinstance(data.get('complete'),bool) else None
        snap['rank_version']=text(data.get('rank_version'),100) or None
        snap['period']=text(data.get('period'),100) or None
        snap['categories']=[]
        raw_categories=data.get('categories')
        for value in raw_categories if isinstance(raw_categories,list) else []:
            if not isinstance(value,str): continue
            value=value.strip()[:80]
            if value and value not in snap['categories']: snap['categories'].append(value)
            if len(snap['categories'])>=100: break
        with self.connect() as c:
            c.execute('INSERT INTO snapshots VALUES (?,?,?)',(snap['id'],timestamp,dumps(snap)))
            metadata={k:v for k,v in snap.items() if k not in ('books','categories')}
            c.execute('INSERT INTO snapshot_metadata VALUES (?,?,?)',(snap['id'],timestamp,dumps(metadata)))
        return snap

    def _snapshot_metadata(self,c):
        # Backfill old installations lazily, including records written by an
        # older CLI. Ordinary UI polling must not decode every historical book.
        missing=c.execute('SELECT s.id,s.created_at,s.body FROM snapshots s LEFT JOIN snapshot_metadata m ON m.id=s.id WHERE m.id IS NULL').fetchall()
        for row in missing:
            source=json.loads(row['body'])
            metadata={k:v for k,v in source.items() if k not in ('books','categories')}
            c.execute('INSERT INTO snapshot_metadata VALUES (?,?,?)',(row['id'],row['created_at'],dumps(metadata)))
        return [json.loads(r[0]) for r in c.execute('SELECT m.body FROM snapshot_metadata m JOIN snapshots s ON s.id=m.id ORDER BY s.created_at DESC')]

    def snapshot_revision(self):
        """Immutable snapshot additions invalidate cached aggregate results."""
        with self.connect() as c:
            row=c.execute('SELECT COUNT(*),COALESCE(MAX(rowid),0) FROM snapshots').fetchone()
            return tuple(row)

    def snapshot_history(self):
        """Full ranking records only, without loading reports or user texts."""
        with self.connect() as c:
            return [json.loads(r[0]) for r in c.execute('SELECT body FROM snapshots ORDER BY created_at DESC')]

    def overview_history(self):
        """Load current scopes and comparable baselines, not every page preview."""
        from .overview import relevant_snapshot_ids
        with self.connect() as c:
            ids=relevant_snapshot_ids(self._snapshot_metadata(c))
            return [self._snapshot(c,sid) for sid in ids]

    def _ordered(self,c,snapshot,filters):
        books=snapshot['books']; query=filters.get('query','').casefold()
        favs={r[0] for r in c.execute('SELECT book_id FROM favorites WHERE platform=?',(snapshot['platform'],))}
        result=[b for b in books if (not query or query in (' '.join([b['title'],b['author'],b['summary'],*b['tags']])).casefold()) and (not filters.get('favorites') or b['id'] in favs)]
        if filters.get('sort')=='title': result.sort(key=lambda b:b['title'])
        elif filters.get('sort')=='rank_desc': result.sort(key=lambda b:-(b['rank'] or 0))
        elif filters.get('sort')=='source': pass
        else: result.sort(key=lambda b:b['rank'] if b['rank'] else 10000000)
        return [b['id'] for b in result]

    def set_view(self, payload):
        mode=payload.get('mode','long')
        if mode not in ('long','short'): raise AppError('invalid_mode','作品类型不正确。')
        with self.connect() as c:
            view=self._kv(c,'view:'+mode,{'platform':'fanqie' if mode=='long' else 'heiyan','snapshot_id':None,'filters':{},'page':1,'page_size':20,'selected_id':None,'ordered_ids':[]})
            old=view.copy()
            if payload.get('platform'):
                p=next((p for p in PLATFORMS if p['id']==payload['platform'] and p['mode']==mode),None)
                if not p: raise AppError('invalid_platform','作品平台与类型不匹配。')
                if view['platform']!=payload['platform']:
                    view={**view,'platform':payload['platform'],'snapshot_id':None,'selected_id':None,'ordered_ids':[],'page':1}
            if 'snapshot_id' in payload:
                view['snapshot_id']=payload['snapshot_id'] or None
                if view['snapshot_id']!=old.get('snapshot_id'): view['selected_id']=None
            if 'filters' in payload:
                f=payload['filters']
                if not isinstance(f,dict): raise AppError('invalid_filters','筛选条件不正确。')
                sort=f.get('sort','rank')
                if sort not in ('source','rank','rank_asc','rank_desc','title'): raise AppError('invalid_sort','排序条件不正确。')
                view['filters']={'query':text(f.get('query'),200),'sort':sort,'favorites':bool(f.get('favorites'))}
            try:
                view['page']=max(1,int(payload.get('page',view['page'])))
                view['page_size']=min(100,max(1,int(payload.get('page_size',view['page_size']))))
            except (ValueError,TypeError): raise AppError('invalid_page','分页参数不正确。')
            if view.get('snapshot_id'):
                s=self._snapshot(c,view['snapshot_id'])
                if s['mode']!=mode or s['platform']!=view['platform']: raise AppError('view_mismatch','榜单与当前平台不匹配。')
                view['ordered_ids']=self._ordered(c,s,view['filters'])
                if 'selected_id' in payload: view['selected_id']=str(payload['selected_id']) if payload['selected_id'] else None
                if view.get('selected_id') and not any(b['id']==view['selected_id'] for b in s['books']): raise AppError('book_missing','选中的书不在这份榜单中。')
                view['page']=min(view['page'],max(1,(len(view['ordered_ids'])+view['page_size']-1)//view['page_size']))
                if view.get('selected_id') and ('selected_id' in payload) and (old.get('selected_id')!=view['selected_id'] or old.get('snapshot_id')!=view['snapshot_id']):
                    c.execute('INSERT INTO history(snapshot_id,book_id,selected_at) VALUES(?,?,?)',(s['id'],view['selected_id'],now()))
            self._set(c,'view:'+mode,view); self._set(c,'active_mode',mode)
        return self.state()

    def toggle_favorite(self,sid,bid,enabled):
        with self.connect() as c:
            s,b=self._book(c,sid,bid)
            if enabled: c.execute('INSERT INTO favorites VALUES (?,?,?,?) ON CONFLICT(platform,book_id) DO UPDATE SET snapshot_id=excluded.snapshot_id',(s['platform'],b['id'],sid,now()))
            else: c.execute('DELETE FROM favorites WHERE platform=? AND book_id=?',(s['platform'],b['id']))
            for mode in ('long','short'):
                v=self._kv(c,'view:'+mode)
                if v and v.get('snapshot_id'):
                    v['ordered_ids']=self._ordered(c,self._snapshot(c,v['snapshot_id']),v.get('filters',{}))
                    v['page']=min(v['page'],max(1,(len(v['ordered_ids'])+v['page_size']-1)//v['page_size']))
                    self._set(c,'view:'+mode,v)
        return self.state()

    def _state(self,c,full=False):
        snapshots=([json.loads(r[0]) for r in c.execute('SELECT body FROM snapshots ORDER BY created_at DESC')]
                   if full else self._snapshot_metadata(c))
        lookup={s['id']:s for s in snapshots} if full else {}
        book_lookup={}
        def referenced(sid,bid):
            if sid not in lookup:
                try: lookup[sid]=self._snapshot(c,sid)
                except AppError: lookup[sid]=None
            source=lookup[sid]
            if source and sid not in book_lookup:
                book_lookup[sid]={b['id']:b for b in source['books']}
            return source,book_lookup.get(sid,{}).get(bid,{})
        views={mode:self._kv(c,'view:'+mode,{'platform':'fanqie' if mode=='long' else 'heiyan','snapshot_id':None,'filters':{'query':'','sort':'rank','favorites':False},'page':1,'page_size':20,'selected_id':None,'ordered_ids':[]}) for mode in ('long','short')}
        fav=[]
        for row in c.execute('SELECT * FROM favorites ORDER BY created_at DESC'):
            s,b=referenced(row['snapshot_id'],row['book_id'])
            fav.append({**dict(row),'title':b.get('title',''),'mode':s['mode'] if s else '', 'book':b})
        history=[]
        for row in c.execute('SELECT * FROM history ORDER BY id DESC LIMIT 2000'):
            s,b=referenced(row['snapshot_id'],row['book_id'])
            history.append({**dict(row),'title':b.get('title',''),'platform':s['platform'] if s else '', 'mode':s['mode'] if s else '', 'book':b})
        return {'config':self._kv(c,'config',{}),'platforms':PLATFORMS,'snapshots':snapshots if full else [{k:v for k,v in s.items() if k not in ('books','categories')} for s in snapshots],'views':views,'active_mode':self._kv(c,'active_mode','long'),'favorites':fav,'history':history,'reports':[json.loads(r[0]) for r in c.execute('SELECT body FROM reports ORDER BY created_at DESC')],'texts':[json.loads(r[0]) for r in c.execute('SELECT body FROM texts ORDER BY created_at DESC')],'jobs':[json.loads(r[0]) for r in c.execute('SELECT body FROM jobs ORDER BY created_at DESC LIMIT 30')],'auth':self._kv(c,'auth',{}),'analysis_target':self._kv(c,'analysis_target')}

    def state(self,full=False):
        with self.connect() as c: return self._state(c,full)

    def context(self,session=None):
        with self.connect() as c:
            context=self._state(c,True)
            if session: context["analysis_target"]=self._kv(c,"analysis_target:"+str(session))
        context['data_dir']=str(self.directory); context['now']=datetime.now().astimezone().isoformat(); context['timezone']=os.environ.get('FAGAO_TIMEZONE')
        return context

    def auth_state(self,platform,value):
        with self.connect() as c:
            states=self._kv(c,'auth',{}); states[platform]=value; self._set(c,'auth',states)
        return value

    def update_config(self,values):
        with self.connect() as c:
            config=self._kv(c,'config',{})
            if 'points_site' in values:
                url=values['points_site']
                if url and not safe_url(url): raise AppError('invalid_url','积分站地址须为 http 或 https 网址。')
                config['points_site']=safe_url(url)
            self._set(c,'config',config)
        return config

    def import_text(self,sid,bid,content,scope,complete=False,source='用户导入',details=None):
        if not isinstance(content,str) or len(content.strip())<30: raise AppError('empty_text','正文不足 30 字，未保存为有效文本。')
        if len(content)>8_000_000: raise AppError('text_too_large','单次正文最多 800 万字符。')
        if re.search(r'<(?:!doctype|html|script|body)\b',content[:2000],re.I): raise AppError('html_not_text','这看起来是网页或错误页面，请导入实际正文。')
        if any(s in content[:300] for s in ('Access Denied','403 Forbidden','502 Bad Gateway','请完成安全验证')): raise AppError('error_not_text','内容像错误页面，未保存。')
        if not str(scope or '').strip(): raise AppError('scope_required','请说明正文范围，例如“前 3 章”。')
        with self.connect() as c:
            s,b=self._book(c,sid,bid)
            digest=hashlib.sha256(content.encode()).hexdigest()
            existing=c.execute('SELECT body FROM texts WHERE snapshot_id=? AND book_id=? ORDER BY created_at DESC',(sid,bid)).fetchall()
            for row in existing:
                m=json.loads(row[0])
                if m['sha256']==digest and m['scope']==scope and m['complete']==bool(complete) and m.get('acquisition','user_import')==('public_download' if details else 'user_import') and all(m.get(k)==v for k,v in (details or {}).items()): return m
            meta={'id':uuid.uuid4().hex,'snapshot_id':sid,'book_id':bid,'platform':s['platform'],'title':b['title'],'created_at':now(),'scope':text(scope,500),'complete':bool(complete),'completeness_basis':'用户声明' if complete else '部分文本或未核实完整性','source':text(source,1000),'characters':len(content),'sha256':digest}
            if details: meta.update(details)
            c.execute('INSERT INTO texts VALUES(?,?,?,?,?,?)',(meta['id'],sid,bid,meta['created_at'],dumps(meta),content))
        return meta

    def save_fetched_text(self,sid,bid,raw):
        """Store a verified downloaded range as the same asset used by WorkBuddy."""
        snapshot,book=self.book(sid,bid)
        if not isinstance(raw,dict) or raw.get('platform')!=snapshot['platform'] or str(raw.get('book_id') or '')!=book['id']:
            raise AppError('text_identity_mismatch','取得的正文与选定作品身份不一致，未保存。')
        for key in ('complete','coverage_complete','cancelled'):
            if not isinstance(raw.get(key,False),bool): raise AppError('invalid_text_metadata','正文完整度标记不正确。')
        chapter_rows=raw.get('chapters')
        if not isinstance(chapter_rows,list) or not chapter_rows or len(chapter_rows)>50000:
            raise AppError('invalid_text_range','没有有效的已取得章节记录，未保存正文。')
        chapters=[]; chapter_ids=set(); indices=set()
        for item in chapter_rows:
            if not isinstance(item,dict): raise AppError('invalid_text_range','章节范围格式不正确。')
            index=item.get('index'); item_id=item.get('item_id') or item.get('id')
            if isinstance(index,bool) or not isinstance(index,int) or index<1 or not isinstance(item_id,(str,int)) or isinstance(item_id,bool) or not str(item_id).strip() or len(str(item_id))>250:
                raise AppError('invalid_text_range','章节必须包含正确的序号与稳定标识。')
            item_id=str(item_id)
            if index in indices or item_id in chapter_ids: raise AppError('invalid_text_range','取得的章节重复，未保存正文。')
            if item.get('id') is not None and item.get('item_id') is not None and str(item['id'])!=str(item['item_id']): raise AppError('invalid_text_range','章节身份不一致。')
            characters=item.get('characters')
            if isinstance(characters,bool) or not isinstance(characters,int) or characters<1: raise AppError('invalid_text_range','存在空章节，不能标记正文下载成功。')
            indices.add(index); chapter_ids.add(item_id)
            chapters.append({'id':item_id,'item_id':item_id,'index':index,'title':text(item.get('title'),500),'url':safe_url(item.get('url')),'characters':characters})
        if [c['index'] for c in chapters]!=sorted(indices): raise AppError('invalid_text_range','正文中的章节顺序不正确。')
        missing=[]
        missing_rows=raw.get('missing',[])
        if not isinstance(missing_rows,list): raise AppError('invalid_text_range','缺失章节记录不正确。')
        for item in missing_rows[:50000]:
            if not isinstance(item,dict): raise AppError('invalid_text_range','缺失章节记录不正确。')
            index=item.get('index')
            if index is not None and (isinstance(index,bool) or not isinstance(index,int) or index<1 or index in indices): raise AppError('invalid_text_range','已取得与缺失章节范围冲突。')
            missing.append({'index':index,'id':text(item.get('id') or item.get('item_id'),250),'chapter':text(item.get('chapter') or item.get('title'),500),'reason':text(item.get('reason'),1000)})
        counts={}
        for key in ('catalog_chapters','requested_chapters'):
            value=raw.get(key)
            if isinstance(value,bool) or not isinstance(value,int) or value<1 or value>50000 or value<len(chapters): raise AppError('invalid_text_range','章节总数或请求范围不正确。')
            counts[key]=value
        if counts['requested_chapters']>counts['catalog_chapters'] or max(indices)>counts['catalog_chapters']:
            raise AppError('invalid_text_range','取得的章节超出目录范围。')
        requested_start=raw.get('requested_start',min(indices))
        if isinstance(requested_start,bool) or not isinstance(requested_start,int) or requested_start<1: raise AppError('invalid_text_range','请求起始章节不正确。')
        wanted=set(range(requested_start,requested_start+counts['requested_chapters']))
        if max(wanted)>counts['catalog_chapters'] or not indices.issubset(wanted) or any(item['index'] is not None and item['index'] not in wanted for item in missing): raise AppError('invalid_text_range','正文或缺失章节超出本次请求范围。')
        catalog_complete=raw.get('catalog_complete',False)
        if not isinstance(catalog_complete,bool): raise AppError('invalid_text_metadata','目录完整性标记不正确。')
        coverage=raw.get('coverage_complete',False); cancelled=raw.get('cancelled',False)
        if coverage and (missing or cancelled or indices!=wanted): raise AppError('invalid_text_range','部分或中断的正文不能标记请求范围完整。')
        complete=raw.get('complete',False)
        if complete and (not catalog_complete or not coverage or (isinstance(raw.get('status'),bool) or raw.get('status') not in ('已完结','完结','finished','completed','0',0)) or len(chapters)!=counts['catalog_chapters'] or indices!=set(range(1,counts['catalog_chapters']+1))):
            raise AppError('invalid_text_range','只有已完结作品的全部目录正文取齐后才能标记全书完整。')
        scope=text(raw.get('scope'),500).strip()
        if not scope: raise AppError('scope_required','下载结果没有说明实际阅读范围。')
        details={'acquisition':'public_download','chapters':chapters,'missing':missing,**counts,
                 'coverage_complete':coverage,'catalog_complete':catalog_complete,'requested_start':requested_start,'cancelled':cancelled,'status':text(raw.get('status'),60),
                 'collected_at':text(raw.get('collected_at'),100),
                 'completeness_basis':'已完结作品的当前公开目录全部取得' if complete else '仅所列已取得章节，不代表全书完整'}
        return self.import_text(sid,book['id'],raw.get('text'),scope,complete,text(raw.get('source'),1000) or '公开章节页面',details)

    def text_record(self,tid):
        with self.connect() as c:
            r=c.execute('SELECT body,content FROM texts WHERE id=?',(tid,)).fetchone()
            if not r: raise AppError('text_missing','找不到正文记录。',404)
            return {**json.loads(r[0]),'content':r[1]}

    def prepare_task(self,target,request,session=None):
        with self.connect() as c:
            sid,bid=target['snapshot_id'],target['book_id']; s,b=self._book(c,sid,bid)
            r=c.execute('SELECT body FROM texts WHERE book_id=? ORDER BY created_at DESC',(bid,)).fetchall()
            tx=next((json.loads(row[0]) for row in r if json.loads(row[0]).get('platform')==s['platform']),None)
            t={'id':uuid.uuid4().hex,'snapshot_id':sid,'book_id':bid,'platform':s['platform'],'mode':s['mode'],'book':b,'request':request,'created_at':now(),'text_id':tx['id'] if tx else None,'scope':tx['scope'] if tx else '仅简介（未取得正文）','status':'ready' if tx else 'needs_text','session':session,'text':tx,'snapshot':{k:v for k,v in s.items() if k!='books'}}
            c.execute('INSERT INTO tasks VALUES(?,?,?)',(t['id'],t['created_at'],dumps(t)))
            self._set(c,'analysis_target'+(':'+str(session) if session else ''),{'snapshot_id':sid,'book_id':bid,'task_id':t['id']})
        return t

    def task(self,tid):
        with self.connect() as c:
            r=c.execute('SELECT body FROM tasks WHERE id=?',(tid,)).fetchone()
            if not r: raise AppError('task_missing','找不到分析任务。',404)
            return json.loads(r[0])

    def save_report(self,tid,content):
        t=self.task(tid)
        if not isinstance(content,str) or len(content.strip())<30: raise AppError('empty_report','报告为空或过短。')
        if len(content)>2_000_000: raise AppError('large_report','报告内容过大。')
        report={'id':uuid.uuid4().hex,'task_id':tid,'snapshot_id':t['snapshot_id'],'book_id':t['book_id'],'platform':t['platform'],'mode':t['mode'],'title':t['book']['title'],'scope':t['scope'],'text_id':t['text_id'],'created_at':now()}
        heading=f"# 《{t['book']['title']}》分析\n\n阅读范围：{t['scope']}\n作品 ID：{t['platform']} / {t['book_id']}\n榜单快照：{t['snapshot_id']}\n\n---\n\n"
        with self.connect() as c:
            c.execute('INSERT INTO reports VALUES(?,?,?,?)',(report['id'],report['created_at'],dumps(report),heading+content))
            t['status']='completed'; t['report_id']=report['id']; c.execute('UPDATE tasks SET body=? WHERE id=?',(dumps(t),tid))
        return report

    def report(self,rid):
        with self.connect() as c:
            r=c.execute('SELECT body,content FROM reports WHERE id=?',(rid,)).fetchone()
            if not r: raise AppError('report_missing','找不到报告。',404)
            return {**json.loads(r[0]),'content':r[1]}

    def save_job(self,job):
        with self.connect() as c: c.execute('INSERT INTO jobs VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',(job['id'],job['created_at'],dumps(job)))
        return job

    def job(self,jid):
        with self.connect() as c:
            r=c.execute('SELECT body FROM jobs WHERE id=?',(jid,)).fetchone()
            if not r: raise AppError('job_missing','找不到采集任务。',404)
            return json.loads(r[0])

    def scope_jobs(self,scope_key):
        """Read persisted attempts for one exact collection scope, newest first."""
        with self.connect() as c:
            jobs=[json.loads(r[0]) for r in c.execute('SELECT body FROM jobs ORDER BY created_at DESC')]
        return [j for j in jobs if j.get('scope_key')==scope_key]

    def recover_jobs(self):
        with self.connect() as c:
            for r in c.execute('SELECT id,body FROM jobs').fetchall():
                j=json.loads(r['body'])
                if j.get('status') in ('running','pending','cancelling'):
                    j.update(status='interrupted',message='上次采集因程序退出而中断，可重试；已有榜单仍保留。',finished_at=now())
                    c.execute('UPDATE jobs SET body=? WHERE id=?',(dumps(j),r['id']))
