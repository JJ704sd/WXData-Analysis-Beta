"""Synthetic acceptance tests for both daily-report scripts and four export sources."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from offline_daily import DailyReportError, build_daily_report
from offline_review import REVIEW_CHECKS


DAY = '2026-10-27'
SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


def message(identifier, text, *, own=False, time='2026-10-27T09:00:00+08:00', **extra):
    return {'id': identifier, 'time': time, 'kind': 'text', 'text': text,
            'senderId': 'synthetic-owner' if own else 'synthetic-peer',
            'senderName': '合成本人' if own else '合成对方', 'direction': 'outgoing' if own else 'incoming',
            'mentions': {'ids': []}, 'parseStatus': 'ok', 'decodeStatus': 'ok', **extra}


def formal_export(base, name, rows, *, client='wechat', mode='direct', own='synthetic-owner', chat_id=None):
    root = base / name
    root.mkdir()
    chat = {'id': chat_id or (mode + '-conversation'), 'name': '合成' + ('群聊' if mode == 'group' else '私聊'), 'conversationKind': mode}
    records = [{**row, 'chatId': chat['id']} for row in rows]
    path = root / 'messages.jsonl'
    path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in records), encoding='utf-8')
    reviewed = [{'path': path.name, 'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]
    review = {'status': 'confirmed', 'reviewId': 'synthetic-review-' + name, 'client': client,
              'chat': chat, 'selfId': own, 'sourceFingerprint': 'synthetic-source-fingerprint',
              'startDate': None, 'endDate': None, 'confirmationBasis': 'explicit_review_checklist',
              'checks': {key: True for key in REVIEW_CHECKS}, 'reviewedArtifacts': reviewed}
    review_path = root / 'review.json'
    review_path.write_text(json.dumps(review, ensure_ascii=False), encoding='utf-8')
    manifest = {'schemaVersion': 1, 'client': client, 'chat': chat, 'exportedRows': len(rows),
                'selfId': own, 'startDate': None, 'endDate': None,
                'humanReview': {'status': 'confirmed', 'reviewId': review['reviewId']},
                'artifacts': reviewed + [{'path': 'review.json', 'bytes': review_path.stat().st_size,
                                         'sha256': hashlib.sha256(review_path.read_bytes()).hexdigest()}]}
    (root / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    return root


class DailyReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='synthetic-daily-report-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)

    def generate(self, roots, *, client='wechat', name='report', **kwargs):
        result = build_daily_report(roots, client=client, day=DAY, output=self.base / name, **kwargs)
        report = json.loads(Path(result['jsonFile']).read_text(encoding='utf-8'))
        evidence = [json.loads(line) for line in Path(result['evidenceFile']).read_text(encoding='utf-8').splitlines()]
        return result, report, evidence

    def test_both_clients_combine_group_and_direct_without_account_mixing(self):
        for client in ('wechat', 'wecom'):
            with self.subTest(client=client):
                direct = formal_export(self.base, client + '-direct', [message('d1', '请确认报价')], client=client)
                group = formal_export(self.base, client + '-group', [message('g1', '请尽快处理订单', mentions={'ids': ['synthetic-owner']})], client=client, mode='group')
                _, report, evidence = self.generate([direct, group], client=client, name=client + '-report')
                self.assertEqual(report['overview']['messages'], 2)
                self.assertEqual(report['overview']['activeConversations'], 2)
                self.assertEqual(report['overview']['personalFollowups'], 2)
                self.assertEqual({row['conversationMode'] for row in evidence}, {'group', 'direct'})
                self.assertEqual(len(report['sources']), 2)

    def test_four_real_export_entry_points_are_accepted(self):
        from offline_desktop import DesktopSession
        from test_four_exports53 import CASES, source_fixture
        for client, mode in CASES:
            with self.subTest(client=client, mode=mode):
                base = self.base / (client + mode)
                base.mkdir()
                config, chat, _ = source_fixture(base, client, mode, count=0)
                session = DesktopSession(base / 'profile', client=client, conversation_mode=mode)
                session.open_source(config)
                parsed = session.prepare_review(chat, copy_media=False, load_view=False)
                exported = session.confirm_export(parsed['review']['reviewId'], {key: True for key in REVIEW_CHECKS}, chat=chat)
                result = build_daily_report([exported['output']], client=client, day='2023-11-15', output=base / 'daily')
                self.assertTrue(result['ok'])
                self.assertGreater(result['overview']['messages'], 0)

    def test_deferral_does_not_remove_unfinished_request_and_earlier_day_is_retained(self):
        root = formal_export(self.base, 'deferred', [
            message('request', '请确认报价', time='2026-10-26T18:00:00+08:00'),
            message('reply', '我先开会，稍后回复', own=True, time='2026-10-27T10:00:00+08:00')])
        _, report, _ = self.generate([root])
        self.assertEqual(report['overview']['messages'], 1)
        self.assertEqual(report['overview']['personalFollowups'], 1)
        self.assertEqual(report['followups'][0]['state'], 'deferred')
        self.assertIsNotNone(report['followups'][0]['replyEvidenceId'])

    def test_independent_requests_and_explicit_quote_are_not_collapsed(self):
        root = formal_export(self.base, 'multi', [
            message('request-1', '请确认报价'),
            message('request-2', '请处理发票', time='2026-10-27T09:05:00+08:00'),
            message('reply-1', '已完成', own=True, time='2026-10-27T10:00:00+08:00', quote={'sourceMessageId': 'request-1'})])
        _, report, _ = self.generate([root])
        self.assertEqual(len(report['followups']), 2)
        self.assertEqual({item['text']: item['state'] for item in report['followups']},
                         {'请确认报价': 'completion_signal', '请处理发票': 'needs_response'})
        self.assertEqual(report['overview']['personalFollowups'], 1)

    def test_other_group_targets_and_unknown_identity_are_not_personal_tasks(self):
        group = formal_export(self.base, 'group', [
            message('other', '张三，请确认报价', mentions={'ids': ['synthetic-other']}),
            message('unknown', '请确认发货时间'),
            message('named', '李四，请处理订单')], mode='group')
        _, report, _ = self.generate([group], my_names=['李四'])
        self.assertEqual(len(report['followups']), 2)
        self.assertEqual(report['overview']['personalFollowups'], 1)
        self.assertEqual({item['targetBasis'] for item in report['followups']}, {'group_target_unknown', 'explicit_name_clue'})
        unknown = formal_export(self.base, 'identity-unknown', [message('u1', '请确认报价')], own='')
        _, report, _ = self.generate([unknown], name='unknown-report')
        self.assertIsNone(report['overview']['outgoing'])
        self.assertIsNone(report['conversations'][0]['outgoing'])
        self.assertEqual(report['overview']['personalFollowups'], 0)
        self.assertEqual(report['followups'][0]['state'], 'identity_unknown')

    def test_quoted_forwarded_and_attachment_words_preserve_authorship(self):
        root = formal_export(self.base, 'nested', [
            message('quote', '收到，谢谢', kind='quote', quote={'message': {'text': '紧急，请确认报价', 'senderId': 'third-party'}}),
            message('record', '转发记录', kind='record', record={'status': 'ok', 'items': [{'kind': 'text', 'text': '请尽快处理'}]}),
            message('document', '附件.pdf', kind='file', attachmentContent={'extractionStatus': 'partial', 'text': '紧急，请确认报价', 'requiresReview': True})])
        _, report, evidence = self.generate([root])
        self.assertEqual(report['followups'], [])
        self.assertEqual(report['riskClues'], [])
        self.assertGreater(report['overview']['messagesWithGaps'], 0)
        self.assertTrue(any('紧急，请确认报价' in json.dumps(row, ensure_ascii=False) for row in evidence))

    def test_declarative_completion_is_not_a_new_request(self):
        root = formal_export(self.base, 'declaration', [message('d1', '报价已确认，订单已完成')])
        _, report, _ = self.generate([root])
        self.assertEqual(report['followups'], [])

    def test_beijing_day_boundaries_undated_and_future_reply(self):
        root = formal_export(self.base, 'times', [
            message('before', '日前', time='2026-10-26T15:59:59Z'),
            message('start', '请确认方案', time='2026-10-26T16:00:00Z'),
            message('future', '已处理', own=True, time='2026-10-27T16:00:00Z'),
            message('unknown', '请确认未知时间', time='invalid-time')])
        _, report, evidence = self.generate([root], lookback_days=1)
        self.assertEqual(report['overview']['messages'], 1)
        self.assertEqual(report['overview']['hourly'][0], 1)
        self.assertEqual(report['overview']['undatedMessages'], 1)
        self.assertEqual(report['followups'][0]['state'], 'needs_response')
        self.assertEqual({row['messageId'] for row in evidence}, {'start', 'unknown'})

    def test_overlap_is_deduplicated_by_id_and_conflicting_versions_fail(self):
        first = formal_export(self.base, 'first', [message('same', '请确认报价')])
        second = formal_export(self.base, 'second', [message('same', '请确认报价')])
        _, report, evidence = self.generate([first, second])
        self.assertEqual(report['overview']['messages'], 1)
        self.assertEqual(report['overview']['duplicateOccurrences'], 1)
        self.assertEqual(len(evidence[0]['sources']), 2)
        changed = formal_export(self.base, 'changed', [message('same', '请确认新报价')])
        with self.assertRaisesRegex(DailyReportError, '内容冲突'):
            self.generate([first, changed], name='conflict')
        self.assertFalse((self.base / 'conflict').exists())

    def test_client_account_review_and_digest_validation_precede_output(self):
        original = formal_export(self.base, 'original', [message('m1', '请确认报价')])
        other_client = formal_export(self.base, 'other-client', [message('m2', '请确认报价')], client='wecom')
        other_account = formal_export(self.base, 'other-account', [message('m3', '请确认报价')], own='another-synthetic-owner')
        for roots, expected in (([original, other_client], '客户端'), ([original, other_account], '账号')):
            with self.subTest(expected=expected), self.assertRaisesRegex(DailyReportError, expected):
                self.generate(roots)
        (original / 'messages.jsonl').write_text('{}\n', encoding='utf-8')
        with self.assertRaisesRegex(DailyReportError, '摘要'):
            self.generate([original])
        self.assertFalse((self.base / 'report').exists())

    def test_batch_roots_and_partial_coverage_are_explicit(self):
        batch = self.base / 'batch'
        batch.mkdir()
        formal_export(batch, 'chat-one', [message('a', '请确认报价')])
        (batch / 'batch.json').write_text(json.dumps({'groups': [{'directory': 'chat-one'}], 'failed': [{'chatId': 'missing'}]}), encoding='utf-8')
        result, report, _ = self.generate([batch])
        self.assertEqual(report['overview']['messages'], 1)
        self.assertTrue(any('批次' in item for item in report['limitations']))
        self.assertTrue(any('批次' in item for item in result['notices']))

    def test_review_checks_must_be_actual_booleans(self):
        root = formal_export(self.base, 'checks', [message('m1', '请确认报价')])
        review_path = root / 'review.json'
        review = json.loads(review_path.read_text(encoding='utf-8'))
        review['checks'] = {key: 1 for key in REVIEW_CHECKS}
        review_path.write_text(json.dumps(review, ensure_ascii=False), encoding='utf-8')
        manifest_path = root / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        item = next(item for item in manifest['artifacts'] if item['path'] == 'review.json')
        item.update(bytes=review_path.stat().st_size, sha256=hashlib.sha256(review_path.read_bytes()).hexdigest())
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        with self.assertRaisesRegex(DailyReportError, '人工复核'):
            self.generate([root])

    def test_output_is_new_and_outside_original_exports(self):
        root = formal_export(self.base, 'input', [message('m1', '请确认报价')])
        for output in (root / 'daily', root.parent):
            with self.assertRaises(DailyReportError):
                build_daily_report([root], client='wechat', day=DAY, output=output)
        self.generate([root])
        before = (self.base / 'report/report.json').read_bytes()
        with self.assertRaisesRegex(DailyReportError, '已存在'):
            self.generate([root])
        self.assertEqual((self.base / 'report/report.json').read_bytes(), before)

    def test_html_escapes_message_data_and_manifest_covers_outputs(self):
        payload = '请确认 <img src=x onerror="alert(1)"> 报价 & </script>'
        root = formal_export(self.base, 'html', [message('m1', payload)])
        result, report, evidence = self.generate([root])
        body = Path(result['htmlFile']).read_text(encoding='utf-8')
        self.assertNotIn('<img src=x', body)
        self.assertIn('&lt;img', body)
        self.assertIn('clue-filter', body)
        self.assertIn('id="' + evidence[0]['evidenceId'] + '"', body)
        self.assertIn(payload, evidence[0]['text'])
        manifest = json.loads((self.base / 'report/manifest.json').read_text(encoding='utf-8'))
        self.assertEqual({row['path'] for row in manifest['files']}, {'report.html', 'report.json', 'evidence.jsonl', 'diagnostics.jsonl'})
        for row in manifest['files']:
            self.assertEqual(row['sha256'], hashlib.sha256((self.base / 'report' / row['path']).read_bytes()).hexdigest())
        self.assertFalse(report['unread']['available'])

    def test_business_html_keeps_trace_ids_in_diagnostics(self):
        root = formal_export(self.base, 'business-view', [message('internal-message-token', '请确认新报价',
            kind='quote', quote={'message': {'id': 'internal-quote-token', 'text': '旧报价仅作参考', 'senderId': 'internal-sender-token'}})])
        result, report, evidence = self.generate([root])
        body = Path(result['htmlFile']).read_text(encoding='utf-8')
        for value in ('internal-message-token', 'internal-quote-token', 'internal-sender-token', report['sources'][0]['sourceId'],
                      report['sources'][0]['reviewId'], str(root), 'sourceFingerprint', 'source_id_unavailable'):
            self.assertNotIn(value, body)
        self.assertIn('请确认新报价', body)
        self.assertIn('旧报价仅作参考', body)
        self.assertIn('诊断日志', body)
        diagnostic = Path(result['diagnosticsFile']).read_text(encoding='utf-8')
        self.assertIn('internal-message-token', diagnostic)
        self.assertIn(report['sources'][0]['reviewId'], diagnostic)
        self.assertIn(report['sources'][0]['sourceId'], diagnostic)
        self.assertIn('internal-quote-token', json.dumps(evidence, ensure_ascii=False))

    def test_both_client_clis_report_capabilities_and_generate_real_files(self):
        for client in ('wechat', 'wecom'):
            with self.subTest(client=client):
                script = SCRIPTS / (client + '_daily_report.py')
                capabilities = subprocess.run([sys.executable, str(script), '--capabilities'], capture_output=True, text=True, encoding='utf-8', timeout=20)
                self.assertEqual(capabilities.returncode, 0, capabilities.stderr)
                self.assertEqual(json.loads(capabilities.stdout)['client'], client)
                root = formal_export(self.base, 'cli-' + client, [message('m1', '请确认报价')], client=client)
                output = self.base / ('cli-report-' + client)
                result = subprocess.run([sys.executable, str(script), '--export-root', str(root), '--date', DAY, '--output', str(output)],
                                        capture_output=True, text=True, encoding='utf-8', timeout=20)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue(json.loads(result.stdout)['ok'])
                self.assertTrue((output / 'report.html').is_file())


if __name__ == '__main__':
    unittest.main()
