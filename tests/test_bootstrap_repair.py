# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from fagao.integrity import ReleaseLease

SOURCE=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('fagao_repair_bootstrap',SOURCE/'scripts/bootstrap.py')
bootstrap=importlib.util.module_from_spec(spec);spec.loader.exec_module(bootstrap)


class VerifiedArchiveRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=self.base/'app';self.data=self.base/'data';self.data.mkdir()
        self.sentinel=self.data/'keep.sqlite3';self.sentinel.write_bytes(b'original user data')
        self.source=self.base/'source';self.source.mkdir()
        for name in (*bootstrap.REQUIRED,'fagao/membership.py','fagao/runtime.py','fagao/network.py','fagao/code_seal.py'):
            path=self.source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('# '+bootstrap.APP+'\n')
        (self.source/'fagao/integrity.py').write_bytes((SOURCE/'fagao/integrity.py').read_bytes())
        self.commit='d'*40;self.archive=self.base/'original.zip'
        with zipfile.ZipFile(self.archive,'w') as package:
            for path in self.source.rglob('*'):
                if path.is_file():package.write(path,'fagao-scan-workbench-'+self.commit+'/'+path.relative_to(self.source).as_posix())
        self.digest=hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.service=None;self.calls=[]
        self.addCleanup(patch.stopall)
        patch.object(bootstrap,'health',side_effect=lambda port:self.service).start()
        self.launch=patch.object(bootstrap.subprocess,'run',side_effect=self.start).start()
        self.component=patch.object(bootstrap,'provision_submission',return_value={'status':'installed'}).start()
        self.protection=patch.object(bootstrap,'protect_release',side_effect=self.seal_fixture).start()

    def start(self,command,**kwargs):
        self.calls.append(command)
        self.service={'app':bootstrap.APP,'data_dir':str(self.data),'source_root':str(Path(command[1]).parent.parent)}
        return SimpleNamespace(returncode=0,stdout=json.dumps({'status':'started','readiness':{'status':'ready'},'pid':123}),stderr='')

    def seal_fixture(self,stage,originals,data,*,target=None,metadata=None,discard=False):
        if discard:return metadata
        if target:return metadata
        packed=b'{"fixture":"not a real protection envelope"}'
        (stage/'.fagao-code-seal.json').write_bytes(packed)
        modules={}
        for name in ('membership','runtime','network'):
            relative='fagao/'+name+'.py';stub=('# protected fixture '+name+'\n').encode()
            (stage/relative).write_bytes(stub)
            modules[relative]={'module':'fagao.'+name,'source_sha256':originals[relative],
                'stub_sha256':hashlib.sha256(stub).hexdigest(),'token_sha256':'a'*64}
        return {'format':1,'stage_name':stage.name,'pack_file':'.fagao-code-seal.json','pack_sha256':hashlib.sha256(packed).hexdigest(),
            'data_root':str(data),'modules':modules}

    def install(self,**extra):
        return bootstrap.install(repo='HeyFagao/fagao-scan-workbench',commit=self.commit,
            archive_sha256=self.digest,archive_file=self.archive,install_root=self.root,data_dir=self.data,
            no_browser=True,**extra)

    def damaged(self):
        first=self.install();self.service=None
        release=Path(first['release_dir']);(release/'fagao/server.py').write_text('damaged original')
        return release,(self.root/'current.json').read_bytes()

    def test_trusted_archive_repairs_owned_program_and_preserves_the_old_copy(self):
        release,pointer=self.damaged()
        result=self.install()
        self.assertEqual(result['repair']['status'],'repaired')
        backup=Path(result['repair']['backup_dir'])
        self.assertEqual((backup/'fagao/server.py').read_text(),'damaged original')
        self.assertEqual((release/'fagao/server.py').read_text(),'# '+bootstrap.APP+'\n')
        self.assertEqual(self.sentinel.read_bytes(),b'original user data')
        self.assertEqual((self.root/'current.json').read_bytes(),pointer)

    def test_running_exact_release_is_not_replaced_and_returns_reviewable_resume(self):
        release,pointer=self.damaged()
        self.service={'app':bootstrap.APP,'data_dir':str(self.data),'source_root':str(release)}
        result=self.install()
        self.assertEqual(result['status'],'restart_required')
        self.assertEqual(result['repair']['status'],'blocked_running')
        self.assertEqual((release/'fagao/server.py').read_text(),'damaged original')
        self.assertEqual((self.root/'current.json').read_bytes(),pointer)
        self.assertTrue(result['resume']['archive_reverified_on_resume'])

    def test_release_used_on_another_port_is_protected_by_the_shared_lease(self):
        release,pointer=self.damaged()
        with ReleaseLease(release):result=self.install()
        self.assertEqual(result['repair']['status'],'blocked_running')
        self.assertEqual((release/'fagao/server.py').read_text(),'damaged original')
        self.assertEqual((self.root/'current.json').read_bytes(),pointer)
        self.assertEqual(result['protection_cleanup']['status'],'removed')
        discarded=[call for call in self.protection.call_args_list if call.kwargs.get('discard')]
        self.assertEqual(len(discarded),1)
        self.assertTrue(discarded[0].args[0].name.startswith('.staging-'))
        self.assertNotEqual(discarded[0].args[0],release)

    def test_deferred_staging_key_cleanup_retains_its_pack_reference(self):
        release,pointer=self.damaged()
        def cleanup_locked(stage,originals,data,*,target=None,metadata=None,discard=False):
            if discard:
                error=bootstrap.InstallError('fixture cleanup locked',code='installation_integrity_failed')
                error.protection_diagnostic={'repair_required':False,'deferred':True}
                raise error
            return self.seal_fixture(stage,originals,data,target=target,metadata=metadata)
        self.protection.side_effect=cleanup_locked
        with ReleaseLease(release):result=self.install()
        cleanup=result['protection_cleanup'];self.assertEqual(cleanup['status'],'deferred')
        retained=Path(cleanup['stage_dir'])
        self.assertTrue((retained/'.fagao-code-seal.json').is_file())
        self.assertTrue((retained/'.fagao-release.json').is_file())
        self.assertEqual((release/'fagao/server.py').read_text(),'damaged original')
        self.assertEqual((self.root/'current.json').read_bytes(),pointer)

    def test_failed_start_restores_the_original_directory_without_touching_data(self):
        release,pointer=self.damaged()
        self.launch.side_effect=lambda *args,**kwargs:SimpleNamespace(returncode=1,stdout='',stderr='fixture failure')
        with self.assertRaises(bootstrap.InstallError):self.install()
        self.assertEqual((release/'fagao/server.py').read_text(),'damaged original')
        self.assertEqual((self.root/'current.json').read_bytes(),pointer)
        self.assertEqual(self.sentinel.read_bytes(),b'original user data')

    def test_component_failure_rolls_back_code_and_preserves_pointer(self):
        release,pointer=self.damaged();self.component.side_effect=bootstrap.InstallError('fixture component failure')
        with self.assertRaises(bootstrap.InstallError):self.install()
        self.assertEqual((release/'fagao/server.py').read_text(),'damaged original')
        self.assertEqual((self.root/'current.json').read_bytes(),pointer)

    def test_invalid_new_archive_never_touches_the_damaged_installation(self):
        release,pointer=self.damaged();self.archive.write_bytes(b'not the verified archive')
        with self.assertRaises(bootstrap.InstallError):self.install()
        self.assertEqual((release/'fagao/server.py').read_text(),'damaged original')
        self.assertEqual((self.root/'current.json').read_bytes(),pointer)

    def test_locked_secure_storage_is_deferred_without_replacing_or_resealing(self):
        result=self.install();self.service=None;release=Path(result['release_dir']);before=(release/'.fagao-release.json').read_bytes()
        def unavailable(stage,originals,data,*,target=None,metadata=None,discard=False):
            error=bootstrap.InstallError('fixture secure storage locked',code='installation_integrity_failed')
            error.protection_diagnostic={'repair_required':False,'deferred':True,'recovery':'unlock_keychain'}
            raise error
        self.protection.side_effect=unavailable
        with self.assertRaises(bootstrap.InstallError) as caught:self.install()
        self.assertFalse(caught.exception.diagnostic()['repair_required'])
        self.assertEqual((release/'.fagao-release.json').read_bytes(),before)
        self.assertFalse((self.root/'quarantine').exists())

    def test_missing_invalid_and_modified_receipt_can_be_repaired_from_original_archive(self):
        release,pointer=self.damaged()
        receipt=release/'.fagao-release.json'
        receipt.unlink()
        first=self.install();self.assertEqual(first['repair']['status'],'repaired');self.service=None
        receipt.write_text('{broken fixture receipt')
        second=self.install();self.assertEqual(second['repair']['status'],'repaired');self.service=None
        value=json.loads(receipt.read_text());value['original_files']['fagao/server.py']='0'*64
        receipt.write_text(json.dumps(value))
        third=self.install();self.assertEqual(third['repair']['status'],'repaired')
        self.assertEqual((self.root/'current.json').read_bytes(),pointer)
        self.assertEqual(self.sentinel.read_bytes(),b'original user data')

    def test_installed_protected_pack_is_not_a_source_for_another_installation(self):
        result=self.install();release=Path(result['release_dir'])
        with self.assertRaises(bootstrap.InstallError) as caught:
            bootstrap.install(source_dir=release,install_root=self.base/'other-app',data_dir=self.base/'other-data')
        self.assertEqual(caught.exception.code,'protected_source_not_allowed')

    def test_invalid_managed_pointer_is_rebuilt_only_after_verified_start(self):
        release,_=self.damaged();(self.root/'current.json').write_text('{broken pointer')
        result=self.install()
        self.assertEqual(result['repair']['status'],'repaired')
        self.assertEqual(json.loads((self.root/'current.json').read_text())['release_id'],release.name)
        self.assertEqual(self.sentinel.read_bytes(),b'original user data')


if __name__=='__main__':unittest.main()
