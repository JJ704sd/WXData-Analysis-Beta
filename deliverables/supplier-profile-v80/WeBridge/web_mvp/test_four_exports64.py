"""Material gaps stay visible through the four actual export paths."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from offline_desktop import DesktopSession
from private_profiles import ProfileStore
from offline_content_status import forward_notice
from test_four_exports63 import CASES, CONTRACT_URL, REVOKE_SERVER_ID, assert_quality
from test_four_exports63 import source_fixture as previous_fixture, assert_new_content as assert_previous_content
from test_database_adapter import create_shard
from test_wecom_archive import pb

GAP_TITLE = 'FORWARD_GAP64'


def source_fixture(base, client, mode, count=2):
    config, chat, originals = previous_fixture(base, client, mode, count=count)
    source = Path(config['sourceRoot'])
    if client == 'wecom':
        # A known forwarded-record envelope with one unsupported child and one
        # supported text child preserves both the readable text and the gap.
        bad = pb(1, 22) + pb(5, 777) + pb(11, '第三方')
        good = (pb(1, 22) + pb(5, 2) + pb(11, '第三方') +
                pb(101, pb(1, pb(1, 0) + pb(2, pb(1, 'FORWARD_RETAINED64')))))
        record = pb(2, GAP_TITLE) + pb(1, bad) + pb(1, good)
        nested = pb(2, GAP_TITLE + '_NESTED') + pb(1,
            pb(1, 22) + pb(5, 4) + pb(11, '转发者') + pb(107, record))
        with closing(sqlite3.connect(source / 'message.db')) as db, db:
            for mid, body in ((240, record), (241, nested)):
                db.execute('INSERT INTO message_table VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (mid, mid + 10, mid, 11, chat, 4, 1700000000 + mid, 0, body, b'', b''))
    else:
        invalid = '<recordinfo><datalist>'
        outer = ('<recordinfo><title>' + GAP_TITLE + '_NESTED</title><datalist>'
                 '<dataitem datatype="17"><sourcename>第三方</sourcename>'
                 '<recordxml><![CDATA[' + invalid + ']]></recordxml></dataitem>'
                 '<dataitem datatype="1"><sourcename>第三方</sourcename>'
                 '<datadesc>FORWARD_RETAINED64</datadesc></dataitem>'
                 '</datalist></recordinfo>')
        def envelope(body):
            # Escape instead of nested CDATA, which cannot contain a CDATA end.
            from xml.sax.saxutils import escape
            return ('<msg><appmsg><title>' + GAP_TITLE + '</title><type>19</type>'
                    '<recorditem>' + escape(body) + '</recorditem></appmsg></msg>')
        create_shard(source, relative='message/message_12.db', group=chat, rows=[
            {'local': 240, 'type': 49, 'text': envelope(invalid)},
            {'local': 241, 'type': 49, 'text': envelope(outer)}])
    return config, chat, originals


def assert_new_content(test, root, rows, client):
    assert_previous_content(test, root, rows, client)
    from offline_images import message_nodes
    gap_rows = [row for row in rows if GAP_TITLE in row.get('text', '')]
    test.assertEqual(len(gap_rows), 2)
    gaps = [json.loads(line) for line in (root / 'content-gaps.jsonl').read_text(encoding='utf8').splitlines()]
    forwards = [entry for entry in gaps if 'forward_partial' in entry['issues']]
    test.assertTrue(all(any(entry['messageId'] == row['id'] for entry in forwards) for row in gap_rows))
    test.assertTrue(any(entry['nodePath'] for entry in forwards))
    reading = [(root / name).read_text(encoding='utf-8-sig') for name in
               ('messages.md', '业务阅读.txt', 'chat.html')]
    parts = '\n'.join(path.read_text(encoding='utf8') for path in (root / 'analysis').glob('part-*.md'))
    for row in gap_rows:
        for node, _, _ in message_nodes(row):
            note = forward_notice(node.get('record', {}))
            if note:
                for index, text in enumerate(reading + [parts]):
                    test.assertTrue(note in text.replace('\\_', '_'),
                                    f'{client}: forward notice missing from reading output {index}: {note}')
    for text in reading + [parts]:
        test.assertIn('FORWARD_RETAINED64', text)
    # This existing short PDF has native text plus a rich-text-only comment.
    # Its missing-content notice must reach the actual model reading material.
    test.assertIn('只有RC富文本而无Contents', parts)


class FourExports64Tests(unittest.TestCase):
    def test_material_gaps_and_historical_failures_across_four_exports(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-four64-') as folder:
            for client, mode in CASES:
                with self.subTest(client=client, mode=mode):
                    base = Path(folder) / (client + '-' + mode)
                    base.mkdir()
                    config, chat, originals = source_fixture(base, client, mode)
                    session = DesktopSession(base / 'profile', client=client, conversation_mode=mode)
                    session.open_source(config)
                    prepared = session.prepare_review(chat, copy_media=True, load_view=False)
                    root = Path(prepared['view']['viewRoot'])
                    rows = json.loads((root / 'messages.json').read_text(encoding='utf8'))
                    assert_quality(self, root, rows, client, originals)
                    assert_new_content(self, root, rows, client)
                    if mode == 'direct':
                        formal = session.confirm_export(prepared['review']['reviewId'],
                            {key: True for key in prepared['reviewChecks']})
                        request = ProfileStore(base / 'portraits').prepare(formal['output'])
                        test_material = '\n'.join(Path(path).read_text(encoding='utf8') for path in request['materialFiles'])
                        self.assertIn('只有RC富文本而无Contents', test_material)


if __name__ == '__main__':
    unittest.main()
