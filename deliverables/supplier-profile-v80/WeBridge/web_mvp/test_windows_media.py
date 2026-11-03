"""Synthetic attachments only; no access to real accounts or client memory."""
import hashlib
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
from types import SimpleNamespace

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from backend import Engine, Store
from database_adapter import DatabaseAdapter
from database_snapshot import prepare_snapshot
from media_container import dat_decode
from test_database_adapter import create_metadata, create_shard, GROUP, SELF, STAMP
from windows_media import WindowsMediaCache, child, descriptor, resolve_local, image_key

PNG = bytes.fromhex('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000b49444154789c636000020000050001a5f645400000000049454e44ae426082')


class WindowsMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'account/db_storage'
        self.source.mkdir(parents=True)
        self.snapshot = self.root / 'snapshot'
        self.snapshot.mkdir()
        self.config = {'sourceRoot': str(self.source)}
        (self.snapshot / 'hardlink').mkdir()
        with closing(sqlite3.connect(self.snapshot / 'hardlink/hardlink.db')) as db, db:
            db.execute('CREATE TABLE dir2id(username)')
            db.executemany('INSERT INTO dir2id VALUES(?)', [('2023-11',), (hashlib.md5(GROUP.encode()).hexdigest(),)])
            for kind in ('image', 'video', 'file'):
                db.execute('CREATE TABLE ' + kind + '_hardlink_info_v4(md5,file_name,dir1,dir2)')

    def row(self, kind, data, name='example.pdf'):
        digest = hashlib.md5(data).hexdigest()
        xml = f'<msg><appmsg><title>{name}</title><type>6</type><md5>{digest}</md5></appmsg></msg>' if kind == 'file' else f'<msg><{dict(image="img",video="videomsg")[kind]} md5="{digest}"/></msg>'
        return {'kind': kind, '_raw': xml, 'timestamp': STAMP, 'localId': 1, 'serverId': '10', 'media': {'filename': name}}

    def install(self, kind, data, name):
        folder = self.source.parent / 'msg' / kind / '2023-11'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name; path.write_bytes(data)
        with closing(sqlite3.connect(self.snapshot / 'hardlink/hardlink.db')) as db, db:
            db.execute('INSERT INTO ' + kind + '_hardlink_info_v4 VALUES(?,?,1,0)', (hashlib.md5(data).hexdigest(), name))
        return path

    def test_pdf_is_bound_to_message_checksum(self):
        data = b'%PDF-1.7\nSynthetic fixture'
        path = self.install('file', data, 'example.pdf')
        row = self.row('file', data)
        result, body = resolve_local(self.snapshot, self.config, GROUP, row)
        self.assertEqual(body, data)
        self.assertEqual(result['mime'], 'application/pdf')
        path.write_bytes(b'%PDF-1.7\nWrong message')
        result, body = resolve_local(self.snapshot, self.config, GROUP, row)
        self.assertIsNone(body)
        self.assertEqual(result['status'], 'pending')

    def test_mp4_is_not_a_cover(self):
        data = b'\x00\x00\x00\x18ftypmp42' + b'fixture'
        self.install('video', data, 'sample.mp4')
        result, body = resolve_local(self.snapshot, self.config, GROUP, self.row('video', data))
        self.assertEqual(body, data)
        self.assertEqual(result['mime'], 'video/mp4')
        self.assertFalse(result['previewOnly'])

    def test_cover_is_explicitly_preview_only(self):
        row = self.row('video', b'missing video')
        folder = self.source.parent / 'cache/2023-11/Message' / hashlib.md5(GROUP.encode()).hexdigest() / 'Thumb'
        folder.mkdir(parents=True)
        (folder / f'1_{STAMP}_thumb.jpg').write_bytes(PNG)
        result, _ = resolve_local(self.snapshot, self.config, GROUP, row)
        self.assertTrue(result['previewOnly'])

    def test_record_video_requires_exact_digest(self):
        data = b'\x00\x00\x00\x18ftypmp42record'
        row = {'kind': 'record', '_raw': '<msg><appmsg><recorditem><![CDATA[<recordinfo><datalist><dataitem datatype="4"><fullmd5>' + hashlib.md5(data).hexdigest() + '</fullmd5></dataitem></datalist></recordinfo>]]></recorditem></appmsg></msg>', 'timestamp': STAMP, 'localId': 1, 'serverId': '10'}
        folder = self.source.parent / 'msg/attach' / hashlib.md5(GROUP.encode()).hexdigest() / '2023-11/Rec/opaque/V'
        folder.mkdir(parents=True); (folder / '0.mp4').write_bytes(data)
        result, body = resolve_local(self.snapshot, self.config, GROUP, row, '0')
        self.assertEqual(body, data)
        (folder / '0.mp4').write_bytes(b'\x00\x00\x00\x18ftypmp42wrong!')
        self.assertIsNone(resolve_local(self.snapshot, self.config, GROUP, row, '0')[1])

    def test_nested_record_child_xml_resolves_image_and_checks_digest(self):
        from html import escape
        image = '<recordinfo><datalist><dataitem datatype="2"><fullmd5>' + hashlib.md5(PNG).hexdigest() + '</fullmd5></dataitem></datalist></recordinfo>'
        folder = self.source.parent / 'msg/attach' / hashlib.md5(GROUP.encode()).hexdigest() / '2023-11/Rec/opaque/Img'
        folder.mkdir(parents=True)
        path = folder / 'cached.dat'
        for inner in (image, escape(image), '<![CDATA[' + image + ']]>'):
            record = '<recordinfo><datalist><dataitem datatype="17"><recordxml>\n' + inner + '</recordxml></dataitem></datalist></recordinfo>'
            for outer in (record, escape(record), '<![CDATA[' + record.replace(']]>', ']]]]><![CDATA[>') + ']]>'):
                with self.subTest(inner=inner[:25], outer=outer[:25]):
                    row = {'kind': 'record', '_raw': '<msg><appmsg><recorditem>' + outer + '</recorditem></appmsg></msg>',
                           'timestamp': STAMP, 'localId': 1, 'serverId': '10'}
                    path.write_bytes(PNG)
                    metadata, body = resolve_local(self.snapshot, self.config, GROUP, row, '0.0')
                    self.assertEqual(body, PNG)
                    self.assertFalse(metadata['previewOnly'])
                    path.write_bytes(PNG + b'wrong')
                    self.assertIsNone(resolve_local(self.snapshot, self.config, GROUP, row, '0.0')[1])

    def test_paths_reject_traversal_absolute_and_alternate_stream(self):
        for value in ('../private', 'C:\\private', '/private', 'image.dat:secret'):
            with self.assertRaises(ValueError):
                child(self.root, value)

    def test_record_part_is_bounded(self):
        row = {'kind': 'record', '_raw': '<msg/>'}
        for part in ('-1', '0.0.0.0.0.0', '9999', '../a'):
            with self.assertRaises(ValueError):
                descriptor(row, part)

    def test_image_key_rejects_another_account(self):
        path = self.root / 'key.json'
        path.write_text(json.dumps({'keys': [{'sourceRoot': str(self.root / 'another'), 'aes_hex': '11' * 16}]}))
        with self.assertRaises(ValueError):
            image_key({**self.config, 'imageKeyFile': str(path)})

    def test_v2_roundtrip_and_wrong_key(self):
        key = b'0123456789abcdef'
        head, tail = PNG[:32], PNG[32:]
        padder = PKCS7(128).padder(); padded = padder.update(head) + padder.finalize()
        cipher = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        encrypted = b'\x07\x08V2\x08\x07' + struct.pack('<II', len(head), len(tail)) + b'\0' + cipher.update(padded) + cipher.finalize() + bytes(b ^ 73 for b in tail)
        self.assertEqual(dat_decode(encrypted, key), PNG)
        with self.assertRaises(ValueError):
            dat_decode(encrypted, b'badbadbadbadbad!')

    def test_optional_index_snapshot_is_explicit(self):
        create_shard(self.source)
        (self.source / 'hardlink').mkdir()
        with closing(sqlite3.connect(self.source / 'hardlink/hardlink.db')) as db, db:
            db.execute('CREATE TABLE fixture(value)')
        normal = prepare_snapshot(self.source, self.root / 'copies')
        extended = prepare_snapshot(self.source, self.root / 'copies', include_media=True)
        self.assertFalse((normal['root'] / 'hardlink/hardlink.db').exists())
        self.assertTrue((extended['root'] / 'hardlink/hardlink.db').exists())

    def test_cache_checks_subscription_and_revision(self):
        data = b'%PDF-1.7\nSynthetic fixture'
        self.install('file', data, 'example.pdf')
        create_metadata(self.snapshot)
        create_shard(self.snapshot, rows=[{'type': 49, 'text': self.row('file', data)['_raw']}])
        adapter = DatabaseAdapter(self.snapshot, self_id=SELF)
        engine = Engine(Store(self.root / 'state.sqlite'), adapter)
        engine.refresh_connection(force=True)
        import threading
        engine.database_service = SimpleNamespace(lock=threading.RLock(), config=self.config)
        cache = WindowsMediaCache(engine, self.root / 'cache')
        row = adapter.call('messages', account=engine.account, groupId=GROUP)['messages'][0]
        self.assertNotIn('_raw', row)
        with self.assertRaises(ValueError):
            cache.resolve(engine.account, GROUP, row['id'])
        engine.set_watched(engine.account, [GROUP])
        result = cache.resolve(engine.account, GROUP, row['id'])
        self.assertNotIn('digest', result)
        self.assertEqual(cache.get(result['assetId'], engine.account)['path'].read_bytes(), data)
        engine.set_watched(engine.account, [])
        with self.assertRaises(ValueError):
            cache.get(result['assetId'], engine.account)
        engine.set_watched(engine.account, [GROUP])
        adapter._state['info']['revision'] = 'new'
        self.assertEqual(cache.get(result['assetId'], engine.account)['path'].read_bytes(), data)
        cache.entries[result['assetId']]['fingerprint'] = 'old-message'
        with self.assertRaises(ValueError):
            cache.get(result['assetId'], engine.account)


if __name__ == '__main__':
    unittest.main()
