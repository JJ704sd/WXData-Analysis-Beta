"""Bounded, read-only Windows WeCom DbKeyManager recovery for one DB owner.
No injection, debugger, process writes, elevation, network or disk-wide search.
Only a complete mapping verified against each selected encrypted database is saved.
"""
import ctypes
from ctypes import wintypes as W
from datetime import datetime
import json
import os
from pathlib import Path
import re
import struct
import time
import uuid

from database_snapshot import SnapshotError, SQLITE_HEADER
from wecom_crypto import WeComCipher, password_key, page_size
from wecom_snapshot import data_root, inventory


def manager_vtables(image):
    """Resolve MSVC32 RTTI from this image; no version-specific absolute RVA."""
    try:
        pe = struct.unpack_from('<I', image, 60)[0]
        if image[pe:pe + 4] != b'PE\0\0' or struct.unpack_from('<H', image, pe + 4)[0] != 0x14c:
            raise ValueError
        count, optional = struct.unpack_from('<H', image, pe + 6)[0], struct.unpack_from('<H', image, pe + 20)[0]
        preferred = struct.unpack_from('<I', image, pe + 52)[0]
        if not 1 <= count <= 96:
            raise ValueError
        sections = [struct.unpack_from('<8sIIII', image, pe + 24 + optional + i * 40) for i in range(count)]
        def rva(offset):
            for _, _, virtual, size, raw in sections:
                if raw <= offset < raw + size:
                    return virtual + offset - raw
            raise ValueError
        type_at = image.find(b'.?AVDbKeyManager@logic@wework@@\0')
        if type_at < 8:
            raise ValueError
        type_pointer = struct.pack('<I', preferred + rva(type_at - 8))
        tables = set()
        for found in re.finditer(re.escape(type_pointer), image):
            locator = found.start() - 12
            if locator < 0 or image[locator:locator + 4] != bytes(4):
                continue
            pointer = struct.pack('<I', preferred + rva(locator))
            for table in re.finditer(re.escape(pointer), image):
                tables.add(rva(table.start() + 4))
        if not tables or len(tables) > 16:
            raise ValueError
        return tables
    except (ValueError, IndexError, struct.error):
        raise SnapshotError('当前企微可执行文件的密钥管理布局暂不支持；可选择已有企微密钥或已解密副本。') from None


def _match_material(material, pages, verified):
    for key in (password_key(material), material):
        cipher = WeComCipher(key)
        for name, page in pages.items():
            if name not in verified and cipher.verifies(page):
                verified[name] = key


def string_database_keys(regions, read, pages, *, limit=4096):
    """Return a complete per-database match from bounded MSVC32 strings.

    The caller supplies a budgeted reader for one already verified owner.
    A partial match is never returned as a usable result.
    """
    seen, verified = set(), {}
    for begin, size, kind in regions:
        if kind != 0x20000:  # Private data, not image or unrelated mapped files.
            continue
        for offset in range(0, size, 1024 * 1024):
            chunk = read(begin + offset, min(1024 * 1024 + 64, size - offset))
            for marker in re.finditer(b'\x10\x00\x00\x00', chunk):
                at = marker.start()
                if at < 16 or at + 8 > len(chunk):
                    continue
                capacity = struct.unpack_from('<I', chunk, at + 4)[0]
                pointer = struct.unpack_from('<I', chunk, at - 16)[0]
                if not 16 <= capacity <= 255 or pointer < 0x10000:
                    continue
                material = read(pointer, 16)
                if len(material) != 16 or material == bytes(16) or material in seen:
                    continue
                seen.add(material)
                if len(seen) > limit:
                    raise SnapshotError('密钥候选超过本次上限，未返回任何材料。')
                _match_material(material, pages, verified)
                if pages and len(verified) == len(pages):
                    return verified
    return None


def string_profile_key(regions, read, pages, *, limit=4096):
    """Compatibility seam for account-name readers requiring one common key."""
    verified = string_database_keys(regions, read, pages, limit=limit)
    keys = set(verified.values()) if verified else set()
    return next(iter(keys)) if len(keys) == 1 else None


def _recover_key(source_root, directory, *, allow_process_read=False, include_media=True, timeout=30, budget_mib=512,
                 profile_only=False, expected_owner=None):
    if allow_process_read is not True:
        raise SnapshotError('请通过“一键自动配置”明确选择只读访问本人已登录企微。')
    if os.name != 'nt' or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise SnapshotError('企微自动配置需要 Windows x64。')
    if not isinstance(timeout, int) or not 5 <= timeout <= 90 or not isinstance(budget_mib, int) or not 64 <= budget_mib <= 4096:
        raise SnapshotError('企微取钥资源预算无效。')
    source, directory = data_root(source_root), Path(directory).resolve()
    account = source.parent if source.name == 'Data' else source
    if directory == account or directory.is_relative_to(account):
        raise SnapshotError('程序数据和密钥不能写入企微账号目录。')
    names = ['user.db'] if profile_only else [name for name in inventory(source, include_media) if not name.endswith('-wal')]
    pages = {}
    for name in names:
        with (source / name).open('rb') as stream:
            first = stream.read(65536)
        size = page_size(first)
        if not first.startswith(SQLITE_HEADER):
            pages[name] = first[:size]
    if not pages:
        raise SnapshotError('数据已是明文副本，请直接加载，无需恢复密钥。')
    from acquire_database_keys import WindowsDependencies, declare_windows_apis
    kernel = declare_windows_apis()
    owned = [source / ('user.db' if profile_only else 'message.db')]
    for suffix in ('-wal', '-shm'):
        companion = Path(str(owned[0]) + suffix)
        if companion.exists():
            if companion.is_symlink() or not companion.is_file() or companion.resolve().parent != source:
                raise SnapshotError('企微数据库的占用检测文件不在所选目录内，未读取进程。')
            owned.append(companion)
    owners = WindowsDependencies._file_owners(owned)
    if len(owners) != 1 or (expected_owner is not None and owners != [expected_owner]):
        raise SnapshotError('所选企微数据没有唯一的当前持有进程；请登录对应账号，离线副本需提供已有密钥。')
    pid, created = owners[0]
    kernel.OpenProcess.argtypes, kernel.OpenProcess.restype = [W.DWORD, W.BOOL, W.DWORD], W.HANDLE
    kernel.CloseHandle.argtypes = [W.HANDLE]
    kernel.ReadProcessMemory.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    kernel.ReadProcessMemory.restype = W.BOOL
    class Region(ctypes.Structure):
        _fields_ = [('BaseAddress', ctypes.c_void_p), ('AllocationBase', ctypes.c_void_p), ('AllocationProtect', W.DWORD),
                    ('PartitionId', W.WORD), ('RegionSize', ctypes.c_size_t), ('State', W.DWORD), ('Protect', W.DWORD), ('Type', W.DWORD)]
    class Module(ctypes.Structure):
        _fields_ = [('dwSize', W.DWORD), ('th32ModuleID', W.DWORD), ('th32ProcessID', W.DWORD), ('GlblcntUsage', W.DWORD),
                    ('ProccntUsage', W.DWORD), ('modBaseAddr', ctypes.c_void_p), ('modBaseSize', W.DWORD), ('hModule', W.HMODULE),
                    ('szModule', W.WCHAR * 256), ('szExePath', W.WCHAR * 260)]
    kernel.VirtualQueryEx.argtypes = [W.HANDLE, ctypes.c_void_p, ctypes.POINTER(Region), ctypes.c_size_t]
    kernel.VirtualQueryEx.restype = ctypes.c_size_t
    kernel.CreateToolhelp32Snapshot.argtypes, kernel.CreateToolhelp32Snapshot.restype = [W.DWORD, W.DWORD], W.HANDLE
    kernel.Module32FirstW.argtypes, kernel.Module32FirstW.restype = [W.HANDLE, ctypes.POINTER(Module)], W.BOOL
    handle = kernel.OpenProcess(0x410, False, pid)
    if not handle:
        raise SnapshotError('系统不允许只读访问该企微进程，请使用有读取权限的本机用户。')
    deadline, read_bytes, verified = time.monotonic() + timeout, 0, {}
    def read(address, size):
        nonlocal read_bytes
        if time.monotonic() > deadline or read_bytes + size > budget_mib * 1024 * 1024:
            raise SnapshotError('企微取钥达到本次时间或读取预算，未保存不完整密钥。')
        read_bytes += size
        buffer, got = ctypes.create_string_buffer(size), ctypes.c_size_t()
        if kernel.ReadProcessMemory(handle, ctypes.c_void_p(address), buffer, size, ctypes.byref(got)):
            return buffer.raw[:got.value]
        return b''
    try:
        path, length = ctypes.create_unicode_buffer(32768), W.DWORD(32768)
        if not kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(length)) or Path(path.value).name.lower() != 'wxwork.exe':
            raise SnapshotError('数据库持有进程不是企微客户端，未扫描内存。')
        stamp, end, used, user = (W.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, ctypes.byref(stamp), ctypes.byref(end), ctypes.byref(used), ctypes.byref(user)) or created != (stamp.dwHighDateTime << 32) | stamp.dwLowDateTime:
            raise SnapshotError('企微进程归属已变化，未扫描内存。')
        image = Path(path.value).read_bytes()
        tables = manager_vtables(image)
        snapshot = kernel.CreateToolhelp32Snapshot(0x18, pid)
        if snapshot == ctypes.c_void_p(-1).value:
            raise SnapshotError('无法读取所选企微的模块地址。')
        try:
            module = Module()
            module.dwSize = ctypes.sizeof(Module)
            if not kernel.Module32FirstW(snapshot, ctypes.byref(module)) or Path(module.szExePath).resolve() != Path(path.value).resolve():
                raise SnapshotError('所选企微主模块无法确认。')
            base = module.modBaseAddr
        finally:
            kernel.CloseHandle(snapshot)
        needles = [struct.pack('<I', base + offset) for offset in tables]
        regions, address = [], 0
        while address < 0x100000000:
            info = Region()
            if not kernel.VirtualQueryEx(handle, ctypes.c_void_p(address), ctypes.byref(info), ctypes.sizeof(info)):
                break
            begin, size = int(info.BaseAddress or address), info.RegionSize
            if begin + size <= address:
                break
            if info.State == 0x1000 and not info.Protect & 0x101 and info.Protect & 0xff in (4, 8, 0x40, 0x80):
                regions.append((begin, size, info.Type))
            address = begin + size
        regions.sort(key=lambda item: item[2] != 0x20000)
        candidates = set()
        for begin, size, _ in regions:
            for offset in range(0, size, 1024 * 1024):
                chunk = read(begin + offset, min(1024 * 1024 + 4, size - offset))
                for needle in needles:
                    for marker in re.finditer(re.escape(needle), chunk):
                        body = read(begin + offset + marker.start(), 256)
                        for at in range(0, len(body) - 24, 4):
                            length, capacity = struct.unpack_from('<II', body, at + 16)
                            if length != 16 or not 16 <= capacity <= 255:
                                continue
                            pointer = struct.unpack_from('<I', body, at)[0]
                            if pointer < 0x10000:
                                continue
                            material = read(pointer, length)
                            if len(material) != 16 or material in candidates:
                                continue
                            candidates.add(material)
                            if len(candidates) > 256:
                                raise SnapshotError('企微密钥管理对象过多，未保存候选值。')
                            _match_material(material, pages, verified)
                            if len(verified) == len(pages):
                                break
                        if len(verified) == len(pages):
                            break
                    if len(verified) == len(pages):
                        break
                if len(verified) == len(pages):
                    break
            if len(verified) == len(pages):
                break
        if len(verified) != len(pages):
            remaining = {name: page for name, page in pages.items() if name not in verified}
            verified.update(string_database_keys(regions, read, remaining) or {})
    finally:
        kernel.CloseHandle(handle)
    if len(verified) != len(pages) or WindowsDependencies._file_owners(owned) != owners:
        raise SnapshotError('未找到通过全部企微库校验的密钥，或账号进程已变化；当前客户端布局可能不支持。')
    current_names = ['user.db'] if profile_only else [name for name in inventory(source, include_media) if not name.endswith('-wal')]
    if current_names != names:
        raise SnapshotError('企微数据库清单在取钥期间变化，未保存密钥。')
    for name in names:
        with (source / name).open('rb') as stream:
            first = stream.read(65536)
            size = page_size(first)
            if ((name not in pages and not first.startswith(SQLITE_HEADER)) or
                    (name in pages and not WeComCipher(verified[name]).verifies(first[:size]))):
                raise SnapshotError('企微数据库取钥后复核失败，未保存密钥。')
    return verified, names


def recover_profile_key(source_root, temporary, *, expected_owner, allow_process_read=False, timeout=30, budget_mib=512):
    """Return one transient user.db key; never write it or open message DBs."""
    keys, _names = _recover_key(source_root, temporary, allow_process_read=allow_process_read,
                              timeout=timeout, budget_mib=budget_mib, profile_only=True, expected_owner=expected_owner)
    return keys['user.db']


def recover_key_file(source_root, directory, *, allow_process_read=False, include_media=True, timeout=30, budget_mib=512):
    """Existing explicit setup path: persist only fully verified account keys."""
    verified, names = _recover_key(source_root, directory, allow_process_read=allow_process_read,
                                   include_media=include_media, timeout=timeout, budget_mib=budget_mib)
    directory = Path(directory).resolve()
    folder = directory / 'keys'
    folder.mkdir(parents=True, exist_ok=True)
    if folder.is_symlink() or folder.resolve().parent != directory:
        raise SnapshotError('程序密钥目录存在越界链接。')
    target = folder / ('wecom-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8] + '.json')
    with target.open('x', encoding='utf8') as stream:
        json.dump({'wecomKeys': [{'database': name, 'key_hex': key.hex()} for name, key in verified.items()]}, stream)
    return {'keyFile': str(target), 'databaseCount': len(names), 'matchedCount': len(verified),
            'plaintextCount': len(names) - len(verified), 'imageRecovery': 'wecom_cache_no_separate_key'}
