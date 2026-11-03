"""Synthetic tests for supplier attribution and reviewed-export boundaries."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from offline_chat import ArchiveError
from offline_supplier import build_supplier_report, load_materials, speaker_choices
from test_daily_reports import formal_export, message
from chat_release import GROUP_RELEASE


def direct_export(base, name, rows, *, peer='synthetic-peer', **options):
    root = formal_export(base, name, rows, **options)
    review_path, manifest_path = root / 'review.json', root / 'manifest.json'
    review = json.loads(review_path.read_text(encoding='utf-8'))
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if peer is not None:
        review['peerId'] = manifest['peerId'] = peer
    review_path.write_text(json.dumps(review, ensure_ascii=False), encoding='utf-8')
    artifact = next(item for item in manifest['artifacts'] if item['path'] == 'review.json')
    artifact.update(bytes=review_path.stat().st_size, sha256=hashlib.sha256(review_path.read_bytes()).hexdigest())
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    return root


class SupplierProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='synthetic-supplier-profile-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)

    def generate(self, roots, ids, *, name='output', **options):
        result = build_supplier_report(roots, client=options.pop('client', 'wechat'), chat_ids=ids,
            start='2026-10-27', end='2026-10-27', output=self.base / name,
            supplier_label='合成供应商', **options)
        folder = Path(result['output'])
        report = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
        rows = [json.loads(line) for line in (folder / 'evidence.jsonl').read_text(encoding='utf-8').splitlines()]
        return folder, report, rows

    def test_desktop_check_rejects_a_profile_outside_its_isolated_task_folder(self):
        from local_chat_app import main
        task_dir = self.base / 'task'
        task_dir.mkdir()
        task = task_dir / 'synthetic.json'
        task.write_text(json.dumps({'client': 'wechat', 'mode': 'group',
                                    'reports': str(task_dir / 'reports')}), encoding='utf-8')
        receipt = task_dir / 'receipt.json'
        with self.assertRaisesRegex(ValueError, 'isolated task folder'):
            main(['--client', 'wechat', '--conversation-mode', 'group',
                  '--data-dir', str(self.base / 'outside-profile'),
                  '--check-supplier-desktop', str(task), '--result-json', str(receipt)])
        self.assertFalse(receipt.exists())

    def test_mixed_conversations_only_attribute_explicit_peer_and_selected_group_speaker(self):
        direct = direct_export(self.base, 'direct', [
            message('d1', '报价是 30 元 <script>alert(1)</script>'),
            message('d2', '我来确认', own=True),
            message('d3', '第三方报价', senderId='third-party'),
            message('d4', '时间未知的订单', time=None)])
        group = formal_export(self.base, 'group', [
            message('g1', '产品报价待确认'),
            message('g2', '其他人的物流问题', senderId='other-supplier'),
            message('g3', '我方要求交期', own=True),
            message('g4', '收到', kind='quote', quote={'message': {'text': '第三方售后投诉',
                                                                    'senderId': 'third-party'}}),
            message('g5', '附件', kind='file', attachmentContent={'text': '第三方工厂',
                    'extractionStatus': 'partial', 'requiresReview': True})], mode='group')
        folder, report, rows = self.generate([direct, group], ['direct-conversation', 'group-conversation'],
                                              group_speakers={'group-conversation': ['synthetic-peer']})
        by_id = {row['messageId']: row for row in rows}
        self.assertEqual(report['overview']['datedMessages'], 8)
        self.assertEqual(report['overview']['attributedMessages'], 4)
        self.assertEqual(report['overview']['undatedMessages'], 1)
        self.assertEqual(report['version'], GROUP_RELEASE)
        self.assertEqual(by_id['d1']['supplierRelation'], 'selected_direct_peer')
        self.assertEqual(by_id['d2']['supplierRelation'], 'direct_context')
        self.assertEqual(by_id['d3']['supplierRelation'], 'identity_unknown')
        self.assertEqual(by_id['g1']['supplierRelation'], 'selected_group_speaker')
        self.assertEqual(by_id['g2']['supplierRelation'], 'group_context')
        self.assertEqual(by_id['g3']['supplierRelation'], 'group_context')
        dimensions = {item['label']: item['evidenceIds'] for item in report['dimensions']}
        self.assertEqual(set(dimensions['报价与费用']), {by_id['d1']['evidenceId'], by_id['g1']['evidenceId']})
        self.assertEqual(dimensions['售后与异常'], [])  # third-party quote is context, not a root statement
        self.assertEqual(dimensions['能力与资源'], [])  # attachment author is unknown
        self.assertTrue(report['overview']['messagesWithGaps'])
        page = (folder / 'report.html').read_text(encoding='utf-8')
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', page)
        self.assertNotIn('<script>alert(1)</script>', page)
        self.assertIn('id="' + by_id['g1']['evidenceId'] + '"', page)
        self.assertIn('第三方售后投诉', page)
        self.assertTrue(all(row['sources'][0]['reviewId'] and row['sources'][0]['line'] for row in rows))
        for item in json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))['files']:
            self.assertEqual(item['sha256'], hashlib.sha256((folder / item['path']).read_bytes()).hexdigest())

    def test_no_implicit_group_or_unverified_private_attribution(self):
        direct = direct_export(self.base, 'unknown', [message('d', '报价')], peer=None)
        group = formal_export(self.base, 'unselected', [message('g', '报价')], mode='group')
        _, report, rows = self.generate([direct, group], ['direct-conversation', 'group-conversation'])
        self.assertEqual(report['overview']['attributedMessages'], 0)
        self.assertEqual({row['supplierRelation'] for row in rows}, {'group_context', 'identity_unknown'})
        self.assertTrue(any('身份未完整确认' in note for note in report['notices']))

    def test_speaker_mapping_is_per_conversation_and_cannot_select_self(self):
        one = formal_export(self.base, 'one', [message('a', '报价'), message('self', '报价', own=True)],
                            mode='group', chat_id='group-1')
        two = formal_export(self.base, 'two', [message('b', '报价')], mode='group', chat_id='group-2')
        material = load_materials([one, two], client='wechat', chat_ids=['group-1', 'group-2'],
                                  start='2026-10-27', end='2026-10-27')
        self.assertEqual(len(speaker_choices(material)), 2)
        _, report, rows = self.generate([one, two], ['group-1', 'group-2'],
                                        group_speakers={'group-1': ['synthetic-peer']})
        self.assertEqual(report['overview']['attributedMessages'], 1)
        self.assertEqual(next(row for row in rows if row['messageId'] == 'b')['supplierRelation'], 'group_context')
        for index, mapping in enumerate(({'group-1': ['synthetic-owner']},
                                         {'group-2': ['other']}, {'absent': ['synthetic-peer']}, [])):
            with self.subTest(mapping=mapping), self.assertRaises(ArchiveError):
                self.generate([one, two], ['group-1', 'group-2'], name='bad-' + str(index), group_speakers=mapping)

    def test_review_account_source_integrity_and_no_overwrite(self):
        first = direct_export(self.base, 'first', [message('a', '报价')])
        second = direct_export(self.base, 'second', [message('b', '报价')], own='different-owner', chat_id='other')
        with self.assertRaises(ArchiveError):
            self.generate([first, second], ['direct-conversation', 'other'], name='mixed-account')
        with self.assertRaises(ArchiveError):
            self.generate([first], ['direct-conversation'], name='wrong-client', client='wecom')
        with self.assertRaises(ArchiveError):
            build_supplier_report([first], client='wechat', chat_ids=['direct-conversation'], start='2026-10-27',
                                  end='2026-10-27', output=first / 'report', supplier_label='合成')
        folder, _, _ = self.generate([first], ['direct-conversation'])
        before = (folder / 'report.json').read_bytes()
        with self.assertRaises(ArchiveError):
            self.generate([first], ['direct-conversation'])
        self.assertEqual(before, (folder / 'report.json').read_bytes())
        (first / 'messages.jsonl').write_text('{}\n', encoding='utf-8')
        with self.assertRaises(ArchiveError):
            self.generate([first], ['direct-conversation'], name='tampered')

    def test_duplicate_exports_keep_both_review_references(self):
        one = direct_export(self.base, 'copy-1', [message('same', '物流需求')])
        two = direct_export(self.base, 'copy-2', [message('same', '物流需求')])
        _, report, rows = self.generate([one, two], ['direct-conversation'])
        self.assertEqual(report['overview']['duplicateOccurrences'], 1)
        self.assertEqual(report['overview']['attributedMessages'], 1)
        self.assertEqual(len(rows[0]['sources']), 2)

    def test_command_supports_mixed_modes_for_both_clients(self):
        script = Path(__file__).resolve().parents[1] / 'scripts' / 'supplier_profile.py'
        for client in ('wechat', 'wecom'):
            with self.subTest(client=client):
                direct = direct_export(self.base, client + '-d', [message('d', '产品报价')], client=client)
                group = formal_export(self.base, client + '-g', [message('g', '发货')], client=client, mode='group')
                task = {'client': client, 'exportRoots': [str(direct), str(group)],
                        'chatIds': ['direct-conversation', 'group-conversation'], 'start': '2026-10-27',
                        'end': '2026-10-27', 'supplierLabel': '合成供应商',
                        'groupSpeakers': {'group-conversation': ['synthetic-peer']},
                        'output': str(self.base / (client + '-cli'))}
                path = self.base / (client + '-task.json')
                path.write_text(json.dumps(task, ensure_ascii=False), encoding='utf-8')
                run = subprocess.run([sys.executable, str(script), '--task', str(path)],
                                     capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
                self.assertEqual(json.loads(run.stdout)['overview']['attributedMessages'], 2)

    def test_desktop_task_supports_mixed_modes_and_protects_sources(self):
        from local_chat_app import main
        direct = direct_export(self.base, 'desktop-direct', [message('d', '报价')])
        group = formal_export(self.base, 'desktop-group', [message('g', '发货')], mode='group')
        task = {'client': 'wechat', 'exportRoots': [str(direct), str(group)],
                'chatIds': ['direct-conversation', 'group-conversation'], 'start': '2026-10-27',
                'end': '2026-10-27', 'supplierLabel': '合成供应商',
                'groupSpeakers': {'group-conversation': ['synthetic-peer']},
                'output': str(self.base / 'desktop-output')}
        path = self.base / 'desktop-task.json'
        path.write_text(json.dumps(task, ensure_ascii=False), encoding='utf-8')
        receipt = self.base / 'receipt.json'
        args = ['--client', 'wechat', '--conversation-mode', 'group',
                '--supplier-analysis-json', str(path), '--result-json']
        self.assertEqual(main(args + [str(receipt)]), 0)
        self.assertEqual(json.loads(receipt.read_text(encoding='utf-8'))['overview']['attributedMessages'], 2)
        report = json.loads((self.base / 'desktop-output/report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['version'], GROUP_RELEASE)
        self.assertEqual(json.loads((self.base / 'desktop-output/manifest.json').read_text(encoding='utf-8'))['version'], GROUP_RELEASE)
        with self.assertRaises(ArchiveError):
            main(args + [str(direct / 'should-not-exist.json')])
        self.assertFalse((direct / 'should-not-exist.json').exists())
        with self.assertRaises(ArchiveError):
            main(args + [str(self.base / 'desktop-output/should-not-exist.json')])
        self.assertFalse((self.base / 'desktop-output/should-not-exist.json').exists())
        mismatch = self.base / 'mismatch.json'
        task['client'] = 'wecom'
        task['output'] = str(self.base / 'wrong-client-output')
        path.write_text(json.dumps(task, ensure_ascii=False), encoding='utf-8')
        self.assertEqual(main(args + [str(mismatch)]), 2)
        self.assertFalse(json.loads(mismatch.read_text(encoding='utf-8'))['ok'])
        self.assertFalse((self.base / 'wrong-client-output').exists())

    def test_four_actual_parse_and_review_paths(self):
        from offline_desktop import DesktopSession
        from offline_review import REVIEW_CHECKS
        from test_four_exports53 import CASES, source_fixture
        for client, mode in CASES:
            with self.subTest(client=client, mode=mode):
                base = self.base / (client + '-' + mode)
                base.mkdir()
                config, chat, _ = source_fixture(base, client, mode, count=0)
                session = DesktopSession(base / 'profile', client=client, conversation_mode=mode)
                session.open_source(config)
                parsed = session.prepare_review(chat, copy_media=False, load_view=False)
                exported = session.confirm_export(parsed['review']['reviewId'],
                    {key: True for key in REVIEW_CHECKS}, chat=chat)
                result = build_supplier_report([exported['output']], client=client, chat_ids=[chat],
                    start='2023-11-15', end='2023-11-15', output=base / 'supplier', supplier_label='合成供应商')
                self.assertGreater(result['overview']['datedMessages'], 0)
                if mode == 'direct':
                    self.assertGreater(result['overview']['attributedMessages'], 0)


class SupplierDialogTests(unittest.TestCase):
    def test_supplier_period_button_names_the_actual_next_step(self):
        import tkinter as tk
        from local_chat_app import ChatApp
        from offline_desktop import DesktopSession
        from offline_period_ui import PeriodDialog
        with tempfile.TemporaryDirectory(prefix='synthetic-supplier-label-') as temporary:
            root = tk.Tk()
            root.withdraw()
            try:
                session = DesktopSession(Path(temporary), client='wechat', conversation_mode='group')
                session.review_status = lambda _chat: '已导出'
                app = ChatApp(root, session)
                app.groups = [{'id': 'synthetic-group', 'name': '合成群'}]
                app.selected_chats = {'synthetic-group'}
                observed = []

                def inspect():
                    dialog = next(window for window in root.winfo_children() if isinstance(window, PeriodDialog))
                    def collect(widget):
                        if widget.winfo_class() == 'TButton':
                            observed.append(widget.cget('text'))
                        for child in widget.winfo_children():
                            collect(child)
                    collect(dialog)
                    dialog.destroy()
                root.after(30, inspect)
                app.open_supplier_analysis()
                self.assertIn('下一步：填写供应商标识', observed)
            finally:
                if 'app' in locals():
                    app.progress.stop()
                for token in root.tk.splitlist(root.tk.eval('after info')):
                    root.tk.call('after', 'cancel', token)
                root.destroy()

    def test_all_four_main_windows_offer_supplier_action(self):
        import tkinter as tk
        from local_chat_app import ChatApp
        from offline_desktop import DesktopSession
        with tempfile.TemporaryDirectory(prefix='synthetic-supplier-windows-') as temporary:
            for client in ('wechat', 'wecom'):
                for mode in ('group', 'direct'):
                    with self.subTest(client=client, mode=mode):
                        root = tk.Tk()
                        try:
                            app = ChatApp(root, DesktopSession(Path(temporary) / (client + mode),
                                          client=client, conversation_mode=mode))
                            root.update()
                            menu = root.nametowidget(app.scan_button['menu'])
                            labels = [menu.entrycget(i, 'label') for i in range(menu.index('end') + 1)
                                      if menu.type(i) != 'separator']
                            self.assertIn('供应商画像材料…', labels)
                            for token in root.tk.call('after', 'info'):
                                root.after_cancel(token)
                        finally:
                            root.destroy()

    def test_group_member_choice_preserves_conversation_boundary(self):
        import tkinter as tk
        from offline_supplier_ui import SupplierSpeakerDialog
        root = tk.Tk()
        self.addCleanup(root.destroy)
        choices = [{'chatId': chat, 'chatName': '合成群', 'senderId': 'same-sender',
                    'names': ['合成人员'], 'messages': 1} for chat in ('group-a', 'group-b')]
        dialog = SupplierSpeakerDialog(root, choices)
        dialog.geometry('560x380')
        root.update()
        dialog.tree.selection_set('1')
        dialog.submit()
        self.assertEqual(dialog.result, {'group-b': ['same-sender']})

    def test_desktop_action_generates_from_each_of_four_modes(self):
        from local_chat_app import ChatApp
        with tempfile.TemporaryDirectory(prefix='synthetic-supplier-ui-') as temporary:
            base = Path(temporary)
            for client in ('wechat', 'wecom'):
                for mode in ('group', 'direct'):
                    with self.subTest(client=client, mode=mode):
                        name = client + '-' + mode
                        if mode == 'group':
                            source = formal_export(base, name, [message('g', '产品报价')], client=client, mode=mode)
                        else:
                            source = direct_export(base, name, [message('d', '产品报价')], client=client)
                        chat = mode + '-conversation'
                        result = {'chats': [chat], 'start': '2026-10-27', 'end': '2026-10-27'}
                        root = SimpleNamespace(wait_window=lambda _window: None)
                        session = SimpleNamespace(client=client, conversation_mode=mode, _exports={chat: source},
                                                  review_status=lambda _chat: '已导出')
                        status = SimpleNamespace(set=lambda _value: None)
                        app = SimpleNamespace(root=root, session=session, groups=[{'id': chat, 'name': '合成'}],
                            selected_chats={chat}, active_chat=chat, start_date=SimpleNamespace(get=lambda: '2026-10-27'),
                            end_date=SimpleNamespace(get=lambda: '2026-10-27'), status=status,
                            busy=False, read_busy=False, batch_running=False, closing=False)
                        app.work = lambda _label, func, callback: callback(func())
                        picker = SimpleNamespace(result={'group-conversation': ['synthetic-peer']})
                        with patch('offline_period_ui.PeriodDialog', return_value=SimpleNamespace(result=result)), \
                             patch('offline_supplier_ui.SupplierSpeakerDialog', return_value=picker), \
                             patch('local_chat_app.simpledialog.askstring', return_value='合成供应商'), \
                             patch('local_chat_app.filedialog.askdirectory', return_value=str(base)), \
                             patch('local_chat_app.os.startfile') as launch:
                            ChatApp.open_supplier_analysis(app)
                        launch.assert_called_once()
                        report = Path(launch.call_args.args[0])
                        self.assertTrue(report.is_file())
                        payload = json.loads(report.with_name('report.json').read_text(encoding='utf-8'))
                        self.assertEqual(payload['overview']['attributedMessages'], 1)
