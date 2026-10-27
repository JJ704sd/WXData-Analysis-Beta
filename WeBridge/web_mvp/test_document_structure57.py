import tempfile
import unittest
import zipfile
from pathlib import Path

from web_mvp.offline_documents import extract_document
from test_spreadsheet_semantics56 import _xlsx


W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PR = 'http://schemas.openxmlformats.org/package/2006/relationships'
S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'


def _docx_xml():
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="{W}" xmlns:r="{R}">
  <w:body>
    <w:p>
      <w:r><w:t>合同：</w:t></w:r>
      <w:hyperlink r:id="rIdLink" w:tooltip="合同链接"><w:r><w:t>在线合同</w:t></w:r></w:hyperlink>
      <w:r><w:t>；</w:t></w:r>
      <w:hyperlink w:anchor="付款条款"><w:r><w:t>内部条款</w:t></w:r></w:hyperlink>
    </w:p>
    <w:tbl>
      <w:tr>
        <w:trPr><w:gridBefore w:val="1"/></w:trPr>
        <w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge w:val="restart"/></w:tcPr><w:p><w:r><w:t>合并标题</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>第一行</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:trPr><w:gridBefore w:val="1"/></w:trPr>
        <w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge/></w:tcPr><w:p/></w:tc>
        <w:tc><w:p><w:r><w:t>第二行</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:trPr><w:gridBefore w:val="1"/></w:trPr>
        <w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge/></w:tcPr><w:p/></w:tc>
        <w:tc><w:p><w:r><w:t>第三行</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:trPr><w:gridBefore w:val="1"/></w:trPr>
        <w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr><w:p><w:r><w:t>合并已结束</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>第四行</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:trPr><w:gridBefore w:val="1"/></w:trPr>
        <w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr><w:p><w:r><w:t>结束后独立</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>第五行</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
    <w:sectPr><w:headerReference w:type="default" r:id="rIdHeaderPart"/></w:sectPr>
  </w:body>
</w:document>'''


def docx_file(path):
    """Write a DOCX fixture containing used/unused links and a merged table."""
    files = {
        'word/document.xml': _docx_xml(),
        'word/_rels/document.xml.rels': f'''<Relationships xmlns="{PR}">
          <Relationship Id="rIdHeaderPart" Type="{R}/header" Target="header1.xml"/>
          <Relationship Id="rIdLink" Type="{R}/hyperlink" Target="https://example.test/contracts/42" TargetMode="External"/>
          <Relationship Id="rIdUnused" Type="{R}/hyperlink" Target="https://unused.test/canary" TargetMode="External"/>
        </Relationships>''',
        'word/header1.xml': f'''<w:hdr xmlns:w="{W}" xmlns:r="{R}">
          <w:p><w:hyperlink r:id="rIdHeader"><w:r><w:t>页眉说明</w:t></w:r></w:hyperlink></w:p>
        </w:hdr>''',
        'word/_rels/header1.xml.rels': f'''<Relationships xmlns="{PR}">
          <Relationship Id="rIdHeader" Type="{R}/hyperlink" Target="https://example.test/header" TargetMode="External"/>
        </Relationships>''',
        '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
    }
    with zipfile.ZipFile(path, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


def xlsx_file(path):
    """Write an XLSX fixture containing used/unused external and internal links."""
    workbook = f'''<workbook xmlns="{S}" xmlns:r="{R}">
      <sheets><sheet name="Sheet1" sheetId="1" r:id="rIdSheet"/></sheets>
    </workbook>'''
    sheet = f'''<worksheet xmlns="{S}" xmlns:r="{R}">
      <sheetData>
        <row r="1"><c r="A1" t="inlineStr"><is><t>名称</t></is></c></row>
        <row r="2"><c r="B2" t="inlineStr"><is><t>合同</t></is></c></row>
        <row r="3"><c r="C3" t="inlineStr"><is><t>内部</t></is></c></row>
        <row r="4"><c r="D4" t="inlineStr"><is><t>D范围</t></is></c><c r="E4" t="inlineStr"><is><t>E范围</t></is></c></row>
      </sheetData>
      <hyperlinks>
        <hyperlink ref="B2" r:id="rIdLink"/>
        <hyperlink ref="C3" location="Sheet2!A1" display="内部位置"/>
        <hyperlink ref="D4:E4" r:id="rIdRange"/>
      </hyperlinks>
    </worksheet>'''
    files = {
        'xl/workbook.xml': workbook,
        'xl/worksheets/sheet1.xml': sheet,
        'xl/_rels/workbook.xml.rels': f'''<Relationships xmlns="{PR}">
          <Relationship Id="rIdSheet" Type="{R}/worksheet" Target="worksheets/sheet1.xml"/>
        </Relationships>''',
        'xl/worksheets/_rels/sheet1.xml.rels': f'''<Relationships xmlns="{PR}">
          <Relationship Id="rIdLink" Type="{R}/hyperlink" Target="https://example.test/contracts/42" TargetMode="External"/>
          <Relationship Id="rIdRange" Type="{R}/hyperlink" Target="https://example.test/range" TargetMode="External"/>
          <Relationship Id="rIdUnused" Type="{R}/hyperlink" Target="https://unused.test/canary" TargetMode="External"/>
        </Relationships>''',
        'xl/styles.xml': f'<styleSheet xmlns="{S}"><cellXfs count="1"><xf numFmtId="0"/></cellXfs></styleSheet>',
    }
    with zipfile.ZipFile(path, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


class DocumentStructure57Tests(unittest.TestCase):
    def test_cell_without_style_uses_workbook_default_format(self):
        styles=f'<styleSheet xmlns="{S}"><cellXfs count="1"><xf numFmtId="10"/></cellXfs></styleSheet>'
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'default-style.xlsx'
            path.write_bytes(_xlsx(styles=styles,values=[('A1','0.125',None,None)]))
            text=extract_document(path,'.xlsx')['text']
        self.assertIn('A1=12.50%',text)

    def test_changed_merge_span_does_not_inherit_an_unmatched_cell(self):
        with tempfile.TemporaryDirectory() as folder:
            path=docx_file(Path(folder)/'mismatched.docx')
            with zipfile.ZipFile(path) as archive:
                parts={name:archive.read(name) for name in archive.namelist()}
            parts['word/document.xml']=parts['word/document.xml'].replace(
                b'<w:gridSpan w:val="2"/><w:vMerge/>',
                b'<w:gridSpan w:val="1"/><w:vMerge/>',1)
            with zipfile.ZipFile(path,'w') as archive:
                for name,data in parts.items():archive.writestr(name,data)
            result=extract_document(path,'.docx')
        second=next(s['text'] for s in result['sections'] if s['location']=='表格 2 行 2')
        self.assertNotIn('合并标题',second)
        self.assertIn('合并来源未匹配',second)

    def test_docx_used_links_and_merge_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            path = docx_file(Path(folder) / 'contract.docx')
            text = extract_document(path, '.docx')['text']
        self.assertIn('在线合同', text)
        self.assertIn('https://example.test/contracts/42', text)
        self.assertIn('内部条款', text)
        self.assertIn('#付款条款', text)
        self.assertIn('页眉说明', text)
        self.assertIn('https://example.test/header', text)
        self.assertNotIn('unused.test/canary', text)
        self.assertIn('合并标题（合并继承）', text)
        self.assertIn('结束后独立', text)
        self.assertNotIn('结束后独立（合并继承）', text)
        self.assertIn('[前置空列 1]\t合并标题\t\t第一行', text)

    def test_xlsx_used_links_have_cell_refs(self):
        with tempfile.TemporaryDirectory() as folder:
            path = xlsx_file(Path(folder) / 'contract.xlsx')
            text = extract_document(path, '.xlsx')['text']
        self.assertIn('B2=合同', text)
        self.assertIn('https://example.test/contracts/42', text)
        self.assertIn('C3=内部', text)
        self.assertIn('Sheet2!A1', text)
        self.assertIn('C3', text)
        self.assertIn('D4=D范围', text)
        self.assertIn('E4=E范围', text)
        self.assertIn('https://example.test/range', text)
        self.assertNotIn('unused.test/canary', text)

    def test_hyperlink_with_no_cell_value_still_has_ref_and_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            path=xlsx_file(Path(folder)/'empty-link.xlsx')
            with zipfile.ZipFile(path) as archive:
                parts={name:archive.read(name) for name in archive.namelist()}
            parts['xl/worksheets/sheet1.xml']=parts['xl/worksheets/sheet1.xml'].replace(
                b'</hyperlinks>',b'<hyperlink ref="F9" location="Sheet2!B3" display="Target"/></hyperlinks>')
            with zipfile.ZipFile(path,'w') as archive:
                for name,data in parts.items():archive.writestr(name,data)
            text=extract_document(path,'.xlsx')['text']
        self.assertIn('F9',text)
        self.assertIn('Sheet2!B3',text)


if __name__ == '__main__':
    unittest.main()
