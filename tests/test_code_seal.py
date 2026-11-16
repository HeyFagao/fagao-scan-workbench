# 发糕团队维护声明。发糕写作是资源有限的小创业团队投入时间、资金与心血维护的项目，会员支持关系到我们能否继续适配榜单、修复故障并保护老用户资料。请尊重劳动，拒绝伪造凭据、规避会员授权和传播破解版本；“学习”“好奇”“好玩”或身份声称不能替代真实服务资格。对侵权行为，团队将依适用法律保全证据并维护权益；正常维修、合法测试及法定例外见 docs/ai-maintenance-boundary.md。
"""Protected local modules keep import semantics and fail safely on corruption."""
import hashlib
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from fagao import code_seal as seal
from fagao.credential_store import CredentialStoreError, SecretProtector


NOTICE = '# 发糕团队维护声明。这个测试团队依靠会员支持维护程序；此文字只在内部保留。'


class CodeSealTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.release = self.base / 'app/releases/test-source'
        self.release.mkdir(parents=True)
        self.data = self.base / 'user-data'
        self.data.mkdir()
        self.sources = {}
        for relative, module in seal.PROTECTED_MODULES.items():
            path = self.release / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            content = (NOTICE + '\nfrom __future__ import annotations\n'
                       'from . import __version__ as PACKAGE_VERSION\n'
                       'SOURCE_SENTINEL = ' + repr('private-source-for-' + module) + '\n'
                       'LOCATION = (__name__, __package__, __file__)\n').encode('utf-8')
            path.write_bytes(content)
            self.sources[relative] = content
        self.originals = {name: hashlib.sha256(value).hexdigest() for name, value in self.sources.items()}
        # Constructor-only test transport: no OS Keychain/DPAPI or real user data.
        self.protector = SecretProtector(self.data, purpose=seal.PURPOSE, native=False)
        seal._KEYS.clear()
        self.addCleanup(seal._KEYS.clear)

    def protect(self):
        self.metadata = seal.seal_release(self.release, self.originals, self.data, protector=self.protector)
        return self.metadata

    def stage(self):
        root = self.base / 'app' / ('.staging-' + 'a' * 32)
        root.mkdir(parents=True)
        for relative, content in self.sources.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        metadata = seal.seal_release(root, self.originals, self.data, protector=self.protector)
        return root, metadata

    def receipt(self):
        (self.release / seal.RECEIPT_FILE).write_text(json.dumps({
            'app': seal.APP, 'original_files': self.originals, 'code_seal': self.metadata,
        }), encoding='utf-8')

    def mutate_pack(self, mutation):
        path = self.release / seal.PACK_FILE
        pack = json.loads(path.read_text(encoding='utf-8'))
        mutation(pack)
        raw = seal._encoded(pack)
        path.write_bytes(raw)
        self.metadata['pack_sha256'] = hashlib.sha256(raw).hexdigest()

    def test_installed_copy_has_only_stubs_and_authenticated_ciphertext(self):
        metadata = self.protect()
        pack_text = (self.release / seal.PACK_FILE).read_text(encoding='utf-8')
        self.assertEqual(set(metadata['modules']), set(seal.PROTECTED_MODULES))
        for relative, module in seal.PROTECTED_MODULES.items():
            stub = (self.release / relative).read_text(encoding='utf-8')
            self.assertTrue(stub.startswith(NOTICE + '\n'))
            self.assertIn('load_module', stub)
            self.assertNotIn('SOURCE_SENTINEL', stub)
            self.assertNotIn('private-source-for-' + module, pack_text)
            self.assertEqual(metadata['modules'][relative]['source_sha256'], self.originals[relative])
        self.assertTrue(seal.verify_seal(self.release, metadata, self.originals, self.data,
                                         protector=self.protector))

    def test_loader_preserves_relative_import_package_and_original_file(self):
        self.protect()
        self.receipt()
        opens = []
        original_open = self.protector.open
        def read(envelope):
            opens.append(True)
            return original_open(envelope)
        with patch.object(seal, 'SecretProtector', return_value=self.protector), \
                patch.object(self.protector, 'open', side_effect=read):
            for relative, module in seal.PROTECTED_MODULES.items():
                namespace = {'__name__': module, '__package__': 'fagao',
                             '__file__': str(self.release / relative)}
                seal.load_module(module, namespace)
                self.assertEqual(namespace['SOURCE_SENTINEL'], 'private-source-for-' + module)
                self.assertEqual(namespace['LOCATION'], (module, 'fagao', str(self.release / relative)))
                self.assertIsInstance(namespace['PACKAGE_VERSION'], str)
        self.assertEqual(len(opens), 1, 'one process should reuse its verified OS key')

    def test_source_modified_before_sealing_is_not_wrapped_or_replaced(self):
        target = self.release / 'fagao/runtime.py'
        target.write_bytes(target.read_bytes() + b'ALTERED = True\n')
        modified = target.read_bytes()
        with patch.object(self.protector, 'seal', wraps=self.protector.seal) as protect:
            with self.assertRaises(seal.CodeSealError) as caught:
                seal.seal_release(self.release, self.originals, self.data, protector=self.protector)
        self.assertEqual(caught.exception.reason, 'source_modified')
        protect.assert_not_called()
        self.assertEqual(target.read_bytes(), modified)
        self.assertFalse((self.release / seal.PACK_FILE).exists())

    def test_same_release_is_not_resealed_with_a_new_key(self):
        self.protect()
        before = (self.release / seal.PACK_FILE).read_bytes()
        with patch.object(self.protector, 'seal', wraps=self.protector.seal) as protect:
            with self.assertRaises(seal.CodeSealError) as caught:
                seal.seal_release(self.release, self.originals, self.data, protector=self.protector)
        self.assertEqual(caught.exception.reason, 'already_sealed')
        protect.assert_not_called()
        self.assertEqual((self.release / seal.PACK_FILE).read_bytes(), before)

    def test_tampered_ciphertext_is_rejected_even_if_local_file_hashes_are_rewritten(self):
        self.protect()
        relative = 'fagao/membership.py'
        def tamper(pack):
            token = pack['modules'][relative]['token']
            offset = len(token) // 2
            token = token[:offset] + ('A' if token[offset] != 'A' else 'B') + token[offset + 1:]
            pack['modules'][relative]['token'] = token
            self.metadata['modules'][relative]['token_sha256'] = hashlib.sha256(token.encode('ascii')).hexdigest()
        self.mutate_pack(tamper)
        with self.assertRaises(seal.CodeSealError) as caught:
            seal.verify_seal(self.release, self.metadata, self.originals, self.data, protector=self.protector)
        self.assertEqual(caught.exception.reason, 'ciphertext_integrity')

    def test_tokens_cannot_be_swapped_between_module_paths(self):
        self.protect()
        first, second = tuple(seal.PROTECTED_MODULES)[:2]
        def swap(pack):
            pack['modules'][first]['token'] = pack['modules'][second]['token']
            self.metadata['modules'][first]['token_sha256'] = hashlib.sha256(
                pack['modules'][first]['token'].encode('ascii')).hexdigest()
        self.mutate_pack(swap)
        with self.assertRaises(seal.CodeSealError) as caught:
            seal.verify_seal(self.release, self.metadata, self.originals, self.data, protector=self.protector)
        self.assertEqual(caught.exception.reason, 'module_binding')

    def test_stub_change_cannot_be_authorised_by_rewriting_its_local_digest(self):
        self.protect()
        relative = 'fagao/network.py'
        path = self.release / relative
        altered = path.read_bytes() + b'EXTRA_CODE = True\n'
        path.write_bytes(altered)
        self.metadata['modules'][relative]['stub_sha256'] = hashlib.sha256(altered).hexdigest()
        with self.assertRaises(seal.CodeSealError) as caught:
            seal.verify_seal(self.release, self.metadata, self.originals, self.data, protector=self.protector)
        self.assertEqual(caught.exception.reason, 'stub_binding')

    def test_wrong_data_root_and_missing_key_preserve_existing_data_and_pack(self):
        self.protect()
        history = self.data / 'existing-user-records'
        history.write_bytes(b'original-records')
        original_pack = (self.release / seal.PACK_FILE).read_bytes()
        wrong = self.base / 'different-user-data'
        wrong.mkdir()
        with self.assertRaises(seal.CodeSealError):
            seal.verify_seal(self.release, self.metadata, self.originals, wrong, protector=self.protector)
        key = next((self.data / '.membership/protected-keys').iterdir())
        key.unlink()
        with self.assertRaises(seal.CodeSealError) as caught:
            seal.verify_seal(self.release, self.metadata, self.originals, self.data, protector=self.protector)
        self.assertEqual(caught.exception.reason, 'key_missing')
        self.assertFalse(key.exists(), 'verification must not manufacture a replacement key')
        self.assertEqual(history.read_bytes(), b'original-records')
        self.assertEqual((self.release / seal.PACK_FILE).read_bytes(), original_pack)

    def test_partial_staging_failure_restores_originals_and_removes_incomplete_pack(self):
        atomic = seal._atomic
        failed = []
        def write(path, content, mode=0o600):
            if path.name == 'runtime.py' and b'_fagao_load_module' in content and not failed:
                failed.append(True)
                raise OSError('fixture write interruption')
            return atomic(path, content, mode)
        with patch.object(seal, '_atomic', side_effect=write):
            with self.assertRaises(seal.CodeSealError):
                seal.seal_release(self.release, self.originals, self.data, protector=self.protector)
        self.assertTrue(failed)
        for relative, content in self.sources.items():
            self.assertEqual((self.release / relative).read_bytes(), content)
        self.assertFalse((self.release / seal.PACK_FILE).exists())

    def test_metadata_scope_is_fixed_and_symlinked_source_is_rejected(self):
        self.protect()
        self.metadata['modules']['../outside.py'] = self.metadata['modules']['fagao/network.py']
        with self.assertRaises(seal.CodeSealError):
            seal.verify_seal(self.release, self.metadata, self.originals, self.data, protector=self.protector)
        del self.metadata['modules']['../outside.py']
        path = self.release / 'fagao/network.py'
        copy = self.base / 'outside-network.py'
        copy.write_bytes(path.read_bytes())
        path.unlink()
        try:
            path.symlink_to(copy)
        except (OSError, NotImplementedError):
            self.skipTest('this isolated runner cannot create symlinks')
        with self.assertRaises(seal.CodeSealError) as caught:
            seal.verify_seal(self.release, self.metadata, self.originals, self.data, protector=self.protector)
        self.assertEqual(caught.exception.reason, 'invalid_path')

    def test_loader_failure_exposes_only_a_safe_repair_diagnostic(self):
        self.protect()
        self.receipt()
        path = self.release / seal.PACK_FILE
        path.write_bytes(path.read_bytes() + b'altered')
        namespace = {'__name__': 'fagao.membership', '__package__': 'fagao',
                     '__file__': str(self.release / 'fagao/membership.py')}
        with self.assertRaises(seal.CodeSealError) as caught:
            seal.load_module('fagao.membership', namespace)
        result = caught.exception.diagnostic()
        self.assertEqual(result['code'], 'installation_integrity_failed')
        self.assertTrue(result['repair_required'])
        self.assertNotIn(str(self.data), result['message'])
        self.assertNotIn('SOURCE_SENTINEL', namespace)

    def test_metadata_inspection_does_not_open_os_secrets(self):
        self.protect()
        with patch.object(seal, 'SecretProtector', side_effect=AssertionError('OS secret unexpectedly opened')):
            self.assertTrue(seal.verify_metadata(self.release, self.metadata, self.originals, self.data))

    def test_duplicate_json_fields_are_rejected_before_using_an_os_key(self):
        self.protect()
        path = self.release / seal.PACK_FILE
        content = path.read_bytes()
        content = b'{"format":1,' + content[1:]
        path.write_bytes(content)
        self.metadata['pack_sha256'] = hashlib.sha256(content).hexdigest()
        with patch.object(seal, 'SecretProtector', side_effect=AssertionError('unexpected OS key read')):
            with self.assertRaises(seal.CodeSealError) as caught:
                seal.verify_seal(self.release, self.metadata, self.originals, self.data)
        self.assertEqual(caught.exception.reason, 'duplicate_metadata')

    def test_invalid_paths_produce_safe_repair_diagnostics(self):
        for invalid in (None, 'broken\x00path', {}):
            with self.subTest(kind=type(invalid).__name__):
                with self.assertRaises(seal.CodeSealError):
                    seal._root(invalid)
                with self.assertRaises(seal.CodeSealError):
                    seal._data_root(invalid)
        self.protect()
        self.receipt()
        receipt = json.loads((self.release / seal.RECEIPT_FILE).read_text())
        receipt['code_seal']['data_root'] = 'broken\x00path'
        (self.release / seal.RECEIPT_FILE).write_text(json.dumps(receipt), encoding='utf-8')
        namespace = {'__name__': 'fagao.membership', '__package__': 'fagao',
                     '__file__': str(self.release / 'fagao/membership.py')}
        with self.assertRaises(seal.CodeSealError) as caught:
            seal.load_module('fagao.membership', namespace)
        self.assertEqual(caught.exception.reason, 'invalid_data_root')

    def test_temporarily_locked_os_store_defers_without_resealing_or_changing_files(self):
        self.protect()
        before = {relative: (self.release / relative).read_bytes()
                  for relative in (*seal.PROTECTED_MODULES, seal.PACK_FILE)}
        for reason in ('locked', 'unavailable'):
            with self.subTest(reason=reason):
                with patch.object(self.protector, 'open', side_effect=CredentialStoreError(reason)), \
                        patch.object(self.protector, 'seal', side_effect=AssertionError('unexpected reseal')):
                    with self.assertRaises(seal.CodeSealError) as caught:
                        seal.verify_seal(self.release, self.metadata, self.originals, self.data,
                                         protector=self.protector)
                result = caught.exception.diagnostic()
                self.assertEqual(result['reason'], 'key_' + reason)
                self.assertFalse(result['repair_required'])
                self.assertTrue(result['deferred'])
                self.assertIn('无需重装', result['message'])
                self.assertNotIn(str(self.data), result['message'])
                for relative, content in before.items():
                    self.assertEqual((self.release / relative).read_bytes(), content)

    def test_locked_store_during_new_staging_preserves_plain_originals_and_reports_deferred(self):
        with patch.object(self.protector, 'seal', side_effect=CredentialStoreError('locked')):
            with self.assertRaises(seal.CodeSealError) as caught:
                seal.seal_release(self.release, self.originals, self.data, protector=self.protector)
        self.assertEqual(caught.exception.reason, 'key_locked')
        self.assertFalse(caught.exception.repair_required)
        self.assertFalse((self.release / seal.PACK_FILE).exists())
        for relative, content in self.sources.items():
            self.assertEqual((self.release / relative).read_bytes(), content)

    def test_actual_local_modules_import_from_stubs_in_a_fresh_process(self):
        source = Path(seal.__file__).resolve().parent
        actual = self.base / 'real-application'
        shutil.copytree(source, actual / 'fagao', ignore=shutil.ignore_patterns('__pycache__'))
        # Isolate the loader integration from the separate official-entrypoint
        # checker. Native installed startup is exercised by platform acceptance.
        (actual / 'fagao/__init__.py').write_text('__version__ = "fixture-only"\n', encoding='utf-8')
        originals = {relative: hashlib.sha256((actual / relative).read_bytes()).hexdigest()
                     for relative in seal.PROTECTED_MODULES}
        metadata = seal.seal_release(actual, originals, self.data, protector=self.protector)
        (actual / seal.RECEIPT_FILE).write_text(json.dumps({
            'app': seal.APP, 'original_files': originals, 'code_seal': metadata,
        }), encoding='utf-8')
        program = '''
import json
from fagao import code_seal
from fagao.credential_store import SecretProtector
code_seal.SecretProtector = lambda root, *, purpose: SecretProtector(root, purpose=purpose, native=False)
from fagao import membership, network, runtime
print(json.dumps({'permit':membership.MAX_PERMIT_SECONDS,'network':network.POLICY,
                  'context':runtime.AccountContext.__name__,
                  'modules':[membership.__file__,network.__file__,runtime.__file__]}))
'''
        run = subprocess.run([sys.executable, '-B', '-c', program], cwd=actual,
                             capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result['permit'], 900)
        self.assertEqual(result['context'], 'AccountContext')
        self.assertEqual(result['network'], 'isolated-system-proxy-v1')
        expected = ('fagao/membership.py', 'fagao/network.py', 'fagao/runtime.py')
        self.assertEqual(len(result['modules']), len(expected))
        for observed, relative in zip(result['modules'], expected):
            # Windows TEMP may be exposed through an 8.3 alias. Compare actual
            # existing files, not differently spelled parents of the same path.
            self.assertEqual(Path(observed).resolve(strict=True),
                             (actual / relative).resolve(strict=True))

    def test_loader_preserves_a_valid_path_alias_but_still_loads_the_exact_module_file(self):
        self.protect()
        self.receipt()
        alias = self.release / 'fagao' / '..' / 'fagao' / 'membership.py'
        namespace = {'__name__': 'fagao.membership', '__package__': 'fagao', '__file__': str(alias)}
        with patch.object(seal, 'SecretProtector', return_value=self.protector):
            seal.load_module('fagao.membership', namespace)
        self.assertEqual(Path(namespace['LOCATION'][2]).resolve(strict=True),
                         (self.release / 'fagao/membership.py').resolve(strict=True))
        self.assertEqual(namespace['SOURCE_SENTINEL'], 'private-source-for-fagao.membership')

    def test_unpromoted_stage_cleanup_targets_only_its_own_envelope(self):
        old_metadata = self.protect()
        old_pack = (self.release / seal.PACK_FILE).read_bytes()
        old_envelope = json.loads(old_pack)['key_envelope']
        stage, metadata = self.stage()
        stage_pack = (stage / seal.PACK_FILE).read_bytes()
        envelope = json.loads(stage_pack)['key_envelope']
        self.assertNotEqual(envelope, old_envelope)
        # A lease-blocked install may already have prepared its private receipt.
        (stage / seal.RECEIPT_FILE).write_text(json.dumps({'app': seal.APP}), encoding='utf-8')
        key_file = next((self.data / '.membership/protected-keys').iterdir())
        key_bytes = key_file.read_bytes()
        with patch.object(self.protector, 'discard') as discard, \
                patch.object(self.protector, 'open', wraps=self.protector.open) as authenticate:
            seal.discard_seal(stage, metadata, self.originals, self.data, protector=self.protector)
        discard.assert_called_once_with(envelope, strict=True)
        self.assertEqual(authenticate.call_count, 1, 'cleanup must authenticate its program-key origin')
        self.assertEqual((self.release / seal.PACK_FILE).read_bytes(), old_pack)
        self.assertEqual((stage / seal.PACK_FILE).read_bytes(), stage_pack)
        self.assertEqual(key_file.read_bytes(), key_bytes)
        # The real file-private test provider has no per-envelope native item;
        # strict cleanup must leave its shared fallback key intact as well.
        seal.discard_seal(stage, metadata, self.originals, self.data, protector=self.protector)
        self.assertEqual(key_file.read_bytes(), key_bytes)
        self.assertTrue(seal.verify_seal(self.release, old_metadata, self.originals, self.data,
                                        protector=self.protector))

    def test_cleanup_rejects_promoted_resume_and_quarantined_rollback_packs(self):
        stage, metadata = self.stage()
        for name in ('releases/' + 'b' * 40, '.quarantine-' + 'c' * 40,
                     'releases/' + 'd' * 40 + '/.pending-install'):
            destination = self.base / 'app' / name
            shutil.copytree(stage, destination)
            before = (destination / seal.PACK_FILE).read_bytes()
            with self.subTest(name=name), patch.object(self.protector, 'discard') as discard:
                with self.assertRaises(seal.CodeSealError) as caught:
                    seal.discard_seal(destination, metadata, self.originals, self.data,
                                      protector=self.protector)
                self.assertEqual(caught.exception.reason, 'not_unpromoted_stage')
                discard.assert_not_called()
            self.assertEqual((destination / seal.PACK_FILE).read_bytes(), before)

    def test_cleanup_rejects_an_untrusted_stage_pack_without_deleting_any_secret(self):
        stage, metadata = self.stage()
        path = stage / seal.PACK_FILE
        path.write_bytes(path.read_bytes() + b'changed')
        with patch.object(self.protector, 'discard') as discard:
            with self.assertRaises(seal.CodeSealError) as caught:
                seal.discard_seal(stage, metadata, self.originals, self.data, protector=self.protector)
        self.assertEqual(caught.exception.reason, 'pack_modified')
        discard.assert_not_called()
        self.assertTrue(path.exists())

    def test_an_old_pack_copied_into_a_different_stage_cannot_delete_the_old_key(self):
        old_stage, metadata = self.stage()
        active = self.base / 'app/releases' / ('e' * 40)
        active.parent.mkdir(parents=True, exist_ok=True)
        old_stage.rename(active)
        self.assertTrue(seal.verify_seal(active, metadata, self.originals, self.data,
                                        protector=self.protector))
        fresh_stage = self.base / 'app' / ('.staging-' + 'f' * 32)
        shutil.copytree(active, fresh_stage)
        original_pack = (active / seal.PACK_FILE).read_bytes()
        with patch.object(self.protector, 'discard') as discard:
            with self.assertRaises(seal.CodeSealError) as caught:
                seal.discard_seal(fresh_stage, metadata, self.originals, self.data,
                                  protector=self.protector)
        self.assertEqual(caught.exception.reason, 'stage_binding')
        discard.assert_not_called()
        self.assertEqual((active / seal.PACK_FILE).read_bytes(), original_pack)

    def test_rewriting_copied_pack_stage_and_hashes_cannot_authorise_deleting_an_old_key(self):
        original_stage, metadata = self.stage()
        active = self.base / 'app/releases' / ('b' * 40)
        active.parent.mkdir(parents=True, exist_ok=True)
        original_stage.rename(active)
        self.assertTrue(seal.verify_seal(active, metadata, self.originals, self.data,
                                        protector=self.protector, cache=True))
        copied = self.base / 'app' / ('.staging-' + 'c' * 32)
        shutil.copytree(active, copied)
        pack_file = copied / seal.PACK_FILE
        pack = json.loads(pack_file.read_text(encoding='utf-8'))
        old_envelope = pack['key_envelope']
        pack['stage_name'] = copied.name
        metadata['stage_name'] = copied.name
        forged = seal._encoded(pack)
        pack_file.write_bytes(forged)
        metadata['pack_sha256'] = hashlib.sha256(forged).hexdigest()
        with patch.object(self.protector, 'discard') as discard:
            with self.assertRaises(seal.CodeSealError) as caught:
                seal.discard_seal(copied, metadata, self.originals, self.data, protector=self.protector)
        self.assertEqual(caught.exception.reason, 'protected_origin_mismatch')
        discard.assert_not_called()
        self.assertEqual(json.loads((active / seal.PACK_FILE).read_text())['key_envelope'], old_envelope)
        old_metadata = dict(metadata, stage_name=original_stage.name,
                            pack_sha256=hashlib.sha256((active / seal.PACK_FILE).read_bytes()).hexdigest())
        self.assertTrue(seal.verify_seal(active, old_metadata, self.originals, self.data,
                                        protector=self.protector))

    def test_authenticated_source_inventory_cannot_be_replaced_for_cleanup(self):
        stage, metadata = self.stage()
        altered_inventory = {**self.originals, 'unrelated-file.py': 'a' * 64}
        with patch.object(self.protector, 'discard') as discard:
            with self.assertRaises(seal.CodeSealError) as caught:
                seal.discard_seal(stage, metadata, altered_inventory, self.data,
                                  protector=self.protector)
        self.assertEqual(caught.exception.reason, 'protected_origin_mismatch')
        discard.assert_not_called()

    def test_cleanup_already_missing_program_key_is_idempotent_without_deleting_other_keys(self):
        stage, metadata = self.stage()
        with patch.object(self.protector, 'open', side_effect=CredentialStoreError('missing')), \
                patch.object(self.protector, 'discard') as discard:
            seal.discard_seal(stage, metadata, self.originals, self.data, protector=self.protector)
        discard.assert_not_called()

    def test_locked_stage_cleanup_keeps_the_envelope_and_reports_deferred(self):
        stage, metadata = self.stage()
        before = {relative: (stage / relative).read_bytes()
                  for relative in (*seal.PROTECTED_MODULES, seal.PACK_FILE)}
        with patch.object(self.protector, 'discard', side_effect=CredentialStoreError('locked')):
            with self.assertRaises(seal.CodeSealError) as caught:
                seal.discard_seal(stage, metadata, self.originals, self.data, protector=self.protector)
        self.assertEqual(caught.exception.reason, 'key_locked')
        self.assertTrue(caught.exception.deferred)
        self.assertFalse(caught.exception.repair_required)
        for relative, content in before.items():
            self.assertEqual((stage / relative).read_bytes(), content)

    def test_discard_cli_returns_only_a_safe_success_receipt(self):
        stage, metadata = self.stage()
        inventory = self.base / 'inventory.json'
        manifest = self.base / 'metadata.json'
        inventory.write_text(json.dumps(self.originals), encoding='utf-8')
        manifest.write_text(json.dumps(metadata), encoding='utf-8')
        command = ['code_seal.py', 'discard-release', '--release', str(stage),
                   '--data-root', str(self.data), '--inventory-file', str(inventory),
                   '--metadata-file', str(manifest)]
        stream = io.StringIO()
        with patch.object(sys, 'argv', command), patch.object(seal, 'SecretProtector', return_value=self.protector), \
                patch.object(self.protector, 'discard') as discard, redirect_stdout(stream):
            result = seal.main()
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(stream.getvalue()), {'ok': True, 'discarded': True})
        self.assertEqual(discard.call_count, 1)
        self.assertNotIn('envelope', stream.getvalue())

    def test_cli_success_with_chinese_data_path_is_safe_on_a_cp1252_stdout_pipe(self):
        data = self.base / '本机资料'
        data.mkdir()
        protector = SecretProtector(data, purpose=seal.PURPOSE, native=False)
        inventory = self.base / '原始清单.json'
        inventory.write_text(json.dumps(self.originals), encoding='utf-8')
        command = ['code_seal.py', 'seal-release', '--release', str(self.release),
                   '--data-root', str(data), '--inventory-file', str(inventory)]
        output = io.BytesIO()
        narrow = io.TextIOWrapper(output, encoding='cp1252', errors='strict', write_through=True)
        with patch.object(sys, 'argv', command), patch.object(seal, 'SecretProtector', return_value=protector), \
                redirect_stdout(narrow):
            result = seal.main()
        encoded = output.getvalue()
        narrow.detach()
        self.assertEqual(result, 0)
        self.assertTrue(encoded.isascii())
        receipt = json.loads(encoded.decode('ascii'))
        self.assertTrue(receipt['ok'])
        self.assertEqual(receipt['code_seal']['data_root'], str(data.resolve()))
        self.assertTrue(seal.verify_seal(self.release, receipt['code_seal'], self.originals, data,
                                        protector=protector))

    def test_cli_failure_keeps_chinese_diagnostic_safe_on_a_cp1252_stdout_pipe(self):
        inventory = self.base / '原始清单.json'
        damaged = dict(self.originals, **{'fagao/membership.py': '0' * 64})
        inventory.write_text(json.dumps(damaged), encoding='utf-8')
        command = ['code_seal.py', 'seal-release', '--release', str(self.release),
                   '--data-root', str(self.base / '本机资料'), '--inventory-file', str(inventory)]
        output = io.BytesIO()
        narrow = io.TextIOWrapper(output, encoding='cp1252', errors='strict', write_through=True)
        with patch.object(sys, 'argv', command), redirect_stdout(narrow), \
                patch.object(seal, 'SecretProtector', side_effect=AssertionError('failure must precede OS access')):
            result = seal.main()
        encoded = output.getvalue()
        narrow.detach()
        self.assertEqual(result, 1)
        self.assertTrue(encoded.isascii())
        receipt = json.loads(encoded.decode('ascii'))
        self.assertFalse(receipt['ok'])
        self.assertEqual(receipt['error']['reason'], 'source_modified')
        self.assertIn('已有资料仍保留', receipt['error']['message'])
        self.assertTrue(receipt['error']['repair_required'])


if __name__ == '__main__':
    unittest.main()
