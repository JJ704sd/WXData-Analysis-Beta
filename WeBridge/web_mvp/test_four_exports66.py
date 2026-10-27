"""Nested unread documents agree across all exports and private statistics."""
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from xml.sax.saxutils import escape

from offline_desktop import DesktopSession
from offline_images import message_nodes
from private_profiles import ProfileStore
from private_insights import search_messages, summarize, write_report
from test_four_exports65 import CASES, CONTRACT_URL, REVOKE_SERVER_ID
from test_four_exports65 import source_fixture as previous_fixture, assert_quality as previous_quality
from test_four_exports65 import assert_new_content as previous_content
from test_database_adapter import create_shard
from test_offline_documents import pdf_file
from test_wecom_archive import pb

FORWARD_TITLE = 'NESTED_UNREAD66'


def source_fixture(base, client, mode, count=2):
    config, chat, originals = previous_fixture(base, client, mode, count=count)
    path = Path(config['supplementRoot']) / 'nested-unread66.pdf'
    pdf_file(path, blank=True)
    raw = path.read_bytes()
    originals[hashlib.sha256(raw).hexdigest()] = raw
    md5 = hashlib.md5(raw).hexdigest()
    source = Path(config['sourceRoot'])
    if client == 'wecom':
        payload = pb(2, path.name) + pb(4, len(raw)) + pb(10, md5)
        nested = pb(1, pb(1, 22) + pb(2, 1700000001) + pb(5, 20) +
                    pb(11, '合成第三方') + pb(103, payload)) + pb(2, FORWARD_TITLE)
        with closing(sqlite3.connect(source / 'message.db')) as db, db:
            for mid in (270, 271):
                db.execute('INSERT INTO message_table VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (mid, mid + 10, mid, 11, chat, 4, 1700000000 + mid, 0, nested, b'', b''))
    else:
        nested = ('<recordinfo><title>' + FORWARD_TITLE + '</title><datalist>'
            '<dataitem datatype="8"><sourcename>合成第三方</sourcename><datatitle>' + path.name +
            '</datatitle><datasize>' + str(len(raw)) + '</datasize><fullmd5>' + md5 +
            '</fullmd5></dataitem></datalist></recordinfo>')
        envelope = '<msg><appmsg><title>' + FORWARD_TITLE + '</title><type>19</type><recorditem>' + escape(nested) + '</recorditem></appmsg></msg>'
        create_shard(source, relative='message/message_14.db', group=chat, rows=[
            {'local': mid, 'type': 49, 'text': envelope} for mid in (270, 271)])
    return config, chat, originals


def _nested_documents(rows):
    return [(row, node, path) for row in rows if row.get('text') == FORWARD_TITLE
            for node, _trail, path in message_nodes(row) if node.get('attachmentContent')]


def assert_quality(test, root, rows, client, originals):
    nested = _nested_documents(rows)
    test.assertEqual(len(nested), 2)
    nested_hashes = {node['attachmentContent']['sha256'] for _row, node, _path in nested}
    previous_quality(test, root, rows, client, {key: raw for key, raw in originals.items() if key not in nested_hashes})
    for row, node, path in nested:
        document = node['attachmentContent']
        test.assertEqual(path, (('record', 0),))
        test.assertEqual(document['source']['rootMessageId'], row['id'])
        test.assertEqual(document['source']['context'], ['转发第 1 条'])
        test.assertEqual(document['extractionStatus'], 'no_text')
        test.assertEqual(document['text'], '')
        test.assertEqual((root / node['asset']['path']).read_bytes(), originals[document['sha256']])


def assert_coverage(test, root, source):
    result = summarize(source)
    coverage = result['contentCoverage']
    gaps = [json.loads(line) for line in (root / 'content-gaps.jsonl').read_text(encoding='utf8').splitlines()]
    codes = Counter(code for entry in gaps for code in entry['issues'])
    test.assertEqual(coverage['issueCounts'], dict(codes))
    test.assertEqual(coverage['gapOccurrences'], len(gaps))
    test.assertEqual(coverage['messagesWithGaps'], len({entry['messageId'] for entry in gaps}))
    test.assertEqual(coverage['documents']['noText'], 2)
    test.assertEqual(coverage['documents']['observed'] - coverage['documents']['withText'], 2)
    test.assertEqual(coverage['documents']['unknownStatus'], 0)
    test.assertEqual(coverage['observedOccurrences'], sum(1 for row in source['messages'] for _node in message_nodes(row)))
    test.assertEqual(result['totalMessages'], len(source['messages']))
    hits = search_messages(source, '37/KG')
    test.assertEqual(hits['total'], 2)
    for item in hits['items']:
        test.assertEqual(item['matchReviewText'], '附件内容待核对')
    return result


def assert_new_content(test, root, rows, client):
    previous_content(test, root, rows, client)
    assert_coverage(test, root, {'messages': rows})


class FourExports66Tests(unittest.TestCase):
    def test_nested_coverage_and_review_wording_across_four_exports(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-four66-') as folder:
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
                        source = ProfileStore(base / 'portraits').inspect_export(formal['output'])
                        summary = assert_coverage(self, Path(formal['output']), source)
                        files = write_report(summary, base / 'stats')
                        self.assertEqual(json.loads(Path(files['jsonFile']).read_text(encoding='utf8'))['contentCoverage'], summary['contentCoverage'])


if __name__ == '__main__':
    unittest.main()
