"""Loopback-only workbench API, no model API or account credentials required."""
from __future__ import annotations
import argparse
import json
import mimetypes
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from . import __version__
from .store import AppError, Store, now

WEB = Path(__file__).resolve().parent.parent / 'web'
MAX_BODY = 30 * 1024 * 1024

class JobManager:
    def __init__(self,store):
        self.store=store; self.lock=threading.RLock(); self.active={}

    def start(self,platform,options):
        if platform not in {p['id'] for p in self.store.state()['platforms']}: raise AppError('invalid_platform','平台不正确。')
        if not isinstance(options,dict): raise AppError('invalid_options','采集条件不正确。')
        with self.lock:
            if platform in self.active: return self.active[platform]
            job={'id':uuid.uuid4().hex,'platform':platform,'status':'pending','message':'正在准备采集','created_at':now(),'options':options}
            self.store.save_job(job); self.active[platform]=job['id']
            threading.Thread(target=self._run,args=(job,),daemon=True).start()
            return job['id']

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
            with self.lock: self.active.pop(job['platform'],None)

    def cancel(self,jid):
        with self.lock:
            j=self.store.job(jid)
            if j['status'] in ('pending','running'):
                j.update(status='cancelled',message='已取消本次采集，已有结果保留。'); self.store.save_job(j)
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

    def _send(self,status,body,kind='application/json; charset=utf-8'):
        payload=json.dumps(body,ensure_ascii=False).encode() if kind.startswith('application/json') else body
        if isinstance(payload,str): payload=payload.encode()
        self.send_response(status)
        self.send_header('Content-Type',kind); self.send_header('Content-Length',str(len(payload)))
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer'); self.send_header('X-Frame-Options','DENY')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
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
        if path.startswith('/api/texts/'): return store.text_record(path.rsplit('/',1)[-1])
        if path.startswith('/api/'): raise AppError('not_found','接口不存在。',404)
        name='index.html' if path=='/' else path.lstrip('/')
        target=(WEB/name).resolve()
        if not target.is_relative_to(WEB.resolve()) or not target.is_file(): raise AppError('not_found','页面不存在。',404)
        self._send(200,target.read_bytes(),mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
        return None

    def _post(self,path,data):
        store=self.server.store
        if path=='/api/view': return store.set_view(data)
        if path=='/api/favorite': return store.toggle_favorite(data.get('snapshot_id'),data.get('book_id'),bool(data.get('enabled')))
        if path=='/api/collect': return {'job_id':self.server.jobs.start(data.get('platform'),data.get('options',{}))}
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
