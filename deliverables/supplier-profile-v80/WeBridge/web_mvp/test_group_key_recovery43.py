"""Synthetic recovery compatibility; no real owner or process is accessed."""
from contextlib import closing
from contextlib import ExitStack
import ctypes
import importlib
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from portable_key_recovery import recover_key_file
from offline_chat import ArchiveError
from database_snapshot import SnapshotError
from test_acquire_database_keys import encrypted_page
from test_group_snapshot_performance42 import wecom_encrypted_page
import wecom_key_recovery
import wecom_snapshot


class GroupKeyRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.acquire = importlib.import_module('acquire_database_keys')
        temporary = tempfile.TemporaryDirectory(prefix='group43-key-recovery-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.source = self.base / 'selected' / 'db_storage'
        self.source.mkdir(parents=True)
        self.directory = self.base / 'profile'
        self.key, self.salt = b'K' * 32, b'S' * 16
        (self.source / 'message_0.db').write_bytes(encrypted_page(self.key, self.salt))
        self.candidates = [(self.key.hex(), self.salt.hex())]

    def dependencies(self, candidates=None):
        return types.SimpleNamespace(owner=Mock(return_value=(101, 123)),
                                     scan=Mock(return_value=self.candidates if candidates is None else candidates))

    def collect_without_process(self, source, include_media, timeout, budget, include_voice=False):
        return self.acquire.collect_keys(source, self.dependencies(), include_media=include_media,
                                    include_voice=include_voice, timeout=timeout, max_scan_bytes=budget)

    def test_selected_voice_database_is_authenticated_and_saved_with_message_key(self):
        voice_key, voice_salt = b'V' * 32, b'T' * 16
        (self.source / 'media_0.db').write_bytes(encrypted_page(voice_key, voice_salt))
        self.candidates.append((voice_key.hex(), voice_salt.hex()))
        with patch('portable_key_recovery._collect', side_effect=self.collect_without_process):
            result = recover_key_file(self.source, self.directory, allow_process_read=True, include_voice=True)
        rows = json.loads(Path(result['keyFile']).read_text())['keys']
        self.assertCountEqual([(row['key_hex'], row['salt_hex']) for row in rows], self.candidates)
        self.assertEqual((result['databaseCount'], result['matchedCount']), (2, 2))

    def test_unselected_voice_database_needs_no_key_and_is_not_an_owner_target(self):
        (self.source / 'media_0.db').write_bytes(b'unselected voice fixture')
        dependencies = self.dependencies()
        result = self.acquire.collect_keys(self.source, dependencies)
        self.assertEqual((result['databaseCount'], result['matchedCount']), (1, 1))
        self.assertEqual(dependencies.owner.call_args.args[0], (self.source / 'message_0.db',))

    def test_wrong_selected_voice_key_prevents_key_file_publication(self):
        (self.source / 'media_0.db').write_bytes(encrypted_page(b'V' * 32, b'T' * 16))
        with patch('portable_key_recovery._collect', side_effect=self.collect_without_process):
            with self.assertRaises(ArchiveError):
                recover_key_file(self.source, self.directory, allow_process_read=True, include_voice=True)
        self.assertFalse(self.directory.exists())

    def plaintext(self, path):
        with closing(sqlite3.connect(path)) as db:
            db.execute('CREATE TABLE samples(value TEXT)')
            db.execute("INSERT INTO samples VALUES ('synthetic plaintext')")
            db.commit()

    def test_mixed_plain_and_encrypted_source_recovers_only_required_key(self):
        self.plaintext(self.source / 'session.db')
        with patch('portable_key_recovery._collect', side_effect=self.collect_without_process):
            result = recover_key_file(self.source, self.directory, allow_process_read=True)
        rows = json.loads(Path(result['keyFile']).read_text())['keys']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['salt_hex'], self.salt.hex())
        self.assertEqual((result['databaseCount'], result['matchedCount'], result['plaintextCount']), (2, 1, 1))

    def test_plaintext_source_never_queries_owner_or_scans_process(self):
        path = self.source / 'message_0.db'
        path.unlink()
        self.plaintext(path)
        dependencies = self.dependencies()
        with self.assertRaises(self.acquire.AcquireError) as caught:
            self.acquire.collect_keys(self.source, dependencies)
        self.assertEqual(caught.exception.code, 'source_not_encrypted')
        dependencies.owner.assert_not_called()
        dependencies.scan.assert_not_called()
        with patch('portable_key_recovery._collect') as collect:
            with self.assertRaises(ArchiveError):
                recover_key_file(self.source, self.directory, allow_process_read=True)
        collect.assert_not_called()
        self.assertFalse(self.directory.exists())

    def test_plaintext_neighbor_does_not_allow_an_unmatched_encrypted_database(self):
        self.plaintext(self.source / 'session.db')
        self.candidates = [(bytes(32).hex(), self.salt.hex())]
        with patch('portable_key_recovery._collect', side_effect=self.collect_without_process):
            with self.assertRaisesRegex(ArchiveError, '校验'):
                recover_key_file(self.source, self.directory, allow_process_read=True)
        self.assertFalse(self.directory.exists())


class WeComDatabaseKeyRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='group43-wecom-key-recovery-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.source = self.base / 'selected'
        self.source.mkdir()
        self.expected, self.pages, self.keys = {}, {}, {}
        for name in (*wecom_snapshot.CORE, 'file.db'):
            path = self.source / name
            with closing(sqlite3.connect(path)) as db:
                db.execute('CREATE TABLE samples(value TEXT)')
                db.execute('INSERT INTO samples VALUES (?)', (name,))
                db.commit()
            plain = path.read_bytes()
            self.expected[name] = plain
            if name == 'file.db':
                continue
            key = b'A' * 16 if name != 'user.db' else b'B' * 16
            size = int.from_bytes(plain[16:18], 'big')
            encoded = b''.join(wecom_encrypted_page(plain[offset:offset + size], offset // size + 1, key)
                               for offset in range(0, len(plain), size))
            path.write_bytes(encoded)
            self.pages[name], self.keys[name] = encoded[:size], key

    def strings(self, materials):
        begin = 0x10000
        pointers = {0x20000 + number * 0x10000: material for number, material in enumerate(materials)}
        heap = b''.join(struct.pack('<I', address) + bytes(12) + struct.pack('<II', 16, 31) + bytes(40)
                        for address in pointers)

        def read(address, size):
            if address in pointers:
                return pointers[address][:size]
            return heap[address - begin:address - begin + size]
        return [(begin, len(heap), 0x20000)], read

    def test_each_verified_database_key_is_saved_and_all_snapshots_match(self):
        with patch('wecom_key_recovery._recover_key', return_value=(self.keys, list(self.expected))):
            result = wecom_key_recovery.recover_key_file(self.source, self.base / 'profile', allow_process_read=True)
        self.assertEqual((result['databaseCount'], result['matchedCount'], result['plaintextCount']), (4, 3, 1))
        saved = json.loads(Path(result['keyFile']).read_text())['wecomKeys']
        self.assertEqual({row['database']: bytes.fromhex(row['key_hex']) for row in saved}, self.keys)
        snapshot = wecom_snapshot.prepare_snapshot(self.source, self.base / 'snapshots', result['keyFile'])
        self.assertEqual({p.name: p.read_bytes() for p in snapshot['root'].glob('*.db')}, self.expected)

    def test_string_candidates_can_match_different_keys_without_a_common_key(self):
        regions, read = self.strings([b'A' * 16, b'B' * 16])
        self.assertEqual(wecom_key_recovery.string_database_keys(regions, read, self.pages), self.keys)
        # Preserve the single-key contract used by live account-name reads.
        self.assertIsNone(wecom_key_recovery.string_profile_key(regions, read, self.pages))

    def test_incomplete_or_wrong_string_candidates_never_return_partial_mapping(self):
        for materials in ([b'A' * 16], [b'X' * 16, b'Y' * 16]):
            regions, read = self.strings(materials)
            self.assertIsNone(wecom_key_recovery.string_database_keys(regions, read, self.pages))

    def fake_owner(self, materials, *, after_scan=None):
        """A complete fake Win32 boundary; never call a real process API."""
        image = self.base / 'WXWork.exe'
        image.write_bytes(b'synthetic PE bytes; not executable')
        begin, module_base, handle = 0x10000, 0x400000, 0x1234
        pointers = {0x20000 + index * 0x10000: key for index, key in enumerate(materials)}
        heap = struct.pack('<I', module_base)
        heap += b''.join(struct.pack('<I', pointer) + bytes(12) + struct.pack('<II', 16, 31) + bytes(8)
                         for pointer in pointers)
        heap += bytes(256 - len(heap))

        def query_image(_handle, _flags, buffer, _length):
            buffer.value = str(image)
            return True

        def process_time(_handle, created, *_):
            created._obj.dwLowDateTime = 123
            created._obj.dwHighDateTime = 0
            return True

        def module_first(_snapshot, module):
            module._obj.modBaseAddr = module_base
            module._obj.szExePath = str(image)
            return True

        def query_region(_handle, address, info, _size):
            if address.value:
                return 0
            region = info._obj
            region.BaseAddress, region.RegionSize = begin, len(heap)
            region.State, region.Protect, region.Type = 0x1000, 4, 0x20000
            return ctypes.sizeof(region)

        def read_memory(_handle, address, buffer, size, got):
            point = address.value
            data = pointers.get(point, heap[point - begin:point - begin + size])[:size]
            ctypes.memmove(buffer, data, len(data))
            got._obj.value = len(data)
            return True

        def close(value):
            if value == handle and after_scan:
                after_scan()

        kernel = types.SimpleNamespace(
            OpenProcess=Mock(return_value=handle), CloseHandle=Mock(side_effect=close),
            ReadProcessMemory=Mock(side_effect=read_memory), VirtualQueryEx=Mock(side_effect=query_region),
            QueryFullProcessImageNameW=Mock(side_effect=query_image), GetProcessTimes=Mock(side_effect=process_time),
            CreateToolhelp32Snapshot=Mock(return_value=0x2222), Module32FirstW=Mock(side_effect=module_first))
        context = ExitStack()
        context.enter_context(patch('acquire_database_keys.declare_windows_apis', return_value=kernel))
        context.enter_context(patch('acquire_database_keys.WindowsDependencies._file_owners', return_value=[(101, 123)]))
        context.enter_context(patch('wecom_key_recovery.manager_vtables', return_value={0}))
        return context, kernel

    def test_owner_bound_recovery_keeps_all_distinct_keys_and_rechecks_them(self):
        context, kernel = self.fake_owner([b'A' * 16, b'B' * 16])
        with context:
            result = wecom_key_recovery.recover_key_file(self.source, self.base / 'profile', allow_process_read=True)
        saved = json.loads(Path(result['keyFile']).read_text())['wecomKeys']
        self.assertEqual({row['database']: bytes.fromhex(row['key_hex']) for row in saved}, self.keys)
        self.assertEqual((result['databaseCount'], result['matchedCount'], result['plaintextCount']), (4, 3, 1))
        kernel.OpenProcess.assert_called_once_with(0x410, False, 101)

    def test_incomplete_owner_candidates_leave_no_key_file(self):
        context, _ = self.fake_owner([b'A' * 16])
        with context, self.assertRaisesRegex(SnapshotError, '全部企微库'):
            wecom_key_recovery.recover_key_file(self.source, self.base / 'profile', allow_process_read=True)
        self.assertFalse((self.base / 'profile').exists())

    def test_changed_database_after_owner_scan_prevents_key_file_publication(self):
        def corrupt():
            path = self.source / 'user.db'
            data = bytearray(path.read_bytes())
            data[24] ^= 1
            path.write_bytes(data)
        context, _ = self.fake_owner([b'A' * 16, b'B' * 16], after_scan=corrupt)
        with context, self.assertRaisesRegex(SnapshotError, '复核失败'):
            wecom_key_recovery.recover_key_file(self.source, self.base / 'profile', allow_process_read=True)
        self.assertFalse((self.base / 'profile').exists())


if __name__ == '__main__':
    unittest.main()
