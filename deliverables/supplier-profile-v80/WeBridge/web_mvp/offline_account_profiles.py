"""Read account/company names from stable profile copies with existing local keys.

Names are display metadata, never process ownership or authentication evidence.
No message database, process memory, key recovery, or persistent profile writes.
"""
from contextlib import closing, contextmanager
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import unicodedata

from database_snapshot import (SQLITE_HEADER, SnapshotError, _signature, _copy_file,
                               _hash_file, _load_keys, _decrypt_database, _quick_check)
from database_wal import apply_wal
from offline_accounts import same_account_source

MAX_PROFILE_BYTES = 64 * 1024 * 1024
COMPANY_FIELDS = ('companyId', 'companyName', 'companyShortName', 'companyStatus', 'companyBasis')


def empty_company():
    return {'companyId': '', 'companyName': '', 'companyShortName': '',
            'companyStatus': 'unavailable', 'companyBasis': 'none'}


def display_name(value):
    """Keep display text on one bounded line; never use it as an identifier."""
    if isinstance(value, bytes):
        value = value.decode('utf8')
    if not isinstance(value, str):
        return ''
    return ''.join(' ' if unicodedata.category(c).startswith('C') else c for c in value).strip()[:120]


def _existing_keys(paths, client):
    keys = {}
    for path in list(paths)[:16]:
        try:
            if client == 'wecom':
                from wecom_snapshot import load_keys
                loaded = load_keys(path)
            else:
                loaded = _load_keys(path)
            for name, values in loaded.items():
                keys.setdefault(name, []).extend(key for key in values if key not in keys.get(name, []))
        except (OSError, ValueError):
            continue
    return keys


def _capture(path, root, temporary):
    """Copy just one contact DB and its committed WAL; never SQLite-open live data."""
    def inventory():
        files = [path]
        wal = Path(str(path) + '-wal')
        if wal.exists():
            files.append(wal)
        journal = Path(str(path) + '-journal')
        if journal.exists() and journal.stat().st_size:
            raise SnapshotError('profile_changing')
        for item in files:
            if (item.is_symlink() or not item.resolve().is_relative_to(root)
                    or not item.is_file() or item.stat().st_size > MAX_PROFILE_BYTES):
                raise SnapshotError('profile_unavailable')
        return {p: _signature(p) for p in files}
    before = inventory()
    hashes = {p: _copy_file(p, temporary / p.name) for p in before}
    if (before != inventory() or any(_hash_file(p) != digest for p, digest in hashes.items())
            or before != inventory()):
        raise SnapshotError('profile_changing')


@contextmanager
def _profile_database(path, root, keys, client):
    with tempfile.TemporaryDirectory(prefix='local-account-info-') as folder:
        temporary = Path(folder)
        _capture(path, root, temporary)
        raw = temporary / path.name
        plain = temporary / 'profile.sqlite'
        with raw.open('rb') as stream:
            first = stream.read(65536)
        cipher = None
        if client != 'wecom' or first.startswith(SQLITE_HEADER):
            if client == 'wechat' and not first.startswith(SQLITE_HEADER) and not keys.get(first[:16]):
                raise SnapshotError('profile_key_unavailable')
            size, cipher = _decrypt_database(raw, plain, keys if client == 'wechat' else {})
        else:
            from wecom_crypto import WeComCipher, page_size
            size = page_size(first)
            candidates = keys.get(path.name, []) + keys.get('user.db', []) + keys.get('*', [])
            if path.name == 'company.db':
                candidates = list(dict.fromkeys(candidates + [key for values in keys.values() for key in values]))
            cipher = next((WeComCipher(key) for key in candidates
                           if WeComCipher(key).verifies(first[:size])), None)
            if cipher is None:
                raise SnapshotError('profile_key_unavailable')
            with raw.open('rb') as incoming, plain.open('xb') as outgoing:
                number = 1
                while data := incoming.read(size):
                    if len(data) != size:
                        raise SnapshotError('profile_incomplete')
                    outgoing.write(cipher.page(data, number))
                    number += 1
        wal = temporary / (path.name + '-wal')
        if wal.exists():
            apply_wal(plain, wal, expected_page_size=size, decode_page=cipher.page if cipher else None)
        _quick_check(plain)
        with closing(sqlite3.connect(plain.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
            db.execute('PRAGMA query_only=ON')
            yield db


def _company_profile(root, keys, corporation):
    """Match this user's corporation exactly; never choose a cached contact's company."""
    result = empty_company()
    if corporation in (None, '', 0, '0'):
        result['companyStatus'] = 'unknown_id'
        return result
    try:
        company_id = corporation.decode('utf8') if isinstance(corporation, bytes) else str(corporation)
    except UnicodeError:
        return result
    result.update(companyId=company_id, companyBasis='user_table.corp_id')
    path = root / 'company.db'
    if not path.exists():
        result['companyStatus'] = 'missing_database'
        return result
    try:
        with _profile_database(path, root, keys, 'wecom') as db:
            columns = {row[1] for row in db.execute('PRAGMA table_info("external_company_table_v2")')}
            if not {'corpany_id', 'name'} <= columns:
                result['companyStatus'] = 'unsupported_schema'
                return result
            short = 'short_name' if 'short_name' in columns else 'NULL'
            rows = db.execute('SELECT name,' + short + ' FROM external_company_table_v2 '
                              'WHERE CAST(corpany_id AS TEXT)=? LIMIT 3', (result['companyId'],)).fetchall()
            if len(rows) != 1:
                result['companyStatus'] = 'ambiguous' if rows else 'name_not_found'
            else:
                name, short_name = (display_name(value) for value in rows[0])
                result.update(companyName=name, companyShortName=short_name,
                              companyStatus='matched' if name or short_name else 'name_not_found',
                              companyBasis='user_table.corp_id->company.db:external_company_table_v2.corpany_id')
    except (OSError, ValueError, sqlite3.Error, UnicodeError) as error:
        result['companyStatus'] = ('needs_key' if isinstance(error, SnapshotError)
                                   and str(error) == 'profile_key_unavailable' else 'unavailable')
    return result


def _read_profile(path, root, keys, client, ids):
    company = empty_company() if client == 'wecom' else {}
    with _profile_database(path, root, keys, client) as db:
        table, identity, fields = ('user_table', 'id', ('name', 'real_name')) if client == 'wecom' else ('contact', 'username', ('nick_name',))
        columns = {r[1] for r in db.execute('PRAGMA table_info("' + table + '")')}
        if not {identity, *fields} <= columns:
            return [], 'unsupported_schema', company
        has_company = client == 'wecom' and 'corp_id' in columns
        selected = ','.join('"' + field + '"' for field in (identity, *fields)) + (',corp_id' if has_company else '')
        placeholders = ','.join('?' for _ in ids)
        rows = db.execute('SELECT ' + selected + ' FROM "' + table + '" WHERE CAST("' + identity + '" AS TEXT) IN (' + placeholders + ') LIMIT 3', ids).fetchall()
        values = []
        for row in rows:
            label = next((display_name(value) for value in row[1:len(fields) + 1] if display_name(value)), '')
            if label:
                values.append((str(row[0].decode('utf8') if isinstance(row[0], bytes) else row[0]), label))
        if has_company and len(rows) == 1 and len(values) == 1:
            company = _company_profile(root, keys, rows[0][-1])
        return values, table, company


def _enterprise_memberships(root, keys):
    """Windows WeCom self-enterprise list; field 5 is a creator, not our nickname.

    Only the exact account ID (2), corporation ID (1), short name (3) and full
    name (24) are used. Never infer membership from arbitrary contact records.
    """
    from wecom_content import fields
    with _profile_database(root / 'company.db', root, keys, 'wecom') as db:
        columns = {row[1] for row in db.execute('PRAGMA table_info("self_corp_list_table")')}
        if not {'corpany_id', 'self_corp_info'} <= columns:
            return []
        rows = db.execute('SELECT CAST(corpany_id AS TEXT),self_corp_info FROM self_corp_list_table '
                          'WHERE length(self_corp_info)<=65536 LIMIT 33').fetchall()
        if len(rows) > 32:
            return []
        result = []
        for corporation, blob in rows:
            decoded = fields(blob)
            selected = {number: [(wire, value) for field, wire, value in decoded if field == number]
                        for number in (1, 2, 3, 24)}
            if (any(len(selected[number]) != 1 or selected[number][0][0] != wire
                    for number, wire in ((1, 0), (2, 0), (3, 2)))
                    or len(selected[24]) > 1 or (selected[24] and selected[24][0][0] != 2)):
                continue
            corp_id, account_id = selected[1][0][1], selected[2][0][1]
            if corp_id <= 0 or account_id <= 0 or str(corp_id) != corporation:
                continue
            try:
                short = display_name(selected[3][0][1])
                name = display_name(selected[24][0][1]) if selected[24] else ''
            except UnicodeError:
                continue
            if name or short:
                result.append((str(account_id), str(corp_id), name, short))
        return result


def _complete_enterprises(targets, keys, deadline):
    """Fill missing companies from readable own-enterprise lists, without keys recovery."""
    observations = {}
    for root in dict.fromkeys(root for _, root, _ in targets):
        if time.monotonic() > deadline:
            break
        try:
            for account, corporation, name, short in _enterprise_memberships(root, keys):
                observations.setdefault(account, set()).add((corporation, name, short))
        except (OSError, ValueError, sqlite3.Error, UnicodeError):
            continue
    for row, _, ids in targets:
        if row.get('companyStatus') == 'matched' or len(ids) != 1:
            continue
        matches = observations.get(ids[0], set())
        if not matches:
            continue
        corporations = {corp for corp, _, _ in matches}
        names = {name for _, name, _ in matches if name}
        shorts = {short for _, _, short in matches if short}
        if len(corporations) != 1 or len(names) > 1 or len(shorts) > 1:
            row.update(empty_company(), companyStatus='ambiguous_membership')
        else:
            row.update(companyId=next(iter(corporations)), companyName=next(iter(names), ''),
                       companyShortName=next(iter(shorts), ''), companyStatus='matched',
                       companyBasis='self_corp_list_table:exact_account_id')


def account_profiles(discovery, client='wechat', *, key_files=(), configured_source='', configured_self_id=''):
    """Enrich discovered accounts with names; unknown names never affect selection.

    Uses at most 16 explicitly supplied key files and 32 discovered accounts.
    Folder-derived IDs only support exact contact lookup, not self-ID assignment.
    """
    keys = _existing_keys(key_files, client)
    rows = []
    targets = []
    deadline = time.monotonic() + 10
    for index, item in enumerate(discovery.get('accounts', [])):
        row = {**item, 'accountNickname': '', 'accountId': '', 'profileStatus': 'unavailable', 'profileBasis': 'none'}
        if client == 'wecom':
            row.update(empty_company())
        try:
            source = Path(item['sourceRoot'])
            if source.is_symlink():
                raise SnapshotError('profile_unavailable')
            source = source.resolve()
            child = source / ('Data' if client == 'wecom' else 'db_storage')
            root = child if child.is_dir() else source
            account = root.parent if root.name in ('Data', 'db_storage') else root
            ids = []
            if configured_self_id and same_account_source(root, configured_source, client):
                ids = [str(configured_self_id)]
                basis = 'configured_self_id'
            else:
                label = account.name
                if client == 'wecom' and label.isdigit():
                    ids = [label]
                elif client == 'wechat' and re.fullmatch(r'wxid_[A-Za-z0-9_]+', label):
                    ids = [label]
                    if re.search(r'_[0-9a-fA-F]{4}$', label):
                        ids.append(label.rsplit('_', 1)[0])
                basis = 'directory_id_exact_contact'
            if not ids:
                row['profileStatus'] = 'unknown_id'
            elif index >= 32 or time.monotonic() > deadline:
                row['profileStatus'] = 'not_checked'
            else:
                if root.is_symlink():
                    raise SnapshotError('profile_unavailable')
                if client == 'wecom':
                    targets.append((row, root.resolve(), ids))
                relative = 'user.db' if client == 'wecom' else 'contact/contact.db'
                path = root / relative
                if client == 'wechat' and not path.is_file():
                    path = root / 'contact.db'
                values, table, company = _read_profile(path, root.resolve(), keys, client, ids)
                unique = set(values)
                if len(values) == 1 and len(unique) == 1:
                    row.update(accountId=values[0][0], accountNickname=values[0][1], profileStatus='matched',
                               profileBasis=basis + ':' + table, identityStatus='self_id_matched')
                    row.update(company)
                else:
                    row['profileStatus'] = 'ambiguous' if len(values) > 1 else 'name_not_found'
        except (OSError, ValueError, sqlite3.Error, UnicodeError) as error:
            # No paths, SQL content, keys or contact values leave error diagnostics.
            row['profileStatus'] = 'needs_key' if isinstance(error, SnapshotError) and str(error) == 'profile_key_unavailable' else 'unavailable'
        rows.append(row)
    if client == 'wecom':
        _complete_enterprises(targets, keys, deadline)
    return {**discovery, 'accounts': rows}
