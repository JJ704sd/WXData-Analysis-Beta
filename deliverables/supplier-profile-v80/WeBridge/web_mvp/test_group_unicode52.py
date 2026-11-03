"""Malformed PDF character maps must not discard a whole group export."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from offline_desktop import DesktopSession
from offline_documents import extract_document
from test_wecom_archive import fixture, pb
from test_database_adapter import create_metadata, create_shard, GROUP


def unicode_pdf(path, broken=True):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=300)
    cmap = DecodedStreamObject()
    cmap.set_data(('begincmap\n1 begincodespacerange\n<00> <FF>\nendcodespacerange\n'
                   '5 beginbfchar\n<01> <62A5>\n<02> <4EF7>\n<03> <' + ('D800' if broken else '003F') +
                   '>\n<04> <D83DDCAC>\n<05> <' + ('DC00' if broken else '0021') +
                   '>\nendbfchar\nendcmap').encode('ascii'))
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                             NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica'),
                             NameObject('/ToUnicode'): writer._add_object(cmap)})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({
        NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 10 200 Td <01022033372f4b4720032004200520656e64> Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(path)


def attach_pdf(source, client, mid=3):
    cache = source.parent / ('Cache/File' if client == 'wecom' else 'msg/file/2023-11')
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / 'synthetic-unicode.pdf'
    unicode_pdf(path)
    raw = path.read_bytes()
    md5 = hashlib.md5(raw).hexdigest()
    if client == 'wecom':
        body = pb(2, path.name) + pb(4, len(raw)) + pb(10, md5)
        with closing(sqlite3.connect(source / 'message.db')) as db, db:
            db.execute('INSERT INTO message_table VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (mid, mid + 10, mid, 11, 'R:synthetic', 102, 1700000000 + mid, 0, body, b'', b''))
            text = pb(1, pb(1, 0) + pb(2, pb(1, 'message after attachment')))
            db.execute('INSERT INTO message_table VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (mid + 1, mid + 11, mid + 1, 11, 'R:synthetic', 2, 1700000001 + mid, 0, text, b'', b''))
    else:
        body = '<msg><appmsg><title>' + path.name + '</title><type>6</type><appattach><totallen>' + str(len(raw)) + '</totallen><md5>' + md5 + '</md5></appattach></appmsg></msg>'
        create_shard(source, relative='message/message_1.db', rows=[
            {'local': mid, 'type': 49, 'text': body}, {'local': mid + 1, 'text': 'message after attachment'}])
    return raw


class GroupUnicode52Tests(unittest.TestCase):
    def test_malformed_pdf_survives_group_review_and_export(self):
        for client in ('wecom', 'wechat'):
            with self.subTest(client=client), tempfile.TemporaryDirectory() as folder:
                base = Path(folder)
                source = base / 'account' / ('Data' if client == 'wecom' else 'db_storage')
                source.parent.mkdir()
                if client == 'wecom':
                    fixture(source)
                    chat, count = 'R:synthetic', 4
                else:
                    source.mkdir()
                    create_metadata(source)
                    create_shard(source, rows=[{'text': 'before attachment'}])
                    chat, count = GROUP, 3
                original = attach_pdf(source, client)
                session = DesktopSession(base / 'profile', client=client)
                session.open_source({'sourceRoot': str(source)})
                prepared = session.prepare_review(chat, copy_media=True)
                exported = session.confirm_export(prepared['review']['reviewId'],
                                                  {key: True for key in prepared['reviewChecks']})
                root = Path(exported['output'])
                rows = json.loads((root / 'messages.json').read_text(encoding='utf8'))
                self.assertEqual(len(rows), count)
                self.assertTrue(any(r['text'] == 'message after attachment' for r in rows))
                attachment = next(r for r in rows if r.get('attachmentContent'))
                document = attachment['attachmentContent']
                self.assertEqual(document['extractionStatus'], 'partial')
                self.assertTrue(document['requiresReview'])
                self.assertIn('报价 37/KG \\ud800 💬 \\udc00 end', document['text'])
                self.assertEqual((root / attachment['asset']['path']).read_bytes(), original)
                for name in ('messages.jsonl', 'messages.md', '业务阅读.txt', 'analysis/corpus.jsonl'):
                    text = (root / name).read_text(encoding='utf-8-sig')
                    self.assertIn('37/KG', text)
                    self.assertIn('💬', text)
                    self.assertIn('ud800', text)
                self.assertEqual([json.loads(line) for line in (root / 'messages.jsonl').read_text(encoding='utf8').splitlines()], rows)

    def test_valid_pdf_chinese_and_emoji_remain_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'valid.pdf'
            unicode_pdf(path, broken=False)
            result = extract_document(path, '.pdf')
            self.assertEqual(result['extractionStatus'], 'extracted')
            self.assertIn('报价 37/KG ? 💬 ! end', result['text'])
            self.assertFalse(result.get('requiresReview'))


if __name__ == '__main__':
    unittest.main()
