"""Focused tests for exact conversations, bounded time and source traceability."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from offline_chat import ArchiveError, BEIJING
from offline_period import build_period_report, period_range
from offline_review import REVIEW_CHECKS
from test_daily_reports import formal_export, message


class PeriodTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='synthetic-period77-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def generate(self, roots, *, chat_ids=('direct-conversation',), name='report', **options):
        value = build_period_report(roots, client='wechat', chat_ids=list(chat_ids), start='2026-10-27',
                                    end='2026-10-27', output=self.base / name, version='test', **options)
        root = Path(value['output'])
        report = json.loads((root / 'report.json').read_text(encoding='utf-8'))
        rows = [json.loads(line) for line in (root / 'evidence.jsonl').read_text(encoding='utf-8').splitlines()]
        return root, report, rows

    def test_precision_timezone_and_invalid_intervals(self):
        low, high = period_range('2026-10-27', '2026-10-27')
        self.assertEqual((high - low).total_seconds(), 86400)
        self.assertEqual(low.tzinfo, BEIJING)
        self.assertEqual(period_range('2026-10-27 09:00', '2026-10-27 09:00')[1].minute, 1)
        self.assertEqual(period_range('2026-10-27 09:00:00', '2026-10-27 09:00:00')[1].second, 1)
        for start, end in [('2026-02-30', '2026-03-01'), ('2026-10-28', '2026-10-27'),
                           ('2026-10-27T09:00Z', '2026-10-27'), ('20261027', '2026-10-28'),
                           ('9999-12-31', '9999-12-31')]:
            with self.subTest(start=start, end=end), self.assertRaises(ArchiveError):
                period_range(start, end)

    def test_exact_targets_boundaries_utc_and_unknown_time(self):
        chosen = formal_export(self.base, 'chosen', [
            message('before', '前一天', time='2026-10-26T23:59:59+08:00'),
            message('start', '起点', time='2026-10-26T16:00:00Z'),
            message('last', '终点', time='2026-10-27T23:59:59+08:00'),
            message('after', '后一天', time='2026-10-28T00:00:00+08:00'),
            message('undated', '时间待确认', time=None)])
        other = formal_export(self.base, 'other', [message('other', '不可混入')], chat_id='other')
        _, report, rows = self.generate([chosen, other])
        self.assertEqual(report['overview']['messages'], 2)
        self.assertEqual(report['overview']['undatedMessages'], 1)
        self.assertEqual({row['messageId'] for row in rows}, {'start', 'last', 'undated'})
        self.assertEqual({row['chatId'] for row in rows}, {'direct-conversation'})
        self.assertEqual(len(report['sources']), 1)
        self.assertEqual(report['overview']['byDay'], {'2026-10-27': 2})

    def test_minute_and_second_endpoints_select_only_the_requested_period(self):
        source = formal_export(self.base, 'precision', [
            message('before', '前', time='2026-10-27T08:59:59+08:00'),
            message('first', '起', time='2026-10-27T09:00:00+08:00'),
            message('second', '秒', time='2026-10-27T09:00:01+08:00'),
            message('last', '分', time='2026-10-27T09:00:59+08:00'),
            message('after', '后', time='2026-10-27T09:01:00+08:00')])
        for end, expected in [('2026-10-27 09:00', 3), ('2026-10-27 09:00:00', 1)]:
            with self.subTest(end=end):
                value = build_period_report([source], client='wechat', chat_ids=['direct-conversation'],
                    start='2026-10-27 09:00', end=end, output=self.base / str(expected), version='test')
                self.assertEqual(value['overview']['messages'], expected)

    def test_followup_cannot_use_future_reply_and_nested_authors_are_preserved(self):
        chosen = formal_export(self.base, 'chosen', [message('request', '请确认报价'),
            message('reply-after', '已完成', own=True, time='2026-10-28T00:00:00+08:00'),
            message('file', '需求.pdf', kind='file', attachmentContent={'text': '第三方附件正文 <script>alert(1)</script>',
                    'extractionStatus': 'partial', 'requiresReview': True}),
            message('forward', '转发记录', kind='record', record={'title': '转发的原始标题', 'status': 'partial', 'items': [
                {'kind': 'text', 'text': '请安排会议', 'senderName': '原作者', 'time': '2020-01-01T00:00:00+08:00'}]}),
            message('quote', '收到', kind='quote', quote={'senderName': '引用原作者', 'message': {
                'kind': 'text', 'text': '请安排另一会议'}})])
        root, report, rows = self.generate([chosen])
        self.assertEqual(report['followups'][0]['state'], 'needs_response')
        self.assertEqual(len(report['followups']), 1)
        self.assertEqual([item['label'] for item in report['topics']], ['报价与价格'])
        self.assertTrue(report['contentGaps'])
        self.assertIn('原作者', (root / 'report.md').read_text(encoding='utf-8'))
        html = (root / 'report.html').read_text(encoding='utf-8')
        self.assertIn('转发的原始标题', html)
        self.assertIn('引用原作者', html)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', html)
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('data-time="dated"', html)
        self.assertTrue(any(row['originalMessage'].get('attachmentContent') for row in rows))
        for row in rows:
            self.assertIn('id="' + row['evidenceId'] + '"', html)
            self.assertEqual(row['sources'][0]['reviewId'], 'synthetic-review-chosen')
            self.assertGreater(row['sources'][0]['line'], 0)
        manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
        for item in manifest['files']:
            self.assertEqual(hashlib.sha256((root / item['path']).read_bytes()).hexdigest(), item['sha256'])

    def test_duplicate_exports_zero_messages_and_source_coverage_gap(self):
        row = message('same', '早先消息', time='2026-10-25T09:00:00+08:00')
        one = formal_export(self.base, 'one', [row])
        two = formal_export(self.base, 'two', [row])
        _, report, rows = self.generate([one, two])
        self.assertEqual(rows, [])
        self.assertEqual(report['conversations'][0]['messages'], 0)
        self.assertTrue(any('未找到' in note for note in report['notices']))
        from offline_period import _coverage_notices
        low, high = period_range('2026-10-25', '2026-10-27')
        sources = [{'chat': {'id': 'chat', 'name': '群聊'}, 'startDate': day, 'endDate': day}
                   for day in ('2026-10-25', '2026-10-27')]
        self.assertEqual(len(_coverage_notices(sources, ['chat'], low, high)), 1)
        current = formal_export(self.base, 'current', [message('dup', '同一消息')])
        repeated = formal_export(self.base, 'repeat', [message('dup', '同一消息')])
        _, report, rows = self.generate([current, repeated], name='dedup')
        self.assertEqual(report['overview']['messages'], 1)
        self.assertEqual(report['overview']['duplicateOccurrences'], 1)
        self.assertEqual(len(rows[0]['sources']), 2)

    def test_review_identity_mode_tamper_and_no_overwrite(self):
        root = formal_export(self.base, 'source', [message('ok', '消息')])
        for options in ({'chat_ids': []}, {'chat_ids': ['wrong']}, {'conversation_mode': 'group'}):
            with self.subTest(options=options), self.assertRaises(ArchiveError):
                self.generate([root], **options)
        other = formal_export(self.base, 'account2', [message('two', '消息', senderId='other-peer', direction='incoming')],
                              own='other-owner', chat_id='other-chat')
        with self.assertRaises(ArchiveError):
            self.generate([root, other], chat_ids=['direct-conversation', 'other-chat'])
        output, _, _ = self.generate([root])
        before = (output / 'report.json').read_bytes()
        with self.assertRaises(ArchiveError):
            self.generate([root])
        self.assertEqual((output / 'report.json').read_bytes(), before)
        with self.assertRaises(ArchiveError):
            build_period_report([root], client='wechat', chat_ids=['direct-conversation'], start='2026-10-27',
                                end='2026-10-27', output=root / 'analysis', version='test')
        (root / 'messages.jsonl').write_text('{}\n', encoding='utf-8')
        with self.assertRaises(ArchiveError):
            self.generate([root], name='tampered')

    def test_four_actual_session_export_paths_and_cli_task(self):
        from offline_desktop import DesktopSession
        from test_four_exports53 import CASES, source_fixture
        from local_chat_app import main
        for client, mode in CASES:
            with self.subTest(client=client, mode=mode):
                base = self.base / (client + mode)
                base.mkdir()
                config, chat, _ = source_fixture(base, client, mode, count=0)
                session = DesktopSession(base / 'profile', client=client, conversation_mode=mode)
                session.open_source(config)
                parsed = session.prepare_review(chat, copy_media=False, load_view=False)
                with self.assertRaises(ArchiveError):
                    session.analyze_period([chat], start='2023-11-15', end='2023-11-15', output=base / 'early')
                exported = session.confirm_export(parsed['review']['reviewId'], {key: True for key in REVIEW_CHECKS}, chat=chat)
                value = session.analyze_period([chat], start='2023-11-15', end='2023-11-15', output=base / 'period')
                self.assertGreater(value['overview']['messages'], 0)
                task = base / 'task.json'
                task.write_text(json.dumps({'exportRoots': [exported['output']], 'chatIds': [chat],
                    'start': '2023-11-15', 'end': '2023-11-15', 'output': str(base / 'cli-report')}), encoding='utf-8')
                result = base / 'result.json'
                self.assertEqual(main(['--client', client, '--conversation-mode', mode, '--period-analysis-json', str(task),
                                       '--result-json', str(result)]), 0)
                self.assertTrue(json.loads(result.read_text(encoding='utf-8'))['ok'])


class PeriodDialogTests(unittest.TestCase):
    def test_four_main_windows_keep_the_period_action_visible(self):
        import tkinter as tk
        from local_chat_app import ChatApp
        from offline_desktop import DesktopSession
        with tempfile.TemporaryDirectory(prefix='synthetic-period77-windows-') as temporary:
            for client in ('wechat', 'wecom'):
                for mode in ('group', 'direct'):
                    with self.subTest(client=client, mode=mode):
                        root = tk.Tk()
                        try:
                            app = ChatApp(root, DesktopSession(Path(temporary) / (client + mode),
                                          client=client, conversation_mode=mode))
                            for token in root.tk.call('after', 'info'):
                                root.after_cancel(token)
                            root.geometry('1000x700')
                            root.update()
                            button = app.period_analysis_button
                            self.assertTrue(button.winfo_ismapped())
                            self.assertGreaterEqual(button.winfo_rootx(), root.winfo_rootx())
                            self.assertLessEqual(button.winfo_rootx() + button.winfo_width(),
                                                 root.winfo_rootx() + root.winfo_width())
                            self.assertLessEqual(button.winfo_rooty() + button.winfo_height(),
                                                 root.winfo_rooty() + root.winfo_height())
                        finally:
                            root.destroy()

    def test_small_dialog_scrolls_and_submits_exact_ids_and_times(self):
        import tkinter as tk
        from offline_period_ui import PeriodDialog
        root = tk.Tk()
        self.addCleanup(root.destroy)
        chats = [{'id': 'chat-' + str(i), 'name': '同名联系人' + '长名称' * 25} for i in range(40)]
        dialog = PeriodDialog(root, chats, selected=['chat-25'], start='2026-10-27', end='2026-10-27')
        dialog.geometry('540x420')
        root.update()
        dialog.tree.xview_moveto(1)
        dialog.tree.yview_moveto(1)
        self.assertGreater(dialog.tree.xview()[0], 0)
        self.assertGreater(dialog.tree.yview()[0], 0)
        dialog.clocks[0].set('09:00')
        dialog.clocks[1].set('18:00:00')
        dialog.submit()
        self.assertEqual(dialog.result, {'chats': ['chat-25'], 'start': '2026-10-27 09:00', 'end': '2026-10-27 18:00:00'})
