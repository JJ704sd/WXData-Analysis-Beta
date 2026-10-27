"""Regression for quoted messages in WeCom INTEGER PRIMARY KEY tables."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from offline_desktop import DesktopSession
from test_wecom_archive import fixture, pb


def quoted_fixture(source, declaration='INTEGER PRIMARY KEY', identifier='legacy'):
    fixture(source)
    extra = (pb(3045, pb(1, 'a' * 32)) if identifier == 'legacy'
             else pb(3045, pb(3, pb(3, 'synthetic-key'))))
    entry = pb(1, 11) + pb(2, 1700000001) + pb(5, 2)
    entry += pb(17, extra) if identifier == 'legacy' else pb(16, 'synthetic-key')
    with closing(sqlite3.connect(source / 'message.db')) as db, db:
        db.execute('ALTER TABLE message_table RENAME TO old_messages')
        db.execute('CREATE TABLE message_table(message_id ' + declaration +
                   ',server_id integer,sequence integer,sender_id integer,conversation_id text,'
                   'content_type integer,send_time integer,flag integer,content blob,'
                   'extra_content blob,local_extra_content blob)')
        db.execute('INSERT INTO message_table SELECT * FROM old_messages')
        db.execute('DROP TABLE old_messages')
        db.execute('UPDATE message_table SET extra_content=? WHERE message_id=1', (extra,))
        body = pb(1, pb(1, 0) + pb(2, pb(1, 'synthetic reply')))
        db.execute('INSERT INTO message_table VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   (3, 13, 3, 11, 'R:synthetic', 2, 1700000003, 0,
                    body, pb(1002, pb(1, entry)), b''))


class GroupQuoteSchema51Tests(unittest.TestCase):
    def assert_review_and_export(self, identifier):
        for declaration in ('INTEGER', 'INTEGER PRIMARY KEY'):
            with self.subTest(schema=declaration, identifier=identifier), tempfile.TemporaryDirectory() as folder:
                base = Path(folder)
                source = base / 'account' / 'Data'
                source.parent.mkdir()
                quoted_fixture(source, declaration, identifier)
                session = DesktopSession(base / 'profile', client='wecom')
                session.open_source({'sourceRoot': str(source)})
                prepared = session.prepare_review('R:synthetic', copy_media=True, load_view=False)
                self.assertEqual(prepared['state'], 'awaiting_review')
                self.assertEqual(prepared['report']['exportedRows'], 3)
                exported = session.confirm_export(prepared['review']['reviewId'],
                                                  {key: True for key in prepared['reviewChecks']})
                root = Path(exported['output'])
                rows = json.loads((root / 'messages.json').read_text(encoding='utf8'))
                self.assertEqual(len(rows), 3)
                quote = rows[2]['quote']
                self.assertEqual(quote['sourceLinkStatus'], 'matched_local_source')
                self.assertEqual(quote['sourceMessageId'], 'message.db|message_table|1')
                self.assertEqual(quote['message']['text'], '企微消息 https://example.invalid')
                self.assertEqual(len((root / 'messages.jsonl').read_text(encoding='utf8').splitlines()), 3)
                self.assertIn('synthetic reply', (root / 'messages.md').read_text(encoding='utf8'))

    def test_legacy_quote_survives_review_and_export(self):
        self.assert_review_and_export('legacy')

    def test_message_key_quote_survives_review_and_export(self):
        self.assert_review_and_export('message_key')


if __name__ == '__main__':
    unittest.main()
