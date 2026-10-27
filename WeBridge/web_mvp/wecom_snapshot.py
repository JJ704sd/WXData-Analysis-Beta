"""Stable copies of one selected WeCom account; never SQLite-open live files."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import tempfile
import time

from database_snapshot import (SnapshotError, SourceChanged, SQLITE_HEADER,
    _signature, _hash_file, _copy_file, _quick_check, _remove_owned)
from database_wal import apply_wal, MAX_DATABASE_BYTES
from wecom_crypto import WeComCipher, page_size

CORE = ('message.db', 'session.db', 'user.db')


def data_root(value):
    source = Path(value).expanduser().resolve(strict=True)
    return source / 'Data' if (source / 'Data/message.db').is_file() else source


def inventory(source, include_media=True):
    result = {}
    if not all((source / name).is_file() for name in CORE):
        raise SnapshotError('请选择单个企微账号目录、其 Data 目录，或包含 message.db/session.db/user.db 的副本。')
    for name in CORE + (('file.db',) if include_media else ()):
        db = source / name
        if not db.exists():
            continue
        journal = Path(str(db) + '-journal')
        if journal.exists() and journal.stat().st_size:
            raise SnapshotError('企微库存在非空回滚日志，请等待客户端写入结束后重试。')
        for path in (db, Path(str(db) + '-wal')):
            if not path.exists():
                continue
            if path.is_symlink() or path.resolve().parent != source or path.stat().st_size > MAX_DATABASE_BYTES:
                raise SnapshotError('企微数据包含越界链接或文件超过16GiB，未读取。')
            result[path.name] = _signature(path)
    return result


def load_keys(path):
    if not path:
        return {}
    try:
        path = Path(path)
        if path.stat().st_size > 65536:
            raise ValueError
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        rows = value['wecomKeys']
        if not isinstance(rows, list) or len(rows) > 32:
            raise ValueError
        result = {}
        for row in rows:
            name, key = row['database'], row['key_hex']
            if name not in CORE + ('file.db', '*') or not isinstance(key, str) or not re.fullmatch('[a-fA-F0-9]{32}', key):
                raise ValueError
            result.setdefault(name, []).append(bytes.fromhex(key))
        return result
    except (OSError, ValueError, KeyError, TypeError):
        raise SnapshotError('企微密钥文件无效：需要 wecomKeys 数组及 database/key_hex（16字节主密钥）；个微密钥不能混用。') from None


def prepare_snapshot(source, output, keys_file=None, *, include_media=True, **_):
    source, output = data_root(source), Path(output).resolve()
    account = source.parent if source.name == 'Data' else source
    if output == account or output.is_relative_to(account):
        raise SnapshotError('程序输出目录不能位于企微账号数据目录内。')
    keys = load_keys(keys_file)
    inventory(source, include_media)
    output.mkdir(parents=True, exist_ok=True)
    generation = Path(tempfile.mkdtemp(prefix='wecom-snapshot-', dir=output))
    try:
        for attempt in range(3):
            raw = generation / ('.raw-' + str(attempt))
            raw.mkdir()
            try:
                before = inventory(source, include_media)
                hashes = {name: _copy_file(source / name, raw / name) for name in before}
                if before != inventory(source, include_media) or any(
                        _signature(source / name) != stamp or _hash_file(source / name) != hashes[name]
                        for name, stamp in before.items()) or before != inventory(source, include_media):
                    raise SourceChanged('复制期间企微数据发生变化。')
                break
            except (SourceChanged, FileNotFoundError):
                _remove_owned(raw, generation)
                if attempt == 2:
                    raise SnapshotError('企微数据持续变化，无法获得稳定副本，请稍后刷新。') from None
                time.sleep(.05)
        reports = []
        for name in before:
            if name.endswith('-wal'):
                continue
            target = generation / name
            with (raw / name).open('rb') as incoming:
                first = incoming.read(65536)
                size = page_size(first)
                encrypted = not first.startswith(SQLITE_HEADER)
                cipher = None
                if encrypted:
                    cipher = next((WeComCipher(key) for key in keys.get(name, []) + keys.get('*', [])
                                   if WeComCipher(key).verifies(first[:size])), None)
                    if cipher is None:
                        raise SnapshotError(name + '：未找到匹配的企微密钥；登录该账号后点击“一键自动配置”，或选择已有企微密钥。')
                incoming.seek(0)
                with target.open('xb') as outgoing:
                    number = 1
                    while payload := incoming.read(size):
                        if len(payload) != size:
                            raise SnapshotError('企微数据库存在不完整页，未发布副本。')
                        outgoing.write(cipher.page(payload, number) if cipher else payload)
                        number += 1
            wal = name + '-wal'
            wal_report = {'status': 'absent', 'committedFrames': 0}
            if wal in before:
                wal_report = apply_wal(target, raw / wal, expected_page_size=size, decode_page=cipher.page if cipher else None)
            _quick_check(target)
            reports.append({'path': name, 'sourceSha256': hashes[name], 'encrypted': encrypted, 'integrity': 'ok', 'wal': wal_report})
        _remove_owned(raw, generation)
        metadata = {'client': 'wecom', 'createdAt': datetime.now(timezone.utc).isoformat(),
                    'sourceRoot': str(source), 'integrity': 'ok', 'databases': reports}
        (generation / 'manifest.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf8')
        return {'root': generation, 'source': metadata}
    except Exception:
        _remove_owned(generation, output)
        raise


def discover(roots=None):
    if roots is None:
        from windows_data_paths import wecom_path_hints
        roots = wecom_path_hints()
    accounts, issues, seen = [], [], set()
    for root in map(Path, roots):
        if not root.is_dir() or root.is_symlink():
            continue
        candidates = [root] if (root / 'message.db').is_file() or (root / 'Data/message.db').is_file() else list(root.iterdir())
        for candidate in candidates:
            if not candidate.is_dir() or candidate.is_symlink():
                continue
            try:
                source = data_root(candidate)
                if source in seen or not (source / 'message.db').is_file():
                    continue
                before = inventory(source)
                seen.add(source)
                account = source.parent if source.name == 'Data' else source
                stamp = (source / 'message.db').stat()
                accounts.append({'client': 'wecom', 'directoryLabel': account.name, 'sourceRoot': str(account.resolve()),
                                 'identityStatus': 'directory_label_only',
                                 'messageDbBytes': stamp.st_size, 'messageDbModifiedNs': stamp.st_mtime_ns,
                                 'messageDbModifiedAt': datetime.fromtimestamp(stamp.st_mtime, timezone.utc).isoformat(),
                                 'databases': [{'path': name, 'bytes': sig[0]} for name, sig in before.items() if not name.endswith('-wal')]})
            except (OSError, ValueError):
                issues.append({'sourceRoot': str(candidate), 'status': 'unavailable_or_changing'})
    return {'accounts': accounts, 'issues': issues, 'note': '仅列出企微本地账号；多个账号时需明确选择，不混用个微配置。'}
