import http.client
import concurrent.futures
import json
import tempfile
import threading
import unittest
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from fagao.server import WorkbenchServer, JobManager, collection_scope
from fagao.store import Store, AppError


def fragmented_single_chapter(book_id='book-a',title='隔离测试书目'):
    """Regression shape: one published chapter, but only its opening returned."""
    chapter_integrity={'version':1,'status':'partial','declared_word_count':13325,'actual_characters':4439,
                       'reason':'平台标注 13325 字，实际返回 4439 个非空白字符。'}
    return {'platform':'fanqie','book_id':book_id,'title':title,'text':'文'*4439,
            'scope':'公开目录第 1 项，共 1 章','source':'https://fanqienovel.com/page/'+book_id,'status':'已完结',
            'complete':False,'coverage_complete':False,'cancelled':False,'catalog_chapters':1,'catalog_complete':True,
            'requested_start':1,'requested_chapters':1,
            'chapters':[{'index':1,'id':'fragment-1','item_id':'fragment-1','title':'第一章','characters':4439,'integrity':chapter_integrity}],
            'integrity':{'version':1,'status':'partial','declared_book_word_count':13325,'actual_characters':4439,
                         'reason':'单章正文少于平台标注字数，已保存公开片段。'},'missing':[]}


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.store=Store(cls.tmp.name)
        cls.server=WorkbenchServer(('127.0.0.1',0),cls.store)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True); cls.thread.start()
        cls.port=cls.server.server_address[1]
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join(); cls.tmp.cleanup()
    def request(self,path,method='GET',body=None,headers=None):
        c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=10)
        if isinstance(body,dict): body=json.dumps(body)
        c.request(method,path,body=body,headers=headers or {})
        r=c.getresponse(); b=r.read(); h=dict(r.getheaders()); status=r.status; c.close()
        try:b=json.loads(b)
        except (ValueError,UnicodeDecodeError):pass
        return status,b,h
    def post(self,path,payload):
        return self.request(path,'POST',payload,{'Content-Type':'application/json','X-Fagao-Request':'1'})
    def test_health_identity(self):
        status,body,_=self.request('/api/health')
        self.assertEqual(status,200); self.assertEqual(body['app'],'fagao-scan-workbench')
        self.assertEqual(body['data_dir'],str(self.store.directory))
    def test_untrusted_host_rejected(self):
        self.assertEqual(self.request('/api/context',headers={'Host':'evil.example'})[0],403)
    def test_cross_site_read_rejected(self):
        self.assertEqual(self.request('/api/context',headers={'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.request('/api/context',headers={'Sec-Fetch-Site':'cross-site'})[0],403)
    def test_cross_site_write_rejected_even_with_marker(self):
        status,_,_=self.request('/api/config','POST',{}, {'Content-Type':'application/json','X-Fagao-Request':'1','Origin':'https://evil.example'})
        self.assertEqual(status,403)
    def test_marker_required(self):
        self.assertEqual(self.request('/api/config','POST',{}, {'Content-Type':'application/json'})[0],403)
    def test_bad_json_or_shape_returns_client_error(self):
        headers={'Content-Type':'application/json','X-Fagao-Request':'1'}
        self.assertEqual(self.request('/api/config','POST','{',headers)[0],400)
        self.assertEqual(self.request('/api/config','POST','[]',headers)[0],400)
    def test_non_json_type_rejected(self):
        self.assertEqual(self.request('/api/config','POST','x',{'X-Fagao-Request':'1','Content-Type':'text/plain'})[0],415)
    def test_path_traversal_cannot_read_source_or_database(self):
        for p in ('/../fagao/store.py','/../../../../etc/passwd','/%2e%2e/fagao/store.py','/api/../fagao/store.py','//etc/passwd'):
            status,body,_=self.request(p)
            self.assertNotEqual(status,200,p)
    def test_no_cross_origin_cors_and_unsafe_frames(self):
        status,_,headers=self.request('/api/health')
        self.assertEqual(status,200)
        self.assertNotIn('Access-Control-Allow-Origin',headers)
        self.assertEqual(headers['X-Frame-Options'],'DENY')
    def test_cover_csp_allows_http_images_but_not_data_or_script_images(self):
        status,_,headers=self.request('/api/health')
        self.assertEqual(status,200)
        img=headers['Content-Security-Policy'].split('img-src ',1)[1].split(';',1)[0]
        self.assertIn('https:',img); self.assertIn('http:',img)
        self.assertNotIn('data:',img); self.assertNotIn('javascript:',img)

    def test_collect_passes_automatic_and_preserves_response_contract(self):
        expected={'status':'fresh','snapshot_id':'snap','retry_after_seconds':880}
        with patch.object(self.server.jobs,'start',return_value=expected) as start:
            status,body,_=self.post('/api/collect',{'platform':'fanqie','options':{'board':'阅读榜'},'automatic':True})
        self.assertEqual(status,200); self.assertEqual(body,expected)
        start.assert_called_once_with('fanqie',{'board':'阅读榜'},True)

    def test_interactive_collect_and_load_more_contract(self):
        expected={'status':'started','job_id':'scroll-job','job':{'kind':'collect'}}
        with patch.object(self.server.jobs,'start',return_value=expected) as start:
            status,body,_=self.post('/api/collect',{'platform':'fanqie','options':{'board':'新书榜'},'automatic':True,'interactive':True})
        self.assertEqual((status,body),(200,expected))
        start.assert_called_once_with('fanqie',{'board':'新书榜'},True,interactive=True)
        with patch.object(self.server.jobs,'more',return_value=expected) as more:
            status,body,_=self.post('/api/jobs/more',{'job_id':'scroll-job','count':100})
        self.assertEqual((status,body),(200,expected)); more.assert_called_once_with('scroll-job',100)

    def test_overview_endpoints_keep_reading_boards_out_of_options(self):
        expected={'scope_label':'番茄新书榜与其他平台','platforms':[],'update':None}
        with patch.object(self.server.jobs,'overview',return_value=expected):
            status,body,_=self.request('/api/overview')
        self.assertEqual((status,body),(200,expected))
        reply={'status':'started','job_id':'overview-job','job':{'kind':'overview'}}
        with patch.object(self.server.jobs,'start_overview',return_value=reply) as refresh:
            status,body,_=self.post('/api/overview/refresh',{'automatic':True,'options':{'board':'阅读榜'}})
        self.assertEqual((status,body),(200,reply)); refresh.assert_called_once_with(True)

    def test_heiyan_expired_collection_updates_auth_and_preserves_snapshot(self):
        import time
        from unittest.mock import patch
        from fagao.collectors import CollectorError
        old=self.store.save_snapshot({'platform':'heiyan','books':[{'id':'h1','title':'既有书库记录'}]})
        self.store.auth_state('heiyan',{'state':'connected','message':'先前连接有效'})
        with patch('fagao.collectors.collect',side_effect=CollectorError('过期','expired')):
            status,result,_=self.post('/api/collect',{'platform':'heiyan','options':{}})
            self.assertEqual(status,200)
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                job=self.store.job(result['job_id'])
                if job['status']=='failed': break
                time.sleep(0.01)
            self.assertEqual(job['status'],'failed')
        state=self.store.state()
        self.assertEqual(state['auth']['heiyan']['state'],'expired')
        self.assertEqual(state['auth']['heiyan']['message'],'过期')
        self.assertEqual(self.store.snapshot(old['id'])['books'][0]['id'],'h1')
        self.assertTrue(any(s['id']==old['id'] for s in state['snapshots']))
    def test_catalog_is_bound_to_explicit_snapshot_and_book(self):
        snap=self.store.save_snapshot({'platform':'fanqie','books':[{'id':'cat-book','title':'目录测试'}]})
        result={'platform':'fanqie','book_id':'cat-book','title':'目录测试','source':'https://fanqienovel.com/page/cat-book','catalog_chapters':1,'chapters':[{'index':1,'item_id':'chapter-1','title':'第一章'}]}
        with patch('fagao.fanqie_content.catalog',return_value=result) as catalog:
            status,body,_=self.post('/api/catalog',{'snapshot_id':snap['id'],'book_id':'cat-book'})
        self.assertEqual(status,200); self.assertEqual(body['snapshot_id'],snap['id']); self.assertEqual(body['total'],1)
        self.assertEqual(body['source_url'],result['source']); catalog.assert_called_once_with('cat-book',self.store.directory)
        with patch('fagao.fanqie_content.catalog',return_value={**result,'book_id':'another'}):
            self.assertEqual(self.post('/api/catalog',{'snapshot_id':snap['id'],'book_id':'cat-book'})[0],400)

    def test_catalog_unavailable_returns_a_specific_retryable_error(self):
        from fagao.collectors import CollectorError
        snap=self.store.save_snapshot({'platform':'fanqie','books':[{'id':'cat-failed','title':'目录失败测试'}]})
        with patch('fagao.fanqie_content.catalog',side_effect=CollectorError('目录暂时无法读取，请稍后重试。')):
            status,body,_=self.post('/api/catalog',{'snapshot_id':snap['id'],'book_id':'cat-failed'})
        self.assertEqual(status,502); self.assertEqual(body['error']['code'],'catalog_unavailable')
        self.assertIn('目录暂时无法读取',body['error']['message'])

    def test_text_download_is_utf8_attachment_from_asset_not_arbitrary_path(self):
        snap=self.store.save_snapshot({'platform':'fanqie','books':[{'id':'txt-book','title':'书名/\\..\r\n危险'}]})
        meta=self.store.import_text(snap['id'],'txt-book','可下载的已存正文。'*10,'前三章')
        status,body,headers=self.request('/api/texts/'+meta['id']+'/download')
        self.assertEqual(status,200); self.assertIn('可下载的已存正文',body.decode('utf-8'))
        self.assertIn('阅读范围：前三章',body.decode('utf-8'))
        disposition=headers['Content-Disposition']
        self.assertTrue(disposition.startswith('attachment;'))
        self.assertIn("filename*=UTF-8''",disposition)
        self.assertNotIn('\r',disposition); self.assertNotIn('\n',disposition)
        self.assertEqual(headers['Content-Type'],'text/plain; charset=utf-8')
        self.assertEqual(self.request('/api/texts/../../etc/passwd/download')[0],404)

    def test_text_job_request_forwards_fixed_range(self):
        expected={'status':'started','job_id':'text-job','job':{'id':'text-job','kind':'text'}}
        with patch.object(self.server.jobs,'start_text',return_value=expected) as start:
            status,body,_=self.post('/api/fetch-text',{'snapshot_id':'sid','book_id':'bid','chapters':10,'start_chapter':4})
        self.assertEqual(status,200); self.assertEqual(body,expected)
        start.assert_called_once_with('sid','bid',10,4,False)
        with patch.object(self.server.jobs,'start_text',return_value=expected) as start:
            self.post('/api/fetch-text',{'snapshot_id':'sid','book_id':'bid','all':True})
        start.assert_called_once_with('sid','bid',3,1,True)

    def test_single_chapter_fragment_export_carries_real_scope_and_counts(self):
        snap=self.store.save_snapshot({'platform':'fanqie','books':[{'id':'fragment-export','title':'单章片段导出'}]})
        meta=self.store.save_fetched_text(snap['id'],'fragment-export',fragmented_single_chapter('fragment-export','单章片段导出'))
        status,record,_=self.request('/api/texts/'+meta['id'])
        self.assertEqual(status,200)
        self.assertFalse(record['complete']); self.assertFalse(record['coverage_complete'])
        self.assertEqual(record['integrity']['actual_characters'],4439)
        status,body,_=self.request('/api/texts/'+meta['id']+'/download')
        self.assertEqual(status,200)
        exported=body.decode('utf-8'); header,content=exported.split('\n\n',1)
        self.assertIn('公开片段',header)
        self.assertIn('实际正文 4,439 字符（不含空白）',header)
        self.assertIn('作品标注 13,325 字',header)
        self.assertNotIn('已核实为作品完整正文',header)
        self.assertEqual(content,'文'*4439)

    def test_legacy_export_distinguishes_saved_text_from_body_character_count(self):
        record={'id':'legacy-export','title':'历史正文','scope':'旧下载文本，完整性待重新核验','source':'公开章节页面',
                'platform':'fanqie','book_id':'legacy-book','content':'第一章\n'+'文'*100,
                'acquisition':'public_download','complete':False,'coverage_complete':False,
                'integrity':{'version':1,'status':'unverified','actual_characters':103,
                             'actual_characters_basis':'saved_text_including_headings','declared_book_word_count':None,
                             'reason':'旧记录未保存章节字数核验信息。'}}
        with patch.object(self.store,'text_record',return_value=record):
            status,body,_=self.request('/api/texts/legacy-export/download')
        self.assertEqual(status,200)
        header=body.decode('utf-8').split('\n\n',1)[0]
        self.assertIn('完整性未确认',header)
        self.assertIn('已保存文本 103 字符（不含空白，含章节标题）',header)
        self.assertNotIn('实际正文',header)

    def test_insights_use_local_snapshots_and_preserve_current_view(self):
        snap=self.store.save_snapshot({'platform':'fanqie','books':[{'id':'insight-book','title':'重生测试'}]})
        before=self.store.state()['views']
        with patch('fagao.insights.build_insights',return_value={'snapshot_id':snap['id'],'sample':{'count':1}}) as analyze:
            status,body,_=self.post('/api/insights',{'snapshot_id':snap['id']})
        self.assertEqual(status,200); self.assertEqual(body['sample']['count'],1)
        self.assertEqual(analyze.call_args.args[0]['id'],snap['id'])
        self.assertTrue(any(s['id']==snap['id'] for s in analyze.call_args.args[1]))
        self.assertEqual(self.store.state()['views'],before)

    def test_real_import_select_resolve_report_flow(self):
        payload={'platform':'fanqie','channel':'女频','category':'古言脑洞','board':'新书榜','books':[{'id':'b1','title':'测试一','rank':7},{'id':'b2','title':'测试二','rank':14}]}
        status,s,_=self.post('/api/import/snapshot',{'snapshot':payload}); self.assertEqual(status,200)
        status,_,_=self.post('/api/view',{'mode':'long','platform':'fanqie','snapshot_id':s['id'],'selected_id':'b2'}); self.assertEqual(status,200)
        status,r,_=self.post('/api/resolve',{'query':'分析第二本'}); self.assertEqual(status,200); self.assertEqual(r['book_id'],'b2')
        status,_,_=self.post('/api/import/text',{'snapshot_id':s['id'],'book_id':'b2','text':'真实导入的隔离测试正文。'*10,'scope':'测试段落','complete':False}); self.assertEqual(status,200)
        status,t,_=self.post('/api/prepare',{'query':'分析这本'}); self.assertEqual(status,200); self.assertEqual(t['scope'],'测试段落')
        status,r,_=self.post('/api/reports',{'task_id':t['id'],'content':'这是用于验证记录回写的测试分析文本。'*10}); self.assertEqual(status,200)
        status,report,_=self.request('/api/reports/'+r['id']); self.assertEqual(status,200); self.assertEqual(report['book_id'],'b2')
        self.assertIn('阅读范围：测试段落',report['content'])

class CollectionSchedulingTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(self.tmp.name)
        self.manager=JobManager(self.store)
        self.release=threading.Event()
        self.options={'channel':'女频','category':'古言脑洞','board':'新书榜'}

    def tearDown(self):
        self.release.set()
        deadline=time.monotonic()+5
        while self.manager.active and time.monotonic()<deadline: time.sleep(0.01)
        self.tmp.cleanup()

    def snapshot(self,platform='fanqie',options=None):
        options=options or self.options
        return {'platform':platform,'channel':options.get('channel','女频'),'category':options.get('category','古言脑洞'),'board':options.get('board','新书榜'),'books':[{'id':'book-a','title':'隔离测试书目','rank':1,'cover_url':'https://cdn.example.com/a.jpg'}]}

    def wait(self,jid):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            with self.manager.lock:
                job=self.store.job(jid)
                active=jid in self.manager.active.values()
            if not active and job['status'] in ('completed','failed','cancelled'): return job
            time.sleep(0.01)
        self.fail('mock collection did not finish')

    def persisted(self,status,age=0,options=None):
        key,effective=collection_scope('fanqie',options or self.options)
        stamp=(datetime.now(timezone.utc)-timedelta(seconds=age)).isoformat()
        job={'id':'persisted-'+status+'-'+str(age),'platform':'fanqie','scope_key':key,'options':effective,'status':status,'created_at':stamp,'finished_at':stamp,'message':'测试任务'}
        if status=='completed': job['snapshot_id']=self.store.save_snapshot(self.snapshot(options=options))['id']
        self.store.save_job(job)
        return job

    def test_default_aliases_and_json_key_order_share_scope(self):
        first,_=collection_scope('fanqie',{})
        second,_=collection_scope('fanqie',{'mode':'long','channel':'female','category':'古言脑洞','board':'new','limit':None,'max_pages':100,'detail_budget':None})
        self.assertEqual(first,second)
        self.assertEqual(collection_scope('ciweimao',{})[0],collection_scope('ciweimao',{'channel':'全部','board':'月票榜','period':'月榜'})[0])
        for changed in ({'channel':'男频'},{'category':'现代言情'},{'board':'阅读榜'},{'limit':200},{'max_pages':2},{'detail_budget':20}):
            self.assertNotEqual(first,collection_scope('fanqie',changed)[0])
        self.assertNotEqual(first,collection_scope('qidian',{})[0])

    def test_cross_tab_simultaneous_requests_only_start_one_collector(self):
        entered=threading.Event()
        def collector(platform,options,*args):
            entered.set(); self.release.wait(5)
            return self.snapshot(platform,options)
        with patch('fagao.collectors.collect',side_effect=collector) as collect:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                replies=list(pool.map(lambda _:self.manager.start('fanqie',dict(self.options),True),range(12)))
            self.assertTrue(entered.wait(1))
            self.assertEqual(len({r['job_id'] for r in replies}),1)
            self.assertEqual(sum(r['status']=='started' for r in replies),1)
            self.assertEqual(sum(r['status']=='busy' for r in replies),11)
            self.assertEqual(collect.call_count,1)
            manual=self.manager.start('fanqie',self.options,False)
            self.assertEqual(manual['status'],'busy')
            self.release.set(); job=self.wait(replies[0]['job_id'])
        self.assertEqual(job['status'],'completed')

    def test_different_scopes_on_same_platform_are_not_confused(self):
        def collector(platform,options,*args):
            self.release.wait(5); return self.snapshot(platform,options)
        with patch('fagao.collectors.collect',side_effect=collector) as collect:
            first=self.manager.start('fanqie',self.options,True)
            second=self.manager.start('fanqie',{**self.options,'board':'阅读榜'},True)
            self.assertEqual((first['status'],second['status']),('started','started'))
            self.assertNotEqual(first['job_id'],second['job_id'])
            self.release.set(); self.wait(first['job_id']); self.wait(second['job_id'])
            self.assertEqual(collect.call_count,2)

    def test_automatic_fresh_uses_persisted_exact_scope_and_keeps_selection(self):
        old=self.store.save_snapshot(self.snapshot())
        self.store.set_view({'mode':'long','platform':'fanqie','snapshot_id':old['id'],'selected_id':'book-a'})
        completed=self.persisted('completed',age=899)
        restarted=JobManager(Store(self.tmp.name))
        with patch('fagao.collectors.collect') as collect:
            response=restarted.start('fanqie',self.options,True)
            self.assertEqual(response['status'],'fresh')
            self.assertEqual(response['snapshot_id'],completed['snapshot_id'])
            self.assertGreater(response['retry_after_seconds'],0)
            collect.assert_not_called()
        self.assertEqual(self.store.state()['views']['long']['snapshot_id'],old['id'])

    def test_automatic_stale_starts_and_manual_bypasses_fresh(self):
        self.persisted('completed',age=901)
        with patch('fagao.collectors.collect',side_effect=lambda p,o,*a:self.snapshot(p,o)) as collect:
            reply=self.manager.start('fanqie',self.options,True)
            self.assertEqual(reply['status'],'started'); self.wait(reply['job_id'])
            self.assertEqual(self.manager.start('fanqie',self.options,True)['status'],'fresh')
            manual=self.manager.start('fanqie',self.options,False)
            self.assertEqual(manual['status'],'started'); self.wait(manual['job_id'])
            self.assertEqual(collect.call_count,2)

    def test_failed_automatic_requests_cool_down_across_restart_manual_can_retry(self):
        with patch('fagao.collectors.collect',side_effect=RuntimeError('网络不可达')) as collect:
            initial=self.manager.start('fanqie',self.options,True)
            self.assertEqual(self.wait(initial['job_id'])['status'],'failed')
            restarted=JobManager(Store(self.tmp.name))
            for _ in range(5):
                reply=restarted.start('fanqie',self.options,True)
                self.assertEqual(reply['status'],'cooldown')
                self.assertGreaterEqual(reply['retry_after_seconds'],299)
                self.assertEqual(reply['job']['id'],initial['job_id'])
            self.assertEqual(collect.call_count,1)
            manual=self.manager.start('fanqie',self.options,False)
            self.assertEqual(manual['status'],'started'); self.wait(manual['job_id'])
            self.assertEqual(collect.call_count,2)

    def test_failed_scope_does_not_cool_down_another_board(self):
        self.persisted('failed',age=1)
        with patch('fagao.collectors.collect',side_effect=lambda p,o,*a:self.snapshot(p,o)):
            reply=self.manager.start('fanqie',{**self.options,'board':'阅读榜'},True)
            self.assertEqual(reply['status'],'started'); self.wait(reply['job_id'])

    def test_cooldown_expires_after_five_minutes(self):
        self.persisted('failed',age=301)
        with patch('fagao.collectors.collect',side_effect=lambda p,o,*a:self.snapshot(p,o)):
            reply=self.manager.start('fanqie',self.options,True)
            self.assertEqual(reply['status'],'started'); self.wait(reply['job_id'])

    def test_recent_failure_precedes_older_fresh_success(self):
        self.persisted('completed',age=30); failed=self.persisted('failed',age=1)
        self.assertEqual(self.manager.start('fanqie',self.options,True)['status'],'cooldown')

    def test_partial_heiyan_session_failure_saves_books_and_requires_relogin(self):
        self.store.auth_state('heiyan',{'state':'connected','message':'之前已连接'})
        data=self.snapshot('heiyan',{'channel':'全部','category':'书库','board':'书库'})
        data.update(auth_state='expired',auth_message='登录已过期，请重新登录。',complete=False,
                    pagination={'pages_fetched':2,'max_pages':100,'stop_reason':'session_failed','source_total':None})
        with patch('fagao.collectors.collect',return_value=data) as collect:
            started=self.manager.start('heiyan',{},True)
            done=self.wait(started['job_id'])
            self.assertEqual(done['status'],'completed')
            self.assertTrue(done['partial']); self.assertEqual(done['auth_state'],'expired')
            self.assertEqual(self.store.snapshot(done['snapshot_id'])['books_count'],1)
            self.assertFalse(self.store.snapshot(done['snapshot_id'])['complete'])
            self.assertEqual(self.store.state()['auth']['heiyan']['state'],'expired')
            for _ in range(3):
                reply=self.manager.start('heiyan',{},True)
                self.assertEqual(reply['status'],'cooldown')
                self.assertTrue(reply['requires_login']); self.assertEqual(reply['auth']['state'],'expired')
            self.assertEqual(collect.call_count,1)
            # Once a real connection check succeeds, the partial snapshot must not
            # suppress continuation behind the normal 15-minute freshness cache.
            self.store.auth_state('heiyan',{'state':'connected','message':'重新连接成功'})
            resumed=self.manager.start('heiyan',{},True)
            self.assertEqual(resumed['status'],'started'); self.wait(resumed['job_id'])
            self.assertEqual(collect.call_count,2)

    def test_partial_heiyan_network_error_uses_failure_cooldown(self):
        self.store.auth_state('heiyan',{'state':'connected','message':'之前已连接'})
        data=self.snapshot('heiyan',{'channel':'全部','category':'书库','board':'书库'})
        data.update(auth_state='network_error',auth_message='第三页网络中断',complete=False)
        with patch('fagao.collectors.collect',return_value=data) as collect:
            started=self.manager.start('heiyan',{},True); self.wait(started['job_id'])
            reply=self.manager.start('heiyan',{},True)
            self.assertEqual(reply['status'],'cooldown')
            self.assertGreaterEqual(reply['retry_after_seconds'],299)
            self.assertEqual(collect.call_count,1)
            self.assertEqual(self.store.state()['auth']['heiyan']['state'],'network_error')

    def test_invalid_options_fail_before_background_work(self):
        for platform,options in [({},{}),('fanqie',[]),('fanqie',{'mode':'short'}),('fanqie',{'channel':{}}),('fanqie',{'max_pages':True}),('fanqie',{'max_pages':-1}),('fanqie',{'limit':'100'})]:
            with self.subTest(platform=platform,options=options):
                with self.assertRaises(AppError): self.manager.start(platform,options,True)
        with self.assertRaises(AppError): self.manager.start('fanqie',{},'true')
        self.assertEqual(self.store.state()['jobs'],[])

class TextDownloadJobsTest(unittest.TestCase):
    setUp=CollectionSchedulingTest.setUp
    tearDown=CollectionSchedulingTest.tearDown
    wait=CollectionSchedulingTest.wait
    snapshot=CollectionSchedulingTest.snapshot

    def raw(self,**changes):
        text='公开第一章的实际测试正文。'*10
        actual=len(text)
        result={'platform':'fanqie','book_id':'book-a','title':'隔离测试书目','text':text,
                'scope':'公开目录第 1 项，共 1 章','source':'https://fanqienovel.com/page/book-a','status':'连载中',
                'complete':False,'coverage_complete':True,'cancelled':False,'catalog_chapters':3,'catalog_complete':True,
                'requested_start':1,'requested_chapters':1,'chapters':[{'index':1,'id':'ch-a1','item_id':'ch-a1','title':'第一章','characters':actual,
                    'integrity':{'version':1,'status':'verified','declared_word_count':actual,'actual_characters':actual,'reason':'已核对章节字数。'}}],
                'integrity':{'version':1,'status':'verified','declared_book_word_count':None,'actual_characters':actual,'reason':'已核对请求章节字数。'},
                'missing':[],'collected_at':'2026-11-09T12:00:00+08:00'}
        result.update(changes); return result

    def test_text_job_is_pinned_and_deduplicated_without_blocking_collection(self):
        snap=self.store.save_snapshot(self.snapshot())
        other=self.store.save_snapshot({'platform':'fanqie','books':[{'id':'other','title':'其他书目'}]})
        entered=threading.Event()
        def fetch(book_id,data_dir,**kwargs):
            entered.set(); self.release.wait(5)
            return self.raw()
        with patch('fagao.fanqie_content.fetch_book_text',side_effect=fetch) as fetcher:
            first=self.manager.start_text(snap['id'],'book-a',1)
            self.assertTrue(entered.wait(1))
            second=self.manager.start_text(snap['id'],'book-a',1)
            self.assertEqual(second['status'],'busy'); self.assertEqual(first['job_id'],second['job_id'])
            self.store.set_view({'mode':'long','platform':'fanqie','snapshot_id':other['id'],'selected_id':'other'})
            self.release.set(); done=self.wait(first['job_id'])
            fetcher.assert_called_once()
        self.assertEqual(done['status'],'completed'); self.assertEqual(done['kind'],'text')
        self.assertEqual(done['snapshot_id'],snap['id']); self.assertEqual(done['book_id'],'book-a')
        self.assertEqual(done['text']['snapshot_id'],snap['id'])
        self.assertIn(done['text_id'],done['download_url'])
        self.assertEqual(self.store.state()['views']['long']['selected_id'],'other')
        self.assertNotIn('content',self.store.state()['texts'][0])

    def test_all_chapters_is_explicit_and_maps_to_none(self):
        snap=self.store.save_snapshot(self.snapshot())
        raw=self.raw(catalog_chapters=1)
        with patch('fagao.fanqie_content.fetch_book_text',return_value=raw) as fetcher:
            started=self.manager.start_text(snap['id'],'book-a',all_chapters=True)
            self.assertEqual(self.wait(started['job_id'])['status'],'completed')
            self.assertIsNone(fetcher.call_args.kwargs['max_chapters'])
            self.assertEqual(fetcher.call_args.kwargs['start_chapter'],1)
        self.assertFalse(self.store.state()['texts'][0]['complete'])

    def test_partial_download_is_saved_with_missing_range(self):
        snap=self.store.save_snapshot(self.snapshot())
        raw=self.raw(requested_chapters=3,coverage_complete=False,missing=[{'index':2,'id':'ch-a2','chapter':'第二章','reason':'需订阅'}])
        with patch('fagao.fanqie_content.fetch_book_text',return_value=raw):
            started=self.manager.start_text(snap['id'],'book-a',3)
            done=self.wait(started['job_id'])
        self.assertEqual(done['status'],'completed'); self.assertTrue(done['partial'])
        self.assertFalse(done['text']['coverage_complete']); self.assertFalse(done['text']['complete'])
        self.assertEqual(done['text']['missing'][0]['reason'],'需订阅')

    def test_single_chapter_fragment_job_uses_saved_integrity_not_directory_count(self):
        snap=self.store.save_snapshot(self.snapshot())
        with patch('fagao.fanqie_content.fetch_book_text',return_value=fragmented_single_chapter()):
            started=self.manager.start_text(snap['id'],'book-a',all_chapters=True)
            done=self.wait(started['job_id'])
        self.assertEqual(done['status'],'completed'); self.assertTrue(done['partial'])
        self.assertFalse(done['text']['complete']); self.assertFalse(done['text']['coverage_complete'])
        self.assertIn('公开片段',done['message'])
        self.assertIn('4,439',done['message']); self.assertIn('13,325',done['message'])
        self.assertNotIn('已核实为作品完整正文',done['message'])
        self.assertEqual(self.store.text_record(done['text_id'])['content'],'文'*4439)

    def test_cancel_keeps_already_obtained_text(self):
        snap=self.store.save_snapshot(self.snapshot()); entered=threading.Event()
        def fetch(book_id,data_dir,progress=None,**kwargs):
            entered.set(); self.release.wait(5)
            try: progress('开始下一章')
            except AppError as error: self.assertEqual(error.code,'cancelled')
            return self.raw(requested_chapters=3,coverage_complete=False,cancelled=True)
        with patch('fagao.fanqie_content.fetch_book_text',side_effect=fetch):
            started=self.manager.start_text(snap['id'],'book-a',3)
            self.assertTrue(entered.wait(1)); cancelling=self.manager.cancel(started['job_id'])
            self.assertEqual(cancelling['status'],'cancelling'); self.assertNotIn('finished_at',cancelling)
            self.release.set(); done=self.wait(started['job_id'])
        self.assertEqual(done['status'],'cancelled'); self.assertTrue(done['text']['cancelled'])
        self.assertEqual(len(self.store.state()['texts']),1)

    def test_cancel_before_first_chapter_finishes_without_empty_asset(self):
        snap=self.store.save_snapshot(self.snapshot()); entered=threading.Event()
        def fetch(book_id,data_dir,**kwargs):
            entered.set(); self.release.wait(5)
            return self.raw(text='',chapters=[],requested_chapters=3,coverage_complete=False,cancelled=True)
        with patch('fagao.fanqie_content.fetch_book_text',side_effect=fetch):
            started=self.manager.start_text(snap['id'],'book-a',3)
            self.assertTrue(entered.wait(1)); self.manager.cancel(started['job_id'])
            self.release.set(); done=self.wait(started['job_id'])
        self.assertEqual(done['status'],'cancelled'); self.assertIn('finished_at',done)
        self.assertNotIn('text_id',done); self.assertEqual(self.store.state()['texts'],[])

    def test_empty_or_wrong_book_text_never_succeeds(self):
        snap=self.store.save_snapshot(self.snapshot())
        for changes in ({'text':''},{'book_id':'wrong'},{'requested_start':2}):
            with self.subTest(changes=changes):
                with patch('fagao.fanqie_content.fetch_book_text',return_value=self.raw(**changes)):
                    started=self.manager.start_text(snap['id'],'book-a',1)
                    done=self.wait(started['job_id'])
                self.assertEqual(done['status'],'failed'); self.assertNotIn('text_id',done)
        self.assertEqual(self.store.state()['texts'],[])

    def test_download_rejects_invalid_ranges_and_unsupported_platform(self):
        snap=self.store.save_snapshot(self.snapshot())
        for count,start,all_flag in [(0,1,False),(5001,1,False),(True,1,False),(3,0,False),(3,1,'yes')]:
            with self.assertRaises(AppError): self.manager.start_text(snap['id'],'book-a',count,start,all_flag)
        other=self.store.save_snapshot(self.snapshot('qidian'))
        with self.assertRaises(AppError): self.manager.start_text(other['id'],'book-a')
        self.assertEqual(self.store.state()['jobs'],[])

if __name__=='__main__': unittest.main()
