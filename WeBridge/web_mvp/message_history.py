"""Bounded, read-only searches over one configured immutable message snapshot.

Opaque cursors live only in this adapter process. They expire after 15 minutes,
after eviction, or when the snapshot is reconfigured. Up to 32 returned pages
can be replayed for back/forward navigation. The nontext-message pass
precedes display so ordinary revocations also cover originals on older pages or
outside the selected dates. This does not claim recovery of deleted records or
nonstandard revocation encodings unsupported by the existing content decoder.
"""
from collections import OrderedDict
from contextlib import ExitStack
from copy import deepcopy
from datetime import date, datetime, time as datetime_time, timedelta, timezone
import heapq
import json
import re
import secrets
import time

from database_adapter import DatabaseError, _quote, _warning


MAX_SCAN = 5000
MAX_SESSIONS = 16
MAX_CACHED_PAGES = 32
MAX_CACHED_BYTES = 16 * 1024 * 1024
MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_REVOKE_BYTES = 4 * 1024 * 1024
MAX_SUMMARY_TEXT = 8000
MAX_TRACKED = 100000
CURSOR_TTL = 15 * 60
CURSOR_PATTERN = re.compile(r'[A-Za-z0-9_-]{43}')
SHANGHAI = timezone(timedelta(hours=8), name='Asia/Shanghai')
FIELDS = ('local_id', 'server_id', 'local_type', 'create_time', 'real_sender_id',
          'message_content', 'compress_content', 'source', 'message_source',
          'msgsource', 'msg_source', 'atuserlist', 'at_list')


def validate_history_params(params):
    """Shared adapter/HTTP validation; accepts numeric HTTP limit strings.

    Returned values can be expanded directly into adapter.call('message_history',
    account=..., groupId=..., **validated). Account/group access checks remain in
    DatabaseAdapter.call and the server's watched-conversation authorization.
    """
    def invalid(message):
        raise DatabaseError('invalid_history_params', message)

    limit = params.get('limit', 200)
    if isinstance(limit, str) and re.fullmatch(r'[1-9][0-9]{0,3}', limit):
        limit = int(limit)
    if type(limit) is not int or not 1 <= limit <= 2000:
        invalid('每页消息数量须为 1 至 2000。')
    query = params.get('query', '')
    if not isinstance(query, str) or len(query) > 200 or any(ord(c) < 32 for c in query):
        invalid('搜索词须为不超过 200 字的单行文本。')
    result = {'limit': limit, 'query': query.strip(), 'startDate': '', 'endDate': ''}
    for name in ('startDate', 'endDate'):
        value = params.get(name, '')
        if not isinstance(value, str) or (value and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value)):
            invalid('日期须使用 YYYY-MM-DD 格式。')
        try:
            if value:
                day = date.fromisoformat(value)
                # Bound to the timestamps accepted by the snapshot decoder.
                if day.year < 1970 or day.year > 9998:
                    invalid('日期须在 1970 至 9998 年之间。')
        except ValueError:
            invalid('日期无效。')
        result[name] = value
    if result['startDate'] and result['endDate'] and result['startDate'] > result['endDate']:
        invalid('开始日期不能晚于结束日期。')
    cursor = params.get('cursor', '')
    if not isinstance(cursor, str) or (cursor and CURSOR_PATTERN.fullmatch(cursor) is None):
        invalid('历史查询游标无效，请重新查询。')
    result['cursor'] = cursor
    return result


def _bound(value, next_day=False):
    if not value:
        return None
    day = date.fromisoformat(value) + timedelta(days=bool(next_day))
    return int(datetime.combine(day, datetime_time.min, SHANGHAI).timestamp())


def _scope(state, group, params):
    return (state['source_id'], state['info']['revision'], group,
            params['query'].casefold(), params['startDate'], params['endDate'], params['limit'])


def _streams(adapter, state, group, positions, system_only, stack, warnings):
    """Merge by (timestamp, shard, local_id, rowid), never timestamp alone.

    Each SQL cursor yields lazily and all connections close at the page boundary.
    SQLite may still scan/sort source tables without suitable indexes; the bound
    applies to decoded raw rows, not a promise of constant database query time.
    """
    streams = []
    for index, path in enumerate(state['shards']):
        if positions.get(str(index), {}).get('done'):
            continue
        table = adapter._message_table(state['tables'][path], group)
        if table is None:
            positions[str(index)] = {'done': True}
            continue
        db = stack.enter_context(adapter._connect(path))
        columns = adapter._columns(db, table)
        file = path.relative_to(state['root']).as_posix()
        if not {'local_id', 'local_type', 'create_time', 'message_content'} <= columns.keys():
            warnings.append(_warning('message_schema_unsupported', '消息表缺少必要字段，此分片未读取。', file))
            positions[str(index)] = {'done': True}
            continue
        selected = [key for key in FIELDS if key in columns]
        sender, join = "''", ''
        names = state['tables'][path].get('name2id')
        if names and 'real_sender_id' in columns:
            name_columns = adapter._columns(db, names)
            if 'user_name' in name_columns:
                sender = 'n.' + _quote(name_columns['user_name'])
                join = ' LEFT JOIN ' + _quote(names) + ' n ON m.' + _quote(columns['real_sender_id']) + '=n.rowid'
        timestamp = 'COALESCE(CAST(m.' + _quote(columns['create_time']) + ' AS INTEGER),0)'
        local = 'COALESCE(CAST(m.' + _quote(columns['local_id']) + ' AS INTEGER),0)'
        sql = 'SELECT ' + ','.join('m.' + _quote(columns[key]) + ' AS ' + _quote(key) for key in selected)
        sql += ',m.rowid AS history_rowid,' + timestamp + ' AS history_timestamp,' + local + ' AS history_local,'
        sql += sender + ' AS sender_username FROM ' + _quote(table) + ' m' + join
        conditions, values = [], []
        if system_only:
            # parse_content recognizes sysmsg XML for any nontext native type.
            # Checking only 10000/10002 would expose a recognized revocation's
            # original on earlier pages when its native type is nonstandard.
            conditions.append('(CAST(m.' + _quote(columns['local_type']) + ' AS INTEGER) & 4294967295) != 1')
        last = positions.get(str(index), {}).get('last')
        if last is not None:
            conditions.append('(' + timestamp + ',' + local + ',m.rowid) < (?,?,?)')
            values.extend(last)
        if conditions:
            sql += ' WHERE ' + ' AND '.join(conditions)
        sql += ' ORDER BY ' + timestamp + ' DESC,' + local + ' DESC,m.rowid DESC LIMIT ?'
        values.append(MAX_SCAN + 1)
        cursor = db.execute(sql, values)
        streams.append((index, file, table, cursor))
    return streams


def _iter_rows(adapter, state, group, positions, system_only, warnings):
    with ExitStack() as stack:
        heap = []
        streams = _streams(adapter, state, group, positions, system_only, stack, warnings)
        def push(stream):
            index, file, table, cursor = stream
            row = cursor.fetchone()
            if row is None:
                positions[str(index)] = {'done': True}
                return
            item = dict(row)
            key = (item['history_timestamp'], item['history_local'], item['history_rowid'])
            heapq.heappush(heap, ((-key[0], -index, -key[1], -key[2]), stream, key, item))
        for stream in streams:
            push(stream)
        while heap:
            _, stream, key, item = heapq.heappop(heap)
            index, file, table, _ = stream
            positions[str(index)] = {'last': key}
            push(stream)
            yield item, file, table, bool(heap)


def _search_text(row):
    pieces = []
    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ('text', 'sender', 'senderId', 'senderName', 'filename', 'title', 'description') and isinstance(child, str):
                    pieces.append(child)
                elif isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(row)
    return '\n'.join(pieces).casefold()


def _json_size(value):
    return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))


def _summary(row):
    """Search full decoded content first; return only bounded display fields."""
    result = {key: row[key] for key in ('id', 'serverId', 'localId', 'timestamp', 'type', 'kind',
                                      'isSelf', 'isSelfKnown', 'mentionSelf', 'mentionEveryone',
                                      'mentionStatus', 'decodeStatus', 'source')}
    truncated = bool(row.get('textTruncated'))
    for key, limit in (('text', MAX_SUMMARY_TEXT), ('senderName', 256), ('senderId', 256),
                       ('dbName', 512), ('sourceId', 160)):
        value = row[key]
        result[key] = value[:limit]
        truncated |= len(value) > limit
    if 'media' in row:
        media = row['media']
        filename = media['filename']
        result['media'] = {'kind': media['kind'], 'filename': filename[:1000]}
        truncated |= len(filename) > 1000
    # Nested record bodies and quote structures are searched but not retained in
    # history-page caches. The regular conversation renderer owns rich previews.
    truncated |= bool(row.get('record') or row.get('quote'))
    result['textTruncated'] = truncated
    return result


def _bounded_warnings(warnings):
    result = [{key: value[:limit] for key, limit in (('code', 128), ('message', 512),
               ('file', 256), ('messageId', 256)) if isinstance(value := row.get(key), str)}
              for row in warnings[:25]]
    if len(warnings) > 25:
        result.append(_warning('history_warnings_truncated', '本页警告较多，仅显示前 25 条。'))
    return result


def _dedupe(row, session):
    if session['seenStamp'] != row['timestamp']:
        session['seenStamp'], session['seen'] = row['timestamp'], set()
    digest = row.pop('_contentHash')
    if int(row['serverId']) <= 0 or row['decodeStatus'] != 'ok':
        return False
    key = (row['serverId'], row['senderId'], row['type'], digest,
           row['mentionSelf'], row['mentionEveryone'], row['mentionStatus'])
    if key in session['seen']:
        return True
    if len(session['seen']) >= MAX_TRACKED:
        raise DatabaseError('history_state_limit', '同秒消息过多，无法安全继续去重，请缩小副本范围。')
    session['seen'].add(key)
    return False


def _apply_revoke(row, revokes):
    if row['serverId'] in revokes and row['kind'] != 'revoke':
        for key in ('media', 'record', 'quote', 'description'):
            row.pop(key, None)
        replacement = revokes[row['serverId']]
        row.update(kind='revoke', text=replacement['text'], textTruncated=replacement['truncated'],
                   mentionSelf=False, mentionEveryone=False)


def _page(adapter, state, group, params, session):
    warnings = deepcopy(state['warnings'])
    scanned, messages = 0, []
    pending = session.pop('pending', None)
    if pending is not None:
        messages.append(pending)
    message_bytes = sum(_json_size(row) + 2 for row in messages)
    message_budget = MAX_PAGE_BYTES - min(65536, MAX_PAGE_BYTES // 4)
    page_limited = False
    if session['phase'] == 'revokes':
        stream = _iter_rows(adapter, state, group, session['revokePositions'], True, warnings)
        try:
            for item, file, table, more in stream:
                scanned += 1
                row = adapter._present_message(item, state, file, table, warnings)
                if row and row.get('revokedServerId') and row['kind'] == 'revoke':
                    key = row['revokedServerId']
                    if key not in session['revokes']:
                        if len(session['revokes']) >= MAX_TRACKED:
                            raise DatabaseError('history_state_limit', '撤回记录超过历史查询安全上限，请缩小副本范围。')
                        replacement = row['text'][:512]
                        session['revokeBytes'] += len((key + replacement).encode('utf-8'))
                        if session['revokeBytes'] > MAX_REVOKE_BYTES:
                            raise DatabaseError('history_state_limit', '撤回记录文本超过历史查询安全上限，请缩小副本范围。')
                        session['revokes'][key] = {'text': replacement, 'truncated': len(row['text']) > 512}
                if scanned >= MAX_SCAN:
                    if not more:
                        session['phase'] = 'messages'
                    break
            else:
                session['phase'] = 'messages'
        finally:
            stream.close()
    start, end = session['bounds']
    query = params['query'].casefold()
    if session['phase'] == 'messages' and scanned < MAX_SCAN and len(messages) < params['limit']:
        stream = _iter_rows(adapter, state, group, session['positions'], False, warnings)
        try:
            for item, file, table, more in stream:
                scanned += 1
                row = adapter._present_message(item, state, file, table, warnings)
                if row is not None:
                    duplicate = _dedupe(row, session)
                    if start is not None and row['timestamp'] < start:
                        session['phase'] = 'done'
                        break
                    _apply_revoke(row, session['revokes'])
                    if (not duplicate and (end is None or row['timestamp'] < end)
                            and (not query or query in _search_text(row))):
                        summary = _summary(row)
                        size = _json_size(summary) + 2
                        if size > message_budget:
                            raise DatabaseError('history_result_limit', '单条历史摘要超过响应安全上限。')
                        if message_bytes + size > message_budget:
                            session['pending'] = summary
                            page_limited = True
                            if not more:
                                session['phase'] = 'done'
                            break
                        messages.append(summary)
                        message_bytes += size
                    if len(messages) >= params['limit']:
                        if not more:
                            session['phase'] = 'done'
                        break
                if scanned >= MAX_SCAN:
                    if not more:
                        session['phase'] = 'done'
                    break
            else:
                session['phase'] = 'done'
        finally:
            stream.close()
    more = session['phase'] != 'done' or session.get('pending') is not None
    result = {'messages': sorted(messages, key=lambda row: (row['timestamp'], row['id'])),
              'warnings': _bounded_warnings(warnings), 'limit': params['limit'], 'hasMore': more,
              'nextCursor': '', 'scanned': scanned, 'scanLimited': more and scanned >= MAX_SCAN,
              'sourceRevision': state['info']['revision'], 'timezone': 'Asia/Shanghai'}
    result['pageLimited'] = page_limited
    if page_limited:
        result['warnings'].append(_warning('history_page_size_limit', '本页摘要已达大小上限，可继续下一页。'))
    if session['phase'] == 'revokes':
        result['warnings'].append(_warning('history_preparing_revocations', '正在核对撤回记录，请继续查询下一页。'))
    return result


def query_history(adapter, state, group, raw_params):
    params = validate_history_params(raw_params)
    scope, now = _scope(state, group, params), time.monotonic()
    sessions = state.setdefault('history_sessions', OrderedDict())
    pages = state.setdefault('history_pages', OrderedDict())
    for key in list(sessions):
        if now - sessions[key]['touched'] > CURSOR_TTL:
            del sessions[key]
    for token in list(pages):
        if pages[token]['sessionId'] not in sessions or now - pages[token]['touched'] > CURSOR_TTL:
            del pages[token]
    cursor, session_id, session = params['cursor'], None, None
    if cursor:
        cached = pages.get(cursor)
        if cached is not None:
            if cached['scope'] != scope:
                raise DatabaseError('history_cursor_mismatch', '历史查询条件或数据来源已变化，请重新查询。')
            cached['touched'] = now
            sessions[cached['sessionId']]['touched'] = now
            sessions.move_to_end(cached['sessionId'])
            pages.move_to_end(cursor)
            return deepcopy(cached['result'])
        for key, candidate in sessions.items():
            if cursor == candidate['token']:
                session_id, session = key, candidate
                break
        if session is None:
            raise DatabaseError('history_cursor_expired', '历史查询游标已失效，请重新查询。')
        if session['scope'] != scope:
            raise DatabaseError('history_cursor_mismatch', '历史查询条件或数据来源已变化，请重新查询。')
        session = deepcopy(session)
    else:
        session_id = secrets.token_urlsafe(18)
        session = {'scope': scope, 'phase': 'revokes', 'positions': {}, 'revokePositions': {},
                   'bounds': (_bound(params['startDate']), _bound(params['endDate'], True)),
                   'revokes': {}, 'revokeBytes': 0, 'seen': set(), 'seenStamp': None, 'token': '', 'touched': now}
    result = _page(adapter, state, group, params, session)
    if result['hasMore']:
        result['nextCursor'] = secrets.token_urlsafe(32)
    session.update(token=result['nextCursor'], touched=now)
    if cursor:
        pages[cursor] = {'scope': scope, 'sessionId': session_id, 'touched': now,
                         'size': _json_size(result), 'result': deepcopy(result)}
        pages.move_to_end(cursor)
        while len(pages) > MAX_CACHED_PAGES or sum(page['size'] for page in pages.values()) > MAX_CACHED_BYTES:
            pages.popitem(last=False)
    if cursor or result['hasMore']:
        sessions[session_id] = session
        sessions.move_to_end(session_id)
        while len(sessions) > MAX_SESSIONS:
            evicted, _ = sessions.popitem(last=False)
            for token in list(pages):
                if pages[token]['sessionId'] == evicted:
                    del pages[token]
    return result
