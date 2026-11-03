"""Loss-aware WeCom protobuf presentation. Unknown fields stay in rawColumns.
Field mappings are based on Windows 5.0.12 source snapshots, not WeChat types.
No generated protobuf runtime or network access is required.
"""
from datetime import datetime, timezone, timedelta
import hashlib
import re
from rich_content import http_links

MAX_BYTES, MAX_FIELDS, MAX_DEPTH = 4 * 1024 * 1024, 10000, 10
FORWARD_FIELDS = {0: 101, 2: 101, 4: 107, 14: 103, 15: 103, 20: 103, 22: 105, 23: 105, 29: 106}


def fields(data):
    if not isinstance(data, bytes) or len(data) > MAX_BYTES:
        return []
    offset, result = 0, []
    def varint():
        nonlocal offset
        value = 0
        for shift in range(0, 70, 7):
            if offset >= len(data):
                raise ValueError
            byte = data[offset]
            offset += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value
        raise ValueError
    try:
        while offset < len(data):
            tag = varint()
            number, wire = tag >> 3, tag & 7
            if not number or len(result) >= MAX_FIELDS:
                raise ValueError
            if wire == 0:
                value = varint()
            elif wire in (1, 5):
                size = 8 if wire == 1 else 4
                value = data[offset:offset + size]
                offset += size
            elif wire == 2:
                size = varint()
                value = data[offset:offset + size]
                offset += size
                if len(value) != size:
                    raise ValueError
            else:
                raise ValueError
            if offset > len(data):
                raise ValueError
            result.append((number, wire, value))
        return result
    except (ValueError, IndexError):
        return []


def values(data, number, wire=2):
    return [value for field, kind, value in fields(data) if field == number and kind == wire]


def value(data, number, default=b'', wire=2):
    items = values(data, number, wire)
    return items[0] if items else default


def text(data):
    if isinstance(data, str):
        return data
    try:
        decoded = data.decode('utf8')
        return decoded if all(ord(c) >= 32 or c in '\r\n\t' for c in decoded) else ''
    except (UnicodeError, AttributeError):
        return ''


def _text_fields(data, *numbers):
    raw = [value(data, number) for number in numbers]
    decoded = [text(item) for item in raw]
    return decoded, any(original and not item for original, item in zip(raw, decoded))


def leaves(data, depth=0):
    if depth >= MAX_DEPTH:
        return []
    result = []
    for _, wire, child in fields(data):
        if wire != 2:
            continue
        nested = fields(child)
        if nested:
            result.extend(leaves(child, depth + 1))
        elif text(child):
            result.append(text(child))
    return result


def quote_entry(extra):
    """Observed Windows 5.0.12 quote envelope, never arbitrary protobuf strings."""
    envelopes = values(extra, 1002)
    entries = values(envelopes[0], 1) if len(envelopes) == 1 else []
    if len(entries) != 1:
        return b''
    decoded = fields(entries[0])
    for number in (1, 2):
        matches = [(wire, val) for field, wire, val in decoded if field == number]
        if len(matches) != 1 or matches[0][0] != 0:
            return b''
    kinds = [(wire, val) for field, wire, val in decoded if field == 5]
    if kinds and (len(kinds) != 1 or kinds[0][0] != 0):
        return b''
    return entries[0]


def quote_identifier(extra, entry):
    if not entry:
        return ''
    identifiers = values(entry, 16)
    if not identifiers or identifiers == [b'']:
        envelopes = values(extra, 1002)
        identifiers = values(envelopes[0], 2) if len(envelopes) == 1 else []
    return text(identifiers[0]) if len(identifiers) == 1 and len(identifiers[0]) <= 256 else ''


def _entry_metadata(entry):
    sender_values = values(entry, 1, 0)
    stamp_values = values(entry, 2, 0)
    sender = str(sender_values[0]) if sender_values else ''
    stamp = stamp_values[0] if stamp_values else None
    names, bad_name = _text_fields(entry, 11)
    name = names[0]
    issues = ['sender_name'] if bad_name else []
    if not sender_values:
        issues.append('sender_id')
    if stamp is None:
        issues.append('send_time')
    if stamp is not None and stamp > 20000000000:
        stamp //= 1000
    try:
        iso = datetime.fromtimestamp(stamp, timezone(timedelta(hours=8))).isoformat() if stamp else ''
    except (ValueError, OSError, OverflowError):
        iso = ''
        stamp = None
        issues.append('send_time')
    return sender, stamp, iso, issues, name


def typed_message(data, kind, *, depth=0, extra=b'', prefix=''):
    result = {'kind': 'unknown', 'text': '', 'links': [], 'parseStatus': 'unsupported_wecom_type', 'baseType': kind, 'subType': 0,
              'mentions': {'ids': [], 'self': None, 'status': 'none'}}
    if depth >= MAX_DEPTH:
        return {**result, 'parseStatus': 'depth_limit'}
    if kind in (0, 2):
        chunks, gap_indexes, mention_ids, unsupported = [], set(), [], False

        def append_chunk(part, *, gap=False):
            if part:
                if gap:
                    gap_indexes.add(len(chunks))
                chunks.append(part)

        for block in values(data, 1):
            body = value(block, 2)
            block_type = value(block, 1, -1, 0)
            if block_type in (0, 3):
                body_values = values(body, 1)
                for raw in body_values:
                    part = text(raw)
                    if part:
                        append_chunk(part)
                    elif raw:
                        append_chunk('[文本片段未完整解析]', gap=True)
                        unsupported = True
                if body and not body_values:
                    append_chunk('[文本片段未完整解析]', gap=True)
                    unsupported = True
            elif block_type in (5, 10):
                member = value(body, 1, None, 0)
                name = text(value(body, 2))
                if member is not None and name:
                    append_chunk('@' + name)
                    mention_ids.append(str(member))
                else:
                    append_chunk(name if name.strip() else '')
                    append_chunk('[提及对象未记录]', gap=True)
                    unsupported = True
            else:
                append_chunk('[文本片段未完整解析]', gap=True)
                unsupported = True
        text_value = ''.join(chunks)
        gap_spans, offset = [], 0
        for index, part in enumerate(chunks):
            end = offset + len(part)
            if index in gap_indexes:
                gap_spans.append((offset, end))
            offset = end
        result.update(kind='text', text=text_value, parseStatus='ok' if not unsupported and (chunks or not data) else 'unsupported_text_blocks',
                      mentions={'ids': list(dict.fromkeys(mention_ids)), 'self': None, 'status': 'structured_wecom' if mention_ids else 'none'})
        result['links'] = http_links(text_value, excluded_spans=gap_spans)
    elif kind in (14, 15, 20, 22, 23, 29, 101, 102):
        label = {14: 'image', 15: 'file', 20: 'file', 22: 'video', 23: 'video', 29: 'emoji', 101: 'image', 102: 'file'}[kind]
        filename_fields = (2,) if kind in (14, 15, 20, 102) else (107,) if kind == 22 else ()
        filenames, bad_filename = _text_fields(data, *filename_fields)
        filename = filenames[0] if filenames else label
        digest_field = 6 if kind == 29 else 9 if kind in (22, 23) else 10
        result.update(kind=label, text=filename or label,
                      parseStatus='unsupported_text_fields' if bad_filename else 'ok',
                      media={'filename': filename or label, 'md5': text(value(data, digest_field)),
                             'size': value(data, 3 if kind in (22, 23) else 4, 0, 0),
                             'width': value(data, 5, 0, 0), 'height': value(data, 6, 0, 0),
                             'thumbnailMd5': text(value(data, 30 if kind in (14, 101) else 18)),
                             'thumbnailSize': value(data, 27 if kind in (14, 101) else 17, 0, 0)})
        if kind in (101, 102) and (not re.fullmatch('[a-fA-F0-9]{32}', result['media']['md5'])
                or (kind == 102 and not filename)):
            result.update(kind='unknown', text='未完整适配的企微媒体结构；原始字段保留。', parseStatus='unsupported_media_schema')
            result.pop('media')
    elif kind == 4:
        items = []
        entries = values(data, 1)
        for index, entry in enumerate(entries):
            subtype = value(entry, 5, -1, 0)
            # Embedded content lives in a field specific to its message type.
            field = FORWARD_FIELDS.get(subtype)
            payload = value(entry, field) if field else b''
            part = prefix + '.' + str(index) if prefix else str(index)
            row = typed_message(payload, subtype, depth=depth + 1, extra=value(entry, 17), prefix=part) if payload else {
                **result, 'parseStatus': 'unsupported_forward_payload'}
            sender, stamp, iso, issues, name = _entry_metadata(entry)
            row.update(part=part, dataType=subtype, senderId=sender,
                       senderName=name, timestamp=stamp, time=iso)
            if issues:
                row.update(metadataIssues=issues, contentParseStatus=row['parseStatus'], parseStatus='invalid_metadata')
            items.append(row)
        (title,), bad_title = _text_fields(data, 2)
        result.update(kind='record', text=title or '转发聊天记录',
                      parseStatus='unsupported_text_fields' if bad_title else 'ok',
                      record={'title': title, 'items': items, 'count': len(items),
                              'status': 'ok' if not bad_title and items and all(v['parseStatus'] == 'ok' for v in items) else 'partially_supported'})
    elif kind in (13, 31, 38, 104):
        content = value(data, 1) if kind == 31 else data
        title_field = 3 if kind == 13 else 1
        description_field = 4 if kind == 13 else 2
        url_field = 1 if kind == 13 else 5 if kind == 31 else 3
        (title, description, url), bad_fields = _text_fields(content, title_field, description_field, url_field)
        result.update(kind='link' if url.startswith(('https://', 'http://')) else 'system', text=title,
                      description=description, links=[url] if url.startswith(('https://', 'http://')) else [],
                      parseStatus='unsupported_text_fields' if bad_fields else 'ok')
    elif kind in (503, 1001, 1002, 1003, 1004, 1011, 1017, 1022, 1023, 1051, 1052):
        result.update(kind='system', text=text(data) or '企微系统事件（原始字段见 JSONL）', parseStatus='plain_system')
    else:
        # Do not treat arbitrary protobuf strings (keys, server IDs) as prose.
        result['text'] = '未适配的企微消息类型 ' + str(kind) + '；原始字段已保留。'
    if values(extra, 1002):
        entry = quote_entry(extra)
        source_type = value(entry, 5, -1, 0)
        field = FORWARD_FIELDS.get(source_type)
        payload = value(entry, field) if field else b''
        source_extra = value(entry, 17)
        message = typed_message(payload, source_type, depth=depth + 1, extra=source_extra) if payload else {
            'kind': 'unknown', 'text': '', 'parseStatus': 'unsupported_quote_source', 'links': []}
        sender, stamp, iso, issues, name = _entry_metadata(entry)
        message.update(senderId=sender, senderName=name, timestamp=stamp, time=iso)
        if issues:
            message.update(metadataIssues=issues, contentParseStatus=message['parseStatus'], parseStatus='invalid_metadata')
        token = text(value(value(source_extra, 3045), 1))
        status = ('unsupported_quote_schema' if not entry else 'embedded' if payload and message['parseStatus'] == 'ok'
                  else 'embedded_partial' if payload else 'source_not_found')
        result['quote'] = {'senderId': sender, 'senderName': name, 'message': message,
                           'sourceType': source_type, 'sourceTimestamp': stamp,
                           'sourceToken': token if re.fullmatch(r'[0-9a-fA-F]{32}', token) else '',
                           'sourceIdentifier': quote_identifier(extra, entry),
                           'contentSha256': hashlib.sha256(payload).hexdigest() if payload else '',
                           'resolutionStatus': status}
        if result['kind'] == 'text':
            result['kind'] = 'quote'
        if result['parseStatus'] == 'ok' and status != 'embedded':
            result['parseStatus'] = 'partial_quote'
    return result
