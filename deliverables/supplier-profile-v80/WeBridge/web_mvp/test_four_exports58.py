"""Four actual export paths retain legacy spreadsheet and forwarded information."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from offline_desktop import DesktopSession
from private_profiles import ProfileStore
from private_insights import search_messages
from test_four_exports57 import (CASES, CONTRACT_URL, REVOKE_SERVER_ID, _attachment_row,
                                 assert_quality, source_fixture as previous_fixture)
from test_four_exports57 import assert_new_content as assert_previous_content
from test_database_adapter import create_shard
from test_legacy_spreadsheet58 import xls_file
from test_forward_semantics58 import wechat_forward_xml

XLS_NAME = 'legacy-delivery.xls'


def source_fixture(base, client, mode, count=2):
    config, chat, originals = previous_fixture(base, client, mode, count=count)
    path = Path(config['supplementRoot']) / XLS_NAME
    xls_file(path)
    raw = path.read_bytes()
    originals[hashlib.sha256(raw).hexdigest()] = raw
    source = Path(config['sourceRoot'])
    if client == 'wecom':
        with closing(sqlite3.connect(source / 'message.db')) as db, db:
            for mid in (110, 111):
                db.execute('INSERT INTO message_table VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                           (mid, mid + 10, mid, 11, chat, 102, 1700000000 + mid, 0,
                            _attachment_row(client, path, mid), b'', b''))
    else:
        create_shard(source, relative='message/message_6.db', group=chat, rows=[
            {'local': mid, 'type': 49, 'text': _attachment_row(client, path, mid)}
            for mid in (110, 111)
        ] + [{'local': 112, 'type': 49, 'text': wechat_forward_xml()}])
    return config, chat, originals


def assert_new_content(test, root, rows, client):
    assert_previous_content(test, root, rows, client)
    docs = [row['attachmentContent'] for row in rows if row.get('text') == XLS_NAME]
    test.assertEqual(len(docs), 2)
    reading = [(root / filename).read_text(encoding='utf-8-sig')
               for filename in ('messages.md', '业务阅读.txt', 'chat.html', 'analysis/corpus.jsonl')]
    analysis = '\n'.join(path.read_text(encoding='utf8') for path in (root / 'analysis').glob('part-*.md'))
    for text in [doc['text'] for doc in docs] + reading + [analysis]:
        for expected in ('A1=2024-01-01', 'B1=2024-01-01T12:00:00', 'C1=12.50%',
                         'D1=000123', 'E1=TRUE', 'F1=#DIV/0!', '1900日期序列60'):
            test.assertIn(expected, text)
    for doc in docs:
        test.assertNotIn('A1=2024-01-01T12:00:00', doc['text'])
    if client == 'wechat':
        for text in reading + [analysis]:
            test.assertIn('LINK_TITLE_NEEDLE', text)
            test.assertIn('FORWARD_DESCRIPTION_NEEDLE', text)
        source = {'messages': rows, 'analysisRefs': {row['id']: index for index, row in enumerate(rows, 1)}}
        for keyword in ('LINK_TITLE_NEEDLE', 'FORWARD_DESCRIPTION_NEEDLE'):
            hits = search_messages(source, keyword)
            test.assertEqual(hits['total'], 1)
            test.assertIn(keyword, hits['items'][0]['snippet'])


class FourExports58Tests(unittest.TestCase):
    def test_new_content_and_old_failures_across_four_exports(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-four58-') as folder:
            for client, mode in CASES:
                with self.subTest(client=client, mode=mode):
                    base = Path(folder) / (client + '-' + mode)
                    base.mkdir()
                    config, chat, originals = source_fixture(base, client, mode)
                    session = DesktopSession(base / 'profile', client=client, conversation_mode=mode)
                    session.open_source(config)
                    prepared = session.prepare_review(chat, copy_media=True, load_view=False)
                    output = Path(prepared['view']['viewRoot'])
                    rows = json.loads((output / 'messages.json').read_text(encoding='utf8'))
                    assert_quality(self, output, rows, client, originals)
                    assert_new_content(self, output, rows, client)
                    if mode == 'direct':
                        formal = session.confirm_export(prepared['review']['reviewId'],
                            {key: True for key in prepared['reviewChecks']})
                        request = ProfileStore(base / 'portraits').prepare(formal['output'])
                        self.assertEqual(request['status'], 'ready')


if __name__ == '__main__':
    unittest.main()
