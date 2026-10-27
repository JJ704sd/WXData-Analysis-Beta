"""Resolve referenced Word footnotes, endnotes, and comments."""

import posixpath
import xml.etree.ElementTree as ET


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"

_REFERENCE_TAGS = {
    W + "footnoteReference": ("footnote", "脚注"),
    W + "endnoteReference": ("endnote", "尾注"),
    W + "commentReference": ("comment", "批注"),
}
_NOTE_TAGS = {
    "footnote": W + "footnote",
    "endnote": W + "endnote",
    "comment": W + "comment",
}
_KIND_NAMES = {kind: label for kind, label in _REFERENCE_TAGS.values()}
_RELATION_SUFFIXES = {
    "/footnotes": "footnote",
    "/endnotes": "endnote",
    "/comments": "comment",
}


class WordAnnotations:
    """Collect Word annotation references in first-reference order."""

    MAX_REFERENCES = 1000

    def __init__(self, max_references=MAX_REFERENCES):
        self.max_references = max_references
        self._references = []
        self._reference_keys = set()
        self._reference_limit_reached = False
        self._notice_keys = set()

    def register(self, kind, identifier):
        """Register a known annotation kind without inventing a missing ID."""
        kind = kind.rsplit("Reference", 1)[0] if kind.endswith("Reference") else kind
        if kind not in _KIND_NAMES:
            return None
        identifier = "" if identifier is None else str(identifier).strip()
        if not identifier:
            label = "[{} ID 未标识]".format(_KIND_NAMES[kind])
            return label
        label = "[{} ID {}]".format(_KIND_NAMES[kind], identifier)
        key = (kind, identifier)
        if key not in self._reference_keys:
            if len(self._references) >= self.max_references:
                self._reference_limit_reached = True
            else:
                self._reference_keys.add(key)
                self._references.append((kind, identifier, label))
        return label

    def marker(self, node):
        """Register a Word reference node, returning its visible marker."""
        reference = _REFERENCE_TAGS.get(node.tag)
        if reference is None:
            return None
        return self.register(reference[0], node.get(W + "id"))

    def _notice(self, out, message):
        if message not in self._notice_keys:
            self._notice_keys.add(message)
            out.notices.append(message)

    @staticmethod
    def _rels_name(source_name):
        return posixpath.join(
            posixpath.dirname(source_name),
            "_rels",
            posixpath.basename(source_name) + ".rels",
        )

    def _parts(self, z, read_xml, resolve_target, out):
        grouped = {"footnote": [], "endnote": [], "comment": []}
        declared = {kind: False for kind in grouped}
        rels_name = self._rels_name("word/document.xml")
        names = set(z.namelist())
        if rels_name in names:
            try:
                relationships = read_xml(z, rels_name)
            except (KeyError, ET.ParseError, ValueError):
                self._notice(out, "Word附注关系文件无法读取；正文保留。")
                declared = {kind: True for kind in grouped}
            else:
                for relationship in relationships:
                    if relationship.tag != PR + "Relationship":
                        continue
                    relation_type = relationship.get("Type", "")
                    kind = next((value for suffix, value in _RELATION_SUFFIXES.items()
                                 if relation_type.endswith(suffix)), None)
                    if kind is None:
                        continue
                    declared[kind] = True
                    if relationship.get("TargetMode") == "External":
                        continue
                    target = relationship.get("Target")
                    if not target:
                        continue
                    try:
                        resolved = resolve_target("word", target)
                    except (KeyError, ET.ParseError, ValueError):
                        self._notice(out, "Word{}关系目标无法解析；正文保留。".format(kind))
                        continue
                    if resolved:
                        grouped[kind].append(resolved)
        for kind, canonical in (
            ("footnote", "word/footnotes.xml"),
            ("endnote", "word/endnotes.xml"),
            ("comment", "word/comments.xml"),
        ):
            if not declared[kind] and canonical in names:
                grouped[kind].append(canonical)
        return grouped, names

    def _load_index(self, z, part_name, kind, read_xml, names, out):
        if part_name not in names:
            self._notice(out, "Word{}部件缺失（{}）；正文保留。".format(kind, part_name))
            return {}
        try:
            root = read_xml(z, part_name)
        except (KeyError, ET.ParseError, ValueError):
            self._notice(out, "Word{}部件无法读取（{}）；正文保留。".format(kind, part_name))
            return {}
        index = {}
        for node in root.iter(_NOTE_TAGS[kind]):
            if node.get(W + "type") in ("separator", "continuationSeparator"):
                continue
            identifier = node.get(W + "id")
            if identifier is not None and identifier not in index:
                index[identifier] = node
        return index

    @staticmethod
    def _metadata(kind, node):
        if kind != "comment":
            return ""
        values = []
        if node.get(W + "author"):
            values.append("文件记录作者=" + node.get(W + "author"))
        if node.get(W + "date"):
            values.append("文件记录日期=" + node.get(W + "date"))
        return "；".join(values)

    def referenced(self, z, out, *, read_xml, resolve_target):
        """Yield referenced nodes once; new references may be registered while iterating."""
        if self._reference_limit_reached:
            self._notice(out, "Word附注引用超过{}项，仅提取前{}项；正文保留。".format(
                self.max_references, self.max_references
            ))
        grouped = None
        part_cache = {}
        index = 0
        while index < len(self._references) and not out.truncated:
            kind, identifier, label = self._references[index]
            index += 1
            if grouped is None:
                grouped, names = self._parts(z, read_xml, resolve_target, out)
            found = False
            for part_name in grouped[kind]:
                cache_key = (kind, part_name)
                if cache_key not in part_cache:
                    part_cache[cache_key] = self._load_index(
                        z, part_name, kind, read_xml, names, out
                    )
                node = part_cache[cache_key].get(identifier)
                if node is not None:
                    found = True
                    yield label, part_name, node, self._metadata(kind, node)
                    break
            if not found:
                self._notice(out, label + "目标未找到；正文保留。")
