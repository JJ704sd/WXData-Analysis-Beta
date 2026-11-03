"""Typed, occurrence-based media counts for host receipts and desktop results."""
from pathlib import PureWindowsPath
import re

from offline_images import image_state, message_nodes

KINDS = {'image': 'images', 'file': 'attachments', 'emoji': 'emoji',
         'video': 'videos', 'voice': 'voice'}


class MediaSummary:
    def __init__(self):
        self.counts = {
            category: dict.fromkeys(('total', 'original', 'thumbnail', 'localPreview', 'missing', 'notLoaded')
                                    if category == 'images' else
                                    ('total', 'ready', 'previewOnly', 'missing', 'notLoaded'), 0)
            for category in KINDS.values()}
        self.attachment_types = {}

    def add(self, row):
        for node, _, _ in message_nodes(row):
            category = KINDS.get(node.get('kind'))
            if not category:
                continue
            asset = node.get('asset') or {}
            state = image_state(asset)[0]
            key = {'preview': 'thumbnail', 'local_preview': 'localPreview', 'not_loaded': 'notLoaded'}.get(state, state)
            if category != 'images':
                key = {'original': 'ready', 'thumbnail': 'previewOnly'}.get(key, key)
            self.counts[category]['total'] += 1
            self.counts[category][key] += 1
            if category == 'attachments':
                name = asset.get('filename') or node.get('media', {}).get('filename') or ''
                extension = PureWindowsPath(str(name)).suffix.lower()
                if not re.fullmatch(r'\.[a-z0-9]{1,12}', extension):
                    extension = 'unknown'
                counts = self.attachment_types.setdefault(extension, dict.fromkeys(self.counts['attachments'], 0))
                counts['total'] += 1
                counts[key] += 1

    def result(self):
        return {'schemaVersion': 1, 'scope': 'ordinary_forward_quote_references',
                'unit': 'reference_occurrence', 'deduplicated': False, **self.counts,
                'attachmentTypes': dict(sorted(self.attachment_types.items()))}


def attachment_type_text(summary):
    types = summary['attachmentTypes']
    if not types:
        return '附件类型（按引用）：无附件。'
    return '附件类型（按引用）：' + '、'.join(
        ('未识别扩展名' if key == 'unknown' else key) + f' {item["total"]}处'
        for key, item in types.items()) + '。各类型按全部引用计数，可用总数见上方附件统计。'


def summary_text(summary):
    image = summary['images']
    parts = [f'图片引用 {image["total"]} 处：原图 {image["original"]}，缩略图 {image["thumbnail"]}，'
             f'本地预览 {image.get("localPreview", 0)}，'
             f'缺失图片 {image["missing"]}，未加载 {image["notLoaded"]}']
    for key, label in (('attachments', '附件'), ('emoji', '表情'), ('videos', '视频'), ('voice', '语音')):
        item = summary[key]
        if key in ('videos', 'voice') and not item['total']:
            continue
        parts.append(f'{label}引用 {item["total"]} 处：可用 {item["ready"]}，仅预览 {item["previewOnly"]}，'
                     f'未取得 {item["missing"]}，未加载 {item["notLoaded"]}')
    return '；'.join(parts) + '。统计普通消息、转发和引用中的出现次数，同一文件可能重复，不是去重后的文件张数。'
