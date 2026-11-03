"""Regression tests for known WeCom text fields that fail UTF-8 decoding."""
import base64
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from offline_daily import build_daily_report
from offline_desktop import DesktopSession
from test_four_exports53 import source_fixture
from test_offline_documents import pdf_file
from wecom_content import typed_message


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


def forward_entry(*, sender=11, stamp=1700000000, name='合成发送人', payload=None):
    return (pb(1, sender) + pb(2, stamp) + pb(5, 2) + pb(11, name)
            + pb(101, payload or text_payload()))


def add_fields76(source, chat, supplement_root):
    """Add known malformed-field rows and return their raw fixture evidence."""
    source = Path(source)
    supplement_root = Path(supplement_root)
    supplement_root.mkdir(parents=True, exist_ok=True)
    original = supplement_root / 'fields76-804.pdf'
    if not original.exists():
        pdf_file(original, 'Synthetic fields76 attachment')
    original_bytes = original.read_bytes()
    digest = hashlib.md5(original_bytes).hexdigest()

    card_801 = pb(3, b'\xff') + pb(4, b'\xfe') + pb(1, 'https://safe.example.test/x')
    card_802 = pb(1, pb(1, '正常标题') + pb(5, b'\xff'))
    child_803 = forward_entry(name=b'\xfe', payload=text_payload('转发正文'))
    forward_803 = pb(2, b'\xff') + pb(1, child_803)
    file_804 = pb(2, b'\xff') + pb(4, len(original_bytes)) + pb(10, digest)
    rows = {
        801: {'kind': 13, 'content': card_801, 'extra_content': b''},
        802: {'kind': 31, 'content': card_802, 'extra_content': b''},
        803: {'kind': 4, 'content': forward_803, 'extra_content': b''},
        804: {'kind': 15, 'content': file_804, 'extra_content': b'', 'original': original_bytes},
    }
    with closing(sqlite3.connect(source / 'message.db')) as db, db:
        db.executemany(
            'insert into message_table values(?,?,?,?,?,?,?,?,?,?,?)',
            [(local_id, local_id + 10, local_id, 11, chat, item['kind'],
              1700000800 + local_id, 0, item['content'], item['extra_content'], b'')
             for local_id, item in rows.items()])
    return rows


class WeComKnownField76Tests(unittest.TestCase):
    def test_card_mappings_keep_valid_values_and_mark_bad_known_text(self):
        cases = {
            13: (pb(3, b'\xff') + pb(4, b'\xfe') + pb(1, 'https://safe.example.test/x'),
                 'https://safe.example.test/x'),
            31: (pb(1, pb(1, b'\xff') + pb(2, '描述') + pb(5, 'https://safe.example.test/31')),
                 'https://safe.example.test/31'),
            38: (pb(1, '标题') + pb(2, b'\xff') + pb(3, 'https://safe.example.test/38'),
                 'https://safe.example.test/38'),
            104: (pb(1, b'\xff') + pb(2, '描述') + pb(3, 'https://safe.example.test/104'),
                  'https://safe.example.test/104'),
        }
        for kind, (data, url) in cases.items():
            with self.subTest(kind=kind):
                parsed = typed_message(data, kind)
                self.assertEqual(parsed['parseStatus'], 'unsupported_text_fields')
                self.assertEqual(parsed['links'], [url])

    def test_card_bad_url_does_not_create_link_but_good_title_survives(self):
        parsed = typed_message(pb(3, '正常标题') + pb(1, b'\xff'), 13)
        self.assertEqual(parsed['text'], '正常标题')
        self.assertEqual(parsed['links'], [])
        self.assertEqual(parsed['kind'], 'system')
        self.assertEqual(parsed['parseStatus'], 'unsupported_text_fields')

    def test_empty_known_card_fields_remain_optional(self):
        self.assertEqual(typed_message(b'', 13)['parseStatus'], 'ok')
        self.assertEqual(typed_message(pb(3, b'') + pb(4, b''), 13)['parseStatus'], 'ok')

    def test_media_filename_failure_keeps_md5_and_media_object(self):
        digest = 'a' * 32
        for kind in (14, 15, 20, 22, 102):
            with self.subTest(kind=kind):
                fields = pb(2, b'\xff') + pb(10, digest)
                if kind == 22:
                    fields = pb(107, b'\xff') + pb(9, digest)
                parsed = typed_message(fields, kind)
                if kind == 102:
                    self.assertEqual(parsed['parseStatus'], 'unsupported_media_schema')
                    self.assertNotIn('media', parsed)
                else:
                    self.assertEqual(parsed['parseStatus'], 'unsupported_text_fields')
                    self.assertEqual(parsed['media']['md5'], digest)
        image = typed_message(pb(2, b'\xff') + pb(10, digest), 101)
        self.assertEqual(image['parseStatus'], 'ok')
        self.assertEqual(image['media']['filename'], 'image')
        self.assertEqual(image['media']['md5'], digest)
        self.assertEqual(typed_message(pb(10, 'invalid'), 101)['parseStatus'], 'unsupported_media_schema')

    def test_forward_title_and_source_name_failures_preserve_children(self):
        entry = forward_entry(name=b'\xfe', payload=text_payload('正文保留'))
        parsed = typed_message(pb(2, b'\xff') + pb(1, entry), 4)
        item = parsed['record']['items'][0]
        self.assertEqual(parsed['parseStatus'], 'unsupported_text_fields')
        self.assertEqual(parsed['record']['status'], 'partially_supported')
        self.assertEqual(item['text'], '正文保留')
        self.assertEqual(item['senderId'], '11')
        self.assertEqual(item['timestamp'], 1700000000)
        self.assertIn('sender_name', item['metadataIssues'])
        self.assertEqual(item['parseStatus'], 'invalid_metadata')
        self.assertEqual(item['contentParseStatus'], 'ok')

    def test_valid_nested_quote_and_empty_name_keep_existing_metadata_contract(self):
        entry = forward_entry(name=b'', payload=text_payload('合法引用'))
        extra = pb(1002, pb(1, entry))
        parsed = typed_message(text_payload('正文'), 2, extra=extra)
        self.assertEqual(parsed['quote']['message']['parseStatus'], 'ok')
        self.assertEqual(parsed['quote']['resolutionStatus'], 'embedded')
        self.assertNotIn('metadataIssues', parsed['quote']['message'])


class WeComKnownField76IntegrationTests(unittest.TestCase):
    def test_group_and_direct_exports_keep_field_gaps_and_attachment_evidence(self):
        for mode in ('group', 'direct'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(prefix='synthetic-fields76-') as folder:
                base = Path(folder)
                config, chat, _ = source_fixture(base, 'wecom', mode, count=1)
                expected = add_fields76(config['sourceRoot'], chat, base / 'fields76-originals')
                config['supplementRoot'] = str(base / 'fields76-originals')
                session = DesktopSession(base / 'profile', client='wecom', conversation_mode=mode)
                session.open_source(config)
                prepared = session.prepare_review(chat, copy_media=True, load_view=False)
                confirmed = session.confirm_export(
                    prepared['review']['reviewId'], {key: True for key in prepared['reviewChecks']},
                    'synthetic fields76 review', chat=chat)
                root = Path(confirmed['output'])
                rows = json.loads((root / 'messages.json').read_text(encoding='utf-8'))
                card = next(row for row in rows if row['localId'] == 801)
                self.assertEqual(card['parseStatus'], 'unsupported_text_fields')
                self.assertEqual(card['links'], ['https://safe.example.test/x'])
                bad_url = next(row for row in rows if row['localId'] == 802)
                self.assertEqual(bad_url['text'], '正常标题')
                self.assertEqual(bad_url['links'], [])
                forward = next(row for row in rows if row['localId'] == 803)
                self.assertEqual(forward['record']['status'], 'partially_supported')
                self.assertIn('sender_name', forward['record']['items'][0]['metadataIssues'])
                attachment = next(row for row in rows if row['localId'] == 804)
                self.assertEqual(attachment['asset']['status'], 'ready')
                self.assertEqual(attachment['asset']['source'], 'user_supplement')
                self.assertEqual(attachment['asset']['localCheck']['result'], 'matched_original_md5')
                self.assertEqual((root / attachment['asset']['path']).read_bytes(), expected[804]['original'])
                self.assertEqual(base64.b64decode(card['rawColumns']['content']['data']), expected[801]['content'])
                markdown = (root / 'messages.md').read_text(encoding='utf-8')
                business = (root / '业务阅读.txt').read_text(encoding='utf-8-sig')
                self.assertIn('解析提醒：', markdown)
                self.assertIn('解析提醒：', business)
                corpus = (root / 'analysis/corpus.jsonl').read_text(encoding='utf-8')
                self.assertIn('unsupported_text_fields', corpus)
                daily = build_daily_report([root], client='wecom', day='2023-11-15', output=base / 'daily')
                report = json.loads(Path(daily['jsonFile']).read_text(encoding='utf-8'))
                self.assertTrue(report['contentGaps'])


if __name__ == '__main__':
    unittest.main()
