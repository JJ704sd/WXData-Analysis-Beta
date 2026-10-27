"""Synthetic private-chat UI/CLI integration; never reads real account state."""
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from offline_chat import ArchiveError
from offline_desktop import DesktopSession
from offline_direct import system_account_reason
from offline_review import REVIEW_CHECKS
from test_database_adapter import create_metadata, create_shard, SELF, PEER, GROUP
from test_wecom_archive import fixture


def direct_source(base, client):
    source = base / ('source-' + client)
    if client == 'wechat':
        source.mkdir()
        create_metadata(source)
        create_shard(source, group=PEER, rows=[
            {'local': 1, 'sender': 2, 'text': '<msg><emoji/></msg>', 'type': 47},
            {'local': 2, 'sender': 2, 'text': '希望先看书面方案'},
            {'local': 3, 'sender': 1, 'text': '我会在周五发方案'}])
        return source, PEER, SELF
    fixture(source)
    with contextlib.closing(sqlite3.connect(source / 'user.db')) as db, db:
        db.execute('insert into user_table values(22,?,?)', ('合成客户', '合成客户'))
    with contextlib.closing(sqlite3.connect(source / 'session.db')) as db, db:
        db.execute('insert into conversation_table values(?,?,?,?)', ('D:synthetic', '合成客户', '', 1700000000))
        db.executemany('insert into conversation_user_table values(?,?,?)',
                       [('D:synthetic', 11, '合成本人'), ('D:synthetic', 22, '合成客户')])
    with contextlib.closing(sqlite3.connect(source / 'message.db')) as db, db:
        db.execute('update message_table set conversation_id=?,sender_id=?', ('D:synthetic', 22))
    return source, 'D:synthetic', '11'


class PrivateIntegrationTests(unittest.TestCase):
    def tearDown(self):
        # Dispose Tcl cycles on the owning thread before later worker tests.
        import gc
        gc.collect()

    def test_both_clients_parse_review_export_and_restore_mode_isolation(self):
        for client in ('wechat', 'wecom'):
            with self.subTest(client=client), tempfile.TemporaryDirectory() as folder:
                base = Path(folder)
                source, chat, own = direct_source(base, client)
                session = DesktopSession(base / 'profile', client=client, conversation_mode='direct')
                ready = session.open_source({'sourceRoot': str(source), 'selfId': own, 'includeMedia': False})
                self.assertIn(chat, [r['id'] for r in ready['groups']])
                self.assertTrue(all(r['conversationKind'] == 'direct' for r in ready['groups']))
                with self.assertRaises(ArchiveError):
                    session.select_chat(chat_id=GROUP if client == 'wechat' else 'R:synthetic')
                parsed = session.prepare_review(chat, copy_media=False)
                self.assertEqual(parsed['review']['peerId'], PEER if client == 'wechat' else '22')
                restored = DesktopSession(base / 'profile', client=client, conversation_mode='direct')
                self.assertGreater(restored.bootstrap(resume=True).get('restoredReviews', 0), 0)
                group = DesktopSession(base / 'profile', client=client)
                self.assertNotEqual(group._progress.path, restored._progress.path)
                self.assertFalse(group._progress.path.exists())
                with self.assertRaises(ArchiveError):
                    session.confirm_export(parsed['review']['reviewId'], {}, chat=chat)
                result = session.confirm_export(parsed['review']['reviewId'],
                    {key: True for key in REVIEW_CHECKS}, 'Synthetic integration test only', chat=chat)
                report = json.loads((Path(result['output']) / 'manifest.json').read_text(encoding='utf8'))
                self.assertEqual(report['humanReview']['status'], 'confirmed')
                self.assertIn('私聊', (Path(result['output']) / 'analysis/README.md').read_text(encoding='utf8'))
                self.assertEqual(session.settings.get('lastDirectChat'), chat)
                from private_chat import main
                cli_base = ['--store', str(base / 'portraits'), '--bound-client', client, 'insights',
                            '--export-root', str(result['output'])]
                cli_output = io.StringIO()
                with contextlib.redirect_stdout(cli_output):
                    self.assertEqual(main(cli_base + ['--output-dir', str(base / 'statistics')]), 0)
                statistics = json.loads(cli_output.getvalue())
                self.assertEqual(statistics['summary']['identity']['client'], client)
                self.assertGreater(statistics['summary']['totalMessages'], 0)
                self.assertTrue(Path(statistics['report']['htmlFile']).is_file())
                for invalid_target in (Path(result['output']) / 'report', base / 'portraits' / 'report'):
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(main(cli_base + ['--output-dir', str(invalid_target)]), 2)
                    self.assertFalse(invalid_target.exists())
                wrong_client = 'wecom' if client == 'wechat' else 'wechat'
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(['--store', str(base / 'portraits'), '--bound-client', wrong_client,
                                          'insights', '--export-root', str(result['output'])]), 2)
                from private_profiles import ProfileStore
                store = ProfileStore(base / 'portraits')
                request = store.prepare(result['output'])
                self.assertEqual(request['status'], 'ready')
                repeated = store.prepare(result['output'])
                self.assertEqual(repeated['requestId'], request['requestId'])
                candidate = {'schemaVersion': 1, 'requestId': request['requestId'],
                    'baseRevision': request['baseRevision'], 'coveredChunks': request['requiredChunks'],
                    'observations': [{'subject': 'peer', 'category': 'needs',
                        'text': '合成观察，仅用于链路验证', 'basis': 'uncertain',
                        'evidence': [{'sourceId': request['sources'][0]['sourceId'], 'ref': 1}]}],
                    'limitations': ['Synthetic test, not a real profile.']}
                staged = store.stage(request['requestId'], candidate)
                from private_chat import show_candidate
                with patch('private_chat._review_window') as window:
                    with self.assertRaises(ValueError):
                        show_candidate(store, store.candidate(staged['candidateId']), bound_client=wrong_client)
                    window.assert_not_called()
                self.assertIsNone(store.current(request['client'], request['accountKey'], request['peerId']))
                confirmed = store.confirm(staged['candidateId'], confirmed_by='synthetic_test')
                self.assertEqual(confirmed['status'], 'confirmed')
                self.assertEqual(store.prepare(result['output'])['status'], 'unchanged')

    def test_service_words_in_a_person_name_are_not_system_evidence(self):
        self.assertIsNone(system_account_reason({'id': PEER, 'name': '客服同事（合成）'}))
        self.assertIsNotNone(system_account_reason({'id': 'filehelper', 'name': '助手'}))

    def test_private_name_selection_does_not_trim_group_suffixes(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source, chat, own = direct_source(base, 'wechat')
            session = DesktopSession(base / 'profile', conversation_mode='direct')
            session.open_source({'sourceRoot': str(source), 'selfId': own, 'includeMedia': False})
            name = session.select_chat(chat_id=chat)['name']
            with self.assertRaises(ArchiveError):
                session.select_chat(chat_name=name + '群')
            self.assertEqual(session.select_chat(chat_name=name)['id'], chat)
            from offline_batch import execute
            with self.assertRaises(ArchiveError):
                execute({'version': 1, 'action': 'export', 'conversationMode': 'direct',
                         'chatName': name + '群'}, base / 'profile')

    def test_private_package_defaults_to_direct_and_rejects_group_requests(self):
        from offline_direct import resolve_packaged_mode
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'runtime.json').write_text(json.dumps(
                {'privateAnalysis': True, 'conversationMode': 'direct'}), encoding='utf8')
            with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(root / 'bin/tool.exe')):
                self.assertEqual(resolve_packaged_mode(), 'direct')
                with self.assertRaises(ArchiveError):
                    resolve_packaged_mode('group')

    def test_gui_labels_preserve_contact_name_and_do_not_load_data(self):
        import tkinter as tk
        from local_chat_app import ChatApp
        with tempfile.TemporaryDirectory() as folder:
            root = tk.Tk()
            try:
                session = DesktopSession(folder, conversation_mode='direct')
                app = ChatApp(root, session)
                for token in root.tk.call('after', 'info'):
                    root.after_cancel(token)
                root.update()
                self.assertIn('私聊', root.title())
                self.assertNotIn('群', app.group_title.get())
                app.groups = [{'id': PEER, 'name': '微信群业务联系人（合成）', 'conversationKind': 'direct'}]
                app.render_groups()
                self.assertIn('微信群业务联系人', str(app.tree.item(PEER)['values']))
                self.assertFalse((Path(folder) / 'settings.local.json').exists())
            finally:
                root.destroy()

    def test_profile_cli_capabilities_has_no_store_side_effect_and_no_confirm_switch(self):
        from private_chat import main, parser
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'unused-store'
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(['--store', str(target), '--capabilities']), 0)
            self.assertTrue(json.loads(output.getvalue())['privateAnalysis'])
            self.assertFalse(target.exists())
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser().parse_args(['confirm', '--candidate-id', 'synthetic'])

    def test_profile_cli_invalid_json_shape_returns_structured_failure(self):
        from private_chat import main
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'export'
            source.mkdir()
            (source / 'review.json').write_text('[]', encoding='utf8')
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(['--store', str(root / 'store'), 'prepare', '--export-root', str(source)])
            self.assertEqual(code, 2)
            self.assertFalse(json.loads(output.getvalue())['ok'])

    def test_profile_review_starts_unchecked_and_cancel_does_not_save(self):
        import tkinter as tk
        from tkinter import ttk
        from private_chat import _review_window
        window = tk.Tk()
        saved = []

        def inspect_then_cancel():
            pending, buttons, checks = [window], [], []
            while pending:
                widget = pending.pop()
                pending.extend(widget.winfo_children())
                if isinstance(widget, ttk.Button):
                    buttons.append(widget)
                if isinstance(widget, ttk.Checkbutton):
                    checks.append(widget)
            confirm = next(button for button in buttons if button.cget('text') == '确认并保存')
            self.assertTrue(confirm.instate(['disabled']))
            self.assertEqual(len(checks), 3)
            self.assertTrue(all(not widget.instate(['selected']) for widget in checks))
            window.destroy()

        with patch('tkinter.Tk', return_value=window), patch.object(window, 'mainloop', side_effect=inspect_then_cancel):
            result = _review_window('合成候选复核测试', 'Synthetic fixture only', lambda note: saved.append(note))
        self.assertEqual(result['status'], 'awaiting_confirmation')
        self.assertEqual(saved, [])


if __name__ == '__main__':
    unittest.main()
