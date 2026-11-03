"""Regression fixtures for XLSX/XLS hidden-sheet, row, and column metadata."""
import struct
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from offline_documents import extract_document
from offline_spreadsheet_visibility import Visibility
from test_offline_documents import compound_file


S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PR = 'http://schemas.openxmlformats.org/package/2006/relationships'
F = 'urn:foreign-sheet-extension'


def xlsx_file(path):
    """Write a small OOXML workbook containing every supported state marker."""
    workbook = f'''<workbook xmlns="{S}" xmlns:r="{R}"><sheets>
      <sheet name="Visible" sheetId="1" r:id="rId1"/>
      <sheet name="Hidden" sheetId="2" state="hidden" r:id="rId2"/>
      <sheet name="VeryHidden" sheetId="3" state="veryHidden" r:id="rId3"/>
    </sheets></workbook>'''
    visible = f'''<worksheet xmlns="{S}" xmlns:f="{F}"><sheetFormatPr zeroHeight="1"/><cols>
      <col min="3" max="3" hidden="1"/>
      <col min="10" max="16384" hidden="1"/>
      <col min="4" max="4" width="24"/>
    </cols><sheetData>
      <row r="1"><c r="A1" t="inlineStr"><is><t>VISIBLE_XLSX_NEEDLE</t></is></c>
        <c r="C1" t="inlineStr"><is><t>HIDDEN_XLSX_NEEDLE</t></is></c>
        <c r="D1" t="inlineStr"><is><t>VISIBLE_WIDTH_ONLY_XLSX</t></is></c>
        <c r="J1" t="inlineStr"><is><t>HIDDEN_XLSX_LARGE_RANGE</t></is></c></row>
      <row r="2" hidden="1"><c r="A2" t="inlineStr"><is><t>HIDDEN_XLSX_ROW_NEEDLE</t></is></c></row>
      <row hidden="1"><c r="B3" t="inlineStr"><is><t>HIDDEN_XLSX_UNLOCATED_NEEDLE</t></is></c></row>
      <row r="4" hidden="0"><c r="A4" t="inlineStr"><is><t>VISIBLE_ZERO_HEIGHT_OVERRIDE_XLSX</t></is></c></row>
    </sheetData><extLst><f:ext><f:row hidden="1"><f:col min="4" max="4" hidden="1"/></f:row></f:ext></extLst></worksheet>'''
    hidden = f'''<worksheet xmlns="{S}"><sheetFormatPr zeroHeight="0"/><sheetData>
      <row r="1"><c r="A1" t="inlineStr"><is><t>HIDDEN_XLSX_NEEDLE</t></is></c>
    </row></sheetData></worksheet>'''
    very_hidden = f'''<worksheet xmlns="{S}"><sheetData>
      <row r="1"><c r="A1" t="inlineStr"><is><t>VERY_HIDDEN_XLSX_NEEDLE</t></is></c>
    </row></sheetData></worksheet>'''
    relationships = f'''<Relationships xmlns="{PR}">
      <Relationship Id="rId1" Type="{R}/worksheet" Target="worksheets/sheet1.xml"/>
      <Relationship Id="rId2" Type="{R}/worksheet" Target="worksheets/sheet2.xml"/>
      <Relationship Id="rId3" Type="{R}/worksheet" Target="worksheets/sheet3.xml"/>
    </Relationships>'''
    with zipfile.ZipFile(path, 'w') as archive:
        for name, value in {
            'xl/workbook.xml': workbook,
            'xl/_rels/workbook.xml.rels': relationships,
            'xl/worksheets/sheet1.xml': visible,
            'xl/worksheets/sheet2.xml': hidden,
            'xl/worksheets/sheet3.xml': very_hidden,
            '[Content_Types].xml': f'<Types xmlns="{PR}"/>',
        }.items():
            archive.writestr(name, value)
    return path


def _record(kind, payload=b''):
    return struct.pack('<HH', kind, len(payload)) + payload


def _bof(kind):
    return _record(0x0809, struct.pack('<HHHHII', 0x0600, kind, 0x0DBB, 0x07CC, 0, 6))


def _label(row, col, text):
    encoded = text.encode('latin1')
    return _record(0x0204, struct.pack('<HHHHB', row, col, 0, len(encoded), 0) + encoded)


def _boundsheet(offset, visibility, name):
    encoded = name.encode('latin1')
    return _record(0x0085, struct.pack('<IBBBB', offset, visibility, 0, len(encoded), 0) + encoded)


def _row(row, hidden):
    flags = 0x20 if hidden else 0
    return _record(0x0208, struct.pack('<HHHHHHHH', row, 0, 3, 0, 0, 0, flags, 0))


def _colinfo(first, last, hidden):
    return _record(0x007D, struct.pack('<HHHHHH', first, last, 2048, 0, 1 if hidden else 0, 0))


def _sheet(records, *, max_row=3, max_col=256):
    return (_bof(0x0010) + _record(0x0200, struct.pack('<IIHHH', 0, max_row, 0, max_col, 0))
            + b''.join(records) + _record(0x000A))


def xls_file(path):
    """Write a minimal BIFF8 workbook with sparse row/column metadata."""
    sheets = [
        ('Visible', 0, _sheet([_label(0, 0, 'VISIBLE_XLS_NEEDLE')], max_row=1, max_col=1)),
        ('Hidden', 1, _sheet([
            _colinfo(2, 2, True), _label(0, 2, 'HIDDEN_XLS_NEEDLE'),
        ], max_row=1, max_col=3)),
        ('VeryHidden', 2, _sheet([_label(0, 0, 'VERY_HIDDEN_XLS_NEEDLE')], max_row=1, max_col=1)),
        ('Rows', 0, _sheet([
            _record(0x0225, struct.pack('<HH', 0x0002, 0)),
            _label(0, 0, 'DEFAULT_HIDDEN_XLS_NEEDLE'),
            _row(1, False), _label(1, 0, 'EXPLICIT_VISIBLE_XLS_NEEDLE'),
            _row(2, True), _label(2, 0, 'HIDDEN_XLS_ROW_NEEDLE'),
        ], max_row=3, max_col=1)),
        ('Columns', 0, _sheet([
            _colinfo(10, 255, True), _label(0, 10, 'HIDDEN_XLS_LARGE_RANGE'),
        ], max_row=1, max_col=256)),
    ]
    prefix_size = len(_bof(0x0005)) + sum(
        len(_boundsheet(0, visibility, name)) for name, visibility, _ in sheets
    ) + len(_record(0x000A))
    offsets = []
    current = prefix_size
    for _, _, data in sheets:
        offsets.append(current)
        current += len(data)
    globals_ = _bof(0x0005) + b''.join(
        _boundsheet(offset, visibility, name)
        for offset, (name, visibility, _) in zip(offsets, sheets)
    ) + _record(0x000A)
    compound_file(path, {'Workbook': globals_ + b''.join(data for _, _, data in sheets)})
    return path


class SpreadsheetVisibility61Tests(unittest.TestCase):
    def test_xlsx_states_rows_columns_and_visible_text(self):
        with TemporaryDirectory() as folder:
            result = extract_document(xlsx_file(Path(folder) / 'visibility.xlsx'), '.xlsx')
        text = result['text']
        self.assertIn('VISIBLE_XLSX_NEEDLE', text)
        self.assertIn('A1=VISIBLE_XLSX_NEEDLE', text)
        self.assertIn('C1=HIDDEN_XLSX_NEEDLE [隐藏列]', text)
        self.assertIn('工作表 Visible 行 2 [隐藏行]', text)
        self.assertIn('HIDDEN_XLSX_ROW_NEEDLE', text)
        self.assertIn('HIDDEN_XLSX_UNLOCATED_NEEDLE', text)
        self.assertIn('工作表 Visible 行 ? [隐藏行]', text)
        self.assertIn('隐藏行=有 1 行未记录行号', text)
        self.assertIn('默认隐藏行设置 zeroHeight=1', text)
        self.assertIn('VISIBLE_ZERO_HEIGHT_OVERRIDE_XLSX', text)
        self.assertNotIn('工作表 Visible 行 4 [隐藏行]', text)
        self.assertIn('工作表 Hidden [隐藏工作表 state=hidden]', text)
        self.assertIn('默认隐藏行设置 zeroHeight=0', text)
        self.assertIn('工作表 VeryHidden [隐藏工作表 state=veryHidden]', text)
        self.assertIn('隐藏列=C,J:XFD', text)
        self.assertIn('D1=VISIBLE_WIDTH_ONLY_XLSX', text)
        self.assertNotIn('D1=VISIBLE_WIDTH_ONLY_XLSX [隐藏列]', text)

    def test_xls_default_rows_explicit_override_and_large_column_range(self):
        with TemporaryDirectory() as folder:
            result = extract_document(xls_file(Path(folder) / 'visibility.xls'), '.xls')
        text = result['text']
        self.assertIn('A1=VISIBLE_XLS_NEEDLE', text)
        self.assertIn('C1=HIDDEN_XLS_NEEDLE [隐藏列]', text)
        self.assertIn('工作表 Rows 行 1 [默认隐藏行]', text)
        self.assertIn('工作表 Rows 行 2', text)
        self.assertIn('EXPLICIT_VISIBLE_XLS_NEEDLE', text)
        self.assertNotIn('工作表 Rows 行 2 [隐藏行]', text)
        self.assertIn('工作表 Rows 行 3 [隐藏行]', text)
        self.assertIn('工作表 Hidden [隐藏工作表 state=hidden]', text)
        self.assertIn('工作表 VeryHidden [隐藏工作表 state=veryHidden]', text)
        self.assertIn('K1=HIDDEN_XLS_LARGE_RANGE [隐藏列]', text)
        self.assertIn('隐藏列=K:IV', text)

    def test_direct_visibility_query_does_not_expand_ranges(self):
        visibility = Visibility('Large', 'visible', ((2, 2),), ((10, 16384),))
        self.assertEqual(visibility.row_label(2), '工作表 Large 行 2 [隐藏行]')
        self.assertEqual(visibility.cell_text('XFD1', 'HIDDEN'), 'XFD1=HIDDEN [隐藏列]')
        self.assertEqual(visibility.cell_text('A1', 'VISIBLE'), 'A1=VISIBLE')
        self.assertEqual(visibility.hidden_column_ranges, ((10, 16384),))

    def test_unknown_state_is_neutral_and_invalid_column_ranges_fail(self):
        visibility = Visibility('Custom', 'custom')
        self.assertEqual(visibility.sheet_name, 'Custom [工作表状态=custom]')
        with self.assertRaises(ValueError):
            Visibility('Broken', 'visible', hidden_columns=((5, 3),))
        with self.assertRaises(ValueError):
            Visibility('Broken', 'visible', hidden_columns=((0, 4),))

    def test_xlsx_fixture_is_readable_as_a_zip(self):
        with TemporaryDirectory() as folder:
            path = xlsx_file(Path(folder) / 'visibility.xlsx')
            with zipfile.ZipFile(path) as archive:
                self.assertIn('xl/workbook.xml', archive.namelist())
                self.assertIn('xl/worksheets/sheet1.xml', archive.namelist())


if __name__ == '__main__':
    unittest.main()
