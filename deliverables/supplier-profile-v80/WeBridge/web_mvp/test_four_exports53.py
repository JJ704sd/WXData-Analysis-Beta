"""Shared quality and repeated-document checks for both clients and both modes."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from offline_desktop import DesktopSession
from test_database_adapter import create_metadata, create_shard, GROUP, SELF
from test_private_chat_integration import direct_source
from test_wecom_archive import fixture, pb
from test_offline_documents import pdf_file
from test_group_unicode52 import unicode_pdf

CASES = [(client, mode) for client in ('wechat', 'wecom') for mode in ('group', 'direct')]


def source_fixture(base, client, mode, count=4):
    account = base / 'account'
    account.mkdir()
    if mode == 'direct':
        source, chat, own = direct_source(account, client)
    elif client == 'wechat':
        source = account / 'db_storage'
        source.mkdir()
        create_metadata(source)
        create_shard(source, rows=[{'text': 'before attachments'}])
        chat, own = GROUP, SELF
    else:
        source = account / 'Data'
        fixture(source)
        chat, own = 'R:synthetic', '11'
    if client == 'wecom':
        with closing(sqlite3.connect(source / 'message.db')) as db, db:
            columns = db.execute("SELECT sql FROM sqlite_master WHERE name='message_table'").fetchone()[0]
            db.execute('ALTER TABLE message_table RENAME TO old_messages')
            db.execute(columns.replace('message_id integer,', 'message_id INTEGER PRIMARY KEY,'))
            db.execute('INSERT INTO message_table SELECT * FROM old_messages')
            db.execute('DROP TABLE old_messages')
            extra = pb(3045, pb(1, 'a' * 32) + pb(3, pb(3, 'synthetic-key')))
            db.execute('UPDATE message_table SET extra_content=? WHERE message_id=1', (extra,))
            sender = 22 if mode == 'direct' else 11
            entry = pb(1, sender) + pb(2, 1700000001) + pb(5, 2)
            for mid, reference in ((3, pb(17, extra)), (4, pb(16, 'synthetic-key'))):
                body = pb(1, pb(1, 0) + pb(2, pb(1, 'synthetic quoted reply')))
                db.execute('INSERT INTO message_table VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                           (mid, mid + 10, mid, sender, chat, 2, 1700000000 + mid, 0,
                            body, pb(1002, pb(1, entry + reference)), b''))
    original_root = base / 'originals'
    original_root.mkdir()
    originals, new_rows = {}, []
    for index in range(count):
        path = original_root / f'document-{index}.pdf'
        unicode_pdf(path) if index == 0 else pdf_file(path, 'SYNTHETIC quotation ' + str(index))
        raw = path.read_bytes()
        originals[hashlib.sha256(raw).hexdigest()] = raw
        digest = hashlib.md5(raw).hexdigest()
        for mid in (10 + index * 2, 11 + index * 2):
            if client == 'wecom':
                body = pb(2, path.name) + pb(4, len(raw)) + pb(10, digest)
                with closing(sqlite3.connect(source / 'message.db')) as db, db:
                    db.execute('INSERT INTO message_table VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                               (mid, mid + 10, mid, 11, chat, 102, 1700000000 + mid, 0, body, b'', b''))
            else:
                xml = '<msg><appmsg><title>' + path.name + '</title><type>6</type><appattach><totallen>' + str(len(raw)) + '</totallen><md5>' + digest + '</md5></appattach></appmsg></msg>'
                new_rows.append({'local': mid, 'type': 49, 'text': xml})
    if client == 'wechat':
        new_rows.append({'local': 100, 'text': 'message after attachments'})
        create_shard(source, relative='message/message_1.db', group=chat, rows=new_rows)
    else:
        body = pb(1, pb(1, 0) + pb(2, pb(1, 'message after attachments')))
        with closing(sqlite3.connect(source / 'message.db')) as db, db:
            db.execute('INSERT INTO message_table VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (100, 110, 100, 11, chat, 2, 1700000100, 0, body, b'', b''))
    config = {'sourceRoot': str(source), 'selfId': own, 'includeMedia': True, 'supplementRoot': str(original_root)}
    return config, chat, originals


def assert_quality(test, root, rows, client, originals):
    test.assertTrue(any(r['text'] == 'message after attachments' for r in rows))
    attachments = [r for r in rows if r.get('attachmentContent')]
    test.assertEqual(len(attachments), 2 * len(originals))
    for row in attachments:
        doc = row['attachmentContent']
        test.assertEqual(doc['source']['rootMessageId'], row['id'])
        test.assertEqual(doc['source']['chatId'], row['chatId'])
        test.assertEqual((root / row['asset']['path']).read_bytes(), originals[doc['sha256']])
        test.assertTrue(doc['text'])
        if 'ud800' in doc['text']:
            test.assertEqual(doc['extractionStatus'], 'partial')
            test.assertTrue(doc['requiresReview'])
            test.assertIn('💬', doc['text'])
    if client == 'wecom':
        quotes = [r['quote'] for r in rows if r.get('quote')]
        test.assertEqual(len(quotes), 2)
        test.assertTrue(all(q['sourceLinkStatus'] == 'matched_local_source' for q in quotes))
    test.assertEqual(rows, [json.loads(line) for line in (root / 'messages.jsonl').read_text(encoding='utf8').splitlines()])


class FourExport53Tests(unittest.TestCase):
    def test_cross_conversation_reuse_keeps_source_and_reload_starts_fresh(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            config, first_chat, _ = source_fixture(base, 'wecom', 'group', count=1)
            source = Path(config['sourceRoot'])
            second_chat = 'R:second-synthetic'
            with closing(sqlite3.connect(source / 'session.db')) as db, db:
                db.execute('INSERT INTO conversation_table VALUES(?,?,?,?)',
                           (second_chat, 'Second synthetic group', '', 1700000000))
            with closing(sqlite3.connect(source / 'message.db')) as db, db:
                db.execute('INSERT INTO message_table SELECT message_id+1000,server_id+1000,sequence+1000,'
                           'sender_id,?,content_type,send_time,flag,content,extra_content,local_extra_content '
                           'FROM message_table WHERE conversation_id=?', (second_chat, first_chat))
            session = DesktopSession(base / 'profile', client='wecom')
            session.open_source(config)
            session.prepare_review(first_chat, copy_media=True, load_view=False)
            second = session.prepare_review(second_chat, copy_media=True, load_view=False)
            rows = json.loads((Path(second['view']['viewRoot']) / 'messages.json').read_text(encoding='utf8'))
            for row in rows:
                if row.get('attachmentContent'):
                    self.assertEqual(row['attachmentContent']['source']['chatId'], second_chat)
                    self.assertEqual(row['attachmentContent']['source']['rootMessageId'], row['id'])
            self.assertEqual(second['report']['attachmentTextSummary']['extractionRuns'], 0)
            self.assertEqual(second['report']['attachmentTextSummary']['reusedFromPreviousExports'], 1)
            session.open_source(config)
            fresh = session.prepare_review(second_chat, copy_media=True, load_view=False)
            self.assertEqual(fresh['report']['attachmentTextSummary']['extractionRuns'], 1)

    def test_repeat_parse_preserves_four_modes_and_reuses_verified_documents(self):
        for client, mode in CASES:
            with self.subTest(client=client, mode=mode), tempfile.TemporaryDirectory() as folder:
                base = Path(folder)
                config, chat, originals = source_fixture(base, client, mode, count=2)
                session = DesktopSession(base / 'profile', client=client, conversation_mode=mode)
                session.open_source(config)
                first = session.prepare_review(chat, copy_media=True, load_view=False)
                first_rows = json.loads((Path(first['view']['viewRoot']) / 'messages.json').read_text(encoding='utf8'))
                second = session.prepare_review(chat, copy_media=True, load_view=False)
                exported = session.confirm_export(second['review']['reviewId'],
                                                  {key: True for key in second['reviewChecks']})
                root = Path(exported['output'])
                rows = json.loads((root / 'messages.json').read_text(encoding='utf8'))
                assert_quality(self, root, rows, client, originals)
                self.assertEqual(rows, first_rows)
                summary = second['report']['attachmentTextSummary']
                self.assertEqual(summary.get('extractionRuns', -1), 0)
                self.assertEqual(summary.get('reusedFromPreviousExports', -1), 2)


if __name__ == '__main__':
    unittest.main()
