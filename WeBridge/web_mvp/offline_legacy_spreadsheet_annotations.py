"""Extract hyperlink and note metadata from an already parsed legacy XLS sheet.

The BIFF parser in :mod:`xlrd` has the source material needed for these
annotations, but the ordinary cell scan does not include it.  This module
keeps the metadata bounded by the same ``TextResult`` writer used for cell
text.  It records targets as file evidence and never opens or resolves them.
"""

from xlrd.formula import colname


def _cell_ref(rowx, colx):
    return colname(colx) + str(rowx + 1)


def _range_ref(item):
    first = _cell_ref(item.frowx, item.fcolx)
    last = _cell_ref(item.lrowx, item.lcolx)
    return first if first == last else first + ':' + last


def _annotated_ref(visibility, ref, rowx, colx, *, single_cell):
    if not single_cell:
        return ref
    hidden_row, default_hidden = visibility._hidden_row(rowx + 1)
    hidden_column = visibility._hidden_column(colx + 1)
    markers = []
    if hidden_row:
        markers.append('默认隐藏行' if default_hidden else '隐藏行')
    if hidden_column:
        markers.append('隐藏列')
    return ref + (' [' + '；'.join(markers) + ']' if markers else '')


def _add_hyperlink(visibility, out, item):
    ref = _range_ref(item)
    location = _annotated_ref(visibility, ref, item.frowx, item.fcolx,
                              single_cell=item.frowx == item.lrowx and item.fcolx == item.lcolx)
    target = item.url_or_path if item.url_or_path is not None else '（未提供）'
    lines = ['类型：' + item.type, '目标：' + str(target)]
    if item.desc:
        lines.append('显示文字：' + item.desc)
    if item.textmark:
        lines.append('文本标记：' + item.textmark)
    if item.quicktip:
        lines.append('悬停提示：' + item.quicktip)
    out.add('工作表 ' + visibility.sheet_name + ' 超链接 ' + location,
            '\n'.join(lines))


def _add_note(visibility, out, rowx, colx, note):
    ref = _cell_ref(rowx, colx)
    location = _annotated_ref(visibility, ref, rowx, colx, single_cell=True)
    author = note.author or '（空）'
    text = note.text or '（空）'
    out.add('工作表 ' + visibility.sheet_name + ' 批注 ' + location,
            '作者：' + author + '\n正文：' + text)


def extract_xls_annotations(sheet, visibility, out):
    """Append BIFF hyperlinks and notes from ``sheet`` to ``out``.

    Hyperlink rectangles remain ranges instead of being expanded to every
    covered cell.  The caller owns the sheet's ordinary cell and merge scan;
    this function only consumes xlrd's parsed annotation collections.
    """
    for item in sheet.hyperlink_list:
        _add_hyperlink(visibility, out, item)
        if out.truncated:
            return
    for (rowx, colx), note in sorted(sheet.cell_note_map.items()):
        _add_note(visibility, out, rowx, colx, note)
        if out.truncated:
            return
