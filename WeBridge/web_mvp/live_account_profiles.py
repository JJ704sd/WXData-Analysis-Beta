"""Explicit, bounded nickname lookup for uniquely owned running accounts."""
from pathlib import Path
import multiprocessing
import os
import re
import sys
import tempfile

from offline_account_profiles import _read_profile, display_name, COMPANY_FIELDS
from offline_accounts import _windows_owners

PROFILE_FIELDS = ('accountNickname', 'accountId', 'profileStatus', 'profileBasis', 'identityStatus') + COMPANY_FIELDS


def _source_profile(source, client):
    original = Path(source)
    if original.is_symlink():
        raise ValueError('redirected_profile')
    root = original.resolve()
    child = root / ('Data' if client == 'wecom' else 'db_storage')
    root = child if child.is_dir() else root
    path = root / ('user.db' if client == 'wecom' else 'contact/contact.db')
    if client == 'wechat' and not path.is_file():
        path = root / 'contact.db'
    if root.is_symlink() or path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('profile_unavailable')
    owned = [path]
    wal = Path(str(path) + '-wal')
    if wal.exists():
        if wal.is_symlink() or not wal.resolve().is_relative_to(root.resolve()):
            raise ValueError('redirected_profile')
        owned.append(wal)
    account = root.parent if root.name in ('Data', 'db_storage') else root
    label = account.name
    ids = [label] if (label.isdigit() if client == 'wecom' else re.fullmatch(r'wxid_[A-Za-z0-9_]+', label)) else []
    if client == 'wechat' and ids and re.search(r'_[0-9a-fA-F]{4}$', label):
        ids.append(label.rsplit('_', 1)[0])
    if not ids:
        raise ValueError('profile_identity_unknown')
    return root.resolve(), path, owned, ids


def _profile_worker(source, client, owner, outgoing):
    """Only account/company display metadata crosses IPC; keys die with worker."""
    with open(os.devnull, 'w') as sink:
        for descriptor in (1, 2):
            try:
                os.dup2(sink.fileno(), descriptor)
            except OSError:
                pass
        sys.stdout = sys.stderr = sink
        stage = 'owner_check'
        try:
            root, path, owned, ids = _source_profile(source, client)
            if _windows_owners(owned, client) != [owner]:
                raise ValueError('owner_changed')
            if client == 'wechat':
                stage = 'wechat_profile_key'
                from acquire_database_keys import WindowsDependencies, read_first_pages, matching_keys
                dependencies = WindowsDependencies()
                if dependencies.owner(owned) != owner:
                    raise ValueError('owner_changed')
                pages = read_first_pages([path])
                candidates = dependencies.scan(owner[0], expected_start_time=owner[1], max_scan_bytes=4096 * 1024 * 1024, timeout=30)
                verified = matching_keys(pages, candidates)
                del candidates
                keys = {bytes.fromhex(item['salt_hex']): [bytes.fromhex(item['key_hex'])] for item in verified}
                del verified
            else:
                stage = 'wecom_profile_key'
                from wecom_key_recovery import recover_profile_key
                with tempfile.TemporaryDirectory(prefix='live-account-info-') as folder:
                    key = recover_profile_key(root, folder, expected_owner=owner, allow_process_read=True)
                keys = {'user.db': [key]}
                del key
            if _windows_owners(owned, client) != [owner]:
                raise ValueError('owner_changed')
            stage = 'profile_lookup'
            values, table, company = _read_profile(path, root, keys, client, ids)
            del keys
            _root, _path, current_owned, _ids = _source_profile(source, client)
            if current_owned != owned or _windows_owners(current_owned, client) != [owner]:
                raise ValueError('owner_changed')
            if len(values) != 1:
                raise ValueError('profile_not_unique')
            outgoing.send({'accountNickname': values[0][1], 'accountId': values[0][0], 'profileStatus': 'matched',
                           'profileBasis': 'live_owner:' + table, **company})
        except Exception as error:
            from acquire_database_keys import SAFE_CODES
            code = getattr(error, 'code', None)
            issue = code if code in SAFE_CODES else type(error).__name__
            if type(error).__name__ == 'SnapshotError':
                message = str(error)
                for marker, safe_code in [('达到本次时间或读取预算', 'scan_budget_or_timeout'),
                                          ('密钥管理布局暂不支持', 'layout_unsupported'),
                                          ('未找到通过全部', 'key_not_found'),
                                          ('进程归属已变化', 'owner_changed'),
                                          ('模块', 'module_unavailable')]:
                    if marker in message:
                        issue = safe_code
                        break
            outgoing.send({'profileStatus': 'live_unavailable', 'profileIssue': stage + ':' + issue})
        finally:
            outgoing.close()


def read_running_profile(account, client, *, allow_process_read=False):
    """Called only after explicit permission; revalidate owner before/after IPC."""
    if allow_process_read is not True:
        raise ValueError('运行账号昵称需要明确授权临时读取资料密钥。')
    identity = account['processIdentities'][0]
    owner = (int(identity['pid']), int(identity['created']))
    _root, _path, owned, _ids = _source_profile(account['sourceRoot'], client)
    if _windows_owners(owned, client) != [owner]:
        raise ValueError('owner_changed')
    context = multiprocessing.get_context('spawn')
    incoming, outgoing = context.Pipe(duplex=False)
    worker = context.Process(target=_profile_worker, args=(account['sourceRoot'], client, owner, outgoing))
    try:
        worker.start()
        outgoing.close()
        if not incoming.poll(40):
            raise ValueError('live_profile_timeout')
        result = incoming.recv()
        _root, _path, current, _ids = _source_profile(account['sourceRoot'], client)
        if current != owned or _windows_owners(current, client) != [owner]:
            raise ValueError('owner_changed')
        return result
    finally:
        incoming.close()
        outgoing.close()
        if worker.pid is not None:
            worker.join(1)
            if worker.is_alive():
                worker.terminate()  # Only this application's bounded worker.
                worker.join(5)


def complete_live_profiles(discovery, client, *, allow_process_read=False, reader=None):
    if allow_process_read is not True:
        raise ValueError('运行账号昵称需要明确授权临时读取资料密钥。')
    if client not in ('wechat', 'wecom'):
        raise ValueError('unsupported_client')
    read = reader or (lambda row, which: read_running_profile(row, which, allow_process_read=True))
    rows = []
    for account in discovery.get('accounts', []):
        row = dict(account)
        identities = row.get('processIdentities', [])
        eligible = (row.get('processState') == 'active' and len(identities) == 1
                    and row.get('processIds') == [identities[0].get('pid')] and identities[0].get('created'))
        needs_company = client == 'wecom' and not (row.get('companyName') or row.get('companyShortName'))
        if eligible and (not row.get('accountNickname') or needs_company):
            try:
                result = read(row, client)
                if result.get('profileStatus') == 'matched' and result.get('accountId') and result.get('accountNickname'):
                    row.update({key: result[key] for key in PROFILE_FIELDS if key in result})
                    row['identityStatus'] = 'self_id_matched'
                    row['accountNickname'] = display_name(row['accountNickname'])
                    for field in ('companyName', 'companyShortName'):
                        if field in row:
                            row[field] = display_name(row[field])
                else:
                    row['profileStatus'] = 'live_unavailable'
                    row['profileIssue'] = result.get('profileIssue', 'profile_read_failed')
            except Exception as error:
                row['profileStatus'] = 'live_unavailable'
                row['profileIssue'] = 'owner_or_worker:' + type(error).__name__
        rows.append(row)
    return {**discovery, 'accounts': rows}
