"""Four entry-point fixtures with spreadsheet and chart-only presentation coverage."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from offline_desktop import DesktopSession
from test_four_exports55 import CASES, source_fixture as previous_fixture, assert_quality, TAIL
from test_four_exports55 import assert_new_content as assert_previous_content
from test_database_adapter import create_shard
from test_presentation_charts56 import _bar_chart_xml, _chart_xml, _pptx
from test_spreadsheet_semantics56 import _xlsx
from test_wecom_archive import pb

XLSX_NAME = 'styled-semantics.xlsx'
PPTX_NAME = 'chart-only-quarterly.pptx'


def _attachment_row(client, path, mid, chat):
    raw = path.read_bytes()
    digest = hashlib.md5(raw).hexdigest()
    if client == 'wecom':
        return mid, pb(2, path.name) + pb(4, len(raw)) + pb(10, digest)
    return mid, '<msg><appmsg><title>' + path.name + '</title><type>6</type><appattach><totallen>' + str(len(raw)) + '</totallen><md5>' + digest + '</md5></appattach></appmsg></msg>'


def source_fixture(base, client, mode, count=2):
    config, chat, originals = previous_fixture(base, client, mode, count=count)
    original_root = Path(config['supplementRoot'])

    xlsx = original_root / XLSX_NAME
    xlsx.write_bytes(_xlsx(values=[
        ('A1', '45292.5', 1, None),
        ('B1', '0.125', 2, None),
        ('C1', '123', 3, None),
    ], styles='''
      <styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
        <numFmts count="2">
          <numFmt numFmtId="164" formatCode="yyyy-mm-dd hh:mm"/>
          <numFmt numFmtId="166" formatCode="000000"/>
        </numFmts>
        <cellXfs count="4">
          <xf numFmtId="0"/><xf numFmtId="164"/><xf numFmtId="10"/><xf numFmtId="166"/>
        </cellXfs>
      </styleSheet>'''))
    pptx = original_root / PPTX_NAME
    _pptx(pptx, _chart_xml(_bar_chart_xml()))
    for path in (xlsx, pptx):
        raw = path.read_bytes()
        originals[hashlib.sha256(raw).hexdigest()] = raw

    source = Path(config['sourceRoot'])
    if client == 'wecom':
        with closing(sqlite3.connect(source / 'message.db')) as db, db:
            for offset, path in ((40, xlsx), (50, pptx)):
                for mid in (offset, offset + 1):
                    _, body = _attachment_row(client, path, mid, chat)
                    db.execute('INSERT INTO message_table VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                               (mid, mid + 10, mid, 11, chat, 102, 1700000000 + mid, 0,
                                body, b'', b''))
    else:
        rows = []
        for offset, path in ((40, xlsx), (50, pptx)):
            for mid in (offset, offset + 1):
                _, body = _attachment_row(client, path, mid, chat)
                rows.append({'local': mid, 'type': 49, 'text': body})
        create_shard(source, relative='message/message_3.db', group=chat, rows=rows)
    return config, chat, originals


def assert_new_content(test, root, rows):
    assert_previous_content(test, root, rows)
    corpus = (root / 'analysis/corpus.jsonl').read_text(encoding='utf8')
    for filename in (XLSX_NAME, PPTX_NAME):
        matches = [r for r in rows if r.get('text') == filename]
        test.assertEqual(len(matches), 2)
        test.assertTrue(all(r.get('attachmentContent') for r in matches))
    xlsx_text = next(r['attachmentContent']['text'] for r in rows if r.get('text') == XLSX_NAME)
    test.assertIn('A1=2024-01-01T12:00:00', xlsx_text)
    test.assertIn('B1=12.50%', xlsx_text)
    test.assertIn('C1=000123', xlsx_text)
    pptx_text = next(r['attachmentContent']['text'] for r in rows if r.get('text') == PPTX_NAME)
    test.assertIn('值：10', pptx_text)
    test.assertIn('季度销售额', pptx_text)
    test.assertIn('A1=2024-01-01T12:00:00', corpus)
    test.assertIn('B1=12.50%', corpus)
    test.assertIn('C1=000123', corpus)
    test.assertIn('值：10', corpus)


class FourExports56Tests(unittest.TestCase):
    def test_new_content_and_old_failures_across_four_exports(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-four56-') as folder:
            for client, mode in CASES:
                with self.subTest(client=client, mode=mode):
                    base = Path(folder) / (client + '-' + mode)
                    base.mkdir()
                    config, chat, originals = source_fixture(base, client, mode)
                    session = DesktopSession(base / 'profile', client=client, conversation_mode=mode)
                    session.open_source(config)
                    prepared = session.prepare_review(chat, copy_media=True, load_view=False)
                    output = Path(prepared['view']['viewRoot'])
                    rows = json.loads((output / 'messages.json').read_text(encoding='utf8'))
                    assert_quality(self, output, rows, client, originals)
                    assert_new_content(self, output, rows)


if __name__ == '__main__':
    unittest.main()
