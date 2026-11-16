# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
import hashlib
import contextlib
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from fagao import integrity

SOURCE = Path(__file__).resolve().parents[1]


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'app/releases' / ('a' * 40)
        self.root.mkdir(parents=True)
        self.write('sample.py', b'answer = 42\n')
        self.metadata = {'app':integrity.APP, 'release_id':self.root.name,
            'source':{'kind':'github','repo':integrity.REPOSITORY,'commit':self.root.name,'archive_sha256':'b'*64},
            'files':{'sample.py':hashlib.sha256((self.root/'sample.py').read_bytes()).hexdigest()},
            'runtime_integrity':1}
        self.receipt()

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def receipt(self):
        (self.root/'.fagao-release.json').write_text(json.dumps(self.metadata), encoding='utf-8')

    def test_checked_source_and_non_installed_development_directory(self):
        self.assertTrue(integrity.verify_installation(self.root)['official'])
        self.assertEqual(integrity.verify_installation(self.base)['status'], 'source_directory')

    def test_mutated_missing_and_extra_executable_are_rejected(self):
        for name in ('sample.py', 'unexpected.py', 'injected.pth', 'injected.pyd', 'injected.so'):
            with self.subTest(name=name):
                previous=(self.root/'sample.py').read_bytes()
                self.write(name, b'untrusted = True\n')
                with self.assertRaises(integrity.IntegrityError): integrity.verify_installation(self.root)
                if name=='sample.py':self.write(name, previous)
                else:(self.root/name).unlink()
        (self.root/'sample.py').unlink()
        with self.assertRaises(integrity.IntegrityError):integrity.verify_installation(self.root)

    def test_generated_cache_is_checked_against_source_not_blindly_ignored(self):
        source=self.root/'sample.py'
        cache=Path(py_compile.compile(str(source),doraise=True))
        integrity.verify_installation(self.root)
        malicious=self.base/'evil.py';malicious.write_text('raise RuntimeError("fixture")\n')
        py_compile.compile(str(malicious),cfile=str(cache),doraise=True)
        with self.assertRaises(integrity.IntegrityError):integrity.verify_installation(self.root)

    def test_untracked_or_sourceless_python_cache_is_rejected(self):
        self.write('__pycache__/extra.'+sys.implementation.cache_tag+'.pyc',b'not a trusted source cache')
        with self.assertRaises(integrity.IntegrityError):integrity.verify_installation(self.root)

    def test_bounded_file_manager_metadata_does_not_lock_a_normal_installation(self):
        self.write('.DS_Store',b'finder metadata fixture')
        self.write('notes/desktop.ini',b'shell metadata fixture')
        integrity.verify_installation(self.root)
        self.write('notes/untracked.py',b'print("unexpected executable")\n')
        with self.assertRaises(integrity.IntegrityError):integrity.verify_installation(self.root)

    def test_receipt_structure_path_collisions_and_size_are_bounded(self):
        for bad in ('../sample.py','/sample.py','sample.py/../x','dir\\x','a//b','a/./b','CON.py'):
            with self.subTest(path=bad):
                self.metadata['files']={bad:'0'*64};self.receipt()
                with self.assertRaises(integrity.IntegrityError):integrity.verify_installation(self.root)
        self.metadata['files']={'sample.py':'0'*64,'SAMPLE.py':'0'*64};self.receipt()
        with self.assertRaises(integrity.IntegrityError):integrity.verify_installation(self.root)
        (self.root/'.fagao-release.json').write_text(' '*(integrity.MAX_RECEIPT+1))
        with self.assertRaises(integrity.IntegrityError):integrity.verify_installation(self.root)

    def test_missing_receipt_cannot_turn_installed_release_into_development_mode(self):
        (self.root/'.fagao-release.json').unlink()
        with patch.dict(os.environ, {'FAGAO_SKIP_INTEGRITY':'1','FAGAO_DEV_MODE':'1'}):
            with self.assertRaises(integrity.IntegrityError):integrity.verify_installation(self.root)

    @unittest.skipIf(os.name=='nt','symlink privileges vary')
    def test_symbolic_link_in_an_installed_tree_is_rejected(self):
        (self.root/'link').symlink_to(self.base,target_is_directory=True)
        with self.assertRaises(integrity.IntegrityError):integrity.verify_installation(self.root)

    def test_multiple_readers_block_an_exclusive_repair_lease(self):
        with integrity.ReleaseLease(self.root),integrity.ReleaseLease(self.root):
            with self.assertRaises(integrity.IntegrityError) as caught:
                with integrity.ReleaseLease(self.root,exclusive=True):pass
            self.assertEqual(caught.exception.reason,'release_in_use')
        with integrity.ReleaseLease(self.root,exclusive=True):
            with self.assertRaises(integrity.IntegrityError):
                with integrity.ReleaseLease(self.root):pass

    def test_package_guard_runs_before_a_modified_application_module(self):
        for name in ('fagao/__init__.py','fagao/integrity.py'):
            self.write(name,(SOURCE/name).read_bytes())
        marker=self.base/'must-not-execute'
        self.write('fagao/runtime.py',b'# trusted fixture\n')
        self.metadata['files']={p.relative_to(self.root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in self.root.rglob('*') if p.is_file() and p.name!='.fagao-release.json'}
        self.receipt()
        self.write('fagao/runtime.py',('from pathlib import Path\nPath('+repr(str(marker))+').touch()\n').encode())
        result=subprocess.run([sys.executable,'-I','-B','-c',
            'import sys;sys.path.insert(0,'+repr(str(self.root))+');import fagao.runtime'],
            capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,1)
        self.assertEqual(json.loads(result.stderr)['error']['code'],'installation_integrity_failed')
        self.assertFalse(marker.exists())

    def package_fixture(self):
        for name in ('fagao/__init__.py','fagao/integrity.py'):
            self.write(name,(SOURCE/name).read_bytes())
        self.write('fagao/runtime.py',b'# trusted runtime fixture\n')
        self.write('fagao/__main__.py',b'def main(arguments):\n print("trusted CLI fixture")\n return 0\n')
        self.metadata['files']={p.relative_to(self.root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in self.root.rglob('*') if p.is_file() and p.name!='.fagao-release.json'}
        self.receipt()

    def inject_cached_marker(self,target,marker):
        original=target.stat()
        source=('from pathlib import Path\nPath('+repr(str(marker))+').touch()\nraise RuntimeError("untrusted cache")\n').encode()
        source+=b' '*(target.stat().st_size-len(source))
        malicious=self.base/'cached-fixture.py';malicious.write_bytes(source)
        os.utime(malicious,ns=(original.st_atime_ns,original.st_mtime_ns))
        cache=target.parent/'__pycache__'/(target.stem+'.'+sys.implementation.cache_tag+'.pyc')
        cache.parent.mkdir(exist_ok=True)
        py_compile.compile(str(malicious),cfile=str(cache),dfile=str(target),doraise=True,
            invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)

    def test_verifier_cache_is_not_executed_before_package_guard(self):
        self.package_fixture();marker=self.base/'verifier-cache-executed'
        self.inject_cached_marker(self.root/'fagao/integrity.py',marker)
        result=subprocess.run([sys.executable,'-I','-B','-c',
            'import sys;sys.pycache_prefix=None;sys.path.insert(0,'+repr(str(self.root))+');import fagao.runtime'],
            capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,1)
        self.assertEqual(json.loads(result.stderr)['error']['code'],'installation_integrity_failed')
        self.assertFalse(marker.exists(),'cached verifier ran before source verification')

    def test_fixed_cli_verifies_package_init_cache_before_importing_application(self):
        self.package_fixture();marker=self.base/'package-cache-executed'
        self.inject_cached_marker(self.root/'fagao/__init__.py',marker)
        spec=importlib.util.spec_from_file_location('fagao_test_fixed_cli',SOURCE/'scripts/bootstrap.py')
        bootstrap=importlib.util.module_from_spec(spec);spec.loader.exec_module(bootstrap)
        install=self.root.parent.parent
        entry=install/'open-cli.py';entry.write_text(bootstrap.OPEN_CLI_SCRIPT)
        (install/'current.json').write_text(json.dumps({'app':integrity.APP,'release_id':self.root.name,
            'data_dir':str(self.base/'private-data'),'interpreter':str(Path(sys.executable).resolve()),'port':18376}))
        result=subprocess.run([sys.executable,'-I','-B',str(entry),'state'],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,1)
        self.assertEqual(json.loads(result.stdout)['error']['code'],'installation_integrity_failed')
        self.assertFalse(marker.exists(),'package cache ran before the fixed CLI verification')

    def test_fixed_browser_entry_verifies_launcher_before_it_can_execute(self):
        self.package_fixture();marker=self.base/'launcher-must-not-execute'
        target=self.write('scripts/launch.py',b'# trusted launcher fixture\n')
        self.metadata['files']['scripts/launch.py']=hashlib.sha256(target.read_bytes()).hexdigest();self.receipt()
        target.write_text('from pathlib import Path\nPath('+repr(str(marker))+').touch()\n')
        spec=importlib.util.spec_from_file_location('fagao_test_fixed_browser',SOURCE/'scripts/bootstrap.py')
        bootstrap=importlib.util.module_from_spec(spec);spec.loader.exec_module(bootstrap)
        install=self.root.parent.parent
        entry=install/'open-workbench.py';entry.write_text(bootstrap.OPEN_SCRIPT)
        (install/'current.json').write_text(json.dumps({'app':integrity.APP,'release_id':self.root.name,
            'data_dir':str(self.base/'private-data'),'interpreter':str(Path(sys.executable).resolve()),'port':18376}))
        result=subprocess.run([sys.executable,'-I','-B',str(entry)],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,1)
        self.assertEqual(json.loads(result.stderr)['error']['code'],'installation_integrity_failed')
        self.assertFalse(marker.exists())

    def test_fixed_cli_preserves_session_and_binds_current_data_directory(self):
        self.package_fixture()
        self.write('fagao/__main__.py',b'import json\ndef main(arguments):\n print(json.dumps(arguments))\n return 0\n')
        self.metadata['files']['fagao/__main__.py']=hashlib.sha256((self.root/'fagao/__main__.py').read_bytes()).hexdigest();self.receipt()
        spec=importlib.util.spec_from_file_location('fagao_test_cli_session',SOURCE/'scripts/bootstrap.py')
        bootstrap=importlib.util.module_from_spec(spec);spec.loader.exec_module(bootstrap)
        install=self.root.parent.parent;entry=install/'open-cli.py';entry.write_text(bootstrap.OPEN_CLI_SCRIPT)
        data=str(self.base/'private-data')
        (install/'current.json').write_text(json.dumps({'app':integrity.APP,'release_id':self.root.name,
            'data_dir':data,'interpreter':str(Path(sys.executable).resolve()),'port':18376}))
        result=subprocess.run([sys.executable,'-I','-B',str(entry),'--session','conversation-a','state'],
            capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),['--data-dir',data,'--session','conversation-a','state'])
        wrong=subprocess.run([sys.executable,'-I','-B',str(entry),'--data-dir',str(self.base/'other'),'state'],
            capture_output=True,text=True,timeout=10)
        self.assertEqual(wrong.returncode,1)
        self.assertEqual(json.loads(wrong.stdout)['error']['code'],'fixed_data_directory')

    def test_fixed_cli_resets_narrow_streams_for_chinese_results_errors_and_progress(self):
        self.package_fixture()
        source='''import json, sys
def main(arguments):
    title = "盐选故事：发糕🍰"
    print("正在读取：" + title, file=sys.stderr)
    value = {"title":title,"data_dir":arguments[1]}
    if "fail" in arguments:
        value = {"error":{"message":"暂时无法读取，请重试。","candidates":[value]}}
    print(json.dumps(value,ensure_ascii=False))
    return 1 if "fail" in arguments else 0
'''
        target=self.write('fagao/__main__.py',source.encode('utf-8'))
        self.metadata['files']['fagao/__main__.py']=hashlib.sha256(target.read_bytes()).hexdigest();self.receipt()
        install=self.root.parent.parent
        renamed=install.with_name('中文 安装🍰');install.rename(renamed)
        self.root=renamed/'releases'/self.root.name;install=renamed
        spec=importlib.util.spec_from_file_location('fagao_test_narrow_cli',SOURCE/'scripts/bootstrap.py')
        bootstrap=importlib.util.module_from_spec(spec);spec.loader.exec_module(bootstrap)
        entry=install/'open-cli.py';entry.write_text(bootstrap.OPEN_CLI_SCRIPT,encoding='utf-8')
        data=str(self.base/'中文 资料🍰')
        (install/'current.json').write_text(json.dumps({'app':integrity.APP,'release_id':self.root.name,
            'data_dir':data,'interpreter':str(Path(sys.executable).resolve()),'port':18376}),encoding='utf-8')
        driver=('import runpy,sys;'
            'sys.stdout.reconfigure(encoding="cp1252",errors="strict");'
            'sys.stderr.reconfigure(encoding="cp1252",errors="strict");'
            'sys.argv=sys.argv[1:];runpy.run_path(sys.argv[0],run_name="__main__")')
        for command,expected in (('state',0),('fail',1)):
            with self.subTest(command=command):
                result=subprocess.run([sys.executable,'-I','-B','-c',driver,str(entry),command],
                    capture_output=True,timeout=10,
                    env={**os.environ,'PYTHONIOENCODING':'ascii','PYTHONUTF8':'0'})
                self.assertEqual(result.returncode,expected,result.stderr.decode('utf-8'))
                value=json.loads(result.stdout.decode('utf-8'))
                item=value if command=='state' else value['error']['candidates'][0]
                self.assertEqual(item,{'title':'盐选故事：发糕🍰','data_dir':data})
                self.assertIn('正在读取：盐选故事：发糕🍰',result.stderr.decode('utf-8'))

    def test_cli_machine_result_remains_lossless_on_an_unconfigurable_narrow_stream(self):
        from fagao.__main__ import emit
        class NarrowCapture:
            def __init__(self):self.raw=bytearray()
            def write(self,text):self.raw.extend(text.encode('cp1252','strict'))
            def flush(self):pass
        stream=NarrowCapture()
        value={'title':'知乎盐选🍰','error':{'message':'会员资料保留，请稍后重试。'}}
        with contextlib.redirect_stdout(stream):emit(value)
        self.assertTrue(bytes(stream.raw).isascii())
        self.assertEqual(json.loads(stream.raw),value)

    def test_cli_help_and_parser_errors_reset_narrow_streams(self):
        from fagao.__main__ import main
        for arguments,expected in ((['--help'],0),(['state','--中文参数'],2)):
            with self.subTest(arguments=arguments):
                stdout_bytes=io.BytesIO();stderr_bytes=io.BytesIO()
                stdout=io.TextIOWrapper(stdout_bytes,encoding='cp1252',errors='strict')
                stderr=io.TextIOWrapper(stderr_bytes,encoding='cp1252',errors='strict')
                with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr),self.assertRaises(SystemExit) as caught:
                    main(arguments)
                stdout.flush();stderr.flush()
                output=stdout_bytes.getvalue().decode('utf-8')+stderr_bytes.getvalue().decode('utf-8')
                stdout.detach();stderr.detach()
                self.assertEqual(caught.exception.code,expected)
                self.assertIn('发糕扫榜' if expected==0 else '中文参数',output)

    def test_secure_storage_deferred_reason_remains_recoverable(self):
        error=integrity.IntegrityError('key_locked',{'repair_required':False,'deferred':True,
            'recovery':'unlock_keychain','message':'系统安全存储暂时锁定。'})
        self.assertFalse(error.diagnostic()['repair_required'])
        self.assertEqual(error.diagnostic()['recovery'],'unlock_keychain')

    def test_api_and_cli_report_repair_instead_of_membership_expiry(self):
        from fagao.__main__ import main
        from fagao.runtime import WorkbenchRuntime
        from fagao.server import WorkbenchServer
        from membership_fixtures import SyntheticMembership
        data=self.base/'account-data'
        runtime=WorkbenchRuntime(data,SyntheticMembership(data))
        server=WorkbenchServer(('127.0.0.1',0),runtime=runtime)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with patch.object(integrity,'verify_installation',side_effect=integrity.IntegrityError('fixture_tamper')):
                output=io.StringIO()
                with contextlib.redirect_stdout(output):
                    code=main(['--data-dir',str(data),'state'],runtime_factory=lambda directory:runtime)
                self.assertEqual(code,1)
                self.assertEqual(json.loads(output.getvalue())['error']['code'],'installation_integrity_failed')
                connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
                connection.request('GET','/api/state',headers={'X-Fagao-Account':'member-a','X-Fagao-Generation':'session-a'})
                response=connection.getresponse();value=json.loads(response.read());connection.close()
                self.assertEqual(response.status,503)
                self.assertEqual(value['error']['code'],'installation_integrity_failed')
                self.assertTrue(value['error']['data_preserved'])
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)


if __name__=='__main__':unittest.main()
