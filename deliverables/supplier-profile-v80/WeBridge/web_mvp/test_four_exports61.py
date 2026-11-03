"""File visibility and table relationships survive all four export paths."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from offline_desktop import DesktopSession
from private_profiles import ProfileStore
from private_insights import search_messages
from test_four_exports60 import CASES, CONTRACT_URL, REVOKE_SERVER_ID, assert_quality
from test_four_exports60 import source_fixture as previous_fixture, assert_new_content as assert_previous_content
from test_four_exports57 import _attachment_row
from test_database_adapter import create_shard
from test_spreadsheet_visibility61 import xlsx_file, xls_file
from test_presentation_tables61 import pptx_file

XLSX_NAME='hidden-order-details.xlsx'
XLS_NAME='hidden-legacy-details.xls'
PPTX_NAME='quotation-table.pptx'
KEYWORDS=('HIDDEN_XLSX_NEEDLE','HIDDEN_XLS_NEEDLE','PPT_TABLE_NEEDLE')


def source_fixture(base,client,mode,count=2):
    config,chat,originals=previous_fixture(base,client,mode,count=count)
    paths=[]
    for offset,filename,create in ((170,XLSX_NAME,xlsx_file),(180,XLS_NAME,xls_file),(190,PPTX_NAME,pptx_file)):
        path=Path(config['supplementRoot'])/filename
        create(path)
        raw=path.read_bytes()
        originals[hashlib.sha256(raw).hexdigest()]=raw
        paths.append((offset,path))
    source=Path(config['sourceRoot'])
    if client=='wecom':
        with closing(sqlite3.connect(source/'message.db')) as db,db:
            for offset,path in paths:
                for mid in (offset,offset+1):
                    db.execute('INSERT INTO message_table VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        (mid,mid+10,mid,11,chat,102,1700000000+mid,0,_attachment_row(client,path,mid),b'',b''))
    else:
        create_shard(source,relative='message/message_9.db',group=chat,rows=[
            {'local':mid,'type':49,'text':_attachment_row(client,path,mid)}
            for offset,path in paths for mid in (offset,offset+1)])
    return config,chat,originals


def assert_new_content(test,root,rows,client):
    assert_previous_content(test,root,rows,client)
    for filename,keyword in zip((XLSX_NAME,XLS_NAME,PPTX_NAME),KEYWORDS):
        docs=[row['attachmentContent'] for row in rows if row.get('text')==filename]
        test.assertEqual(len(docs),2)
        for doc in docs:
            test.assertIn(keyword,doc['text'])
            if filename==PPTX_NAME:
                test.assertIn('表格 1 行 1',doc['text'])
                test.assertIn('C2=（空）',doc['text'])
                test.assertEqual(doc['text'].count('PPT_TABLE_NEEDLE'),1)
                test.assertIn('R3C1:R3C2',doc['text'])
            else:
                test.assertIn('隐藏',doc['text'])
                test.assertIn('隐藏列',doc['text'])
                test.assertIn('隐藏行',doc['text'])
    reading=[(root/name).read_text(encoding='utf-8-sig') for name in
             ('messages.md','业务阅读.txt','chat.html','analysis/corpus.jsonl')]
    parts=[path.read_text(encoding='utf8') for path in (root/'analysis').glob('part-*.md')]
    for keyword in KEYWORDS:
        for text in reading:test.assertIn(keyword,text)
        test.assertTrue(any(keyword in text for text in parts))
    for text in reading:
        test.assertIn('隐藏列',text)
        test.assertIn('表格 1 行 1',text)
    source={'messages':rows,'analysisRefs':{row['id']:index for index,row in enumerate(rows,1)}}
    for keyword in KEYWORDS:
        result=search_messages(source,keyword)
        test.assertEqual(result['total'],2)
        test.assertTrue(all('附件正文' in item['matchSource'] for item in result['items']))


class FourExports61Tests(unittest.TestCase):
    def test_table_context_and_historical_failures_across_four_exports(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-four61-') as folder:
            for client,mode in CASES:
                with self.subTest(client=client,mode=mode):
                    base=Path(folder)/(client+'-'+mode)
                    base.mkdir()
                    config,chat,originals=source_fixture(base,client,mode)
                    session=DesktopSession(base/'profile',client=client,conversation_mode=mode)
                    session.open_source(config)
                    prepared=session.prepare_review(chat,copy_media=True,load_view=False)
                    output=Path(prepared['view']['viewRoot'])
                    rows=json.loads((output/'messages.json').read_text(encoding='utf8'))
                    assert_quality(self,output,rows,client,originals)
                    assert_new_content(self,output,rows,client)
                    if mode=='direct':
                        formal=session.confirm_export(prepared['review']['reviewId'],{key:True for key in prepared['reviewChecks']})
                        self.assertEqual(ProfileStore(base/'portraits').prepare(formal['output'])['status'],'ready')


if __name__=='__main__':
    unittest.main()
