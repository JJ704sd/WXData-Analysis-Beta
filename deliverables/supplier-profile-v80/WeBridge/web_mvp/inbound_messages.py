"""Internal, bounded inbound scans over one immutable database revision.

Consumers drain a scan while holding their source lock. A cursor is a trusted,
JSON-serializable checkpoint, independent of UI history searches. This module
does not send messages, retain scan sessions, or perform event-ledger dedupe.
"""
from collections import OrderedDict
from contextlib import ExitStack
from copy import deepcopy
import heapq
import json

from database_adapter import DatabaseError, _quote, _warning


MAX_SCAN = 500
MAX_MESSAGES = 100
PAGE_BYTES = 2 * 1024 * 1024
MAX_REVOKED = 100000
MAX_REVOKE_CACHE_GROUPS = 512
MAX_REVOKE_CACHE_BYTES = 8 * 1024 * 1024
FIELDS = ('local_id', 'server_id', 'local_type', 'create_time', 'real_sender_id',
          'message_content', 'compress_content', 'source', 'message_source',
          'msgsource', 'msg_source', 'atuserlist', 'at_list')


def _streams(adapter, state, group, checkpoint, warnings, stack):
    streams = []
    positions = checkpoint['positions']
    for path in state['shards']:
        file = path.relative_to(state['root']).as_posix()
        if positions.get(file, {}).get('done'):
            continue
        table = adapter._message_table(state['tables'][path], group)
        if table is None:
            positions[file] = {'done': True}
            continue
        db = stack.enter_context(adapter._connect(path))
        columns = adapter._columns(db, table)
        if not {'local_id', 'local_type', 'create_time', 'message_content'} <= columns.keys():
            warnings.append(_warning('message_schema_unsupported', '消息表缺少必要字段，此分片未读取。', file))
            positions[file] = {'done': True}
            continue
        sender, join = "''", ''
        names = state['tables'][path].get('name2id')
        if names and 'real_sender_id' in columns:
            name_columns = adapter._columns(db, names)
            if 'user_name' in name_columns:
                sender = 'n.' + _quote(name_columns['user_name'])
                join = ' LEFT JOIN ' + _quote(names) + ' n ON m.' + _quote(columns['real_sender_id']) + '=n.rowid'
        timestamp = 'COALESCE(CAST(m.' + _quote(columns['create_time']) + ' AS INTEGER),0)'
        local = 'COALESCE(CAST(m.' + _quote(columns['local_id']) + ' AS INTEGER),0)'
        selected = [key for key in FIELDS if key in columns]
        sql = 'SELECT ' + ','.join('m.' + _quote(columns[key]) + ' AS ' + _quote(key) for key in selected)
        sql += ',m.rowid AS inbound_rowid,' + timestamp + ' AS inbound_timestamp,' + local + ' AS inbound_local,'
        sql += sender + ' AS sender_username FROM ' + _quote(table) + ' m' + join
        conditions, values = [], []
        if checkpoint['phase'] == 'revokes':
            # The content decoder recognizes revocation XML for any nontext
            # native kind. Do not apply either time bound: source timestamps do
            # not guarantee that a revocation is dated after its original.
            conditions.append('(CAST(m.' + _quote(columns['local_type']) + ' AS INTEGER) & 4294967295) != 1')
        else:
            conditions.append(timestamp + ' >= ?')
            values.append(checkpoint['start'])
            if checkpoint['end'] is not None:
                conditions.append(timestamp + ' <= ?')
                values.append(checkpoint['end'])
        last = positions.get(file, {}).get('last')
        if last is not None:
            conditions.append('(' + timestamp + ',' + local + ',m.rowid) > (?,?,?)')
            values.extend(last)
        sql += ' WHERE ' + ' AND '.join(conditions)
        sql += ' ORDER BY ' + timestamp + ',' + local + ',m.rowid LIMIT ?'
        values.append(MAX_SCAN + 1)
        streams.append((file, table, db.execute(sql, values)))
    return streams


def _rows(adapter, state, group, checkpoint, warnings):
    """Global order is timestamp, relative shard, local ID, SQLite rowid."""
    with ExitStack() as stack:
        heap = []
        positions = checkpoint['positions']
        def push(stream):
            file, table, cursor = stream
            row = cursor.fetchone()
            if row is None:
                positions[file] = {'done': True}
                return
            item = dict(row)
            key = (item['inbound_timestamp'], file, item['inbound_local'], item['inbound_rowid'])
            heapq.heappush(heap, (key, stream, item))
        for stream in _streams(adapter, state, group, checkpoint, warnings, stack):
            push(stream)
        while heap:
            key, stream, item = heapq.heappop(heap)
            file, table, _ = stream
            positions[file] = {'last': [key[0], key[2], key[3]]}
            push(stream)
            yield item, file, table, bool(heap)


def _size(message):
    return len(json.dumps(message, ensure_ascii=False).encode('utf-8'))


def scan_inbound(adapter, state, group, start, end, cursor=None):
    # This cache belongs to the configured state object, never just its revision
    # label. configure() replaces it even when a caller reuses that label.
    cache = state.setdefault('inbound_revoke_cache', OrderedDict())
    if cursor is None:
        checkpoint = {'revision': state['info']['revision'], 'sourceId': state['source_id'],
                      'group': group, 'start': start, 'end': end, 'phase': 'revokes',
                      'positions': {}, 'revoked': [], 'revokes_cacheable': True, 'complete': False}
        cached = cache.get(group)
        if cached is not None:
            checkpoint.update(phase='messages', revoked=list(cached['revoked']))
            cache.move_to_end(group)
    else:
        if cursor['revision'] != state['info']['revision']:
            raise DatabaseError('inbound_cursor_revision_changed', '入站扫描副本已变化，请重新扫描当前窗口。')
        if cursor['sourceId'] != state['source_id'] or cursor['group'] != group:
            raise DatabaseError('inbound_cursor_scope_changed', '入站扫描来源或会话已变化。')
        assert (cursor['start'], cursor['end']) == (start, end), 'inbound cursor time range changed'
        checkpoint = deepcopy(cursor)

    warnings = deepcopy(state['warnings'])
    revoked = set(checkpoint['revoked'])
    scanned, messages = 0, []
    pending = checkpoint.pop('pending', None)
    if pending is not None:
        messages.append(pending)
    size = sum(_size(message) for message in messages)
    if checkpoint['phase'] == 'revokes':
        warning_count = len(warnings)
        rows = _rows(adapter, state, group, checkpoint, warnings)
        try:
            for item, file, table, more in rows:
                scanned += 1
                message = adapter._present_message(item, state, file, table, warnings)
                if message is not None and message['kind'] == 'revoke' and message['revokedServerId']:
                    revoked.add(message['revokedServerId'])
                    if len(revoked) > MAX_REVOKED:
                        raise DatabaseError('inbound_revoke_limit', '入站扫描撤回记录超过安全上限。')
                if not more:
                    checkpoint.update(phase='messages', positions={})
                if scanned >= MAX_SCAN:
                    break
            else:
                checkpoint.update(phase='messages', positions={})
        finally:
            rows.close()
        checkpoint['revokes_cacheable'] &= len(warnings) == warning_count
        if checkpoint['phase'] == 'messages' and checkpoint['revokes_cacheable']:
            ids = tuple(sorted(revoked))
            size_bytes = _size({group: ids})
            if size_bytes <= MAX_REVOKE_CACHE_BYTES:
                cache[group] = {'revoked': ids, 'size': size_bytes}
                cache.move_to_end(group)
                while (len(cache) > MAX_REVOKE_CACHE_GROUPS or
                       sum(entry['size'] for entry in cache.values()) > MAX_REVOKE_CACHE_BYTES):
                    cache.popitem(last=False)

    if checkpoint['phase'] == 'messages' and scanned < MAX_SCAN and size < PAGE_BYTES:
        rows = _rows(adapter, state, group, checkpoint, warnings)
        try:
            for item, file, table, more in rows:
                scanned += 1
                message = adapter._present_message(item, state, file, table, warnings)
                if not more:
                    checkpoint['phase'] = 'done'
                if message is not None:
                    if message['serverId'] in revoked and message['kind'] != 'revoke':
                        for field in ('media', 'record', 'quote', 'description'):
                            message.pop(field, None)
                        message.update(kind='revoke', text='一条消息已撤回', mentionSelf=False, mentionEveryone=False)
                    message.pop('_contentHash')
                    message_size = _size(message)
                    if messages and size + message_size > PAGE_BYTES:
                        checkpoint['pending'] = message
                        break
                    messages.append(message)
                    size += message_size
                if scanned >= MAX_SCAN or len(messages) >= MAX_MESSAGES or size >= PAGE_BYTES:
                    break
            else:
                checkpoint['phase'] = 'done'
        finally:
            rows.close()
    checkpoint['revoked'] = sorted(revoked)
    checkpoint['complete'] = checkpoint['phase'] == 'done' and 'pending' not in checkpoint
    return {'messages': messages, 'warnings': warnings, 'cursor': checkpoint,
            'complete': checkpoint['complete']}
