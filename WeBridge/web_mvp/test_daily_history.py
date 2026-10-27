"""Historical encoding failures replayed at the reviewed daily-report boundary."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from offline_daily import DailyReportError, build_daily_report
from test_daily_reports import DAY, formal_export, message


def unicode_export(base, name, *, client='wechat', mode='direct', text=None, **request_fields):
    # Escaped UTF-16 units are legal JSON text. Rebuild the same reviewed file
    # inventory so the regression reaches the report, rather than a stale hash.
    root = formal_export(base, name, [message('placeholder', 'placeholder')], client=client, mode=mode)
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    rows = [message('request', text if text is not None else '请确认报价 ' + chr(0xD800) + ' 37/KG 💬', **request_fields),
            message('attachment', '历史附件正文', kind='file', asset={'status': 'not_loaded'},
                    attachmentContent={'extractionStatus': 'partial', 'text': '报价 ' + chr(0xDC00) + ' 37/KG 💬'}),
            message('after', '异常内容之后的消息完整保留')]
    rows = [{**row, 'chatId': manifest['chat']['id']} for row in rows]
    path = root / 'messages.jsonl'
    path.write_text(''.join(json.dumps(row, ensure_ascii=True) + '\n' for row in rows), encoding='utf-8')
    item = {'path': path.name, 'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    review_path = root / 'review.json'
    review = json.loads(review_path.read_text(encoding='utf-8'))
    review['reviewedArtifacts'] = [item]
    review_path.write_text(json.dumps(review, ensure_ascii=True), encoding='utf-8')
    manifest['exportedRows'] = len(rows)
    manifest['artifacts'] = [item, {'path': review_path.name, 'bytes': review_path.stat().st_size,
                                  'sha256': hashlib.sha256(review_path.read_bytes()).hexdigest()}]
    (root / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=True), encoding='utf-8')
    return root


class DailyHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='synthetic-daily-history-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)

    def test_bad_attachment_characters_do_not_abort_either_clients_daily_report(self):
        for client in ('wechat', 'wecom'):
            for mode in ('direct', 'group'):
                with self.subTest(client=client, mode=mode):
                    name = client + '-' + mode
                    root = unicode_export(self.base, name, client=client, mode=mode)
                    before = {path.name: path.read_bytes() for path in root.iterdir()}
                    result = build_daily_report([root], client=client, day=DAY, output=self.base / (name + '-report'))
                    self.assertTrue(result['ok'])
                    self.assertEqual(result['overview']['messages'], 3)
                    self.assertEqual(result['overview']['messagesWithGaps'], 2)
                    evidence = [json.loads(line) for line in Path(result['evidenceFile']).read_text(encoding='utf-8').splitlines()]
                    by_id = {row['messageId']: row for row in evidence}
                    self.assertIn(chr(0xD800), by_id['request']['text'])
                    self.assertIn(chr(0xDC00), by_id['attachment']['content']['attachmentContent']['text'])
                    self.assertEqual(by_id['after']['text'], '异常内容之后的消息完整保留')
                    self.assertIn('message_unicode_partial', by_id['request']['issues'])
                    html = Path(result['htmlFile']).read_text(encoding='utf-8')
                    self.assertIn('37/KG 💬', html)
                    self.assertIn('\\ud800', html)
                    self.assertIn('\\udc00', html)
                    self.assertIn('字符', html)
                    self.assertEqual(before, {path.name: path.read_bytes() for path in root.iterdir()})

    def test_escaped_display_does_not_merge_conflicting_original_messages(self):
        raw = unicode_export(self.base, 'raw-unit', text='请确认报价 ' + chr(0xD800))
        literal = unicode_export(self.base, 'literal-marker', text='请确认报价 \\ud800')
        output = self.base / 'conflict'
        with self.assertRaisesRegex(DailyReportError, '内容冲突'):
            build_daily_report([raw, literal], client='wechat', day=DAY, output=output)
        self.assertFalse(output.exists())
        duplicate = unicode_export(self.base, 'same-original', text='请确认报价 ' + chr(0xD800))
        result = build_daily_report([raw, duplicate], client='wechat', day=DAY, output=self.base / 'deduplicated')
        self.assertEqual(result['overview']['messages'], 3)
        self.assertEqual(result['overview']['duplicateOccurrences'], 3)
        rows = [json.loads(line) for line in Path(result['evidenceFile']).read_text(encoding='utf-8').splitlines()]
        self.assertTrue(all(len(row['sources']) == 2 for row in rows))


if __name__ == '__main__':
    unittest.main()
