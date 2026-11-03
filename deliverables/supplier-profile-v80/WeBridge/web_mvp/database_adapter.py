"""Read-only WeChat 4.x plaintext snapshots, never a client/key extractor.

The caller prepares a consistent copy with WAL merged before configure(). Source
databases are opened with mode=ro&immutable=1 and never copied or changed here.
Schema handling is independently implemented from the reference's field layout.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading

from backend import BridgeError
from rich_content import parse_content, xml_root


SQLITE_HEADER = b'SQLite format 3\0'
SHARD_NAME = re.compile(r'(?:biz_)?message_[0-9]+\.db', re.I)
GROUP_ID = re.compile(r'[A-Za-z0-9_.-]+@(?:im\.)?chatroom')
MAX_TEXT = 4 * 1024 * 1024
MAX_METADATA = 200000


class DatabaseError(BridgeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _text(value):
    if value is None:
        return ''
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode('utf-8')
    return str(value)


def _name(value):
    value = _text(value).strip()
    if len(value) > 256 or any(ord(c) < 32 for c in value):
        raise ValueError('invalid identifier')
    return value


def _quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def _fingerprint(path):
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def _warning(code, message, file=None, message_id=None):
    result = {'code': code, 'message': message}
    if file is not None:
        result['file'] = file
    if message_id is not None:
        result['messageId'] = message_id
    return result


def has_blocking_warnings(warnings):
    """Only a failed display label is unrelated to automated message identity.

    All other warnings, including unfamiliar codes, still block automation.
    The adapter emits this exception only after validating the contact ID and
    deletion flag; callers continue showing the warning to the user.
    """
    return any(row['code'] != 'contact_label_decode_error' for row in warnings)


def _decode(value):
    """Decode strictly and bound decompressed output; never discard bad bytes."""
    if value is None:
        return '', 'ok'
    if isinstance(value, str):
        return (value, 'ok') if len(value.encode('utf-8')) <= MAX_TEXT else ('', 'content_too_large')
    if not isinstance(value, (bytes, bytearray, memoryview)):
        return '', 'invalid_content_type'
    raw = bytes(value)
    if len(raw) > MAX_TEXT:
        return '', 'content_too_large'
    if raw.startswith(b'\x28\xb5\x2f\xfd'):
        try:
            import zstandard
        except ImportError:
            return '', 'missing_zstandard'
        try:
            size = zstandard.frame_content_size(raw)
            if size != zstandard.CONTENTSIZE_UNKNOWN and size > MAX_TEXT:
                return '', 'content_too_large'
            # Unlike stream_reader.read(), decompress rejects incomplete frames.
            # Inspect declared size first: max_output_size only bounds unknown sizes.
            raw = zstandard.ZstdDecompressor(max_window_size=(MAX_TEXT + 1023) // 1024).decompress(
                raw, max_output_size=MAX_TEXT, allow_extra_data=False)
            if len(raw) > MAX_TEXT:
                return '', 'content_too_large'
        except Exception:
            return '', 'zstd_decode_error'
    try:
        return raw.decode('utf-8'), 'ok'
    except UnicodeDecodeError:
        return '', 'utf8_decode_error'


def _protobuf_fields(raw):
    if not isinstance(raw, bytes) or len(raw) > 1024 * 1024:
        raise ValueError('unsupported membership data')
    offset = 0
    fields = {}
    def varint():
        nonlocal offset
        result = 0
        for shift in range(0, 70, 7):
            if offset >= len(raw):
                raise ValueError('truncated membership data')
            value = raw[offset]; offset += 1
            result |= (value & 127) << shift
            if not value & 128:
                return result
        raise ValueError('invalid membership integer')
    while offset < len(raw):
        tag = varint(); field, wire = tag >> 3, tag & 7
        if not field:
            raise ValueError('invalid membership field')
        if wire == 0:
            value = varint()
        elif wire in (1, 2, 5):
            size = varint() if wire == 2 else (8 if wire == 1 else 4)
            if offset + size > len(raw):
                raise ValueError('truncated membership field')
            value = raw[offset:offset + size]; offset += size
        else:
            raise ValueError('unsupported membership field')
        fields.setdefault(field, []).append(value)
    return fields


class DatabaseAdapter:
    mode = 'database'
    read_only = True

    def __init__(self, root=None, self_id='', source_id='', source_info=None):
        self.lock = threading.RLock()
        self._state = None
        if root is not None:
            self.configure(root, self_id, source_id, source_info)

    @contextmanager
    def _connect(self, path):
        connection = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
        try:
            connection.row_factory = sqlite3.Row
            connection.text_factory = bytes
            connection.execute('PRAGMA query_only=ON')
            connection.execute('PRAGMA trusted_schema=OFF')
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _tables(connection):
        return {_text(row[0]).lower(): _text(row[0]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}

    @staticmethod
    def _columns(connection, table):
        return {_text(row[1]).lower(): _text(row[1]) for row in connection.execute('PRAGMA table_info(' + _quote(table) + ')')}

    @staticmethod
    def _check_file(path, root):
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise DatabaseError('source_outside_root', '数据库文件超出选定副本目录。')
        for suffix in ('-wal', '-journal'):
            sidecar = Path(str(path) + suffix)
            if sidecar.exists() and sidecar.stat().st_size:
                raise DatabaseError('unmerged_journal', '副本含未合并的 WAL 或回滚日志，请重新准备一致的明文副本。')
        with resolved.open('rb') as stream:
            if stream.read(16) != SQLITE_HEADER:
                raise DatabaseError('encrypted_or_invalid_database', '需要解密后的 SQLite 明文副本；此入口不读取密钥或解密数据库。')
        return resolved

    def configure(self, root, self_id='', source_id='', source_info=None):
        """Validate an entire new source under the lock; failures retain the old one."""
        with self.lock:
            try:
                directory = Path(root).expanduser().resolve(strict=True)
                if not directory.is_dir():
                    raise DatabaseError('invalid_root', '请选择单个账号的数据库副本目录。')
                self_id = _name(self_id)
                if not isinstance(source_id, str) or len(source_id) > 160 or any(ord(c) < 32 for c in source_id):
                    raise DatabaseError('invalid_source_id', '数据库来源标识无效。')
                if source_info is not None and not isinstance(source_info, dict):
                    raise DatabaseError('invalid_source_info', '数据库来源信息无效。')
                files = []
                metadata = {}
                for name in ('contact', 'session'):
                    choices = [path for path in (directory / (name + '.db'), directory / name / (name + '.db')) if path.exists()]
                    if len(choices) > 1:
                        raise DatabaseError('ambiguous_metadata', '副本目录含重复的联系人或会话库，请选择唯一账号目录。')
                    if choices:
                        metadata[name] = self._check_file(choices[0], directory)
                        files.append(metadata[name])
                shards = []
                for folder in (directory, directory / 'message', directory / 'biz_message'):
                    if not folder.is_dir():
                        continue
                    for path in sorted(folder.glob('*.db')):
                        if SHARD_NAME.fullmatch(path.name):
                            checked = self._check_file(path, directory)
                            if checked not in shards:
                                shards.append(checked)
                if not shards:
                    raise DatabaseError('no_message_database', '没有找到 message_数字.db 或 biz_message_数字.db；请选择单个账号的明文副本目录。')
                files += shards
                for relative in ('hardlink/hardlink.db', 'message/message_resource.db'):
                    index = directory / relative
                    if index.exists():
                        files.append(self._check_file(index, directory))
                fingerprints = {path: _fingerprint(path) for path in files}
                namespace = source_id or 'database:' + hashlib.sha256((str(directory).casefold() + '|' + self_id).encode()).hexdigest()[:24]
                state = {'root': directory, 'files': files, 'metadata': metadata, 'shards': shards,
                         'fingerprints': fingerprints, 'self_id': self_id, 'source_id': namespace,
                         'contacts': {}, 'rooms': {}, 'tables': {}, 'groups': [], 'warnings': []}
                self._load_metadata(state)
                self._ensure_unchanged(state)
                info = deepcopy(source_info or {})
                revision_data = [(str(path.relative_to(directory)), fingerprints[path]) for path in files]
                stamp = _now()
                info.update(id=namespace, revision=info.get('revision') or hashlib.sha256(json.dumps(revision_data).encode()).hexdigest(),
                            status='snapshot_ready', createdAt=info.get('createdAt') or stamp,
                            modifiedAt=info.get('modifiedAt') or datetime.fromtimestamp(max(p.stat().st_mtime for p in files), timezone.utc).isoformat(timespec='seconds'),
                            readAt=stamp, readOnly=True, selfId=self_id, identityVerified=False,
                            fileCount=len(files), shardCount=len(shards), groupCount=len(state['groups']),
                            warnings=state['warnings'])
                state['info'] = info
                self._state = state
                return deepcopy(info)
            except DatabaseError:
                raise
            except (OSError, sqlite3.Error, ValueError, TypeError, UnicodeError):
                raise DatabaseError('invalid_snapshot', '无法打开或解析数据库副本；请检查路径和 SQLite 文件结构。') from None

    def _load_metadata(self, state):
        candidates = set()
        deleted = set()
        recognized_message_schema = False
        contact = state['metadata'].get('contact')
        if contact:
            with self._connect(contact) as db:
                tables = self._tables(db)
                for kind in ('contact', 'stranger'):
                    if kind not in tables:
                        continue
                    table = tables[kind]; columns = self._columns(db, table)
                    if 'username' not in columns:
                        state['warnings'].append(_warning('contact_schema_unsupported', '联系人表缺少 username，名称可能无法显示。'))
                        continue
                    selected = [columns[key] for key in ('username', 'remark', 'nick_name', 'alias', 'delete_flag') if key in columns]
                    for row in self._metadata_rows(db, table, selected, state):
                        try:
                            item = {key.lower(): value for key, value in dict(row).items()}
                            username = _name(item['username'])
                            if not username:
                                continue
                            if item.get('delete_flag') and int(item['delete_flag']):
                                deleted.add(username)
                        except (ValueError, UnicodeError):
                            state['warnings'].append(_warning('contact_decode_error', '有联系人字段无法解码，已跳过该名称。'))
                            continue
                        label = username
                        try:
                            for key in ('remark', 'nick_name', 'alias'):
                                candidate = _text(item.get(key)).strip()
                                if candidate:
                                    label = candidate
                                    break
                        except UnicodeError:
                            state['warnings'].append(_warning('contact_label_decode_error', '有联系人显示名称无法解码，已改用账号标识显示。'))
                        state['contacts'].setdefault(username, label)
                        if GROUP_ID.fullmatch(username):
                            candidates.add(username)
                if 'chat_room' in tables:
                    table = tables['chat_room']; columns = self._columns(db, table)
                    if 'username' in columns:
                        selected = [columns[key] for key in ('username', 'owner', 'ext_buffer') if key in columns]
                        for row in self._metadata_rows(db, table, selected, state):
                            try:
                                item = {key.lower(): value for key, value in dict(row).items()}
                                username = _name(item['username'])
                                if GROUP_ID.fullmatch(username):
                                    state['rooms'][username] = item; candidates.add(username)
                            except (ValueError, UnicodeError):
                                state['warnings'].append(_warning('room_decode_error', '有群资料无法解码。'))
        session = state['metadata'].get('session')
        if session:
            with self._connect(session) as db:
                tables = self._tables(db)
                table = tables.get('sessiontable') or tables.get('session')
                if table and 'username' in self._columns(db, table):
                    column = self._columns(db, table)['username']
                    for row in self._metadata_rows(db, table, [column], state):
                        try:
                            username = _name(row[0])
                            if username:
                                candidates.add(username)
                        except (ValueError, UnicodeError):
                            state['warnings'].append(_warning('session_decode_error', '有会话标识无法解码。'))
        for path in state['shards']:
            with self._connect(path) as db:
                tables = self._tables(db); state['tables'][path] = tables
                if 'name2id' in tables:
                    table = tables['name2id']; columns = self._columns(db, table)
                    if 'user_name' in columns:
                        recognized_message_schema = True
                        for row in self._metadata_rows(db, table, [columns['user_name']], state):
                            try:
                                username = _name(row[0])
                                if username and self._message_table(tables, username):
                                    candidates.add(username)
                            except (ValueError, UnicodeError):
                                state['warnings'].append(_warning('sender_decode_error', '有发送者标识无法解码。'))
                if any(self._message_table(tables, candidate) for candidate in candidates):
                    recognized_message_schema = True
        if not recognized_message_schema:
            raise DatabaseError('message_schema_unsupported', '未找到微信 4.x 的 Name2Id 或会话消息表，请检查副本格式。')
        state['groups'] = sorted([{'id': key, 'name': state['contacts'].get(key, key),
                                  'conversationKind': 'group' if GROUP_ID.fullmatch(key) else 'direct'}
                                 for key in candidates - deleted], key=lambda group: (group['name'].casefold(), group['id']))

    @staticmethod
    def _metadata_rows(db, table, selected, state):
        rows = db.execute('SELECT ' + ','.join(_quote(key) for key in selected) + ' FROM ' + _quote(table) + ' LIMIT ?', (MAX_METADATA + 1,)).fetchall()
        if len(rows) > MAX_METADATA:
            state['warnings'].append(_warning('metadata_truncated', '元数据超过读取上限，列表可能不完整。'))
        return rows[:MAX_METADATA]

    @staticmethod
    def _message_table(tables, group):
        digest = hashlib.md5(group.encode('utf-8')).hexdigest()
        matches = [tables[prefix + digest] for prefix in ('msg_', 'chat_') if prefix + digest in tables]
        if len(matches) > 1:
            raise DatabaseError('ambiguous_message_table', '同一会话存在多个候选消息表，无法安全确定读取范围。')
        return matches[0] if matches else None

    def _ensure_unchanged(self, state):
        # A newly added shard is a new snapshot too, even if existing files did
        # not change. Do not keep showing the old subset under the old revision.
        try:
            directory = state['root']
            discovered = set()
            for name in ('contact', 'session'):
                for path in (directory / (name + '.db'), directory / name / (name + '.db')):
                    if path.exists():
                        discovered.add(path.resolve(strict=True))
            for folder in (directory, directory / 'message', directory / 'biz_message'):
                if folder.is_dir():
                    discovered.update(path.resolve(strict=True) for path in folder.glob('*.db') if SHARD_NAME.fullmatch(path.name))
            for relative in ('hardlink/hardlink.db', 'message/message_resource.db'):
                index = directory / relative
                if index.exists():
                    discovered.add(index.resolve(strict=True))
            if discovered != set(state['files']):
                raise DatabaseError('snapshot_changed', '数据库副本的文件列表已变化，请重新导入。')
        except OSError:
            raise DatabaseError('snapshot_unavailable', '数据库副本已移动或不可访问，请重新导入。') from None
        for path, previous in state['fingerprints'].items():
            try:
                current = self._check_file(path, state['root'])
                if current != path or _fingerprint(path) != previous:
                    raise DatabaseError('snapshot_changed', '数据库副本已变化，请重新导入；不会把不同版本的数据混合显示。')
            except OSError:
                raise DatabaseError('snapshot_unavailable', '数据库副本已移动或不可访问，请重新导入。') from None

    def get_source_info(self):
        with self.lock:
            if self._state is None:
                return {'id': '', 'revision': '', 'status': 'unconfigured', 'readOnly': True,
                        'createdAt': None, 'modifiedAt': None, 'readAt': None}
            result = deepcopy(self._state['info'])
            try:
                self._ensure_unchanged(self._state)
            except DatabaseError as exc:
                result.update(status='unavailable', issueCode=exc.code, error=str(exc))
            return result

    def auth(self):
        with self.lock:
            info = self.get_source_info()
            return {'status': info['status'], 'sourceId': info['id'], 'selfId': info.get('selfId', ''),
                    'readOnly': True, 'source': info, **({'error': info['error']} if info.get('error') else {})}

    def _members(self, state, group):
        warnings = []
        room = state['rooms'].get(group)
        if not GROUP_ID.fullmatch(group):
            return [{'id': group, 'name': state['contacts'].get(group, group),
                     'kind': '企业微信' if group.endswith('@openim') else '联系人'}], warnings
        raw = room.get('ext_buffer') if room else None
        if not isinstance(raw, bytes) or not raw:
            return [], [_warning('members_unavailable', '副本缺少已验证的群成员资料；不会用历史发言者补造成员。')]
        try:
            fields = _protobuf_fields(raw)
            members = []
            seen = set()
            for item in fields.get(1, []):
                nested = _protobuf_fields(item)
                username = _name(nested.get(1, [b''])[0])
                if not username or username in seen:
                    continue
                if not re.fullmatch(r'[A-Za-z0-9_.@-]+', username):
                    raise ValueError('unsupported member id')
                name = _text(nested.get(2, [b''])[0]) or state['contacts'].get(username, username)
                members.append({'id': username, 'name': name,
                                'kind': '企业微信' if username.endswith('@openim') else '个人微信'})
                seen.add(username)
            if not members:
                raise ValueError('no verified members')
            return members, warnings
        except (ValueError, TypeError, UnicodeError):
            return [], [_warning('members_schema_unsupported', '群成员字段结构尚未适配，成员列表保持未知。')]

    def _messages(self, state, group, include_raw=False, limit=200):
        if not isinstance(limit, int) or not 1 <= limit <= 2000:
            raise ValueError('消息读取数量无效。')
        rows = []
        warnings = deepcopy(state['warnings'])
        for path in state['shards']:
            file = path.relative_to(state['root']).as_posix()
            table = self._message_table(state['tables'][path], group)
            if table is None:
                continue
            with self._connect(path) as db:
                columns = self._columns(db, table)
                required = {'local_id', 'local_type', 'create_time', 'message_content'}
                if not required <= columns.keys():
                    warnings.append(_warning('message_schema_unsupported', '消息表缺少必要字段，此分片未读取。', file)); continue
                selected = [key for key in ('local_id', 'server_id', 'local_type', 'create_time', 'real_sender_id', 'message_content', 'compress_content', 'source', 'message_source', 'msgsource', 'msg_source', 'atuserlist', 'at_list') if key in columns]
                sender = "''"
                join = ''
                names = state['tables'][path].get('name2id')
                if names and 'real_sender_id' in columns:
                    name_columns = self._columns(db, names)
                    if 'user_name' in name_columns:
                        sender = 'n.' + _quote(name_columns['user_name'])
                        join = ' LEFT JOIN ' + _quote(names) + ' n ON m.' + _quote(columns['real_sender_id']) + '=n.rowid'
                sql = 'SELECT ' + ','.join('m.' + _quote(columns[key]) + ' AS ' + _quote(key) for key in selected)
                sql += ',' + sender + ' AS sender_username FROM ' + _quote(table) + ' m' + join
                sql += ' ORDER BY m.' + _quote(columns['create_time']) + ' DESC,m.' + _quote(columns['local_id']) + ' DESC LIMIT ' + str(limit)
                for row in db.execute(sql):
                    item = dict(row)
                    message = self._present_message(item, state, file, table, warnings, include_raw)
                    if message is not None:
                        rows.append(message)
        # Deduplicate only confirmed identical server messages; local IDs are per shard.
        unique = []
        seen = set()
        for row in sorted(rows, key=lambda value: value['id']):
            # Include decoded content and mention metadata: two image placeholders
            # may have different attachments even when their displayed text matches.
            key = (row['serverId'], row['senderId'], row['timestamp'], row['type'],
                   row.pop('_contentHash'), row['mentionSelf'], row['mentionEveryone'], row['mentionStatus'])
            if int(row['serverId']) > 0 and row['decodeStatus'] == 'ok':
                if key in seen:
                    continue
                seen.add(key)
            unique.append(row)
        revokes = {row.get('revokedServerId'): row['text'] for row in unique if row['kind'] == 'revoke' and row.get('revokedServerId')}
        for row in unique:
            if row['serverId'] in revokes and row['kind'] != 'revoke':
                for key in ('media', 'record', 'quote'):
                    row.pop(key, None)
                row.update(kind='revoke', text=revokes[row['serverId']], mentionSelf=False, mentionEveryone=False)
        return {'messages': sorted(unique, key=lambda row: (row['timestamp'], row['id']))[-limit:], 'warnings': warnings}

    def _present_message(self, item, state, file, table, warnings, include_raw=False):
        try:
            local_id = int(item['local_id']); timestamp = int(item['create_time']); kind = int(item['local_type'])
            if local_id < 0 or timestamp < 0 or timestamp > 253402300799:
                raise ValueError('invalid message coordinates')
            server_id = str(int(item.get('server_id') or 0))
            sender_id = _name(item.get('sender_username'))
        except (ValueError, TypeError, UnicodeError):
            warnings.append(_warning('message_metadata_invalid', '一条消息的编号、时间或发送者字段无效，已跳过。', file)); return None
        identity = state['source_id'] + '|' + file + '|' + table + '|' + str(local_id)
        value = item.get('compress_content')
        value = value if value not in (None, '', b'') else item['message_content']
        text, decode_status = _decode(value)
        if decode_status == 'ok':
            presentation = parse_content(text, kind)
        else:
            presentation = {'kind': 'unsupported', 'text': '压缩消息尚未解码，请安装 zstandard 后重新读取。' if decode_status == 'missing_zstandard' else '消息内容无法完整解码，未显示损坏文本。'}
            warnings.append(_warning(decode_status, presentation['text'], file, identity))
        mentioned, mention_status = self._mentions(item)
        if mention_status == 'invalid':
            warnings.append(_warning('mention_metadata_invalid', '此消息的结构化 @ 信息无法解析，未推断真实 @。', file, identity))
        known_self = bool(state['self_id'] and sender_id)
        result = {'id': identity, 'serverId': server_id, 'localId': local_id,
                     'dbName': file, 'senderId': sender_id, 'senderName': state['contacts'].get(sender_id, sender_id or '未知成员'),
                     'timestamp': timestamp, 'type': kind & 0xffffffff, **presentation,
                     'isSelf': known_self and sender_id == state['self_id'], 'isSelfKnown': known_self,
                     'mentionSelf': bool(state['self_id'] and state['self_id'] in mentioned),
                     'mentionEveryone': 'notify@all' in mentioned,
                     'mentionStatus': mention_status if state['self_id'] else 'self_unknown',
                     'decodeStatus': decode_status, 'source': 'database', 'sourceId': state['source_id'],
                     '_contentHash': hashlib.sha256((str(kind) + '\0' + text).encode('utf-8')).hexdigest()}
        if include_raw:
            result['_raw'] = text
        return result

    @staticmethod
    def _mentions(item):
        known = False
        invalid = False
        mentioned = set()
        for field in ('atuserlist', 'at_list', 'source', 'message_source', 'msgsource', 'msg_source'):
            if field not in item:
                continue
            if item[field] is None:
                continue
            text, status = _decode(item[field])
            if status != 'ok':
                invalid = True
                continue
            if not text.strip():
                known = True
                continue
            if field in ('atuserlist', 'at_list'):
                mentioned.update(key.strip() for key in text.split(',') if key.strip())
                known = True
                continue
            root = xml_root(text)
            if root is not None:
                node = root if root.tag == 'atuserlist' else root.find('.//atuserlist')
                if node is not None:
                    mentioned.update(key.strip() for key in (node.text or '').split(',') if key.strip())
                known = True
            else:
                invalid = True
        if invalid:
            return [], 'invalid'
        return sorted(mentioned), ('structured' if mentioned else 'none') if known else 'unknown'

    def attachment_message(self, account, group, message_id):
        """Internal-only raw XML; callers must not serialize it to the browser."""
        with self.lock:
            self.call('groups', account=account)
            state = self._state
            if group not in {row['id'] for row in state['groups']}:
                raise DatabaseError('invalid_conversation', '请选择当前副本中的有效会话。')
            rows = self._messages(state, group, include_raw=True)['messages']
            self._ensure_unchanged(state)
            row = next((row for row in rows if row['id'] == message_id), None)
            if row is None or row['kind'] == 'revoke':
                raise ValueError('消息不存在或已经撤回，请刷新会话。')
            return row, state['root'], state['info']['revision']

    def call(self, action, **params):
        if action in ('send', 'media', 'login', 'logout'):
            raise DatabaseError('read_only_source', '数据库副本模式只读，不支持发送、登录、退出或附件加载。')
        if action not in ('groups', 'members', 'group_info', 'messages', 'message_history', 'inbound_messages'):
            raise DatabaseError('unsupported_action', '数据库副本模式不支持此操作。')
        with self.lock:
            state = self._state
            if state is None:
                raise DatabaseError('unconfigured', '请先导入单个账号的明文数据库副本。')
            self._ensure_unchanged(state)
            if params.get('account') != state['source_id']:
                raise DatabaseError('source_changed', '数据库来源已变化，请刷新会话列表。')
            try:
                if action == 'groups':
                    result = {'account': state['source_id'], 'selfId': state['self_id'],
                              'name': state['contacts'].get(state['self_id'], '数据库副本'),
                              'groups': [dict(group) for group in state['groups']], 'warnings': deepcopy(state['warnings'])}
                else:
                    group = params.get('groupId')
                    if group not in {row['id'] for row in state['groups']}:
                        raise DatabaseError('invalid_conversation', '请选择当前副本中的有效会话。')
                    if action == 'inbound_messages':
                        from inbound_messages import scan_inbound
                        result = scan_inbound(self, state, group, params['start'], params['end'], params.get('cursor'))
                    elif action == 'message_history':
                        from message_history import query_history
                        result = query_history(self, state, group, params)
                    elif action == 'messages':
                        result = self._messages(state, group, limit=params.get('limit', 200))
                    else:
                        members, warnings = self._members(state, group)
                        result = {'members': members, 'membersAvailable': not warnings, 'warnings': warnings}
                        if action == 'group_info':
                            room = state['rooms'].get(group, {})
                            owner = _name(room.get('owner'))
                            result.update(id=group, name=state['contacts'].get(group, group),
                                          ownerId=owner, ownerName=state['contacts'].get(owner, owner or '未同步'),
                                          memberCount=len(members), conversationKind='group' if GROUP_ID.fullmatch(group) else 'direct')
                self._ensure_unchanged(state)
                state['info']['readAt'] = _now()
                return result
            except DatabaseError:
                raise
            except (sqlite3.Error, OSError, ValueError, TypeError, UnicodeError):
                raise DatabaseError('snapshot_read_failed', '数据库副本读取失败，未返回不完整或混合版本的数据。') from None

    def login(self):
        raise DatabaseError('read_only_source', '数据库副本没有登录操作。')

    def logout(self):
        raise DatabaseError('read_only_source', '数据库副本没有微信退出操作。')
