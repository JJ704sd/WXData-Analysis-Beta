"""Keep WeCom text gaps and verified Emotion originals through review and reports."""
import base64
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from PIL import Image
from offline_daily import build_daily_report
from offline_desktop import DesktopSession
from test_office_accuracy72 import office_source
from test_wecom_archive import pb
from test_wecom_text_gaps73 import text_block


GAP_TEXT = '金额10[文本片段未完整解析]0元'


def source73(base, client, mode):
    config, chat, originals = office_source(base, client, mode)
    payload = text_block('金额10') + text_block(b'\xff') + text_block('0元')
    stream = io.BytesIO()
    Image.new('RGB', (3, 2), '#335577').save(stream, format='PNG')
    picture = stream.getvalue()
    if client == 'wecom':
        source = Path(config['sourceRoot'])
        account = source.parent if source.name == 'Data' else source
        emotion = account / 'Emotion'
        emotion.mkdir()
        (emotion / 'opaque73').write_bytes(picture)
        media = pb(4, len(picture) + 1) + pb(10, hashlib.md5(picture).hexdigest())
        with closing(sqlite3.connect(source / 'message.db')) as db, db:
            for mid, kind, body in ((401, 2, payload), (402, 101, media)):
                db.execute('INSERT INTO message_table VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (mid, mid + 10, mid, 11, chat, kind, 1700000000 + mid, 0, body, b'', b''))
    return config, chat, originals, payload, picture


class WeComGapDelivery73Tests(unittest.TestCase):
    def test_group_and_direct_keep_gaps_raw_bytes_media_and_daily_notice(self):
        for mode in ('group', 'direct'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(prefix='synthetic-gaps73-') as folder:
                base = Path(folder)
                config, chat, _originals, payload, picture = source73(base, 'wecom', mode)
                session = DesktopSession(base / 'profile', client='wecom', conversation_mode=mode)
                session.open_source(config)
                parsed = session.prepare_review(chat, copy_media=True, load_view=False)
                self.assertEqual(parsed['state'], 'awaiting_review')
                before = (Path(parsed['view']['viewRoot']) / 'messages.json').read_bytes()
                result = session.confirm_export(parsed['review']['reviewId'],
                    {key: True for key in parsed['reviewChecks']}, 'synthetic text-gap review', chat=chat)
                self.assertEqual(result['humanReview']['status'], 'confirmed')
                output = Path(result['output'])
                self.assertEqual((output / 'messages.json').read_bytes(), before)
                rows = json.loads(before)
                gap = next(row for row in rows if row['localId'] == 401)
                self.assertEqual(gap['text'], GAP_TEXT)
                self.assertEqual(gap['parseStatus'], 'unsupported_text_blocks')
                self.assertEqual(gap['decodeStatus'], 'partially_supported')
                self.assertEqual(base64.b64decode(gap['rawColumns']['content']['data']), payload)
                self.assertEqual(gap['provenance']['localId'], 401)
                corpus = [json.loads(line) for line in (output / 'analysis/corpus.jsonl').read_text(encoding='utf-8').splitlines()]
                reading = next(row for row in corpus if row['id'] == gap['id'])
                self.assertEqual(reading['text'], GAP_TEXT)
                self.assertEqual(reading.get('parseStatus'), 'unsupported_text_blocks')
                image = next(row for row in rows if row['localId'] == 402)
                self.assertEqual(image['asset']['status'], 'ready')
                self.assertEqual((output / image['asset']['path']).read_bytes(), picture)
                daily = build_daily_report([output], client='wecom', day=gap['time'][:10], output=base / 'daily')
                report = json.loads(Path(daily['jsonFile']).read_text(encoding='utf-8'))
                evidence = [json.loads(line) for line in (base / 'daily/evidence.jsonl').read_text(encoding='utf-8').splitlines()]
                item = next(row for row in evidence if row['messageId'] == gap['id'])
                self.assertIn('message_partial', item['issues'])
                self.assertTrue(any(row['evidenceId'] == item['evidenceId'] for row in report['contentGaps']))
                self.assertIn(GAP_TEXT, Path(daily['htmlFile']).read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
