"""Comments and active Word alternatives survive all four export paths."""
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
from test_four_exports62 import CASES, CONTRACT_URL, REVOKE_SERVER_ID, assert_quality
from test_four_exports62 import source_fixture as previous_fixture, assert_new_content as assert_previous_content
from test_four_exports57 import _attachment_row
from test_database_adapter import create_shard
from test_pdf_comments63 import pdf_file
from test_word_alternatives63 import docx_file

PDF_NAME='review-comments.pdf'
DOCX_NAME='compatibility-terms.docx'
KEYWORDS=('PDF_COMMENT_NEEDLE','PDF_REPLY_NEEDLE','WORD_ALT_NEEDLE','WORD_BLOCK_NEEDLE')


def source_fixture(base,client,mode,count=2):
    config,chat,originals=previous_fixture(base,client,mode,count=count)
    paths=[]
    for offset,filename,create in ((220,PDF_NAME,pdf_file),(230,DOCX_NAME,docx_file)):
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
        create_shard(source,relative='message/message_11.db',group=chat,rows=[
            {'local':mid,'type':49,'text':_attachment_row(client,path,mid)}
            for offset,path in paths for mid in (offset,offset+1)])
    return config,chat,originals


def assert_new_content(test,root,rows,client):
    assert_previous_content(test,root,rows,client)
    for filename,keywords in ((PDF_NAME,KEYWORDS[:2]),(DOCX_NAME,KEYWORDS[2:])):
        docs=[row['attachmentContent'] for row in rows if row.get('text')==filename]
        test.assertEqual(len(docs),2)
        for doc in docs:
            for keyword in keywords:test.assertEqual(doc['text'].count(keyword),1)
            if filename==DOCX_NAME:
                test.assertNotIn('INACTIVE_WORD_ALT',doc['text'])
                test.assertIn('COMMENT_ACTIVE',doc['text'])
                test.assertIn('HEADER_ACTIVE',doc['text'])
            else:
                test.assertIn('PDF批注 第 1 页',doc['text'])
                test.assertIn('回复关系: 同页注释 1',doc['text'])
    reading=[(root/name).read_text(encoding='utf-8-sig') for name in
             ('messages.md','业务阅读.txt','chat.html','analysis/corpus.jsonl')]
    parts=[path.read_text(encoding='utf8') for path in (root/'analysis').glob('part-*.md')]
    for keyword in KEYWORDS:
        for text in reading:test.assertIn(keyword,text)
        test.assertTrue(any(keyword in text for text in parts))
    for text in reading+parts:test.assertNotIn('INACTIVE_WORD_ALT',text)
    source={'messages':rows,'analysisRefs':{row['id']:index for index,row in enumerate(rows,1)}}
    for keyword in KEYWORDS:
        result=search_messages(source,keyword)
        test.assertEqual(result['total'],2)
        test.assertTrue(all('附件正文' in item['matchSource'] for item in result['items']))
    test.assertEqual(search_messages(source,'INACTIVE_WORD_ALT')['total'],0)


class FourExports63Tests(unittest.TestCase):
    def test_comments_alternatives_and_historical_failures_across_four_exports(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-four63-') as folder:
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
