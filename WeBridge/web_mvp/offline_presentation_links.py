"""Extract referenced DrawingML hyperlinks without following their targets."""

import posixpath

from offline_documents import A, MAX_SECTIONS, MAX_TEXT, R


PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"


def _relationship_part(part_name):
    part_name = part_name.lstrip("/")
    directory, filename = posixpath.split(part_name)
    return posixpath.join(directory, "_rels", filename + ".rels")


def read_presentation_links(z, part_name, read_xml):
    """Read raw relationships for *part_name* without resolving any target."""
    rels_name = _relationship_part(part_name)
    if rels_name not in z.namelist():
        return {}
    root = read_xml(z, rels_name)
    result = {}
    for relationship in root.findall(PR + "Relationship"):
        relation_id = relationship.get("Id")
        if not relation_id:
            continue
        result[relation_id] = {
            "target": relationship.get("Target", ""),
            "type": relationship.get("Type", ""),
            "mode": relationship.get("TargetMode"),
        }
    return result


def _paragraph_text(run):
    return "".join(
        (node.text or "") if node.tag == A + "t" else "\n"
        for node in run.iter()
        if node.tag in (A + "t", A + "br")
    )


def _link_text(node, parents):
    current = parents.get(node)
    while current is not None:
        if current.tag in (A + "r", A + "fld"):
            return _paragraph_text(current)
        current = parents.get(current)
    return ""


def _table_context(node, parents, tables):
    current = parents.get(node)
    cell = None
    row = None
    table = None
    while current is not None:
        if cell is None and current.tag == A + "tc":
            cell = current
        elif row is None and current.tag == A + "tr":
            row = current
        elif table is None and current.tag == A + "tbl":
            table = current
            break
        current = parents.get(current)
    if table is None or row is None or cell is None:
        return None
    table_number = tables[table]
    row_number = [item for item in table if item.tag == A + "tr"].index(row) + 1
    column_number = [item for item in row if item.tag == A + "tc"].index(cell) + 1
    return table_number, row_number, column_number


def _shape_context(node, parents):
    current = parents.get(node)
    while current is not None:
        if current.tag == P + "cNvPr":
            return current.get("name", ""), current.get("id", "")
        current = parents.get(current)
    return None


def _relation_kind(relation):
    target = relation["target"]
    relation_type = relation["type"]
    mode = relation["mode"] or ""
    if mode.lower() == "external":
        return "外部超链接"
    if relation_type.endswith("/slide"):
        return "文档内幻灯片跳转"
    if relation_type.endswith("/hyperlink") and target.lower().startswith(("http://", "https://", "mailto:")):
        return "外部超链接"
    if relation_type.endswith("/hyperlink"):
        return "文档内超链接"
    return "关系链接"


def _location(node, label, parents, tables):
    shape = _shape_context(node, parents)
    if shape is not None:
        name, shape_id = shape
        return label + " 形状 " + (name or "（未命名）") + " id=" + (shape_id or "?")
    table = _table_context(node, parents, tables)
    if table is not None:
        table_number, row_number, column_number = table
        return (label + " 表格 " + str(table_number) + " 行 " + str(row_number)
                + " 列 " + str(column_number))
    return label + " 正文链接"


def _format_link(node, relation, parents):
    action = node.get("action")
    tooltip = node.get("tooltip")
    text = _link_text(node, parents)
    shape = _shape_context(node, parents)
    fields = []
    if relation is None:
        fields.append("动作声明，无关系目标" if action else "关系缺失")
    else:
        fields.append(_relation_kind(relation))
        fields.append("目标=" + relation["target"])
        if relation["type"]:
            fields.append("类型=" + relation["type"])
        if relation["mode"]:
            fields.append("模式=" + relation["mode"])
    if action:
        fields.append("action=" + action)
    if tooltip:
        fields.append("tooltip=" + tooltip)
    if shape is not None:
        name, shape_id = shape
        fields.append("形状=" + (name or "（未命名）") + " id=" + (shape_id or "?"))
    else:
        fields.append("文字=" + (text if text else "（无正文）"))
    return "；".join(fields)


def extract_presentation_links(root, out, label, relationships):
    """Add one bounded section per referenced click or hover link.

    The target is emitted verbatim.  Nothing in this helper opens, parses, or
    normalizes an external URL or an internal package target.
    """
    parents = {child: parent for parent in root.iter() for child in parent}
    tables = {table: index for index, table in enumerate(root.iter(A + "tbl"), 1)}
    links = [node for node in root.iter() if node.tag in (A + "hlinkClick", A + "hlinkHover")]
    for index, node in enumerate(links):
        if len(out.sections) >= MAX_SECTIONS or out.characters >= MAX_TEXT:
            out.truncated = True
            break
        relation_id = node.get(R + "id")
        relation = relationships.get(relation_id) if relation_id else None
        if relation_id and relation is None:
            out.notices.append(label + "：链接关系 " + relation_id + " 缺失，保留正文但未读取目标。")
        if not relation_id and not node.get("action"):
            out.notices.append(label + "：链接缺少关系 ID 和 action，保留正文但未读取目标。")
        kind = "悬停链接" if node.tag == A + "hlinkHover" else "点击链接"
        location = _location(node, label, parents, tables) + " " + kind
        out.add(location, _format_link(node, relation, parents))
        if (len(out.sections) >= MAX_SECTIONS or out.characters >= MAX_TEXT) and index + 1 < len(links):
            out.truncated = True
            break
