"""Read stored PDF AcroForm values without executing form behavior."""

from pypdf.errors import PyPdfError
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject, NullObject


_PASSWORD_FLAG = 1 << 13
_PUSHBUTTON_FLAG = 1 << 16


def _resolve(value):
    return value.get_object() if isinstance(value, IndirectObject) else value


def _scalar(value):
    value = _resolve(value)
    if value is None or isinstance(value, NullObject):
        return None
    if isinstance(value, NameObject):
        return str(value).lstrip('/')
    if isinstance(value, str) or isinstance(value, (int, float, bool)):
        return str(value)
    return None


def _inherited(field, key):
    return field.indirect_reference.get_object().get_inherited(key, None)


def _has_value(value):
    value = _resolve(value)
    if value is None or isinstance(value, NullObject):
        return False
    if isinstance(value, ArrayObject):
        return bool(value)
    return not isinstance(value, str) or bool(value)


def _has_named_kids(field):
    kids = field.indirect_reference.get_object().get('/Kids')
    return isinstance(kids, ArrayObject) and any(
        (_resolve(kid).get('/T') is not None or _resolve(kid).get('/TM') is not None)
        for kid in kids
    )


def _pages(reader, field):
    result = []
    for page in reader.get_pages_showing_field(field):
        number = reader.get_page_number(page)
        if number is not None and number + 1 not in result:
            result.append(number + 1)
    return result


def _location(name, pages, max_pages):
    visible = [page for page in pages if page <= max_pages]
    if not visible:
        return 'PDF表单字段（未定位页） · ' + name
    return 'PDF表单 第 ' + '、'.join(map(str, visible)) + ' 页 · ' + name


def _options(value):
    result = {}
    if not isinstance(value, ArrayObject):
        return result
    for option in value:
        option = _resolve(option)
        if isinstance(option, ArrayObject) and len(option) >= 2:
            code = _scalar(option[0]); label = _scalar(option[1])
            if code is not None and label is not None:
                result[code] = label
        else:
            label = _scalar(option)
            if label is not None:
                result[label] = label
    return result


def _choice(value, options):
    value = _resolve(value)
    values = value if isinstance(value, ArrayObject) else ArrayObject([value])
    mapping = _options(options)
    rendered = []
    for item in values:
        code = _scalar(item)
        if code is None:
            return None
        label = mapping.get(code, code)
        rendered.append(label if label == code else label + '（原值: ' + code + '）')
    return '、'.join(rendered)


def _title(name, field):
    label = _scalar(_inherited(field, '/TU'))
    return name if not label else label + '（字段: ' + name + '）'


def _append(out, name, title, field_type, value, options, flags, pages, max_pages):
    location = _location(name, pages, max_pages)
    if field_type == 'Tx':
        rendered = _scalar(value)
        if rendered is None:
            out.notices.append('PDF表单字段 '+name+' 的值类型未支持，未展开对象。')
        else:
            out.add(location, title + ': ' + rendered)
    elif field_type == 'Ch':
        rendered = _choice(value, options)
        if rendered is None:
            out.notices.append('PDF表单字段 '+name+' 的选项值类型未支持，未展开对象。')
        else:
            out.add(location, title + ': ' + rendered)
    elif field_type == 'Btn':
        if flags & _PUSHBUTTON_FLAG:
            return
        code = _scalar(value)
        if code is None:
            out.notices.append('PDF表单按钮 '+name+' 的状态类型未支持，未展开对象。')
        else:
            out.add(location, title + ': ' + ('未选（Off）' if code == 'Off' else '已选（'+code+'）'))
    else:
        out.notices.append('PDF表单字段 '+name+' 的类型暂未支持，未展开其值对象。')


def extract_pdf_forms(reader, out, *, max_pages, max_sections, max_text):
    """Append stored values once per terminal field, bounded by output limits."""
    acroform = _resolve(reader.root_object.get('/AcroForm'))
    if acroform is None or isinstance(acroform, NullObject):
        return 0
    if not isinstance(acroform, DictionaryObject):
        out.notices.append('PDF交互表单结构未能完整读取；页面正文仍保留。')
        return 0
    if acroform.get('/XFA') is not None:
        out.notices.append('PDF包含XFA动态表单数据；未执行脚本或计算，字段值可能不完整。')
    try:
        fields = reader.get_fields()
    except (PyPdfError, TypeError, ValueError):
        out.notices.append('PDF交互表单结构未能完整读取；页面正文仍保留。')
        return 0
    if not fields:
        return 0
    emitted = 0
    for number, (key, field) in enumerate(fields.items(), 1):
        if number > max_sections or len(out.sections) >= max_sections or out.characters >= max_text:
            out.truncated = True
            break
        if _has_named_kids(field):
            continue
        value = _inherited(field, '/V')
        if not _has_value(value):
            continue
        name = key or _scalar(_inherited(field, '/T')) or '未命名字段'
        field_type = _scalar(_inherited(field, '/FT'))
        try:
            flags = int(_inherited(field, '/Ff') or 0)
        except (TypeError, ValueError):
            out.notices.append('PDF表单字段 '+name+' 的标志值未支持，未展开该字段。')
            continue
        try:
            pages = _pages(reader, field)
        except (PyPdfError, ValueError):
            pages = []
            out.notices.append('PDF表单字段 '+name+' 的页码未能定位。')
        if pages and any(page > max_pages for page in pages):
            out.truncated = True
        if pages and not any(page <= max_pages for page in pages):
            continue
        title = _title(name, field)
        if field_type == 'Tx' and flags & _PASSWORD_FLAG:
            out.add(_location(name, pages, max_pages), title+'：密码字段已填写，值未导出。')
        elif field_type == 'Sig':
            out.notices.append('PDF签名字段 '+name+' 存在；未导出签名值，也未验证签名有效性。')
        else:
            _append(out, name, title, field_type, value, _inherited(field, '/Opt'), flags, pages, max_pages)
        emitted += 1
    return emitted


def sort_pdf_sections(sections):
    """Order page sections while keeping unlocated form metadata last."""
    import re
    def key(section):
        match = re.match(r'^(?:PDF表单 |PDF批注 )?第 (\d+)(?:、\d+)* 页(?:$|[ ·（])', section.get('location', ''))
        return (int(match.group(1)) if match else 10**9, 0 if match else 1)
    sections.sort(key=key)
