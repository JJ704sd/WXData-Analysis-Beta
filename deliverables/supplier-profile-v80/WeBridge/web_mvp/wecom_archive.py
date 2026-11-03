"""WeCom data adapter sharing the existing desktop view and export pipeline."""
from contextlib import closing
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3

from database_adapter import _quote
from database_snapshot import _hash_file
from offline_chat import (ArchiveError, LocalArchive, BEIJING, json_value, integer_sql,
                          timestamp_sql, source_integer, source_timestamp, source_identity,
                          MIN_TIMESTAMP, MAX_TIMESTAMP)
from wecom_content import typed_message, value, values, quote_entry, FORWARD_FIELDS
from wecom_media import WeComMedia
from offline_images import message_nodes

TABLES = ('message_table', 'message_small_table', 'kf_message_tableV1')
REQUIRED = {'message_id', 'server_id', 'sender_id', 'conversation_id', 'content_type', 'send_time', 'content'}


def connect(path):
    db = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    db.row_factory = sqlite3.Row
    def decode(value):
        try:
            return value.decode('utf8')
        except UnicodeDecodeError:
            return value
    db.text_factory = decode
    return db


def tables(db):
    return {row[0] for row in db.execute("select name from sqlite_master where type='table'")}


class WeComArchive(LocalArchive):
    def __init__(self, snapshot, self_id='', media_config=None):
        self.root, self.media_config = Path(snapshot).resolve(), media_config or {}
        self.metadata = json.loads((self.root / 'manifest.json').read_text(encoding='utf8'))
        self.fingerprints = {p: _hash_file(p) for p in self.root.glob('*.db')}
        self.adapter = self
        self.media_search = WeComMedia(self.media_config)
        self.contacts, self.member_names = {}, {}
        with closing(connect(self.root / 'user.db')) as db:
            for row in db.execute('SELECT id,name,real_name FROM user_table'):
                self.contacts[str(row['id'])] = row['real_name'] or row['name'] or str(row['id'])
        if not self_id:
            source = Path(self.media_config.get('sourceRoot', self.root))
            label = source.parent.name if source.name == 'Data' else source.name
            self_id = label if label.isdigit() and label in self.contacts else ''
        self.state = {'groups': [], 'contacts': self.contacts, 'self_id': self_id, 'warnings': []}
        with closing(connect(self.root / 'session.db')) as db:
            for row in db.execute('SELECT id,name,roomname_remark,last_message_time FROM conversation_table'):
                row = dict(row)
                cid = row['id']
                self.state['groups'].append({'id': cid, 'name': row.get('roomname_remark') or row.get('name') or cid,
                    'conversationKind': 'group' if cid.startswith('R:') else 'direct',
                    'lastMessageTime': row.get('last_message_time'), 'nameBasis': 'conversation_table'})
            if 'conversation_user_table' in tables(db):
                for row in db.execute('SELECT conversation_id,user_id,nick_name FROM conversation_user_table'):
                    self.member_names.setdefault(row['conversation_id'], {})[str(row['user_id'])] = row['nick_name'] or self.contacts.get(str(row['user_id']), str(row['user_id']))
        self.message_tables, self.message_counts = [], {}
        with closing(connect(self.root / 'message.db')) as db:
            present = tables(db)
            for table in TABLES:
                if table not in present:
                    continue
                columns = {r[1] for r in db.execute('PRAGMA table_info(' + _quote(table) + ')')}
                if not REQUIRED <= columns:
                    raise ArchiveError('企微消息表字段未支持，未加载部分数据。')
                self.message_tables.append(table)
                for cid, count in db.execute('SELECT conversation_id,count(*) FROM ' + _quote(table) + ' GROUP BY conversation_id'):
                    self.message_counts[cid] = self.message_counts.get(cid, 0) + count
            known = {v['id'] for v in self.state['groups']}
            for cid in self.message_counts.keys() - known:
                self.state['groups'].append({'id': cid, 'name': cid, 'conversationKind': 'group' if cid.startswith('R:') else 'direct', 'nameBasis': 'unmapped_conversation_id'})
        if not self.message_tables:
            raise ArchiveError('企微副本没有受支持的消息表。')

    def get_source_info(self):
        return self.metadata

    def _check(self):
        if self._integrity_deferred():
            return
        if {p for p in self.root.glob('*.db')} != set(self.fingerprints) or any(_hash_file(p) != digest for p, digest in self.fingerprints.items()):
            raise ArchiveError('企微副本在读取期间变化，请重新加载。')

    def coverage(self):
        return {'client': 'wecom', 'storageFamily': 'WeCom Windows wxSQLite3 AES-128 / SQLite',
                'scope': 'local_snapshot_only', 'cloudHistoryFetched': False,
                'selfIdConfigured': bool(self.state['self_id']),
                'identityBasis': 'selected_account_or_supplied_self_id' if self.state['self_id'] else 'unknown',
                'supportedMessageTables': self.message_tables, 'unsupportedTypesPreserved': True}

    def groups(self, include_direct=False):
        self._check()
        return {'schemaVersion': 1, 'conversations': [{**row, 'localMessageCount': self.message_counts.get(row['id'], 0),
                 'hasLocalMessages': bool(self.message_counts.get(row['id'])), 'messageDatabases': ['message.db']}
                for row in self.state['groups'] if include_direct or row['conversationKind'] == 'group'],
                'warnings': self.state['warnings'], 'coverage': self.coverage()}

    def members(self, chat):
        return {'members': [{'id': key, 'name': name} for key, name in self.member_names.get(chat, {}).items()],
                'warnings': [], 'historicalSpeakersAreMembers': False}

    def _streams(self, stack, chat, low, high, *, server_id=None):
        streams, schemas = [], []
        for table in self.message_tables:
            db = stack.enter_context(closing(connect(self.root / 'message.db')))
            layouts = self.__dict__.setdefault('_time_layouts', {})
            layout_key = (table, chat)
            if layout_key not in layouts:
                lower, upper, invalid, fractional = db.execute(
                    'SELECT min(send_time),max(send_time),sum(CASE WHEN typeof(send_time)!='
                    "'integer' OR typeof(message_id)!='integer' THEN 1 ELSE 0 END),"
                    'sum(CASE WHEN send_time>20000000000 AND send_time%1000!=0 THEN 1 ELSE 0 END) '
                    'FROM ' + _quote(table) + ' WHERE conversation_id=?', (chat,)).fetchone()
                scale = None
                if lower is None and not invalid:
                    scale = 1
                elif not invalid:
                    if MIN_TIMESTAMP <= lower <= upper <= 20000000000:
                        scale = 1
                    elif lower > 20000000000 and upper // 1000 <= MAX_TIMESTAMP and not fractional:
                        # Subsecond rows require normalized ordering: raw time
                        # order could disagree with the cross-table merge key.
                        scale = 1000
                layouts[layout_key] = scale
            scale = layouts[layout_key]
            conditions, params = ['conversation_id=?'], [chat]
            # Normalize each row: imported/migrated tables can mix seconds and
            # milliseconds. A bad row must neither change all dates nor vanish.
            date_conditions = []
            for operator, stamp in (('>=', low), ('<', high)):
                if stamp is not None:
                    date_conditions.append(('send_time' if scale else '__offline_timestamp') + operator + '?')
                    params.append(stamp * scale if scale else stamp)
            if date_conditions:
                conditions.append(' AND '.join(date_conditions) if scale else
                                  '((' + ' AND '.join(date_conditions) + ') OR __offline_timestamp IS NULL)')
            if server_id is not None:
                conditions.append('server_id=?')
                params.append(server_id)
            inner = ('SELECT *,rowid AS __offline_rowid,message_id AS local_id,' +
                     ('message_id' if scale else integer_sql('message_id')) + ' AS __offline_local_id,' +
                     ('send_time/' + str(scale) if scale else timestamp_sql('send_time', milliseconds=True)) +
                     ' AS __offline_timestamp FROM ' + _quote(table))
            cursor = stack.enter_context(closing(db.execute('SELECT *,__offline_timestamp AS create_time FROM (' + inner + ') WHERE ' +
                                ' AND '.join(conditions) + (' ORDER BY send_time,message_id,__offline_rowid' if scale else
                                ' ORDER BY __offline_timestamp IS NULL,__offline_timestamp,'
                                '__offline_local_id IS NULL,__offline_local_id,__offline_rowid'), params)))
            streams.append(self._stream(cursor, 'message.db', table))
            schemas.append({'database': 'message.db', 'table': table, 'columns': [r[1] for r in db.execute('PRAGMA table_info(' + _quote(table) + ')')]})
        return streams, schemas

    def _message(self, item, file, table, chat):
        raw = {k: v for k, v in item.items() if k not in ('local_id', 'create_time') and not k.startswith('__offline_')}
        stamp = item.get('__offline_timestamp', source_timestamp(item['send_time'], milliseconds=True))
        mid = item.get('__offline_local_id', source_integer(item['message_id']))
        sender = source_identity(item['sender_id'])
        kind = source_integer(item['content_type'])
        issues = [field for field, value in (('message_id', mid), ('send_time', stamp), ('content_type', kind)) if value is None]
        if item['sender_id'] not in (None, '', b'') and not sender:
            issues.append('sender_id')
        result = typed_message(item['content'], kind, extra=item.get('extra_content', b''))
        for node, _, _ in message_nodes(result):
            quote = node.get('quote')
            if quote and not quote.get('senderName'):
                name = self.member_names.get(chat['id'], {}).get(quote['senderId']) or self.contacts.get(quote['senderId'], '')
                quote['senderName'] = quote['message']['senderName'] = name
        own = bool(self.state['self_id']) and bool(sender)
        result['mentions']['self'] = self.state['self_id'] in result['mentions']['ids'] if own else None
        try:
            iso = datetime.fromtimestamp(stamp, BEIJING).isoformat() if stamp is not None else None
        except (OverflowError, ValueError, OSError):
            iso = None
            stamp = None
            if 'send_time' not in issues:
                issues.append('send_time')
        if issues:
            result.update(metadataIssues=issues, contentParseStatus=result['parseStatus'], parseStatus='invalid_metadata')
        identifier = str(mid) if mid is not None else 'rowid:' + str(item['__offline_rowid'])
        return {'schemaVersion': 1, 'client': 'wecom', 'chatId': chat['id'], 'chatName': chat['name'],
                'id': file + '|' + table + '|' + identifier, 'localId': mid, 'serverId': source_identity(item['server_id']),
                'timestamp': stamp, 'time': iso, 'localType': kind, 'senderId': sender,
                'senderName': self.member_names.get(chat['id'], {}).get(sender) or self.contacts.get(sender, sender),
                'senderIdentityBasis': 'conversation_user_table/user_table' if sender else 'unknown', 'senderConflict': False,
                'isSelf': sender == self.state['self_id'] if own else None,
                'direction': ('outgoing' if sender == self.state['self_id'] else 'incoming') if own else 'unknown',
                **result,
                'decodeStatus': 'ok' if result['parseStatus'] in ('ok', 'plain_system') else 'partially_supported',
                'decodeAttempts': [], 'rawContent': '', 'rawColumns': {k: json_value(v) for k, v in raw.items()},
                'provenance': {'database': file, 'table': table, 'localId': mid,
                               'sourceRowId': item.get('__offline_rowid')}}

    def _link_quote(self, row, destination, copy_media, visited=None, depth=0):
        if depth >= 10:
            return
        # Embedded occurrences have their own quote metadata but inherit the
        # enclosing chat boundary. Never search another conversation for them.
        for number, child in enumerate(row.get('record', {}).get('items', [])):
            nested = {**child, 'chatId': row['chatId'], 'id': row['id'] + ':part:' + str(number)}
            self._link_quote(nested, destination, copy_media, visited, depth + 1)
            for key in ('quote', 'parseStatus'):
                if key in nested:
                    child[key] = nested[key]
        quote = row.get('quote')
        if not quote:
            return
        token = quote.get('sourceToken')
        identifier = quote.get('sourceIdentifier')
        quote['sourceLinkStatus'] = 'source_id_unavailable'
        if not (token or identifier):
            return
        chat = row['chatId']
        cached_chat, index = getattr(self, '_quote_sources', (None, {}))
        if cached_chat != chat:
            index = {}
            with closing(connect(self.root / 'message.db')) as db:
                available = tables(db)
                for table in TABLES:
                    if table not in available:
                        continue
                    columns = {entry[1] for entry in db.execute('PRAGMA table_info(' + _quote(table) + ')')}
                    if 'extra_content' not in columns:
                        continue
                    # INTEGER PRIMARY KEY aliases rowid; name the result explicitly.
                    for item in db.execute('SELECT rowid AS __offline_rowid,extra_content FROM '+_quote(table)+' WHERE conversation_id=?', (chat,)):
                        candidate = value(value(item['extra_content'], 3045), 1)
                        if candidate:
                            index.setdefault(('legacy', candidate), []).append((item['__offline_rowid'], table))
                        key = value(value(value(item['extra_content'], 3045), 3), 3)
                        if key:
                            index.setdefault(('message_key', key), []).append((item['__offline_rowid'], table))
            self._quote_sources = chat, index
        candidates = []
        locations = index.get(('message_key', identifier.encode('utf8')), []) if identifier else index.get(('legacy', token.encode('ascii')), [])
        if locations:
            # Keep message bodies out of the group-wide index. Fetch only the
            # token's candidates, then apply the same exact matching rules.
            with closing(connect(self.root / 'message.db')) as db:
                for rowid, table in locations:
                    found = db.execute('SELECT *,rowid AS __offline_rowid FROM ' + _quote(table) +
                                       ' WHERE rowid=? AND conversation_id=?', (rowid, chat)).fetchone()
                    if found is None:
                        continue
                    item = dict(found)
                    stamp = source_timestamp(item['send_time'], milliseconds=True)
                    item['create_time'] = stamp
                    if (stamp is None or (quote['sourceType'] != -1 and source_integer(item['content_type']) != quote['sourceType']) or source_identity(item['sender_id']) != quote['senderId'] or
                            stamp != quote['sourceTimestamp'] or
                            (quote.get('contentSha256') and (not isinstance(item['content'], bytes) or
                             hashlib.sha256(item['content']).hexdigest() != quote['contentSha256']))):
                        continue
                    candidates.append((item, table))
        if len(candidates) != 1:
            quote['sourceLinkStatus'] = 'ambiguous_source' if candidates else 'source_not_found'
            return
        item, table = candidates[0]
        source = self._message(item, 'message.db', table, self.select_chat(chat_id=chat))
        visited = set(visited or ()) | {row['id']}
        if source['id'] in visited:
            quote['sourceLinkStatus'] = 'cyclic_source'
            return
        quote.update(sourceLinkStatus='matched_local_source', sourceMessageId=source['id'],
                     sourceLocalId=source['localId'], sourceProvenance=source['provenance'])
        if quote['resolutionStatus'] == 'embedded' or source['decodeStatus'] != 'ok' or source.get('metadataIssues'):
            return
        if copy_media:
            self._media(source, destination, Counter())
        self._link_quote(source, destination, copy_media, visited, depth + 1)
        quote.update(resolutionStatus='linked_local_source', embeddedMessage=quote['message'],
                     message={key: val for key, val in source.items() if key not in ('rawColumns','rawContent','quote')})
        if source.get('quote'):
            quote['message']['quote'] = source['quote']
        if row['parseStatus'] == 'partial_quote':
            row.update(parseStatus='ok', decodeStatus='ok')

    def _media(self, row, destination, media_counts):
        raw = row.get('rawColumns', {}).get('local_extra_content', {})
        import base64
        local = base64.b64decode(raw['data']) if isinstance(raw, dict) and raw.get('encoding') == 'base64' else b''
        raw_content = row.get('rawColumns', {}).get('content', {})
        content = base64.b64decode(raw_content['data']) if isinstance(raw_content, dict) and raw_content.get('encoding') == 'base64' else b''
        raw_extra = row.get('rawColumns', {}).get('extra_content', {})
        extra = base64.b64decode(raw_extra['data']) if isinstance(raw_extra, dict) and raw_extra.get('encoding') == 'base64' else b''
        def attach(node, payload, local_hint, extra):
            if node['kind'] in ('image', 'file', 'video', 'voice', 'emoji'):
                node['asset'] = self.media_search.attach(node, destination, local_hint)
                media_counts['preview_only' if node['asset'].get('previewOnly') else node['asset']['status']] += 1
            entries = values(payload, 1) if node.get('kind') == 'record' else []
            for index, child in enumerate(node.get('record', {}).get('items', [])):
                if child.get('timestamp'):
                    try:
                        child['time'] = datetime.fromtimestamp(child['timestamp'], BEIJING).isoformat()
                    except (ValueError, OSError, OverflowError):
                        pass
                entry = entries[index] if index < len(entries) else b''
                field = FORWARD_FIELDS.get(child.get('dataType'))
                attach(child, value(entry, field) if field else b'', value(entry, 17), value(entry, 17))
            if node.get('quote'):
                entry = quote_entry(extra)
                field = FORWARD_FIELDS.get(node['quote'].get('sourceType'))
                attach(node['quote']['message'], value(entry, field) if field else b'', value(entry, 17), value(entry, 17))
        attach(row, content, local, extra)
