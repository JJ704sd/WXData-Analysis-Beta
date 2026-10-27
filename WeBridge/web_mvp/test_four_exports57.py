"""Four entry-point fixtures for text encoding, document structure and revoke notices."""
from contextlib import closing
import codecs
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from offline_desktop import DesktopSession
from private_profiles import ProfileStore
from test_four_exports56 import CASES, source_fixture as previous_fixture, assert_quality
from test_four_exports56 import assert_new_content as assert_previous_content
from test_database_adapter import create_shard
from test_document_structure57 import docx_file, xlsx_file
from test_wecom_archive import pb

CSV_NAME = 'utf16-delivery.csv'
DOCX_NAME = 'linked-merged-contract.docx'
XLSX_NAME = 'linked-contract.xlsx'
CONTRACT_URL = 'https://example.test/contracts/42'
MERGED_TITLE = '合并标题'
REVOKE_SERVER_ID = '9753102468'


def _attachment_row(client, path, mid):
    raw = path.read_bytes()
    digest = hashlib.md5(raw).hexdigest()
    if client == 'wecom':
        return pb(2, path.name) + pb(4, len(raw)) + pb(10, digest)
    return ('<msg><appmsg><title>' + path.name + '</title><type>6</type><appattach><totallen>'
            + str(len(raw)) + '</totallen><md5>' + digest + '</md5></appattach></appmsg></msg>')


def _add_attachments(source, client, chat, paths):
    if client == 'wecom':
        with closing(sqlite3.connect(source / 'message.db')) as db, db:
            for offset, path in paths:
                for mid in (offset, offset + 1):
                    db.execute('INSERT INTO message_table VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                               (mid, mid + 10, mid, 11, chat, 102, 1700000000 + mid, 0,
                                _attachment_row(client, path, mid), b'', b''))
    else:
        rows = []
        for offset, path in paths:
            for mid in (offset, offset + 1):
                rows.append({'local': mid, 'type': 49, 'text': _attachment_row(client, path, mid)})
        create_shard(source, relative='message/message_4.db', group=chat, rows=rows)


def source_fixture(base, client, mode, count=2):
    config, chat, originals = previous_fixture(base, client, mode, count=count)
    original_root = Path(config['supplementRoot'])

    csv = original_root / CSV_NAME
    csv.write_bytes(codecs.BOM_UTF16_LE + '客户,交付时间\n示例,周五下午 🚚'.encode('utf-16-le'))
    docx = original_root / DOCX_NAME
    docx_file(docx)
    xlsx = original_root / XLSX_NAME
    xlsx_file(xlsx)
    paths = ((60, csv), (70, docx), (80, xlsx))
    for _, path in paths:
        raw = path.read_bytes()
        originals[hashlib.sha256(raw).hexdigest()] = raw
    source = Path(config['sourceRoot'])
    _add_attachments(source, client, chat, paths)

    if client == 'wechat':
        revoke = (
            '<sysmsg type="revokemsg"><revokemsg>'
            '<session>synthetic-revoke-session</session><newmsgid>' + REVOKE_SERVER_ID + '</newmsgid>'
            '<replacemsg><![CDATA[示例撤回了一条消息]]></replacemsg>'
            '</revokemsg></sysmsg>'
        )
        create_shard(source, relative='message/message_5.db', group=chat, rows=[
            {'local': 90, 'server': 9753102469, 'type': 10000, 'time': 1700000300, 'text': revoke}])
    return config, chat, originals


def assert_new_content(test, root, rows, client=None):
    assert_previous_content(test, root, rows)
    corpus = (root / 'analysis/corpus.jsonl').read_text(encoding='utf8')
    for filename in (CSV_NAME, DOCX_NAME, XLSX_NAME):
        matches = [row for row in rows if row.get('text') == filename]
        test.assertEqual(len(matches), 2)
        test.assertTrue(all(row.get('attachmentContent') for row in matches))

    csv_text = next(row['attachmentContent']['text'] for row in rows if row.get('text') == CSV_NAME)
    test.assertIn('客户,交付时间', csv_text)
    test.assertIn('示例,周五下午 🚚', csv_text)
    test.assertIn('客户,交付时间', corpus)
    test.assertIn('示例,周五下午 🚚', corpus)

    docx_text = next(row['attachmentContent']['text'] for row in rows if row.get('text') == DOCX_NAME)
    test.assertIn(CONTRACT_URL, docx_text)
    test.assertIn(MERGED_TITLE + '（合并继承）', docx_text)
    test.assertNotIn('unused.test/canary', docx_text)
    test.assertIn(CONTRACT_URL, corpus)
    test.assertIn(MERGED_TITLE + '（合并继承）', corpus)

    xlsx_text = next(row['attachmentContent']['text'] for row in rows if row.get('text') == XLSX_NAME)
    test.assertIn('B2=合同', xlsx_text)
    test.assertIn(CONTRACT_URL, xlsx_text)
    test.assertIn('C3=内部', xlsx_text)
    test.assertIn('Sheet2!A1', xlsx_text)
    test.assertNotIn('unused.test/canary', xlsx_text)
    test.assertIn(CONTRACT_URL, corpus)

    revokes = [row for row in rows if row.get('kind') == 'revoke']
    if client == 'wechat':
        test.assertEqual(len(revokes), 1)
        test.assertEqual(revokes[0]['text'], '示例撤回了一条消息')
        test.assertEqual(revokes[0]['revokedServerId'], REVOKE_SERVER_ID)
        test.assertNotIn(REVOKE_SERVER_ID, revokes[0]['text'])
        test.assertIn('示例撤回了一条消息', corpus)
    else:
        test.assertFalse(revokes)


class FourExports57Tests(unittest.TestCase):
    def test_new_content_and_old_failures_across_four_exports(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-four57-') as folder:
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
                    if mode=='direct':
                        formal=session.confirm_export(prepared['review']['reviewId'],
                            {key:True for key in prepared['reviewChecks']})
                        request=ProfileStore(base/'portraits').prepare(formal['output'])
                        self.assertEqual(request['status'],'ready')


if __name__ == '__main__':
    unittest.main()
