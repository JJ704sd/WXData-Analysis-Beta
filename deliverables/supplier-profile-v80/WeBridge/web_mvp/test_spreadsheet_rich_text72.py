"""Spreadsheet pronunciation guides must not change stored cell values."""
import hashlib
from pathlib import Path
import tempfile
import unittest
import zipfile

from offline_documents import AttachmentExtractor, extract_document


S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PR = 'http://schemas.openxmlformats.org/package/2006/relationships'
EXPECTED = 'A1=订单 编号\tB1=客户名称\tC1=普通　文本\tD1=Reference 007'


def workbook(path):
    parts = {
        'xl/workbook.xml': f'<workbook xmlns="{S}" xmlns:r="{R}"><sheets><sheet name="业务资料" sheetId="1" r:id="sheet"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels': f'<Relationships xmlns="{PR}"><Relationship Id="sheet" Type="{R}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        'xl/sharedStrings.xml': f'''<sst xmlns="{S}">
          <si><r><t xml:space="preserve">订单 </t></r><r><t>编号</t></r>
            <rPh sb="0" eb="2"><t>SHARED_PRONUNCIATION</t></rPh></si>
          <si><t>普通　文本</t><rPh sb="0" eb="2"><t>PLAIN_PRONUNCIATION</t></rPh></si>
        </sst>''',
        'xl/worksheets/sheet1.xml': f'''<worksheet xmlns="{S}"><sheetData><row r="1">
          <c r="A1" t="s"><v>0</v></c>
          <c r="B1" t="inlineStr"><is><r><t>客户</t></r><r><t>名称</t></r>
            <rPh sb="0" eb="2"><t>INLINE_PRONUNCIATION</t></rPh></is></c>
          <c r="C1" t="s"><v>1</v></c>
          <c r="D1" t="inlineStr"><is><t xml:space="preserve">Reference 007</t></is></c>
          <c r="E1" t="inlineStr"><is/></c>
        </row></sheetData></worksheet>''',
    }
    with zipfile.ZipFile(path, 'w') as archive:
        for name, value in parts.items():
            archive.writestr(name, value)
    return path


class SpreadsheetRichText72Tests(unittest.TestCase):
    def test_shared_and_inline_strings_preserve_base_text_and_run_order(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-rich-text72-') as folder:
            result = extract_document(workbook(Path(folder) / 'business.xlsx'), '.xlsx')
        self.assertEqual(result['extractionStatus'], 'extracted')
        self.assertEqual(result['sections'], [{'location': '工作表 业务资料 行 1', 'text': EXPECTED}])
        self.assertNotIn('PRONUNCIATION', result['text'])

    def test_attachment_worker_keeps_clean_text_source_and_original_bytes(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-rich-attachment72-') as folder:
            root = Path(folder).resolve()
            (root / 'assets').mkdir()
            path = workbook(root / 'assets/business.xlsx')
            original = path.read_bytes()
            row = {'id': 'synthetic-message', 'chatId': 'synthetic-chat', 'chatName': '合成会话',
                   'kind': 'file', 'provenance': {'file': 'synthetic.db', 'table': 'synthetic'},
                   'asset': {'status': 'ready', 'filename': 'business.xlsx',
                             'path': 'assets/business.xlsx', 'size': len(original),
                             'digest': hashlib.sha256(original).hexdigest()}}
            AttachmentExtractor().attach(row, root, True)
            content = row['attachmentContent']
            self.assertEqual(content['extractionStatus'], 'extracted')
            self.assertEqual(content['sections'][0]['text'], EXPECTED)
            self.assertEqual(content['source']['rootMessageId'], row['id'])
            self.assertEqual(content['source']['provenance'], row['provenance'])
            self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
