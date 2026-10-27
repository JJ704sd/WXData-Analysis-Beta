"""Synthetic Windows WeCom schema fixtures, kept separate from runtime data."""
import json
from contextlib import closing
import hashlib
import struct
from pathlib import Path
import sqlite3
import tempfile
import unittest
import io

from offline_desktop import DesktopSession
from wecom_content import typed_message
from database_snapshot import SnapshotError
from wecom_crypto import page_iv


def varint(value):
    data = bytearray()
    while value > 127:
        data.append((value & 127) | 128)
        value >>= 7
    return bytes(data) + bytes([value])


def pb(number, value):
    if isinstance(value, int):
        return varint(number << 3) + varint(value)
    value = value.encode() if isinstance(value, str) else value
    return varint((number << 3) | 2) + varint(len(value)) + value


def fixture(root):
    root.mkdir()
    with closing(sqlite3.connect(root / 'session.db')) as db, db:
        db.execute('create table conversation_table(id text,name text,roomname_remark text,last_message_time integer)')
        db.execute('insert into conversation_table values(?,?,?,?)', ('R:synthetic', '企微合成群', '', 1700000000))
        db.execute('create table conversation_user_table(conversation_id text,user_id integer,nick_name text)')
        db.execute('insert into conversation_user_table values(?,?,?)', ('R:synthetic', 11, '群内昵称'))
    with closing(sqlite3.connect(root / 'user.db')) as db, db:
        db.execute('create table user_table(id integer,name text,real_name text)')
        db.execute('insert into user_table values(11,?,?)', ('合成发送人', '合成发送人'))
    with closing(sqlite3.connect(root / 'message.db')) as db, db:
        db.execute('create table message_table(message_id integer,server_id integer,sequence integer,sender_id integer,conversation_id text,content_type integer,send_time integer,flag integer,content blob,extra_content blob,local_extra_content blob)')
        text = pb(1, pb(1, 0) + pb(2, pb(1, '企微消息 https://example.invalid')))
        forward = pb(1, pb(1, 11) + pb(2, 1700000000) + pb(5, 2) + pb(11, '合成发送人') + pb(101, text)) + pb(2, '合成转发')
        for index, kind, content in ((1, 2, text), (2, 4, forward)):
            db.execute('insert into message_table values(?,?,?,?,?,?,?,?,?,?,?)', (index, index + 10, index, 11, 'R:synthetic', kind, 1700000000 + index, 0, content, b'', b''))


class WeComArchiveTests(unittest.TestCase):
    def test_downloaded_converted_image_and_later_cache_are_visible_only_as_previews(self):
        from PIL import Image
        from offline_images import image_state
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source = base / 'account' / 'Data'
            source.parent.mkdir()
            fixture(source)
            cached = source.parent / 'Cache' / 'Image' / 'converted.jpg'
            cached.parent.mkdir(parents=True)
            stream = io.BytesIO()
            Image.new('RGB', (108, 144), '#335577').save(stream, format='JPEG')
            preview = stream.getvalue()
            content = pb(4, 123456) + pb(5, 216) + pb(6, 288) + pb(10, 'a' * 32)
            hint = pb(1, r'C:\old-computer\account\Cache\Image\converted.jpg')
            with closing(sqlite3.connect(source / 'message.db')) as db, db:
                db.execute('insert into message_table values(?,?,?,?,?,?,?,?,?,?,?)',
                    (3, 13, 3, 11, 'R:synthetic', 14, 1700000003, 0, content, b'', hint))
                entry = pb(5, 14) + pb(103, content) + pb(17, hint)
                nested = pb(1, pb(5, 4) + pb(107, pb(1, entry)))
                # Root hint must never be inherited by an unrelated forward.
                no_hint = pb(1, pb(5, 14) + pb(103, content))
                for mid, body in ((4, nested), (5, no_hint)):
                    db.execute('insert into message_table values(?,?,?,?,?,?,?,?,?,?,?)',
                        (mid, mid + 10, mid, 11, 'R:synthetic', 4, 1700000000 + mid, 0, body, b'', hint))
            session = DesktopSession(base / 'profile', client='wecom')
            session.open_source({'sourceRoot': str(source), 'includeMedia': True})
            first = session.export('R:synthetic')
            rows = json.loads((Path(first['output']) / 'messages.json').read_text(encoding='utf8'))
            self.assertEqual(rows[2]['asset']['status'], 'unavailable')
            cached.write_bytes(preview)
            # Same loaded archive: a previous miss must not freeze the media index.
            second = session.export('R:synthetic')
            dest = Path(second['output'])
            rows = json.loads((dest / 'messages.json').read_text(encoding='utf8'))
            asset = rows[2]['asset']
            self.assertEqual(asset['status'], 'ready')
            self.assertTrue(asset['previewOnly'])
            self.assertEqual(asset['previewVariant'], 'local_converted')
            self.assertEqual(image_state(asset)[0], 'local_preview')
            self.assertEqual((dest / asset['path']).read_bytes(), preview)
            self.assertEqual(second['report']['mediaSummary']['images']['localPreview'], 2)
            self.assertEqual(second['report']['mediaSummary']['images']['original'], 0)
            self.assertEqual(rows[3]['record']['items'][0]['record']['items'][0]['asset']['status'], 'ready')
            self.assertEqual(rows[4]['record']['items'][0]['asset']['status'], 'unavailable')
            self.assertIn('本地预览', (dest / 'messages.md').read_text(encoding='utf8'))
            self.assertIn('缓存', asset['matchBasis'])
            Image.new('RGB', (180, 100), '#335577').save(cached, format='JPEG')
            third = session.export('R:synthetic')
            rows = json.loads((Path(third['output']) / 'messages.json').read_text(encoding='utf8'))
            self.assertEqual(rows[2]['asset']['status'], 'unavailable')
            self.assertEqual(rows[2]['asset']['reasonCode'], 'linked_cache_not_validated')

    def test_renamed_file_with_stale_size_is_matched_by_content_not_its_name(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source = base / 'account' / 'Data'
            source.parent.mkdir()
            fixture(source)
            cache = source.parent / 'Cache' / 'File'
            cache.mkdir(parents=True)
            data = b'approved synthetic business attachment'
            (cache / 'client-generated-cache-id').write_bytes(data)
            content = pb(2, 'report.txt') + pb(4, len(data) + 9) + pb(10, hashlib.md5(data).hexdigest())
            with closing(sqlite3.connect(source / 'message.db')) as db, db:
                db.execute('insert into message_table values(?,?,?,?,?,?,?,?,?,?,?)',
                    (3, 13, 3, 11, 'R:synthetic', 15, 1700000003, 0, content, b'', b''))
            session = DesktopSession(base / 'profile', client='wecom')
            session.open_source({'sourceRoot': str(source), 'includeMedia': True})
            result = session.export('R:synthetic')
            rows = json.loads((Path(result['output']) / 'messages.json').read_text(encoding='utf8'))
            self.assertEqual(rows[-1]['asset']['status'], 'ready')
            self.assertEqual(rows[-1]['asset']['matchBasis'], 'content_md5')
            self.assertIn(data.decode(), rows[-1]['attachmentContent']['text'])

    def test_other_group_timestamp_unit_cannot_change_target_date_range(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source = base / 'source'
            fixture(source)
            with closing(sqlite3.connect(source / 'message.db')) as db, db:
                db.execute('insert into message_table values(?,?,?,?,?,?,?,?,?,?,?)',
                    (31, 31, 31, 11, 'R:unrelated', 2, 1700000000000, 0, b'', b'', b''))
            session = DesktopSession(base / 'profile', client='wecom')
            session.open_source({'sourceRoot': str(source)})
            result = session.parse_and_open(chat_name='企微合成群', start='2023-11-15', end='2023-11-15')
            rows = json.loads((Path(result['output']) / 'messages.json').read_text(encoding='utf8'))
            self.assertEqual(len(rows), 2)
            self.assertEqual(result['view']['total'], 2)
            self.assertTrue(all(row['time'].startswith('2023-11-15') for row in rows))

    def test_encrypted_source_requires_matching_wecom_key_and_remains_unchanged(self):
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source = base / 'source'
            fixture(source)
            key = bytes(range(16))
            before = {}
            for path in source.glob('*.db'):
                plain = path.read_bytes()
                size = int.from_bytes(plain[16:18], 'big')
                output = bytearray()
                for offset in range(0, len(plain), size):
                    number = offset // size + 1
                    page = plain[offset:offset + size]
                    pagekey = hashlib.md5(key + struct.pack('<I', number) + b'sAlT').digest()
                    encrypt = Cipher(algorithms.AES(pagekey), modes.CBC(page_iv(number))).encryptor()
                    body = page[16:] if number == 1 else page
                    encrypted = encrypt.update(body) + encrypt.finalize()
                    output.extend(bytes(8) + encrypted[:8] + page[16:24] + encrypted[8:] if number == 1 else encrypted)
                path.write_bytes(output)
                before[path] = hashlib.sha256(output).hexdigest()
            profile = base / 'profile'
            session = DesktopSession(profile, client='wecom')
            with self.assertRaises(SnapshotError):
                session.open_source({'sourceRoot': str(source)})
            self.assertFalse(session.settings_path.exists())
            self.assertEqual(list((profile / 'snapshots').iterdir()), [])
            keys = base / 'keys.json'
            keys.write_text(json.dumps({'wecomKeys': [{'database': '*', 'key_hex': key.hex()}]}))
            self.assertEqual(session.open_source({'sourceRoot': str(source), 'keyFile': str(keys)})['status'], 'ready')
            self.assertTrue(all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in before.items()))

    def test_mentions_and_nested_forward_use_wecom_fields(self):
        body = pb(1, pb(1, 5) + pb(2, pb(1, 11) + pb(2, '合成成员')))
        parsed = typed_message(body, 2)
        self.assertEqual(parsed['text'], '@合成成员')
        self.assertEqual(parsed['mentions']['ids'], ['11'])
        entry = pb(1, 11) + pb(2, 1700000000) + pb(5, 2) + pb(101, body)
        nested = pb(1, pb(5, 4) + pb(107, pb(1, entry)))
        parsed = typed_message(nested, 4)
        self.assertEqual(parsed['record']['items'][0]['record']['items'][0]['text'], '@合成成员')

    def test_selected_client_exports_preserved_messages_and_forward_tree(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source = base / 'source'
            fixture(source)
            session = DesktopSession(base / 'profile', client='wecom')
            info = session.open_source({'sourceRoot': str(source), 'selfId': '11'})
            self.assertEqual(info['groups'][0]['name'], '企微合成群')
            result = session.parse_and_open(chat_name='企微合成群')
            dest = Path(result['output'])
            rows = [json.loads(line) for line in (dest / 'messages.jsonl').read_text(encoding='utf8').splitlines()]
            self.assertEqual(json.loads((dest / 'messages.json').read_text(encoding='utf8')), rows)
            self.assertIn('企微消息', (dest / 'messages.md').read_text(encoding='utf8'))
            self.assertEqual(rows[0]['senderName'], '群内昵称')
            self.assertEqual(rows[0]['direction'], 'outgoing')
            self.assertEqual(rows[1]['record']['items'][0]['text'], rows[0]['text'])
            self.assertEqual(rows[0]['provenance']['table'], 'message_table')
            self.assertEqual(info['coverage']['client'], 'wecom')
            self.assertEqual(DesktopSession(base / 'profile', client='wecom').bootstrap()['status'], 'ready')

    def test_cross_client_profile_is_rejected_without_changing_saved_source(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source = base / 'source'
            fixture(source)
            DesktopSession(base / 'profile', client='wecom').open_source({'sourceRoot': str(source)})
            before = (base / 'profile/settings.local.json').read_bytes()
            result = DesktopSession(base / 'profile').bootstrap()
            self.assertEqual(result['status'], 'needs_source')
            self.assertEqual((base / 'profile/settings.local.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
