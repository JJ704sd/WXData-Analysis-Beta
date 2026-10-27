"""Legacy annotations and slide visibility survive every offline entry point."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile

from offline_desktop import DesktopSession
from private_profiles import ProfileStore
from private_insights import search_messages
from test_four_exports64 import CASES, CONTRACT_URL, REVOKE_SERVER_ID, assert_quality
from test_four_exports64 import source_fixture as previous_fixture, assert_new_content as assert_previous_content
from test_four_exports57 import _attachment_row
from test_database_adapter import create_shard
from test_legacy_annotations65 import xls_file
from test_presentation_visibility65 import pptx_file

XLS_NAME = 'legacy-annotations65.xls'
PPT_NAME = 'hidden-slides65.pptx'
KEYWORDS = ('https://example.test/legacy', 'NOTE_BODY', 'NOTE_AUTHOR',
            'PPT_LINK65_NEEDLE', 'NOTE_LINK65_NEEDLE')


def _deck(path):
    pptx_file(path)
    with zipfile.ZipFile(path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    with zipfile.ZipFile(path, 'w') as archive:
        for name, raw in files.items():
            # Different needles keep earlier cross-entry search assertions exact.
            raw = raw.replace(b'_LINK_NEEDLE', b'_LINK65_NEEDLE')
            archive.writestr(name, raw)


def source_fixture(base, client, mode, count=2):
    config, chat, originals = previous_fixture(base, client, mode, count=count)
    paths = []
    for offset, filename, create in ((250, XLS_NAME, xls_file), (260, PPT_NAME, _deck)):
        path = Path(config['supplementRoot']) / filename
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
        create_shard(source, relative='message/message_13.db', group=chat, rows=[
            {'local': mid, 'type': 49, 'text': _attachment_row(client, path, mid)}
            for offset, path in paths for mid in (offset, offset + 1)])
    return config, chat, originals


def assert_new_content(test, root, rows, client):
    assert_previous_content(test, root, rows, client)
    for filename, keywords in ((XLS_NAME, KEYWORDS[:3]), (PPT_NAME, KEYWORDS[3:])):
        docs = [row['attachmentContent'] for row in rows if row.get('text') == filename]
        test.assertEqual(len(docs), 2)
        for doc in docs:
            for keyword in keywords:
                test.assertIn(keyword, doc['text'])
            if filename == PPT_NAME:
                hidden = [s for s in doc['sections'] if 'VISIBLE_SLIDE65' not in s['text']]
                test.assertTrue(all('[隐藏幻灯片]' in s['location'] for s in hidden))
            else:
                test.assertIn('隐藏工作表', doc['text'])
                test.assertIn('批注 B3 [隐藏列]', doc['text'])
                test.assertIn('超链接 A2 [隐藏行]', doc['text'])
    reading = [(root / name).read_text(encoding='utf-8-sig') for name in
               ('messages.md', '业务阅读.txt', 'chat.html', 'analysis/corpus.jsonl')]
    parts = '\n'.join(path.read_text(encoding='utf8') for path in (root / 'analysis').glob('part-*.md'))
    for text in reading + [parts]:
        for keyword in KEYWORDS:
            test.assertIn(keyword, text)
        test.assertIn('隐藏幻灯片', text)
    source = {'messages': rows, 'analysisRefs': {row['id']: index for index, row in enumerate(rows, 1)}}
    for keyword in KEYWORDS:
        hits = search_messages(source, keyword)
        test.assertEqual(hits['total'], 2)
        test.assertTrue(all('附件正文' in item['matchSource'] for item in hits['items']))


class FourExports65Tests(unittest.TestCase):
    def test_annotation_visibility_and_historical_failures_across_four_exports(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-four65-') as folder:
            for client, mode in CASES:
                with self.subTest(client=client, mode=mode):
                    base = Path(folder) / (client + '-' + mode)
                    base.mkdir()
                    config, chat, originals = source_fixture(base, client, mode)
                    session = DesktopSession(base / 'profile', client=client, conversation_mode=mode)
                    session.open_source(config)
                    prepared = session.prepare_review(chat, copy_media=True, load_view=False)
                    root = Path(prepared['view']['viewRoot'])
                    rows = json.loads((root / 'messages.json').read_text(encoding='utf8'))
                    assert_quality(self, root, rows, client, originals)
                    assert_new_content(self, root, rows, client)
                    if mode == 'direct':
                        formal = session.confirm_export(prepared['review']['reviewId'],
                            {key: True for key in prepared['reviewChecks']})
                        request = ProfileStore(base / 'portraits').prepare(formal['output'])
                        material = '\n'.join(Path(path).read_text(encoding='utf8') for path in request['materialFiles'])
                        for keyword in KEYWORDS:
                            self.assertIn(keyword, material)
                        self.assertIn('隐藏幻灯片', material)


if __name__ == '__main__':
    unittest.main()
