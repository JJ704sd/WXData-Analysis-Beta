"""Synthetic quality checks for the streaming snapshot optimizations."""
from contextlib import closing
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

import database_snapshot
import wecom_snapshot
from wecom_crypto import page_iv


def aes_encrypt(key, iv, payload):
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(payload) + encryptor.finalize()


def wechat_fixture(root):
    source = root / 'wechat'
    source.mkdir()
    database = source / 'message_0.db'
    with closing(sqlite3.connect(database)) as connection:
        connection.execute('PRAGMA page_size=4096')
        connection.execute('VACUUM')
    empty = bytearray(database.read_bytes())
    empty[20] = 80
    empty[105:107] = (4016).to_bytes(2, 'big')
    database.write_bytes(empty)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute('CREATE TABLE samples(id INTEGER PRIMARY KEY, value TEXT)')
        connection.executemany('INSERT INTO samples(value) VALUES (?)',
                               ((f'{number}:合成数据' + 'x' * 1800,) for number in range(700)))
        connection.commit()
        assert connection.execute('PRAGMA quick_check').fetchone() == ('ok',)
    original = database.read_bytes()
    key, salt = bytes(range(32)), bytes(range(16))
    mac_key = hashlib.pbkdf2_hmac('sha512', key, bytes(b ^ 0x3a for b in salt), 2, 32)
    expected, encrypted = bytearray(), bytearray()
    for offset in range(0, len(original), 4096):
        number = offset // 4096 + 1
        page = original[offset:offset + 4096]
        start = 16 if number == 1 else 0
        iv = number.to_bytes(16, 'little')
        encoded = aes_encrypt(key, iv, page[start:4016]) + iv
        mac = hmac.new(mac_key, encoded + number.to_bytes(4, 'little'), hashlib.sha512).digest()
        encrypted.extend((salt if start else b'') + encoded + mac)
        expected.extend(page[:4016] + iv + mac)
    database.write_bytes(encrypted)
    keys = root / 'wechat-keys.json'
    keys.write_text(json.dumps({'keys': [
        {'key_hex': bytes(32).hex(), 'salt_hex': salt.hex()},
        {'key_hex': key.hex(), 'salt_hex': salt.hex()}]}))
    return source, keys, bytes(expected)


def wecom_fixture(root, size=4096):
    source = root / ('wecom-' + str(size))
    source.mkdir()
    for name in wecom_snapshot.CORE:
        with closing(sqlite3.connect(source / name)) as connection:
            connection.execute(f'PRAGMA page_size={size}')
            connection.execute('CREATE TABLE samples(id INTEGER PRIMARY KEY, value BLOB)')
            count = 300 if name == 'message.db' else 2
            connection.executemany('INSERT INTO samples(value) VALUES (?)', ((bytes(4096),) for _ in range(count)))
            connection.commit()
    return source


def encrypt_wecom(source):
    key = bytes(range(16))
    for path in source.glob('*.db'):
        original = path.read_bytes()
        size = int.from_bytes(original[16:18], 'big')
        size = 65536 if size == 1 else size
        encrypted = bytearray()
        for offset in range(0, len(original), size):
            number = offset // size + 1
            page = original[offset:offset + size]
            encrypted.extend(wecom_encrypted_page(page, number, key))
        path.write_bytes(encrypted)
    keys = source.parent / 'wecom-keys.json'
    keys.write_text(json.dumps({'wecomKeys': [
        {'database': '*', 'key_hex': bytes(16).hex()},
        {'database': '*', 'key_hex': key.hex()}]}))
    return keys


def wecom_encrypted_page(page, number, key):
    pagekey = hashlib.md5(key + struct.pack('<I', number) + b'sAlT').digest()
    payload = aes_encrypt(pagekey, page_iv(number), page[16:] if number == 1 else page)
    return bytes(8) + payload[:8] + page[16:24] + payload[8:] if number == 1 else payload


def wal_checksum(data, a=0, b=0, little=True):
    for x, y in struct.iter_unpack('<II' if little else '>II', data):
        a = (a + x + b) & 0xffffffff
        b = (b + y + a) & 0xffffffff
    return a, b


class GroupSnapshotPerformanceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='group42-snapshot-test-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.output = self.root / 'output'

    def test_wechat_multimegabyte_authenticated_pages_match_every_output_byte(self):
        source, keys, expected = wechat_fixture(self.root)
        self.assertGreater(len(expected), 1024 * 1024)
        before = (source / 'message_0.db').read_bytes()
        result = database_snapshot.prepare_snapshot(source, self.output, keys)
        target = result['root'] / 'message_0.db'
        self.assertEqual(target.read_bytes(), expected)
        self.assertEqual((source / 'message_0.db').read_bytes(), before)
        with closing(sqlite3.connect(target)) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM samples').fetchone(), (700,))
            self.assertEqual(connection.execute('SELECT value FROM samples ORDER BY id LIMIT 1').fetchone(),
                             ('0:合成数据' + 'x' * 1800,))

    def test_wechat_late_corruption_and_truncation_preserve_previous_snapshot(self):
        source, keys, _ = wechat_fixture(self.root)
        database = source / 'message_0.db'
        original = database.read_bytes()
        previous = database_snapshot.prepare_snapshot(source, self.output, keys)['root']
        previous_bytes = (previous / database.name).read_bytes()
        corrupt = bytearray(original)
        corrupt[1024 * 1024 + 100] ^= 1
        for payload, message in ((corrupt, 'HMAC'), (original[:-1], '4096')):
            with self.subTest(message=message):
                database.write_bytes(payload)
                with self.assertRaisesRegex(database_snapshot.SnapshotError, message):
                    database_snapshot.prepare_snapshot(source, self.output, keys)
                self.assertEqual(list(self.output.iterdir()), [previous])
                self.assertEqual((previous / database.name).read_bytes(), previous_bytes)
                self.assertEqual(database.read_bytes(), payload)

    def test_wecom_page_sizes_plain_and_encrypted_have_identical_complete_output(self):
        for size in (512, 4096, 65536):
            with self.subTest(page_size=size):
                source = wecom_fixture(self.root, size)
                expected = {path.name: path.read_bytes() for path in source.glob('*.db')}
                self.assertGreater(len(expected['message.db']), 1024 * 1024)
                plain = wecom_snapshot.prepare_snapshot(source, self.output)['root']
                self.assertEqual({p.name: p.read_bytes() for p in plain.glob('*.db')}, expected)
                keys = encrypt_wecom(source)
                before = {path.name: path.read_bytes() for path in source.glob('*.db')}
                decrypted = wecom_snapshot.prepare_snapshot(source, self.output, keys)['root']
                self.assertEqual({p.name: p.read_bytes() for p in decrypted.glob('*.db')}, expected)
                self.assertEqual({p.name: p.read_bytes() for p in source.glob('*.db')}, before)

    def test_wecom_truncated_plain_or_encrypted_does_not_publish_partial_snapshot(self):
        source = wecom_fixture(self.root)
        database = source / 'message.db'
        for encrypted in (False, True):
            keys = encrypt_wecom(source) if encrypted else None
            original = database.read_bytes()
            previous = wecom_snapshot.prepare_snapshot(source, self.output, keys)['root']
            previous_bytes = (previous / database.name).read_bytes()
            existing = set(self.output.iterdir())
            database.write_bytes(original[:-1])
            with self.assertRaisesRegex(database_snapshot.SnapshotError, '不完整页'):
                wecom_snapshot.prepare_snapshot(source, self.output, keys)
            self.assertEqual(set(self.output.iterdir()), existing)
            self.assertEqual((previous / database.name).read_bytes(), previous_bytes)
            database.write_bytes(original)

    def test_wecom_damaged_encrypted_btree_still_fails_integrity_check(self):
        source = wecom_fixture(self.root)
        keys = encrypt_wecom(source)
        database = source / 'message.db'
        payload = bytearray(database.read_bytes())
        payload[4096:8192] = bytes(4096)
        database.write_bytes(payload)
        with self.assertRaises(database_snapshot.SnapshotError):
            wecom_snapshot.prepare_snapshot(source, self.output, keys)
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertEqual(database.read_bytes(), payload)

    def test_wecom_same_size_change_with_unchanged_metadata_is_rejected(self):
        source = wecom_fixture(self.root)
        signatures = {p.name: wecom_snapshot._signature(p) for p in source.glob('*.db')}
        original_copy = wecom_snapshot._copy_file

        def mutate_after_copy(original, destination):
            digest = original_copy(original, destination)
            if original.name == 'message.db':
                with original.open('r+b') as handle:
                    handle.seek(-1, 2)
                    value = handle.read(1)
                    handle.seek(-1, 2)
                    handle.write(bytes([value[0] ^ 1]))
            return digest

        with patch.object(wecom_snapshot, '_signature', side_effect=lambda p: signatures[p.name]), \
                patch.object(wecom_snapshot, '_copy_file', side_effect=mutate_after_copy):
            with self.assertRaisesRegex(database_snapshot.SnapshotError, '持续变化'):
                wecom_snapshot.prepare_snapshot(source, self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_wecom_committed_wal_plain_and_encrypted_preserves_rows(self):
        for encrypted in (False, True):
            with self.subTest(encrypted=encrypted):
                case = self.root / ('encrypted-wal' if encrypted else 'plain-wal')
                case.mkdir()
                source = wecom_fixture(case)
                database = source / 'message.db'
                wal = Path(str(database) + '-wal')
                with closing(sqlite3.connect(database)) as connection:
                    connection.execute('PRAGMA journal_mode=WAL')
                    connection.execute('PRAGMA wal_autocheckpoint=0')
                    connection.execute('INSERT INTO samples(value) VALUES (?)', (b'committed-in-wal',))
                    connection.commit()
                    main_bytes, wal_bytes = database.read_bytes(), wal.read_bytes()
                    expected = connection.execute('SELECT id,value FROM samples ORDER BY id').fetchall()
                database.write_bytes(main_bytes)
                wal.write_bytes(wal_bytes)
                keys = None
                if encrypted:
                    keys = encrypt_wecom(source)
                    encoded = bytearray(wal_bytes)
                    size = int.from_bytes(encoded[8:12], 'big')
                    a, b = struct.unpack('>II', encoded[24:32])
                    little = int.from_bytes(encoded[:4], 'big') == 0x377f0682
                    for offset in range(32, len(encoded), size + 24):
                        number = int.from_bytes(encoded[offset:offset + 4], 'big')
                        page = wal_bytes[offset + 24:offset + 24 + size]
                        encoded[offset + 24:offset + 24 + size] = wecom_encrypted_page(page, number, bytes(range(16)))
                        a, b = wal_checksum(encoded[offset:offset + 8] + encoded[offset + 24:offset + 24 + size], a, b, little)
                        encoded[offset + 16:offset + 24] = struct.pack('>II', a, b)
                    wal.write_bytes(encoded)
                before = database.read_bytes(), wal.read_bytes()
                result = wecom_snapshot.prepare_snapshot(source, self.output, keys)
                with closing(sqlite3.connect(result['root'] / 'message.db')) as connection:
                    self.assertEqual(connection.execute('SELECT id,value FROM samples ORDER BY id').fetchall(), expected)
                report = next(row for row in result['source']['databases'] if row['path'] == 'message.db')
                self.assertGreater(report['wal']['committedFrames'], 0)
                self.assertEqual((database.read_bytes(), wal.read_bytes()), before)


if __name__ == '__main__':
    unittest.main()
