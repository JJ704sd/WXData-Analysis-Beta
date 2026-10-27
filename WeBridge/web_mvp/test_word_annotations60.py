import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from offline_documents import extract_document
from offline_word_annotations import WordAnnotations


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"

FOOTNOTE_NEEDLE = "FOOTNOTE_NEEDLE"
ENDNOTE_NEEDLE = "ENDNOTE_NEEDLE"
WORD_COMMENT_NEEDLE = "WORD_COMMENT_NEEDLE"
COMMENT_NEEDLE = WORD_COMMENT_NEEDLE


def _p(text):
    return f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>'


def _reference(kind, identifier):
    return f'<w:r><w:{kind} w:id="{identifier}"/></w:r>'


def docx_file(path, *, custom_parts=False, malformed_annotation=False):
    """Write a real OOXML fixture for integration and parser tests."""
    footnote_part = "word/notes/custom-footnotes.xml" if custom_parts else "word/footnotes.xml"
    endnote_part = "word/notes/custom-endnotes.xml" if custom_parts else "word/endnotes.xml"
    comment_part = "word/notes/custom-comments.xml" if custom_parts else "word/comments.xml"
    document = f'''<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="{W}" xmlns:r="{R}"><w:body>
  <w:p><w:r><w:t>正文前</w:t></w:r>{_reference('footnoteReference', '2')}{_reference('footnoteReference', '2')}{_reference('endnoteReference', '3')}{_reference('commentReference', '0')}{_reference('footnoteReference', '404')}</w:p>
  <w:sdt><w:sdtContent><w:p><w:r><w:t>控件前</w:t></w:r>{_reference('footnoteReference', '2')}</w:p></w:sdtContent></w:sdt>
  <w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格前</w:t></w:r>{_reference('endnoteReference', '3')}</w:p></w:tc></w:tr></w:tbl>
  <w:del><w:p><w:r><w:t>已删除正文</w:t></w:r>{_reference('footnoteReference', '77')}</w:p></w:del>
  <w:p>{_reference('footnoteReference', '')}<w:r><w:t>正文后</w:t></w:r></w:p>
  <w:sectPr/></w:body></w:document>'''
    footnotes = f'''<w:footnotes xmlns:w="{W}">
      <w:footnote w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>
      <w:footnote w:id="0"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>
      <w:footnote w:id="2">{_p(FOOTNOTE_NEEDLE)}</w:footnote>
      <w:footnote w:id="77">{_p("DELETED_NOTE_CANARY")}</w:footnote>
      <w:footnote w:id="9">{_p("UNREFERENCED_NOTE_CANARY")}</w:footnote>
    </w:footnotes>'''
    endnotes = f'''<w:endnotes xmlns:w="{W}">
      <w:endnote w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:endnote>
      <w:endnote w:id="3">{_p(ENDNOTE_NEEDLE)}</w:endnote>
      <w:endnote w:id="8">{_p("UNREFERENCED_NOTE_CANARY")}</w:endnote>
    </w:endnotes>'''
    comments = f'''<w:comments xmlns:w="{W}">
      <w:comment w:id="0" w:author="审核者" w:date="2026-10-25T10:20:00Z">{_p(COMMENT_NEEDLE)}</w:comment>
    </w:comments>'''
    if malformed_annotation:
        comments = "<w:comments xmlns:w=\"" + W + "\"><w:comment>"
    files = {
        "word/document.xml": document,
        footnote_part: footnotes,
        endnote_part: endnotes,
        comment_part: comments,
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
    }
    if custom_parts:
        files["word/_rels/document.xml.rels"] = f'''<Relationships xmlns="{PR}">
          <Relationship Id="rFoot" Type="{R}/footnotes" Target="notes/custom-footnotes.xml"/>
          <Relationship Id="rEnd" Type="{R}/endnotes" Target="notes/custom-endnotes.xml"/>
          <Relationship Id="rCom" Type="{R}/comments" Target="notes/custom-comments.xml"/>
        </Relationships>'''
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


class WordAnnotations60Tests(unittest.TestCase):
    def test_marker_registers_only_reference_nodes_and_preserves_id_labels(self):
        annotations = WordAnnotations()
        root = ET.fromstring(f'<w:p xmlns:w="{W}">{_reference("footnoteReference", "42")}</w:p>')
        reference = next(iter(next(iter(root))))
        self.assertEqual(annotations.marker(reference), "[脚注 ID 42]")
        self.assertEqual(annotations.marker(reference), "[脚注 ID 42]")
        self.assertIsNone(annotations.marker(root))
        missing = ET.fromstring(f'<w:footnoteReference xmlns:w="{W}"/>')
        self.assertEqual(annotations.marker(missing), "[脚注 ID 未标识]")
        whitespace = ET.fromstring(f'<w:footnoteReference xmlns:w="{W}" w:id="   "/>' )
        self.assertEqual(annotations.marker(whitespace), "[脚注 ID 未标识]")

    def test_real_docx_extracts_referenced_annotations_and_skips_orphans(self):
        with tempfile.TemporaryDirectory(prefix="word-annotations60-") as folder:
            path = docx_file(Path(folder) / "annotations.docx")
            result = extract_document(path, ".docx")
        text = result["text"]
        self.assertIn("正文前", text)
        self.assertIn("控件前", text)
        self.assertIn("表格前", text)
        self.assertIn(FOOTNOTE_NEEDLE, text)
        self.assertIn(ENDNOTE_NEEDLE, text)
        self.assertIn(COMMENT_NEEDLE, text)
        self.assertIn("[脚注 ID 2]", text)
        self.assertIn("[尾注 ID 3]", text)
        self.assertIn("[批注 ID 0]", text)
        self.assertIn("文件记录作者=审核者", text)
        self.assertIn("文件记录日期=2026-10-25T10:20:00Z", text)
        self.assertIn("[脚注 ID 404]目标未找到", "\n".join(result["notices"]))
        self.assertNotIn("UNREFERENCED_NOTE_CANARY", text)
        self.assertNotIn("DELETED_NOTE_CANARY", text)
        self.assertNotIn("已删除正文", text)
        self.assertEqual(text.count(FOOTNOTE_NEEDLE), 1)

    def test_custom_parts_and_canonical_parts_are_both_supported(self):
        with tempfile.TemporaryDirectory(prefix="word-annotations60-") as folder:
            custom = docx_file(Path(folder) / "custom.docx", custom_parts=True)
            canonical = docx_file(Path(folder) / "canonical.docx")
            custom_text = extract_document(custom, ".docx")["text"]
            canonical_text = extract_document(canonical, ".docx")["text"]
        self.assertIn(FOOTNOTE_NEEDLE, custom_text)
        self.assertIn(ENDNOTE_NEEDLE, custom_text)
        self.assertIn(COMMENT_NEEDLE, custom_text)
        self.assertIn(FOOTNOTE_NEEDLE, canonical_text)
        self.assertIn(ENDNOTE_NEEDLE, canonical_text)
        self.assertIn(COMMENT_NEEDLE, canonical_text)

    def test_missing_annotation_xml_keeps_body_and_reports_boundary(self):
        with tempfile.TemporaryDirectory(prefix="word-annotations60-") as folder:
            path = docx_file(Path(folder) / "missing.docx")
            with zipfile.ZipFile(path) as archive:
                files = {name: archive.read(name) for name in archive.namelist()}
            del files["word/footnotes.xml"]
            with zipfile.ZipFile(path, "w") as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
            result = extract_document(path, ".docx")
        self.assertIn("正文前", result["text"])
        self.assertIn("正文保留", "\n".join(result["notices"]))

    def test_malformed_annotation_xml_keeps_body_and_referenced_is_incremental(self):
        with tempfile.TemporaryDirectory(prefix="word-annotations60-") as folder:
            path = docx_file(Path(folder) / "malformed.docx", malformed_annotation=True)
            result = extract_document(path, ".docx")
        self.assertIn("正文前", result["text"])
        self.assertIn("无法读取", "\n".join(result["notices"]))

        annotations = WordAnnotations()
        first = ET.fromstring(f'<w:footnoteReference xmlns:w="{W}" w:id="1"/>')
        second = ET.fromstring(f'<w:endnoteReference xmlns:w="{W}" w:id="2"/>')
        self.assertEqual(annotations.marker(first), "[脚注 ID 1]")
        with tempfile.TemporaryDirectory(prefix="word-annotations60-") as folder:
            path = Path(folder) / "incremental.docx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/footnotes.xml", f'<w:footnotes xmlns:w="{W}"><w:footnote w:id="1">{_p("one")}</w:footnote></w:footnotes>')
                archive.writestr("word/endnotes.xml", f'<w:endnotes xmlns:w="{W}"><w:endnote w:id="2">{_p("two")}</w:endnote></w:endnotes>')
            out = type("Out", (), {"notices": [], "truncated": False})()
            with zipfile.ZipFile(path) as package:
                iterator = annotations.referenced(package, out,
                    read_xml=lambda z, name: ET.fromstring(z.read(name)),
                    resolve_target=lambda base, target: target)
                label, _, _, _ = next(iterator)
                self.assertEqual(label, "[脚注 ID 1]")
                annotations.marker(second)
                self.assertEqual(next(iterator)[0], "[尾注 ID 2]")

    def test_reference_limit_is_bounded_and_reports_notice(self):
        annotations = WordAnnotations(max_references=2)
        for identifier in ("1", "2", "3"):
            node = ET.fromstring(f'<w:footnoteReference xmlns:w="{W}" w:id="{identifier}"/>')
            annotations.marker(node)
        out = type("Out", (), {"notices": [], "truncated": False})()
        with tempfile.TemporaryDirectory(prefix="word-annotations60-") as folder:
            path = Path(folder) / "limit.docx"
            with zipfile.ZipFile(path, "w"):
                pass
            with zipfile.ZipFile(path) as package:
                items = list(annotations.referenced(
                    package, out,
                    read_xml=lambda z, name: ET.fromstring(z.read(name)),
                    resolve_target=lambda base, target: target,
                ))
        self.assertEqual(items, [])
        self.assertTrue(any("超过2项" in notice for notice in out.notices))

    def test_strict_namespace_separator_filter_and_single_part_read(self):
        annotations = WordAnnotations()
        foreign = ET.fromstring('<x:footnoteReference xmlns:x="urn:other" x:id="1"/>')
        self.assertIsNone(annotations.marker(foreign))
        for identifier in ("-1", "2", "2"):
            node = ET.fromstring(f'<w:footnoteReference xmlns:w="{W}" w:id="{identifier}"/>')
            annotations.marker(node)
        with tempfile.TemporaryDirectory(prefix="word-annotations60-") as folder:
            path = Path(folder) / "counted.docx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/footnotes.xml", f'''<w:footnotes xmlns:w="{W}">
                    <w:footnote w:id="-1" w:type="separator">{_p("SEPARATOR_CANARY")}</w:footnote>
                    <w:footnote w:id="2">{_p("FOOTNOTE_ONCE")}</w:footnote>
                </w:footnotes>''')
            calls = {}
            out = type("Out", (), {"notices": [], "truncated": False})()
            with zipfile.ZipFile(path) as package:
                def read_xml(z, name):
                    calls[name] = calls.get(name, 0) + 1
                    return ET.fromstring(z.read(name))
                items = list(annotations.referenced(
                    package, out, read_xml=read_xml,
                    resolve_target=lambda base, target: target,
                ))
        self.assertEqual(calls["word/footnotes.xml"], 1)
        self.assertEqual([item[0] for item in items], ["[脚注 ID 2]"])
        self.assertTrue(any("[脚注 ID -1]目标未找到" in notice for notice in out.notices))


if __name__ == "__main__":
    unittest.main()
