"""Image references with source context, shared by desktop and offline HTML."""
import hashlib


def quote_link_text(quote):
    status = quote.get('sourceLinkStatus')
    if not status:
        return ''
    labels = {'matched_local_source': '已精确关联当前群的本地原消息',
              'source_id_unavailable': '缺少可验证的来源标识',
              'source_not_found': '当前群副本中未找到可验证的来源',
              'ambiguous_source': '多个候选来源，未自动关联', 'cyclic_source': '循环引用，已停止关联'}
    line = '引用本地来源：' + labels.get(status, '来源尚未确认')
    if quote.get('resolutionStatus') == 'embedded':
        line += '；内嵌原文已保留'
    elif quote.get('resolutionStatus') == 'embedded_partial':
        line += '；内嵌原文仅部分适配'
    return line


def image_state(asset):
    if not asset:
        return 'not_loaded', '未加载媒体 · 尚未核对缓存'
    if asset.get('status') == 'ready':
        if asset.get('previewOnly') and asset.get('previewVariant') == 'local_converted':
            return 'local_preview', '本地预览 · 原图未取得'
        return ('preview', '仅缩略图 · 原图未取得') if asset.get('previewOnly') else ('original', '原图可查看')
    return 'missing', '图片缺失 · 影响判断时需补充'


def message_anchor(message_id):
    return 'msg-' + hashlib.sha256(str(message_id).encode('utf-8')).hexdigest()[:24]


def message_nodes(root):
    """Walk ordinary, forwarded and quoted occurrences in the same reading scope."""
    def walk(node, trail=(), node_path=()):
        yield node, trail, node_path
        for index, child in enumerate(node.get('record', {}).get('items', []), 1):
            yield from walk(child, (*trail, f'转发第 {index} 条'), (*node_path, ('record', index - 1)))
        if node.get('quote', {}).get('message'):
            yield from walk(node['quote']['message'], (*trail, '引用来源'), (*node_path, ('quote', 0)))
    yield from walk(root)


def image_references(root):
    """Retain reference occurrences, including quoted/forwarded images; no dedup by file."""
    for node, trail, node_path in message_nodes(root):
        if node.get('kind') == 'image':
            yield {'asset': node.get('asset', {}), 'senderName': node.get('senderName') or '身份未知',
                   'time': node.get('time') or '', 'part': node.get('part') or '',
                   'rootMessageId': root.get('id', ''), 'rootTime': root.get('time') or '',
                   'rootSenderName': root.get('senderName') or '身份未知',
                   'provenance': root.get('provenance', {}), 'trail': list(trail), 'nodePath': list(node_path),
                   'context': ' / '.join(trail) if trail else '普通消息'}
