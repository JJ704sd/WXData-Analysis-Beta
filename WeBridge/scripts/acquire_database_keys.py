"""Optional, explicitly invoked key bootstrap for one authorized account.

The workbench itself only consumes a supplied key file. This CLI depends on the
separate local PoC package; it never imports attachment scripts. No process is
opened on import or --help. Output and diagnostics never contain memory data.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import hmac
import json
import multiprocessing
import os
from pathlib import Path
import re
import sys
import tempfile
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUSINESS_DB = re.compile(r'(?:contact|session|(?:biz_)?message_\d+)\.db\Z', re.I)
BUSINESS_DIRS = {'contact', 'session', 'message', 'biz_message'}
READ_ACCESS = 0x0010 | 0x0400  # PROCESS_VM_READ | PROCESS_QUERY_INFORMATION
MAX_CANDIDATES = 4096
PAGE_SIZE = 4096
SAFE_CODES = {
    'invalid_arguments', 'cancelled',
    'invalid_source', 'invalid_output', 'output_exists', 'output_write_failed',
    'missing_poc', 'windows_x64_required', 'owner_unknown', 'owner_not_unique',
    'owner_changed', 'process_unavailable', 'source_changed', 'source_not_encrypted',
    'wechat_not_running', 'source_not_active',
    'scan_failed', 'scan_budget_exceeded', 'scan_timeout', 'key_match_incomplete',
    'key_match_ambiguous', 'internal_failure',
}


class AcquireError(ValueError):
    def __init__(self, code):
        self.code = code if code in SAFE_CODES else 'internal_failure'
        super().__init__(self.code)


def inventory(source_root, include_media=False, include_voice=False):
    """Inspect names only in the explicit account root and known child dirs."""
    try:
        root = Path(source_root).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise AcquireError('invalid_source')
        entries = list(root.iterdir())
        folders = [root]
        pattern = re.compile(r'(?:contact|session|(?:biz_)?message_\d+|message_resource|hardlink)\.db\Z', re.I) if include_media else BUSINESS_DB
        if include_voice:
            pattern = re.compile('(?:' + pattern.pattern.removesuffix('\\Z') + r'|media_\d+\.db)\Z', re.I)
        directories = BUSINESS_DIRS | {'hardlink'} if include_media else BUSINESS_DIRS
        for entry in entries:
            if entry.name.lower() in directories and entry.is_dir():
                if entry.is_symlink() or not entry.resolve().is_relative_to(root):
                    raise AcquireError('invalid_source')
                folders.append(entry)
        databases, owned_files = [], []
        for folder in folders:
            for path in sorted(folder.iterdir()):
                if not pattern.fullmatch(path.name) or not path.is_file():
                    continue
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise AcquireError('invalid_source')
                databases.append(path)
                owned_files.append(path)
                wal = Path(str(path) + '-wal')
                if wal.exists():
                    if wal.is_symlink() or not wal.resolve().is_relative_to(root) or not wal.is_file():
                        raise AcquireError('invalid_source')
                    owned_files.append(wal)
        if not databases or len(databases) > MAX_CANDIDATES:
            raise AcquireError('invalid_source')
        return root, tuple(sorted(databases)), tuple(sorted(owned_files))
    except AcquireError:
        raise
    except Exception:
        raise AcquireError('invalid_source') from None


def read_first_pages(databases):
    """Return encrypted pages only; plaintext is checked fully by the snapshot."""
    pages = {}
    try:
        for path in databases:
            with path.open('rb') as stream:
                page = stream.read(PAGE_SIZE)
            if page.startswith(b'SQLite format 3\0'):
                size = int.from_bytes(page[16:18], 'big')
                size = 65536 if size == 1 else size
                if (len(page) < 100 or page[21:24] != b'\x40\x20\x20' or
                        not 512 <= size <= 65536 or size & (size - 1) or path.stat().st_size % size):
                    raise AcquireError('source_not_encrypted')
                continue
            if len(page) != PAGE_SIZE:
                raise AcquireError('source_not_encrypted')
            pages[path] = page
        if not pages:
            raise AcquireError('source_not_encrypted')
        return pages
    except AcquireError:
        raise
    except Exception:
        raise AcquireError('source_changed') from None


def matching_keys(pages, candidates):
    """Keep only candidates authenticated by every selected DB's first page."""
    if not isinstance(candidates, (list, tuple)) or len(candidates) > MAX_CANDIDATES:
        raise AcquireError('scan_failed')
    usable = set()
    for candidate in candidates:
        if not isinstance(candidate, (list, tuple)) or len(candidate) != 2:
            continue
        key, salt = candidate
        if not isinstance(key, str) or not re.fullmatch('[0-9a-fA-F]{64}', key):
            continue
        if not isinstance(salt, str) or (salt and not re.fullmatch('[0-9a-fA-F]{32}', salt)):
            continue
        usable.add((bytes.fromhex(key), bytes.fromhex(salt) if salt else b''))
    verified = set()
    for page in pages.values():
        if not isinstance(page, bytes) or len(page) != PAGE_SIZE:
            raise AcquireError('source_changed')
        salt = page[:16]
        matches = set()
        for key, claimed_salt in usable:
            if claimed_salt and claimed_salt != salt:
                continue
            mac_key = hashlib.pbkdf2_hmac('sha512', key, bytes(byte ^ 0x3a for byte in salt), 2, 32)
            actual = hmac.new(mac_key, page[16:4032] + b'\x01\0\0\0', hashlib.sha512).digest()
            if hmac.compare_digest(actual, page[4032:4096]):
                matches.add(key)
        if not matches:
            raise AcquireError('key_match_incomplete')
        if len(matches) != 1:
            raise AcquireError('key_match_ambiguous')
        verified.add((next(iter(matches)).hex(), salt.hex()))
    if not pages:
        raise AcquireError('invalid_source')
    return [{'key_hex': key, 'salt_hex': salt} for key, salt in sorted(verified)]


def collect_keys(source_root, dependencies, *, max_scan_bytes=4096 * 1024 * 1024, timeout=90,
                 include_media=False, include_voice=False):
    """Dependency-injected seam for synthetic tests; performs no output writes."""
    try:
        root, databases, owned_files = inventory(source_root, include_media, include_voice)
        pages = read_first_pages(databases)
        owner = dependencies.owner(owned_files)
        candidates = dependencies.scan(owner[0], expected_start_time=owner[1], max_scan_bytes=max_scan_bytes, timeout=timeout)
        keys = matching_keys(pages, candidates)
        # Ignore unrelated process candidates after matching; only verified keys
        # can cross the worker IPC boundary and reach the new JSON file.
        del candidates
        current_root, current_databases, current_owned = inventory(root, include_media, include_voice)
        if current_root != root or current_databases != databases or current_owned != owned_files:
            raise AcquireError('source_changed')
        if dependencies.owner(current_owned) != owner:
            raise AcquireError('owner_changed')
        current_pages = read_first_pages(current_databases)
        if set(current_pages) != set(pages) or any(current_pages[path][:16] != pages[path][:16] for path in pages):
            raise AcquireError('source_changed')
        verified = matching_keys(current_pages, [(row['key_hex'], row['salt_hex']) for row in keys])
        return {'keys': verified, 'databaseCount': len(databases), 'matchedCount': len(pages),
                'plaintextCount': len(databases) - len(pages)}
    except AcquireError:
        raise
    except Exception:
        raise AcquireError('scan_failed') from None


def validate_output(output_file, repository_root=PROJECT_ROOT):
    """Only a new direct child JSON of the project's real .secrets directory."""
    try:
        project = Path(repository_root).resolve(strict=True)
        secrets = project / '.secrets'
        if secrets.is_symlink() or (secrets.exists() and secrets.resolve() != secrets):
            raise AcquireError('invalid_output')
        output = Path(output_file).expanduser().absolute()
        if output.parent.resolve() != secrets or output.suffix.lower() != '.json':
            raise AcquireError('invalid_output')
        if output.exists() or output.is_symlink():
            raise AcquireError('output_exists')
        if not output.name or any(ord(character) < 32 for character in output.name):
            raise AcquireError('invalid_output')
        return output, secrets
    except AcquireError:
        raise
    except Exception:
        raise AcquireError('invalid_output') from None


def write_keys(output_file, keys, repository_root=PROJECT_ROOT):
    output, secrets = validate_output(output_file, repository_root)
    temporary = None
    try:
        secrets.mkdir(mode=0o700, exist_ok=True)
        # A same-directory hard link publishes complete bytes without overwriting
        # an existing file (including one created after the initial path check).
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=secrets,
                                         prefix='.key-bootstrap-', suffix='.json', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({'keys': keys}, stream, ensure_ascii=True, separators=(',', ':'))
            stream.flush(); os.fsync(stream.fileno())
        validate_output(output, repository_root)
        os.link(temporary, output)
    except AcquireError:
        raise
    except FileExistsError:
        raise AcquireError('output_exists') from None
    except Exception:
        raise AcquireError('output_write_failed') from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def declare_windows_apis(kernel32=None, restart_manager=None):
    """Declare pointer widths before calling PoC code with local structures."""
    kernel32 = kernel32 if kernel32 is not None else ctypes.windll.kernel32
    restart_manager = restart_manager if restart_manager is not None else ctypes.windll.rstrtmgr
    pointer = ctypes.c_void_p
    def declare(library, name, result, args):
        function = getattr(library, name)
        function.restype = result; function.argtypes = args
    declare(kernel32, 'OpenProcess', wintypes.HANDLE, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD])
    declare(kernel32, 'CloseHandle', wintypes.BOOL, [wintypes.HANDLE])
    declare(kernel32, 'ReadProcessMemory', wintypes.BOOL,
            [wintypes.HANDLE, pointer, pointer, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)])
    declare(kernel32, 'VirtualQueryEx', ctypes.c_size_t, [wintypes.HANDLE, pointer, pointer, ctypes.c_size_t])
    declare(kernel32, 'CreateToolhelp32Snapshot', wintypes.HANDLE, [wintypes.DWORD, wintypes.DWORD])
    for name in ('Module32FirstW', 'Module32NextW', 'Process32FirstW', 'Process32NextW'):
        declare(kernel32, name, wintypes.BOOL, [wintypes.HANDLE, pointer])
    declare(kernel32, 'GetProcessTimes', wintypes.BOOL,
            [wintypes.HANDLE, pointer, pointer, pointer, pointer])
    declare(kernel32, 'IsWow64Process', wintypes.BOOL, [wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)])
    declare(kernel32, 'QueryFullProcessImageNameW', wintypes.BOOL,
            [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)])
    declare(restart_manager, 'RmStartSession', wintypes.DWORD, [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, wintypes.LPWSTR])
    declare(restart_manager, 'RmRegisterResources', wintypes.DWORD,
            [wintypes.DWORD, wintypes.UINT, ctypes.POINTER(wintypes.LPCWSTR), wintypes.UINT, pointer, wintypes.UINT, pointer])
    declare(restart_manager, 'RmGetList', wintypes.DWORD,
            [wintypes.DWORD, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT), pointer, ctypes.POINTER(wintypes.DWORD)])
    declare(restart_manager, 'RmEndSession', wintypes.DWORD, [wintypes.DWORD])
    return kernel32


class WindowsDependencies:
    def __init__(self):
        if os.name != 'nt' or ctypes.sizeof(ctypes.c_void_p) != 8:
            raise AcquireError('windows_x64_required')
        # Optional sibling package, never an archive or a hidden import from a
        # current directory. The core database-reader has no dependency on it.
        poc = PROJECT_ROOT.parent / 'poc'
        if not getattr(sys, 'frozen', False):
            if not (poc / 'wechat_agent_poc/process_own.py').is_file() or not (poc / 'wechat_agent_poc/weixin_cipher_scan.py').is_file():
                raise AcquireError('missing_poc')
            sys.path.insert(0, str(poc))
        self.kernel32 = declare_windows_apis()
        try:
            from wechat_agent_poc import process_own, weixin_cipher_scan
        except Exception:
            raise AcquireError('missing_poc') from None
        self.ownership = process_own
        self.scanner = weixin_cipher_scan

    def _open(self, pid):
        handle = self.kernel32.OpenProcess(READ_ACCESS, False, int(pid))
        if not handle:
            raise AcquireError('process_unavailable')
        return handle

    def _identity(self, handle):
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not self.kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)):
            raise AcquireError('process_unavailable')
        size = wintypes.DWORD(32768); name = ctypes.create_unicode_buffer(size.value)
        if not self.kernel32.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)):
            raise AcquireError('process_unavailable')
        if Path(name.value).name.lower() not in ('weixin.exe', 'wechat.exe'):
            raise AcquireError('owner_not_unique')
        wow64 = wintypes.BOOL()
        if not self.kernel32.IsWow64Process(handle, ctypes.byref(wow64)) or wow64.value:
            raise AcquireError('windows_x64_required')
        return (created.dwHighDateTime << 32) | created.dwLowDateTime

    @staticmethod
    def _file_owners(paths):
        # The PoC helper drops Restart Manager's creation time. Retain it here
        # so a PID reused between ownership lookup and OpenProcess is refused.
        class UniqueProcess(ctypes.Structure):
            _fields_ = [('pid', wintypes.DWORD), ('created', wintypes.FILETIME)]
        class ProcessInfo(ctypes.Structure):
            _fields_ = [('process', UniqueProcess), ('name', wintypes.WCHAR * 256),
                        ('service', wintypes.WCHAR * 64), ('kind', ctypes.c_int),
                        ('status', wintypes.ULONG), ('session', wintypes.DWORD), ('restartable', wintypes.BOOL)]
        library = ctypes.windll.rstrtmgr
        session = wintypes.DWORD(); key = ctypes.create_unicode_buffer(33)
        if library.RmStartSession(ctypes.byref(session), 0, key):
            raise AcquireError('owner_unknown')
        try:
            resources = (ctypes.c_wchar_p * len(paths))(*(str(path) for path in paths))
            if library.RmRegisterResources(session, len(paths), resources, 0, None, 0, None):
                raise AcquireError('owner_unknown')
            needed, count, reboot = wintypes.UINT(), wintypes.UINT(), wintypes.DWORD()
            result = library.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), None, ctypes.byref(reboot))
            if result not in (0, 234):
                raise AcquireError('owner_unknown')
            for _ in range(3):
                if needed.value == 0:
                    return []
                if needed.value > 1024:
                    raise AcquireError('owner_not_unique')
                infos = (ProcessInfo * needed.value)(); count = wintypes.UINT(needed.value)
                result = library.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), infos, ctypes.byref(reboot))
                if result == 234:
                    continue
                if result:
                    raise AcquireError('owner_unknown')
                return sorted({(int(row.process.pid), (row.process.created.dwHighDateTime << 32) | row.process.created.dwLowDateTime)
                               for row in infos[:count.value]})
            raise AcquireError('owner_unknown')
        finally:
            library.RmEndSession(session)

    def owner(self, paths):
        holders = self._file_owners(paths)
        if not holders:
            raise AcquireError('source_not_active' if self.ownership.weixin_pids() else 'wechat_not_running')
        if len(holders) != 1:
            raise AcquireError('owner_not_unique')
        pid, created = holders[0]
        if pid not in self.ownership.weixin_pids():
            raise AcquireError('owner_not_unique')
        handle = self._open(pid)
        try:
            if self._identity(handle) != created:
                raise AcquireError('owner_changed')
            return (int(pid), created)
        finally:
            self.kernel32.CloseHandle(handle)

    def scan(self, pid, *, expected_start_time, max_scan_bytes, timeout):
        deadline = time.monotonic() + timeout
        total = 0
        scanner = self.scanner
        def factory(candidate_pid):
            handle = self._open(candidate_pid)
            try:
                if self._identity(handle) != expected_start_time:
                    raise AcquireError('owner_changed')
                process = scanner.ReadOnlyProcess(candidate_pid, handle=handle)
            except Exception:
                self.kernel32.CloseHandle(handle)
                raise
            original_read = process.read_bytes
            def bounded_read(address, size):
                nonlocal total
                if time.monotonic() > deadline:
                    raise AcquireError('scan_timeout')
                total += size
                if size <= 0 or total > max_scan_bytes:
                    raise AcquireError('scan_budget_exceeded')
                # The old heap scanner compares PAGE_GUARD as an exact value;
                # refuse combined guard/no-access flags before any memory read.
                position, end = address, address + size
                while position < end:
                    info = scanner.MEMORY_BASIC_INFORMATION()
                    if not self.kernel32.VirtualQueryEx(handle, ctypes.c_void_p(position), ctypes.byref(info), ctypes.sizeof(info)):
                        raise OSError('region_unavailable')
                    next_address = (info.BaseAddress or position) + info.RegionSize
                    if (next_address <= position or info.State != 0x1000 or info.Protect & 0x101 or
                            info.Protect & 0xff not in (0x02, 0x04, 0x08, 0x20, 0x40, 0x80)):
                        raise OSError('region_unreadable')
                    position = next_address
                data = original_read(address, size)
                if len(data) != size:
                    raise OSError('incomplete_read')
                return data
            process.read_bytes = bounded_read
            return process
        return scanner.scan_owned_process(pid, process_factory=factory)


def _worker(source_root, result_pipe, budget, timeout, include_media=False, include_voice=False):
    # Redirect all incidental library output. Keys travel only in this private
    # parent/child connection after HMAC filtering, never stdout or stderr.
    with open(os.devnull, 'w') as sink:
        os.dup2(sink.fileno(), 1); os.dup2(sink.fileno(), 2)
        sys.stdout = sink; sys.stderr = sink
        try:
            result = collect_keys(source_root, WindowsDependencies(), max_scan_bytes=budget, timeout=timeout,
                                  include_media=include_media, include_voice=include_voice)
            result_pipe.send({'ok': True, **result})
        except AcquireError as exc:
            result_pipe.send({'ok': False, 'code': exc.code})
        except BaseException:
            result_pipe.send({'ok': False, 'code': 'scan_failed'})
        finally:
            result_pipe.close()


def run_worker(source_root, budget, timeout, include_media=False, include_voice=False):
    context = multiprocessing.get_context('spawn')
    incoming, outgoing = context.Pipe(duplex=False)
    worker = context.Process(target=_worker, args=(str(source_root), outgoing, budget, timeout, include_media, include_voice))
    try:
        worker.start(); outgoing.close()
        if not incoming.poll(timeout):
            raise AcquireError('scan_timeout')
        result = incoming.recv()
        if not isinstance(result, dict) or not result.get('ok'):
            raise AcquireError(result.get('code', 'scan_failed') if isinstance(result, dict) else 'scan_failed')
        return result
    except AcquireError:
        raise
    except Exception:
        raise AcquireError('scan_failed') from None
    finally:
        incoming.close(); outgoing.close()
        if worker.pid is not None:
            worker.join(1)
            if worker.is_alive():
                # This is our own bounded Python worker, never Weixin.
                worker.terminate(); worker.join(5)
            worker.close()


def main(argv=None):
    class SafeParser(argparse.ArgumentParser):
        def error(self, message):
            raise AcquireError('invalid_arguments')
    parser = SafeParser(description='Optional Windows x64 key bootstrap. Requires sibling poc/wechat_agent_poc; scans only the unique owner of the explicitly selected account business DB/WAL files. Never logs key values.')
    parser.add_argument('--source-root', required=True, help='Explicit account db_storage directory; no account discovery')
    parser.add_argument('--include-media', action='store_true', help='Also authenticate hardlink and message_resource indexes for the same account')
    parser.add_argument('--include-voice', action='store_true', help='Also authenticate the selected account media_N.db voice databases')
    parser.add_argument('--output-file', required=True, help='New .json file directly inside WeBridge/.secrets; existing files are refused')
    parser.add_argument('--timeout', type=int, default=90, help='Worker time limit, 5..300 seconds (default 90)')
    parser.add_argument('--max-scan-mib', type=int, default=4096, help='Read budget, 64..16384 MiB (default 4096)')
    try:
        args = parser.parse_args(argv)
        if not 5 <= args.timeout <= 300 or not 64 <= args.max_scan_mib <= 16384:
            raise AcquireError('scan_budget_exceeded')
        validate_output(args.output_file)
        root, _, _ = inventory(args.source_root, args.include_media, args.include_voice)
        result = run_worker(root, args.max_scan_mib * 1024 * 1024, args.timeout, args.include_media, args.include_voice)
        write_keys(args.output_file, result['keys'])
        print(json.dumps({'status': 'ok', 'databaseCount': result['databaseCount'], 'matchedCount': result['matchedCount']}))
        return 0
    except AcquireError as exc:
        print(json.dumps({'status': 'error', 'code': exc.code}))
        return 1
    except KeyboardInterrupt:
        print(json.dumps({'status': 'error', 'code': 'cancelled'}))
        return 130
    except Exception:
        print(json.dumps({'status': 'error', 'code': 'internal_failure'}))
        return 1


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
