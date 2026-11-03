"""DOCX content-control coverage at the public attachment extraction seam."""
import hashlib
from pathlib import Path
import tempfile
import unittest
import zipfile

from offline_documents import AttachmentExtractor


W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def office_file(path, document_xml):
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('word/document.xml', document_xml)


def text(value):
    return f'<w:p><w:r><w:t>{value}</w:t></w:r></w:p>'


class DocumentCoverage55Tests(unittest.TestCase):
    def test_attach_extracts_content_controls_in_block_order_without_deleted_revision(self):
        document = f'''<w:document xmlns:w="{W}"><w:body>
            {text('正文前')}
            <w:sdt><w:sdtPr/><w:sdtContent>
                {text('控件段落')}
                <w:tbl><w:tr><w:tc>{text('控件表格一')}</w:tc><w:tc>{text('控件表格二')}</w:tc></w:tr></w:tbl>
            </w:sdtContent></w:sdt>
            <w:del><w:p><w:r><w:t>已删除正文</w:t></w:r></w:p></w:del>
            <w:p><w:r><w:t>当前</w:t></w:r><w:del><w:r><w:t>已删除行内</w:t></w:r></w:del></w:p>
            <w:sectPr/>
        </w:body></w:document>'''
        with tempfile.TemporaryDirectory(prefix='synthetic-document55-') as folder:
            root = Path(folder)
            (root / 'assets').mkdir()
            path = root / 'assets' / 'contract.docx'
            office_file(path, document)
            raw = path.read_bytes()
            row = {'id': 'synthetic-message', 'chatId': 'synthetic-chat', 'chatName': '合成私聊',
                   'kind': 'file', 'asset': {'status': 'ready', 'filename': 'contract.docx',
                   'path': 'assets/contract.docx', 'digest': hashlib.sha256(raw).hexdigest(),
                   'size': len(raw)}}

            AttachmentExtractor().attach(row, root, True)

            result = row['attachmentContent']
            self.assertEqual(result['extractionStatus'], 'extracted')
            self.assertEqual(
                [section['text'] for section in result['sections']],
                ['正文前', '控件段落', '控件表格一\t控件表格二', '当前'],
            )
            self.assertLess(result['text'].index('控件段落'), result['text'].index('控件表格一'))
            self.assertNotIn('已删除正文', result['text'])
            self.assertNotIn('已删除行内', result['text'])


if __name__ == '__main__':
    unittest.main()
