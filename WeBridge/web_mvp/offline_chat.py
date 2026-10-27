"""Offline archive API. Reads validated snapshots, never controls a client."""
import base64
from collections import Counter
from contextlib import ExitStack, closing, contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import csv
import hashlib
import heapq
import html
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time

from database_adapter import DatabaseAdapter, DatabaseError, _quote, _text
from database_snapshot import _hash_file, _inventory
from offline_content import body_text, interpret, mentions
from offline_content_status import forward_notice
from offline_media_search import LocalMediaSearch
from offline_quality import ContentQuality
from offline_images import image_references, message_anchor, quote_link_text
from offline_media_summary import MediaSummary, summary_text
from offline_reporting import completion_report, REPORT_FILE
from offline_image_html import CSS as IMAGE_CSS, image_card, gallery_body
from offline_markdown import header as markdown_header, message as markdown_message
from offline_documents import AttachmentExtractor, ExtractionCache, summary_text as document_summary_text
from offline_analysis import AnalysisWriter

BEIJING = timezone(timedelta(hours=8), name='Asia/Shanghai')
SCHEMA_VERSION = 1
MIN_TIMESTAMP, MAX_TIMESTAMP = -62135625600, 253402271999
QUOTE_NOTES = {'source_id_unavailable': '原记录未提供有效的引用来源编号',
               'embedded_partial': '引用内嵌原文仅部分适配，保留可读取内容',
               'unsupported_quote_schema': '引用字段布局暂不支持，原始字段已保留',
               'source_not_found': '本地副本未找到对应原消息',
               'ambiguous_source': '引用来源对应多条记录，暂不能确认',
               'source_sender_conflict': '引用来源的发送者信息不一致',
               'cyclic_source': '引用来源出现循环', 'source_decode_error': '原消息正文未成功解码'}


class ArchiveError(ValueError):
    """Safe diagnostics authored here, never copied from database payloads."""


class DirectoryPublishError(ArchiveError):
    """A completed stage could not be renamed within the Windows lock budget."""
    def __init__(self, error):
        super().__init__('结果文件已写入，但发布目录仍被占用或禁止重命名；请释放占用、确认目录权限后重试。')
        self.errno = error.errno
        self.winerror = error.winerror


def publish_directory(stage, destination):
    """Publish once unlocked, without overwriting a target or repeating parsing."""
    delays = (0, .1, .2, .4, .8, 1.5)
    for attempt, delay in enumerate(delays):
        if delay:
            time.sleep(delay)
        if destination.exists():
            raise ArchiveError('结果目录已存在，未覆盖；请选择新的导出位置。')
        try:
            stage.rename(destination)
            return
        except PermissionError as error:
            if os.name != 'nt' or getattr(error, 'winerror', None) not in (5, 32, 33):
                raise
            if attempt == len(delays) - 1:
                raise DirectoryPublishError(error) from error


class IntegrityScopeError(ArchiveError):
    """An operation finished its work but its final snapshot check failed."""


_INTEGRITY_SCOPES = ContextVar('archive_integrity_scopes', default=())


def json_value(value):
    if isinstance(value, bytes):
        return {'encoding': 'base64', 'data': base64.b64encode(value).decode('ascii')}
    return value


def source_integer(value):
    """Keep malformed SQLite values unknown instead of coercing them to zero."""
    if isinstance(value, bytes):
        try:
            value = value.decode('ascii')
        except UnicodeError:
            return None
    if isinstance(value, str):
        if not re.fullmatch(r'-?(?:0|[1-9][0-9]{0,18})', value):
            return None
        value = int(value)
    if isinstance(value, float):
        if not value.is_integer():
            return None
        value = int(value)
    return value if type(value) is int and -(2 ** 63) <= value < 2 ** 63 else None


def source_timestamp(value, milliseconds=False):
    stamp = source_integer(value)
    if stamp is not None and milliseconds and stamp > 20000000000:
        stamp //= 1000
    return stamp if stamp is not None and MIN_TIMESTAMP <= stamp <= MAX_TIMESTAMP else None


def integer_sql(column):
    """SQLite evaluates this expression; no per-row Python callback is needed."""
    cast = 'CAST(' + column + ' AS INTEGER)'
    return ('CASE WHEN typeof(' + column + ") IN ('integer','real') AND " + column + '=' + cast +
            " OR typeof(" + column + ") IN ('text','blob') AND CAST(" + cast + ' AS TEXT)=CAST(' +
            column + ' AS TEXT) THEN ' + cast + ' END')


def timestamp_sql(column, milliseconds=False):
    stamp = integer_sql(column)
    if milliseconds:
        stamp = '(CASE WHEN (' + stamp + ')>20000000000 THEN (' + stamp + ')/1000 ELSE (' + stamp + ') END)'
    return ('CASE WHEN (' + stamp + ') BETWEEN ' + str(MIN_TIMESTAMP) + ' AND ' +
            str(MAX_TIMESTAMP) + ' THEN (' + stamp + ') END')


def source_order(value):
    item, file, table = value
    stamp, local = item['__offline_timestamp'], item['__offline_local_id']
    return (stamp is None, stamp or 0, local is None, local or 0,
            file, table, item['__offline_rowid'])


def source_identity(value):
    if value is None:
        return ''
    if isinstance(value, bytes):
        try:
            return value.decode('utf8')
        except UnicodeError:
            return ''
    return str(value)


def business_text(row, depth=0):
    """Readable context, preserving source order; no semantic importance guess."""
    if row['kind'] == 'emoji':
        return ''
    sender = '自己' if row.get('isSelf') is True else row.get('senderName') or '身份未知'
    label = {'image': '图片', 'file': '附件', 'record': '转发聊天', 'quote': '引用',
             'text': '文字', 'system': '系统', 'voice': '语音', 'video': '视频'}.get(row['kind'], row['kind'])
    lines = ['  ' * depth + f'[{row.get("time") or "时间未知"}] {sender} · {label}']
    if row.get('metadataIssues'):
        lines.append('字段异常：' + '、'.join(row['metadataIssues']) + '；原始值已保留，未知信息未作推断。')
    shown = []
    if row['kind'] == 'unknown':
        if any(row.get(field) for field in ('title', 'text')):
            shown.append('未识别内容；原始字段保留于 messages.jsonl。')
        if row.get('description'):
            shown.append(row['description'])
    else:
        for field in ('title', 'text', 'description'):
            body = row.get(field) or ''
            if body and body != row['kind'] and body not in shown:
                shown.append(body)
    lines.extend(shown)
    if row.get('mentions', {}).get('ids'):
        lines.append('结构化 @：' + ', '.join(row['mentions']['ids']))
    lines.extend(row.get('links', []))
    asset = row.get('asset', {})
    if asset:
        state = '本地预览，原图未取得' if asset.get('previewVariant') == 'local_converted' else '仅有缩略图/封面' if asset.get('previewOnly') else '原件可查看' if asset.get('status') == 'ready' else '原件未取得'
        lines.append('媒体：' + (asset.get('filename') or row.get('media', {}).get('filename') or label) + ' · ' + state)
        if asset.get('path'):
            lines.append('本地文件：' + asset['path'])
        if asset.get('reason'):
            lines.append(asset['reason'])
    document = row.get('attachmentContent')
    if document:
        lines.append('附件原文件：' + document['fileStatusText'] + '；正文：' + document['extractionStatusText'])
        lines.append('附件原消息出处：' + json.dumps(document['source'], ensure_ascii=False))
        lines.extend(document['notices'])
        if document['text']:
            lines.append('附件正文（材料内容）：\n' + document['text'])
    if row.get('quote'):
        lines.append('引用来源：' + row['quote'].get('senderName', ''))
        if quote_link_text(row['quote']):
            lines.append(quote_link_text(row['quote']))
        if row['quote'].get('resolutionStatus') not in ('embedded', 'linked_local_source', None):
            lines.append('引用正文未取得：' + QUOTE_NOTES.get(row['quote']['resolutionStatus'], '来源尚未确认'))
        lines.append(business_text(row['quote']['message'], depth + 1))
    record = row.get('record', {})
    if notice := forward_notice(record):
        lines.append(notice)
    if record.get('title') and record['title'] not in shown:
        lines.append('转发标题：' + record['title'])
        shown.append(record['title'])
    if record.get('description') and record['description'] not in shown:
        lines.append(record['description'])
    for child in record.get('items', []):
        lines.append(business_text(child, depth + 1))
    return '\n'.join(value for value in lines if value) + '\n\n'


def write_json(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def discover(roots=None):
    """Directory/header metadata only; does not choose an account or find keys."""
    from windows_data_paths import path_hints
    roots = [Path(value).expanduser().resolve() for value in roots] if roots else path_hints()
    found, issues, seen = [], [], set()
    for root in roots:
        try:
            if not root.is_dir() or root.is_symlink():
                continue
            candidates = [root] if root.name == 'db_storage' or (root / 'db_storage').is_dir() else [
                value for value in root.iterdir() if value.is_dir() and not value.is_symlink()]
        except OSError:
            issues.append({'sourceRoot': str(root), 'status': 'unavailable_or_changing'})
            continue
        for account in sorted(candidates):
            source = account / 'db_storage' if (account / 'db_storage').is_dir() else account
            if source.name != 'db_storage' or source.is_symlink() or source.resolve() in seen:
                continue
            seen.add(source.resolve())
            try:
                inventory = _inventory(source)
                databases = []
                for relative, signature in inventory.items():
                    if relative.endswith('-wal'):
                        continue
                    with (source / relative).open('rb') as stream:
                        encrypted = stream.read(16) != b'SQLite format 3\0'
                    databases.append({'path': relative, 'bytes': signature[0], 'encrypted': encrypted})
                found.append({'directoryLabel': source.parent.name, 'sourceRoot': str(source),
                              'identityStatus': 'directory_label_only', 'databases': databases})
            except (OSError, ValueError):
                issues.append({'sourceRoot': str(source), 'status': 'unavailable_or_changing'})
    return {'accounts': found, 'issues': issues,
            'note': '目录名不是已验证账号。加密数据库需要该副本对应的本地密钥，不能仅凭文件解密。'}


def workbench_source(path):
    path = Path(path).resolve(strict=True)
    if path.stat().st_size > 1024 * 1024:
        raise ArchiveError('工作台配置超过支持大小。')
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    config = data['config']
    # Only reuse an already prepared source. No key extraction/refresh or HTTP.
    root = Path(data['snapshotRoot']).resolve(strict=True)
    expected = path.parent / 'database-snapshots'
    if root.parent != expected.resolve():
        raise ArchiveError('工作台副本不属于该配置的 database-snapshots 目录。')
    return root, dict(config)


def date_range(start=None, end=None):
    def stamp(value):
        try:
            parsed = datetime.strptime(value, '%Y-%m-%d').replace(tzinfo=BEIJING)
        except ValueError:
            raise ArchiveError('日期须为有效的 YYYY-MM-DD。') from None
        if parsed.strftime('%Y-%m-%d') != value:
            raise ArchiveError('日期须为 YYYY-MM-DD。')
        return parsed
    low = int(stamp(start).timestamp()) if start else None
    high = int((stamp(end) + timedelta(days=1)).timestamp()) if end else None
    if low is not None and high is not None and low >= high:
        raise ArchiveError('开始日期不能晚于结束日期。')
    return low, high


class LocalArchive:
    """Complete per-conversation streaming export over the workbench DB seam.

    No 200/2000-message UI limit. Duplicate source rows are retained with their
    shard/table/local ID provenance. Unknown rows keep original columns.
    """
    def __init__(self, snapshot, self_id='', media_config=None):
        source_info = None
        manifest = Path(snapshot) / 'manifest.json'
        try:
            if manifest.is_file() and not manifest.is_symlink() and manifest.stat().st_size <= 16 * 1024 * 1024:
                metadata = json.loads(manifest.read_text(encoding='utf8'))
                stamp = metadata.get('createdAt') if isinstance(metadata, dict) else None
                if isinstance(stamp, str) and datetime.fromisoformat(stamp).tzinfo is not None:
                    source_info = {'createdAt': stamp}
        except (OSError, ValueError):
            pass
        self.adapter = DatabaseAdapter(snapshot, self_id=self_id, source_info=source_info)
        self.state = self.adapter._state
        self.root = self.state['root']
        self.media_config = media_config or {}
        self.media_search = LocalMediaSearch(self.media_config)
        self.voice_files = sorted(set(self.root.glob('message/media_*.db')) | set(self.root.glob('media_*.db')))
        self.voice_fingerprints = {}
        for path in self.voice_files:
            self.adapter._check_file(path, self.root)
            self.voice_fingerprints[path] = _hash_file(path)

    def _integrity_deferred(self):
        return self in _INTEGRITY_SCOPES.get()

    @contextmanager
    def integrity_scope(self):
        """Check both operation boundaries; nested checks share only this context."""
        scopes = _INTEGRITY_SCOPES.get()
        if self in scopes:
            yield
            return
        self._check()
        token = _INTEGRITY_SCOPES.set(scopes + (self,))
        finished = False
        try:
            yield
            finished = True
        finally:
            _INTEGRITY_SCOPES.reset(token)
            try:
                self._check()
            except (ArchiveError, DatabaseError, OSError) as error:
                # Keep an interrupted operation's original error. Its completed
                # groups remain checkpointed, but it cannot report success.
                if finished:
                    message = (str(error) if isinstance(error, (ArchiveError, DatabaseError))
                               else '数据库副本不可访问。')
                    raise IntegrityScopeError(message + ' 请重新加载数据后继续；已验证的导出文件保留。') from None

    def _check(self):
        if self._integrity_deferred():
            return
        self.adapter._ensure_unchanged(self.state)
        current = sorted(set(self.root.glob('message/media_*.db')) | set(self.root.glob('media_*.db')))
        if current != self.voice_files or any(_hash_file(path) != value for path, value in self.voice_fingerprints.items()):
            raise ArchiveError('语音副本发生变化，请重新查询。')

    def groups(self, include_direct=False):
        self._check()
        rows = []
        for group in self.state['groups']:
            if include_direct or group['conversationKind'] == 'group':
                shards = [path.relative_to(self.root).as_posix() for path in self.state['shards']
                          if self.adapter._message_table(self.state['tables'][path], group['id'])]
                rows.append({**group, 'messageDatabases': shards, 'hasLocalMessages': bool(shards)})
        return {'schemaVersion': SCHEMA_VERSION, 'conversations': rows,
                'warnings': self.state['warnings'], 'coverage': self.coverage()}

    def coverage(self):
        orphans = []
        known = {group['id'] for group in self.state['groups']}
        for path in self.state['shards']:
            tables = self.state['tables'][path]
            resolved = {self.adapter._message_table(tables, group) for group in known}
            for table in tables.values():
                if re.fullmatch(r'(?:Msg|Chat)_[0-9a-fA-F]{32}', table, re.I) and table not in resolved:
                    orphans.append({'database': path.relative_to(self.root).as_posix(), 'table': table})
        return {'storageFamily': 'WeChat desktop 4.x', 'unmappedMessageTables': orphans,
                'voiceDatabases': len(self.voice_files), 'selfIdConfigured': bool(self.state['self_id']),
                'identityBasis': 'supplied_self_id' if self.state['self_id'] else 'unknown',
                'scope': 'local_snapshot_only', 'cloudHistoryFetched': False}

    def select_chat(self, chat_id=None, chat_name=None):
        matches = [group for group in self.state['groups'] if
                   (group['id'] == chat_id if chat_id else group['name'] == chat_name)]
        if not matches and not chat_id and chat_name:
            # Natural-language requests may append a category to the real name.
            # Exact names (including names ending in 群) always take priority.
            for suffix in ('群聊', '群'):
                if chat_name.endswith(suffix):
                    name = chat_name[:-len(suffix)]
                    matches = [group for group in self.state['groups'] if group['name'] == name]
                    break
        if len(matches) != 1:
            raise ArchiveError('会话不存在或名称重复，请先列群并使用唯一的 --chat ID。')
        return matches[0]

    def members(self, chat):
        rows, warnings = self.adapter._members(self.state, chat)
        return {'members': rows, 'warnings': warnings, 'historicalSpeakersAreMembers': False}

    def _streams(self, stack, chat, low, high, *, server_id=None):
        streams, schemas = [], []
        for path in self.state['shards']:
            table = self.adapter._message_table(self.state['tables'][path], chat)
            if not table:
                continue
            db = stack.enter_context(self.adapter._connect(path))
            columns = self.adapter._columns(db, table)
            required = {'local_id', 'local_type', 'create_time', 'message_content'}
            if not required <= columns.keys():
                raise ArchiveError('所选会话存在未支持的消息表字段，未发布部分导出。')
            file = path.relative_to(self.root).as_posix()
            schemas.append({'database': file, 'table': table, 'columns': list(columns.values())})
            raw_time = 'm.' + _quote(columns['create_time'])
            raw_local = 'm.' + _quote(columns['local_id'])
            layouts = self.__dict__.setdefault('_time_layouts', {})
            layout_key = (path, table)
            if layout_key not in layouts:
                # The immutable snapshot is checked before/after export. Probe
                # once so ordinary tables retain their time-index query plan.
                invalid = db.execute('SELECT 1 FROM ' + _quote(table) + ' m WHERE typeof(' + raw_time +
                    ")!='integer' OR " + raw_time + ' NOT BETWEEN ? AND ? OR typeof(' + raw_local +
                    ")!='integer' LIMIT 1", (MIN_TIMESTAMP, MAX_TIMESTAMP)).fetchone()
                layouts[layout_key] = invalid is None
            fast = layouts[layout_key]
            conditions, params = [], []
            if server_id is not None:
                if 'server_id' not in columns:
                    continue
                conditions.append('m.' + _quote(columns['server_id']) + '=?')
                params.append(server_id)
            date_conditions = []
            for operator, value in (('>=', low), ('<', high)):
                if value is not None:
                    date_conditions.append((raw_time if fast else '__offline_timestamp') + operator + '?')
                    params.append(value)
            if date_conditions:
                conditions.append(' AND '.join(date_conditions) if fast else
                                  '((' + ' AND '.join(date_conditions) + ') OR __offline_timestamp IS NULL)')
            names = self.state['tables'][path].get('name2id')
            sender, join = "''", ''
            if names and 'real_sender_id' in columns:
                names_columns = self.adapter._columns(db, names)
                if 'user_name' in names_columns:
                    sender = 'n.' + _quote(names_columns['user_name'])
                    join = ' LEFT JOIN ' + _quote(names) + ' n ON m.' + _quote(columns['real_sender_id']) + '=n.rowid'
            sql = ('SELECT m.*,m.rowid AS __offline_rowid,' + (raw_time if fast else timestamp_sql(raw_time)) +
                   ' AS __offline_timestamp,' + (raw_local if fast else integer_sql(raw_local)) + ' AS __offline_local_id,' +
                   sender + ' AS __offline_sender FROM ' + _quote(table) + ' m' + join)
            # Keep source column names intact inside the inner query. Only the
            # derived aliases participate in filtering/ordering.
            if not fast:
                if server_id is not None:
                    sql += ' WHERE ' + conditions.pop(0)
                sql = 'SELECT * FROM (' + sql + ')'
            if conditions:
                sql += ' WHERE ' + ' AND '.join(conditions)
            sql += (' ORDER BY ' + raw_time + ',' + raw_local + ',m.rowid' if fast else
                    ' ORDER BY __offline_timestamp IS NULL,__offline_timestamp,'
                    '__offline_local_id IS NULL,__offline_local_id,__offline_rowid')
            # A retained traceback can keep an unfinished cursor alive after
            # connection.close(); finalize it first to release Windows file locks.
            cursor = stack.enter_context(closing(db.execute(sql, params)))
            streams.append(self._stream(cursor, file, table))
        return streams, schemas

    @staticmethod
    def _stream(cursor, file, table):
        for row in cursor:
            item = {key.lower(): value for key, value in dict(row).items()}
            yield item, file, table

    def _message(self, item, file, table, chat):
        raw, decode_status, attempts = body_text(item)
        sender_value = item.get('__offline_sender')
        sender = source_identity(sender_value)
        issues = []
        if sender_value not in (None, '', b'') and not sender:
            issues.append('sender_id')
        prefix = raw.split(':\n', 1)[0] if ':\n' in raw else ''
        prefix = prefix if re.fullmatch(r'[A-Za-z0-9_.@-]{1,200}', prefix) else ''
        prefix = prefix if prefix and (prefix == sender or prefix in self.state['contacts']) and chat['conversationKind'] == 'group' else ''
        sender_basis = 'Name2Id' if sender else 'unknown'
        if not sender and prefix and chat['conversationKind'] == 'group':
            sender, sender_basis = prefix, 'content_prefix'
        own = bool(self.state['self_id']) and bool(sender)
        local_id = item.get('__offline_local_id', source_integer(item['local_id']))
        timestamp = item.get('__offline_timestamp', source_timestamp(item['create_time']))
        local_type = source_integer(item['local_type'])
        server = source_integer(item.get('server_id') or 0)
        for field, value in (('local_id', local_id), ('create_time', timestamp),
                             ('local_type', local_type), ('server_id', server)):
            if value is None:
                issues.append(field)
        presentation_raw = raw.split(':\n', 1)[1] if prefix else raw
        presentation = interpret(presentation_raw, local_type) if decode_status == 'ok' and local_type is not None else {
            'kind': 'unknown', 'text': '', 'parseStatus': decode_status, 'links': []}
        if local_type is None:
            presentation['text'] = raw
        ats = mentions({**item, 'local_type': local_type or 0}, raw)
        ats['self'] = self.state['self_id'] in ats['ids'] if self.state['self_id'] else None
        try:
            iso = datetime.fromtimestamp(timestamp, BEIJING).isoformat() if timestamp is not None else None
        except (ValueError, OSError, OverflowError):
            iso = None
            timestamp = None
            if 'create_time' not in issues:
                issues.append('create_time')
        provenance = {'database': file, 'table': table, 'localId': local_id,
                      'sourceRowId': item.get('__offline_rowid')}
        if 'sender_id' in issues:
            provenance['senderValue'] = json_value(sender_value)
        if issues:
            presentation.update(metadataIssues=issues, contentParseStatus=presentation['parseStatus'],
                                parseStatus='invalid_metadata')
        identifier = str(local_id) if local_id is not None else 'rowid:' + str(item['__offline_rowid'])
        row = {'schemaVersion': SCHEMA_VERSION, 'chatId': chat['id'], 'chatName': chat['name'],
               'id': file + '|' + table + '|' + identifier, 'localId': local_id,
               'serverId': str(server or 0), 'timestamp': timestamp, 'time': iso,
               'localType': local_type, 'senderId': sender,
               'senderName': self.state['contacts'].get(sender, sender), 'senderIdentityBasis': sender_basis,
               'senderConflict': bool(prefix and sender and prefix != sender),
               'isSelf': sender == self.state['self_id'] if own else None,
               'direction': ('outgoing' if sender == self.state['self_id'] else 'incoming') if own else 'unknown',
               **presentation, 'mentions': ats, 'decodeStatus': decode_status, 'decodeAttempts': attempts,
               'rawContent': raw, 'rawColumns': {key: json_value(value) for key, value in item.items()
                                               if not key.startswith('__offline_')},
               'provenance': provenance}
        return row

    def _link_quote(self, row, destination, copy_media, visited=None, depth=0):
        quote = row.get('quote')
        if not quote:
            return
        embedded = quote['message']
        if embedded.get('kind') != 'unknown' or embedded.get('text', '').strip():
            quote['resolutionStatus'] = 'embedded'
            return
        visited = set(visited or ()) | {row['id']}
        key = quote.get('serverId', '')
        if not re.fullmatch(r'-?\d{1,20}', key) or int(key) == 0 or depth >= 10:
            quote['resolutionStatus'] = 'source_id_unavailable'
            return
        identifier = int(key)
        if not -(2 ** 63) <= identifier < 2 ** 63:
            quote['resolutionStatus'] = 'source_id_unavailable'
            return
        matches = []
        with ExitStack() as stack:
            streams, _ = self._streams(stack, row['chatId'], None, None, server_id=identifier)
            for stream in streams:
                for item, file, table in stream:
                    matches.append((item, file, table))
                    if len(matches) > 1:
                        break
                if len(matches) > 1:
                    break
        if len(matches) != 1:
            quote['resolutionStatus'] = 'ambiguous_source' if matches else 'source_not_found'
            return
        item, file, table = matches[0]
        source = self._message(item, file, table, self.select_chat(chat_id=row['chatId']))
        if source['id'] in visited:
            quote['resolutionStatus'] = 'cyclic_source'
            return
        if quote.get('senderId') not in ('', None, row['chatId'], source['senderId']):
            quote['resolutionStatus'] = 'source_sender_conflict'
            return
        if source['decodeStatus'] != 'ok' or source.get('metadataIssues'):
            quote['resolutionStatus'] = 'source_decode_error'
            return
        if copy_media:
            previous_recovered = self.media_search.recovered
            try:
                self._media(source, destination, Counter())
            finally:
                # Quote references do not add duplicate media to root-row counts.
                self.media_search.recovered = previous_recovered
        self._link_quote(source, destination, copy_media, visited, depth + 1)
        fields = ('kind', 'text', 'description', 'baseType', 'subType', 'appType', 'parseStatus',
                  'senderId', 'senderName', 'time', 'timestamp', 'isSelf', 'links', 'mentions',
                  'media', 'asset', 'record', 'quote')
        quote.update(resolutionStatus='linked_local_source', sourceMessageId=source['id'],
                     sourceLocalId=source['localId'], embeddedMessage=embedded,
                     message={key: source[key] for key in fields if key in source})

    def _voice(self, row, destination):
        matches = []
        for path in self.voice_files:
            with self.adapter._connect(path) as db:
                tables = self.adapter._tables(db)
                voice, names = tables.get('voiceinfo'), tables.get('name2id') or tables.get('chatname2id')
                if not voice or not names:
                    continue
                required = {'chat_name_id', 'local_id', 'svr_id', 'voice_data'}
                if not required <= self.adapter._columns(db, voice).keys():
                    continue
                sql = 'SELECT voice_data FROM ' + _quote(voice) + ' WHERE chat_name_id=(SELECT rowid FROM ' + _quote(names)
                sql += ' WHERE user_name=?) AND local_id=? AND svr_id=? LIMIT 2'
                matches.extend(bytes(value[0]) for value in db.execute(sql, (row['chatId'], row['localId'], int(row['serverId']))) if value[0])
        if len(matches) != 1:
            return {'status': 'unavailable', 'reason': '语音副本缺失、结构未适配或映射不唯一。'}
        data = matches[0]
        if len(data) > 100 * 1024 * 1024:
            return {'status': 'unavailable', 'reason': '语音超过 100 MB 上限。'}
        extension = 'silk' if data.startswith((b'#!SILK', b'\x02#!SILK')) else 'amr' if data.startswith(b'#!AMR') else 'bin'
        digest = hashlib.sha256(data).hexdigest()
        relative = 'assets/' + digest + '.' + extension
        path = destination / relative
        if not path.exists():
            path.write_bytes(data)
        return {'status': 'ready', 'path': relative, 'bytes': len(data), 'sha256': digest,
                'playable': False, 'reason': '保留原始语音编码；未做转码或语音识别。'}

    def _media(self, row, destination, media_counts):
        from windows_media import resolve_local
        (destination / 'assets').mkdir(exist_ok=True)
        def attach(target, part=''):
            try:
                if target['kind'] == 'voice' and not part:
                    target['asset'] = self._voice(row, destination)
                else:
                    resolver_row = {**row, '_raw': row['rawContent'], 'media': target.get('media', {})}
                    result, data = resolve_local(self.root, self.media_config, row['chatId'], resolver_row, part,
                                                 recovery=self.media_search if target['kind'] != 'emoji' else None,
                                                 destination=destination)
                    if data is not None:
                        # Do not expose untrusted attachment names as paths or HTML.
                        suffix = Path(result['filename']).suffix.lower()
                        if not re.fullmatch(r'\.[a-z0-9]{1,12}', suffix):
                            suffix = '.bin'
                        relative = 'assets/' + result['digest'] + suffix
                        path = destination / relative
                        if not path.exists():
                            path.write_bytes(data)
                        result['path'] = relative
                    target['asset'] = result
                    if target['kind'] == 'emoji' and result['status'] != 'ready':
                        result['localCheck'] = {'checked': False, 'result': 'optional_emoji_not_required'}
            except (ValueError, OSError, sqlite3.Error, ImportError, KeyError, TypeError):
                target['asset'] = {'status': 'unavailable', 'reason': '附件索引、密钥或本机缓存缺失，或格式暂未支持。'}
            key = 'preview_only' if target['asset'].get('previewOnly') else target['asset']['status']
            media_counts[key] += 1
        def records(record):
            for child in record.get('items', []):
                if child['kind'] in ('image', 'video', 'file'):
                    attach(child, child['part'])
                elif child['kind'] == 'voice':
                    child['asset'] = {'status': 'unavailable', 'reason': '转发记录中的语音原件尚未适配。'}
                    media_counts['unavailable'] += 1
                if child.get('record'):
                    records(child['record'])
        if row['kind'] in ('image', 'video', 'emoji', 'file', 'voice'):
            attach(row)
        if row.get('record'):
            records(row['record'])

    def export(self, destination, *, chat_id=None, chat_name=None, start=None, end=None, limit=None, copy_media=False,
               progress=None):
        self._check()
        if copy_media and hasattr(self.media_search, 'begin_export'):
            self.media_search.begin_export()
        supplemental = None
        if copy_media and self.media_config.get('supplementRoot'):
            from offline_supplements import SupplementalMedia
            supplemental = getattr(self, '_supplemental_media', None)
            if supplemental is None or str(supplemental.root) != str(Path(self.media_config['supplementRoot']).absolute()):
                supplemental = self._supplemental_media = SupplementalMedia(self.media_config['supplementRoot'])
            supplemental.begin_export()
            supplemental.progress = (lambda value: progress({'stage': 'supplement_search', **value})) if progress else None
        recovered_before = self.media_search.recovered
        chat = self.select_chat(chat_id, chat_name)
        low, high = date_range(start, end)
        if limit is not None and (not isinstance(limit, int) or limit <= 0):
            raise ArchiveError('limit 须为正整数；不填写则导出完整时间范围。')
        destination = Path(destination).expanduser().absolute()
        resolved = destination.resolve()
        roots = [self.root]
        if self.media_config.get('sourceRoot'):
            source = Path(self.media_config['sourceRoot']).resolve()
            roots.append(source.parent if source.name == 'db_storage' else source)
        if any(resolved == root or resolved.is_relative_to(root) for root in roots):
            raise ArchiveError('导出目录不能位于源副本或微信账号目录中。')
        if destination.exists():
            raise ArchiveError('导出目录已存在，请选择新目录；不会覆盖现有交付物。')
        destination.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='.offline-export-', dir=destination.parent))
        counts, decode_counts, parse_counts, media_counts = Counter(), Counter(), Counter(), Counter()
        mention_counts, direction_counts, record_counts = Counter(), Counter(), Counter()
        business_media = Counter()
        quote_counts = Counter()
        business_count = 0
        images = []
        media_summary = MediaSummary()
        if not hasattr(self, '_document_cache'):
            self._document_cache = ExtractionCache()
        documents = AttachmentExtractor(self.media_config, progress, shared_cache=self._document_cache)
        def count_records(record):
            record_counts[record['status']] += 1
            for child in record.get('items', []):
                if child.get('record'):
                    count_records(child['record'])
        count, truncated = 0, False
        warnings = list(self.state['warnings'])
        invalid_fields = Counter()
        invalid_rows = unknown_time_rows = 0
        try:
            last_progress = time.monotonic()
            if progress:
                progress({'stage': 'parsing', 'processed': 0, 'chatId': chat['id'], 'chatName': chat['name']})
            with ExitStack() as stack:
                quality = stack.enter_context(ContentQuality(stage))
                if hasattr(self.media_search, 'progress'):
                    self.media_search.progress = (lambda value: progress({'stage': 'media_search', 'processed': count,
                        'chatId': chat['id'], 'chatName': chat['name'], **value})) if progress else None
                streams, schemas = self._streams(stack, chat['id'], low, high)
                data = stack.enter_context((stage / 'messages.jsonl').open('x', encoding='utf-8'))
                json_data = stack.enter_context((stage / 'messages.json').open('x', encoding='utf-8'))
                json_data.write('[\n')
                markdown = stack.enter_context((stage / 'messages.md').open('x', encoding='utf-8'))
                markdown.write(markdown_header(chat, start, end))
                reading = stack.enter_context((stage / '业务阅读.txt').open('x', encoding='utf-8-sig'))
                reading.write(chat['name'] + ' · 业务阅读\n\n保留非表情记录的完整时间上下文，包括系统提示、引用和嵌套转发；未按关键词删选业务重要性。\n'
                              '图片和附件可在桌面窗口直接查看；缺失原件以明确提示保留，不能据此断定内容与业务无关。\n\n')
                gaps = stack.enter_context((stage / 'media-recovery.jsonl').open('x', encoding='utf-8'))
                def write_gaps(node, parent, trail=()):
                    asset = node.get('asset', {})
                    if asset and node['kind'] != 'emoji':
                        business_media['preview_only' if asset.get('previewOnly') else asset['status']] += 1
                    if asset.get('status') in ('pending', 'unavailable') or asset.get('previewOnly'):
                        gaps.write(json.dumps({'messageId': parent['id'], 'localId': parent['localId'],
                            'time': parent['time'], 'part': node.get('part', ''), 'kind': node['kind'],
                            'status': 'preview_only' if asset.get('previewOnly') else asset['status'],
                            'reason': asset.get('reason', ''), 'reasonCode': asset.get('reasonCode', ''),
                            'filename': node.get('media', {}).get('filename', ''), 'md5': node.get('media', {}).get('md5', ''),
                            'context': list(trail), 'provenance': parent.get('provenance', {}),
                            'localCheck': asset.get('localCheck')}, ensure_ascii=False) + '\n')
                    for index, child in enumerate(node.get('record', {}).get('items', []), 1):
                        write_gaps(child, parent, (*trail, '转发第 ' + str(index) + ' 条'))
                    if node.get('quote', {}).get('message'):
                        write_gaps(node['quote']['message'], parent, (*trail, '引用来源'))
                csv_stream = stack.enter_context((stage / 'messages.csv').open('x', encoding='utf-8-sig', newline=''))
                csv_writer = csv.writer(csv_stream)
                csv_writer.writerow(['time', 'chat_id', 'sender_id', 'sender_name', 'direction', 'kind', 'text', 'mention_ids', 'server_id', 'source_id'])
                page = stack.enter_context((stage / 'chat.html').open('x', encoding='utf-8'))
                page.write(_html_start(chat['name']))
                analysis = stack.enter_context(AnalysisWriter(stage, chat, self.members(chat['id'])['members'],
                                                              self.state['contacts']))
                for item, file, table in heapq.merge(*streams, key=source_order):
                    if limit is not None and count >= limit:
                        truncated = True
                        break
                    row = self._message(item, file, table, chat)
                    if row.get('metadataIssues'):
                        invalid_rows += 1
                        invalid_fields.update(row['metadataIssues'])
                    if row.get('timestamp') is None:
                        unknown_time_rows += 1
                    tracked_media = []
                    if copy_media:
                        self._media(row, stage, media_counts)
                        if supplemental:
                            from offline_images import message_nodes
                            tracked_media = [(node, 'preview_only' if node['asset'].get('previewOnly') else node['asset']['status'])
                                for node, _, _ in message_nodes(row) if node.get('asset')]
                    self._link_quote(row, stage, copy_media)
                    if supplemental:
                        supplemental.attach(row, stage)
                        for node, previous in tracked_media:
                            asset = node['asset']
                            current = 'preview_only' if asset.get('previewOnly') else asset['status']
                            if current != previous:
                                media_counts[previous] -= 1
                                if not media_counts[previous]: del media_counts[previous]
                                media_counts[current] += 1
                    documents.attach(row, stage, copy_media)
                    if copy_media:
                        write_gaps(row, row)
                    quality.add(row)
                    if row.get('quote'):
                        quote_counts[row['quote']['resolutionStatus']] += 1
                    if row['kind'] != 'emoji':
                        business_count += 1
                        reading.write(business_text(row))
                    analysis.add(row)
                    serialized = json.dumps(row, ensure_ascii=False)
                    data.write(serialized + '\n')
                    json_data.write((',\n' if count else '') + serialized)
                    markdown.write(markdown_message(row, count + 1))
                    csv_writer.writerow([_csv_safe(value) for value in (
                        row['time'], row['chatId'], row['senderId'], row['senderName'], row['direction'],
                        row['kind'], row['text'], ','.join(row['mentions']['ids']), row['serverId'], row['id'])])
                    page.write(_message_html(row))
                    images.extend(image_references(row))
                    media_summary.add(row)
                    count += 1
                    counts[row['kind']] += 1
                    decode_counts[row['decodeStatus']] += 1
                    parse_counts[row['parseStatus']] += 1
                    mention_counts[row['mentions']['status']] += 1
                    direction_counts[row['direction']] += 1
                    if row.get('record'):
                        count_records(row['record'])
                    if progress:
                        now = time.monotonic()
                        if now - last_progress >= 0.25:
                            progress({'stage': 'parsing', 'processed': count,
                                      'chatId': chat['id'], 'chatName': chat['name']})
                            last_progress = now
                if progress:
                    progress({'stage': 'finalizing', 'processed': count,
                              'chatId': chat['id'], 'chatName': chat['name']})
                json_data.write('\n]\n')
                markdown.write('共导出 ' + str(count) + ' 条源消息。' + ('已达到条数限制，结果不完整。' if truncated else '当前筛选范围已导出完毕。') + '\n')
                page.write('</main><footer>本地副本存档。完整原始字段在 messages.jsonl；CSV 和此页为便于阅读的视图。</footer></body></html>')
                analysis_summary = analysis.finish()
                quality_summary = quality.finish()
            (stage / 'images.html').write_text(_html_start(chat['name'], gallery=True)
                + gallery_body(images) + '</main></body></html>', encoding='utf-8')
            self._check()
            write_json(stage / 'members.json', self.members(chat['id']))
            if unknown_time_rows:
                warnings.append('保留了 ' + str(unknown_time_rows) + ' 条时间未知的消息，排列在已知时间消息之后；' +
                    ('无法确认是否位于所选日期范围，请人工复核。' if start or end else '原始时间值已保留，请人工复核。'))
            report = {'schemaVersion': SCHEMA_VERSION, 'chat': chat, 'exportedRows': count,
                      'createdAt': datetime.now(BEIJING).isoformat(timespec='seconds'),
                      'startDate': start, 'endDate': end, 'timezone': 'Asia/Shanghai',
                      'truncatedByLimit': truncated, 'kinds': dict(counts), 'decodeStatuses': dict(decode_counts),
                      'parseStatuses': dict(parse_counts), 'mediaStatuses': dict(media_counts),
                      'mediaSummary': media_summary.result(), 'mediaSummaryText': summary_text(media_summary.result()),
                      'attachmentTextSummary': documents.summary(), 'attachmentTextSummaryText': document_summary_text(documents.summary()),
                      'localMediaSearch': self.media_search.report(recovered_before),
                      'supplementalMediaSearch': supplemental.report() if supplemental else {'enabled': False},
                      'mediaRecoveryFile': 'media-recovery.jsonl',
                      'contentQuality': quality_summary,
                      'businessReadingFile': '业务阅读.txt', 'businessRows': business_count,
                      'analysis': analysis_summary,
                      'messageExports': {'jsonl': 'messages.jsonl', 'json': 'messages.json', 'md': 'messages.md'},
                      'imageGalleryFile': 'images.html', 'imageReferences': len(images),
                      'businessMediaStatuses': dict(business_media),
                      'quoteSourceStatuses': dict(quote_counts),
                      'mentionStatuses': dict(mention_counts), 'directions': dict(direction_counts),
                      'recordStatuses': dict(record_counts),
                      'copyMediaRequested': copy_media, 'schemas': schemas, 'warnings': warnings,
                      'recordIssues': {'invalidMetadataRows': invalid_rows,
                                       'invalidFields': dict(invalid_fields),
                                       'unknownTimeRows': unknown_time_rows,
                                       'unknownTimeIncludedOutsideDateFilter': unknown_time_rows if start or end else 0},
                      'coverage': self.coverage(), 'sourceRoot': str(self.root),
                      'limitations': ['仅限本机已保存的消息与已缓存附件；不补拉云端、撤回或已删除原件。',
                          '原始字段保留于 JSONL，未识别类型、解码错误与身份未知不会当成完整解析。',
                          '链接只提取不访问；图片不做 OCR，语音不转文字，视频不做内容识别。',
                          '同一消息在不同分片的记录全部保留，使用 provenance 区分，不按正文猜测去重。']}
            report['reporting'] = completion_report(destination, report)
            (stage / REPORT_FILE).write_text(report['reporting']['replyMarkdown'], encoding='utf-8')
            artifacts = []
            for path in sorted(stage.rglob('*')):
                if path.is_file():
                    artifacts.append({'path': path.relative_to(stage).as_posix(), 'bytes': path.stat().st_size, 'sha256': _hash_file(path)})
            report['artifacts'] = artifacts
            write_json(stage / 'manifest.json', report)
            # Directory rename publishes only a finished batch; never overwrites.
            if destination.exists():
                raise ArchiveError('导出目标在处理期间已创建，未覆盖。')
            publish_directory(stage, destination)
            return report
        finally:
            if supplemental: supplemental.progress = None
            if hasattr(self.media_search, 'progress'):
                self.media_search.progress = None
            if stage.exists():
                # stage is always the exact mkdtemp-created child of this parent.
                if stage.resolve().parent == destination.parent.resolve() and stage.name.startswith('.offline-export-'):
                    shutil.rmtree(stage)


def _csv_safe(value):
    text = '' if value is None else str(value)
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else text


def _html_start(title, gallery=False):
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; media-src 'self'; base-uri 'none'; form-action 'none'">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>''' + html.escape(title) + '''</title>
<style>body{margin:0;background:#f4f6f8;color:#243243;font:15px/1.65 system-ui,sans-serif}header,main,footer{max-width:960px;margin:auto;padding:24px}header{border-bottom:1px solid #dce2e8}h1{margin:0;font-size:25px}article{margin:16px 0;padding:18px;background:white;border-radius:12px;border:1px solid #e0e5eb}article.outgoing{border-left:5px solid #159b83}article.unknown{border-left:5px solid #8793a0}.meta{font-size:12px;color:#657383;overflow-wrap:anywhere}.text{white-space:pre-wrap;overflow-wrap:anywhere}details{margin:8px 0;padding:10px;background:#f6f8fa}img,video{max-width:100%;max-height:420px}a{color:#087c9a}footer{font-size:12px;color:#657383}</style>
<style>''' + IMAGE_CSS + '''</style>
<body><header><h1>''' + html.escape(title) + (' · 图片总览' if gallery else '') + '''</h1><p>本地聊天记录 · 北京时间 · 发送者与结构化 @ 以数据库字段为依据</p>
<a class="gallery-link" href="images.html">图片总览（含转发、引用）</a><a class="gallery-link" href="chat.html">聊天记录与上下文</a></header><main>'''


def _message_html(row, nested=False):
    escape = lambda value: html.escape(str(value or ''), quote=True)
    anchor = '' if nested else ' id="' + message_anchor(row.get('id', '')) + '"'
    content = '<article' + anchor + ' class="' + escape(row.get('direction', 'unknown')) + '"><div class="meta">'
    content += escape(row.get('senderName')) + ' · ' + escape(row.get('senderId')) + ' · ' + escape(row.get('time')) + ' · ' + escape(row['kind']) + '</div>'
    displayed = []
    for field in ('title', 'text', 'description'):
        value = row.get(field) or ''
        if value and value not in displayed:
            displayed.append(value)
    content += '<div class="text">' + escape('\n'.join(displayed)) + '</div>'
    for url in row.get('links', []):
        if re.match(r'^https?://[^\s\x00-\x1f]+$', url, re.I):
            content += '<div><a target="_blank" rel="noopener noreferrer" href="' + escape(url) + '">' + escape(url) + '</a></div>'
    if row.get('mentions', {}).get('ids'):
        content += '<div class="meta">结构化 @：' + escape(', '.join(row['mentions']['ids'])) + '</div>'
    asset = row.get('asset')
    if row['kind'] == 'image':
        content += image_card(asset or {})
    elif asset:
        if asset.get('path'):
            path = escape(asset['path'])
            mime = asset.get('mime', '')
            if mime.startswith('image/'):
                content += '<img loading="lazy" src="' + path + '" alt="本机图片缓存">'
            elif mime == 'video/mp4':
                content += '<video controls preload="none" src="' + path + '"></video>'
            content += '<div><a download href="' + path + '">保存附件</a></div>'
        content += '<div class="meta">附件：' + escape('仅预览' if asset.get('previewOnly') else asset['status']) + ' ' + escape(asset.get('reason')) + '</div>'
    document = row.get('attachmentContent')
    if document:
        content += '<div class="meta">原文件：' + escape(document['fileStatusText']) + '；正文：' + escape(document['extractionStatusText']) + '</div>'
        for notice in document['notices']:
            content += '<div class="meta">' + escape(notice) + '</div>'
        if document['text']:
            content += '<details><summary>展开附件正文</summary><pre>' + escape(document['text']) + '</pre></details>'
    if row.get('record'):
        content += '<details><summary>转发的聊天记录</summary>'
        if notice := forward_notice(row['record']):
            content += '<div class="meta">' + escape(notice) + '</div>'
        if row['record'].get('title') and row['record']['title'] not in displayed:
            content += '<div class="text">转发标题：' + escape(row['record']['title']) + '</div>'
        if row['record'].get('description') and row['record']['description'] not in displayed:
            content += '<div class="text">' + escape(row['record']['description']) + '</div>'
        content += ''.join(_message_html(child, nested=True) for child in row['record'].get('items', []))
        content += '<div class="meta">解析状态：' + escape(row['record']['status']) + '</div></details>'
    if row.get('quote'):
        content += '<details><summary>引用 · ' + escape(row['quote']['senderName']) + '</summary>' + _message_html(row['quote']['message'], nested=True) + '</details>'
    if row.get('decodeStatus', 'ok') != 'ok' or row.get('parseStatus', 'ok') not in ('ok', 'plain_system'):
        content += '<div class="meta">解析状态：' + escape(row.get('decodeStatus')) + ' / ' + escape(row.get('parseStatus')) + '</div>'
    return content + '</article>'
