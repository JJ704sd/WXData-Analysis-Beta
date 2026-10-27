"""Document structure survives all four export paths and private materials."""
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
from test_four_exports58 import CASES, CONTRACT_URL, REVOKE_SERVER_ID, assert_quality
from test_four_exports58 import source_fixture as previous_fixture, assert_new_content as assert_previous_content
from test_four_exports57 import _attachment_row
from test_database_adapter import create_shard
from test_pdf_forms59 import pdf_file
from test_spreadsheet_merges59 import xlsx_file, xls_file

PDF_NAME = 'filled-order-form.pdf'
XLSX_NAME = 'merged-order-sheet.xlsx'
XLS_NAME = 'merged-legacy-order-sheet.xls'


def source_fixture(base, client, mode, count=2):
    config, chat, originals = previous_fixture(base, client, mode, count=count)
    original_root = Path(config['supplementRoot'])
    paths = []
    for offset, filename, create in ((120, PDF_NAME, pdf_file), (130, XLSX_NAME, xlsx_file),
                                     (140, XLS_NAME, xls_file)):
        path = original_root / filename
        create(path)
        raw = path.read_bytes()
        originals[hashlib.sha256(raw).hexdigest()] = raw
        paths.append((offset, path))
    source = Path(config['sourceRoot'])
    if client == 'wecom':
        with closing(sqlite3.connect(source / 'message.db')) as db, db:
            for offset, path in paths:
                for mid in (offset, offset + 1):
                    db.execute('INSERT INTO message_table VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                               (mid, mid + 10, mid, 11, chat, 102, 1700000000 + mid, 0,
                                _attachment_row(client, path, mid), b'', b''))
    else:
        create_shard(source, relative='message/message_7.db', group=chat, rows=[
            {'local': mid, 'type': 49, 'text': _attachment_row(client, path, mid)}
            for offset, path in paths for mid in (offset, offset + 1)])
    return config, chat, originals


def assert_new_content(test, root, rows, client):
    assert_previous_content(test, root, rows, client)
    for filename in (PDF_NAME, XLSX_NAME, XLS_NAME):
        docs = [row['attachmentContent'] for row in rows if row.get('text') == filename]
        test.assertEqual(len(docs), 2)
        for doc in docs:
            if filename == PDF_NAME:
                test.assertIn('FORM_CUSTOMER_NEEDLE', doc['text'])
                test.assertIn('approved', doc['text'])
                test.assertIn('已批准', doc['text'])
            else:
                test.assertIn('A2:A3', doc['text'])
                test.assertIn('MERGED_CUSTOMER_NEEDLE', doc['text'])
                test.assertNotIn('A3=MERGED_CUSTOMER_NEEDLE', doc['text'])
    reading = [(root / filename).read_text(encoding='utf-8-sig')
               for filename in ('messages.md', '业务阅读.txt', 'chat.html', 'analysis/corpus.jsonl')]
    reading += [path.read_text(encoding='utf8') for path in (root / 'analysis').glob('part-*.md')]
    for keyword in ('FORM_CUSTOMER_NEEDLE', 'MERGED_CUSTOMER_NEEDLE', 'A2:A3'):
        for text in reading[:4]:
            test.assertIn(keyword, text)
        test.assertTrue(any(keyword in text for text in reading[4:]))
    source = {'messages': rows, 'analysisRefs': {row['id']: index for index, row in enumerate(rows, 1)}}
    for keyword, expected in (('FORM_CUSTOMER_NEEDLE', 2), ('MERGED_CUSTOMER_NEEDLE', 4)):
        result = search_messages(source, keyword)
        test.assertEqual(result['total'], expected)
        test.assertTrue(all('附件正文' in item['matchSource'] for item in result['items']))


class FourExports59Tests(unittest.TestCase):
    def test_new_document_information_and_historical_failures_across_four_exports(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-four59-') as folder:
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
