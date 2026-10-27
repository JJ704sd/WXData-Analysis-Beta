"""Decode message presentation data without exposing XML credentials or URLs."""
import html
import re
import xml.etree.ElementTree as ET

MAX_XML = 4 * 1024 * 1024
_HTTP_URL = re.compile(r'https?://[^\s<>"\x00-\x1f，。！？；：、“”‘’]+', re.I)
_URL_CLOSERS = {')': '(', '］': '［', ']': '[', '｝': '｛', '}': '{',
                '）': '（', '】': '【', '》': '《', '」': '「', '』': '『'}
_URL_TRAILING = set('.,!?;:，。！？；：、)]］}｝）】》」』')


def http_links(text):
    """Extract public HTTP(S) URLs without sentence punctuation."""
    links = []
    position = 0
    while match := _HTTP_URL.search(text or '', position):
        position = match.end()
        link = match.group(0)
        balances = {opener: 0 for opener in _URL_CLOSERS.values()}
        for index, char in enumerate(link):
            if char in balances:
                balances[char] += 1
            elif char in _URL_CLOSERS:
                opener = _URL_CLOSERS[char]
                if balances[opener] == 0:
                    link = link[:index]
                    position = match.start() + index + 1
                    break
                balances[opener] -= 1
        while link and link[-1] in _URL_TRAILING:
            closer = link[-1]
            opener = _URL_CLOSERS.get(closer)
            if opener and link.count(opener) >= link.count(closer):
                break
            link = link[:-1]
        if link:
            links.append(link)
    return links


def xml_root(text):
    if len(text) > MAX_XML or re.search(r'<!\s*(?:DOCTYPE|ENTITY)', text, re.I):
        return None
    try:
        return ET.fromstring(text.strip())
    except (ET.ParseError, ValueError):
        return None


def xml_text(node, path):
    """Read embedded XML in child-element, escaped-text and CDATA forms."""
    child = node.find(path)
    if child is None:
        return ''
    return ''.join(ET.tostring(value, encoding='unicode') for value in child) if len(child) else child.text or ''


def plain(text):
    return re.sub(r'<[^>]{1,1000}>', '', html.unescape(text or '')).strip()


def number(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def strip_sender(text):
    if ':\n' in text and re.fullmatch(r'[A-Za-z0-9_@.-]{1,100}', text.split(':\n', 1)[0]):
        return text.split(':\n', 1)[1]
    return text


def record_content(text, depth=0, prefix=''):
    root = xml_root(text)
    if root is None or depth > 4:
        return {'title': '聊天记录', 'items': [], 'error': '这份聊天记录格式暂时无法解析。'}
    nodes = root.findall('./datalist/dataitem')
    items = []
    for index, node in enumerate(nodes[:200]):
        kind = number(node.get('datatype'))
        path = f'{prefix}.{index}' if prefix else str(index)
        row = {'sender': node.findtext('sourcename') or '未知成员',
               'time': node.findtext('sourcetime') or node.findtext('srcMsgCreateTime') or '',
               'kind': 'text', 'text': node.findtext('datadesc') or node.findtext('datatitle') or ''}
        if kind in (2, 4, 8):
            media_kind = {2: 'image', 4: 'video', 8: 'file'}[kind]
            row.update(kind=media_kind, media={'kind': media_kind, 'part': path,
                'filename': node.findtext('datatitle') or {'image': '图片', 'video': '视频', 'file': '文件'}[media_kind],
                'size': number(node.findtext('datasize')), 'duration': number(node.findtext('duration'))})
        elif kind == 17:
            nested = node.findtext('recordxml') or node.findtext('recorditem')
            if nested:
                row.update(kind='record', record=record_content(nested, depth + 1, path))
            else:
                row['text'] = row['text'] or '嵌套聊天记录暂无完整内容'
        elif kind != 1:
            row['text'] = row['text'] or {3: '语音消息', 5: '分享链接', 6: '位置消息'}.get(kind, '记录附件')
        items.append(row)
    return {'title': root.findtext('title') or '聊天记录', 'description': root.findtext('desc') or '',
            'items': items, 'count': len(nodes), 'truncated': len(nodes) > len(items)}


def parse_content(text, local_type):
    text = strip_sender(text)
    base = int(local_type) & 0xffffffff
    subtype = int(local_type) >> 32
    if base == 1:
        return {'kind': 'text', 'text': text}
    root = xml_root(text)
    if base in (10000, 10002) or (root is not None and root.tag == 'sysmsg'):
        revoke = root.find('.//revokemsg') if root is not None else None
        if revoke is not None:
            return {'kind': 'revoke', 'text': plain(revoke.findtext('content') or revoke.findtext('replacemsg')) or '一条消息已撤回',
                    'revokedServerId': revoke.findtext('newmsgid') or revoke.findtext('msgid') or ''}
        if root is None:
            return {'kind': 'system', 'text': plain(text) or '群系统通知'}
        body = root.findtext('.//content_template/template') or root.findtext('.//content') or root.findtext('.//replacemsg')
        if body:
            for link in root.findall('.//link_list/link'):
                value = '、'.join(n.text or '' for n in link.findall('.//nickname')) or ''.join(link.itertext()).strip()
                body = body.replace('$' + (link.get('name') or '') + '$', value)
        return {'kind': 'system', 'text': plain(body) or plain(' '.join(root.itertext())) or '群系统通知'}
    if base in (3, 43, 47):
        kind = {3: 'image', 43: 'video', 47: 'emoji'}[base]
        node = root.find({3: 'img', 43: 'videomsg', 47: 'emoji'}[base]) if root is not None else None
        attrs = node.attrib if node is not None else {}
        return {'kind': kind, 'text': {'image': '图片', 'video': '视频', 'emoji': '表情'}[kind],
                'media': {'kind': kind, 'filename': {'image': '图片', 'video': '视频', 'emoji': '表情'}[kind],
                          'size': number(attrs.get('length') or attrs.get('tplength') or attrs.get('length')),
                          'duration': number(attrs.get('playlength')), 'width': number(attrs.get('tpwidth')),
                          'height': number(attrs.get('tpheight'))}}
    if base == 49 and root is not None:
        app = root.find('appmsg')
        if app is None:
            app = root
        kind = number(app.findtext('type')) or subtype
        title = app.findtext('title') or ''
        if kind == 19:
            record = record_content(app.findtext('recorditem') or '')
            return {'kind': 'record', 'text': title or '聊天记录', 'record': record}
        if kind == 6:
            return {'kind': 'file', 'text': title or '文件', 'media': {'kind': 'file', 'filename': title or '文件',
                'size': number(app.findtext('appattach/totallen')), 'extension': app.findtext('appattach/fileext') or ''}}
        if kind == 57:
            refer = app.find('refermsg')
            quote = {}
            if refer is not None:
                quoted = parse_content(refer.findtext('content') or '', number(refer.findtext('type')))
                quote = {'sender': refer.findtext('displayname') or '引用消息', 'text': quoted['text']}
            return {'kind': 'text', 'text': title, 'quote': quote}
        return {'kind': 'card', 'text': title or '分享消息', 'description': app.findtext('des') or '', 'appType': kind}
    return {'kind': 'system' if base == 0 else 'unsupported',
            'text': {34: '语音消息（暂未解码）', 42: '联系人名片', 48: '位置消息'}.get(base, f'暂未支持的消息类型（{base}）')}
