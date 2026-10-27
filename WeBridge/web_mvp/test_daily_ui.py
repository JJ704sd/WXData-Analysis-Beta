"""Native Tk interactions with synthetic exports and isolated desktop settings."""
from datetime import date
import json
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from chat_release import DAILY_REPORT_VERSION
from offline_daily import DailyReportCancelled, DailyReportError, build_daily_report
from offline_daily_ui import DailyReportApp
from test_daily_distribution import source_pair
from test_daily_reports import DAY, formal_export, message


class DailyDesktopTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='synthetic-daily-desktop-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = tk.Tk()
        self.app = None
        self.addCleanup(self.cleanup_window)

    def cleanup_window(self):
        if self.app and self.app.busy:
            self.app.cancel_event.set()
            self.app.worker.join(3)
        try:
            if self.app and not self.app.closed:
                self.app._destroy()
            elif self.root.winfo_exists():
                self.root.destroy()
        except tk.TclError:
            pass

    def create_app(self, *, client='wechat', builder=build_daily_report):
        self.opened = []
        self.app = DailyReportApp(self.root, client, DAILY_REPORT_VERSION, builder=builder,
                                  settings_directory=self.base / 'settings' / client, opener=self.opened.append)
        self.app.auto_open.set(False)
        self.app.output_parent.set(str(self.base / 'reports'))
        self.app.date.set(DAY)
        return self.app

    def pump(self, condition, timeout=6):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertTrue(condition(), 'Desktop did not reach its expected state')

    def test_directory_picker_deduplicates_and_removes_only_selected_inputs(self):
        app = self.create_app()
        first, second = self.base / '已复核 私聊', self.base / '已复核 群聊'
        with patch('offline_daily_ui.filedialog.askdirectory', side_effect=[str(first), str(first), str(second), '']):
            for _ in range(4):
                app.choose_source()
        self.assertEqual(app.roots, [first, second])
        app.source_list.selection_set(0)
        app.remove_sources()
        self.assertEqual(app.roots, [second])
        self.assertIn('待生成时校验', app.source_count.get())
        app.clear_sources()
        self.assertEqual(app.roots, [])

    def test_background_failure_preserves_inputs_prevents_duplicate_jobs_and_restores_controls(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def slow_builder(roots, **options):
            calls.append(roots)
            entered.set()
            options['progress']({'phase': 'checking', 'current': 1, 'total': 2})
            release.wait(4)
            raise DailyReportError('材料尚未完成复核。')
        self.addCleanup(release.set)
        app = self.create_app(builder=slow_builder)
        app._add_path(self.base / 'reviewed')
        app._refresh_roots()
        app.names.set('业务称呼')
        app.generate()
        self.assertTrue(entered.wait(1))
        app.generate()
        self.assertEqual(len(calls), 1)
        self.assertIn('disabled', app.generate_button.state())
        responsive = []
        self.root.after(0, lambda: responsive.append(True))
        self.pump(lambda: bool(responsive) and '1 / 2' in app.status.get())
        release.set()
        self.pump(lambda: not app.busy)
        self.assertEqual(app.names.get(), '业务称呼')
        self.assertEqual(app.date.get(), DAY)
        self.assertEqual(len(app.roots), 1)
        self.assertNotIn('disabled', app.generate_button.state())
        self.assertIn('复核', app.status.get())
        self.assertIn('generation_failed', app.log_path.read_text(encoding='utf-8'))

    def test_cancel_and_close_wait_for_worker_cleanup(self):
        stopped = threading.Event()
        def cancellable(roots, **options):
            while not options['cancel']():
                time.sleep(.01)
            stopped.set()
            raise DailyReportCancelled('cancelled')
        app = self.create_app(builder=cancellable)
        app._add_path(self.base / 'reviewed')
        app.generate()
        with patch('offline_daily_ui.messagebox.askyesno', return_value=True):
            app.close()
        self.assertTrue(app.closing)
        self.pump(lambda: app.closed)
        self.assertTrue(stopped.is_set())
        self.assertFalse((self.base / 'reports').exists())

    def test_both_clients_generate_filter_show_business_details_and_open_results(self):
        for client in ('wechat', 'wecom'):
            with self.subTest(client=client):
                app = self.create_app(client=client)
                material = self.base / client
                material.mkdir()
                for root in source_pair(material, client):
                    app._add_path(root)
                app._refresh_roots()
                app.generate()
                self.pump(lambda: not app.busy)
                self.assertIsNotNone(app.result, app.status.get())
                self.assertEqual([value.get() for value in app.metrics], ['3', '2', '2', '0'])
                self.assertEqual(len(app.followup_tree.get_children()), 2)
                app.search.set('报价')
                children = app.followup_tree.get_children()
                self.assertEqual(len(children), 1)
                app.followup_tree.selection_set(children[0])
                app.show_detail()
                detail = app.detail.get('1.0', 'end')
                self.assertIn('请确认报价', detail)
                self.assertNotIn('synthetic-owner', detail)
                self.assertNotIn('evidenceId', detail)
                app.filter.set('处理信号待核对')
                self.assertEqual(app.followup_tree.get_children(), ())
                app.clear_filters()
                self.assertEqual(len(app.followup_tree.get_children()), 2)
                app.open_result('htmlFile')
                app.open_result('output')
                self.assertEqual(self.opened, [Path(app.result['htmlFile']), Path(app.result['output'])])
                app.copy_result()
                self.assertEqual(self.root.clipboard_get(), app.result['output'])
                first = app.result['output']
                app.generate()
                self.pump(lambda: not app.busy)
                self.assertNotEqual(app.result['output'], first)
                self.assertTrue((Path(first) / 'report.html').is_file())
                app._destroy()
                if client == 'wechat':
                    self.root = tk.Tk()

    def test_invalid_form_does_not_start_worker_or_discard_values(self):
        app = self.create_app()
        app.generate()
        self.assertIn('添加', app.status.get())
        app._add_path(self.base / 'reviewed')
        app.date.set('2026-02-30')
        app.generate()
        self.assertFalse(app.busy)
        self.assertEqual(app.date.get(), '2026-02-30')
        app.date.set(DAY)
        app.lookback.set('91')
        app.generate()
        self.assertIn('90', app.status.get())
        self.assertFalse((self.base / 'reports').exists())

    def test_unresolved_sender_name_keeps_identifier_out_of_business_preview(self):
        app = self.create_app()
        source = formal_export(self.base, 'unresolved-name', [message('request', '请确认报价', senderName='synthetic-peer')])
        app._add_path(source)
        app.generate()
        self.pump(lambda: not app.busy)
        selected = app.followup_tree.get_children()[0]
        values = app.followup_tree.item(selected)['values']
        self.assertIn('发送人待确认', values)
        self.assertNotIn('synthetic-peer', str(values))
        evidence = Path(app.result['evidenceFile']).read_text(encoding='utf-8')
        self.assertIn('synthetic-peer', evidence)

    def test_date_picker_changes_selection_and_settings_do_not_auto_run_or_restore_old_date(self):
        app = self.create_app()
        picker = app.pick_date()
        picker.choose(date(2026, 10, 26))
        self.assertEqual(app.date.get(), '2026-10-26')
        app.lookback.set('30')
        app.names.set('业务称呼')
        app._add_path(self.base / 'reviewed')
        app._save_settings()
        app._destroy()
        self.root = tk.Tk()
        self.app = DailyReportApp(self.root, 'wechat', DAILY_REPORT_VERSION,
                                  settings_directory=self.base / 'settings/wechat')
        self.assertEqual(self.app.lookback.get(), '30')
        self.assertEqual(self.app.names.get(), '业务称呼')
        self.assertEqual(len(self.app.roots), 1)
        self.assertEqual(self.app.date.get(), self.app.today.isoformat())
        self.assertFalse(self.app.busy)
        self.assertIsNone(self.app.result)

    def test_small_window_with_large_fonts_keeps_actions_and_lists_accessible(self):
        self.root.tk.call('tk', 'scaling', 1.75)
        app = self.create_app()
        self.root.geometry('1000x700')
        self.root.update()
        for widget in (app.generate_button, app.cancel_button, app.open_report_button, app.source_list):
            self.assertGreater(widget.winfo_width(), 20)
            self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), self.root.winfo_rootx() + self.root.winfo_width())
            self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), self.root.winfo_rooty() + self.root.winfo_height())
        app._add_path(self.base / ('long-folder-' * 35))
        app._refresh_roots()
        self.root.update()
        app.source_list.xview_moveto(1)
        self.assertGreater(app.source_list.xview()[0], 0)
        app.tabs.select(app.results)
        self.root.update()
        for widget in (app.search_entry, app.filter_box, app.followup_tree, app.detail):
            self.assertGreater(widget.winfo_height(), 20)
            self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), app.results.winfo_rooty() + app.results.winfo_height())
        app.followup_tree.xview_moveto(1)
        self.assertGreater(app.followup_tree.xview()[0], 0)


class DailyProgressTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='synthetic-daily-cancel-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)

    def test_progress_phases_and_cancel_before_publish_preserve_source_and_remove_stage(self):
        root = formal_export(self.base, 'reviewed', [message('request', '请确认报价')])
        before = {path.name: path.read_bytes() for path in root.iterdir()}
        phases = []
        stop = threading.Event()
        def callback(event):
            phases.append(event['phase'])
            if event['phase'] == 'writing':
                stop.set()
        output = self.base / 'cancelled'
        with self.assertRaises(DailyReportCancelled):
            build_daily_report([root], client='wechat', day=DAY, output=output, progress=callback, cancel=stop.is_set)
        self.assertEqual(set(phases), {'checking', 'reading', 'organizing', 'writing'})
        self.assertFalse(output.exists())
        self.assertFalse(list(self.base.glob('.daily-report-*')))
        self.assertEqual(before, {path.name: path.read_bytes() for path in root.iterdir()})

    def test_cancel_during_message_stream_stops_without_publishing(self):
        rows = [message('request-' + str(index), '普通消息') for index in range(600)]
        root = formal_export(self.base, 'reviewed', rows)
        stop = threading.Event()
        def callback(event):
            if event['phase'] == 'reading' and event['messages'] >= 500:
                stop.set()
        with self.assertRaises(DailyReportCancelled):
            build_daily_report([root], client='wechat', day=DAY, output=self.base / 'cancelled',
                               progress=callback, cancel=stop.is_set)
        self.assertFalse((self.base / 'cancelled').exists())


if __name__ == '__main__':
    unittest.main()
