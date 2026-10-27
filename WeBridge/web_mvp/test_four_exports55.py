"""Four entry-point fixtures combining historical failures with useful new content."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from offline_desktop import DesktopSession
from test_four_exports53 import CASES, source_fixture as previous_fixture, assert_quality
from test_database_adapter import create_shard
from test_offline_documents import office_file
from test_wecom_archive import pb

TAIL = 'FINAL_DELIVERY_CLAUSE'


def source_fixture(base, client, mode, count=2):
    config, chat, originals = previous_fixture(base, client, mode, count=count)
    original = Path(config['supplementRoot'])/'controlled-contract.docx'
    office_file(original, {'word/document.xml':
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        '<w:sdt><w:sdtContent><w:p><w:r><w:t>' + '合同正文' * 1000 + TAIL +
        '</w:t></w:r></w:p></w:sdtContent></w:sdt></w:body></w:document>'})
    raw = original.read_bytes()
    originals[hashlib.sha256(raw).hexdigest()] = raw
    digest = hashlib.md5(raw).hexdigest()
    source = Path(config['sourceRoot'])
    url_text = '查看 https://example.test/条款。请核对'
    if client == 'wecom':
        with closing(sqlite3.connect(source/'message.db')) as db, db:
            for mid in (30, 31):
                body = pb(2, original.name) + pb(4, len(raw)) + pb(10, digest)
                db.execute('INSERT INTO message_table VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                           (mid, mid+10, mid, 11, chat, 102, 1700000000+mid, 0, body, b'', b''))
            body = pb(1, pb(1, 0) + pb(2, pb(1, url_text)))
            db.execute('INSERT INTO message_table VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (32, 42, 32, 11, chat, 2, 1700000032, 0, body, b'', b''))
    else:
        xml = '<msg><appmsg><title>' + original.name + '</title><type>6</type><appattach><totallen>' + str(len(raw)) + '</totallen><md5>' + digest + '</md5></appattach></appmsg></msg>'
        create_shard(source, relative='message/message_2.db', group=chat, rows=[
            {'local': 30, 'type': 49, 'text': xml}, {'local': 31, 'type': 49, 'text': xml},
            {'local': 32, 'text': url_text}])
    return config, chat, originals


def assert_new_content(test, root, rows):
    docs = [r['attachmentContent'] for r in rows if r.get('text') == 'controlled-contract.docx']
    test.assertEqual(len(docs), 2)
    test.assertTrue(all(TAIL in d['text'] for d in docs))
    link = next(r for r in rows if r.get('text', '').startswith('查看 https://'))
    test.assertEqual(link['links'], ['https://example.test/条款'])
    test.assertIn(TAIL, (root/'analysis/corpus.jsonl').read_text(encoding='utf8'))


class FourExports55Tests(unittest.TestCase):
    def test_new_content_and_old_failures_across_four_exports(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-four55-') as folder:
            for client, mode in CASES:
                with self.subTest(client=client, mode=mode):
                    base = Path(folder)/(client+'-'+mode)
                    base.mkdir()
                    config, chat, originals = source_fixture(base, client, mode)
                    session = DesktopSession(base/'profile', client=client, conversation_mode=mode)
                    session.open_source(config)
                    prepared = session.prepare_review(chat, copy_media=True, load_view=False)
                    output = Path(prepared['view']['viewRoot'])
                    rows = json.loads((output/'messages.json').read_text(encoding='utf8'))
                    assert_quality(self, output, rows, client, originals)
                    assert_new_content(self, output, rows)


if __name__ == '__main__':
    unittest.main()
