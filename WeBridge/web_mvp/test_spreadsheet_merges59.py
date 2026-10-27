"""Regression fixtures for spreadsheet merge metadata extraction."""
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from offline_documents import extract_document
from test_offline_documents import compound_file


S='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PR='http://schemas.openxmlformats.org/package/2006/relationships'


def xlsx_file(path, *, two_sheets=False):
    """Write a real OOXML workbook used by export fixtures and merge tests."""
    workbook=f'''<workbook xmlns="{S}" xmlns:r="{R}">
      <sheets><sheet name="Orders" sheetId="1" r:id="rId1"/>{'<sheet name="Second" sheetId="2" r:id="rId2"/>' if two_sheets else ''}</sheets>
    </workbook>'''
    sheet1=f'''<worksheet xmlns="{S}"><sheetData>
      <row r="1"><c r="A1" t="inlineStr"><is><t>Orders</t></is></c></row>
      <row r="2"><c r="A2" t="inlineStr"><is><t>MERGED_CUSTOMER_NEEDLE</t></is></c><c r="B2" t="inlineStr"><is><t>Order-001</t></is></c></row>
      <row r="3"><c r="B3" t="inlineStr"><is><t>Order-002</t></is></c></row>
      <row r="4"><c r="D4" s="1"><v>0.125</v></c><c r="F4" t="inlineStr"/></row>
    </sheetData><mergeCells count="5"><mergeCell ref="A1:C1"/><mergeCell ref="A2:A3"/><mergeCell ref="D4:E4"/><mergeCell ref="F4:G4"/><mergeCell ref="H1:XFD1048576"/></mergeCells></worksheet>'''
    sheet2=f'''<worksheet xmlns="{S}"><sheetData>
      <row r="2"><c r="A2" t="inlineStr"><is><t>MERGED_CUSTOMER_NEEDLE</t></is></c></row>
    </sheetData><mergeCells count="1"><mergeCell ref="A2:A3"/></mergeCells></worksheet>'''
    files={
        'xl/workbook.xml':workbook,
        'xl/worksheets/sheet1.xml':sheet1,
        'xl/_rels/workbook.xml.rels':f'''<Relationships xmlns="{PR}">
          <Relationship Id="rId1" Type="{R}/worksheet" Target="worksheets/sheet1.xml"/>{'<Relationship Id="rId2" Type="'+R+'/worksheet" Target="worksheets/sheet2.xml"/>' if two_sheets else ''}
        </Relationships>''',
        'xl/styles.xml':f'<styleSheet xmlns="{S}"><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="10"/></cellXfs></styleSheet>',
        '[Content_Types].xml':f'<Types xmlns="{PR}"/>',
    }
    if two_sheets:files['xl/worksheets/sheet2.xml']=sheet2
    with zipfile.ZipFile(path,'w') as archive:
        for name,content in files.items():archive.writestr(name,content)
    return path


def _record(kind,payload=b''):
    return struct.pack('<HH',kind,len(payload))+payload


def _bof(kind):
    return _record(0x0809,struct.pack('<HHHHII',0x0600,kind,0x0DBB,0x07CC,0,6))


def _label(row,col,text):
    encoded=text.encode('latin1')
    return _record(0x0204,struct.pack('<HHHHB',row,col,0,len(encoded),0)+encoded)


def _number(row,col,xf_index,value):
    return _record(0x0203,struct.pack('<HHHd',row,col,xf_index,value))


def _xf(format_id):
    data=bytearray(20)
    struct.pack_into('<HHH',data,0,0,format_id,0)
    return _record(0x00E0,data)


def xls_file(path):
    """Write a real BIFF8 workbook matching the OOXML fixture's key values."""
    custom=_record(0x041E,struct.pack('<HHB',164,6,0)+b'0.00%')
    xfs=b''.join((_xf(0),_xf(164)))
    sheet=_bof(0x0010)+_record(0x0200,struct.pack('<IIHHH',0,4,0,8,0))
    sheet+=_label(0,0,'Orders')
    sheet+=_label(1,0,'MERGED_CUSTOMER_NEEDLE')+_label(1,1,'Order-001')+_label(2,1,'Order-002')
    sheet+=_number(3,3,1,0.125)+_record(0x0201,struct.pack('<HHH',3,5,0))
    sheet+=_record(0x00E5,struct.pack('<H',5)+b''.join(struct.pack('<HHHH',*item) for item in (
        (0,0,0,2),(1,2,0,0),(3,3,3,4),(3,3,5,6),(0,65535,7,255))))
    sheet+=_record(0x000A)
    prefix=_bof(0x0005)+custom+xfs
    name=b'audit'
    bound=_record(0x0085,struct.pack('<IBBBB',len(prefix)+4+8+len(name)+4,0,0,len(name),0)+name)
    compound_file(path,{'Workbook':prefix+bound+_record(0x000A)+sheet})
    return path


class SpreadsheetMerges59Tests(unittest.TestCase):
    def test_xlsx_merges_preserve_rows_formatting_and_empty_anchor(self):
        with tempfile.TemporaryDirectory() as folder:
            result=extract_document(xlsx_file(Path(folder)/'merged.xlsx'),'.xlsx')
        text=result['text']
        self.assertIn('A2=MERGED_CUSTOMER_NEEDLE\tB2=Order-001',text)
        self.assertIn('B3=Order-002',text)
        self.assertIn('合并单元格 A2:A3',text)
        self.assertIn('锚点 A2=MERGED_CUSTOMER_NEEDLE',text)
        self.assertIn('合并单元格 D4:E4',text)
        self.assertIn('锚点 D4=12.50%',text)
        self.assertIn('合并单元格 F4:G4',text)
        self.assertIn('锚点 F4=空',text)
        self.assertIn('合并单元格 H1:XFD1048576',text)
        self.assertNotIn('I1=',text)

    def test_xlsx_same_address_on_two_worksheets_is_kept_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            text=extract_document(xlsx_file(Path(folder)/'two.xlsx',two_sheets=True),'.xlsx')['text']
        self.assertEqual(text.count('合并单元格 A2:A3'),2)
        self.assertIn('[工作表 Orders 合并单元格 A2:A3]',text)
        self.assertIn('[工作表 Second 合并单元格 A2:A3]',text)

    def test_xls_merges_use_end_exclusive_bounds_without_expansion(self):
        with tempfile.TemporaryDirectory() as folder:
            result=extract_document(xls_file(Path(folder)/'merged.xls'),'.xls')
        text=result['text']
        self.assertIn('A2=MERGED_CUSTOMER_NEEDLE\tB2=Order-001',text)
        self.assertIn('B3=Order-002',text)
        self.assertIn('合并单元格 A2:A3',text)
        self.assertIn('锚点 A2=MERGED_CUSTOMER_NEEDLE',text)
        self.assertIn('合并单元格 D4:E4',text)
        self.assertIn('锚点 D4=12.50%',text)
        self.assertIn('合并单元格 H1:IV65536',text)
        self.assertNotIn('I1=',text)


if __name__=='__main__':
    unittest.main()
