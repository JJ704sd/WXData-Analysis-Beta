"""Focused daily-report regressions using reviewed synthetic exports."""
import json
import contextlib
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from daily_report import main
from offline_chat import DirectoryPublishError
from offline_daily import DailyReportCancelled, DailyReportError, build_daily_report
from test_daily_reports import DAY, formal_export, message


class DailyRefinementTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='synthetic-daily-refinements-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)

    def generate(self, rows, *, name='case', client='wechat', mode='direct', **options):
        root = formal_export(self.base, name, rows, client=client, mode=mode)
        result = build_daily_report([root], client=client, day=DAY, output=self.base / (name + '-report'), **options)
        report = json.loads(Path(result['jsonFile']).read_text(encoding='utf-8'))
        return result, report

    def test_numeric_quote_links_only_its_request_when_multiple_requests_exist(self):
        for client in ('wechat', 'wecom'):
            with self.subTest(client=client):
                rows = [message(101, '请确认报价'),
                        message(102, '请安排发货', time='2026-10-27T09:01:00+08:00'),
                        message('reply', '已处理', own=True, kind='quote', time='2026-10-27T09:02:00+08:00',
                                quote={'sourceMessageId': 101, 'message': {'id': 101, 'text': '请确认报价'}})]
                result, report = self.generate(rows, name=client, client=client)
                clues = {item['text']: item for item in report['followups']}
                self.assertEqual(clues['请确认报价']['state'], 'completion_signal')
                self.assertEqual(clues['请确认报价']['replyBasis'], 'explicit_quote')
                self.assertEqual(clues['请安排发货']['state'], 'needs_response')
                evidence = Path(result['evidenceFile']).read_text(encoding='utf-8')
                self.assertIn('"sourceMessageId": 101', evidence)

    def test_same_second_reply_requires_an_explicit_quote(self):
        for client in ('wechat', 'wecom'):
            for quoted in (False, True):
                with self.subTest(client=client, quoted=quoted):
                    extra = {'kind': 'quote', 'quote': {'sourceMessageId': 'request'}} if quoted else {}
                    _, report = self.generate([message('request', '请确认报价'), message('reply', '已处理', own=True, **extra)],
                                              name=client + str(quoted), client=client)
                    self.assertEqual(report['followups'][0]['state'], 'completion_signal' if quoted else 'needs_response')

    def test_resolved_local_source_does_not_complete_another_embedded_identifier(self):
        _, report = self.generate([
            message('request-a', '请确认报价'),
            message('request-b', '请安排发货', time='2026-10-27T09:01:00+08:00'),
            message('reply', '已处理', own=True, kind='quote', time='2026-10-27T10:00:00+08:00',
                    quote={'sourceMessageId': 'request-a', 'message': {'id': 'request-b', 'text': '保留的嵌入原文'}})])
        clues = {item['text']: item for item in report['followups']}
        self.assertEqual(clues['请确认报价']['state'], 'completion_signal')
        self.assertEqual(clues['请安排发货']['state'], 'needs_response')

    def test_cancellation_during_followup_organization_does_not_publish(self):
        stop = threading.Event()
        rows = [message(str(index), '请确认报价') for index in range(600)]
        root = formal_export(self.base, 'organizing', rows)
        output = self.base / 'cancelled'
        def progress(event):
            if event['phase'] == 'organizing' and event.get('processedMessages', 0) >= 500:
                stop.set()
        with self.assertRaises(DailyReportCancelled):
            build_daily_report([root], client='wechat', day=DAY, output=output, progress=progress, cancel=stop.is_set)
        self.assertTrue(stop.is_set())
        self.assertFalse(output.exists())
        self.assertFalse(list(self.base.glob('.daily-report-*')))

    def test_malformed_mentions_do_not_use_substring_or_dictionary_membership_as_a_target(self):
        for index, value in enumerate(('prefix-synthetic-owner-suffix', {'synthetic-owner': True})):
            with self.subTest(valueType=type(value).__name__):
                _, report = self.generate([message('request', '请确认报价', mentions={'ids': value})],
                                          name='mentions-' + str(index), mode='group')
                self.assertEqual(report['overview']['personalFollowups'], 0)
                self.assertEqual(report['followups'][0]['state'], 'target_uncertain')

    def test_date_window_overflow_returns_an_actionable_validation_error(self):
        for day in ('0001-01-01', '9999-12-31'):
            with self.subTest(day=day), self.assertRaisesRegex(DailyReportError, '日期|回看'):
                build_daily_report([], client='wechat', day=day, output=self.base / day)
        self.assertFalse(any(self.base.iterdir()))

    def test_windows_publication_failure_returns_json_and_cleans_only_its_stage(self):
        root = formal_export(self.base, 'reviewed', [message('request', '请确认报价')])
        before = {path.name: path.read_bytes() for path in root.iterdir()}
        failure = PermissionError('synthetic Windows directory lock')
        failure.winerror = 5
        destination = self.base / 'locked-output'
        with patch('offline_daily.publish_directory', side_effect=DirectoryPublishError(failure)), contextlib.redirect_stdout(io.StringIO()) as stream:
            code = main('wechat', ['--export-root', str(root), '--date', DAY, '--output', str(destination)])
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(stream.getvalue())['ok'])
        self.assertIn('占用', json.loads(stream.getvalue())['error'])
        self.assertFalse(destination.exists())
        self.assertFalse(list(self.base.glob('.daily-report-*')))
        self.assertEqual(before, {path.name: path.read_bytes() for path in root.iterdir()})


if __name__ == '__main__':
    unittest.main()
