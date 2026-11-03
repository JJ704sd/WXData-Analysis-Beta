"""Group UI contracts against native Tk and isolated synthetic archives."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from local_chat_app import ChatApp, main
from offline_desktop import DesktopSession
from offline_review import REVIEW_CHECKS
from test_offline_multiselect import multi_group_fixture
from chat_release import GROUP_RELEASE, GROUP_VERSION, PRIVATE_VERSION


class GroupCapabilities42Tests(unittest.TestCase):
    def test_capabilities_respect_requested_and_packaged_mode(self):
        for mode in ('group', 'direct'):
            with self.subTest(mode=mode), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(['--client', 'wecom', '--conversation-mode', mode, '--capabilities']), 0)
                value = json.loads(output.getvalue())
                self.assertEqual(value['conversationMode'], mode)
                self.assertEqual(value['privateAnalysis'], mode == 'direct')
                self.assertEqual(value['version'], PRIVATE_VERSION if mode == 'direct' else GROUP_VERSION)
                if mode == 'group':
                    self.assertEqual(value['release'], GROUP_RELEASE)
        with patch('offline_direct.resolve_packaged_mode', return_value='direct'):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(['--capabilities']), 0)
                self.assertEqual(json.loads(output.getvalue())['conversationMode'], 'direct')


class GroupUI42Tests(unittest.TestCase):
    def test_media_scan_progress_is_visible_without_completing_parse(self):
        app = self.app('wecom')
        app.busy = True
        app.batch_ids = ['synthetic-group']
        app.batch_queue = []
        app.export_events.put({'operation': 'parse', 'stage': 'media_search', 'processed': 12,
                               'files': 60000, 'bytes': 600*1048576, 'chatName': '合成群'})
        app.drain_export_progress()
        self.assertIn('60,000', app.status.get())
        self.assertIn('600 MiB', app.status.get())
        self.assertTrue(app.busy)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-group42-ui-')
        self.base = Path(self.temp.name)
        self.root = tk.Tk()
        self.original_scale = self.root.tk.call('tk', 'scaling')

    def tearDown(self):
        for token in self.root.tk.call('after', 'info'):
            self.root.after_cancel(token)
        self.root.tk.call('tk', 'scaling', self.original_scale)
        self.root.destroy()
        self.temp.cleanup()

    def app(self, client='wechat'):
        app = ChatApp(self.root, DesktopSession(self.base / client, client=client))
        for token in self.root.tk.call('after', 'info'):
            self.root.after_cancel(token)
        return app

    def inside(self, widget):
        self.assertTrue(widget.winfo_ismapped(), str(widget))
        self.assertGreaterEqual(widget.winfo_rootx(), self.root.winfo_rootx())
        self.assertGreaterEqual(widget.winfo_rooty(), self.root.winfo_rooty())
        self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(),
                             self.root.winfo_rootx() + self.root.winfo_width() + 1)
        self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(),
                             self.root.winfo_rooty() + self.root.winfo_height() + 1)

    def test_long_group_title_and_controls_fit_small_and_large_windows(self):
        self.root.tk.call('tk', 'scaling', 1.75)
        app = self.app()
        full = '合成业务讨论群' * 10
        app.chat_title.set(full)
        app.session.last_export = self.base / 'exports' / 'synthetic-group'
        app.batch_output = app.session.last_export.parent
        app.update_buttons()
        for geometry in ('1000x700', '1220x820'):
            self.root.geometry(geometry)
            self.root.update()
            for identity in (False, True):
                if bool(app.group_identity_line.winfo_manager()) != identity:
                    app.toggle_group_identity()
                for dates in (False, True):
                    app.toggle_date_filters(dates)
                    self.root.update()
                    for widget in (app.export_button, app.review_button, app.batch_review_button,
                                   app.group_file_button, app.group_identity_button,
                                   app.message_search_entry, app.date_toggle_button,
                                   app.file_row, app.folder_button, app.repair_metadata_button, app.footer):
                        self.inside(widget)
                    if dates:
                        self.inside(app.apply_date_button)
                        self.inside(app.clear_date_button)
            self.assertEqual(app.chat_title.get(), full)
            self.assertTrue(app.group_chat_display.get().endswith('…'))
        app.toggle_date_filters(False)
        app.toggle_group_identity()
        self.root.geometry('1000x700')
        self.root.update()
        self.assertGreaterEqual(app.browser.winfo_height(), 150)

    def test_empty_state_is_compact_and_does_not_cover_a_parsed_group(self):
        app = self.app('wecom')
        self.root.geometry('1000x700')
        app.update_buttons()
        self.root.update()
        self.assertFalse(app.completion_banner.winfo_ismapped())
        self.assertTrue(app.group_empty_panel.winfo_ismapped())
        self.assertGreaterEqual(app.group_empty_panel.winfo_y(), 0)
        self.assertLessEqual(app.group_empty_panel.winfo_y() + app.group_empty_panel.winfo_height(),
                             app.browser.winfo_height())
        app.active_chat = app.parsed_chat = 'synthetic-group'
        app.fit_group_empty()
        self.root.update()
        self.assertFalse(app.group_empty_panel.winfo_ismapped())

    def test_metadata_failure_offers_repair_and_keeps_saved_directory_available(self):
        self.root.tk.call('tk', 'scaling', 1.75)
        app = self.app('wecom')
        self.root.geometry('1000x700')
        app.update_buttons()
        self.root.update()
        self.assertFalse(app.file_row.winfo_ismapped())
        output = self.base / 'exports' / 'synthetic-batch'
        value = {'exports': [], 'failed': [], 'alreadyExported': [{'chatId': 'synthetic-group'}],
                 'output': str(output), 'metadataComplete': False,
                 'metadataErrors': [{'file': 'batch.json', 'error': 'synthetic failure'}]}
        with patch.object(app, 'sync_review_states'):
            app.batch_export_ready(value)
        self.root.update()
        self.assertIn('正文已保存', app.status.get())
        self.assertIn('跨群分析材料', app.status.get())
        self.assertTrue(app.folder_button.instate(['!disabled']))
        self.assertTrue(app.repair_metadata_button.instate(['!disabled']))
        self.assertEqual(app.group_file_menu.entrycget(7, 'state'), 'disabled')
        self.inside(app.repair_metadata_button)
        self.inside(app.file_row)
        with patch.object(app, 'work') as work:
            app.repair_metadata_button.invoke()
            work.call_args.args[2]({'output': str(output), 'metadataComplete': True,
                                   'failed': [{'chatId': 'synthetic-unfinished'}]})
        self.assertIn('正文保持原样', app.status.get())
        self.assertIn('1 个会话未导出', app.status.get())

    def test_progress_from_worker_does_not_finish_job_or_enable_duplicate_export(self):
        app = self.app()
        app.busy = True
        app.update_buttons()
        worker = threading.Thread(target=lambda: app.export_events.put(
            {'stage': 'saved', 'completed': 2, 'total': 5, 'chatName': '合成群'}))
        worker.start()
        worker.join()
        app.drain_export_progress()
        self.assertTrue(app.busy)
        self.assertTrue(app.export_button.instate(['disabled']))
        self.assertIn('已保存 2/5 群', app.status.get())
        self.assertIn('合成群', app.status.get())
        app.export_events.put({'stage': 'analysis', 'completed': 5, 'total': 5})
        app.drain_export_progress()
        self.assertIn('生成跨群分析材料', app.status.get())
        self.assertTrue(app.busy)

    def test_late_progress_cannot_replace_final_export_summary(self):
        app = self.app()
        app.busy = True
        final_text = '正文已保存，导出清单未完整生成；请修复导出清单。'
        def completion_arrives():
            # The worker publishes progress after the first drain and before
            # the completion read, so both queues must be considered together.
            app.export_events.put({'stage': 'completed', 'completed': 2, 'total': 2})
            return (lambda _: app.status.set(final_text), {}, None, None)
        with patch.object(app.events, 'get_nowait', side_effect=completion_arrives):
            app._drain()
        self.assertFalse(app.busy)
        self.assertTrue(app.export_events.empty())
        app._drain()
        self.assertEqual(app.status.get(), final_text)

    def test_restart_restores_group_exports_and_saved_location(self):
        app = self.app('wecom')
        chats = multi_group_fixture(self.base / 'source', 'wecom')
        session = app.session
        session.open_source({'sourceRoot': str(self.base / 'source')})
        parsed = session.prepare_review(chats[0], copy_media=False)
        session.approve_review(parsed['review']['reviewId'], {key: True for key in REVIEW_CHECKS}, chat=chats[0])
        session.remember_selection([chats[0]], chats[0])
        result = session.export_reviewed([chats[0]])
        app.session = DesktopSession(session.directory, client='wecom')
        info = app.session.bootstrap(resume=True)
        self.assertEqual(info['restoredPublished'], 1)
        app.ready(info)
        self.root.after(1, app._drain)
        deadline = time.monotonic() + 8
        while app.read_busy:
            self.assertLess(time.monotonic(), deadline, 'saved reading did not load')
            self.root.update()
            time.sleep(.005)
        self.assertIn(chats[0], app.parsed_results)
        self.assertEqual(app.batch_output, Path(result['output']))
        self.assertIn(str(app.batch_output), app.result_text.get())
        self.assertTrue(app.folder_button.instate(['!disabled']))
        self.assertTrue(app.repair_metadata_button.instate(['!disabled']))
        self.assertEqual(app.group_file_menu.entrycget(7, 'state'), 'normal')
        self.assertEqual(app.group_file_menu.entrycget(11, 'state'), 'normal')
        self.assertEqual(app.group_file_menu.entrycget(12, 'state'), 'normal')
        with patch('local_chat_app.os.startfile') as open_file:
            app.open_batch_analysis()
        open_file.assert_called_once_with(str(Path(result['output']) / 'batch-analysis' / 'README.md'))

    def test_failed_reparse_keeps_old_group_result_and_review(self):
        app = self.app()
        chats = multi_group_fixture(self.base / 'source', 'wechat')
        session = app.session
        info = session.open_source({'sourceRoot': str(self.base / 'source')})
        original = session.prepare_review(chats[0], copy_media=False)
        session.approve_review(original['review']['reviewId'], {key: True for key in REVIEW_CHECKS}, chat=chats[0])
        app.groups = info['groups']
        app.parsed_results[chats[0]] = original
        app.parse_states[chats[0]] = '已复核'
        app.render_groups()
        app.show_chat(chats[0])
        def work(label, load, done, **options):
            try:
                done(load())
            except OSError:
                options['on_error']('合成解析失败')
        with patch.object(session, 'prepare_review', side_effect=OSError('synthetic failure')):
            with patch.object(app, 'work', side_effect=work):
                app.start_batch([chats[0]])
                self.root.update()
        self.assertEqual(session.review_status(chats[0]), '已复核')
        self.assertIs(app.parsed_results[chats[0]], original)
        self.assertIn('保留上次结果', app.parse_errors[chats[0]])


if __name__ == '__main__':
    unittest.main()
