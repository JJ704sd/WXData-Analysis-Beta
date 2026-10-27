"""Paged local reading and checked attachment paths for the native desktop."""
import hashlib
import json
from pathlib import Path
import re
from collections import Counter

from offline_chat import ArchiveError
from offline_images import image_references, image_state, message_nodes

PAGE_SIZE = 100
OPEN_EXTENSIONS = frozenset(('.pdf', '.csv', '.txt', '.md', '.log', '.json', '.docx', '.xlsx',
                             '.pptx', '.doc', '.xls', '.ppt', '.png', '.jpg', '.jpeg', '.gif',
                             '.webp', '.bmp', '.mp4', '.mov', '.mp3', '.wav', '.m4a', '.ogg'))


def has_media_gap(row, kind=None):
    return any((kind is None or node.get('kind') == kind) and
               (node.get('asset', {}).get('status') in ('pending', 'unavailable') or
                bool(node.get('asset', {}).get('previewOnly')))
               for node, _, _ in message_nodes(row))


class ConversationView:
    """Index JSONL offsets, keeping full source rows out of the widget tree."""
    def __init__(self, directory):
        self.root = Path(directory).resolve()
        self.report = json.loads((self.root / 'manifest.json').read_text(encoding='utf-8'))
        self.index = []
        self.image_index = []
        self.image_counts = Counter()
        self.message_offsets = {}
        self.quote_offsets = set()
        self.search_cache = None
        with (self.root / 'messages.jsonl').open('rb') as stream:
            while True:
                offset = stream.tell()
                line = stream.readline()
                if not line:
                    break
                row = json.loads(line)
                self.index.append((offset, row['kind'], has_media_gap(row), has_media_gap(row, 'image')))
                self.message_offsets[row['id']] = len(self.index) - 1
                if row['kind'] == 'quote' or any(node.get('quote') for node, _, _ in message_nodes(row)):
                    self.quote_offsets.add(offset)
                for number, entry in enumerate(image_references(row)):
                    self.image_index.append((offset, number))
                    self.image_counts[image_state(entry['asset'])[0]] += 1

    def image_page(self, offset=0, page_size=24):
        offset = max(0, min(offset, max(0, len(self.image_index) - 1)))
        images, previous, entries = [], None, []
        with (self.root / 'messages.jsonl').open('rb') as stream:
            for position, number in self.image_index[offset:offset + page_size]:
                if position != previous:
                    stream.seek(position)
                    entries = list(image_references(json.loads(stream.readline())))
                    previous = position
                images.append(entries[number])
        return {'images': images, 'offset': offset, 'total': len(self.image_index),
                'pageSize': page_size, 'counts': dict(self.image_counts), 'viewRoot': str(self.root)}

    def page_for_message(self, message_id):
        if message_id not in self.message_offsets:
            raise ArchiveError('当前导出中未找到这条图片的原始消息。')
        return self.page(self.message_offsets[message_id])

    def page(self, offset=None, kind=None, *, page_size=PAGE_SIZE, query=''):
        if not isinstance(page_size, int) or not 1 <= page_size <= 1000:
            raise ArchiveError('每页条数需为1至1000。')
        query = query.strip().casefold()
        if len(query) > 200:
            raise ArchiveError('消息关键词不能超过200字。')
        matches = None
        if query:
            if self.search_cache is None or self.search_cache[0] != query:
                matches = set()
                with (self.root / 'messages.jsonl').open('rb') as stream:
                    for index, (position, *_rest) in enumerate(self.index):
                        stream.seek(position)
                        row = json.loads(stream.readline())
                        for node, _, _ in message_nodes(row):
                            texts = [node.get(key) or '' for key in ('text', 'senderName', 'senderId', 'time', 'description')]
                            texts += node.get('links', [])
                            texts.append(node.get('attachmentContent', {}).get('text', ''))
                            if any(query in str(value).casefold() for value in texts):
                                matches.add(index)
                                break
                self.search_cache = query, matches
            matches = self.search_cache[1]
        selected = [position for position, value, gap, image_gap in self.index if
                    (gap if kind == 'missing_media' else image_gap if kind == 'missing_images'
                     else value != 'emoji' if kind == 'business' else
                     position in self.quote_offsets if kind == 'quote' else not kind or value == kind)]
        if matches is not None:
            matching_offsets = {self.index[index][0] for index in matches}
            selected = [position for position in selected if position in matching_offsets]
        if offset is None:
            offset = max(0, len(selected) - page_size)
        offset = max(0, min(offset, max(0, len(selected) - 1)))
        messages = []
        with (self.root / 'messages.jsonl').open('rb') as stream:
            for position in selected[offset:offset + page_size]:
                stream.seek(position)
                messages.append(json.loads(stream.readline()))
        return {'messages': messages, 'offset': offset, 'total': len(selected), 'pageSize': page_size,
                'query': query, 'sourceTotal': len(self.index),
                'viewRoot': str(self.root), 'report': self.report}


def attachment_path(root, asset):
    """Only return unchanged files inside this conversation's managed assets."""
    root = Path(root).resolve()
    relative = asset.get('path', '')
    if asset.get('status') != 'ready' or not isinstance(relative, str) or not relative.startswith('assets/'):
        raise ArchiveError('本机没有可打开的附件，请先在微信下载后更新本地记录。')
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root / 'assets') or not path.is_file():
        raise ArchiveError('附件不属于当前会话的本地副本。')
    if path.stat().st_size != asset.get('size') or path.stat().st_size > 100 * 1024 * 1024:
        raise ArchiveError('附件大小发生变化，请更新本地记录。')
    if hashlib.sha256(path.read_bytes()).hexdigest() != asset.get('digest'):
        raise ArchiveError('附件完整性校验失败，请更新本地记录。')
    return path


def document_path(root, asset):
    """Prepare a checked, recognizably named document for the OS default viewer."""
    source = attachment_path(root, asset)
    if source.suffix.lower() not in OPEN_EXTENSIONS:
        raise ArchiveError('此附件没有受支持的直接查看方式，可从导出目录另行处理。')
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', asset.get('filename') or source.name).strip(' .')
    if not name or Path(name).suffix.lower() != source.suffix.lower():
        name = source.name
    if len(name) > 150:
        name = name[:120] + source.suffix.lower()
    root = Path(root).resolve()
    folder = (root / 'opened' / asset['digest']).resolve()
    if not folder.is_relative_to(root / 'opened'):
        raise ArchiveError('附件打开目录无效。')
    folder.mkdir(parents=True, exist_ok=True)
    target = (folder / name).resolve()
    if target.parent != folder:
        raise ArchiveError('附件文件名无效。')
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != asset['digest']:
            raise ArchiveError('附件查看副本已变化，请从导出目录另行处理。')
    else:
        with target.open('xb') as stream:
            stream.write(source.read_bytes())
    return target


def read_image(root, asset, bounds=(760, 520)):
    from PIL import Image, ImageOps
    path = attachment_path(root, asset)
    try:
        with Image.open(path) as source:
            if source.width * source.height > 40_000_000:
                raise ArchiveError('图片像素超过窗口预览上限，可打开原文件查看。')
            image = ImageOps.exif_transpose(source)
            image.thumbnail(bounds, Image.Resampling.LANCZOS)
            return image.convert('RGBA').copy()
    except Image.DecompressionBombError:
        raise ArchiveError('图片像素超过窗口预览上限，可打开原文件查看。') from None
