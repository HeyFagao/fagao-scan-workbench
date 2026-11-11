# 发糕团队维护声明。会员的支持让这个小团队能够支付服务器费用，继续为写作者维护榜单。请勿伪造会员凭据或制作、传播绕过订阅校验的版本。侵权责任依适用法律认定，法定例外与许可说明见 docs/ai-maintenance-boundary.md。
import contextlib
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from fagao.__main__ import main
from fagao.runtime import WorkbenchRuntime
from fagao.store import AppError, Store


from membership_fixtures import SyntheticMembership


class MembershipCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='fagao-membership-cli-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.membership = SyntheticMembership(self.root)
        self.runtime = WorkbenchRuntime(self.root, self.membership)

    def invoke(self, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(['--data-dir', str(self.root), *arguments],
                        runtime_factory=lambda directory: self.runtime)
        return code, json.loads(output.getvalue())

    def test_official_process_denies_without_session_before_opening_database(self):
        result = subprocess.run([sys.executable, '-m', 'fagao', '--data-dir', str(self.root), 'state'],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)['error']['code'], 'membership_not_authenticated')
        self.assertEqual(list(self.root.rglob('*.sqlite3')), [])

    def test_expired_cli_cannot_export_or_start_before_store_construction(self):
        self.membership.allowed = False
        self.membership.reason = 'expired'
        for arguments in [('translation-context',), ('collect', 'fanqie'), ('resume', 'old-task')]:
            with self.subTest(arguments=arguments):
                code, result = self.invoke(*arguments)
                self.assertEqual(code, 1)
                self.assertEqual(result['error']['code'], 'membership_expired')
        self.assertEqual(list(self.root.rglob('*.sqlite3')), [])

    def test_account_switch_does_not_expose_previous_accounts_snapshot(self):
        context = self.runtime.capture()
        saved = context.store.save_snapshot({'platform': 'fanqie', 'channel': '女频', 'category': '古言脑洞',
                'board': '新书榜', 'books': [{'id': 'private-book', 'title': '账号甲私有样本'}]}, imported=True)
        code, first = self.invoke('state')
        self.assertEqual(code, 0)
        self.assertIn(saved['id'], json.dumps(first))
        self.membership.account = 'member-b'
        self.membership.generation = 'session-b'
        code, second = self.invoke('state')
        self.assertEqual(code, 0)
        self.assertNotIn(saved['id'], json.dumps(second))
        self.assertNotIn('账号甲私有样本', json.dumps(second, ensure_ascii=False))
        self.assertTrue(context.store.db.is_file())
        self.assertFalse((self.root / 'workbench.sqlite3').exists())

    def test_logout_during_operation_prevents_late_private_output(self):
        def operation(*args):
            self.membership.logout()
            return {'secret_content': 'previous-account-data'}
        with patch('fagao.__main__.run_command', side_effect=operation):
            code, result = self.invoke('state')
        self.assertEqual(code, 1)
        self.assertNotIn('previous-account-data', json.dumps(result))

    def test_expired_member_can_inspect_membership_and_fix_network(self):
        self.membership.allowed = False
        self.membership.reason = 'expired'
        code, status = self.invoke('membership', 'status')
        self.assertEqual(code, 0)
        self.assertEqual(status['reason'], 'expired')
        code, status = self.invoke('network-config', '--mode', 'direct')
        self.assertEqual(code, 0)
        self.assertEqual(status['mode'], 'direct')
        self.assertEqual(list(self.root.rglob('*.sqlite3')), [])

    def test_expired_member_reads_saved_history_but_cannot_fetch_new_content(self):
        context=self.runtime.capture()
        snapshot=context.store.save_snapshot({'platform':'fanqie','channel':'女频','category':'古言脑洞',
            'board':'新书榜','books':[{'id':'saved-book','title':'旧资料'}]},imported=True)
        text=context.store.import_text(snapshot['id'],'saved-book','已有的正文资料。'*10,'已保存片段')
        self.membership.allowed=False;self.membership.reason='expired'
        for arguments in [('state',),('text',text['id']),('search','旧资料')]:
            code,result=self.invoke(*arguments)
            self.assertEqual(code,0,result)
        def unexpected_source_request(*args):
            from fagao import network
            network.check_authorization()
            self.fail('History access cannot authorize a network fetch')
        with patch('fagao.__main__.run_command',side_effect=unexpected_source_request):
            code,result=self.invoke('state')
        self.assertEqual(code,1)
        self.assertEqual(result['error']['code'],'membership_expired')

    def test_collect_and_batch_preserve_current_page_when_membership_expires(self):
        for command, target in [('collect','fagao.collectors.collect'),
                                ('collect-all','fagao.batch.collect')]:
            with self.subTest(command=command):
                self.membership.allowed = True
                self.membership.reason = 'active'
                context = self.runtime.capture()
                before = len(context.store.state()['snapshots'])
                def collector(platform, options, directory, progress):
                    progress('已取得第一页', snapshot={'platform':'fanqie','channel':'女频',
                        'category':'古言脑洞','board':'新书榜',
                        'books':[{'id':command+'-partial','title':'已完成部分'}]})
                    self.membership.allowed = False
                    self.membership.reason = 'expired'
                    from fagao import network
                    network.check_authorization()
                    self.fail('Expiry must prevent the next source request')
                with patch(target, side_effect=collector), contextlib.redirect_stderr(io.StringIO()):
                    code, result = self.invoke(command,'fanqie')
                self.assertEqual(code,1)
                self.assertEqual(result['error']['code'],'membership_expired')
                self.assertEqual(len(context.store.state()['snapshots']),before+1)

    def legacy_source(self):
        source = Store(self.root)
        snapshot = source.save_snapshot({'platform':'fanqie','channel':'女频','category':'古言脑洞',
            'board':'新书榜','books':[{'id':'legacy-book','title':'旧版匿名测试资料'}]}, imported=True)
        return source, snapshot

    def test_legacy_inspect_only_counts_and_claim_needs_explicit_confirmation(self):
        source, snapshot = self.legacy_source()
        code, status = self.invoke('legacy','inspect')
        self.assertEqual(code,0,status)
        self.assertTrue(status['decision_required'])
        self.assertEqual(status['counts']['snapshots'],1)
        self.assertNotIn('旧版匿名测试资料',json.dumps(status,ensure_ascii=False))
        self.assertFalse((self.root/'accounts').exists())
        code, denied = self.invoke('legacy','claim')
        self.assertEqual(code,1)
        self.assertEqual(denied['error']['code'],'legacy_confirmation_required')
        code, claimed = self.invoke('legacy','claim','--confirm')
        self.assertEqual(code,0,claimed)
        self.assertEqual(claimed['status'],'completed')
        self.assertTrue(source.db.is_file())
        code, state = self.invoke('state')
        self.assertEqual(code,0,state)
        self.assertIn(snapshot['id'],json.dumps(state))

    def test_legacy_skip_needs_confirmation_and_unblocks_new_work(self):
        self.legacy_source()
        with patch('fagao.__main__.run_command') as operation:
            code, blocked = self.invoke('collect','fanqie')
            self.assertEqual(code,1)
            self.assertEqual(blocked['error']['code'],'legacy_decision_required')
            operation.assert_not_called()
        code, denied = self.invoke('legacy','skip')
        self.assertEqual(code,1)
        self.assertEqual(denied['error']['code'],'legacy_confirmation_required')
        code, skipped = self.invoke('legacy','skip','--confirm')
        self.assertEqual(code,0,skipped)
        self.assertEqual(skipped['decision'],'skipped')
        with patch('fagao.__main__.run_command',return_value={'started':True}) as operation:
            code, result = self.invoke('collect','fanqie')
            self.assertEqual(code,0,result)
            operation.assert_called_once()

    def test_expired_member_cannot_read_legacy_counts_or_make_ownership_decision(self):
        self.legacy_source()
        self.membership.allowed=False;self.membership.reason='expired'
        for arguments in [('legacy','inspect'),('legacy','claim','--confirm'),('legacy','skip','--confirm')]:
            with self.subTest(arguments=arguments):
                code,result=self.invoke(*arguments)
                self.assertEqual(code,1)
                self.assertEqual(result['error']['code'],'membership_expired')
        self.assertFalse((self.root/'accounts').exists())


if __name__ == '__main__':
    unittest.main()
