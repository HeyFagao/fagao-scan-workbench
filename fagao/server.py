# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Loopback-only workbench API with account-bound membership authorization."""
from __future__ import annotations
import argparse
import hashlib
import math
import json
import mimetypes
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, quote, parse_qs
from . import __version__
from . import network
from .store import AppError, Store, now, PLATFORMS

WEB = Path(__file__).resolve().parent.parent / 'web'
MAX_BODY = 30 * 1024 * 1024
SUBMISSION_GET = frozenset(('/api/submissions/drafts', '/api/submissions/draft',
                            '/api/submissions/component'))
SUBMISSION_POST = frozenset(('/api/submissions/save', '/api/submissions/delete',
                             '/api/submissions/open'))

AUTO_FRESH_SECONDS = 15 * 60
AUTO_FAILURE_COOLDOWN_SECONDS = 5 * 60
INTERACTIVE_IDLE_SECONDS = 120
PUBLIC_SYNC_DEADLINE_SECONDS = 40
PUBLIC_SYNC_FINAL_WAIT_SECONDS = .25
OVERVIEW_SCOPE = 'overview:fanqie-new-qidian-ciweimao:v1'


def text_integrity_summary(record):
    """Describe saved evidence consistently in task messages and TXT headers."""
    if record.get('acquisition')!='public_download':
        return ('用户标记为完整正文' if record.get('complete') else '按用户导入范围保存','')
    integrity=record.get('integrity') or {}
    status=integrity.get('status')
    if status=='partial': label='公开片段'
    elif status!='verified': label='完整性未确认'
    elif record.get('complete'): label='已核实为作品完整正文'
    elif record.get('coverage_complete'): label='本次请求范围已完整取得'
    else: label='已保存实际可读部分'
    details=[]
    actual=integrity.get('actual_characters')
    declared=integrity.get('declared_book_word_count')
    if isinstance(actual,int) and not isinstance(actual,bool) and actual>=0:
        if integrity.get('actual_characters_basis')=='saved_text_including_headings':
            details.append(f'已保存文本 {actual:,} 字符（不含空白，含章节标题）')
        else:
            details.append(f'实际正文 {actual:,} 字符（不含空白）')
    if isinstance(declared,int) and not isinstance(declared,bool) and declared>0:
        details.append(f'作品标注 {declared:,} 字')
    if integrity.get('reason'): details.append(str(integrity['reason']))
    return label,'；'.join(details)


def _collection_key(platform, options):
    try: serialized=json.dumps({'platform':platform,'options':options},ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    except (TypeError,ValueError): raise AppError('invalid_options','采集条件无法保存。')
    if len(serialized)>16000: raise AppError('invalid_options','采集条件过长。')
    return hashlib.sha256(serialized.encode()).hexdigest(),options


def collection_scope(platform, options):
    """Use effective collector defaults so equivalent requests share one job."""
    if not isinstance(options,dict): raise AppError('invalid_options','采集条件不正确。')
    options=dict(options)
    modes={item['id']:item['mode'] for item in PLATFORMS}
    if not isinstance(platform,str) or platform not in modes: raise AppError('invalid_platform','平台不正确。')
    mode=modes[platform]
    if options.get('mode') not in (None,mode): raise AppError('invalid_mode','作品类型与采集平台不一致。')
    from .long_rankings import COLLECTORS, collection_options
    if platform in COLLECTORS:
        return _collection_key(platform, collection_options(platform, options))
    options['mode']=mode
    overseas=next((item for item in PLATFORMS if item['id']==platform and item['mode']=='overseas'),None)
    if overseas:
        defaults=overseas['default_options']
        if not options.get('period') and defaults.get('period'):
            options['period']=defaults['period']
        for key, choices in (('channel','channels'),('category','categories'),('board','boards')):
            value=options.get(key) or defaults[key]
            if not isinstance(value,str) or value not in overseas[choices]:
                raise AppError('invalid_options','请选择该海外平台提供的频道、分类和榜型。')
            options[key]=value
    for key in ('channel','category','board','period'):
        if options.get(key) is not None and (not isinstance(options[key],str) or len(options[key])>200): raise AppError('invalid_options','频道、分类、榜型和周期必须是简短文字。')
    channel=options.get('channel') or {'fanqie':'女频','qidian':'男频','ciweimao':'综合','heiyan':'全部','fanqie_short':'全部'}.get(platform,'全部')
    options['channel']={'female':'女频','male':'男频','all':'全部'}.get(channel,channel)
    if platform=='fanqie': options['channel']={'0':'女频','1':'男频'}.get(options['channel'],options['channel'])
    if platform=='ciweimao' and options['channel']=='全部': options['channel']='综合'
    default_category=('古言脑洞' if options['channel']=='女频' else '玄幻脑洞') if platform=='fanqie' else ('书库' if platform=='heiyan' else '全部')
    options['category']=options.get('category') or default_category
    board=options.get('board') or {'fanqie':'新书榜','qidian':'月票榜','ciweimao':'月票榜','heiyan':'书库','fanqie_short':'热门故事'}.get(platform,'分类榜')
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
    return _collection_key(platform, options)


def _age_seconds(timestamp):
    try:
        then=datetime.fromisoformat(str(timestamp).replace('Z','+00:00'))
        if then.tzinfo is None: then=then.replace(tzinfo=timezone.utc)
        return max(0,(datetime.fromisoformat(now())-then).total_seconds())
    except (ValueError,TypeError): return None


class JobManager:
    def __init__(self,store,*,require_access=None):
        self.store=store; self.lock=threading.RLock(); self.active={}
        self.require_access=require_access
        self.condition=threading.Condition(self.lock)
        self.overview_lock=threading.Lock(); self.overview_cache=None
        self.public_import_lock=threading.Lock()

    def _require(self):
        if self.require_access is not None:
            return self.require_access()

    def _run_authorized(self,worker,job):
        try:
            with network.authorized_requests(self._require if self.require_access is not None else None):
                self._require()
                worker(job)
        except Exception as exc:
            with self.lock:
                current=self.store.job(job['id'])
                if current['status'] not in ('cancelled','cancelling'):
                    current.update(status='interrupted',error_code=getattr(exc,'code','operation_failed'),
                                   message=str(getattr(exc,'message',exc))[:1200],finished_at=now(),waiting_for_more=False)
                    self.store.save_job(current)
                if self.active.get(job['scope_key'])==job['id']: self.active.pop(job['scope_key'],None)

    def start(self,platform,options,automatic=False,interactive=False,demand_count=10):
        self._require()
        if not isinstance(automatic,bool): raise AppError('invalid_automatic','automatic 应为布尔值。')
        if not isinstance(interactive,bool): raise AppError('invalid_interactive','interactive 应为布尔值。')
        if isinstance(demand_count,bool) or not isinstance(demand_count,int) or not 1<=demand_count<=10000:
            raise AppError('invalid_count','浏览目标数量应为 1 到 10000。')
        key,options=collection_scope(platform,options)
        with self.lock:
            active_id=self.active.get(key)
            if active_id:
                job=self.store.job(active_id)
                job['shared']=True
                if not interactive and job.get('interactive'):
                    # The overview needs the complete category and must not wait
                    # for scrolling in a different browser tab.
                    job.update(interactive=False,waiting_for_more=False)
                    self.condition.notify_all()
                self.store.save_job(job)
                return {'status':'busy','job_id':active_id,'job':job}
            if automatic:
                auth=self.store.state().get('auth',{}).get('heiyan',{}) if platform=='heiyan' else {}
                if auth.get('state') in ('logged_out','expired','forbidden'):
                    return {'status':'cooldown','retry_after_seconds':AUTO_FAILURE_COOLDOWN_SECONDS,'requires_login':True,'auth':auth}
                attempts=self.store.scope_jobs(key)
                last=attempts[0] if attempts else None
                if (options.get('mode')=='overseas' and last
                        and last.get('status')=='failed'
                        and last.get('error_code') in ('forbidden','source_access_denied','verification_required')):
                    return {'status':'cooldown','retry_after_seconds':86400,'requires_action':True,'job':last}
                if last and (last.get('status') in ('failed','interrupted') or last.get('auth_state') in ('network_error','unavailable')):
                    age=_age_seconds(last.get('finished_at') or last.get('updated_at') or last.get('created_at'))
                    if age is not None and age<AUTO_FAILURE_COOLDOWN_SECONDS:
                        return {'status':'cooldown','retry_after_seconds':max(1,math.ceil(AUTO_FAILURE_COOLDOWN_SECONDS-age)),'job':last}
                # A saved partial batch is usable, but an expired session must not
                # make it look freshly complete after the user reconnects.
                completed=None if last and last.get('auth_state') in ('logged_out','expired','forbidden','network_error','unavailable') else next((j for j in attempts if j.get('status')=='completed' and j.get('snapshot_id') and not j.get('paused_for_demand')),None)
                if completed:
                    age=_age_seconds(completed.get('finished_at') or completed.get('created_at'))
                    if age is not None and age<AUTO_FRESH_SECONDS:
                        try: self.store.snapshot(completed['snapshot_id'])
                        except AppError: pass
                        else: return {'status':'fresh','snapshot_id':completed['snapshot_id'],'retry_after_seconds':max(1,math.ceil(AUTO_FRESH_SECONDS-age))}
            job={'id':uuid.uuid4().hex,'kind':'collect','platform':platform,'status':'pending','message':'正在准备采集','created_at':now(),'options':options,'scope_key':key,'automatic':automatic,
                 'interactive':interactive,'demand_count':demand_count,'waiting_for_more':False}
            self.store.save_job(job); self.active[key]=job['id']
            threading.Thread(target=self._run_authorized,args=(self._run,job),daemon=True).start()
            return {'status':'started','job_id':job['id'],'job':dict(job)}

    def more(self,jid,count=100):
        self._require()
        if isinstance(count,bool) or not isinstance(count,int) or not 1<=count<=1000:
            raise AppError('invalid_count','每次继续读取的数量应为 1 到 1000。')
        with self.condition:
            job=self.store.job(jid)
            if job.get('kind')!='collect': raise AppError('invalid_job','只有榜单浏览任务可以继续读取。')
            target=min(10000,max(job.get('demand_count',10),job.get('partial_count',0))+count)
            if job['status'] in ('pending','running'):
                job.update(demand_count=target,waiting_for_more=False,demand_updated_at=now())
                self.store.save_job(job); self.condition.notify_all()
                return {'status':'continued','job_id':jid,'job':job}
            if job.get('paused_for_demand'):
                if self.active.get(job['scope_key'])==jid: self.active.pop(job['scope_key'],None)
                return self.start(job['platform'],job['options'],False,True,target)
            return {'status':job['status'],'job_id':jid,'job':job}

    def overview(self):
        self._require()
        return self._overview_data()

    def _overview_data(self):
        """Saved-data computation; the HTTP caller supplies read authorization."""
        from .overview import build_overview
        revision=self.store.snapshot_revision()
        with self.overview_lock:
            if self.overview_cache is None or self.overview_cache[0]!=revision:
                self.overview_cache=(revision,build_overview(self.store.overview_history()))
            result=dict(self.overview_cache[1])
        jobs=self.store.scope_jobs(OVERVIEW_SCOPE)
        result['update']=jobs[0] if jobs else None
        return result

    def start_overview(self,automatic=False):
        self._require()
        if not isinstance(automatic,bool): raise AppError('invalid_automatic','automatic 应为布尔值。')
        with self.lock:
            if OVERVIEW_SCOPE in self.active:
                jid=self.active[OVERVIEW_SCOPE]
                return {'status':'busy','job_id':jid,'job':self.store.job(jid)}
            attempts=self.store.scope_jobs(OVERVIEW_SCOPE)
            last=attempts[0] if attempts else None
            if automatic and last:
                age=_age_seconds(last.get('finished_at') or last.get('created_at'))
                fresh=last['status']=='completed' and not last.get('partial')
                duration=AUTO_FRESH_SECONDS if fresh else AUTO_FAILURE_COOLDOWN_SECONDS
                if age is not None and age<duration:
                    return {'status':'fresh' if fresh else 'cooldown','job_id':last['id'],'job':last,
                            'retry_after_seconds':max(1,math.ceil(duration-age))}
            job={'id':uuid.uuid4().hex,'kind':'overview','platform':'overview','status':'pending',
                 'message':'正在汇总番茄新书榜、起点与刺猬猫月票榜','created_at':now(),
                 'scope_key':OVERVIEW_SCOPE,'automatic':automatic,
                 'progress':{'platform':'fanqie','channel':'男频','category':'玄幻脑洞',
                             'completed_categories':0,'failed_categories':0,'skipped_categories':0,'total_categories':4}}
            self.store.save_job(job); self.active[OVERVIEW_SCOPE]=job['id']
            threading.Thread(target=self._run_authorized,args=(self._run_overview,job),daemon=True).start()
            return {'status':'started','job_id':job['id'],'job':dict(job)}

    def _start_public_sync(self,job):
        """Run the optional archive beside direct previews; never hold up a source."""
        done=threading.Event()
        deadline=time.monotonic()+PUBLIC_SYNC_DEADLINE_SECONDS
        with self.lock:
            current=self.store.job(job['id'])
            if current['status']=='cancelled':
                done.set(); return done
            job.update(current)
            job['public_source']={'status':'running','snapshot_count':0,'started_at':now()}
            self.store.save_job(job)

        def finish(status, **fields):
            with self.lock:
                current=self.store.job(job['id'])
                if current['status']=='cancelled':
                    done.set(); return
                job['public_source']={'status':status,'snapshot_count':0,**fields,'finished_at':now()}
                current['public_source']=job['public_source']; current['updated_at']=now()
                self.store.save_job(current)
            done.set()

        def ensure_active(*args,**kwargs):
            self._require()
            ensure_storage_active()

        def ensure_storage_active():
            with self.lock:
                if self.store.job(job['id'])['status']=='cancelled':
                    raise AppError('cancelled','已暂停公开快照同步。')
            if time.monotonic()>deadline:
                raise AppError('public_timeout','公开来源响应超时，已保留直采结果。')

        def worker():
            try:
                self._require()
                from .public_rankings import sync_public_rankings
                from .overview import _scope_key
                raw=sync_public_rankings(progress=ensure_active)
                interruption=raw.get('interrupted_reason')
                interruption_message=raw.get('interrupted_message')
                try: ensure_active()
                except AppError as exc:
                    if not exc.code.startswith('membership_'): raise
                    interruption=exc.code; interruption_message=exc.message
                ensure_storage_active()
                imported=0
                with self.public_import_lock:
                    existing={(item.get('source_id'),_scope_key(item),item.get('data_date'),item.get('source_revision'))
                              for item in self.store.state()['snapshots']}
                    for snapshot in sorted(raw.get('snapshots',[]),key=lambda item:str(item.get('data_date') or '')):
                        key=(snapshot.get('source_id'),_scope_key({**snapshot,'mode':snapshot.get('mode') or 'long'}),snapshot.get('data_date'),snapshot.get('source_revision'))
                        with self.lock:
                            # Persist results already obtained in this account,
                            # while never issuing more requests after expiry.
                            ensure_storage_active()
                            if key in existing: continue
                            self.store.save_snapshot(snapshot); existing.add(key); imported+=1
                finish('interrupted' if interruption else 'completed' if raw.get('snapshots') else 'unavailable',snapshot_count=imported,
                       coverage=raw.get('coverage',[]),warnings=raw.get('warnings',[]),failures=raw.get('failures',[]),
                       **({'error_code':interruption,'message':interruption_message} if interruption else {}))
            except Exception as exc:
                if getattr(exc,'code',None)!='cancelled':
                    finish('interrupted' if str(getattr(exc,'code','')).startswith('membership_') else 'unavailable',
                           message=str(exc)[:800],error_code=getattr(exc,'code',None))
                done.set()

        def watchdog():
            if not done.wait(PUBLIC_SYNC_DEADLINE_SECONDS):
                finish('unavailable',message='公开来源响应超时，已保留直采结果。')

        def authorized_worker():
            with network.authorized_requests(self._require if self.require_access is not None else None):
                worker()
        threading.Thread(target=authorized_worker,daemon=True).start()
        threading.Thread(target=watchdog,daemon=True).start()
        return done

    def _run_overview(self,job):
        from .batch import collect_all_categories
        counters=job['progress']
        result={'channels':[],'snapshots_count':0,'preview_count':0,'failed_categories':[],
                'incomplete_categories':[],'warnings':[],'complete':False}
        public_done=self._start_public_sync(job)

        def update(message):
            self._require()
            with self.lock:
                if self.store.job(job['id'])['status']=='cancelled':
                    raise AppError('cancelled','首页更新已暂停，已取得榜单保留。')
                job.update(status='running',message=str(message),progress=dict(counters),updated_at=now())
                self.store.save_job(job)

        def load(platform,options,track=True):
            counters.update(platform=platform,channel=options['channel'],category=options['category'])
            update(f"正在更新{options['channel']}／{options['board']}／{options['category']}")
            reply=self.start(platform,options,job['automatic'])
            child_id=reply.get('job_id')
            with self.lock:
                if self.store.job(job['id'])['status']=='cancelled':
                    if child_id and reply['status']=='started' and not self.store.job(child_id).get('shared'):
                        self.cancel(child_id)
                    raise AppError('cancelled','首页更新已暂停，已取得榜单保留。')
                job.update(active_child_id=child_id,owns_child=reply['status']=='started')
                self.store.save_job(job)
            try:
                if reply['status']=='cooldown': raise AppError('source_cooldown','该分类刚才访问失败，稍后自动重试。')
                if reply['status']=='fresh':
                    snapshot=self.store.snapshot(reply['snapshot_id'])
                    if track: counters['skipped_categories']+=1
                else:
                    last_message=None
                    while True:
                        self._require()
                        with self.lock:
                            if self.store.job(job['id'])['status']=='cancelled':
                                if job.get('owns_child') and not self.store.job(child_id).get('shared'): self.cancel(child_id)
                                raise AppError('cancelled','首页更新已暂停。')
                        child=self.store.job(child_id)
                        if child['status'] not in ('pending','running','cancelling'): break
                        if child.get('message')!=last_message:
                            last_message=child.get('message'); update(last_message or '正在更新榜单')
                        threading.Event().wait(0.05)
                    if child['status']!='completed' or not child.get('snapshot_id'):
                        raise AppError(child.get('error_code','source_failed'),child.get('message','该分类暂时无法更新。'))
                    snapshot=self.store.snapshot(child['snapshot_id'])
                if track:
                    counters['completed_categories']+=1; result['snapshots_count']+=1
                    update(f"已汇总 {counters['completed_categories']} 个榜单分类，继续更新其余分类")
                else: result['preview_count']+=1
                return snapshot
            except Exception as exc:
                if track and getattr(exc,'code',None)!='cancelled': counters['failed_categories']+=1
                raise

        try:
            # Populate each platform before deep category pagination. A returning
            # user sees the existing cache immediately; only missing seed scopes
            # need this one-page pass. These are previews, never fresh full scans.
            counters['phase']='preview'
            cached=self.store.state()['snapshots']
            for platform,channel,category,board,period in (
                ('fanqie','男频','玄幻脑洞','新书榜',''),
                ('fanqie','女频','古言脑洞','新书榜',''),
                ('qidian','男频','全部','月票榜',''),
                ('ciweimao','综合','全部','月票榜','月榜'),
            ):
                if any(s.get('platform')==platform and s.get('channel')==channel and s.get('category')==category
                       and s.get('board')==board and (s.get('period') or '')==period and s.get('books_count',0)>0 for s in cached):
                    continue
                options={'channel':channel,'category':category,'board':board,'limit':10000,'max_pages':1}
                if period: options['period']=period
                try: load(platform,options,False)
                except Exception as exc:
                    if getattr(exc,'code',None)=='cancelled' or str(getattr(exc,'code','')).startswith('membership_'): raise
                    result['warnings'].append(f"{platform}首批暂未取得，将在完整更新时重试。")
            counters['phase']='full'
            for channel in ('男频','女频'):
                def discovered(categories):
                    counters['total_categories']+=max(0,len(categories)-1)
                    update(f"已发现番茄{channel}新书榜 {len(categories)} 个分类")
                batch=collect_all_categories(self.store,{'channel':channel,'board':'新书榜','limit':10000,'max_pages':100},
                    update,load_category=lambda options:load('fanqie',options),on_discovery=discovered)
                result['channels'].append({key:value for key,value in batch.items() if key!='snapshots'})
                result['failed_categories'].extend({'platform':'fanqie','channel':channel,**item} for item in batch['failed_categories'])
                result['incomplete_categories'].extend({'platform':'fanqie','channel':channel,'category':category} for category in batch['incomplete_categories'])
                result['warnings'].extend(batch['warnings'])
            for platform,channel,period in (('qidian','男频',''),('ciweimao','综合','月榜')):
                options={'channel':channel,'category':'全部','board':'月票榜','limit':10000,'max_pages':100}
                if period: options['period']=period
                try:
                    snapshot=load(platform,options)
                    if snapshot.get('complete') is not True:
                        result['incomplete_categories'].append({'platform':platform,'channel':channel,'category':'全部'})
                except Exception as exc:
                    if getattr(exc,'code',None)=='cancelled' or str(getattr(exc,'code','')).startswith('membership_'): raise
                    result['failed_categories'].append({'platform':platform,'channel':channel,'category':'全部',
                                                       'code':getattr(exc,'code','source_failed'),'message':str(exc)[:1200]})
            public_done.wait(PUBLIC_SYNC_FINAL_WAIT_SECONDS)
            self._require()
            result['complete']=not result['failed_categories'] and not result['incomplete_categories'] and all(b['discovery_complete'] for b in result['channels'])
            with self.lock:
                if self.store.job(job['id'])['status']=='cancelled': return
                job.update(status='completed' if result['snapshots_count'] or result['preview_count'] else 'failed',partial=not result['complete'],result=result,
                           message=f"已汇总 {result['snapshots_count']} 个榜单分类"+('；部分来源稍后重试' if not result['complete'] else ''),
                           finished_at=now(),progress=dict(counters),active_child_id=None,owns_child=False)
                self.store.save_job(job)
        except Exception as exc:
            with self.lock:
                if self.store.job(job['id'])['status']!='cancelled':
                    job.update(status='interrupted' if str(getattr(exc,'code','')).startswith('membership_') else 'failed',
                               error_code=getattr(exc,'code','overview_failed'),partial=True,result=result,message=str(exc)[:1200],finished_at=now())
                    self.store.save_job(job)
        finally:
            with self.lock:
                if self.active.get(OVERVIEW_SCOPE)==job['id']: self.active.pop(OVERVIEW_SCOPE,None)

    def start_text(self,sid,bid,chapters=3,start_chapter=1,all_chapters=False):
        self._require()
        if not isinstance(all_chapters,bool): raise AppError('invalid_range','all 应为布尔值。')
        if isinstance(start_chapter,bool) or not isinstance(start_chapter,int) or not 1<=start_chapter<=5000: raise AppError('invalid_range','起始章节应为 1 到 5000 的整数。')
        if not all_chapters and (isinstance(chapters,bool) or not isinstance(chapters,int) or not 1<=chapters<=5000): raise AppError('invalid_range','下载章节数应为 1 到 5000 的整数。')
        snapshot,book=self.store.book(sid,bid)
        if snapshot['platform'] not in ('fanqie','qidian','ciweimao'): raise AppError('unsupported_text_source','此平台暂未接入自动取文，可导入原文。')
        if snapshot['platform'] in ('qidian','ciweimao'):
            from .opening_content import validate_range
            from .collectors import CollectorError
            try: validate_range(snapshot['platform'],None if all_chapters else chapters,start_chapter)
            except CollectorError as exc: raise AppError('invalid_range',str(exc)) from exc
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
            threading.Thread(target=self._run_authorized,args=(self._run_text,job),daemon=True).start()
            return {'status':'started','job_id':job['id'],'job':dict(job)}

    def _run_text(self,job):
        def progress(message,*args,**kwargs):
            self._require()
            with self.lock:
                if self.store.job(job['id'])['status'] in ('cancelling','cancelled'): raise AppError('cancelled','已暂停本次正文下载。')
                job.update(status='running',message=str(message),updated_at=now()); self.store.save_job(job)
        try:
            from .collectors import fetch_text
            progress('正在读取选定作品的公开章节目录')
            options=job['options']
            raw=fetch_text(job['platform'],job['book_id'],self.store.directory,max_chapters=options['chapters'],start_chapter=options['start_chapter'],progress=progress)
            if not isinstance(raw,dict): raise AppError('invalid_text_result','正文结果格式不正确。')
            try: self._require()
            except AppError as exc:
                if not exc.code.startswith('membership_'): raise
                raw={**raw,'interrupted':True,'interrupted_reason':exc.code,'interrupted_message':exc.message,
                     'coverage_complete':False,'complete':False}
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
                if not str(raw.get('text') or '').strip() and (cancelled or raw.get('interrupted_reason')):
                    job.update(status='cancelled' if cancelled else 'interrupted',
                               error_code=raw.get('interrupted_reason'),message='正文下载已暂停，尚未取得可保存的章节。',finished_at=now())
                else:
                    text_meta=self.store.save_fetched_text(job['snapshot_id'],job['book_id'],raw)
                    label,detail=text_integrity_summary(text_meta)
                    message=('已暂停，已取得内容已保存：' if cancelled else f'{label}，正文已保存：')+text_meta['scope']
                    if detail: message+='；'+detail
                    job.update(status='cancelled' if cancelled else 'completed',partial=not text_meta.get('coverage_complete',False),
                               message=message,
                               text_id=text_meta['id'],text=text_meta,download_url=f"/api/texts/{text_meta['id']}/download",finished_at=now())
                    if raw.get('interrupted_reason'):
                        job.update(status='interrupted',partial=True,error_code=raw['interrupted_reason'],
                                   message=str(raw.get('interrupted_message') or '会员验证中断，已取得的正文已保存。')[:1200])
                self.store.save_job(job)
        except Exception as exc:
            with self.lock:
                if self.store.job(job['id'])['status'] in ('cancelling','cancelled'):
                    job.update(status='cancelled',message='正文下载已暂停，先前保存的正文仍保留。',finished_at=now())
                else:
                    message=getattr(exc,'message',None) or str(exc)
                    job.update(status='interrupted' if str(getattr(exc,'code','')).startswith('membership_') else 'failed',message=message[:1200] or '未取得有效正文，已有内容保留。',error_code=getattr(exc,'code','text_failed'),finished_at=now())
                self.store.save_job(job)
        finally:
            with self.lock:
                if self.active.get(job['scope_key'])==job['id']: self.active.pop(job['scope_key'],None)

    def _run(self,job):
        def progress(message,*args,**kwargs):
            with self.condition:
                current=self.store.job(job['id'])
                if current['status']=='cancelled': raise AppError('cancelled','已取消本次采集。')
                job.update({key:current[key] for key in ('interactive','demand_count','waiting_for_more','shared') if key in current})
                partial=kwargs.get('snapshot')
                if partial is not None:
                    if not isinstance(partial,dict) or partial.get('platform')!=job['platform']:
                        raise AppError('partial_scope_mismatch','增量榜单的平台与请求不一致。')
                    for field in ('channel','category','board'):
                        if partial.get(field)!=job['options'][field]:
                            raise AppError('partial_scope_mismatch','增量榜单的频道、分类或榜型与请求不一致。')
                    if job['options'].get('period') and partial.get('period')!=job['options']['period']:
                        raise AppError('partial_scope_mismatch','增量榜单的周期与请求不一致。')
                    snapshot=self.store.save_snapshot({**partial,'complete':False})
                    job.update(partial_snapshot_id=snapshot['id'],partial_count=snapshot['books_count'])
                self._require()
                job.update(status='running',message=str(message),updated_at=now()); self.store.save_job(job)
                # Only a genuine source-page boundary can pause network work.
                # Polling never renews the idle timer; an abandoned tab releases
                # its worker while retaining immutable visible snapshots.
                if partial is not None and kwargs.get('has_more') and job.get('interactive'):
                    deadline=time.monotonic()+INTERACTIVE_IDLE_SECONDS
                    while True:
                        self._require()
                        current=self.store.job(job['id'])
                        if current['status']=='cancelled': raise AppError('cancelled','已取消本次采集。')
                        job.update({key:current[key] for key in ('interactive','demand_count','waiting_for_more','shared') if key in current})
                        if not current.get('interactive') or current.get('demand_count',10)>job.get('partial_count',0):
                            job.update(waiting_for_more=False); self.store.save_job(job); break
                        remaining=deadline-time.monotonic()
                        if remaining<=0: raise AppError('paused_for_demand','已保留当前榜单，继续向下浏览时再读取。')
                        if not job.get('waiting_for_more'):
                            job.update(waiting_for_more=True,message=f"已显示 {job.get('partial_count',0)} 本，向下浏览继续读取")
                            self.store.save_job(job)
                        self.condition.wait(min(remaining,1))
        try:
            from .collectors import collect
            progress('正在连接平台')
            result=collect(job['platform'],job['options'],self.store.directory,progress)
            try: self._require()
            except AppError as exc:
                if not exc.code.startswith('membership_'): raise
                result={**result,'complete':False,'interrupted_reason':exc.code,'interrupted_message':exc.message}
            with self.lock:
                if self.store.job(job['id'])['status']=='cancelled': return
                snapshot=self.store.save_snapshot(result)
                job.update(status='completed',message=f"已保存 {snapshot['books_count']} 本作品",snapshot_id=snapshot['id'],finished_at=now(),waiting_for_more=False)
                result_auth=result.get('auth_state')
                if job['platform']=='heiyan' and result_auth in ('connected','logged_out','expired','forbidden','network_error','unavailable'):
                    auth_message=str(result.get('auth_message') or ('书库访问已确认。' if result_auth=='connected' else '本次只保存部分结果，请重新检查黑岩连接。'))[:1200]
                    self.store.auth_state('heiyan',{'state':result_auth,'message':auth_message})
                    job.update(auth_state=result_auth,auth_message=auth_message)
                    if result_auth!='connected':
                        job.update(partial=True,message=f"已保存 {snapshot['books_count']} 本有效作品；{auth_message}")
                source_state=result.get('source_state')
                if result.get('mode')=='overseas' and source_state in ('forbidden','source_access_denied','verification_required'):
                    source_message=str(result.get('source_message') or '来源拒绝继续读取，自动重试已暂停。')[:1000]
                    job.update(status='failed',partial=True,error_code=source_state,
                               message=f"已保存 {snapshot['books_count']} 本有效作品；{source_message}")
                if result.get('interrupted_reason'):
                    job.update(status='interrupted',partial=True,error_code=result['interrupted_reason'],
                               message=str(result.get('interrupted_message') or '会员验证中断，已取得榜单已保存。')[:1200])
                self.store.save_job(job)
        except Exception as exc:
            with self.lock:
                if self.store.job(job['id'])['status']!='cancelled':
                    message=getattr(exc,'message',None) or str(exc)
                    if getattr(exc,'code',None)=='paused_for_demand' and job.get('partial_snapshot_id'):
                        job.update(status='completed',snapshot_id=job['partial_snapshot_id'],partial=True,
                                   paused_for_demand=True,waiting_for_more=False,message=message,finished_at=now())
                        self.store.save_job(job)
                        return
                    auth_state=getattr(exc,'state',None)
                    if job['platform']=='heiyan' and auth_state in ('logged_out','expired','forbidden','network_error','unavailable'):
                        self.store.auth_state('heiyan',{'state':auth_state,'message':message[:1200]})
                    job.update(status='interrupted' if str(getattr(exc,'code','')).startswith('membership_') else 'failed',message=message[:1200] or '采集失败，请重试。',error_code=getattr(exc,'code',None) or auth_state or 'collection_failed',finished_at=now(),waiting_for_more=False)
                    self.store.save_job(job)
        finally:
            with self.lock:
                if self.active.get(job['scope_key'])==job['id']: self.active.pop(job['scope_key'],None)

    def cancel(self,jid):
        with self.lock:
            j=self.store.job(jid)
            if j['status'] in ('pending','running') or (j.get('kind')=='overview' and j.get('public_source',{}).get('status')=='running'):
                if j.get('kind')=='text':
                    j.update(status='cancelling',message='正在暂停正文下载，已取得内容将保留。',cancel_requested_at=now())
                else:
                    j.update(status='cancelled',message='已取消本次采集，已有结果保留。',finished_at=now())
                if j.get('public_source',{}).get('status')=='running':
                    j['public_source']={**j['public_source'],'status':'cancelled','finished_at':now()}
                self.store.save_job(j)
                if j.get('kind')=='overview' and j.get('owns_child') and j.get('active_child_id'):
                    if not self.store.job(j['active_child_id']).get('shared'): self.cancel(j['active_child_id'])
                self.condition.notify_all()
            return j

class WorkbenchServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,store=None,*,runtime=None):
        if store is not None and runtime is not None:
            raise ValueError('Use one application context')
        if store is None and runtime is None:
            from .runtime import WorkbenchRuntime
            runtime=WorkbenchRuntime(recover_jobs=True)
        super().__init__(address,Handler)
        # Explicit Store injection remains an internal test harness seam. The
        # executable entrypoint always constructs the protected runtime.
        self.runtime=runtime
        self.store=store
        self.jobs=JobManager(store) if store is not None else None
        self.directory=runtime.root_dir if runtime is not None else store.directory
        self.network_probe={'status':'not_checked'}
        if store is not None: self.store.recover_jobs()

    def membership_status(self,refresh=False):
        if self.runtime is not None: return self.runtime.membership.status(refresh=refresh)
        return {'allowed':False,'reason':'not_authenticated','account_id':None,'account':None,
                'generation':None,'membership_expires_at':None,'permit_expires_at':None,'server_time':now()}

    def start_network_probe(self, timeout=10):
        self.network_probe={'status':'checking'}
        lock=threading.Lock()
        def finish(result):
            with lock:
                if self.network_probe['status']=='checking': self.network_probe=result
        timer=threading.Timer(timeout,lambda:finish({'status':'failed','code':'probe_timeout','message':'启动网络探测超时；工作台已打开，实际连通以当前采集结果为准。'}))
        timer.daemon=True
        def check():
            from .collectors import probe_network
            try: result=probe_network()
            except Exception:
                result={'status':'failed','code':'diagnostic_error','message':'网络自检未完成，请查看当前采集结果。'}
            finish(result)
            timer.cancel()
        timer.start()
        threading.Thread(target=check,name='fagao-network-check',daemon=True).start()

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

    def _guard(self,write=False,callback=False,navigation=False):
        port=self.server.server_address[1]
        if self.headers.get('Host') not in (f'127.0.0.1:{port}',f'localhost:{port}'):
            raise AppError('invalid_host','工作台仅允许通过本机地址访问。',403)
        if callback:
            if self.headers.get('Host')!=f'127.0.0.1:{port}':
                raise AppError('invalid_host','授权回调地址与本次请求不一致。',403)
            return
        if navigation:
            # Browsers preserve cross-site metadata through the authorization
            # callback's redirect chain. A top-level document may load the
            # static shell only; all data still uses guarded same-origin APIs.
            return
        origin=self.headers.get('Origin')
        if origin and origin not in (f'http://127.0.0.1:{port}',f'http://localhost:{port}'):
            raise AppError('invalid_origin','拒绝来自其他网站的请求。',403)
        if self.headers.get('Sec-Fetch-Site')=='cross-site': raise AppError('cross_site','拒绝跨站请求。',403)
        if write and self.headers.get('X-Fagao-Request')!='1': raise AppError('missing_header','缺少工作台请求标记。',403)

    def do_GET(self): self._dispatch(False)
    def do_POST(self): self._dispatch(True)

    def _dispatch(self,write):
        self._context=None
        try:
            path=urlsplit(self.path).path
            callback=(not write and path=='/auth/callback' and self.server.runtime is not None)
            navigation=(not write and path in ('/','/index.html','/submissions.html')
                        and self.headers.get('Sec-Fetch-Mode')=='navigate'
                        and self.headers.get('Sec-Fetch-Dest')=='document')
            self._guard(write,callback=callback,navigation=navigation)
            if callback:
                self._authorization_callback()
                return
            submission_route = path in (SUBMISSION_POST if write else SUBMISSION_GET)
            if submission_route:
                # Require a non-simple request even for reads of local manuscripts.
                # Requests from a different loopback port/origin cannot share this entry.
                origin = self.headers.get('Origin')
                if (self.headers.get_all('X-Fagao-Request', []) != ['1']
                        or len(self.headers.get_all('Host', [])) != 1
                        or len(self.headers.get_all('Origin', [])) > 1
                        or (origin is not None and origin != 'http://' + self.headers['Host'])
                        or self.headers.get('Sec-Fetch-Site') not in (None, 'none', 'same-origin')):
                    raise AppError('submission_origin', '请在本机工作台中打开投稿。', 403)
            data={}
            if write:
                if self.headers.get('Content-Type','').split(';')[0]!='application/json': raise AppError('invalid_content_type','需要 JSON 请求。',415)
                try: length=int(self.headers.get('Content-Length','0'))
                except ValueError: raise AppError('invalid_length','请求长度不正确。')
                if length<0 or length>(1024 * 1024 if submission_route else MAX_BODY):
                    raise AppError('too_large','请求文件过大。',413)
                try: data=json.loads(self.rfile.read(length))
                except (ValueError,UnicodeDecodeError): raise AppError('invalid_json','JSON 格式无效。')
                if not isinstance(data,dict): raise AppError('invalid_body','请求必须是对象。')
            if submission_route:
                self._send(200, self._submission_action(path, data))
                return
            if self.server.runtime is not None and (
                    not write and path=='/api/legacy-data/status' or
                    write and path in ('/api/legacy-data/claim','/api/legacy-data/skip')):
                self._legacy_data_action(path,data)
                return
            public_get={'/api/health','/api/network','/api/membership/status'}
            public_post={'/api/membership/connect','/api/membership/refresh','/api/membership/logout'}
            protected=path.startswith('/api/') and path not in (public_post if write else public_get)
            cancelling=write and path=='/api/jobs/cancel'
            operation='read' if not write or path in ('/api/view','/api/insights','/api/resolve') else 'use'
            if protected and self.server.runtime is not None:
                expected_account,expected_generation=self._expected_identity(path)
                if cancelling: self._context=self.server.runtime.cancellation_context(expected_account,expected_generation)
                else: self._context=self.server.runtime.capture(operation=operation,expected_account=expected_account,expected_generation=expected_generation)
                if path=='/api/view':
                    from .legacy_claim import require_legacy_decision
                    require_legacy_decision(self.server.runtime,expected_account,expected_generation,operation='read')
            check=self._context.require if self._context is not None and not cancelling else None
            # Even a history-only request cannot accidentally initiate source
            # network work. Jobs always retain an active-use authorization.
            source_check=self._context.jobs._require if self._context is not None and not cancelling else None
            with network.authorized_requests(source_check):
                result=self._post(path,data) if write else self._get(path)
                if result is not None:
                    if check is not None: check()
                    self._send(200,result)
        except AppError as e: self._send(e.status,{'error':{'code':e.code,'message':e.message}})
        except Exception as e:
            if hasattr(e,'code') and hasattr(e,'message'):
                self._send(400,{'error':{'code':e.code,'message':e.message,'candidates':getattr(e,'candidates',[])}})
            else:
                self._send(500,{'error':{'code':'internal_error','message':'处理未完成，请重试或查看诊断。'}})
                import traceback; traceback.print_exc()

    def _submission_action(self, path, data):
        from .submissions import SubmissionDrafts, invalid
        drafts = SubmissionDrafts(self.server.directory)
        query = urlsplit(self.path).query
        if path == '/api/submissions/draft':
            try:
                params = parse_qs(query, keep_blank_values=True, strict_parsing=True, max_num_fields=1)
            except ValueError as exc:
                raise invalid('草稿编号不正确。') from exc
            if set(params) != {'id'} or len(params['id']) != 1:
                raise invalid('草稿编号不正确。')
            return {'draft': drafts.read({'id': params['id'][0]})}
        if query:
            raise invalid()
        if path == '/api/submissions/drafts':
            return {'drafts': drafts.summaries()}
        if path == '/api/submissions/save':
            return {'draft': drafts.save(data)}
        if path == '/api/submissions/delete':
            drafts.delete(data)
            return {'deleted': True}
        if path == '/api/submissions/open':
            return drafts.handoff(data)
        from .submission_component import component_status
        return component_status(self.server.directory)

    @property
    def _store(self):
        return self._context.store if self._context is not None else self.server.store

    @property
    def _jobs(self):
        return self._context.jobs if self._context is not None else self.server.jobs

    def _callback_uri(self):
        return f'http://127.0.0.1:{self.server.server_address[1]}/auth/callback'

    def _expected_identity(self,path,allow_anonymous=False):
        account=self.headers.get('X-Fagao-Account')
        generation=self.headers.get('X-Fagao-Generation')
        if (not account and not allow_anonymous) or not generation or len(account or '')>512 or len(generation)>512:
            raise AppError('membership_account_changed','请从当前账号的工作台重新发起操作。',403)
        if len(self.headers.get_all('X-Fagao-Account',[]))>1 or len(self.headers.get_all('X-Fagao-Generation',[]))>1:
            raise AppError('membership_account_changed','请求的账号信息不唯一。',403)
        return account or None,generation

    def _authorization_callback(self):
        try:
            params=parse_qs(urlsplit(self.path).query,keep_blank_values=True,strict_parsing=True,max_num_fields=8)
        except ValueError as exc:
            raise AppError('membership_invalid_callback','授权回调参数无效，请重新连接。',400) from exc
        if len(params.get('state',[]))!=1 or not params['state'][0] or len(params['state'][0])>2048:
            raise AppError('membership_invalid_callback','授权回调缺少唯一有效的参数。',400)
        if 'error' in params:
            if params['error']!=['access_denied'] or 'code' in params:
                raise AppError('membership_invalid_callback','授权回调状态无效，请重新连接。',400)
            self.server.runtime.membership.cancel_authorization(params['state'][0],self._callback_uri())
            self._send(303,b'','text/plain; charset=utf-8',{'Location':'/?authorization=cancelled'})
            return
        if len(params.get('code',[]))!=1 or not params['code'][0] or len(params['code'][0])>2048:
            raise AppError('membership_invalid_callback','授权回调缺少唯一有效的参数。',400)
        self.server.runtime.membership.complete_authorization(params['code'][0],params['state'][0],self._callback_uri())
        # Never leave the authorization code in the page URL or return it as
        # JSON. No third-party resources are loaded on this callback response.
        self._send(303,b'','text/plain; charset=utf-8',{'Location':'/'})

    def _legacy_data_action(self,path,data):
        from .legacy_claim import legacy_decision,claim_legacy,skip_legacy
        account,generation=self._expected_identity(path)
        runtime=self.server.runtime
        # No account Store is constructed merely to inspect legacy availability.
        runtime.membership.require(account_id=account,generation=generation,operation='use')
        options={'expected_account':account,'expected_generation':generation}
        if path.endswith('/status'):
            result=legacy_decision(runtime,**options)
        elif path.endswith('/claim'):
            result=claim_legacy(runtime,confirmed=data.get('confirmed') is True,**options)
        else:
            result=skip_legacy(runtime,confirmed=data.get('confirmed') is True,**options)
        runtime.membership.require(account_id=account,generation=generation,operation='use')
        self._send(200,result)

    def _get(self,path):
        store=self._store
        if path=='/api/health': return {'app':'fagao-scan-workbench','version':__version__,'data_dir':str(self.server.directory),'source_root':str(Path(__file__).resolve().parent.parent),'network':{**network.status(),'probe':self.server.network_probe}}
        if path=='/api/network': return {**network.status(),'probe':self.server.network_probe}
        if path=='/api/membership/status': return self.server.membership_status()
        if path == '/api/daily-boards':
            from .daily_feeds import read_cache
            if self._context is None:
                raise AppError('membership_unavailable', '请连接当前工作台账号。', 403)
            params = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            if len(params.get('board', [])) != 1 or len(params.get('revision', [])) > 1:
                raise AppError('daily_feed_parameters', '请选择一个榜单和版本。', 400)
            return read_cache(store, params['board'][0], params.get('revision', [None])[0])
        if path=='/api/state': return store.state()
        if path=='/api/overview': return self._jobs._overview_data() if self._context is not None else self._jobs.overview()
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
            label,detail=text_integrity_summary(record)
            integrity_header=f'正文状态：{label}\n'+(f'完整性核验：{detail}\n' if detail else '')
            content=f"《{record['title']}》\n阅读范围：{record['scope']}\n{integrity_header}来源：{record['source']}\n作品：{record['platform']} / {record['book_id']}\n\n"+record['content']
            if self._context is not None: self._context.require()
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
        if path=='/api/membership/refresh': return self.server.membership_status(refresh=True)
        if path in ('/api/membership/connect','/api/membership/logout'):
            if self.server.runtime is None:
                raise AppError('membership_unavailable','隔离测试服务不提供真实账号连接。',503)
            account,generation=self._expected_identity(path,allow_anonymous=True)
            if path.endswith('/connect'):
                return self.server.runtime.membership.begin_authorization(self._callback_uri(),
                    expected_account=account,expected_generation=generation)
            # The core verifies and mutates the captured generation atomically.
            # A newer account must never be cancelled by this request.
            result=self.server.runtime.membership.logout(expected_account=account,expected_generation=generation)
            self.server.runtime.stop_account_jobs(account,generation)
            return result
        store=self._store
        if path == '/api/daily-boards/sync':
            from .daily_feeds import sync_board
            if self._context is None:
                raise AppError('membership_unavailable', '请连接当前工作台账号。', 403)
            return sync_board(self._context, self.server.runtime.membership, data.get('board'))
        if path=='/api/translation-context':
            return store.translation_context(reference=data.get('query','翻译这本'),session=data.get('session','default'),snapshot_id=data.get('snapshot_id'),book_id=data.get('book_id'))
        if path=='/api/catalog':
            snapshot,book=store.book(data.get('snapshot_id'),data.get('book_id'))
            if snapshot['platform'] not in ('fanqie','qidian','ciweimao'): raise AppError('unsupported_text_source','此平台暂未接入在线目录，可导入原文。')
            from .collectors import CollectorError, fetch_catalog
            try: result=fetch_catalog(snapshot['platform'],book['id'],store.directory)
            except CollectorError as exc: raise AppError('catalog_unavailable',str(exc)[:1200],502) from exc
            if str(result.get('book_id'))!=book['id'] or result.get('platform')!=snapshot['platform']: raise AppError('text_identity_mismatch','目录作品身份不一致，未采用。')
            return {**result,'snapshot_id':snapshot['id'],'book_id':book['id'],'total':result.get('catalog_chapters',len(result.get('chapters',[]))),'source_url':result.get('source','')}
        if path=='/api/fetch-text': return self._jobs.start_text(data.get('snapshot_id'),data.get('book_id'),data.get('chapters',3),data.get('start_chapter',1),data.get('all',False))
        if path=='/api/insights':
            from .insights import build_insights
            snapshot=store.snapshot(data.get('snapshot_id'))
            return build_insights(snapshot,store.state(full=True)['snapshots'])
        if path=='/api/view': return store.set_view(data)
        if path=='/api/favorite': return store.toggle_favorite(data.get('snapshot_id'),data.get('book_id'),bool(data.get('enabled')))
        if path=='/api/collect':
            args=(data.get('platform'),data.get('options',{}),data.get('automatic',False))
            return self._jobs.start(*args,interactive=data['interactive']) if 'interactive' in data else self._jobs.start(*args)
        if path=='/api/overview/refresh': return self._jobs.start_overview(data.get('automatic',False))
        if path=='/api/jobs/more': return self._jobs.more(data.get('job_id'),data.get('count',100))
        if path=='/api/jobs/cancel':
            result=self._jobs.cancel(data.get('job_id'))
            return {'id':result['id'],'status':result['status']} if self.server.runtime is not None else result
        if path=='/api/auth':
            if self.server.runtime is not None:
                raise AppError('external_link','黑岩书单在腾讯文档中打开，请在普通浏览器登录。',400)
            from .collectors import auth
            p=data.get('platform'); action=data.get('action')
            if p!='heiyan' or action not in ('login','check'): raise AppError('invalid_auth','仅支持黑岩登录或连接检查。')
            return store.auth_state(p,auth(p,action,store.directory))
        if path=='/api/import/snapshot': return store.save_snapshot(data.get('snapshot'),imported=True)
        if path=='/api/import/overseas-html':
            if data.get('platform')!='scribblehub':
                raise AppError('unsupported_platform','HTML 榜单导入当前支持 Scribble Hub 周收藏榜。')
            from .scribble_import import parse_saved_ranking
            from .collectors import CollectorError
            try: snapshot=parse_saved_ranking(data.get('html'),data.get('source_url'))
            except CollectorError as exc: raise AppError('invalid_ranking_html',str(exc)) from exc
            return store.save_snapshot(snapshot,imported=True)
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
    parser.add_argument('--defer-network-probe',action='store_true',
                        help='直接打开本机投稿时无需联系榜单来源。')
    args=parser.parse_args()
    from .runtime import WorkbenchRuntime
    runtime=WorkbenchRuntime(args.data_dir,recover_jobs=True)
    network.configure(runtime.root_dir)
    server=WorkbenchServer(('127.0.0.1',args.port),runtime=runtime)
    if not args.defer_network_probe:
        server.start_network_probe()
    print(f'发糕扫榜已启动：http://127.0.0.1:{server.server_address[1]}',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__=='__main__': main()
