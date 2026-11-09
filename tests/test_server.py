import http.client
import json
import tempfile
import threading
import unittest
from fagao.server import WorkbenchServer
from fagao.store import Store

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

if __name__=='__main__': unittest.main()
