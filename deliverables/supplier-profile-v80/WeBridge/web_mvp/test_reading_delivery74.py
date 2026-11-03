"""Check partial messages and verified links in all four reviewed export flows."""
import base64
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from offline_daily import build_daily_report
from offline_desktop import DesktopSession
from test_database_adapter import create_shard
from test_four_exports53 import CASES
from test_wecom_gap_delivery73 import source73
from test_wecom_text_gaps73 import pb, text_block


GAP = '[文本片段未完整解析]'
EXPECTED_TEXT = ('金额10' + GAP + '0元 完整网址 https://safe.example.test/x。 '
                 '损坏网址 https://example.' + GAP + 'com 正常换片段 https://example.test/full')
EXPECTED_LINKS = ['https://safe.example.test/x', 'https://example.test/full']


def source74(base, client, mode):
    config, chat, originals, _payload, picture = source73(base, client, mode)
    source = Path(config['sourceRoot'])
    unknown = pb(1, pb(1, 99) + pb(2, pb(1, 'unmapped')))
    payload = (text_block('金额10') + unknown + text_block('0元 完整网址 https://safe.example.test/x。 '
               '损坏网址 https://example.') + unknown + text_block('com 正常换片段 https://exa')
               + text_block('mple.test/full'))
    if client == 'wecom':
        with closing(sqlite3.connect(source / 'message.db')) as db, db:
            db.execute('UPDATE message_table SET content=? WHERE message_id=401', (payload,))
        identifier = 401
    else:
        create_shard(source, relative='message/message_74.db', group=chat,
            rows=[{'local': 501, 'type': 49, 'text': '<msg><appmsg><type>5</type><title>未完成卡片'}])
        identifier = 501
    return config, chat, originals, payload, picture, identifier


class ReadingDelivery74Tests(unittest.TestCase):
    def test_four_flows_keep_partial_notices_and_do_not_invent_links(self):
        for client, mode in CASES:
            with self.subTest(client=client, mode=mode), tempfile.TemporaryDirectory(prefix='synthetic-reading74-') as folder:
                base = Path(folder)
                config, chat, _originals, payload, _picture, identifier = source74(base, client, mode)
                session = DesktopSession(base / 'profile', client=client, conversation_mode=mode)
                session.open_source(config)
                prepared = session.prepare_review(chat, copy_media=True, load_view=False)
                self.assertEqual(prepared['state'], 'awaiting_review')
                before = (Path(prepared['view']['viewRoot']) / 'messages.json').read_bytes()
                confirmed = session.confirm_export(prepared['review']['reviewId'],
                    {key: True for key in prepared['reviewChecks']}, 'synthetic reading review', chat=chat)
                self.assertEqual(confirmed['humanReview']['status'], 'confirmed')
                output = Path(confirmed['output'])
                self.assertEqual((output / 'messages.json').read_bytes(), before)
                rows = json.loads(before)
                partial = next(row for row in rows if row['localId'] == identifier)
                self.assertNotEqual(partial['parseStatus'], 'ok')
                markdown = (output / 'messages.md').read_text(encoding='utf-8')
                business = (output / '业务阅读.txt').read_text(encoding='utf-8-sig')
                self.assertIn('解析提醒：', markdown)
                self.assertIn('解析提醒：', business)
                if client == 'wecom':
                    self.assertEqual(partial['text'], EXPECTED_TEXT)
                    self.assertEqual(partial['links'], EXPECTED_LINKS)
                    self.assertEqual(base64.b64decode(partial['rawColumns']['content']['data']), payload)
                    self.assertIn(EXPECTED_TEXT, markdown)
                    self.assertIn(EXPECTED_TEXT, business)
                    corpus = [json.loads(line) for line in (output / 'analysis/corpus.jsonl').read_text(encoding='utf-8').splitlines()]
                    reading = next(row for row in corpus if row['id'] == partial['id'])
                    self.assertEqual(reading['links'], EXPECTED_LINKS)
                    self.assertEqual(reading['parseStatus'], 'unsupported_text_blocks')
                daily = build_daily_report([output], client=client, day=partial['time'][:10], output=base / 'daily')
                evidence = [json.loads(line) for line in (base / 'daily/evidence.jsonl').read_text(encoding='utf-8').splitlines()]
                item = next(row for row in evidence if row['messageId'] == partial['id'])
                self.assertIn('message_partial', item['issues'])
                report = json.loads(Path(daily['jsonFile']).read_text(encoding='utf-8'))
                self.assertTrue(any(row['evidenceId'] == item['evidenceId'] for row in report['contentGaps']))


if __name__ == '__main__':
    unittest.main()
