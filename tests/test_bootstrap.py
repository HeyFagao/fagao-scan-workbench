# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import ssl
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
import zipfile

SPEC = importlib.util.spec_from_file_location("fagao_bootstrap",Path(__file__).resolve().parents[1]/"scripts/bootstrap.py")
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.archive=self.base/'source.zip'
    def tearDown(self): self.temp.cleanup()
    def archive_with(self,entries):
        with zipfile.ZipFile(self.archive,'w') as z:
            for name,content in entries:z.writestr(name,content)
    def archive_with_raw_name(self,name):
        # ZipInfo normalizes backslashes *while writing* on Windows and strips
        # NUL on every OS. Replace equal-length ASCII filename bytes in both ZIP
        # headers so the reader really receives the unsafe name on every OS.
        raw_name=name.encode('ascii');placeholder='x'*len(raw_name)
        self.archive_with([(placeholder,'bad')])
        content=self.archive.read_bytes();encoded=placeholder.encode('ascii')
        self.assertEqual(content.count(encoded),2)
        self.archive.write_bytes(content.replace(encoded,raw_name))
        with zipfile.ZipFile(self.archive) as zipped:
            self.assertEqual(zipped.infolist()[0].orig_filename,name)
    def test_extracts_one_exact_root(self):
        self.archive_with([('repo-sha/',''),('repo-sha/a/b.txt','hello')])
        bootstrap.extract_archive(self.archive,self.base/'out','repo-sha')
        self.assertEqual((self.base/'out/a/b.txt').read_text(),'hello')
    def test_rejects_traversal_absolute_drive_and_backslash(self):
        for name in ['repo-sha/../../escaped','/repo-sha/file','repo-sha/C:/file','repo-sha/a\\file','repo-sha/CON']:
            with self.subTest(name=name):
                self.archive_with_raw_name(name)
                with self.assertRaises(bootstrap.InstallError):bootstrap.extract_archive(self.archive,self.base/'out','repo-sha')
                self.assertFalse((self.base/'out').exists())
        self.assertFalse((self.base/'escaped').exists())
    def test_rejects_raw_nul_names_before_zipinfo_truncation_can_hide_them(self):
        for name in ['repo-sha/safe.txt\x00hidden','repo-sha/\x00hidden','repo-sha/a\x00/../../escaped']:
            with self.subTest(name=name):
                self.archive_with_raw_name(name)
                with zipfile.ZipFile(self.archive) as zipped:
                    member=zipped.infolist()[0]
                    self.assertNotEqual(member.orig_filename,member.filename)
                with self.assertRaises(bootstrap.InstallError):
                    bootstrap.extract_archive(self.archive,self.base/'out','repo-sha')
                self.assertFalse((self.base/'out').exists())
        self.assertFalse((self.base/'escaped').exists())
    def test_rejects_original_backslash_even_when_zipinfo_normalizes_windows_separator(self):
        self.archive_with_raw_name('repo-sha/a\\file')
        with patch.object(zipfile.os,'sep','\\'):
            with zipfile.ZipFile(self.archive) as zipped:
                member=zipped.infolist()[0]
                self.assertEqual(member.orig_filename,'repo-sha/a\\file')
                self.assertEqual(member.filename,'repo-sha/a/file')
            with self.assertRaises(bootstrap.InstallError):
                bootstrap.extract_archive(self.archive,self.base/'out','repo-sha')
        self.assertFalse((self.base/'out').exists())
    def test_rejects_symlink_and_special_files(self):
        for file_type in [stat.S_IFLNK,stat.S_IFIFO]:
            info=zipfile.ZipInfo('repo-sha/link');info.create_system=3;info.external_attr=(file_type|0o777)<<16
            self.archive_with([(info,'../../secret')])
            with self.assertRaises(bootstrap.InstallError):bootstrap.extract_archive(self.archive,self.base/'out','repo-sha')
    def test_rejects_case_collisions_and_wrong_root(self):
        for entries in [[('repo-sha/A','x'),('repo-sha/a','y')],[('another/file','x')]]:
            self.archive_with(entries)
            with self.assertRaises(bootstrap.InstallError):bootstrap.extract_archive(self.archive,self.base/'out','repo-sha')
    def test_rejects_oversized_archive_and_expansion(self):
        self.archive_with([('repo-sha/a','0123456789')])
        with patch.object(bootstrap,'MAX_ARCHIVE',1):
            with self.assertRaises(bootstrap.InstallError):bootstrap.extract_archive(self.archive,self.base/'out','repo-sha')
        with patch.object(bootstrap,'MAX_EXPANDED',5):
            with self.assertRaises(bootstrap.InstallError):bootstrap.extract_archive(self.archive,self.base/'out','repo-sha')
    def test_rejects_branch_instead_of_fixed_sha(self):
        for commit in ['main','latest','abc123','a'*39,'a'*41]:
            with self.assertRaises(bootstrap.InstallError):bootstrap.download_archive('owner/repo',commit,self.base/'download')
    def test_rejects_archives_containing_device_credentials_or_account_cache(self):
        for name in ['.membership/session.json','accounts/member/body.json','legacy-claims/receipt.json','legacy-decisions.json']:
            with self.subTest(name=name):
                self.archive_with([('repo-sha/'+name,'private data')])
                with self.assertRaisesRegex(bootstrap.InstallError,'账号凭据'):
                    bootstrap.extract_archive(self.archive,self.base/'out','repo-sha')
                self.assertFalse((self.base/'out').exists())
    def test_redirect_remains_https_codeload_only(self):
        redirect=bootstrap.PinnedRedirect()
        with self.assertRaises(bootstrap.InstallError):redirect.redirect_request(None,None,302,'',{},'https://evil.test/payload')
        with self.assertRaises(bootstrap.InstallError):redirect.redirect_request(None,None,302,'',{},'http://codeload.github.com/path')


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.target=self.base/'download.zip';self.content=b'fixed trusted archive'
        self.digest=hashlib.sha256(self.content).hexdigest()
        self.commit='a'*40
    def tearDown(self):self.temp.cleanup()
    def success(self,url,destination,*args):destination.write_bytes(self.content)
    def download(self,**kwargs):
        return bootstrap.download_archive('owner/repo',self.commit,self.target,self.digest,**kwargs)
    def certificate_error(self):
        return URLError(ssl.SSLCertVerificationError(1,'CERTIFICATE_VERIFY_FAILED https://secret:password@proxy.invalid/'))
    def test_checks_hash_after_python_download(self):
        with patch.object(bootstrap,'_download_urllib',side_effect=self.success):result=self.download()
        self.assertEqual(result['transport'],'python_https');self.assertEqual(result['archive_sha256'],self.digest)
        self.assertEqual(result['attempts'],[{'transport':'python_https','status':'verified'}])
    def test_windows_certificate_error_switches_once_to_native_system_tls(self):
        with patch.object(bootstrap.sys,'platform','win32'),patch.object(bootstrap,'_windows_system_curl',return_value='system-curl.exe'),patch.object(bootstrap,'_download_urllib',side_effect=self.certificate_error()) as python,patch.object(bootstrap,'_download_curl',side_effect=self.success) as native,patch.object(bootstrap.time,'sleep') as sleep:
            result=self.download()
        self.assertEqual(python.call_count,1);self.assertEqual(native.call_count,1);sleep.assert_not_called()
        self.assertEqual(result['transport'],'windows_system_curl')
        self.assertNotIn('password',json.dumps(result))
    def test_windows_502_uses_native_channel_without_manual_reinstall(self):
        failure=HTTPError('https://codeload.github.com/',502,'Bad Gateway',{},None)
        with patch.object(bootstrap.sys,'platform','win32'),patch.object(bootstrap,'_windows_system_curl',return_value='system-curl.exe'),patch.object(bootstrap,'_download_urllib',side_effect=failure),patch.object(bootstrap,'_download_curl',side_effect=self.success):
            result=self.download()
        self.assertEqual(len(result['attempts']),2)
    def test_transient_http_failure_retries_bounded_and_removes_partial_file(self):
        calls=[]
        def flaky(url,destination):
            self.assertFalse(destination.exists());calls.append(url)
            if len(calls)<3:
                destination.write_bytes(b'partial')
                raise HTTPError(url,502,'Bad Gateway',{},None)
            self.success(url,destination)
        with patch.object(bootstrap.sys,'platform','darwin'),patch.object(bootstrap,'_download_urllib',side_effect=flaky),patch.object(bootstrap.time,'sleep') as sleep:
            result=self.download()
        self.assertEqual(len(result['attempts']),3);self.assertEqual(sleep.call_count,2)
        self.assertEqual(self.target.read_bytes(),self.content)
    def test_native_failure_does_not_loop_or_leak_proxy_details(self):
        failure=URLError('connection through https://secret:password@proxy.invalid/ failed')
        with patch.object(bootstrap.sys,'platform','win32'),patch.object(bootstrap,'_windows_system_curl',return_value='system-curl.exe'),patch.object(bootstrap,'_download_urllib',side_effect=self.certificate_error()) as python,patch.object(bootstrap,'_download_curl',side_effect=failure) as native,patch.object(bootstrap.time,'sleep'):
            with self.assertRaises(bootstrap.InstallError) as caught:self.download()
        self.assertEqual(python.call_count,1);self.assertEqual(native.call_count,2)
        value=caught.exception.diagnostic();self.assertFalse(value['retryable'])
        self.assertEqual(len(value['attempts']),3);self.assertNotIn('password',json.dumps(value))
        self.assertFalse(self.target.exists())
    def test_missing_native_channel_does_not_repeat_certificate_failure(self):
        with patch.object(bootstrap.sys,'platform','win32'),patch.object(bootstrap,'_windows_system_curl',return_value=None),patch.object(bootstrap,'_download_urllib',side_effect=self.certificate_error()) as call:
            with self.assertRaises(bootstrap.InstallError) as caught:self.download()
        self.assertEqual(call.call_count,1);self.assertEqual(caught.exception.code,'download_tls_verification_failed')
    def test_hash_mismatch_is_not_retried_or_downgraded(self):
        def bad(url,destination):destination.write_bytes(b'tampered archive')
        with patch.object(bootstrap,'_download_urllib',side_effect=bad) as call,patch.object(bootstrap,'_windows_system_curl') as native:
            with self.assertRaises(bootstrap.InstallError) as caught:self.download()
        self.assertEqual(caught.exception.code,'archive_checksum_mismatch');self.assertEqual(call.call_count,1)
        native.assert_not_called();self.assertFalse(self.target.exists())
    def test_native_download_is_also_hash_checked(self):
        def bad(url,destination,executable):destination.write_bytes(b'tampered native archive')
        with patch.object(bootstrap.sys,'platform','win32'),patch.object(bootstrap,'_windows_system_curl',return_value='system-curl.exe'),patch.object(bootstrap,'_download_urllib',side_effect=self.certificate_error()),patch.object(bootstrap,'_download_curl',side_effect=bad) as native:
            with self.assertRaises(bootstrap.InstallError) as caught:self.download()
        self.assertEqual(caught.exception.code,'archive_checksum_mismatch');self.assertEqual(native.call_count,1)
        self.assertFalse(self.target.exists())
    def test_permanent_http_error_is_not_retried(self):
        with patch.object(bootstrap,'_download_urllib',side_effect=HTTPError('https://codeload.github.com/',404,'Not Found',{},None)) as call:
            with self.assertRaises(bootstrap.InstallError):self.download()
        self.assertEqual(call.call_count,1)
    def test_missing_hash_fails_before_network_or_files(self):
        with patch.object(bootstrap,'_download_urllib') as call:
            with self.assertRaises(bootstrap.InstallError) as caught:
                bootstrap.download_archive('owner/repo',self.commit,self.target)
        self.assertEqual(caught.exception.code,'archive_checksum_required');call.assert_not_called()
        self.assertEqual(list(self.base.iterdir()),[])
    def test_existing_download_target_is_never_removed(self):
        self.target.write_bytes(b'prior unrelated file')
        with self.assertRaises(bootstrap.InstallError):self.download()
        self.assertEqual(self.target.read_bytes(),b'prior unrelated file')
    def test_urllib_rejects_declared_or_streamed_oversize(self):
        class Response(io.BytesIO):
            def __init__(self,content,headers):super().__init__(content);self.headers=headers
        for declared in ['100',None]:
            with self.subTest(declared=declared):
                response=Response(b'1234567890',{'Content-Length':declared})
                class Opener:
                    def open(self,*args,**kwargs):return response
                with patch.object(bootstrap,'MAX_ARCHIVE',5),patch.object(bootstrap,'build_opener',return_value=Opener()),patch.object(bootstrap,'_download_urllib',side_effect=bootstrap._download_urllib_stream):
                    with self.assertRaises(bootstrap.InstallError) as caught:self.download()
                self.assertEqual(caught.exception.code,'archive_too_large');self.assertFalse(self.target.exists())
    def test_python_worker_total_deadline_terminates_stalled_writer_and_cleans_partial(self):
        worker=self.base/'stall-worker.py'
        worker.write_text('import sys,time\nwith open(sys.argv[-1],"wb") as f:\n while True:\n  f.write(b"x");f.flush();time.sleep(0.02)\n')
        started=time.monotonic()
        with patch.object(bootstrap,'__file__',str(worker)),patch.object(bootstrap,'DOWNLOAD_TIMEOUT',0.5),patch.object(bootstrap,'DOWNLOAD_ATTEMPTS',1),patch.object(bootstrap.sys,'platform','darwin'):
            with self.assertRaises(bootstrap.InstallError) as caught:self.download()
        self.assertEqual(caught.exception.code,'download_timeout')
        self.assertLess(time.monotonic()-started,3)
        self.assertFalse(self.target.exists())
    def test_streaming_deadline_uses_read1_for_slow_trickle(self):
        elapsed=[0.0]
        class Trickle:
            headers={}
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):raise AssertionError('read waits for a full buffer under a slow trickle')
            def read1(self,size):elapsed[0]+=0.5;return b'x'
        class Opener:
            def open(self,*args,**kwargs):return Trickle()
        with patch.object(bootstrap,'DOWNLOAD_TIMEOUT',2),patch.object(bootstrap.time,'monotonic',side_effect=lambda:elapsed[0]),patch.object(bootstrap,'build_opener',return_value=Opener()):
            with self.assertRaises(TimeoutError):bootstrap._download_urllib_stream('https://codeload.github.com/owner/repo/zip/'+self.commit,self.target)
        self.assertLessEqual(elapsed[0],2.5)
    def test_internal_worker_rejects_unpinned_or_other_host_urls_before_writing(self):
        for url in ['https://evil.test/owner/repo/zip/'+self.commit,
                    'http://codeload.github.com/owner/repo/zip/'+self.commit,
                    'https://codeload.github.com/owner/repo/zip/main',
                    'https://user:password@codeload.github.com/owner/repo/zip/'+self.commit,
                    'https://codeload.github.com/owner/repo/zip/'+self.commit+'?other=1']:
            with self.subTest(url=url),patch.object(bootstrap,'_download_urllib_stream') as call:
                with self.assertRaises(bootstrap.InstallError):bootstrap._download_worker(url,self.target)
                call.assert_not_called();self.assertFalse(self.target.exists())
    def test_native_curl_command_verifies_tls_disables_rc_and_bounds_stream(self):
        class Process:
            def __init__(self):self.stdout=io.BytesIO(b'1234567890');self.killed=False;self.returncode=None
            def wait(self,timeout=None):self.returncode=0;return 0
            def poll(self):return self.returncode
            def kill(self):self.killed=True;self.returncode=-1
        process=Process();calls=[]
        def start(command,**kwargs):calls.append((command,kwargs));return process
        with patch.object(bootstrap,'MAX_ARCHIVE',5),patch.object(bootstrap.subprocess,'Popen',side_effect=start),patch.object(bootstrap,'_curl_environment',return_value={}):
            with self.assertRaises(bootstrap.InstallError) as caught:
                bootstrap._download_curl('https://codeload.github.com/owner/repo/zip/'+self.commit,self.target,'system-curl.exe')
        self.assertEqual(caught.exception.code,'archive_too_large');self.assertTrue(process.killed)
        command=calls[0][0];self.assertEqual(command[1],'--disable')
        self.assertIn('--max-filesize',command);self.assertIn('--max-time',command)
        self.assertNotIn('--insecure',command);self.assertNotIn('-k',command);self.assertNotIn('--location',command)
        self.assertEqual(calls[0][1]['stderr'],subprocess.DEVNULL)
    def test_native_certificate_rejection_is_not_retried_as_transient(self):
        class Process:
            stdout=io.BytesIO(b'')
            def wait(self,timeout=None):return 60
            def poll(self):return 60
        with patch.object(bootstrap.subprocess,'Popen',return_value=Process()),patch.object(bootstrap,'_curl_environment',return_value={}):
            with self.assertRaises(bootstrap.InstallError) as caught:
                bootstrap._download_curl('https://codeload.github.com/owner/repo/zip/'+self.commit,self.target,'system-curl.exe')
        self.assertEqual(caught.exception.code,'download_tls_verification_failed');self.assertFalse(caught.exception.retryable)
    def test_native_environment_keeps_proxy_but_never_changes_parent(self):
        env={'HTTPS_PROXY':'http://user:password@proxy.invalid:1234','SSL_CERT_FILE':'untrusted.pem','CURL_SSL_BACKEND':'openssl','KEEP':'value'}
        with patch.dict(bootstrap.os.environ,env,clear=True),patch.object(bootstrap,'getproxies',return_value={}):
            result=bootstrap._curl_environment()
            self.assertEqual(dict(bootstrap.os.environ),env)
        self.assertEqual(result['HTTPS_PROXY'],env['HTTPS_PROXY']);self.assertNotIn('SSL_CERT_FILE',result)
        self.assertNotIn('CURL_SSL_BACKEND',result)
    def test_verified_local_archive_avoids_network_and_checks_hash(self):
        source=self.base/'already-downloaded.zip';source.write_bytes(self.content)
        result=bootstrap.copy_verified_archive(source,self.target,self.digest)
        self.assertEqual(result['transport'],'verified_local_archive')
        self.target.unlink()
        with self.assertRaises(bootstrap.InstallError) as caught:bootstrap.copy_verified_archive(source,self.target,'0'*64)
        self.assertEqual(caught.exception.code,'archive_checksum_mismatch')


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.source=self.base/'trusted';self.source.mkdir()
        for name in bootstrap.REQUIRED:
            p=self.source/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('# '+bootstrap.APP+'\n')
        self.root=self.base/'install';self.data=self.base/'data';self.data.mkdir()
        self.sentinel=self.data/'workbench.sqlite3';self.sentinel.write_bytes(b'user data must survive')
        self.service=None;self.calls=[]
        self.health_patch=patch.object(bootstrap,'health',side_effect=lambda port:self.service)
        self.run_patch=patch.object(bootstrap.subprocess,'run',side_effect=self.mock_run)
        self.health_patch.start();self.run_patch.start()
    def tearDown(self):
        self.health_patch.stop();self.run_patch.stop();self.temp.cleanup()
    def mock_run(self,command,**kwargs):
        self.calls.append(command)
        self.service={'app':bootstrap.APP,'data_dir':str(self.data),'source_root':str(Path(command[1]).parent.parent)}
        class Result: returncode=0;stdout=json.dumps({'status':'started','browser_opened':False,'pid':12345,'readiness':{'status':'ready'}});stderr=''
        return Result()
    def install(self,**kwargs):
        return bootstrap.install(source_dir=self.source,install_root=self.root,data_dir=self.data,no_browser=True,**kwargs)
    def test_install_and_repeat_preserve_data_and_single_release(self):
        first=self.install();second=self.install()
        self.assertEqual(first['status'],'ready');self.assertEqual(first['release_id'],second['release_id'])
        self.assertEqual(len(list((self.root/'releases').iterdir())),1)
        self.assertEqual(self.sentinel.read_bytes(),b'user data must survive')
        self.assertEqual(json.loads((self.root/'current.json').read_text())['release_id'],first['release_id'])
        self.assertTrue(Path(first['shortcut']).is_file())
        self.assertEqual(first['readiness']['status'],'ready')
    def test_upgrade_stages_but_does_not_switch_running_old_service(self):
        first=self.install();before=(self.root/'current.json').read_bytes()
        (self.source/'web/index.html').write_text('new version')
        upgraded=self.install()
        self.assertEqual(upgraded['status'],'restart_required')
        self.assertNotEqual(first['release_id'],upgraded['release_id'])
        self.assertEqual((self.root/'current.json').read_bytes(),before)
        self.assertEqual(len(self.calls),1)
        self.service=None
        ready=self.install()
        self.assertEqual(ready['status'],'ready')
        self.assertEqual(ready['release_id'],upgraded['release_id'])
    def test_refuses_unowned_existing_directory(self):
        self.root.mkdir();(self.root/'unrelated.txt').write_text('keep')
        with self.assertRaises(bootstrap.InstallError):self.install()
        self.assertEqual((self.root/'unrelated.txt').read_text(),'keep')
    def test_refuses_program_root_equal_or_above_data(self):
        for root in [self.data,self.base]:
            with self.assertRaises(bootstrap.InstallError):bootstrap.install(source_dir=self.source,install_root=root,data_dir=self.data,no_browser=True)
    @unittest.skipIf(os.name=='nt','symlink privileges vary')
    def test_refuses_source_or_install_symlinks(self):
        (self.source/'external').symlink_to(self.sentinel)
        with self.assertRaises(bootstrap.InstallError):self.install()
        (self.source/'external').unlink()
        other=self.base/'target';other.mkdir()
        link=self.base/'linked-root';link.symlink_to(other,target_is_directory=True)
        with self.assertRaises(bootstrap.InstallError):bootstrap.install(source_dir=self.source,install_root=link,data_dir=self.data,no_browser=True)
    def test_modified_installed_code_is_neither_executed_nor_overwritten(self):
        first=self.install();file=Path(first['release_dir'])/'fagao/server.py';file.write_text('modified')
        with self.assertRaises(bootstrap.InstallError):self.install()
        self.assertEqual(file.read_text(),'modified');self.assertEqual(len(self.calls),1)
    def test_does_not_switch_current_when_health_is_wrong(self):
        def bad_run(command,**kwargs):
            result=self.mock_run(command,**kwargs);self.service['source_root']=str(self.source);return result
        with patch.object(bootstrap.subprocess,'run',side_effect=bad_run):
            with self.assertRaises(bootstrap.InstallError):self.install()
        self.assertFalse((self.root/'current.json').exists())
    def test_excludes_runtime_and_secret_files_from_local_copy(self):
        (self.source/'.env').write_text('secret')
        (self.source/'node_modules').mkdir();(self.source/'node_modules/dep.js').write_text('dependency')
        result=self.install();release=Path(result['release_dir'])
        self.assertFalse((release/'.env').exists());self.assertFalse((release/'node_modules').exists())
    def test_excludes_database_profiles_and_logs_from_local_source(self):
        for name in ['.data/workbench.sqlite3','browser-profiles/default/Cookies','app.sqlite3','app.sqlite3-wal','error.log']:
            p=self.source/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_text('private runtime data')
        result=self.install(); release=Path(result['release_dir'])
        for name in ['.data','browser-profiles','app.sqlite3','app.sqlite3-wal','error.log']:
            self.assertFalse((release/name).exists(),name)
    def test_refuses_install_root_inside_local_source(self):
        with self.assertRaises(bootstrap.InstallError):
            bootstrap.install(source_dir=self.source,install_root=self.source/'app',data_dir=self.data,no_browser=True)
        self.assertFalse((self.source/'app').exists())
    def test_never_copies_membership_credentials_or_account_json_into_release(self):
        private=['.membership/session.json','accounts/owner/body-cache/chapter.json',
                 'legacy-claims/receipt.json','legacy-claim.json','legacy-decisions.json','.legacy-claim.lock','network.json']
        for name in private:
            path=self.source/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text('private-device-token-and-personal-content')
        release=Path(self.install()['release_dir'])
        for name in private:self.assertFalse((release/name).exists(),name)
        self.assertNotIn('private-device-token-and-personal-content',
                         ''.join(p.read_text(errors='ignore') for p in release.rglob('*') if p.is_file()))
    def test_refuses_extra_executable_file_in_installed_release(self):
        first=self.install(); injected=Path(first['release_dir'])/'scripts/json.py'; injected.write_text('# unexpected new code')
        with self.assertRaises(bootstrap.InstallError): self.install()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(injected.read_text(),'# unexpected new code')
    def test_runtime_cache_is_compatible_with_repeat_verification(self):
        first=self.install(); cache=Path(first['release_dir'])/'fagao/__pycache__/store.cpython-312.pyc'
        cache.parent.mkdir(); cache.write_bytes(b'generated cache')
        self.assertEqual(self.install()['release_id'],first['release_id'])
    def test_stable_project_workspace_and_interpreter_returned(self):
        first=self.install(); project=Path(first['project_dir'])
        self.assertEqual(project,(self.root/'workspace').resolve())
        instructions=(project/'AGENTS.md').read_text()
        self.assertIn('current.json',instructions); self.assertNotIn(first['release_id'],instructions)
        current=json.loads((self.root/'current.json').read_text())
        self.assertEqual(current['interpreter'],first['interpreter'])
        self.assertTrue(Path(first['interpreter']).is_absolute())
        self.service=None; (self.source/'web/index.html').write_text('new version')
        next_version=self.install()
        self.assertEqual(next_version['project_dir'],first['project_dir'])
        self.assertEqual((project/'AGENTS.md').read_text(),instructions)
        self.assertNotEqual(next_version['release_id'],first['release_id'])
    def test_user_modified_workspace_is_not_overwritten_or_launched(self):
        first=self.install(); project=Path(first['project_dir'])
        (project/'AGENTS.md').write_text('user additions')
        with self.assertRaises(bootstrap.InstallError): self.install()
        self.assertEqual((project/'AGENTS.md').read_text(),'user additions')
        self.assertEqual(len(self.calls),1)
    @unittest.skipIf(os.name=='nt','symlink privileges vary')
    def test_project_workspace_cannot_redirect_writes(self):
        first=self.install(); project=Path(first['project_dir'])
        shutil.rmtree(project); elsewhere=self.base/'elsewhere'; elsewhere.mkdir()
        project.symlink_to(elsewhere,target_is_directory=True)
        with self.assertRaises(bootstrap.InstallError): self.install()
        self.assertFalse((elsewhere/'AGENTS.md').exists())
    def test_private_node_runtime_survives_install_and_fixed_entry(self):
        import sys
        first=self.install(); self.service=None
        node=self.base/'private tools'/'node'; node.parent.mkdir(); node.write_text('fake node for environment inspection')
        runtime=self.root/'runtime.json'; original=json.dumps({'node_executable':str(node)})
        runtime.write_text(original)
        environments=[]
        def run(command,**kwargs):
            environments.append(kwargs.get('env',{})); return self.mock_run(command,**kwargs)
        with patch.object(bootstrap.subprocess,'run',side_effect=run): second=self.install()
        self.assertEqual(environments[0]['PATH'].split(os.pathsep)[0],str(node.parent))
        self.assertEqual(runtime.read_text(),original)
        calls=[]
        with patch('urllib.request.build_opener',side_effect=OSError('no live service')), patch('subprocess.call',side_effect=lambda command,**kwargs: calls.append((command,kwargs)) or 0):
            with self.assertRaises(SystemExit) as exit_result:
                exec(compile(bootstrap.OPEN_SCRIPT,str(self.root/'open-workbench.py'),'exec'),{'__file__':str(self.root/'open-workbench.py')})
        self.assertEqual(exit_result.exception.code,0)
        self.assertEqual(calls[0][1]['env']['PATH'].split(os.pathsep)[0],str(node.parent))
        self.assertEqual(calls[0][0][0],sys.executable)
    def test_obsolete_optional_node_does_not_block_current_features(self):
        self.install(); self.service=None
        for node in ['relative/node',str(self.base/'missing'/'node'),['node']]:
            with self.subTest(node=node):
                (self.root/'runtime.json').write_text(json.dumps({'node_executable':node}))
                self.assertEqual(self.install()['status'],'ready')
        self.assertEqual(len(self.calls),4)
    def test_explicit_legacy_backend_invalid_node_stops_before_launch(self):
        self.install(); self.service=None
        for node in ['relative/node',str(self.base/'missing'/'node'),['node']]:
            with self.subTest(node=node):
                (self.root/'runtime.json').write_text(json.dumps({'node_executable':node,'enable_legacy_heiyan_backend':True}))
                with self.assertRaises(bootstrap.InstallError): self.install()
        self.assertEqual(len(self.calls),1)
    def test_missing_readiness_does_not_switch_current(self):
        def unchecked_run(command,**kwargs):
            result=self.mock_run(command,**kwargs)
            result.stdout=json.dumps({'status':'started','browser_opened':False})
            return result
        with patch.object(bootstrap.subprocess,'run',side_effect=unchecked_run):
            with self.assertRaises(bootstrap.InstallError): self.install()
        self.assertFalse((self.root/'current.json').exists())
    def test_fixed_entry_ignores_missing_optional_node(self):
        self.install()
        runtime=self.root/'runtime.json'
        runtime.write_text(json.dumps({'node_executable':str(self.base/'gone'/'node')}))
        calls=[]
        with patch('urllib.request.build_opener',side_effect=OSError('no live service')), patch('subprocess.call',side_effect=lambda command,**kwargs: calls.append(command) or 0):
            with self.assertRaises(SystemExit) as result:
                exec(compile(bootstrap.OPEN_SCRIPT,str(self.root/'open-workbench.py'),'exec'),{'__file__':str(self.root/'open-workbench.py')})
        self.assertEqual(result.exception.code,0)
        self.assertEqual(len(calls),1)
    def test_fixed_launcher_reads_current_not_initial_release(self):
        first=self.install();entry=(self.root/'open-workbench.py').read_text()
        self.assertIn('current.json',entry);self.assertNotIn(first['release_id'],entry)
    def test_remote_install_requires_hash_before_writing_install_root(self):
        with self.assertRaises(bootstrap.InstallError) as caught:
            bootstrap.install(repo='owner/repo',commit='a'*40,install_root=self.root,data_dir=self.data)
        self.assertEqual(caught.exception.code,'archive_checksum_required')
        self.assertFalse(self.root.exists());self.assertEqual(self.calls,[])
    def test_verified_archive_retains_fixed_commit_identity_and_reuses_offline(self):
        commit='b'*40;archive=self.base/'already-verified.zip'
        with zipfile.ZipFile(archive,'w') as handle:
            for file in self.source.rglob('*'):
                if file.is_file():handle.write(file,'repo-'+commit+'/'+file.relative_to(self.source).as_posix())
        digest=hashlib.sha256(archive.read_bytes()).hexdigest()
        with patch.object(bootstrap,'download_archive') as download:
            first=bootstrap.install(repo='owner/repo',commit=commit,archive_file=archive,archive_sha256=digest,
                                    install_root=self.root,data_dir=self.data,no_browser=True)
            second=bootstrap.install(repo='owner/repo',commit=commit,archive_file=archive,archive_sha256=digest,
                                     install_root=self.root,data_dir=self.data,no_browser=True)
        download.assert_not_called();self.assertEqual(first['release_id'],commit)
        self.assertEqual(first['release_id'],second['release_id'])
        self.assertEqual(first['download']['transport'],'verified_local_archive')
        self.assertEqual(self.sentinel.read_bytes(),b'user data must survive')
        self.assertTrue(archive.exists(), 'User-supplied ZIP must never be deleted')
    def test_archive_hash_failure_cannot_replace_current_or_user_data(self):
        first=self.install();before=(self.root/'current.json').read_bytes()
        archive=self.base/'bad.zip';archive.write_bytes(b'tampered')
        with self.assertRaises(bootstrap.InstallError) as caught:
            bootstrap.install(repo='owner/repo',commit='b'*40,archive_file=archive,archive_sha256='0'*64,
                              install_root=self.root,data_dir=self.data,no_browser=True)
        self.assertEqual(caught.exception.code,'archive_checksum_mismatch')
        self.assertEqual((self.root/'current.json').read_bytes(),before)
        self.assertEqual(self.sentinel.read_bytes(),b'user data must survive')
        self.assertFalse(list(self.root.glob('.download-*')))
        self.assertFalse(list(self.root.glob('.staging-*')))
    def test_source_dir_cannot_mix_remote_archive_arguments(self):
        with self.assertRaises(bootstrap.InstallError):self.install(archive_sha256='0'*64)
        self.assertFalse(self.root.exists())
    def background_result(self,command,**kwargs):
        import sys
        release=Path(command[1]).parent.parent
        value={'status':'background_required','code':'native_background_required',
               'reason':'windows_detach_not_permitted','readiness':{'status':'ready'},
               'action':{'type':'run_in_background','argv':[sys.executable,str(release/'scripts/launch.py'),
                          '--data-dir',str(self.data.resolve()),'--port','18376','--foreground','--no-browser'],
                         'cwd':str(release),'health_url':'http://127.0.0.1:18376/api/health'}}
        return subprocess.CompletedProcess(command,3,stdout=json.dumps(value),stderr='')
    def test_native_background_required_keeps_release_without_committing_current(self):
        with patch.object(bootstrap.subprocess,'run',side_effect=self.background_result):result=self.install()
        self.assertEqual(result['status'],'background_required');self.assertFalse(result['current_switched'])
        self.assertFalse((self.root/'current.json').exists())
        self.assertTrue((Path(result['release_dir'])/'scripts/launch.py').is_file())
        self.assertEqual(self.sentinel.read_bytes(),b'user data must survive')
        ready=self.install()
        self.assertEqual(ready['status'],'ready');self.assertEqual(ready['release_id'],result['release_id'])
    def test_resume_uses_identical_verified_release_installer_instead_of_temporary_download(self):
        (self.source/'scripts/bootstrap.py').write_bytes(Path(bootstrap.__file__).read_bytes())
        with patch.object(bootstrap.subprocess,'run',side_effect=self.background_result):result=self.install()
        self.assertEqual(result['resume']['argv'][1],str(Path(result['release_dir'])/'scripts/bootstrap.py'))
    def test_background_handoff_cannot_run_unverified_command(self):
        def bad(command,**kwargs):
            result=self.background_result(command,**kwargs)
            value=json.loads(result.stdout);value['action']['argv']=['other program']
            result.stdout=json.dumps(value);return result
        with patch.object(bootstrap.subprocess,'run',side_effect=bad):
            with self.assertRaises(bootstrap.InstallError):self.install()
        self.assertFalse((self.root/'current.json').exists())
    def test_background_handoff_preserves_previous_current(self):
        self.install();before=(self.root/'current.json').read_bytes();self.service=None
        (self.source/'web/index.html').write_text('new code')
        with patch.object(bootstrap.subprocess,'run',side_effect=self.background_result):result=self.install()
        self.assertEqual(result['status'],'background_required')
        self.assertEqual((self.root/'current.json').read_bytes(),before)
    def test_remote_background_resume_reverifies_owned_archive_without_downloading_again(self):
        commit='c'*40;official=self.base/'user-supplied.zip'
        with zipfile.ZipFile(official,'w') as handle:
            for file in self.source.rglob('*'):
                if file.is_file():handle.write(file,'repo-'+commit+'/'+file.relative_to(self.source).as_posix())
        digest=hashlib.sha256(official.read_bytes()).hexdigest()
        args={'repo':'owner/repo','commit':commit,'archive_sha256':digest,
              'install_root':self.root,'data_dir':self.data,'no_browser':True}
        with patch.object(bootstrap.subprocess,'run',side_effect=self.background_result):
            pending=bootstrap.install(**args,archive_file=official)
        self.assertEqual(pending['status'],'background_required');resume=pending['resume']['argv']
        cached=Path(resume[resume.index('--archive-file')+1])
        self.assertTrue(cached.is_file());self.assertNotEqual(cached,official)
        self.assertEqual(hashlib.sha256(cached.read_bytes()).hexdigest(),digest)
        self.assertEqual(len(list(self.root.glob('.download-*'))),1)
        self.assertFalse((self.root/'current.json').exists())
        with patch.object(bootstrap,'download_archive') as network:
            ready=bootstrap.install(**args)
        network.assert_not_called();self.assertEqual(ready['status'],'ready')
        self.assertEqual(ready['download']['transport'],'verified_pending_archive')
        self.assertEqual(ready['archive_cleanup']['status'],'removed')
        self.assertFalse(list(self.root.glob('.download-*')))
        self.assertFalse(list(self.root.glob('.pending-install-*')))
        self.assertTrue(official.is_file())
    def test_pending_cache_tampering_is_refused_without_deleting_user_file(self):
        commit='d'*40;archive=self.base/'user.zip'
        with zipfile.ZipFile(archive,'w') as handle:
            for file in self.source.rglob('*'):
                if file.is_file():handle.write(file,'repo-'+commit+'/'+file.relative_to(self.source).as_posix())
        digest=hashlib.sha256(archive.read_bytes()).hexdigest()
        args={'repo':'owner/repo','commit':commit,'archive_sha256':digest,'archive_file':archive,
              'install_root':self.root,'data_dir':self.data,'no_browser':True}
        with patch.object(bootstrap.subprocess,'run',side_effect=self.background_result):pending=bootstrap.install(**args)
        argv=pending['resume']['argv'];cached=Path(argv[argv.index('--archive-file')+1])
        cached.write_bytes(b'tampered')
        with self.assertRaises(bootstrap.InstallError) as caught:bootstrap.install(**args)
        self.assertEqual(caught.exception.code,'archive_checksum_mismatch')
        self.assertTrue(archive.exists());self.assertTrue(cached.exists())
        self.assertFalse((self.root/'current.json').exists())
        receipt=self.root/('.pending-install-'+commit+'.json')
        record=json.loads(receipt.read_text());record['archive_name']='../user.zip';receipt.write_text(json.dumps(record))
        with self.assertRaises(bootstrap.InstallError) as caught:bootstrap.install(**args)
        self.assertEqual(caught.exception.code,'pending_archive_invalid');self.assertTrue(archive.exists())
    def prepare_remote_pending(self):
        commit='e'*40;archive=self.base/'owned-source.zip'
        with zipfile.ZipFile(archive,'w') as handle:
            for file in self.source.rglob('*'):
                if file.is_file():handle.write(file,'repo-'+commit+'/'+file.relative_to(self.source).as_posix())
        args={'repo':'owner/repo','commit':commit,'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
              'archive_file':archive,'install_root':self.root,'data_dir':self.data,'no_browser':True}
        with patch.object(bootstrap.subprocess,'run',side_effect=self.background_result):result=bootstrap.install(**args)
        argv=result['resume']['argv'];cached=Path(argv[argv.index('--archive-file')+1])
        return args,cached,self.root/('.pending-install-'+commit+'.json')
    def test_current_write_failure_preserves_pending_recovery_archive(self):
        args,cached,receipt=self.prepare_remote_pending()
        original=bootstrap.atomic_json
        def fail_current(path,value):
            if path.name=='current.json':raise OSError('simulated pointer write failure')
            return original(path,value)
        with patch.object(bootstrap,'atomic_json',side_effect=fail_current):
            with self.assertRaises(OSError):bootstrap.install(**args)
        self.assertTrue(cached.is_file());self.assertTrue(receipt.is_file())
        self.assertFalse((self.root/'current.json').exists())
    def test_missing_pending_zip_after_partial_cleanup_recovers_from_verified_source(self):
        args,cached,receipt=self.prepare_remote_pending()
        cached.unlink()
        ready=bootstrap.install(**args)
        self.assertEqual(ready['status'],'ready')
        self.assertEqual(ready['archive_cleanup'],{'status':'removed','owned_files':1})
        self.assertFalse(receipt.exists());self.assertTrue(args['archive_file'].exists())
    def test_locked_pending_receipt_does_not_turn_next_install_into_permanent_failure(self):
        args,cached,receipt=self.prepare_remote_pending()
        original=Path.unlink;blocked=[False]
        def unlink(path,*positional,**kwargs):
            if path.resolve()==receipt.resolve() and not blocked[0]:
                blocked[0]=True;raise PermissionError('simulated Windows receipt lock')
            return original(path,*positional,**kwargs)
        with patch.object(Path,'unlink',unlink):first=bootstrap.install(**args)
        self.assertEqual(first['status'],'ready')
        self.assertEqual(first['archive_cleanup']['status'],'deferred')
        self.assertFalse(cached.exists());self.assertTrue(receipt.exists())
        second=bootstrap.install(**args)
        self.assertEqual(second['status'],'ready');self.assertFalse(receipt.exists())
    def test_remote_upgrade_restart_reuses_verified_zip_after_old_service_stops(self):
        self.install();before=(self.root/'current.json').read_bytes()
        commit='f'*40;archive=self.base/'new-release.zip'
        with zipfile.ZipFile(archive,'w') as handle:
            for file in self.source.rglob('*'):
                if file.is_file():handle.write(file,'repo-'+commit+'/'+file.relative_to(self.source).as_posix())
        args={'repo':'owner/repo','commit':commit,'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
              'install_root':self.root,'data_dir':self.data,'no_browser':True}
        pending=bootstrap.install(**args,archive_file=archive)
        self.assertEqual(pending['status'],'restart_required');self.assertFalse(pending['current_switched'])
        self.assertEqual((self.root/'current.json').read_bytes(),before)
        self.assertEqual(pending['resume']['type'],'complete_install')
        self.assertTrue(pending['resume']['archive_reverified_on_resume'])
        self.service=None
        with patch.object(bootstrap,'download_archive') as network:ready=bootstrap.install(**args)
        network.assert_not_called();self.assertEqual(ready['status'],'ready')
        self.assertEqual(ready['release_id'],commit)
        self.assertFalse(list(self.root.glob('.download-*')))
        self.assertFalse(list(self.root.glob('.pending-install-*')))
        self.assertTrue(archive.exists())

if __name__=='__main__':unittest.main()
