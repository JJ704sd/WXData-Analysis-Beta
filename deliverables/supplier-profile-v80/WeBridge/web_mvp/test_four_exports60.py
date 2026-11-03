"""Referenced document annotations retain provenance across four export paths."""
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
from test_four_exports59 import CASES, CONTRACT_URL, REVOKE_SERVER_ID, assert_quality
from test_four_exports59 import source_fixture as previous_fixture, assert_new_content as assert_previous_content
from test_four_exports57 import _attachment_row
from test_database_adapter import create_shard
from test_word_annotations60 import docx_file
from test_spreadsheet_annotations60 import xlsx_file

DOCX_NAME = 'reviewed-contract-notes.docx'
XLSX_NAME = 'shared-formula-comments.xlsx'
KEYWORDS = ('WORD_COMMENT_NEEDLE', 'FOOTNOTE_NEEDLE', 'ENDNOTE_NEEDLE',
            'XLSX_COMMENT_NEEDLE', 'SHARED_FORMULA_NEEDLE')


def source_fixture(base, client, mode, count=2):
    config, chat, originals = previous_fixture(base, client, mode, count=count)
    original_root = Path(config['supplementRoot'])
    paths = []
    for offset, filename, create in ((150, DOCX_NAME, docx_file), (160, XLSX_NAME, xlsx_file)):
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
        create_shard(source, relative='message/message_8.db', group=chat, rows=[
            {'local': mid, 'type': 49, 'text': _attachment_row(client, path, mid)}
            for offset, path in paths for mid in (offset, offset + 1)])
    return config, chat, originals


def assert_new_content(test, root, rows, client):
    assert_previous_content(test, root, rows, client)
    for filename, keywords in ((DOCX_NAME, KEYWORDS[:3]), (XLSX_NAME, KEYWORDS[3:])):
        docs = [row['attachmentContent'] for row in rows if row.get('text') == filename]
        test.assertEqual(len(docs), 2)
        for doc in docs:
            for keyword in keywords:
                test.assertIn(keyword, doc['text'])
            if filename == DOCX_NAME:
                for label in ('脚注 ID 2', '尾注 ID 3', '批注 ID 0'):
                    test.assertIn(label, doc['text'])
                test.assertNotIn('UNREFERENCED_NOTE_CANARY', doc['text'])
                test.assertNotIn('DELETED_NOTE_CANARY', doc['text'])
            else:
                test.assertNotIn('[公式未执行: =]', doc['text'])
                test.assertIn('共享', doc['text'])
                test.assertIn('缓存', doc['text'])
    reading = [(root / filename).read_text(encoding='utf-8-sig')
               for filename in ('messages.md', '业务阅读.txt', 'chat.html', 'analysis/corpus.jsonl')]
    reading += [path.read_text(encoding='utf8') for path in (root / 'analysis').glob('part-*.md')]
    for keyword in KEYWORDS:
        for text in reading[:4]:
            test.assertIn(keyword, text)
        test.assertTrue(any(keyword in text for text in reading[4:]))
    for text in reading:
        test.assertNotIn('UNREFERENCED_NOTE_CANARY', text)
        test.assertNotIn('DELETED_NOTE_CANARY', text)
    source = {'messages': rows, 'analysisRefs': {row['id']: index for index, row in enumerate(rows, 1)}}
    for keyword in KEYWORDS:
        result = search_messages(source, keyword)
        test.assertEqual(result['total'], 2)
        test.assertTrue(all('附件正文' in item['matchSource'] for item in result['items']))


class FourExports60Tests(unittest.TestCase):
    def test_annotations_and_historical_failures_across_four_exports(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-four60-') as folder:
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
