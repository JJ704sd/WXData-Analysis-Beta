"""Bind a Weixin process to the authorized account using file-lock owners only."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, NoReturn

from wechat_agent_poc.models import Halt, HaltError

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_DUP_HANDLE = 0x0040
READONLY_PROCESS_ACCESS = PROCESS_VM_READ | PROCESS_QUERY_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_DUP_HANDLE


def weixin_pids() -> list[int]:
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == ctypes.c_void_p(-1).value or snap == wintypes.HANDLE(-1).value:
        return []
    pids: list[int] = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snap, ctypes.byref(entry)):
            return []
        while True:
            name = entry.szExeFile.lower()
            if name in {"weixin.exe", "wechat.exe"}:
                pids.append(int(entry.th32ProcessID))
            if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snap)
    return sorted(set(pids))


ERROR_MORE_DATA = 234


def pids_holding_files(paths: list[Path], *, allow_missing: bool = False) -> tuple[list[int], str | None]:
    """Return PIDs that hold the given files via Restart Manager. Does not scan other accounts.

    Query failures return an error string. Callers must treat that as unknown, not as an empty holder set.
    When allow_missing is true, absent paths are a successful empty result (used for exclusion checks).
    """
    if os.name != "nt":
        return [], "restart_manager_unavailable_non_windows"
    existing = [str(path) for path in paths if path.exists()]
    if not existing:
        if allow_missing:
            return [], None
        return [], "no_target_files"
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return [], "ctypes_unavailable"

    class RM_UNIQUE_PROCESS(ctypes.Structure):
        _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", wintypes.FILETIME)]

    class RM_PROCESS_INFO(ctypes.Structure):
        _fields_ = [
            ("Process", RM_UNIQUE_PROCESS),
            ("strAppName", wintypes.WCHAR * 256),
            ("strServiceShortName", wintypes.WCHAR * 64),
            ("ApplicationType", ctypes.c_int),
            ("AppStatus", wintypes.ULONG),
            ("TSSessionId", wintypes.DWORD),
            ("bRestartable", wintypes.BOOL),
        ]

    rstrtmgr = ctypes.windll.rstrtmgr
    session = wintypes.DWORD()
    session_key = ctypes.create_unicode_buffer(256)
    err = rstrtmgr.RmStartSession(ctypes.byref(session), 0, session_key)
    if err != 0:
        return [], f"RmStartSession failed winerror={err}"
    try:
        arr_type = ctypes.c_wchar_p * len(existing)
        resources = arr_type(*existing)
        err = rstrtmgr.RmRegisterResources(session, len(existing), resources, 0, None, 0, None)
        if err != 0:
            return [], f"RmRegisterResources failed winerror={err}"
        needed = wintypes.UINT(0)
        reused = wintypes.UINT(0)
        reboot = wintypes.DWORD()
        err = rstrtmgr.RmGetList(session, ctypes.byref(needed), ctypes.byref(reused), None, ctypes.byref(reboot))
        if err not in (0, ERROR_MORE_DATA):
            return [], f"RmGetList failed winerror={err}"
        if needed.value == 0:
            if err == 0:
                return [], None
            return [], f"RmGetList failed winerror={err}"
        infos = (RM_PROCESS_INFO * needed.value)()
        count = wintypes.UINT(needed.value)
        reboot = wintypes.DWORD(0)
        err = rstrtmgr.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), infos, ctypes.byref(reboot))
        if err != 0:
            return [], f"RmGetList failed winerror={err}"
        pids = sorted({int(infos[i].Process.dwProcessId) for i in range(count.value)})
        return pids, None
    finally:
        rstrtmgr.RmEndSession(session)


def _halt_unknown(query: str, error: str, extra: dict[str, object] | None = None) -> NoReturn:
    details = {
        "phase": "G-KEY",
        "reason": "ownership_query_unknown",
        "query": query,
        "platform_error": error,
    }
    if extra:
        details.update(extra)
    raise HaltError(
        Halt(
            "READ_FAILURE",
            f"{query} ownership query failed; treating result as unknown",
            details,
        )
    )


def resolve_owned_weixin_pid(
    account_files: list[Path],
    *,
    excluded_files: list[Path] | None = None,
    pid_lookup: Callable[[], list[int]] | None = None,
    holders_lookup: Callable[[list[Path]], tuple[list[int], str | None]] | None = None,
) -> dict[str, object]:
    weixin = (pid_lookup or weixin_pids)()
    if holders_lookup is not None:
        holders, holder_error = holders_lookup(account_files)
    else:
        holders, holder_error = pids_holding_files(account_files, allow_missing=False)
    if holder_error:
        _halt_unknown("target", holder_error, {"weixin_pid_count": len(weixin)})
    excluded_holders: list[int] = []
    excluded_queried = False
    if excluded_files is not None:
        excluded_queried = True
        if holders_lookup is not None:
            excluded_holders, excluded_error = holders_lookup(excluded_files)
        else:
            excluded_holders, excluded_error = pids_holding_files(excluded_files, allow_missing=True)
        if excluded_error:
            _halt_unknown("excluded", excluded_error, {"weixin_pid_count": len(weixin)})
    owned = [pid for pid in holders if pid in set(weixin)]
    overlap = sorted(set(owned) & set(excluded_holders))
    if overlap:
        raise HaltError(
            Halt(
                "READ_FAILURE",
                "candidate Weixin process also holds an excluded account database; refusing key extraction",
                {
                    "phase": "G-KEY",
                    "owned_pid_count": len(owned),
                    "excluded_holder_count": len(excluded_holders),
                    "overlap_pid_count": len(overlap),
                    "reason": "process_account_not_unique",
                    "target_query_ok": True,
                    "excluded_query_ok": excluded_queried,
                },
            )
        )
    if not owned:
        raise HaltError(
            Halt(
                "READ_FAILURE",
                "no Weixin process is holding the authorized account database files",
                {
                    "weixin_pid_count": len(weixin),
                    "holder_pid_count": len(holders),
                    "excluded_holder_count": len(excluded_holders),
                    "phase": "G-KEY",
                    "reason": "authorized_account_not_live",
                    "target_query_ok": True,
                    "excluded_query_ok": excluded_queried,
                },
            )
        )
    if len(owned) != 1:
        raise HaltError(
            Halt(
                "READ_FAILURE",
                "authorized account files are held by more than one Weixin process",
                {
                    "owned_pid_count": len(owned),
                    "phase": "G-KEY",
                    "reason": "ownership_not_unique",
                    "target_query_ok": True,
                    "excluded_query_ok": excluded_queried,
                },
            )
        )
    return {
        "pid_recorded": True,
        "weixin_pid_count": len(weixin),
        "holder_pid_count": len(holders),
        "excluded_holder_count": len(excluded_holders),
        "overlap_pid_count": 0,
        "candidate_count": 1,
        "target_query_ok": True,
        "excluded_query_ok": excluded_queried,
        "method": "restart_manager_file_owners",
        "platform": sys.platform,
        "pid": owned[0],
    }


def open_readonly_process(pid: int):
    if os.name != "nt":
        raise HaltError(Halt("READ_FAILURE", "live process access is only implemented on Windows"))
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(READONLY_PROCESS_ACCESS, False, int(pid))
    if not handle:
        err = ctypes.GetLastError()
        raise HaltError(
            Halt(
                "READ_FAILURE",
                "OpenProcess for read-only VM access was refused",
                {"platform_error": f"winerror={err}", "pid_recorded": True, "phase": "G-KEY"},
            )
        )
    return handle
