"""Read PDF link annotations without following or executing their targets."""

from pypdf.errors import PyPdfError
from pypdf.generic import (ArrayObject, DictionaryObject,
                           FloatObject, IndirectObject, NameObject, NullObject,
                           NumberObject, TextStringObject)
import re


_READ_ERRORS = (PyPdfError, TypeError, ValueError)


def _resolve(value):
    return value.get_object() if isinstance(value, IndirectObject) else value


def _text(value):
    """Return a scalar PDF value as text, leaving TextResult to normalize it."""
    value = _resolve(value)
    if value is None or isinstance(value, NullObject):
        return None
    if isinstance(value, NameObject):
        return None
    if isinstance(value, (TextStringObject, str)):
        return str(value)
    return None


def _name(value):
    value = _resolve(value)
    if isinstance(value, NameObject):
        return str(value).lstrip('/')
    return None


def _number(value):
    value = _resolve(value)
    if isinstance(value, (NumberObject, FloatObject)):
        return str(value)
    return None


def _integer(value):
    value = _resolve(value)
    return str(value) if isinstance(value, NumberObject) else None


def _page_reference_key(value):
    if isinstance(value, IndirectObject):
        return value.idnum, value.generation
    return None


def _page_map(reader, max_pages):
    """Map only page references evidenced by the reader's page tree."""
    pages = {}
    for index in range(min(len(reader.pages), max_pages)):
        number = index + 1
        page = reader.pages[index]
        reference = getattr(page, 'indirect_reference', None)
        key = _page_reference_key(reference)
        if key is not None:
            pages[key] = number
    return pages


def _destination(reader, page_map, value, max_pages):
    """Render a named or explicit internal destination without guessing page numbers."""
    value = _resolve(value)
    if value is None or isinstance(value, NullObject):
        return '内部目的地: 未提供', None
    if not isinstance(value, ArrayObject):
        name = _text(value)
        if name is None:
            name = _name(value)
        if name is not None:
            return '内部目的地: ' + name, None
        return '内部目的地: 未解析（目标类型未支持）', 'PDF链接内部目的地类型未支持，未展开对象。'
    if not value:
        return '内部目的地: 未解析（目标数组为空）', 'PDF链接内部目的地数组为空。'
    first = value[0]
    page_number = None
    key = _page_reference_key(first)
    if key is not None:
        page_number = page_map.get(key)
    if page_number is None:
        # A direct page object may still be mapped by pypdf itself.  A scalar
        # (especially an integer) is deliberately never treated as a page index.
        try:
            resolved_first = _resolve(first)
            if isinstance(resolved_first, DictionaryObject) and resolved_first.get('/Type') == '/Page':
                page_number = reader.get_page_number(resolved_first)
                if page_number is not None and page_number + 1 <= max_pages:
                    page_number += 1
                else:
                    page_number = None
        except _READ_ERRORS:
            page_number = None
    if page_number is None:
        target = '内部目的地: 页面未定位（未将目标值解释为页码）'
    else:
        target = '内部目的地: 第 ' + str(page_number) + ' 页'
    details = []
    for item in value[1:]:
        resolved = _resolve(item)
        if isinstance(resolved, NullObject):
            rendered = 'null'
        elif isinstance(resolved, NameObject):
            rendered = str(resolved)
        else:
            rendered = _text(resolved)
            if rendered is None:
                rendered = _name(resolved)
            if rendered is None:
                rendered = _number(resolved)
        if rendered is not None:
            details.append(rendered)
    if details:
        target += ' ' + ' '.join(details)
    if page_number is None:
        return target, 'PDF链接内部目的地页面未能通过页面树定位；未猜测数字页码。'
    return target, None


def _root_uri_base(reader):
    try:
        root_uri = _resolve(reader.root_object.get('/URI'))
    except _READ_ERRORS:
        return None
    if not isinstance(root_uri, DictionaryObject):
        return None
    try:
        return _text(root_uri.get('/Base'))
    except _READ_ERRORS:
        return None


def _rect(annotation):
    raw = annotation.get('/Rect')
    if raw is None:
        return '文件坐标 Rect: 未提供（位置缺失；链接地址仍保留）', None
    value = _resolve(raw)
    if not isinstance(value, ArrayObject) or len(value) != 4:
        return '文件坐标 Rect: 未解析（位置缺失；链接地址仍保留）', 'PDF链接Rect不是四元坐标；链接地址仍保留。'
    coordinates = []
    for item in value:
        rendered = _number(item)
        if rendered is None:
            return '文件坐标 Rect: 未解析（位置缺失；链接地址仍保留）', 'PDF链接Rect含不支持的坐标值；链接地址仍保留。'
        coordinates.append(rendered)
    return ('文件坐标 Rect: ' + ', '.join(coordinates) +
            '（PDF文件坐标，非视觉文字位置）'), None


def _budget_add(out, label, text, max_sections, max_text):
    """Apply this extractor's limits while retaining TextResult normalization."""
    if len(out.sections) >= max_sections or out.characters >= max_text:
        out.truncated = True
        return False
    available = max_text - out.characters
    if len(text) > available:
        text = text[:available]
        out.truncated = True
    out.add(label, text)
    return True


def _link_content(reader, page_map, annotation, root_base, page_number, link_number, max_pages):
    lines = []
    notices = []
    action = annotation.get('/A')
    direct_destination = annotation.get('/Dest')
    if action is not None:
        action = _resolve(action)
        if isinstance(action, NullObject):
            action = None
        if action is not None:
            if not isinstance(action, DictionaryObject):
                lines.append('动作类型: 未解析')
                notices.append('PDF第 '+str(page_number)+' 页链接 '+str(link_number)+' 的动作类型未支持。')
            else:
                action_type = _name(action.get('/S'))
                if action_type == 'URI':
                    uri = _text(action.get('/URI'))
                    if uri is None:
                        lines.append('URI: 未提供')
                        notices.append('PDF第 '+str(page_number)+' 页链接 '+str(link_number)+' 的URI未提供。')
                    else:
                        lines.append('URI: ' + uri)
                        if root_base is not None:
                            lines.append('URI基础（根 /URI/Base）: ' + root_base)
                        if not re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', uri):
                            lines.append('URI为相对值，保留原始内容，未联网解析。')
                elif action_type == 'GoTo':
                    target = action.get('/D')
                    if target is None:
                        lines.append('内部目的地: 未提供')
                        notices.append('PDF第 '+str(page_number)+' 页链接 '+str(link_number)+' 的GoTo目标未提供。')
                    else:
                        rendered, notice = _destination(reader, page_map, target, max_pages)
                        lines.append(rendered)
                        if notice:
                            notices.append('PDF第 '+str(page_number)+' 页链接 '+str(link_number)+'：'+notice)
                else:
                    shown = action_type or '未指定'
                    lines.append('动作类型: ' + shown + '（未解析）')
                    notices.append('PDF第 '+str(page_number)+' 页链接 '+str(link_number)+' 的动作类型 '+shown+' 未解析。')
    if direct_destination is not None:
        rendered, notice = _destination(reader, page_map, direct_destination, max_pages)
        lines.append(rendered)
        if notice:
            notices.append('PDF第 '+str(page_number)+' 页链接 '+str(link_number)+'：'+notice)
    if not lines:
        lines.append('链接目标: 未提供')
        notices.append('PDF第 '+str(page_number)+' 页链接 '+str(link_number)+' 未提供URI或内部目的地。')
    contents = annotation.get('/Contents')
    if contents is not None:
        description = _text(contents)
        if description:
            lines.append('链接说明: ' + description)
    rect, notice = _rect(annotation)
    lines.append(rect)
    if notice:
        notices.append('PDF第 '+str(page_number)+' 页链接 '+str(link_number)+'：'+notice)
    return '；'.join(lines), notices


def _comment_rect(annotation):
    raw = annotation.get('/Rect')
    if raw is None:
        return '文件坐标 Rect: 未提供（位置缺失）'
    value = _resolve(raw)
    if not isinstance(value, ArrayObject) or len(value) != 4:
        return '文件坐标 Rect: 未解析（位置缺失）'
    coordinates = []
    for item in value:
        rendered = _number(item)
        if rendered is None:
            return '文件坐标 Rect: 未解析（位置缺失）'
        coordinates.append(rendered)
    return ('文件坐标 Rect: ' + ', '.join(coordinates) +
            '（PDF文件坐标，非视觉文字位置）')


_COMMENT_TYPES = {'Text', 'FreeText', 'Highlight', 'Underline',
                  'Squiggly', 'StrikeOut', 'Caret'}


def _comment_scalar(annotation, key, *, name=False):
    value = annotation.get(key)
    if value is None or isinstance(_resolve(value), NullObject):
        return None
    if name:
        return _name(value) or _text(value)
    return _text(value)


def _comment_flags(annotation):
    value = annotation.get('/F')
    if value is None or isinstance(_resolve(value), NullObject):
        return None, None
    raw = _integer(value)
    if raw is None:
        return None, 'PDF批注F标志值类型未支持，已保留批注正文。'
    flags = int(raw)
    return ('文件声明标志（原始）: F=' + raw +
            '；Invisible=' + ('已声明' if flags & 1 else '未声明') +
            '；Hidden=' + ('已声明' if flags & 2 else '未声明') +
            '；NoView=' + ('已声明' if flags & 32 else '未声明'), None)


def _annotation_ref_key(value):
    key = _page_reference_key(value)
    return ('reference', key) if key is not None else None


def _comment_content(annotation, page_number, link_number, reply_indices):
    """Render stored PDF comment metadata without deriving visible text."""
    lines = []
    notices = []
    contents_value = annotation.get('/Contents')
    contents = _comment_scalar(annotation, '/Contents')
    if contents:
        lines.append('批注内容: ' + contents)
    elif contents is None and contents_value is not None and not isinstance(_resolve(contents_value), NullObject):
        notices.append('PDF第 '+str(page_number)+' 页注释 '+str(link_number)+
                       ' 的Contents类型未支持，未导出批注正文。')
    elif annotation.get('/RC') is not None and not isinstance(_resolve(annotation.get('/RC')), NullObject):
        notices.append('PDF第 '+str(page_number)+' 页注释 '+str(link_number)+
                       ' 只有RC富文本而无Contents，未展开富文本内容。')
    fields = (
        ('作者/T', '/T', False),
        ('修改日期/M', '/M', False),
        ('创建日期/CreationDate', '/CreationDate', False),
        ('主题/Subj', '/Subj', False),
        ('标识/NM', '/NM', False),
        ('状态/State', '/State', True),
        ('状态模型/StateModel', '/StateModel', True),
    )
    for label, key, name in fields:
        value = _comment_scalar(annotation, key, name=name)
        if value is not None:
            lines.append(label + ': ' + value)
    flags, flag_notice = _comment_flags(annotation)
    if flags is not None:
        lines.append(flags)
    if flag_notice:
        notices.append('PDF第 '+str(page_number)+' 页注释 '+str(link_number)+'：'+flag_notice)
    lines.append(_comment_rect(annotation))
    reply = annotation.get('/IRT')
    if reply is not None and not isinstance(reply, NullObject):
        parent_index = reply_indices.get(_annotation_ref_key(reply))
        relation = _comment_scalar(annotation, '/RT', name=True)
        relation = relation or '未声明'
        if parent_index is None:
            lines.append('回复关系: 未定位（仅同页原始注释索引）；RT=' + relation)
        else:
            lines.append('回复关系: 同页注释 ' + str(parent_index) + '；RT=' + relation)
    return '；'.join(lines), notices


def extract_pdf_annotations(reader, out, *, max_pages, max_sections, max_text):
    """Append link and stored text-annotation metadata from available pages.

    Link targets are recorded as source metadata only.  No URI is opened and no
    JavaScript or launch action is evaluated.  Comment contents are stored
    annotation metadata; they are never inferred from page text or highlights.
    """
    total_pages = len(reader.pages)
    if total_pages > max_pages:
        out.truncated = True
    page_map = _page_map(reader, max_pages)
    root_base = _root_uri_base(reader)
    emitted = 0
    checked_entries = 0
    stop = False
    for index in range(min(total_pages, max_pages)):
        if stop or len(out.sections) >= max_sections or out.characters >= max_text:
            out.truncated = True
            break
        page_number = index + 1
        page = reader.pages[index]
        try:
            annotations = page.get('/Annots')
            annotations = _resolve(annotations) if annotations is not None else None
        except _READ_ERRORS:
            out.notices.append('PDF第 '+str(page_number)+' 页链接注释读取失败；页面正文仍保留。')
            continue
        if annotations is None or isinstance(annotations, NullObject):
            continue
        if not isinstance(annotations, ArrayObject):
            if checked_entries >= max_sections:
                out.truncated = True
                break
            checked_entries += 1
            out.notices.append('PDF第 '+str(page_number)+' 页 /Annots 不是数组，链接未读取；页面正文仍保留。')
            continue
        reply_indices = {}
        remaining_entries = max_sections - checked_entries
        for index, raw in enumerate(annotations, 1):
            if index > remaining_entries:
                break
            key = _annotation_ref_key(raw)
            if key is not None:
                reply_indices.setdefault(key, index)
        for link_number, raw in enumerate(annotations, 1):
            if checked_entries >= max_sections:
                out.truncated = True
                stop = True
                break
            checked_entries += 1
            if len(out.sections) >= max_sections or out.characters >= max_text:
                out.truncated = True
                stop = True
                break
            try:
                annotation = _resolve(raw)
            except _READ_ERRORS:
                out.notices.append('PDF第 '+str(page_number)+' 页注释 '+str(link_number)+' 读取失败；页面正文仍保留。')
                continue
            if not isinstance(annotation, DictionaryObject):
                out.notices.append('PDF第 '+str(page_number)+' 页注释 '+str(link_number)+' 不是字典，已跳过；页面正文仍保留。')
                continue
            try:
                subtype = _name(annotation.get('/Subtype'))
            except _READ_ERRORS:
                out.notices.append('PDF第 '+str(page_number)+' 页注释 '+str(link_number)+' 的Subtype读取失败；页面正文仍保留。')
                continue
            if subtype == 'Widget':
                continue
            if subtype == 'Link':
                try:
                    text, notices = _link_content(reader, page_map, annotation, root_base,
                                                  page_number, link_number, max_pages)
                except _READ_ERRORS:
                    out.notices.append('PDF第 '+str(page_number)+' 页链接 '+str(link_number)+' 读取失败；页面正文仍保留。')
                    continue
                label = '第 '+str(page_number)+' 页 链接 '+str(link_number)
            elif subtype in _COMMENT_TYPES:
                try:
                    text, notices = _comment_content(annotation, page_number,
                                                     link_number, reply_indices)
                except _READ_ERRORS:
                    out.notices.append('PDF第 '+str(page_number)+' 页批注 '+str(link_number)+' 读取失败；页面正文仍保留。')
                    continue
                label = 'PDF批注 第 '+str(page_number)+' 页 · 注释 '+str(link_number)+' · '+subtype
            else:
                continue
            if _budget_add(out, label, text,
                           max_sections, max_text):
                out.notices.extend(notices)
                emitted += 1
            else:
                out.notices.extend(notices)
                break
    return emitted
