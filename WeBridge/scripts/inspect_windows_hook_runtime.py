"""Pinned target constants; the old standalone Frida attachment probe is disabled.

Plain Frida 17.19 detachment crashes an isolated fixture in this environment.
Use run_windows_hook_smoke.py, which owns the resident-agent lifecycle.
Importing this module never opens a process or calls a native function.
"""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
HASH='10f8e995453e2da46d4f2b5080cd6da1f13cc5147746adc119ceae38cb039de5'
CODE_RVAS={'textFactory':0x6F4C70,'textConstructor':0x766680,'optionsConstructor':0xF910,'sendMessage':0x19D0BC0}


def loaded_module_path(pid):
    """Resolve the module of the already-owned PID without attaching an agent."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    class Entry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('th32ModuleID', wintypes.DWORD),
                    ('th32ProcessID', wintypes.DWORD), ('GlblcntUsage', wintypes.DWORD),
                    ('ProccntUsage', wintypes.DWORD), ('modBaseAddr', ctypes.c_void_p),
                    ('modBaseSize', wintypes.DWORD), ('hModule', wintypes.HMODULE),
                    ('szModule', wintypes.WCHAR * 256), ('szExePath', wintypes.WCHAR * 260)]
    kernel.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ('Module32FirstW', 'Module32NextW'):
        function = getattr(kernel, name)
        function.argtypes = (wintypes.HANDLE, ctypes.POINTER(Entry))
        function.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    snapshot = kernel.CreateToolhelp32Snapshot(0x08 | 0x10, pid)
    if snapshot in (None, 0, ctypes.c_void_p(-1).value):
        raise OSError('module_inventory_unavailable')
    try:
        entry = Entry(); entry.dwSize = ctypes.sizeof(entry)
        valid = kernel.Module32FirstW(snapshot, ctypes.byref(entry))
        matches = []
        while valid:
            if entry.szModule.casefold() == 'weixin.dll':
                matches.append(Path(entry.szExePath).resolve(strict=True))
            valid = kernel.Module32NextW(snapshot, ctypes.byref(entry))
        if len(matches) != 1:
            raise OSError('module_not_unique')
        return matches[0]
    finally:
        kernel.CloseHandle(snapshot)


def inspect():
    raise RuntimeError('Standalone attachment is disabled; use the resident-agent smoke harness.')


if __name__ == '__main__':
    raise SystemExit('Direct detach disabled. Use scripts/run_windows_hook_smoke.py --help.')
