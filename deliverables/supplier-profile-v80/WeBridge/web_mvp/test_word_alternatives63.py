import base64
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest.mock import patch

import offline_documents
from offline_documents import ContentIssue, extract_document
from offline_word_xml import parse_word_xml


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
WORD_ALT_NEEDLE = "WORD_ALT_NEEDLE"
WORD_BLOCK_NEEDLE = "WORD_BLOCK_NEEDLE"
INACTIVE_WORD_ALT = "INACTIVE_WORD_ALT"


def _p(text):
    return f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>'


def docx_file(path):
    """Create a fixture with alternatives in inline, block, and sectPr XML."""
    document = f'''<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="{W}" xmlns:r="{R}" xmlns:mc="{MC}"
    xmlns:w14="urn:unsupported" xmlns:a="{A}" xmlns:wp="{WP}" xmlns:pic="{PIC}"
    mc:Ignorable="w14">
  <w:body>
    <w:p><w:r><w:t>BEFORE|</w:t></w:r><mc:AlternateContent>
      <mc:Choice Requires="w14"><w:r><w:t>{INACTIVE_WORD_ALT}|</w:t></w:r><w:r><w:commentReference w:id="1"/></w:r>
        <w:r><w:drawing><wp:inline><a:graphic><a:graphicData><pic:pic><pic:blipFill><a:blip r:embed="rInactiveImage"/></pic:blipFill></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>
      </mc:Choice>
      <mc:Fallback><w:r><w:t>{WORD_ALT_NEEDLE}|</w:t></w:r><w:r><w:commentReference w:id="2"/></w:r>
        <w:r><w:drawing><wp:inline><a:graphic><a:graphicData><pic:pic><pic:blipFill><a:blip r:embed="rImage"/></pic:blipFill></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>
      </mc:Fallback>
    </mc:AlternateContent><w:r><w:t>AFTER</w:t></w:r></w:p>
    <mc:AlternateContent>
      <mc:Choice Requires="w14"><w:p><w:r><w:t>{INACTIVE_WORD_ALT}</w:t></w:r></w:p></mc:Choice>
      <mc:Fallback><w:p><w:r><w:t>{WORD_BLOCK_NEEDLE}</w:t></w:r></w:p></mc:Fallback>
    </mc:AlternateContent>
    <w:sectPr><mc:AlternateContent>
      <mc:Choice Requires="w14"><w:headerReference w:type="default" r:id="rInactive"/></mc:Choice>
      <mc:Fallback><w:headerReference w:type="default" r:id="rChoice"/></mc:Fallback>
    </mc:AlternateContent></w:sectPr>
  </w:body>
</w:document>'''
    rels = f'''<Relationships xmlns="{PR}">
      <Relationship Id="rChoice" Type="{R}/header" Target="header1.xml"/>
      <Relationship Id="rInactive" Type="{R}/header" Target="header2.xml"/>
      <Relationship Id="rImage" Type="{R}/image" Target="media/active.png"/>
      <Relationship Id="rInactiveImage" Type="{R}/image" Target="media/inactive.png"/>
      <Relationship Id="rComments" Type="{R}/comments" Target="comments.xml"/>
    </Relationships>'''
    header1 = f'<w:hdr xmlns:w="{W}">{_p("HEADER_ACTIVE")}</w:hdr>'
    header2 = f'<w:hdr xmlns:w="{W}">{_p(INACTIVE_WORD_ALT)}</w:hdr>'
    comments = f'''<w:comments xmlns:w="{W}">
      <w:comment w:id="1"><w:p><w:r><w:t>{INACTIVE_WORD_ALT}</w:t></w:r></w:p></w:comment>
      <w:comment w:id="2"><w:p><w:r><w:t>COMMENT_ACTIVE</w:t></w:r></w:p></w:comment>
    </w:comments>'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("word/_rels/document.xml.rels", rels)
        archive.writestr("word/header1.xml", header1)
        archive.writestr("word/header2.xml", header2)
        archive.writestr("word/comments.xml", comments)
        archive.writestr(
            "word/media/active.png",
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            ),
        )
        archive.writestr(
            "word/media/inactive.png",
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            ),
        )
    return path


class WordAlternatives63Tests(unittest.TestCase):
    def test_supported_choice_is_selected_and_tail_is_preserved(self):
        xml = f'''<w:document xmlns:w="{W}" xmlns:mc="{MC}" xmlns:word="{W}"
            xmlns:other="urn:unsupported"><w:body><w:p>
          <w:r><w:t>before</w:t></w:r><mc:AlternateContent>
            <mc:Choice Requires="other"><w:r><w:t>{INACTIVE_WORD_ALT}</w:t></w:r></mc:Choice>
            <mc:Choice Requires="word"><w:r><w:t>{WORD_ALT_NEEDLE}</w:t></w:r>CHOICE_TAIL</mc:Choice>
            <mc:Fallback><w:r><w:t>fallback</w:t></w:r></mc:Fallback>
          </mc:AlternateContent>AC_TAIL<w:r><w:t>after</w:t></w:r>
        </w:p></w:body></w:document>'''.encode()
        root, omitted = parse_word_xml(xml)
        text = ''.join(root.itertext())
        self.assertEqual(omitted, 0)
        self.assertIn("before", text)
        self.assertIn(WORD_ALT_NEEDLE, text)
        self.assertIn("after", text)
        self.assertNotIn(INACTIVE_WORD_ALT, text)
        self.assertNotIn("fallback", text)
        self.assertLess(text.index(WORD_ALT_NEEDLE), text.index("CHOICE_TAIL"))
        self.assertLess(text.index("CHOICE_TAIL"), text.index("AC_TAIL"))
        self.assertLess(text.index("AC_TAIL"), text.index("after"))
        self.assertIsNone(root.find(f'.//{{{MC}}}AlternateContent'))

    def test_nested_alternative_only_counts_selected_missing_branch(self):
        xml = f'''<w:document xmlns:w="{W}" xmlns:mc="{MC}" xmlns:word="{W}">
          <w:body><w:p><mc:AlternateContent>
            <mc:Choice Requires="word"><w:r><w:t>active</w:t></w:r>
              <mc:AlternateContent><w:r><w:t>bad wrapper</w:t></w:r></mc:AlternateContent>
            </mc:Choice>
            <mc:Fallback><w:r><w:t>inactive</w:t></w:r>
              <mc:AlternateContent><w:r><w:t>unselected missing</w:t></w:r></mc:AlternateContent>
            </mc:Fallback>
          </mc:AlternateContent></w:p></w:body>
        </w:document>'''.encode()
        root, omitted = parse_word_xml(xml)
        self.assertEqual(omitted, 1)
        self.assertIn("active", ''.join(root.itertext()))
        self.assertNotIn("inactive", ''.join(root.itertext()))
        self.assertNotIn("unselected missing", ''.join(root.itertext()))

    def test_local_alias_rebinding_and_multiple_requires(self):
        xml = f'''<w:document xmlns:w="{W}" xmlns:mc="{MC}" xmlns:a="urn:unsupported">
          <w:body><w:p><mc:AlternateContent>
            <mc:Choice Requires="a w"><w:r><w:t>bad</w:t></w:r></mc:Choice>
            <mc:Choice xmlns:a="{W}" Requires="a w"><w:r><w:t>good</w:t></w:r></mc:Choice>
            <mc:Fallback><w:r><w:t>fallback</w:t></w:r></mc:Fallback>
          </mc:AlternateContent>
          <mc:AlternateContent>
            <mc:Choice Requires="a"><w:r><w:t>scope leaked</w:t></w:r></mc:Choice>
            <mc:Fallback><w:r><w:t>scope restored</w:t></w:r></mc:Fallback>
          </mc:AlternateContent>
        </w:p></w:body>
        </w:document>'''.encode()
        root, _ = parse_word_xml(xml)
        text = ''.join(root.itertext())
        self.assertIn("good", text)
        self.assertNotIn("bad", text)
        self.assertNotIn("fallback", text)
        self.assertNotIn("scope leaked", text)
        self.assertIn("scope restored", text)

    def test_no_fallback_is_removed_and_invalid_xml_is_visible(self):
        xml = f'''<w:document xmlns:w="{W}" xmlns:mc="{MC}"><w:body>
          <w:p><mc:AlternateContent><mc:Choice Requires="missing"><w:r><w:t>gone</w:t></w:r></mc:Choice></mc:AlternateContent></w:p>
        </w:body></w:document>'''.encode()
        root, omitted = parse_word_xml(xml)
        self.assertEqual(omitted, 1)
        self.assertNotIn("gone", ''.join(root.itertext()))
        with self.assertRaises(ET.ParseError):
            parse_word_xml(b'<w:document>')

    def test_docx_fixture_keeps_active_word_content_traceable(self):
        with tempfile.TemporaryDirectory(prefix="word-alternatives63-") as folder:
            path = docx_file(Path(folder) / "alternatives.docx")
            with zipfile.ZipFile(path) as archive:
                root, omitted = parse_word_xml(archive.read("word/document.xml"))
                comments = archive.read("word/comments.xml").decode()
                header = archive.read("word/header1.xml").decode()
        text = ''.join(root.itertext())
        self.assertEqual(omitted, 0)
        self.assertIn(WORD_ALT_NEEDLE, text)
        self.assertIn(WORD_BLOCK_NEEDLE, text)
        self.assertNotIn(INACTIVE_WORD_ALT, text)
        self.assertTrue(any(node.get(f"{{{R}}}id") == "rChoice" for node in root.iter()))
        self.assertTrue(any(node.get(f"{{{W}}}id") == "2" for node in root.iter()))
        self.assertIn("COMMENT_ACTIVE", comments)
        self.assertIn("HEADER_ACTIVE", header)

    def test_actual_docx_extracts_active_parts_and_reads_document_once(self):
        with tempfile.TemporaryDirectory(prefix="word-alternatives63-actual-") as folder:
            path = docx_file(Path(folder) / "alternatives.docx")
            with patch.object(offline_documents, "_xml_data",
                              wraps=offline_documents._xml_data) as read_data:
                result = extract_document(path, ".docx")
        text = result["text"]
        self.assertEqual(text.count(WORD_ALT_NEEDLE), 1)
        self.assertEqual(text.count(WORD_BLOCK_NEEDLE), 1)
        self.assertNotIn(INACTIVE_WORD_ALT, text)
        self.assertIn("COMMENT_ACTIVE", text)
        self.assertIn("HEADER_ACTIVE", text)
        self.assertEqual(result["embeddedImages"]["referenced"], 1)
        document_reads = [call for call in read_data.call_args_list
                          if len(call.args) > 1 and call.args[1] == "word/document.xml"]
        self.assertEqual(len(document_reads), 1)

    def test_no_fallback_reports_once_per_word_part_and_doctype_is_rejected(self):
        no_fallback = f'''<w:document xmlns:w="{W}" xmlns:mc="{MC}" xmlns:w14="urn:unsupported">
          <w:body>
            <mc:AlternateContent><mc:Choice Requires="w14"><w:p><w:r><w:t>drop1</w:t></w:r></w:p></mc:Choice></mc:AlternateContent>
            <mc:AlternateContent><mc:Choice Requires="w14"><w:p><w:r><w:t>drop2</w:t></w:r></w:p></mc:Choice></mc:AlternateContent>
          </w:body>
        </w:document>'''
        with tempfile.TemporaryDirectory(prefix="word-alternatives63-safety-") as folder:
            path = Path(folder) / "no-fallback.docx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/document.xml", no_fallback)
            result = extract_document(path, ".docx")
            self.assertEqual(sum("兼容内容无可用分支" in notice for notice in result["notices"]), 1)
            self.assertIn("有 2 处", result["notices"][0])

            doctype = Path(folder) / "doctype.docx"
            unsafe = ('<!DOCTYPE w:document [<!ENTITY injected "bad">]>'
                      + no_fallback)
            with zipfile.ZipFile(doctype, "w") as archive:
                archive.writestr("word/document.xml", unsafe)
            with self.assertRaises(ContentIssue) as error:
                extract_document(doctype, ".docx")
        self.assertEqual(error.exception.status, "failed")


if __name__ == "__main__":
    unittest.main()
