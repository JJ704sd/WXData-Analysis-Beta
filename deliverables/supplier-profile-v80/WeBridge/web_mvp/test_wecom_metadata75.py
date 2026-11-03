"""Focused regression tests for nested WeCom source metadata."""
import json
import base64
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from wecom_content import typed_message
from offline_daily import build_daily_report
from offline_desktop import DesktopSession
from test_four_exports53 import source_fixture


def varint(value):
    data = bytearray()
    while value > 127:
        data.append((value & 127) | 128)
        value >>= 7
    return bytes(data) + bytes([value])


def pb(number, value):
    if isinstance(value, int):
        return varint(number << 3) + varint(value)
    value = value.encode() if isinstance(value, str) else value
    return varint((number << 3) | 2) + varint(len(value)) + value


def text_payload(value='嵌套正文'):
    return pb(1, pb(1, 0) + pb(2, pb(1, value)))


def forward_entry(*, sender=None, stamp=None, payload=None):
    values = []
    if sender is not None:
        values.append(pb(1, sender))
    if stamp is not None:
        values.append(pb(2, stamp))
    values.extend((pb(5, 2), pb(101, payload or text_payload())))
    return b''.join(values)


class WeComMetadata75Tests(unittest.TestCase):
    def test_forward_missing_source_fields_is_partial_without_inventing_zero(self):
        parsed = typed_message(pb(1, forward_entry()), 4)
        item = parsed['record']['items'][0]
        self.assertEqual(item['text'], '嵌套正文')
        self.assertEqual(item['senderId'], '')
        self.assertIsNone(item['timestamp'])
        self.assertEqual(item['time'], '')
        self.assertEqual(item['metadataIssues'], ['sender_id', 'send_time'])
        self.assertEqual(item['contentParseStatus'], 'ok')
        self.assertEqual(item['parseStatus'], 'invalid_metadata')
        self.assertEqual(parsed['record']['status'], 'partially_supported')

    def test_forward_single_missing_field_and_explicit_zero_are_distinguished(self):
        missing_sender = typed_message(pb(1, forward_entry(stamp=1700000000)), 4)['record']['items'][0]
        self.assertEqual(missing_sender['senderId'], '')
        self.assertEqual(missing_sender['timestamp'], 1700000000)
        self.assertEqual(missing_sender['time'], '2023-11-15T06:13:20+08:00')
        self.assertEqual(missing_sender['metadataIssues'], ['sender_id'])

        missing_time = typed_message(pb(1, forward_entry(sender=11)), 4)['record']['items'][0]
        self.assertEqual(missing_time['senderId'], '11')
        self.assertIsNone(missing_time['timestamp'])
        self.assertEqual(missing_time['time'], '')
        self.assertEqual(missing_time['metadataIssues'], ['send_time'])

        explicit_zero = typed_message(pb(1, forward_entry(sender=0, stamp=0)), 4)['record']['items'][0]
        self.assertEqual(explicit_zero['senderId'], '0')
        self.assertEqual(explicit_zero['timestamp'], 0)
        self.assertEqual(explicit_zero['time'], '')
        self.assertNotIn('metadataIssues', explicit_zero)

    def test_complete_forward_and_valid_quote_keep_existing_metadata(self):
        entry = forward_entry(sender=11, stamp=1700000000)
        parsed = typed_message(pb(1, entry), 4)
        item = parsed['record']['items'][0]
        self.assertEqual(item['senderId'], '11')
        self.assertEqual(item['timestamp'], 1700000000)
        self.assertEqual(item['parseStatus'], 'ok')

        quote_extra = pb(1002, pb(1, pb(1, 11) + pb(2, 1700000000) + pb(5, 2) + pb(101, text_payload())))
        quoted = typed_message(text_payload(), 2, extra=quote_extra)
        quote = quoted['quote']
        self.assertEqual(quote['senderId'], '11')
        self.assertEqual(quote['sourceTimestamp'], 1700000000)
        self.assertEqual(quote['message']['parseStatus'], 'ok')
        self.assertNotIn('metadataIssues', quote['message'])

    def test_invalid_quote_schema_keeps_status_and_marks_unknown_source_fields(self):
        invalid_extra = pb(1002, pb(1, pb(5, 2) + pb(101, text_payload())))
        parsed = typed_message(text_payload(), 2, extra=invalid_extra)
        quote = parsed['quote']
        self.assertEqual(quote['resolutionStatus'], 'unsupported_quote_schema')
        self.assertEqual(quote['senderId'], '')
        self.assertIsNone(quote['sourceTimestamp'])
        self.assertEqual(quote['message']['text'], '')
        self.assertEqual(quote['message']['metadataIssues'], ['sender_id', 'send_time'])
        self.assertEqual(quote['message']['contentParseStatus'], 'unsupported_quote_source')
        self.assertEqual(quote['message']['parseStatus'], 'invalid_metadata')


def add_metadata75(source, chat):
    """Extend a WeCom fixture with missing nested source metadata rows."""
    source = Path(source)
    payload = text_payload('待核对正文')
    missing = pb(1, forward_entry(payload=payload)) + pb(2, '缺元数据转发')
    invalid_quote = text_payload('无效引用')
    extra = pb(1002, pb(1, pb(5, 2) + pb(101, payload)))
    with closing(sqlite3.connect(source / 'message.db')) as db, db:
        db.executemany('insert into message_table values(?,?,?,?,?,?,?,?,?,?,?)', [
            (601, 601, 601, 11, chat, 4, 1700000601, 0, missing, b'', b''),
            (602, 602, 602, 11, chat, 2, 1700000602, 0, invalid_quote, extra, b''),
        ])
    return {
        601: {'content': missing, 'extra_content': b'', 'nested_payload': payload},
        602: {'content': invalid_quote, 'extra_content': extra},
    }


class WeComMetadata75IntegrationTests(unittest.TestCase):
    def test_group_and_direct_exports_keep_nested_metadata_gaps_and_source_bytes(self):
        for mode in ('group', 'direct'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(prefix='synthetic-metadata75-') as folder:
                base = Path(folder)
                config, chat, _ = source_fixture(base, 'wecom', mode, count=1)
                expected = add_metadata75(config['sourceRoot'], chat)
                session = DesktopSession(base / 'profile', client='wecom', conversation_mode=mode)
                session.open_source(config)
                prepared = session.prepare_review(chat, copy_media=True, load_view=False)
                confirmed = session.confirm_export(
                    prepared['review']['reviewId'], {key: True for key in prepared['reviewChecks']},
                    'synthetic metadata review', chat=chat)
                output = Path(confirmed['output'])
                rows = json.loads((output / 'messages.json').read_text(encoding='utf-8'))

                forward = next(row for row in rows if row['localId'] == 601)
                item = forward['record']['items'][0]
                self.assertEqual(item['senderId'], '')
                self.assertIsNone(item['timestamp'])
                self.assertEqual(item['time'], '')
                self.assertEqual(item['parseStatus'], 'invalid_metadata')
                self.assertEqual(item['metadataIssues'], ['sender_id', 'send_time'])
                self.assertEqual(base64.b64decode(forward['rawColumns']['content']['data']), expected[601]['content'])

                quote_row = next(row for row in rows if row['localId'] == 602)
                quote = quote_row['quote']
                self.assertEqual(quote['resolutionStatus'], 'unsupported_quote_schema')
                self.assertEqual(quote['senderId'], '')
                self.assertIsNone(quote['sourceTimestamp'])
                self.assertEqual(quote['message']['metadataIssues'], ['sender_id', 'send_time'])
                self.assertEqual(base64.b64decode(quote_row['rawColumns']['extra_content']['data']), expected[602]['extra_content'])

                markdown = (output / 'messages.md').read_text(encoding='utf-8')
                business = (output / '业务阅读.txt').read_text(encoding='utf-8-sig')
                self.assertIn('字段异常：sender\\_id、send\\_time', markdown)
                self.assertIn('字段异常：sender_id、send_time', business)
                corpus = [json.loads(line) for line in (output / 'analysis/corpus.jsonl').read_text(encoding='utf-8').splitlines()]
                corpus_forward = next(row for row in corpus if row['id'] == forward['id'])
                self.assertEqual(corpus_forward['forward']['items'][0]['metadataIssues'], ['sender_id', 'send_time'])
                daily = build_daily_report([output], client='wecom', day=forward['time'][:10], output=base / 'daily')
                report = json.loads(Path(daily['jsonFile']).read_text(encoding='utf-8'))
                self.assertTrue(report['contentGaps'])


if __name__ == '__main__':
    unittest.main()
