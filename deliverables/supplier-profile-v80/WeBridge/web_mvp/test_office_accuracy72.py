"""Verify corrected Office text survives each real synthetic review/export flow."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from offline_desktop import DesktopSession
from test_database_adapter import create_shard
from test_four_exports53 import CASES, source_fixture
from test_spreadsheet_rich_text72 import workbook, EXPECTED
from test_wecom_archive import pb
from test_word_fields72 import _docx


LINK = 'https://example.test/policy72'


def office_source(base, client, mode):
    config, chat, _ = source_fixture(base, client, mode, count=0)
    root = Path(config['supplementRoot'])
    paths = [workbook(root / 'business72.xlsx'), _docx(root / 'policy72.docx',
        '<w:p><w:fldSimple w:instr=\' HYPERLINK "' + LINK + '" \'>'
        '<w:r><w:t>政策条款</w:t></w:r></w:fldSimple></w:p>')]
    source = Path(config['sourceRoot'])
    rows = []
    originals = {}
    for number, path in enumerate(paths, 301):
        raw = path.read_bytes()
        originals[path.name] = raw
        digest = hashlib.md5(raw).hexdigest()
        if client == 'wecom':
            body = pb(2, path.name) + pb(4, len(raw)) + pb(10, digest)
            with closing(sqlite3.connect(source / 'message.db')) as db, db:
                db.execute('INSERT INTO message_table VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (number, number + 10, number, 11, chat, 102, 1700000000 + number, 0, body, b'', b''))
        else:
            body = ('<msg><appmsg><title>' + path.name + '</title><type>6</type><appattach>'
                    '<totallen>' + str(len(raw)) + '</totallen><md5>' + digest + '</md5>'
                    '</appattach></appmsg></msg>')
            rows.append({'local': number, 'type': 49, 'text': body})
    if rows:
        create_shard(source, relative='message/message_72.db', group=chat, rows=rows)
    return config, chat, originals


class OfficeAccuracy72Tests(unittest.TestCase):
    def test_four_clients_and_modes_keep_office_values_links_and_provenance(self):
        for client, mode in CASES:
            with self.subTest(client=client, mode=mode), tempfile.TemporaryDirectory(prefix='synthetic-office72-') as folder:
                base = Path(folder).resolve()
                config, chat, originals = office_source(base, client, mode)
                session = DesktopSession(base / 'profile', client=client, conversation_mode=mode)
                session.open_source(config)
                parsed = session.prepare_review(chat, copy_media=True, load_view=False)
                preview = Path(parsed['view']['viewRoot'])
                before = (preview / 'messages.json').read_bytes()
                result = session.confirm_export(parsed['review']['reviewId'],
                    {key: True for key in parsed['reviewChecks']}, 'synthetic Office fixture', chat=chat)
                self.assertEqual(result['humanReview']['status'], 'confirmed')
                self.assertEqual(set(result['exportFiles']), {'json', 'jsonl', 'md'})
                output = Path(result['output'])
                self.assertEqual((output / 'messages.json').read_bytes(), before)
                rows = json.loads(before)
                found = {}
                for row in rows:
                    content = row.get('attachmentContent') or {}
                    filename = content.get('filename')
                    if filename not in originals:
                        continue
                    found[filename] = content
                    self.assertEqual(content['extractionStatus'], 'extracted')
                    self.assertEqual(content['source']['rootMessageId'], row['id'])
                    self.assertEqual(content['source']['chatId'], chat)
                    self.assertTrue(content['source']['provenance'])
                    self.assertEqual((output / row['asset']['path']).read_bytes(), originals[filename])
                self.assertEqual(set(found), set(originals))
                self.assertEqual(found['business72.xlsx']['sections'][0]['text'], EXPECTED)
                self.assertNotIn('PRONUNCIATION', found['business72.xlsx']['text'])
                self.assertIn('政策条款 [链接: ' + LINK + ']', found['policy72.docx']['text'])
                markdown = (output / 'messages.md').read_text(encoding='utf-8')
                self.assertIn(LINK, markdown)
                self.assertNotIn('PRONUNCIATION', markdown)
                corpus = (output / 'analysis/corpus.jsonl').read_text(encoding='utf-8')
                self.assertIn(LINK, corpus)
                self.assertNotIn('PRONUNCIATION', corpus)


if __name__ == '__main__':
    unittest.main()
