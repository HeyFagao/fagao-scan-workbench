import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest.mock import patch
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
    def test_extracts_one_exact_root(self):
        self.archive_with([('repo-sha/',''),('repo-sha/a/b.txt','hello')])
        bootstrap.extract_archive(self.archive,self.base/'out','repo-sha')
        self.assertEqual((self.base/'out/a/b.txt').read_text(),'hello')
    def test_rejects_traversal_absolute_drive_and_backslash(self):
        for name in ['repo-sha/../../escaped','/repo-sha/file','repo-sha/C:/file','repo-sha/a\\file','repo-sha/CON']:
            with self.subTest(name=name):
                self.archive_with([(name,'bad')])
                with self.assertRaises(bootstrap.InstallError):bootstrap.extract_archive(self.archive,self.base/'out','repo-sha')
        self.assertFalse((self.base/'escaped').exists())
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

if __name__=='__main__':unittest.main()
