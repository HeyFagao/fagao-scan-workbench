# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
"""Isolated fixture API + actual frontend DOM integration; requires optional jsdom."""
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fagao.store import Store
from fagao.server import WorkbenchServer
from fagao.runtime import WorkbenchRuntime
from membership_fixtures import SyntheticMembership
with tempfile.TemporaryDirectory() as directory, patch('fagao.public_rankings.sync_public_rankings', return_value={'snapshots':[]}), patch('fagao.network._Opener.open', side_effect=AssertionError('UI regression must not access an external service')):
    runtime=WorkbenchRuntime(directory,SyntheticMembership(directory))
    store=runtime.capture().store
    s=store.save_snapshot({'platform':'fanqie','channel':'女频','category':'古言脑洞','board':'新书榜','books':[
      {'id':'qa-a','title':'测试作品甲','rank':2,'summary':'隔离测试样本，非真实榜单'},
      {'id':'qa-b','title':'测试作品乙','rank':14,'summary':'这是独有简介，仅用于验收'},
      {'id':'qa-c','title':'测试作品丙','rank':26,'summary':'另一个测试样本'},
    ]},imported=True)
    store.set_view({'mode':'long','platform':'fanqie','snapshot_id':s['id']})
    t=store.prepare_task({'snapshot_id':s['id'],'book_id':'qa-b'},'验收报告')
    store.save_report(t['id'],'这是隔离测试报告，不包含真实作品分析。<script>throw new Error("unsafe")</script>')
    server=WorkbenchServer(('127.0.0.1',0),runtime=runtime)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    env=dict(os.environ,FAGAO_TEST_URL='http://127.0.0.1:'+str(server.server_port))
    try:
        result=subprocess.run(['node','tests/ui-smoke.cjs'],env=env,cwd=Path(__file__).resolve().parents[1],timeout=90)
        code=result.returncode
    finally:server.shutdown();server.server_close();thread.join()
    sys.exit(code)
