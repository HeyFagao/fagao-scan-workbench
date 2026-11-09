"""Loopback-only workbench API, no model API or account credentials required."""
from __future__ import annotations
import argparse
import hashlib
import math
import json
import mimetypes
import re
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, quote
from . import __version__
from .store import AppError, Store, now

WEB = Path(__file__).resolve().parent.parent / 'web'
MAX_BODY = 30 * 1024 * 1024

AUTO_FRESH_SECONDS = 15 * 60
AUTO_FAILURE_COOLDOWN_SECONDS = 5 * 60


def collection_scope(platform, options):
    """Use effective collector defaults so equivalent requests share one job."""
    if not isinstance(options,dict): raise AppError('invalid_options','采集条件不正确。')
    options=dict(options)
    modes={'fanqie':'long','qidian':'long','ciweimao':'long','heiyan':'short','fanqie_short':'short'}
    if not isinstance(platform,str) or platform not in modes: raise AppError('invalid_platform','平台不正确。')
    mode=modes[platform]
    if options.get('mode') not in (None,mode): raise AppError('invalid_mode','作品类型与采集平台不一致。')
    options['mode']=mode
    for key in ('channel','category','board','period'):
        if options.get(key) is not None and (not isinstance(options[key],str) or len(options[key])>200): raise AppError('invalid_options','频道、分类、榜型和周期必须是简短文字。')
    channel=options.get('channel') or {'fanqie':'女频','qidian':'男频','ciweimao':'综合','heiyan':'全部','fanqie_short':'全部'}[platform]
    options['channel']={'female':'女频','male':'男频','all':'全部'}.get(channel,channel)
    if platform=='fanqie': options['channel']={'0':'女频','1':'男频'}.get(options['channel'],options['channel'])
    if platform=='ciweimao' and options['channel']=='全部': options['channel']='综合'
    default_category=('古言脑洞' if options['channel']=='女频' else '玄幻脑洞') if platform=='fanqie' else ('书库' if platform=='heiyan' else '全部')
    options['category']=options.get('category') or default_category
    board=options.get('board') or {'fanqie':'新书榜','qidian':'月票榜','ciweimao':'月票榜','heiyan':'书库','fanqie_short':'热门故事'}[platform]
    options['board']={'new':'新书榜','read':'阅读榜','reading':'阅读榜','monthly':'月票榜','yuepiao':'月票榜','library':'书库','sales':'畅销榜'}.get(board,board)
    for key in ('channel','category','board'):
        if not isinstance(options[key],str) or len(options[key])>200: raise AppError('invalid_options','频道、分类和榜型必须是简短文字。')
    # An omitted/null limit means let the source/end-of-pages determine coverage.
    if options.get('limit') is None: options.pop('limit',None)
    options['max_pages']=options.get('max_pages') if options.get('max_pages') is not None else 100
    if options.get('detail_budget') is None: options.pop('detail_budget',None)
    if platform=='ciweimao' and options['board']=='月票榜': options['period']=options.get('period') or '月榜'
    for key in ('limit','max_pages','detail_budget'):
        if key in options:
            value=options[key]
            if isinstance(value,bool) or not isinstance(value,int) or value<1 or value>10000: raise AppError('invalid_options','采集数量和页数应为 1 到 10000 的整数。')
    try: serialized=json.dumps({'platform':platform,'options':options},ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    except (TypeError,ValueError): raise AppError('invalid_options','采集条件无法保存。')
    if len(serialized)>16000: raise AppError('invalid_options','采集条件过长。')
    return hashlib.sha256(serialized.encode()).hexdigest(),options


def _age_seconds(timestamp):
    try:
        then=datetime.fromisoformat(str(timestamp).replace('Z','+00:00'))
        if then.tzinfo is None: then=then.replace(tzinfo=timezone.utc)
        return max(0,(datetime.fromisoformat(now())-then).total_seconds())
    except (ValueError,TypeError): return None


class JobManager:
    def __init__(self,store):
        self.store=store; self.lock=threading.RLock(); self.active={}

    def start(self,platform,options,automatic=False):
        if not isinstance(automatic,bool): raise AppError('invalid_automatic','automatic 应为布尔值。')
        key,options=collection_scope(platform,options)
        with self.lock:
            active_id=self.active.get(key)
            if active_id:
                job=self.store.job(active_id)
                return {'status':'busy','job_id':active_id,'job':job}
            if automatic:
                auth=self.store.state().get('auth',{}).get('heiyan',{}) if platform=='heiyan' else {}
                if auth.get('state') in ('logged_out','expired','forbidden'):
                    return {'status':'cooldown','retry_after_seconds':AUTO_FAILURE_COOLDOWN_SECONDS,'requires_login':True,'auth':auth}
                attempts=self.store.scope_jobs(key)
                last=attempts[0] if attempts else None
                if last and (last.get('status') in ('failed','interrupted') or last.get('auth_state') in ('network_error','unavailable')):
                    age=_age_seconds(last.get('finished_at') or last.get('updated_at') or last.get('created_at'))
                    if age is not None and age<AUTO_FAILURE_COOLDOWN_SECONDS:
                        return {'status':'cooldown','retry_after_seconds':max(1,math.ceil(AUTO_FAILURE_COOLDOWN_SECONDS-age)),'job':last}
                # A saved partial batch is usable, but an expired session must not
                # make it look freshly complete after the user reconnects.
                completed=None if last and last.get('auth_state') in ('logged_out','expired','forbidden','network_error','unavailable') else next((j for j in attempts if j.get('status')=='completed' and j.get('snapshot_id')),None)
                if completed:
                    age=_age_seconds(completed.get('finished_at') or completed.get('created_at'))
                    if age is not None and age<AUTO_FRESH_SECONDS:
                        try: self.store.snapshot(completed['snapshot_id'])
                        except AppError: pass
                        else: return {'status':'fresh','snapshot_id':completed['snapshot_id'],'retry_after_seconds':max(1,math.ceil(AUTO_FRESH_SECONDS-age))}
            job={'id':uuid.uuid4().hex,'kind':'collect','platform':platform,'status':'pending','message':'正在准备采集','created_at':now(),'options':options,'scope_key':key,'automatic':automatic}
            self.store.save_job(job); self.active[key]=job['id']
            threading.Thread(target=self._run,args=(job,),daemon=True).start()
            return {'status':'started','job_id':job['id'],'job':dict(job)}

    def start_text(self,sid,bid,chapters=3,start_chapter=1,all_chapters=False):
        if not isinstance(all_chapters,bool): raise AppError('invalid_range','all 应为布尔值。')
        if isinstance(start_chapter,bool) or not isinstance(start_chapter,int) or not 1<=start_chapter<=5000: raise AppError('invalid_range','起始章节应为 1 到 5000 的整数。')
        if not all_chapters and (isinstance(chapters,bool) or not isinstance(chapters,int) or not 1<=chapters<=5000): raise AppError('invalid_range','下载章节数应为 1 到 5000 的整数。')
        snapshot,book=self.store.book(sid,bid)
        if snapshot['platform']!='fanqie': raise AppError('unsupported_text_source','此平台暂未接入自动取文，可导入原文。')
        options={'start_chapter':start_chapter,'chapters':None if all_chapters else chapters,'all':all_chapters}
        key='text:'+hashlib.sha256(json.dumps([snapshot['id'],book['id'],options],sort_keys=True).encode()).hexdigest()
        with self.lock:
            if key in self.active:
                jid=self.active[key]
                return {'status':'busy','job_id':jid,'job':self.store.job(jid)}
            job={'id':uuid.uuid4().hex,'kind':'text','platform':snapshot['platform'],'snapshot_id':snapshot['id'],
                 'book_id':book['id'],'title':book['title'],'options':options,'scope_key':key,
                 'status':'pending','message':'正在读取章节目录','created_at':now()}
            self.store.save_job(job); self.active[key]=job['id']
            threading.Thread(target=self._run_text,args=(job,),daemon=True).start()
            return {'status':'started','job_id':job['id'],'job':dict(job)}

    def _run_text(self,job):
        def progress(message,*args,**kwargs):
            with self.lock:
                if self.store.job(job['id'])['status'] in ('cancelling','cancelled'): raise AppError('cancelled','已暂停本次正文下载。')
                job.update(status='running',message=str(message),updated_at=now()); self.store.save_job(job)
        try:
            from .fanqie_content import fetch_book_text
            progress('正在读取选定作品的公开章节目录')
            options=job['options']
            raw=fetch_book_text(job['book_id'],self.store.directory,max_chapters=options['chapters'],start_chapter=options['start_chapter'],progress=progress)
            if not isinstance(raw,dict): raise AppError('invalid_text_result','正文结果格式不正确。')
            if raw.get('chapters'):
                if raw.get('requested_start',options['start_chapter'])!=options['start_chapter']: raise AppError('text_range_mismatch','返回的正文起始位置与本次请求不一致。')
                total=raw.get('catalog_chapters')
                if isinstance(total,int) and not isinstance(total,bool):
                    expected=max(0,total-options['start_chapter']+1)
                    if options['chapters'] is not None: expected=min(expected,options['chapters'])
                    if raw.get('requested_chapters')!=expected: raise AppError('text_range_mismatch','返回的请求范围与指定章节数不一致。')
            for chapter in raw.get('chapters',[]):
                index=chapter.get('index') if isinstance(chapter,dict) else None
                if not isinstance(index,int) or isinstance(index,bool) or index<options['start_chapter'] or (options['chapters'] is not None and index>=options['start_chapter']+options['chapters']):
                    raise AppError('text_range_mismatch','取得章节超出本次指定范围，未保存。')
            with self.lock:
                cancelled=self.store.job(job['id'])['status'] in ('cancelling','cancelled') or bool(raw.get('cancelled'))
                if cancelled:
                    raw={**raw,'cancelled':True,'coverage_complete':False,'complete':False}
                if not str(raw.get('text') or '').strip() and cancelled:
                    job.update(status='cancelled',message='正文下载已暂停，尚未取得可保存的章节。',finished_at=now())
                else:
                    text_meta=self.store.save_fetched_text(job['snapshot_id'],job['book_id'],raw)
                    job.update(status='cancelled' if cancelled else 'completed',partial=not raw.get('coverage_complete',False),
                               message=('已暂停，已取得内容已保存：' if cancelled else '正文已保存：')+text_meta['scope'],
                               text_id=text_meta['id'],text=text_meta,download_url=f"/api/texts/{text_meta['id']}/download",finished_at=now())
                self.store.save_job(job)
        except Exception as exc:
            with self.lock:
                if self.store.job(job['id'])['status'] in ('cancelling','cancelled'):
                    job.update(status='cancelled',message='正文下载已暂停，先前保存的正文仍保留。',finished_at=now())
                else:
                    message=getattr(exc,'message',None) or str(exc)
                    job.update(status='failed',message=message[:1200] or '未取得有效正文，已有内容保留。',error_code=getattr(exc,'code','text_failed'),finished_at=now())
                self.store.save_job(job)
        finally:
            with self.lock:
                if self.active.get(job['scope_key'])==job['id']: self.active.pop(job['scope_key'],None)

    def _run(self,job):
        def progress(message,*args,**kwargs):
            with self.lock:
                current=self.store.job(job['id'])
                if current['status']=='cancelled': raise AppError('cancelled','已取消本次采集。')
                job.update(status='running',message=str(message),updated_at=now()); self.store.save_job(job)
        try:
            from .collectors import collect
            progress('正在连接平台')
            result=collect(job['platform'],job['options'],self.store.directory,progress)
            with self.lock:
                if self.store.job(job['id'])['status']=='cancelled': return
                snapshot=self.store.save_snapshot(result)
                job.update(status='completed',message=f"已保存 {snapshot['books_count']} 本作品",snapshot_id=snapshot['id'],finished_at=now())
                result_auth=result.get('auth_state')
                if job['platform']=='heiyan' and result_auth in ('connected','logged_out','expired','forbidden','network_error','unavailable'):
                    auth_message=str(result.get('auth_message') or ('书库访问已确认。' if result_auth=='connected' else '本次只保存部分结果，请重新检查黑岩连接。'))[:1200]
                    self.store.auth_state('heiyan',{'state':result_auth,'message':auth_message})
                    job.update(auth_state=result_auth,auth_message=auth_message)
                    if result_auth!='connected':
                        job.update(partial=True,message=f"已保存 {snapshot['books_count']} 本有效作品；{auth_message}")
                self.store.save_job(job)
        except Exception as exc:
            with self.lock:
                if self.store.job(job['id'])['status']!='cancelled':
                    message=getattr(exc,'message',None) or str(exc)
                    auth_state=getattr(exc,'state',None)
                    if job['platform']=='heiyan' and auth_state in ('logged_out','expired','forbidden','network_error','unavailable'):
                        self.store.auth_state('heiyan',{'state':auth_state,'message':message[:1200]})
                    job.update(status='failed',message=message[:1200] or '采集失败，请重试。',error_code=getattr(exc,'code','collection_failed'),finished_at=now())
                    self.store.save_job(job)
        finally:
            with self.lock:
                if self.active.get(job['scope_key'])==job['id']: self.active.pop(job['scope_key'],None)

    def cancel(self,jid):
        with self.lock:
            j=self.store.job(jid)
            if j['status'] in ('pending','running'):
                if j.get('kind')=='text':
                    j.update(status='cancelling',message='正在暂停正文下载，已取得内容将保留。',cancel_requested_at=now())
                else:
                    j.update(status='cancelled',message='已取消本次采集，已有结果保留。',finished_at=now())
                self.store.save_job(j)
            return j

class WorkbenchServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,store):
        super().__init__(address,Handler)
        self.store=store; self.jobs=JobManager(store)
        self.store.recover_jobs()

class Handler(BaseHTTPRequestHandler):
    server_version='FagaoWorkbench/0.1'
    def log_message(self,fmt,*args):
        # Do not log request bodies, paths carrying account information, or credentials.
        pass

    def _send(self,status,body,kind='application/json; charset=utf-8',headers=None):
        payload=json.dumps(body,ensure_ascii=False).encode() if kind.startswith('application/json') else body
        if isinstance(payload,str): payload=payload.encode()
        self.send_response(status)
        self.send_header('Content-Type',kind); self.send_header('Content-Length',str(len(payload)))
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer'); self.send_header('X-Frame-Options','DENY')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https: http:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for key,value in (headers or {}).items(): self.send_header(key,value)
        self.end_headers()
        try: self.wfile.write(payload)
        except (BrokenPipeError,ConnectionResetError): pass

    def _guard(self,write=False):
        port=self.server.server_address[1]
        if self.headers.get('Host') not in (f'127.0.0.1:{port}',f'localhost:{port}'):
            raise AppError('invalid_host','工作台仅允许通过本机地址访问。',403)
        origin=self.headers.get('Origin')
        if origin and origin not in (f'http://127.0.0.1:{port}',f'http://localhost:{port}'):
            raise AppError('invalid_origin','拒绝来自其他网站的请求。',403)
        if self.headers.get('Sec-Fetch-Site')=='cross-site': raise AppError('cross_site','拒绝跨站请求。',403)
        if write and self.headers.get('X-Fagao-Request')!='1': raise AppError('missing_header','缺少工作台请求标记。',403)

    def do_GET(self): self._dispatch(False)
    def do_POST(self): self._dispatch(True)

    def _dispatch(self,write):
        try:
            self._guard(write)
            path=urlsplit(self.path).path
            data={}
            if write:
                if self.headers.get('Content-Type','').split(';')[0]!='application/json': raise AppError('invalid_content_type','需要 JSON 请求。',415)
                try: length=int(self.headers.get('Content-Length','0'))
                except ValueError: raise AppError('invalid_length','请求长度不正确。')
                if length<0 or length>MAX_BODY: raise AppError('too_large','请求文件过大。',413)
                try: data=json.loads(self.rfile.read(length))
                except (ValueError,UnicodeDecodeError): raise AppError('invalid_json','JSON 格式无效。')
                if not isinstance(data,dict): raise AppError('invalid_body','请求必须是对象。')
            result=self._post(path,data) if write else self._get(path)
            if result is not None: self._send(200,result)
        except AppError as e: self._send(e.status,{'error':{'code':e.code,'message':e.message}})
        except Exception as e:
            if hasattr(e,'code') and hasattr(e,'message'):
                self._send(400,{'error':{'code':e.code,'message':e.message,'candidates':getattr(e,'candidates',[])}})
            else:
                self._send(500,{'error':{'code':'internal_error','message':'处理未完成，请重试或查看诊断。'}})
                import traceback; traceback.print_exc()

    def _get(self,path):
        store=self.server.store
        if path=='/api/health': return {'app':'fagao-scan-workbench','version':__version__,'data_dir':str(store.directory),'source_root':str(Path(__file__).resolve().parent.parent)}
        if path=='/api/state': return store.state()
        if path=='/api/context': return store.context()
        if path.startswith('/api/snapshots/'): return store.snapshot(path.rsplit('/',1)[-1])
        if path.startswith('/api/reports/'): return store.report(path.rsplit('/',1)[-1])
        if path.startswith('/api/jobs/'): return store.job(path.rsplit('/',1)[-1])
        if path.startswith('/api/tasks/'): return store.task(path.rsplit('/',1)[-1])
        parts=path.strip('/').split('/')
        if len(parts)==4 and parts[:2]==['api','texts'] and parts[3]=='download':
            record=store.text_record(parts[2])
            title=re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]','_',record.get('title') or '正文').strip(' .')[:100] or '正文'
            filename=f"{title}_{record['id'][:8]}.txt"
            content=f"《{record['title']}》\n阅读范围：{record['scope']}\n来源：{record['source']}\n作品：{record['platform']} / {record['book_id']}\n\n"+record['content']
            self._send(200,content,'text/plain; charset=utf-8',{'Content-Disposition':f"attachment; filename=fagao-{record['id']}.txt; filename*=UTF-8''{quote(filename,safe='')}"})
            return None
        if path.startswith('/api/texts/'): return store.text_record(path.rsplit('/',1)[-1])
        if path.startswith('/api/'): raise AppError('not_found','接口不存在。',404)
        name='index.html' if path=='/' else path.lstrip('/')
        target=(WEB/name).resolve()
        if not target.is_relative_to(WEB.resolve()) or not target.is_file(): raise AppError('not_found','页面不存在。',404)
        self._send(200,target.read_bytes(),mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
        return None

    def _post(self,path,data):
        store=self.server.store
        if path=='/api/catalog':
            snapshot,book=store.book(data.get('snapshot_id'),data.get('book_id'))
            if snapshot['platform']!='fanqie': raise AppError('unsupported_text_source','此平台暂未接入在线目录，可导入原文。')
            from .fanqie_content import catalog
            from .collectors import CollectorError
            try: result=catalog(book['id'],store.directory)
            except CollectorError as exc: raise AppError('catalog_unavailable',str(exc)[:1200],502) from exc
            if str(result.get('book_id'))!=book['id'] or result.get('platform')!='fanqie': raise AppError('text_identity_mismatch','目录作品身份不一致，未采用。')
            return {**result,'snapshot_id':snapshot['id'],'book_id':book['id'],'total':result.get('catalog_chapters',len(result.get('chapters',[]))),'source_url':result.get('source','')}
        if path=='/api/fetch-text': return self.server.jobs.start_text(data.get('snapshot_id'),data.get('book_id'),data.get('chapters',3),data.get('start_chapter',1),data.get('all',False))
        if path=='/api/insights':
            from .insights import build_insights
            snapshot=store.snapshot(data.get('snapshot_id'))
            return build_insights(snapshot,store.state(full=True)['snapshots'])
        if path=='/api/view': return store.set_view(data)
        if path=='/api/favorite': return store.toggle_favorite(data.get('snapshot_id'),data.get('book_id'),bool(data.get('enabled')))
        if path=='/api/collect': return self.server.jobs.start(data.get('platform'),data.get('options',{}),data.get('automatic',False))
        if path=='/api/jobs/cancel': return self.server.jobs.cancel(data.get('job_id'))
        if path=='/api/auth':
            from .collectors import auth
            p=data.get('platform'); action=data.get('action')
            if p!='heiyan' or action not in ('login','check'): raise AppError('invalid_auth','仅支持黑岩登录或连接检查。')
            return store.auth_state(p,auth(p,action,store.directory))
        if path=='/api/import/snapshot': return store.save_snapshot(data.get('snapshot'),imported=True)
        if path=='/api/import/text': return store.import_text(data.get('snapshot_id'),data.get('book_id'),data.get('text'),data.get('scope'),data.get('complete',False))
        if path=='/api/config': return store.update_config(data)
        if path in ('/api/resolve','/api/prepare'):
            from .resolver import resolve_reference
            query=data.get('query','')
            if not isinstance(query,str) or not query.strip(): raise AppError('query_required','请说明要分析哪本书。')
            target=resolve_reference(query,store.context(data.get('session')))
            return store.prepare_task(target,query,data.get('session')) if path.endswith('prepare') else target
        if path=='/api/reports': return store.save_report(data.get('task_id'),data.get('content'))
        raise AppError('not_found','接口不存在。',404)

def main():
    parser=argparse.ArgumentParser(description='发糕扫榜本地服务')
    parser.add_argument('--port',type=int,default=18376); parser.add_argument('--data-dir')
    args=parser.parse_args()
    server=WorkbenchServer(('127.0.0.1',args.port),Store(args.data_dir))
    print(f'发糕扫榜已启动：http://127.0.0.1:{server.server_address[1]}',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__=='__main__': main()
