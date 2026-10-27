"""Bounded OOXML spreadsheet annotations used by the document extractor.

The functions in this module deliberately keep formula and comment semantics
as file evidence.  They do not calculate formulas, infer relative references,
resolve external relationships, or identify an author from a chat identity.
"""
import posixpath
import xml.etree.ElementTree as ET


S = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
REL_COMMENTS = '/comments'
REL_THREADED = 'threadedcomment'


def shared_formulas(sheet_root):
    """Return shared-formula metadata for one worksheet XML root.

    OOXML permits a shared-formula follower to precede its anchor in the XML,
    so the scan records all shared ``si`` entries and fills the first nonempty
    formula text found later in the same scan.  The mapping is intentionally
    per worksheet; the same ``si`` on another worksheet is unrelated.
    """
    shared = {}
    for cell in sheet_root.iter(S + 'c'):
        formula = cell.find(S + 'f')
        if formula is None or formula.get('t') != 'shared':
            continue
        si = formula.get('si')
        if si is None:
            continue
        entry = shared.setdefault(si, ('', '', ''))
        raw = ''.join(formula.itertext())
        if raw and not entry[2]:
            shared[si] = (cell.get('r', ''), formula.get('ref', ''), raw)
    return shared


def _formula_cache_exists(cell):
    value = cell.find(S + 'v')
    if value is None:
        return False
    # A string formula may legitimately cache an empty string as <v/>.  An
    # empty boolean cache carries no value and must not become FALSE.
    if cell.get('t') == 'str':
        return True
    return bool((value.text or '').strip())


def _cache_label(cell):
    return '文件缓存=存在' if _formula_cache_exists(cell) else '文件缓存=缺失'


def formula_annotation(cell, shared):
    """Return the text appended to a cell value for its stored formula.

    A shared formula uses the anchor's original expression verbatim.  When an
    anchor is absent, the gap is described explicitly and no fabricated
    ``=`` expression is emitted.  A normal nonempty formula keeps the legacy
    ``[公式未执行: =...]`` representation.
    """
    formula = cell.find(S + 'f')
    if formula is None:
        return ''
    raw = ''.join(formula.itertext())
    if formula.get('t') != 'shared':
        return '[公式未执行: =' + raw + ']' if raw else ''

    si = formula.get('si', '')
    anchor, ref, shared_raw = shared.get(si, ('', '', ''))
    cache = _cache_label(cell)
    if not shared_raw:
        return ('[公式未执行: 共享公式缺口；共享公式 si=' + si +
                '；锚点未知；原始公式未取得；未展开相对引用；' + cache +
                '；未重新计算]')
    metadata = ('[共享公式 si=' + si + '；锚点=' + (anchor or '未知') +
                '；锚点原式: =' + shared_raw + '；范围=' + (ref or '未记录') +
                '；未展开相对引用；' + cache + '；未重新计算]')
    # Keep the historical marker only for the anchor.  A follower must not
    # look as if its own formula text were independently stored.
    if cell.get('r', '') == anchor:
        return '[公式未执行: =' + shared_raw + '] ' + metadata
    return metadata


def _notice(out, sheet_name, message):
    out.notices.append('工作表 ' + sheet_name + '：' + message)


def _author_name(comment, authors):
    author_id = comment.get('authorId')
    if author_id is None:
        return '作者索引缺失'
    try:
        index = int(author_id)
    except ValueError:
        return '作者未记录（authorId=' + author_id + '）'
    if index < 0 or index >= len(authors):
        return '作者未记录（authorId=' + author_id + '）'
    return authors[index] or '作者记录为空（authorId=' + author_id + '）'


def _comment_body(comment):
    text = comment.find(S + 'text')
    if text is None:
        return ''
    # Only main text under the comment's <text> element is content.  This
    # excludes phonetic/rPh nodes that some producers put beside <text>.
    chunks = []

    def visit(node, phonetic=False):
        phonetic = phonetic or node.tag in (S + 'rPh', S + 'phoneticPr')
        if node.tag == S + 't' and not phonetic:
            chunks.append(node.text or '')
        for child in node:
            visit(child, phonetic)

    visit(text)
    return ''.join(chunks)


def extract_sheet_comments(z, sheet_part, sheet_name, out, *, read_xml, resolve_target):
    """Append classic comments from one worksheet's internal relationships.

    ``read_xml`` receives ``(zip_file, part_name)`` and ``resolve_target``
    receives ``(base_directory, target)``.  Only the explicit parse/read
    boundary errors are converted to notices so unrelated programming errors
    remain visible to the caller.
    """
    rels_name = posixpath.join(
        posixpath.dirname(sheet_part), '_rels', posixpath.basename(sheet_part) + '.rels')
    if rels_name not in z.namelist():
        return

    try:
        relationships = read_xml(z, rels_name)
    except (KeyError, ET.ParseError, ValueError):
        _notice(out, sheet_name, '经典批注关系文件无法读取，保留工作表正文。')
        return

    base = posixpath.dirname(sheet_part)
    threaded_seen = False
    for relation in relationships:
        relation_type = relation.get('Type', '').lower()
        if relation.get('TargetMode', '').lower() == 'external':
            continue
        target = relation.get('Target')
        if not target:
            continue
        if REL_THREADED in relation_type:
            threaded_seen = True
            continue
        if not relation_type.endswith(REL_COMMENTS):
            continue
        try:
            comments_part = resolve_target(base, target)
        except ValueError:
            _notice(out, sheet_name, '经典批注目标路径无效，保留工作表正文。')
            continue
        try:
            comments_root = read_xml(z, comments_part)
        except (KeyError, ET.ParseError, ValueError):
            _notice(out, sheet_name, '经典批注部件缺失或无法读取，保留工作表正文。')
            continue

        authors_root = comments_root.find(S + 'authors')
        authors = [] if authors_root is None else [
            ''.join(author.itertext()) for author in authors_root.findall(S + 'author')
        ]
        comment_list = comments_root.find(S + 'commentList')
        if comment_list is None:
            _notice(out, sheet_name, '经典批注列表缺失，保留工作表正文。')
            continue
        for comment in comment_list.findall(S + 'comment'):
            ref = comment.get('ref') or '单元格引用缺失'
            body = _comment_body(comment)
            text = '作者：' + _author_name(comment, authors) + '\n正文：' + (body or '（空）')
            out.add('工作表 ' + sheet_name + ' 批注 ' + ref, text)
            if out.truncated:
                break
        if out.truncated:
            break

    if threaded_seen:
        _notice(out, sheet_name, '存在 threadedComments；当前未读取线程批注。')
