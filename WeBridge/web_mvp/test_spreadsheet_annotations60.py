"""TDD fixtures and unit tests for XLSX shared formulas and classic comments."""
import io
import posixpath
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import offline_documents
from offline_documents import TextResult, extract_document
from offline_spreadsheet_annotations import (
    extract_sheet_comments,
    formula_annotation,
    shared_formulas,
)


S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PR = 'http://schemas.openxmlformats.org/package/2006/relationships'


def _xml(data):
    return ET.fromstring(data)


def _read_xml(z, name):
    if name not in z.namelist():
        raise KeyError(name)
    return ET.fromstring(z.read(name))


def _resolve_target(base, target):
    return posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join(base, target))


def xlsx_file(path):
    """Write a real OOXML workbook used by the four-entry integration fixture."""
    workbook = f'''<workbook xmlns="{S}" xmlns:r="{R}">
      <sheets><sheet name="Orders" sheetId="1" r:id="rId1"/><sheet name="Second" sheetId="2" r:id="rId2"/></sheets>
    </workbook>'''
    sheet1 = f'''<worksheet xmlns="{S}" xmlns:r="{R}"><sheetData>
      <row r="1"><c r="A1" t="inlineStr"><is><t>CURRENT_CELL_VALUE_MUST_NOT_BE_MIXED</t></is></c>
        <c r="B1"><f t="shared" si="0"/><v>14</v></c></row>
      <row r="2"><c r="B2"><f t="shared" si="0" ref="B1:B2">SHARED_FORMULA_NEEDLE+A1</f><v>21</v></c></row>
      <row r="3"><c r="C3"><f>SUM(A1:A2)</f><v>5</v></c></row>
      <row r="4"><c r="D4" t="b"><f t="shared" si="9"/><v></v></c></row>
      <row r="5"><c r="D5" t="b"><f t="shared" si="0"/><v></v></c></row>
    </sheetData><legacyDrawing r:id="rId1"/></worksheet>'''
    sheet2 = f'''<worksheet xmlns="{S}"><sheetData>
      <row r="1"><c r="A1"><f t="shared" si="0" ref="A1:A2">SHARED_FORMULA_NEEDLE+100</f><v>110</v></c></row>
      <row r="2"><c r="A2"><f t="shared" si="0"/><v>111</v></c></row>
    </sheetData></worksheet>'''
    comments = f'''<comments xmlns="{S}"><authors><author>Export Reviewer</author><author>Second Reviewer</author></authors>
      <commentList><comment ref="A1" authorId="0"><text><r><t>XLSX_COMMENT_NEEDLE</t></r><r><t> confirm customer</t></r><rPh sb="0" eb="2"><t>PHONETIC_SHOULD_NOT_APPEAR</t></rPh></text></comment>
      <comment ref="B2" authorId="8"><text><t>Unknown author note</t></text></comment></commentList></comments>'''
    workbook_rels = f'''<Relationships xmlns="{PR}">
      <Relationship Id="rId1" Type="{R}/worksheet" Target="worksheets/sheet1.xml"/>
      <Relationship Id="rId2" Type="{R}/worksheet" Target="worksheets/sheet2.xml"/>
    </Relationships>'''
    sheet_rels = f'''<Relationships xmlns="{PR}">
      <Relationship Id="rId1" Type="{R}/comments" Target="../comments1.xml"/>
      <Relationship Id="rId2" Type="{R}/threadedComment" Target="../threadedComments1.xml"/>
      <Relationship Id="rId3" Type="{R}/comments" Target="https://example.invalid/comments.xml" TargetMode="External"/>
    </Relationships>'''
    files = {
        'xl/workbook.xml': workbook,
        'xl/_rels/workbook.xml.rels': workbook_rels,
        'xl/worksheets/sheet1.xml': sheet1,
        'xl/worksheets/sheet2.xml': sheet2,
        'xl/worksheets/_rels/sheet1.xml.rels': sheet_rels,
        'xl/comments1.xml': comments,
        '[Content_Types].xml': f'<Types xmlns="{PR}"/>',
    }
    with zipfile.ZipFile(path, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


class SpreadsheetAnnotations60Tests(unittest.TestCase):
    def test_shared_formula_map_finds_anchor_after_follower_per_sheet(self):
        with TemporaryDirectory() as folder:
            path = xlsx_file(Path(folder) / 'annotations.xlsx')
            with zipfile.ZipFile(path) as z:
                first = _read_xml(z, 'xl/worksheets/sheet1.xml')
                second = _read_xml(z, 'xl/worksheets/sheet2.xml')
        self.assertEqual(shared_formulas(first), {
            '0': ('B2', 'B1:B2', 'SHARED_FORMULA_NEEDLE+A1'),
            '9': ('', '', ''),
        })
        self.assertEqual(shared_formulas(second), {
            '0': ('A1', 'A1:A2', 'SHARED_FORMULA_NEEDLE+100'),
        })

    def test_shared_formula_annotations_keep_cache_and_do_not_reconstruct_formula(self):
        with TemporaryDirectory() as folder:
            path = xlsx_file(Path(folder) / 'annotations.xlsx')
            with zipfile.ZipFile(path) as z:
                root = _read_xml(z, 'xl/worksheets/sheet1.xml')
        cells = {cell.get('r'): cell for cell in root.iter('{'+S+'}c')}
        shared = shared_formulas(root)
        follower = formula_annotation(cells['B1'], shared)
        self.assertIn('=SHARED_FORMULA_NEEDLE+A1', follower)
        self.assertIn('共享公式 si=0', follower)
        self.assertIn('锚点=B2', follower)
        self.assertIn('文件缓存=存在', follower)
        self.assertNotIn('锚点=B1', follower)
        self.assertIn('[公式未执行: =SUM(A1:A2)]', formula_annotation(cells['C3'], shared))
        missing = formula_annotation(cells['D4'], shared)
        self.assertIn('共享公式缺口', missing)
        self.assertIn('文件缓存=缺失', missing)
        self.assertNotIn('=]', missing)
        anchor = formula_annotation(cells['B2'], shared)
        self.assertIn('[公式未执行: =SHARED_FORMULA_NEEDLE+A1]', anchor)
        self.assertIn('范围=B1:B2', anchor)

        self.assertNotIn('[公式未执行: =SHARED_FORMULA_NEEDLE+A1]', follower)
        self.assertIn('锚点原式: =SHARED_FORMULA_NEEDLE+A1', follower)
        self.assertIn('范围=B1:B2', follower)
        self.assertIn('未展开相对引用', follower)
        self.assertIn('未重新计算', follower)

    def test_comments_keep_author_and_rich_text_without_current_value_or_phonetic(self):
        with TemporaryDirectory() as folder:
            path = xlsx_file(Path(folder) / 'annotations.xlsx')
            out = TextResult()
            with zipfile.ZipFile(path) as z:
                extract_sheet_comments(
                    z, 'xl/worksheets/sheet1.xml', 'Orders', out,
                    read_xml=_read_xml, resolve_target=_resolve_target,
                )
        result = out.result()
        self.assertIn('XLSX_COMMENT_NEEDLE confirm customer', result['text'])
        self.assertIn('作者：Export Reviewer', result['text'])
        self.assertIn('作者未记录（authorId=8）', result['text'])
        self.assertNotIn('PHONETIC_SHOULD_NOT_APPEAR', result['text'])
        self.assertNotIn('CURRENT_CELL_VALUE_MUST_NOT_BE_MIXED', result['text'])
        self.assertTrue(any('threadedComments' in notice for notice in result['notices']))

    def test_missing_comment_part_keeps_existing_sections_and_reports_notice(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as z:
            z.writestr('xl/worksheets/sheet1.xml', f'<worksheet xmlns="{S}"/>')
            z.writestr('xl/worksheets/_rels/sheet1.xml.rels', f'''<Relationships xmlns="{PR}">
              <Relationship Id="rId1" Type="{R}/comments" Target="../missing-comments.xml"/>
            </Relationships>''')
        out = TextResult()
        out.add('工作表 Orders 行 1', '正文已读取')
        with zipfile.ZipFile(data) as z:
            extract_sheet_comments(z, 'xl/worksheets/sheet1.xml', 'Orders', out,
                                    read_xml=_read_xml, resolve_target=_resolve_target)
        result = out.result()
        self.assertIn('正文已读取', result['text'])
        self.assertTrue(any('批注部件缺失' in notice for notice in result['notices']))

    def test_boolean_formula_without_cache_is_not_fabricated(self):
        root = _xml(f'''<worksheet xmlns="{S}"><sheetData><row r="1"><c r="A1" t="b">
          <f t="shared" si="0" ref="A1:A1">SHARED_FORMULA_NEEDLE</f><v/>
        </c></row></sheetData></worksheet>''')
        annotation = formula_annotation(next(root.iter('{'+S+'}c')), shared_formulas(root))
        self.assertIn('文件缓存=缺失', annotation)
        self.assertNotIn('FALSE', annotation)

    def test_empty_string_cache_is_distinguished_from_missing_cache(self):
        root = _xml(f'''<worksheet xmlns="{S}"><sheetData><row r="1">
          <c r="A1" t="str"><f t="shared" si="0" ref="A1:A3">SHARED_FORMULA_NEEDLE</f><v/></c>
          <c r="A2" t="b"><f t="shared" si="0"/><v/></c>
          <c r="A3" t="b"><f t="shared" si="0"/></c>
        </row></sheetData></worksheet>''')
        shared = shared_formulas(root)
        cells = {cell.get('r'): cell for cell in root.iter('{'+S+'}c')}
        self.assertIn('文件缓存=存在', formula_annotation(cells['A1'], shared))
        self.assertIn('文件缓存=缺失', formula_annotation(cells['A2'], shared))
        self.assertIn('文件缓存=缺失', formula_annotation(cells['A3'], shared))

    def test_extract_document_real_xlsx_keeps_shared_follower_and_boolean_cache_semantics(self):
        with TemporaryDirectory() as folder:
            result = extract_document(xlsx_file(Path(folder) / 'annotations.xlsx'), '.xlsx')
        text = result['text']
        self.assertIn('B2=21 [公式未执行: =SHARED_FORMULA_NEEDLE+A1]', text)
        self.assertIn('B1=14 [共享公式 si=0；锚点=B2；锚点原式: =SHARED_FORMULA_NEEDLE+A1；范围=B1:B2；未展开相对引用；文件缓存=存在；未重新计算]', text)
        self.assertNotIn('B1=14 [公式未执行: =SHARED_FORMULA_NEEDLE+A1]', text)
        self.assertIn('D5=[共享公式 si=0；锚点=B2；锚点原式: =SHARED_FORMULA_NEEDLE+A1；范围=B1:B2；未展开相对引用；文件缓存=缺失；未重新计算]', text)
        self.assertNotIn('D5=FALSE', text)

    def test_comments_stop_at_textresult_limit(self):
        out = TextResult()
        with TemporaryDirectory() as folder:
            path = xlsx_file(Path(folder) / 'annotations.xlsx')
            with zipfile.ZipFile(path) as z:
                with patch.object(offline_documents, 'MAX_TEXT', 20):
                    extract_sheet_comments(
                        z, 'xl/worksheets/sheet1.xml', 'Orders', out,
                        read_xml=_read_xml, resolve_target=_resolve_target,
                    )
        result = out.result()
        self.assertTrue(result['truncated'])
        self.assertLessEqual(result['characters'], 20)


if __name__ == '__main__':
    unittest.main()
