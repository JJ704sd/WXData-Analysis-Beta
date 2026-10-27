"""Public export contract for obtained attachments and unavailable originals."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
import zipfile

from test_wecom_archive import fixture, pb
from offline_desktop import DesktopSession
from offline_images import message_nodes
from offline_documents import AttachmentExtractor


def pdf_file(path, text='PDF business requirement', *, encrypted=False, blank=False):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer=PdfWriter();page=writer.add_blank_page(width=400,height=300)
    if not blank:
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream=DecodedStreamObject();stream.set_data(('BT /F1 12 Tf 10 200 Td ('+text+') Tj ET').encode())
        page[NameObject('/Contents')]=writer._add_object(stream)
    if encrypted:writer.encrypt('unprovided-password')
    writer.write(path)


def office_file(path, files):
    with zipfile.ZipFile(path,'w') as archive:
        for name,text in files.items():archive.writestr(name,text)


def compound_file(path, streams):
    """Minimal invented CFB with regular streams, never a copied user document."""
    free,end=0xffffffff,0xfffffffe
    header=bytearray(512);header[:8]=bytes.fromhex('d0cf11e0a1b11ae1')
    struct.pack_into('<HHHH',header,24,0x3e,3,0xfffe,9);struct.pack_into('<H',header,32,6)
    struct.pack_into('<IIIIIIIII',header,40,0,1,1,0,4096,end,0,end,0)
    for i in range(109):struct.pack_into('<I',header,76+4*i,0 if i==0 else free)
    fat=[free]*128;fat[0]=0xfffffffd;fat[1]=end
    directory=bytearray(512)
    def entry(index,name,kind,start,size,right=free,child=free):
        at=128*index;encoded=(name+'\0').encode('utf-16-le');directory[at:at+len(encoded)]=encoded
        struct.pack_into('<HBBIII',directory,at+64,len(encoded),kind,1,free,right,child)
        struct.pack_into('<IQ',directory,at+116,start,size)
    entry(0,'Root Entry',5,end,0,child=1)
    payload=[];sector=2
    for number,(name,data) in enumerate(streams.items(),1):
        data=data+bytes(max(0,4096-len(data)));sectors=(len(data)+511)//512
        entry(number,name,2,sector,len(data),right=number+1 if number<len(streams) else free)
        for i in range(sectors):fat[sector+i]=sector+i+1 if i+1<sectors else end
        payload.append(data+bytes(sectors*512-len(data)));sector+=sectors
    path.write_bytes(header+struct.pack('<128I',*fat)+directory+b''.join(payload))


class DocumentExportTests(unittest.TestCase):
    def test_encrypted_image_only_and_corrupt_files_keep_separate_states(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'assets').mkdir();extractor=AttachmentExtractor()
            for filename,expected in (('encrypted.pdf','encrypted'),('image-only.pdf','no_text'),('broken.docx','failed')):
                path=root/'assets'/filename
                if filename=='broken.docx':path.write_bytes(b'not-an-office-file')
                else:pdf_file(path,encrypted=filename=='encrypted.pdf',blank=filename=='image-only.pdf')
                data=path.read_bytes();row={'id':'synthetic-message','chatId':'R:synthetic','chatName':'合成群','kind':'file',
                    'asset':{'status':'ready','filename':filename,'path':'assets/'+filename,'digest':hashlib.sha256(data).hexdigest(),'size':len(data)}}
                extractor.attach(row,root,True)
                self.assertEqual(row['attachmentContent']['fileStatus'],'ready')
                self.assertEqual(row['attachmentContent']['extractionStatus'],expected)
                self.assertEqual(row['attachmentContent']['text'],'')

    def test_legacy_doc_and_ppt_extract_only_current_structured_text(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'assets').mkdir()
            word=bytearray(4096);word[:2]=b'\xec\xa5';struct.pack_into('<H',word,32,14)
            struct.pack_into('<H',word,62,22);struct.pack_into('<H',word,152,34)
            body='旧版Word业务正文\r';raw=body.encode('utf-16-le');word[1024:1024+len(raw)]=raw
            struct.pack_into('<I',word,76,len(body))
            plc=struct.pack('<II',0,len(body))+struct.pack('<HIH',0,1024,0)
            clx=b'\x02'+struct.pack('<I',len(plc))+plc
            struct.pack_into('<II',word,154+33*8,0,len(clx))
            compound_file(root/'assets/test.doc',{'WordDocument':word,'0Table':clx})
            def record(kind,payload,version=0):return struct.pack('<HHI',version,kind,len(payload))+payload
            data=bytearray(4096)
            document=record(1000,record(1011,struct.pack('<I',2)),15)
            slide=record(1006,record(0xf00d,record(4000,'旧版PPT业务正文'.encode('utf-16-le'))),15)
            directory=record(6002,struct.pack('<III',(2<<20)|1,128,256))
            edit=bytearray(record(4085,bytes(28)));struct.pack_into('<III',edit,16,0,512,1)
            for offset,payload in ((128,document),(256,slide),(512,directory),(768,edit)):data[offset:offset+len(payload)]=payload
            current=bytearray(4096);struct.pack_into('<I',current,16,768)
            compound_file(root/'assets/test.ppt',{'PowerPoint Document':data,'Current User':current})
            extractor=AttachmentExtractor()
            for filename,expected in (('test.doc','旧版Word业务正文'),('test.ppt','旧版PPT业务正文')):
                path=root/'assets'/filename;raw=path.read_bytes()
                row={'id':'synthetic-message','chatId':'R:synthetic','chatName':'合成群','kind':'file',
                    'asset':{'status':'ready','filename':filename,'path':'assets/'+filename,'digest':hashlib.sha256(raw).hexdigest(),'size':len(raw)}}
                extractor.attach(row,root,True)
                self.assertEqual(row['attachmentContent']['extractionStatus'],'partial')
                self.assertIn(expected,row['attachmentContent']['text'])

    def test_selected_export_includes_attachment_text_states_and_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder);source=base/'source';fixture(source)
            cache=source/'Cache/File';cache.mkdir(parents=True)
            pdf_file(cache/'requirement.pdf')
            office_file(cache/'requirement.docx',{'word/document.xml':'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Word业务需求</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格交期</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'})
            office_file(cache/'requirement.xlsx',{'xl/workbook.xml':'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="报价" r:id="r1"/></sheets></workbook>',
                'xl/_rels/workbook.xml.rels':'<Relationships><Relationship Id="r1" Type="x/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
                'xl/worksheets/sheet1.xml':'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="8"><c r="D8" t="inlineStr"><is><t>Excel业务需求</t></is></c><c r="E8"><f>1+2</f><v>3</v></c></row></sheetData></worksheet>'})
            office_file(cache/'requirement.pptx',{'ppt/presentation.xml':'<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="1" r:id="r1"/></p:sldIdLst></p:presentation>',
                'ppt/_rels/presentation.xml.rels':'<Relationships><Relationship Id="r1" Target="slides/slide1.xml"/></Relationships>',
                'ppt/slides/slide1.xml':'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><a:p><a:r><a:t>PPT业务需求</a:t></a:r></a:p></p:cSld></p:sld>'})
            (cache/'requirement.csv').write_bytes('CSV业务需求,交期\r\n订单,周五'.encode('utf8'))
            files=sorted(cache.iterdir())
            def payload(path):
                data=path.read_bytes()
                return pb(2,path.name)+pb(4,len(data))+pb(10,hashlib.md5(data).hexdigest())
            rows=[(i,20,payload(path)) for i,path in enumerate(files,10)]
            rows.append((20,20,pb(2,'missing.pdf')+pb(4,99)+pb(10,'f'*32)))
            nested=pb(1,pb(1,11)+pb(2,1700000000)+pb(5,20)+pb(103,payload(cache/'requirement.docx')))+pb(2,'合成附件转发')
            rows.append((21,4,nested))
            with closing(sqlite3.connect(source/'message.db')) as db,db:
                for mid,kind,body in rows:
                    db.execute('insert into message_table values(?,?,?,?,?,?,?,?,?,?,?)',(mid,mid,mid,11,'R:synthetic',kind,1700000000+mid,0,body,b'',b''))
                db.execute('insert into message_table values(?,?,?,?,?,?,?,?,?,?,?)',(90,90,90,11,'R:other',20,1700000090,0,payload(cache/'requirement.docx'),b'',b''))
            session=DesktopSession(base/'profile',client='wecom');session.open_source({'sourceRoot':str(source)})
            result=session.parse_and_open(chat_name='企微合成群')
            root=Path(result['output']);values=json.loads((root/'messages.json').read_text(encoding='utf8'))
            self.assertEqual(values,[json.loads(line) for line in (root/'messages.jsonl').read_text(encoding='utf8').splitlines()])
            docs=[]
            for row in values:
                self.assertEqual(row['chatId'],'R:synthetic')
                for node,_,_ in message_nodes(row):
                    if node['kind']=='file':
                        doc=node['attachmentContent'];docs.append(doc)
                        self.assertEqual(doc['source']['rootMessageId'],row['id'])
                        self.assertEqual(doc['source']['provenance'],row['provenance'])
            self.assertEqual(len(docs),7)
            ready=[d for d in docs if d['fileStatus']=='ready']
            self.assertEqual(len(ready),6)
            self.assertTrue(all(d['extractionStatus']=='extracted' for d in ready))
            for document in ready:
                self.assertIn(document['text'],(root/'messages.md').read_text(encoding='utf8'))
                self.assertIn(document['text'],(root/'业务阅读.txt').read_text(encoding='utf-8-sig'))
            text='\n'.join(d['text'] for d in ready)
            for expected in ('PDF business requirement','Word业务需求','表格交期','Excel业务需求','D8','PPT业务需求','CSV业务需求'):
                self.assertIn(expected,text)
                self.assertIn(expected,(root/'messages.md').read_text(encoding='utf8'))
                self.assertIn(expected,(root/'业务阅读.txt').read_text(encoding='utf-8-sig'))
            missing=next(d for d in docs if d['filename']=='missing.pdf')
            self.assertEqual((missing['fileStatus'],missing['extractionStatus']),('missing','original_unavailable'))
            self.assertEqual(result['report']['attachmentTextSummary']['extractionStatuses']['extracted'],6)
            self.assertIn('附件正文',result['reporting']['replyMarkdown'])
            no_media=session.parse_and_open(chat_name='企微合成群',copy_media=False)
            rows=json.loads((Path(no_media['output'])/'messages.json').read_text(encoding='utf8'))
            docs=[node['attachmentContent'] for row in rows for node,_,_ in message_nodes(row) if node['kind']=='file']
            self.assertTrue(all((d['fileStatus'],d['extractionStatus'])==('not_loaded','not_requested') for d in docs))


if __name__=='__main__':unittest.main()
