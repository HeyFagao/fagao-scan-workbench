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

if __name__=='__main__': unittest.main()
