import io
import zipfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from web_mvp.offline_documents import _excel_format_number, extract_document


def _xlsx(*, date1904=False, values=None, styles=None):
    values = values or []
    styles = styles or """
      <styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
        <cellXfs count="1"><xf numFmtId="0"/></cellXfs>
      </styleSheet>
    """
    rows = []
    for ref, value, style, formula in values:
        attrs = f' r="{ref}"' + (f' s="{style}"' if style is not None else '')
        formula_xml = f'<f>{formula}</f>' if formula is not None else ''
        rows.append(f'<c{attrs}>{formula_xml}<v>{value}</v></c>')
    workbook_pr = f'<workbookPr date1904="1"/>' if date1904 else '<workbookPr date1904="0"/>'
    workbook = f'''<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
        {workbook_pr}<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>
      </workbook>'''
    sheet = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1">' + ''.join(rows) + '</row></sheetData></worksheet>'
    rels = '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
    </Relationships>'''
    files = {
        'xl/workbook.xml': workbook,
        'xl/worksheets/sheet1.xml': sheet,
        'xl/styles.xml': styles,
        'xl/_rels/workbook.xml.rels': rels,
        '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


class SpreadsheetSemantics56Tests(unittest.TestCase):
    def parse(self, data):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'semantic.xlsx'
            path.write_bytes(data)
            return extract_document(path, '.xlsx')['text']

    def test_dates_percentages_zero_padding_and_formula(self):
        styles = '''
          <styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
            <numFmts count="3">
              <numFmt numFmtId="164" formatCode="yyyy-mm-dd hh:mm"/>
              <numFmt numFmtId="165" formatCode="0.00%"/>
              <numFmt numFmtId="166" formatCode="000000"/>
            </numFmts>
            <cellXfs count="5">
              <xf numFmtId="0"/><xf numFmtId="164"/><xf numFmtId="165"/><xf numFmtId="166"/><xf numFmtId="20"/>
            </cellXfs>
          </styleSheet>'''
        text = self.parse(_xlsx(values=[
            ('A1', '45292.5', 1, None),
            ('B1', '0.125', 2, None),
            ('C1', '123', 3, None),
            ('D1', '123', 3, 'A1+1'),
            ('E1', '0.5', 4, None),
        ], styles=styles))
        self.assertIn('A1=2024-01-01T12:00:00', text)
        self.assertIn('B1=12.50%', text)
        self.assertIn('C1=000123', text)
        self.assertIn('D1=000123 [公式未执行: =A1+1]', text)
        self.assertIn('E1=12:00:00', text)

    def test_date1904_and_serial60_are_explicit(self):
        styles = '''
          <styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
            <numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd"/></numFmts>
            <cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="164"/></cellXfs>
          </styleSheet>'''
        text = self.parse(_xlsx(date1904=True, values=[('A1', '0', 1, None)], styles=styles))
        self.assertIn('A1=1904-01-01', text)

        text = self.parse(_xlsx(values=[('A1', '60', 1, None)], styles=styles))
        self.assertIn('A1=60', text)
        self.assertIn('无法真实表示', text)

    def test_complex_format_keeps_raw_value_and_format_code(self):
        styles = '''
          <styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
            <numFmts count="1"><numFmt numFmtId="164" formatCode="0.00E+00"/></numFmts>
            <cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="164"/></cellXfs>
          </styleSheet>'''
        text = self.parse(_xlsx(values=[('A1', '1234.5', 1, None)], styles=styles))
        self.assertIn('A1=1234.5', text)
        self.assertIn('0.00E+00', text)
        self.assertIn('未完整渲染', text)

    def test_boundary_formats_do_not_guess_and_keep_decimal_precision(self):
        elapsed = _excel_format_number('1.5', '[h]:mm:ss', 0)
        self.assertIn('1.5', elapsed)
        self.assertIn('[h]:mm:ss', elapsed)
        self.assertIn('未完整渲染', elapsed)

        serial_fraction = _excel_format_number('60.5', 'yyyy-mm-dd', 0)
        self.assertIn('60.5', serial_fraction)
        self.assertIn('无法真实表示', serial_fraction)

        conditional = _excel_format_number('-1', '[<0]yyyy-mm-dd;0', 0)
        self.assertIn('-1', conditional)
        self.assertIn('[<0]yyyy-mm-dd;0', conditional)
        self.assertIn('未完整渲染', conditional)

        self.assertEqual(_excel_format_number('12345678901234567', '00000000000000000000', 0),
                         '00012345678901234567')
        self.assertEqual(_excel_format_number('0.125', '0%', 0), '13%')
        self.assertEqual(_excel_format_number('0.125', '0.00%', 0), '12.50%')
        unit = _excel_format_number('12', '000"件"', 0)
        self.assertIn('12', unit)
        self.assertIn('000"件"', unit)
        self.assertIn('未完整渲染', unit)

    def test_builtin_percentage_formats(self):
        styles = '''
          <styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
            <cellXfs count="3"><xf numFmtId="0"/><xf numFmtId="9"/><xf numFmtId="10"/></cellXfs>
          </styleSheet>'''
        text = self.parse(_xlsx(values=[
            ('A1', '0.125', 1, None),
            ('B1', '0.125', 2, None),
        ], styles=styles))
        self.assertIn('A1=13%', text)
        self.assertIn('B1=12.50%', text)


if __name__ == '__main__':
    unittest.main()
