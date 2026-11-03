"""Script-free local image cards and a complete gallery with source anchors."""
import html
from pathlib import PurePosixPath

from offline_images import image_state, message_anchor

CSS = '''
.local_preview{color:#a15b00;background:#fff3d6}
.gallery-link,.action{display:inline-block;padding:7px 14px;margin:4px 8px 4px 0;background:#e6f1ff;border-radius:7px;color:#1756a2;text-decoration:none}
.image-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:16px;align-items:start}
.image-card{padding:14px;border:1px solid #d8e1eb;background:#fff;border-radius:10px;overflow-wrap:anywhere;margin:12px 0}
.image-grid .image-card{margin:0}.image-card img{display:block;max-width:100%;max-height:420px;object-fit:contain;margin:12px auto}
.image-grid img{height:180px;width:100%;object-fit:contain;background:#edf2f7}
.badge{display:inline-block;font-size:14px;font-weight:700;padding:3px 9px;border-radius:5px}
.original{color:#087c62;background:#e6f6f0}.preview{color:#975700;background:#fff3d7}.missing{color:#b42318;background:#fef0ee}
.not_loaded{color:#64748b;background:#edf2f7}
.image-placeholder{padding:32px 12px;background:#fef0ee;color:#b42318;text-align:center;margin:12px 0}
.image-origin{font-size:12px;color:#64748b;margin:8px 0;white-space:pre-wrap}
article:target{outline:3px solid #448bdb;scroll-margin-top:20px}
.lightbox{display:none;position:fixed;inset:0;background:#122033f5;z-index:5;padding:70px 25px 25px;overflow:auto}
.lightbox:target{display:block}.lightbox img{display:block;margin:auto;max-width:none;max-height:none;width:auto;height:auto;background:white}
.lightbox .close{position:fixed;right:28px;top:18px;color:white;font-size:18px;background:#263c54;padding:8px 18px;border-radius:6px;text-decoration:none}
'''


def image_card(asset, *, entry=None, number=None):
    escape = lambda value: html.escape(str(value or ''), quote=True)
    state, label = image_state(asset)
    card_id = f'image-card-{number}' if number is not None else ''
    content = '<figure class="image-card"' + (f' id="{card_id}"' if card_id else '') + '>'
    content += f'<figcaption><span class="badge {state}">{label}</span></figcaption>'
    relative = asset.get('path') or ''
    path = PurePosixPath(relative)
    safe = (asset.get('status') == 'ready' and path.parts and path.parts[0] == 'assets'
            and '..' not in path.parts and '\\' not in relative and not path.is_absolute())
    if safe:
        href = escape(relative)
        target = f'#image-view-{number}' if number is not None else href
        extra = '' if number is not None else ' target="_blank" rel="noopener"'
        content += f'<a href="{target}"{extra}><img loading="lazy" src="{href}" alt="{label}，点击放大"></a>'
        content += f'<a class="action" href="{target}"{extra}>查看大图</a><a href="{href}" download>保存图片</a>'
        if number is not None:
            content += (f'<div class="lightbox" id="image-view-{number}"><a class="close" href="#{card_id}">关闭大图 ×</a>'
                        f'<img loading="lazy" src="{href}" alt="{label}；按原始尺寸显示，可滚动查看"></div>')
    else:
        placeholder = ('本次未加载媒体<br>请勾选包含附件后重新导出；尚未核对图片是否存在。' if state == 'not_loaded'
                       else '图片未取得<br>本地没有可显示的文件；无法据此判断图片内容。')
        content += '<div class="image-placeholder">' + placeholder + '</div>'
    if asset.get('reason'):
        content += f'<p class="image-origin">{escape(asset["reason"])}</p>'
    if entry:
        origin = entry['senderName'] + ' · ' + (entry['time'] or entry['rootTime']) + '\n' + entry['context']
        if entry['trail']:
            origin += '\n外层：' + entry['rootSenderName'] + ' · ' + entry['rootTime']
        origin += '\n消息出处：' + entry['rootMessageId']
        if entry['part']:
            origin += ' · part ' + entry['part']
        content += '<p class="image-origin">' + escape(origin) + '</p>'
        content += f'<a class="action" href="chat.html#{message_anchor(entry["rootMessageId"])}">定位原消息及上下文</a>'
    return content + '</figure>'


def gallery_body(entries):
    from collections import Counter
    counts = Counter(image_state(entry['asset'])[0] for entry in entries)
    content = ('<p>包含普通消息、嵌套转发和引用中的图片。按引用保留出处，同图多次出现不合并。'
               '点击预览查看原始尺寸；缩略图无法还原为原图。</p>')
    content += (f'<p><span class="badge original">原图 {counts["original"]}</span> '
                f'<span class="badge preview">仅缩略图 {counts["preview"]}</span> '
                f'<span class="badge preview">本地预览 {counts["local_preview"]}</span> '
                f'<span class="badge missing">图片缺失 {counts["missing"]}</span> '
                f'<span class="badge not_loaded">未加载 {counts["not_loaded"]}</span> · 共 {len(entries)} 个图片引用</p>')
    if not entries:
        return content + '<p>当前导出没有图片消息。若导出时未包含媒体，请重新勾选后导出。</p>'
    return content + '<section class="image-grid">' + ''.join(
        image_card(entry['asset'], entry=entry, number=number) for number, entry in enumerate(entries, 1)) + '</section>'
