"""Read-only CipherConfig scan of a single already-owned Weixin process.

Ported from the attachment wx_csv/key_extractor.py after review:
no network, no writes, no key logging, no first-process fallback.
Does not use pymem (avoids PROCESS_ALL_ACCESS). Caller must pass a PID
already bound to the authorized account files.
"""

from __future__ import annotations

import ctypes
import os
import re
import struct
from ctypes import wintypes
from typing import Callable

from wechat_agent_poc.models import Halt, HaltError
from wechat_agent_poc.process_own import READONLY_PROCESS_ACCESS

MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
MAX_MODULE_NAME32 = 255

CTOR_PATTERN = bytes(
    [
        0x55,
        0x56,
        0x57,
        0x48,
        0x83,
        0xEC,
        0x50,
        0x48,
        0x8D,
        0x6C,
        0x24,
        0x50,
        0x48,
        0xC7,
        0x45,
        0xF8,
        0xFE,
        0xFF,
        0xFF,
        0xFF,
        0x48,
        0x8D,
        0x05,
        0x00,
        0x00,
        0x00,
        0x00,
        0x48,
        0x89,
        0x4D,
        0xF0,
        0x48,
        0x89,
        0x01,
        0x44,
        0x89,
        0xCE,
        0x44,
        0x89,
        0xC7,
    ]
)
CTOR_MASK = "xxxxxxxxxxxxxxxxxxxxx" + "xx?????" + "xxxxxxxxxxxxxxx"


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.POINTER(ctypes.c_byte)),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", wintypes.WCHAR * (MAX_MODULE_NAME32 + 1)),
        ("szExePath", wintypes.WCHAR * 260),
    ]


class ReadOnlyProcess:
    def __init__(self, pid: int, handle=None):
        if os.name != "nt":
            raise HaltError(Halt("READ_FAILURE", "live process access is only implemented on Windows"))
        self.pid = int(pid)
        self._kernel32 = ctypes.windll.kernel32
        self.handle = handle or self._kernel32.OpenProcess(READONLY_PROCESS_ACCESS, False, self.pid)
        if not self.handle:
            err = ctypes.GetLastError()
            raise HaltError(
                Halt(
                    "READ_FAILURE",
                    "OpenProcess for read-only VM access was refused",
                    {"platform_error": f"winerror={err}", "phase": "G-KEY"},
                )
            )
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._kernel32.CloseHandle(self.handle)
        self._closed = True

    def read_bytes(self, addr: int, size: int) -> bytes:
        if size <= 0 or size > 0x10000000:
            raise HaltError(Halt("READ_FAILURE", "refusing unbounded process read"))
        buf = ctypes.create_string_buffer(size)
        read = ctypes.c_size_t(0)
        ok = self._kernel32.ReadProcessMemory(
            self.handle,
            ctypes.c_void_p(addr),
            buf,
            size,
            ctypes.byref(read),
        )
        if not ok:
            err = ctypes.GetLastError()
            raise OSError(err, f"ReadProcessMemory winerror={err}")
        return buf.raw[: read.value]


def _module_base_size(proc: ReadOnlyProcess, names: tuple[str, ...]) -> tuple[int, int] | None:
    kernel32 = ctypes.windll.kernel32
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, proc.pid)
    invalid = ctypes.c_void_p(-1).value
    if snap in (0, None, invalid):
        return None
    try:
        entry = MODULEENTRY32W()
        entry.dwSize = ctypes.sizeof(MODULEENTRY32W)
        if not kernel32.Module32FirstW(snap, ctypes.byref(entry)):
            return None
        wanted = {name.lower() for name in names}
        while True:
            if entry.szModule.lower() in wanted:
                base = ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value
                if base:
                    return int(base), int(entry.modBaseSize)
            if not kernel32.Module32NextW(snap, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snap)
    return None


def _pattern_to_regex(pattern: bytes, mask: str) -> re.Pattern[bytes]:
    parts = []
    for index, byte in enumerate(pattern):
        if index < len(mask) and mask[index] == "x":
            parts.append(re.escape(bytes([byte])))
        else:
            parts.append(b".")
    return re.compile(b"".join(parts), re.DOTALL)


def _text_section(proc: ReadOnlyProcess, base: int) -> tuple[int, int] | None:
    try:
        e_lfanew = struct.unpack("<I", proc.read_bytes(base + 0x3C, 4))[0]
        pe = base + e_lfanew
        n_sec = struct.unpack("<H", proc.read_bytes(pe + 6, 2))[0]
        opt_sz = struct.unpack("<H", proc.read_bytes(pe + 20, 2))[0]
        sec_start = pe + 24 + opt_sz
        for index in range(n_sec):
            off = sec_start + index * 40
            name = proc.read_bytes(off, 8).split(b"\x00")[0].decode("ascii", errors="ignore")
            vsize = struct.unpack("<I", proc.read_bytes(off + 8, 4))[0]
            vaddr = struct.unpack("<I", proc.read_bytes(off + 12, 4))[0]
            if name == ".text":
                return base + vaddr, vsize
    except OSError:
        return None
    return None


def _find_vtable(proc: ReadOnlyProcess, base: int, module_size: int) -> int | None:
    text = _text_section(proc, base)
    scan_addr, scan_size = text if text else (base, module_size)
    data = proc.read_bytes(scan_addr, scan_size)
    regex = _pattern_to_regex(CTOR_PATTERN, CTOR_MASK)
    for match in regex.finditer(data):
        ctor_off = match.start()
        lea_count = 0
        for extra in range(len(CTOR_PATTERN), len(CTOR_PATTERN) + 0x40):
            pos = ctor_off + extra
            if pos + 7 > len(data):
                break
            if data[pos] == 0x48 and data[pos + 1] == 0x8D and data[pos + 2] == 0x05:
                lea_count += 1
                if lea_count != 1:
                    continue
                rel32 = struct.unpack("<i", data[pos + 3 : pos + 7])[0]
                vtable = scan_addr + pos + 7 + rel32
                if base <= vtable < base + module_size:
                    try:
                        first_entry = struct.unpack("<Q", proc.read_bytes(vtable, 8))[0]
                    except OSError:
                        continue
                    if base <= first_entry < base + module_size:
                        return vtable
    return None


def _scan_heap(proc: ReadOnlyProcess, vtable_addr: int) -> list[int]:
    vtable_bytes = struct.pack("<Q", vtable_addr)
    found: list[int] = []
    addr = 0
    mbi = MEMORY_BASIC_INFORMATION()
    kernel32 = ctypes.windll.kernel32
    while addr < 0x7FFFFFFFFFFF:
        ok = kernel32.VirtualQueryEx(proc.handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi))
        if not ok:
            break
        base = mbi.BaseAddress if mbi.BaseAddress else addr
        size = mbi.RegionSize
        if (
            mbi.State == MEM_COMMIT
            and mbi.Type == MEM_PRIVATE
            and mbi.Protect not in (PAGE_NOACCESS, PAGE_GUARD, 0)
            and size < 0x10000000
        ):
            try:
                blob = proc.read_bytes(base, size)
            except OSError:
                blob = b""
            start = 0
            while blob:
                idx = blob.find(vtable_bytes, start)
                if idx == -1:
                    break
                found.append(base + idx)
                start = idx + 8
        addr = base + size
    return found


def _extract_key_hexes(proc: ReadOnlyProcess, objects: list[int], vtable_addr: int) -> list[tuple[str, str]]:
    xor_mask = proc.read_bytes(vtable_addr + 0x78, 32)
    found: dict[str, None] = {}
    for obj in objects:
        try:
            ptr = struct.unpack("<Q", proc.read_bytes(obj + 0x90, 8))[0]
            length = struct.unpack("<Q", proc.read_bytes(obj + 0x98, 8))[0]
            if ptr < 0x10000 or ptr > 0x7FFFFFFFFFFF:
                continue
            if length < 10 or length > 200:
                continue
            encrypted = proc.read_bytes(ptr, int(length))
            decrypted = bytes(encrypted[j] ^ xor_mask[j % 32] for j in range(int(length)))
            text = decrypted.decode("ascii", errors="replace")
            if text.startswith("x'") and text.endswith("'"):
                hex_key = text[2:-1]
                if re.fullmatch(r"[0-9a-fA-F]+", hex_key):
                    found.setdefault(hex_key.lower(), None)
        except OSError:
            continue
    if not found:
        for obj in objects:
            try:
                inline = proc.read_bytes(obj + 0x60, 32)
            except OSError:
                continue
            if all(byte == 0 for byte in inline):
                continue
            decrypted = bytes(inline[j] ^ xor_mask[j % 32] for j in range(32))
            found.setdefault(decrypted.hex(), None)
    results: list[tuple[str, str]] = []
    for hex_key in found:
        raw = bytes.fromhex(hex_key) if len(hex_key) % 2 == 0 else b""
        if len(raw) == 48:
            results.append((hex_key[:64], hex_key[64:]))
        else:
            results.append((hex_key, ""))
    return results


def scan_owned_process(
    pid: int,
    *,
    process_factory: Callable[[int], ReadOnlyProcess] | None = None,
) -> list[tuple[str, str]]:
    proc = (process_factory or ReadOnlyProcess)(pid)
    try:
        module = _module_base_size(proc, ("Weixin.dll", "WeChatWin.dll"))
        if module is None:
            raise HaltError(
                Halt(
                    "READ_FAILURE",
                    "Weixin.dll / WeChatWin.dll was not found in the owned process",
                    {"phase": "G-KEY"},
                )
            )
        base, size = module
        vtable = _find_vtable(proc, base, size)
        if vtable is None:
            raise HaltError(Halt("READ_FAILURE", "CipherConfig vtable was not found", {"phase": "G-KEY"}))
        objects = _scan_heap(proc, vtable)
        if not objects:
            raise HaltError(Halt("READ_FAILURE", "no CipherConfig objects found; is the account logged in?", {"phase": "G-KEY"}))
        keys = _extract_key_hexes(proc, objects, vtable)
        if not keys:
            raise HaltError(Halt("READ_FAILURE", "CipherConfig objects did not yield usable key material", {"phase": "G-KEY"}))
        return keys
    finally:
        proc.close()
