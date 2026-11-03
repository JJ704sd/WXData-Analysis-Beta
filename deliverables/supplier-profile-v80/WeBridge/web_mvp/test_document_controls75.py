"""Preserve attachment control characters without joining surrounding text."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from offline_daily import build_daily_report
from offline_desktop import DesktopSession
from offline_documents import extract_document
from test_database_adapter import create_shard
from test_four_exports53 import CASES
from test_group_unicode52 import unicode_pdf
from test_reading_delivery74 import source74
from test_wecom_archive import pb
from test_document_structure57 import docx_file


CONTROL_BYTES = '金额10\x000元\r下一页\x0c报价2\x0b元\t中文💬\r\n'.encode('utf-8')
CONTROL_TEXT = '[文件正文]\n金额10\\u00000元\n下一页\n报价2\n元\t中文💬'


def source75(base, client, mode):
    """Add a control-character TXT attachment to the v74 four-flow fixture."""
    config, chat, originals, payload, picture, identifier = source74(base, client, mode)
    source = Path(config['sourceRoot'])
    root = Path(config['supplementRoot'])
    path = root / 'controls75.txt'
    raw = CONTROL_BYTES
    path.write_bytes(raw)
    originals = dict(originals)
    originals[path.name] = raw
    digest = hashlib.md5(raw).hexdigest()
    number = 751
    if client == 'wecom':
        body = pb(2, path.name) + pb(4, len(raw)) + pb(10, digest)
        with closing(sqlite3.connect(source / 'message.db')) as db, db:
            db.execute('INSERT INTO message_table VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (number, number + 10, number, 11, chat, 102, 1700000000 + number,
                        0, body, b'', b''))
    else:
        body = ('<msg><appmsg><title>' + path.name + '</title><type>6</type><appattach>'
                '<totallen>' + str(len(raw)) + '</totallen><md5>' + digest +
                '</md5></appattach></appmsg></msg>')
        create_shard(source, relative='message/message_75.db', group=chat,
                     rows=[{'local': number, 'type': 49, 'text': body}])
    return config, chat, originals, payload, picture, identifier


class DocumentControls75Tests(unittest.TestCase):
    def test_control_characters_are_escaped_and_layout_breaks_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'controls.txt'
            path.write_bytes(CONTROL_BYTES)
            result = extract_document(path, '.txt')
        self.assertEqual(result['extractionStatus'], 'partial')
        self.assertTrue(result['requiresReview'])
        self.assertEqual(result['text'], CONTROL_TEXT)
        self.assertIn('下一页\n报价2\n元\t中文💬', result['text'])
        self.assertEqual(result['notices'].count(
            '正文含异常 Unicode 代理字符或控制字符，已保留为 \\uXXXX 转义标记；请核对原件。'), 1)

    def test_control_only_text_is_not_reported_as_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'only-controls.txt'
            path.write_bytes(b'\x00\x01\x08\x0e\x1f')
            result = extract_document(path, '.txt')
        self.assertNotEqual(result['extractionStatus'], 'no_text')
        self.assertEqual(result['text'], '[文件正文]\n' + r'\u0000\u0001\u0008\u000e\u001f')
        self.assertTrue(result['requiresReview'])

    def test_normal_text_and_existing_office_pdf_extractors_remain_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            txt = root / 'normal.txt'
            txt.write_text('中文💬\t保留\r\n下一行\x0b下一段\x0c下一页', encoding='utf-8')
            result = extract_document(txt, '.txt')
            self.assertEqual(result['extractionStatus'], 'extracted')
            self.assertIn('中文💬\t保留', result['text'])
            self.assertIn('下一行\n下一段\n下一页', result['text'])
            self.assertFalse(result.get('requiresReview'))
            docx = docx_file(root / 'normal.docx')
            self.assertIn('在线合同', extract_document(docx, '.docx')['text'])
            pdf = root / 'normal.pdf'
            unicode_pdf(pdf, broken=False)
            self.assertEqual(extract_document(pdf, '.pdf')['extractionStatus'], 'extracted')

    def test_four_flows_keep_control_attachment_and_daily_gap(self):
        for client, mode in CASES:
            with self.subTest(client=client, mode=mode), tempfile.TemporaryDirectory(
                    prefix='synthetic-controls75-') as folder:
                base = Path(folder)
                config, chat, originals, payload, _picture, _identifier = source75(
                    base, client, mode)
                session = DesktopSession(base / 'profile', client=client,
                                         conversation_mode=mode)
                session.open_source(config)
                prepared = session.prepare_review(chat, copy_media=True, load_view=False)
                before = (Path(prepared['view']['viewRoot']) / 'messages.json').read_bytes()
                result = session.confirm_export(
                    prepared['review']['reviewId'],
                    {key: True for key in prepared['reviewChecks']},
                    'synthetic control review', chat=chat)
                output = Path(result['output'])
                self.assertEqual((output / 'messages.json').read_bytes(), before)
                rows = json.loads(before)
                row = next(r for r in rows if r.get('text') == 'controls75.txt')
                document = row['attachmentContent']
                self.assertEqual(document['extractionStatus'], 'partial')
                self.assertTrue(document['requiresReview'])
                self.assertEqual(document['text'], CONTROL_TEXT)
                self.assertEqual((output / row['asset']['path']).read_bytes(), originals['controls75.txt'])
                for name in ('messages.md', '业务阅读.txt'):
                    self.assertIn(document['text'],
                                  (output / name).read_text(encoding='utf-8-sig'), name)
                corpus = [json.loads(line) for line in
                          (output / 'analysis/corpus.jsonl').read_text(encoding='utf-8').splitlines()]
                self.assertTrue(any(item.get('attachment', {}).get('text') == CONTROL_TEXT
                                    for item in corpus))
                daily = build_daily_report([output], client=client,
                                           day=row['time'][:10], output=base / 'daily')
                report = json.loads(Path(daily['jsonFile']).read_text(encoding='utf-8'))
                self.assertTrue(report['contentGaps'])


if __name__ == '__main__':
    unittest.main()
