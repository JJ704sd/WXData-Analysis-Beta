"""Synthetic key bootstrap tests; never instantiate real Windows dependencies."""
from contextlib import redirect_stdout
import ctypes
import hashlib
import hmac
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location('acquire_database_keys', Path(__file__).resolve().parents[1] / 'scripts/acquire_database_keys.py')
bootstrap = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bootstrap
SPEC.loader.exec_module(bootstrap)


def encrypted_page(key, salt, filler=65):
    page = bytearray([filler] * 4096)
    page[:16] = salt
    mac_key = hashlib.pbkdf2_hmac('sha512', key, bytes(byte ^ 0x3a for byte in salt), 2, 32)
    page[4032:] = hmac.new(mac_key, page[16:4032] + b'\x01\0\0\0', hashlib.sha512).digest()
    return bytes(page)


class KeyBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='webridge-synthetic-key-bootstrap-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / 'WeBridge'; self.project.mkdir()
        self.source = self.base / 'synthetic-account'; self.source.mkdir()
        (self.source / 'message').mkdir()
        self.first = self.source / 'message/message_0.db'
        self.second = self.source / 'contact.db'
        self.key = b'K' * 32; self.salt = b'S' * 16
        self.other_key = b'J' * 32; self.other_salt = b'T' * 16
        self.first.write_bytes(encrypted_page(self.key, self.salt))
        self.second.write_bytes(encrypted_page(self.other_key, self.other_salt))
        self.candidates = [(self.key.hex(), self.salt.hex()), (self.other_key.hex(), self.other_salt.hex())]
        self.dependencies = types.SimpleNamespace(owner=Mock(return_value=(12345, 999)), scan=Mock(return_value=self.candidates))
        self.output = self.project / '.secrets/synthetic-keys.json'

    def collect(self):
        return bootstrap.collect_keys(self.source, self.dependencies)

    def test_all_selected_business_libraries_require_hmac_and_only_matches_survive(self):
        unrelated = (b'X' * 32).hex(), (b'Y' * 16).hex()
        self.dependencies.scan.return_value = self.candidates + [unrelated, ('bad', ''), self.candidates[0]]
        result = self.collect()
        self.assertEqual(result['databaseCount'], 2); self.assertEqual(result['matchedCount'], 2)
        self.assertEqual({(row['key_hex'], row['salt_hex']) for row in result['keys']}, set(self.candidates))
        self.assertNotIn(unrelated[0], str(result))
        self.dependencies.scan.assert_called_once_with(12345, expected_start_time=999, max_scan_bytes=4096 * 1024 * 1024, timeout=90)

    def test_candidate_without_salt_is_saved_only_with_verified_database_salt(self):
        self.dependencies.scan.return_value = [(key, '') for key, _ in self.candidates]
        result = self.collect()
        self.assertEqual({row['salt_hex'] for row in result['keys']}, {self.salt.hex(), self.other_salt.hex()})

    def test_salt_match_without_hmac_match_is_rejected(self):
        self.dependencies.scan.return_value = [(self.other_key.hex(), self.salt.hex()), self.candidates[1]]
        with self.assertRaises(bootstrap.AcquireError) as caught:
            self.collect()
        self.assertEqual(caught.exception.code, 'key_match_incomplete')
        self.assertFalse(self.output.exists())

    def test_one_unmatched_database_prevents_success(self):
        self.dependencies.scan.return_value = self.candidates[:1]
        with self.assertRaises(bootstrap.AcquireError) as caught:
            self.collect()
        self.assertEqual(caught.exception.code, 'key_match_incomplete')

    def test_corrupt_current_page_prevents_success_even_when_original_matched(self):
        def scan(*args, **kwargs):
            page = bytearray(self.first.read_bytes()); page[16] ^= 1; self.first.write_bytes(page)
            return self.candidates
        self.dependencies.scan.side_effect = scan
        with self.assertRaises(bootstrap.AcquireError) as caught:
            self.collect()
        self.assertEqual(caught.exception.code, 'key_match_incomplete')

    def test_start_time_change_prevents_publication(self):
        self.dependencies.owner.side_effect = [(12345, 999), (12345, 1000)]
        with self.assertRaises(bootstrap.AcquireError) as caught:
            self.collect()
        self.assertEqual(caught.exception.code, 'owner_changed')

    def test_owner_failure_does_not_scan_any_process(self):
        self.dependencies.owner.side_effect = bootstrap.AcquireError('owner_not_unique')
        with self.assertRaises(bootstrap.AcquireError):
            self.collect()
        self.dependencies.scan.assert_not_called()

    def test_account_inventory_includes_only_selected_business_db_and_wal(self):
        wal = Path(str(self.first) + '-wal'); wal.write_bytes(b'synthetic WAL')
        (self.source / 'message/message_fts.db').write_bytes(b'not selected')
        other_account = self.source / 'other-account'; other_account.mkdir()
        (other_account / 'message_0.db').write_bytes(b'not selected')
        before = {path: path.read_bytes() for path in self.source.rglob('*') if path.is_file()}
        self.collect()
        selected = set(self.dependencies.owner.call_args_list[0].args[0])
        self.assertEqual(selected, {self.first, self.second, wal})
        self.assertTrue(all(path.read_bytes() == value for path, value in before.items()))

    def test_new_business_database_during_scan_is_refused(self):
        def scan(*args, **kwargs):
            (self.source / 'message/message_1.db').write_bytes(encrypted_page(self.key, self.salt))
            return self.candidates
        self.dependencies.scan.side_effect = scan
        with self.assertRaises(bootstrap.AcquireError) as caught:
            self.collect()
        self.assertEqual(caught.exception.code, 'source_changed')

    def test_plaintext_database_is_not_a_key_extraction_target(self):
        self.first.write_bytes(b'SQLite format 3\0' + b'\0' * 4080)
        with self.assertRaises(bootstrap.AcquireError) as caught:
            self.collect()
        self.assertEqual(caught.exception.code, 'source_not_encrypted')
        self.dependencies.scan.assert_not_called()

    def test_only_new_secret_json_is_published_and_no_temporary_file_remains(self):
        result = self.collect()
        bootstrap.write_keys(self.output, result['keys'], self.project)
        value = json.loads(self.output.read_text())
        self.assertEqual(set(value), {'keys'})
        self.assertTrue(all(set(row) == {'key_hex', 'salt_hex'} for row in value['keys']))
        self.assertEqual(list(self.output.parent.iterdir()), [self.output])
        with self.assertRaises(bootstrap.AcquireError) as caught:
            bootstrap.write_keys(self.output, [], self.project)
        self.assertEqual(caught.exception.code, 'output_exists')
        self.assertEqual(json.loads(self.output.read_text()), value)

    def test_output_outside_secret_directory_and_wrong_extension_are_rejected(self):
        for path in (self.project / 'keys.json', self.base / 'outside.json', self.project / '.secrets/keys.txt', self.project / '.secrets/../outside.json'):
            with self.subTest(path=path):
                with self.assertRaises(bootstrap.AcquireError) as caught:
                    bootstrap.write_keys(path, [], self.project)
                self.assertEqual(caught.exception.code, 'invalid_output')
        self.assertFalse((self.project / '.secrets').exists())

    def test_racing_output_file_is_never_overwritten(self):
        self.output.parent.mkdir()
        original_link = bootstrap.os.link
        def racing_link(source, target):
            self.output.write_text('existing')
            return original_link(source, target)
        with patch.object(bootstrap.os, 'link', side_effect=racing_link):
            with self.assertRaises(bootstrap.AcquireError) as caught:
                bootstrap.write_keys(self.output, [], self.project)
        self.assertEqual(caught.exception.code, 'output_exists')
        self.assertEqual(self.output.read_text(), 'existing')
        self.assertEqual(list(self.output.parent.iterdir()), [self.output])

    def test_argument_and_runtime_errors_never_echo_sensitive_values(self):
        for args in (['--unexpected', self.key.hex()], ['--source-root', self.key.hex(), '--output-file', 'outside.json']):
            output = io.StringIO()
            with redirect_stdout(output):
                code = bootstrap.main(args)
            self.assertEqual(code, 1)
            self.assertNotIn(self.key.hex(), output.getvalue())
            self.assertEqual(json.loads(output.getvalue())['status'], 'error')
        self.dependencies.scan.side_effect = RuntimeError('synthetic private ' + self.key.hex())
        with self.assertRaises(bootstrap.AcquireError) as caught:
            self.collect()
        self.assertEqual(str(caught.exception), 'scan_failed')

    def test_ctypes_declarations_use_pointer_sized_handles_and_sizes(self):
        kernel, manager = Mock(), Mock()
        bootstrap.declare_windows_apis(kernel, manager)
        self.assertIs(kernel.OpenProcess.restype, ctypes.wintypes.HANDLE)
        self.assertIs(kernel.CreateToolhelp32Snapshot.restype, ctypes.wintypes.HANDLE)
        self.assertIs(kernel.VirtualQueryEx.restype, ctypes.c_size_t)
        self.assertIs(kernel.ReadProcessMemory.argtypes[3], ctypes.c_size_t)
        self.assertEqual(len(manager.RmRegisterResources.argtypes), 7)

    def test_process_open_uses_only_vm_read_and_query_information(self):
        dependencies = bootstrap.WindowsDependencies.__new__(bootstrap.WindowsDependencies)
        dependencies.kernel32 = Mock()
        dependencies.kernel32.OpenProcess.return_value = 0x123456789
        self.assertEqual(dependencies._open(12345), 0x123456789)
        dependencies.kernel32.OpenProcess.assert_called_once_with(0x410, False, 12345)

    def test_owner_compares_restart_manager_creation_time_on_same_open_handle(self):
        dependencies = bootstrap.WindowsDependencies.__new__(bootstrap.WindowsDependencies)
        dependencies._file_owners = Mock(return_value=[(12345, 999)])
        dependencies.ownership = types.SimpleNamespace(weixin_pids=Mock(return_value=[12345, 54321]))
        dependencies.kernel32 = Mock(); dependencies._open = Mock(return_value=0x123456789)
        dependencies._identity = Mock(return_value=1000)
        with self.assertRaises(bootstrap.AcquireError) as caught:
            dependencies.owner([self.first])
        self.assertEqual(caught.exception.code, 'owner_changed')
        dependencies.kernel32.CloseHandle.assert_called_once_with(0x123456789)

    def test_scan_owner_identity_change_closes_handle_before_any_memory_read(self):
        dependencies = bootstrap.WindowsDependencies.__new__(bootstrap.WindowsDependencies)
        dependencies._open = Mock(return_value=0x123456789)
        dependencies._identity = Mock(return_value=1000); dependencies.kernel32 = Mock()
        scanner = Mock()
        scanner.scan_owned_process.side_effect = lambda pid, process_factory: process_factory(pid)
        dependencies.scanner = scanner
        with self.assertRaises(bootstrap.AcquireError) as caught:
            dependencies.scan(12345, expected_start_time=999, max_scan_bytes=1024, timeout=1)
        self.assertEqual(caught.exception.code, 'owner_changed')
        scanner.ReadOnlyProcess.assert_not_called()
        dependencies.kernel32.CloseHandle.assert_called_once_with(0x123456789)


if __name__ == '__main__':
    unittest.main()
