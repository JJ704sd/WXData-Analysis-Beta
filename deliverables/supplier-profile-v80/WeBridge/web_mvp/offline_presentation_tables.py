"""Extract bounded, physical-cell text from DrawingML presentation tables."""

from offline_documents import MAX_SECTIONS, MAX_TEXT

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _paragraph_text(paragraph):
    """Keep the existing paragraph text semantics, including explicit breaks."""
    return "".join(
        (node.text or "") if node.tag == A + "t" else "\n"
        for node in paragraph.iter()
        if node.tag in (A + "t", A + "br")
    )


def _cell_text(cell):
    paragraphs = [_paragraph_text(node) for node in cell.iter(A + "p")]
    value = "\n".join(paragraphs)
    return value if value.strip() else ""


def _span(cell, attribute, out, label):
    raw = cell.get(attribute)
    if raw is None:
        return 1
    try:
        value = int(raw)
    except ValueError:
        out.notices.append(label + "：" + attribute + " 属性非法，按1读取；单元格文字保留。")
        return 1
    if value < 1:
        out.notices.append(label + "：" + attribute + " 属性必须为正整数，按1读取；单元格文字保留。")
        return 1
    return value


def _cell_value(cell, row_number, column_number, out, label):
    value = _cell_text(cell)
    continuation = cell.get("hMerge") in ("1", "true") or cell.get("vMerge") in ("1", "true")
    if continuation:
        if value:
            value += "（合并延续单元格保存文字）"
        else:
            value = "（合并延续单元格，空）"

    grid_span = _span(cell, "gridSpan", out, label)
    row_span = _span(cell, "rowSpan", out, label)
    if grid_span > 1 or row_span > 1:
        end_row = row_number + row_span - 1
        end_column = column_number + grid_span - 1
        value += ("；" if value else "") + (
            "声明合并范围 R" + str(row_number) + "C" + str(column_number)
            + ":R" + str(end_row) + "C" + str(end_column)
        )
    return "C" + str(column_number) + "=" + (value or "（空）")


def _extract_table(table, out, label, table_number):
    for row_number, row in enumerate(table.findall(A + "tr"), 1):
        cells = []
        column_number = 1
        row_label = label + " 表格 " + str(table_number) + " 行 " + str(row_number)
        for cell in row.findall(A + "tc"):
            cells.append(_cell_value(cell, row_number, column_number, out, row_label))
            # Number XML cells physically.  A declared gridSpan is represented
            # in the token's range annotation and must not create synthetic
            # covered-cell values.
            column_number += 1
        out.add(row_label, "\t".join(cells))
        if out.truncated and (len(out.sections) >= MAX_SECTIONS or out.characters >= MAX_TEXT):
            return


def extract_slide_text(slide_root, out, label):
    """Walk a slide in XML order, treating each table as a bounded unit."""
    table_number = 0

    def walk(node):
        nonlocal table_number
        if node.tag == A + "tbl":
            table_number += 1
            _extract_table(node, out, label, table_number)
            return
        if node.tag == A + "p":
            out.add(label, _paragraph_text(node))
            return
        for child in node:
            walk(child)
            if out.truncated and (len(out.sections) >= MAX_SECTIONS or out.characters >= MAX_TEXT):
                return

    walk(slide_root)
