"""Loss-aware interpretation for local archives; source columns stay in JSONL.

No URL is fetched. Display names and textual @ tokens are never identity proof.
"""
import re

from database_adapter import DatabaseAdapter, _decode
from rich_content import http_links, number, plain, strip_sender, xml_root, xml_text as _node_text

MAX_DEPTH = 10
MAX_RECORD_ITEMS = 10000


def body_text(item):
    attempts = []
    for field in ('compress_content', 'message_content'):
        value = item.get(field)
        if value in (None, '', b''):
            continue
        text, status = _decode(value)
        attempts.append({'field': field, 'status': status})
        if status == 'ok':
            return text, status, attempts
    if not attempts:
        return '', 'ok', []
    return '', attempts[0]['status'], attempts


def _fields(node):
    if node is None:
        return {}
    return {'attributes': dict(node.attrib), 'fields': {
        child.tag: child.text or '' for child in node if len(child) == 0}}


def _record(text, depth, prefix):
    root = xml_root(text)
    if root is None or depth > MAX_DEPTH:
        return {'items': [], 'status': 'invalid_xml' if root is None else 'depth_limit'}
    nodes = root.findall('./datalist/dataitem')
    items = []
    for index, node in enumerate(nodes[:MAX_RECORD_ITEMS]):
        datatype = number(node.get('datatype'))
        part = f'{prefix}.{index}' if prefix else str(index)
        row = {'part': part, 'dataType': datatype, 'dataId': node.get('dataid', ''),
               'senderName': node.findtext('sourcename') or '',
               'senderId': node.findtext('sourceusername') or node.findtext('fromusr') or '',
               'time': node.findtext('sourcetime') or node.findtext('srcMsgCreateTime') or '',
               'text': node.findtext('datadesc') or node.findtext('datatitle') or '',
               'metadata': _fields(node)}
        if datatype == 5:
            row['title'] = node.findtext('datatitle') or ''
            row['description'] = node.findtext('datadesc') or ''
        row['kind'] = {1: 'text', 2: 'image', 3: 'voice', 4: 'video', 5: 'link',
                       6: 'location', 8: 'file', 17: 'record'}.get(datatype, 'unknown')
        row['links'] = [value for value in (node.findtext('weburl'), node.findtext('url')) if value]
        if row['kind'] in ('image', 'video', 'file', 'voice'):
            row['media'] = {'filename': node.findtext('datatitle') or row['kind'],
                            'md5': node.findtext('fullmd5') or node.findtext('md5') or '',
                            'size': number(node.findtext('datasize')),
                            'duration': number(node.findtext('duration'))}
        if datatype == 17:
            row['record'] = _record(_node_text(node, 'recordxml') or _node_text(node, 'recorditem'), depth + 1, part)
        items.append(row)
    return {'title': root.findtext('title') or '', 'description': root.findtext('desc') or '',
            'items': items, 'count': len(nodes),
            'status': 'item_limit' if len(nodes) > MAX_RECORD_ITEMS else 'ok'}


def interpret(text, local_type, depth=0):
    base, subtype = local_type & 0xffffffff, local_type >> 32
    result = {'kind': 'unknown', 'text': text, 'baseType': base,
              'subType': subtype, 'links': [], 'parseStatus': 'ok'}
    if depth > MAX_DEPTH:
        return {**result, 'text': '', 'parseStatus': 'depth_limit'}
    if base == 1:
        result.update(kind='text', links=http_links(text))
        return result
    root = xml_root(text)
    if root is None:
        return {**result, 'kind': 'system' if base in (10000, 10002) else 'unknown',
                'parseStatus': 'plain_system' if base in (10000, 10002) else 'invalid_or_non_xml'}
    if root.tag == 'sysmsg' or base in (10000, 10002):
        revoke = root.find('.//revokemsg')
        result.update(kind='revoke' if revoke is not None else 'system',
                      text=' '.join(root.itertext()).strip(), metadata=_fields(root))
        if revoke is not None:
            for field in ('replacemsg', 'content'):
                notice = plain(revoke.findtext(field) or '')
                if notice:
                    break
            else:
                notice = '一条消息已撤回'
            result['text'] = notice
            result['revokedServerId'] = revoke.findtext('newmsgid') or revoke.findtext('msgid') or ''
        return result
    elements = {3: ('image', 'img'), 34: ('voice', 'voicemsg'),
                43: ('video', 'videomsg'), 62: ('video', 'videomsg'),
                47: ('emoji', 'emoji'), 48: ('location', 'location'),
                42: ('contact', 'msg'), 50: ('call', 'voipmsg')}
    if base in elements:
        kind, tag = elements[base]
        node = root if root.tag == tag else root.find('.//' + tag)
        result.update(kind=kind, text=kind, metadata=_fields(node))
        attrs = node.attrib if node is not None else {}
        if kind == 'location':
            result['location'] = {key: attrs.get(key, '') for key in ('x', 'y', 'label', 'poiname')}
            result['text'] = ' · '.join(dict.fromkeys(value for value in
                (attrs.get('label'), attrs.get('poiname')) if value)) or '位置'
        elif kind == 'contact':
            result['contact'] = {key: attrs.get(key, '') for key in ('username', 'nickname', 'alias')}
            result['text'] = (result['contact']['nickname'] or result['contact']['alias'] or
                              result['contact']['username'] or '名片')
        if kind in ('image', 'video', 'emoji', 'voice'):
            attrs = node.attrib if node is not None else {}
            result['media'] = {'filename': kind, 'md5': attrs.get('md5', ''),
                               'size': number(attrs.get('length') or attrs.get('lengthsize')),
                               'duration': number(attrs.get('playlength') or attrs.get('voicelength')),
                               'attributes': dict(attrs)}
        return result
    if base == 49:
        app = root if root.tag == 'appmsg' else root.find('appmsg')
        if app is None:
            return {**result, 'parseStatus': 'missing_appmsg'}
        app_type = number(app.findtext('type')) or subtype
        title = app.findtext('title') or ''
        result.update(appType=app_type, text=title, description=app.findtext('des') or '',
                      metadata=_fields(app), kind={3: 'music', 4: 'video_card', 5: 'link',
                      6: 'file', 19: 'record', 33: 'miniprogram', 36: 'miniprogram',
                      51: 'finder', 57: 'quote', 63: 'finder',
                      2000: 'transfer', 2001: 'redpacket'}.get(app_type, 'card'))
        result['links'] = [value for value in (app.findtext('url'), app.findtext('lowurl')) if value]
        if app_type in (33, 36):
            info = app.find('weappinfo')
            result['miniProgram'] = {
                'appId': info.findtext('appid', '') if info is not None else '',
                'username': info.findtext('username', '') if info is not None else '',
                'pagePath': info.findtext('pagepath', '') if info is not None else ''}
        if app_type == 6:
            result['media'] = {'filename': title or 'file', 'md5': app.findtext('md5') or app.findtext('appattach/md5') or '',
                               'size': number(app.findtext('appattach/totallen')),
                               'extension': app.findtext('appattach/fileext') or ''}
        elif app_type == 19:
            result['record'] = _record(_node_text(app, 'recorditem'), depth + 1, '')
        elif app_type == 57:
            refer = app.find('refermsg')
            if refer is not None:
                result['quote'] = {'senderId': refer.findtext('chatusr') or refer.findtext('fromusr') or '',
                    'senderName': refer.findtext('displayname') or '', 'serverId': refer.findtext('svrid') or '',
                    'message': interpret(_node_text(refer, 'content'), number(refer.findtext('type')), depth + 1)}
        return result
    return {**result, 'parseStatus': 'unknown_type', 'metadata': _fields(root)}


def mentions(item, raw_text):
    ids, status = DatabaseAdapter._mentions(item)
    body = xml_root(strip_sender(raw_text))
    if body is not None:
        source = body.find('.//msgsource')
        if source is not None:
            import xml.etree.ElementTree as ET
            extra, extra_status = DatabaseAdapter._mentions({'source': ET.tostring(source, encoding='unicode')})
            ids = sorted(set(ids) | set(extra))
            if status != 'invalid':
                status = extra_status if extra_status == 'invalid' else ('structured' if ids else 'none')
    return {'ids': ids, 'everyone': 'notify@all' in ids, 'status': status,
            'displayTokens': re.findall(r'@[^\s@<>]{1,100}', strip_sender(raw_text)) if int(item['local_type']) & 0xffffffff == 1 else []}
