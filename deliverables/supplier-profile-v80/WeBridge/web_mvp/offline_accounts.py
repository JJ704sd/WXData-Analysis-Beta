"""Match discovered databases to live client file owners without reading memory."""
from collections import Counter
import ctypes
from ctypes import wintypes
import os
import time
from pathlib import Path


def same_account_source(left, right, client='wechat'):
    """Compare a selected account folder with its actual database-folder alias.

    Names and account IDs are not identity evidence. Only paths resolving to
    the same database directory match; different accounts remain distinct.
    """
    if not left or not right:
        return False
    def database_root(value):
        path = Path(value).expanduser().resolve()
        child = path / ('Data' if client == 'wecom' else 'db_storage')
        return child.resolve() if child.is_dir() else path
    try:
        return database_root(left) == database_root(right)
    except (OSError, ValueError, RuntimeError):
        return False


def _windows_owners(paths, client):
    if os.name != 'nt':
        raise OSError('process_metadata_unavailable')
    from acquire_database_keys import WindowsDependencies, declare_windows_apis
    kernel = declare_windows_apis()
    allowed = {'wxwork.exe'} if client == 'wecom' else {'weixin.exe', 'wechat.exe'}
    found = []
    for pid, expected_start in WindowsDependencies._file_owners(paths):
        handle = kernel.OpenProcess(0x1000, False, pid)  # Query metadata only.
        if not handle:
            raise OSError('process_metadata_unavailable')
        try:
            created, ended, used, user = (wintypes.FILETIME() for _ in range(4))
            size = wintypes.DWORD(32768)
            name = ctypes.create_unicode_buffer(size.value)
            if not kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)):
                raise OSError('process_metadata_unavailable')
            if Path(name.value).name.lower() not in allowed:
                continue
            if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(ended), ctypes.byref(used), ctypes.byref(user)):
                raise OSError('process_metadata_unavailable')
            actual = (created.dwHighDateTime << 32) | created.dwLowDateTime
            if actual != expected_start or ended.dwHighDateTime or ended.dwLowDateTime:
                raise OSError('process_changed')
            found.append((int(pid), actual))
        finally:
            kernel.CloseHandle(handle)
    return sorted(set(found))


def client_processes(client):
    """Enumerate client presence and metadata access without reading memory."""
    if os.name != 'nt':
        raise OSError('process_metadata_unavailable')
    from acquire_database_keys import declare_windows_apis
    kernel = declare_windows_apis()
    class Entry(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD), ('pid', wintypes.DWORD),
            ('heap', ctypes.c_size_t), ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
            ('parent', wintypes.DWORD), ('priority', wintypes.LONG), ('flags', wintypes.DWORD),
            ('name', wintypes.WCHAR * 260)]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise OSError('process_metadata_unavailable')
    names = {'wxwork.exe'} if client == 'wecom' else {'weixin.exe', 'wechat.exe'}
    result = []
    try:
        entry = Entry(); entry.size = ctypes.sizeof(Entry)
        if not kernel.Process32FirstW(snapshot, ctypes.byref(entry)):
            raise OSError('process_metadata_unavailable')
        while True:
            if entry.name.lower() in names:
                handle = kernel.OpenProcess(0x1000, False, entry.pid)
                accessible = False
                try:
                    size = wintypes.DWORD(32768); path = ctypes.create_unicode_buffer(size.value)
                    accessible = bool(handle and kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)))
                finally:
                    if handle: kernel.CloseHandle(handle)
                result.append({'pid': int(entry.pid), 'metadataReadable': accessible})
            if not kernel.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel.CloseHandle(snapshot)
    return result


def account_process_hint(row, scan, client='wechat'):
    state = row.get('processState')
    label = '企微' if client == 'wecom' else '微信'
    if state == 'active':
        return '已确认此目录被当前客户端占用。'
    if state == 'ambiguous':
        return '此目录有多个客户端进程占用，不能确定唯一进程；请核对账号并重新识别。'
    if state == 'unavailable':
        if row.get('processIssue') in ('probe_limit', 'probe_incomplete'):
            return '本次文件占用检查未完成，不能据此判断目录未在使用；请手动核对数据源。'
        return '所选目录的占用查询或进程元数据不可读；请核对工具与客户端的 Windows 用户及权限级别、访问限制。已有密钥或离线副本仍可加载。'
    if state == 'inactive':
        count = scan.get('clientProcessCount')
        if count:
            return f'检测到 {count} 个{label}进程，但未确认进程持有所选数据库；请在客户端设置核对实际数据目录。多个进程不等于多个账号；已有密钥或离线副本仍可加载。'
        if count == 0:
            return f'未发现{label}客户端进程；请先登录对应账号。已有密钥或离线副本仍可加载。'
        return '所选数据库未查到持有进程，客户端进程检测也未完成；请核对权限与数据目录。已有密钥或离线副本仍可加载。'
    return '所选目录的进程归属尚未检查。'


def identify_accounts(discovery, client='wechat', *, ownership=None):
    """Return current file-owner evidence and a unique running-account suggestion.

    Discovery remains directory metadata. A suggestion needs one verified live
    owner and never authenticates an account or retrieves a decryption key.
    """
    lookup = ownership or _windows_owners
    rows, seen = [], set()
    for account in discovery.get('accounts', []):
        row = {**account, 'processState': 'unavailable', 'processIds': [], 'processIdentities': [], 'processIssue': 'no_database_files', 'processFileCount': 0}
        try:
            original = Path(account['sourceRoot'])
            if original.is_symlink():
                raise OSError('redirected_source')
            source = original.resolve()
            child = source / ('Data' if client == 'wecom' else 'db_storage')
            root = child if child.is_dir() else source
            if root.is_symlink():
                raise OSError('redirected_source')
            if root in seen:
                continue
            seen.add(root)
            paths = []
            complete = True
            for database in account.get('databases', []):
                relative = Path(database['path'])
                path = root / relative
                if (relative.is_absolute() or '..' in relative.parts or path.is_symlink()
                        or not path.resolve().is_relative_to(root.resolve()) or path.suffix.lower() != '.db'):
                    complete = False
                    continue
                if path.is_file():
                    paths.append(path)
                    for suffix in ('-wal', '-shm'):
                        companion = Path(str(path) + suffix)
                        if companion.exists():
                            if companion.is_file() and not companion.is_symlink() and companion.resolve().is_relative_to(root.resolve()):
                                paths.append(companion)
                            else:
                                complete = False
                else:
                    complete = False
                if len(paths) > 32:
                    complete = False
                    break
            if paths and len(rows) < 32:
                row['processFileCount'] = len(paths[:32])
                owners = sorted(set(lookup(paths[:32], client)))
                row['processIds'] = [int(pid) for pid, _created in owners]
                row['processIdentities'] = [{'pid': int(pid), 'created': int(created)} for pid, created in owners]
                if complete:
                    row['processState'] = 'active' if len(owners) == 1 else ('ambiguous' if owners else 'inactive')
                    row['processIssue'] = 'multiple_owners' if len(owners) > 1 else None
                else:
                    row['processIssue'] = 'probe_incomplete'
            elif len(rows) >= 32:
                row['processIssue'] = 'probe_limit'
        except (OSError, ValueError, KeyError):
            row['processIssue'] = 'metadata_unavailable'
        rows.append(row)
    now = time.time_ns()
    for row in rows:
        modified = row.get('messageDbModifiedNs')
        older_copy = modified is not None and any(
            other.get('directoryLabel') == row.get('directoryLabel') and
            other.get('messageDbModifiedNs', 0) > modified and
            other.get('messageDbBytes', 0) > row.get('messageDbBytes', 0) for other in rows)
        row['stale'] = row['processState'] != 'active' and bool(modified and
            (now - modified > 90 * 86400 * 10**9 or older_copy))
    rows.sort(key=lambda row: (row['processState'] != 'active', row['stale'],
                              -row.get('messageDbBytes', 0), -row.get('messageDbModifiedNs', 0),
                              row.get('directoryLabel', '').casefold(), row['sourceRoot']))
    active = [row for row in rows if row['processState'] == 'active']
    incomplete = bool(discovery.get('issues')) or any(row['processState'] not in ('active', 'inactive') for row in rows)
    recommended = active[0]['sourceRoot'] if len(active) == 1 and not incomplete else None
    result = {**discovery, 'accounts': rows, 'activeAccountCount': len(active),
              'scanIncomplete': incomplete, 'recommendedSourceRoot': recommended}
    if ownership is None:
        try:
            processes = client_processes(client)
            result.update(clientProcessCount=len(processes),
                          processMetadataDenied=sum(not p['metadataReadable'] for p in processes))
        except OSError:
            result.update(clientProcessCount=None, processMetadataDenied=None)
    return result


def account_scan_hint(scan):
    count = len(scan.get('accounts', [])); active = scan.get('activeAccountCount', 0)
    if not count:
        return '未发现本地账号目录；请确认客户端已登录，或在手动页选择实际数据库目录。'
    if active > 1:
        return f'检出 {active} 个在用账号，请在下拉列表选择要解析的账号；不会按文件体积或修改时间自动选账号。'
    if scan.get('scanIncomplete'):
        return '部分账号的文件占用检查未完成；请核对数据目录和访问权限。已有密钥的离线副本仍可加载。'
    if active == 0:
        return '已发现本地账号目录，但均未检出客户端占用；可能是离线或旧副本，请核对实际数据目录。可使用匹配的已有密钥加载离线副本。'
    return '已确认一个在用账号；请核对当前选择的数据库目录。'


def diagnostic_report(scan, client):
    """Safe explicit report: no paths, account IDs, profiles, keys or DB contents."""
    return {'schemaVersion': 1, 'client': client, 'scope': 'process_metadata_and_file_owners',
        'clientProcessCount': scan.get('clientProcessCount'),
        'processMetadataDenied': scan.get('processMetadataDenied'),
        'activeAccountCount': scan['activeAccountCount'], 'scanIncomplete': scan['scanIncomplete'],
        'discoveryIssueCount': len(scan.get('issues', [])),
        'accounts': [{'index': index, 'databaseCount': len(row.get('databases', [])),
            'ownershipFileCount': row['processFileCount'], 'processState': row['processState'],
            'processIssue': row['processIssue'], 'ownerCount': len(row['processIds']),
            'hint': account_process_hint(row, scan, client)} for index, row in enumerate(scan['accounts'], 1)]}


def group_choices(groups, query='', last_chat=None):
    """Search actual names/IDs, put the remembered group first, distinguish names."""
    needle = query.strip().casefold()
    duplicates = Counter(row['name'] for row in groups)
    choices = []
    for row in groups:
        if needle and needle not in (row['name'] + ' ' + row['id']).casefold():
            continue
        name = row['name'] + (' · ' + row['id'] if duplicates[row['name']] > 1 else '')
        choices.append({**row, 'choiceLabel': name, 'recent': row['id'] == last_chat})
    return sorted(choices, key=lambda row: (not row['recent'], row['name'].casefold(), row['id']))
