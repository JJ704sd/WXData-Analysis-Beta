"""Focused contract tests for PPTX hyperlink and action provenance."""

from pathlib import Path
import posixpath
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET

from offline_documents import MAX_SECTIONS, MAX_TEXT, TextResult, _xml, extract_document
from offline_presentation_links import extract_presentation_links, read_presentation_links


A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"


def pptx_file(path, notes_target="../notesSlides/notesSlide1.xml"):
    """Write a small deck covering body, table, shape, hover, and notes links."""
    presentation = f'''<p:presentation xmlns:p="{P}" xmlns:r="{R}">
      <p:sldIdLst><p:sldId id="256" r:id="rIdSlide"/></p:sldIdLst>
    </p:presentation>'''
    presentation_rels = f'''<Relationships xmlns="{PR}">
      <Relationship Id="rIdSlide" Type="{P}/slide" Target="slides/slide1.xml"/>
    </Relationships>'''
    slide = f'''<p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}">
      <p:cSld><p:spTree>
        <p:sp><p:nvSpPr><p:cNvPr id="2" name="shape-link">
          <a:hlinkClick r:id="rIdShape" tooltip="shape tip"/>
        </p:cNvPr></p:nvSpPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>
        <p:sp><p:nvSpPr><p:cNvPr id="3" name="body-link"/></p:nvSpPr>
          <p:txBody><a:bodyPr/><a:lstStyle/><a:p>
            <a:r><a:rPr><a:hlinkClick r:id="rIdBody"/></a:rPr><a:t>Body link text</a:t></a:r>
            <a:r><a:rPr><a:hlinkHover r:id="rIdHover" tooltip="hover tip"/></a:rPr><a:t>Hover link text</a:t></a:r>
            <a:r><a:rPr><a:hlinkClick r:id="rIdSlideJump"/></a:rPr><a:t>Internal jump</a:t></a:r>
            <a:r><a:rPr><a:hlinkClick action="ppaction://hlinkshowjump?jump=nextslide"/></a:rPr><a:t>Raw action</a:t></a:r>
            <a:r><a:rPr><a:hlinkClick r:id="rIdMissing"/></a:rPr><a:t>Missing relation text</a:t></a:r>
          </a:p></p:txBody>
        </p:sp>
        <p:graphicFrame><a:graphic><a:graphicData uri="table"><a:tbl>
          <a:tblGrid><a:gridCol/><a:gridCol/></a:tblGrid>
          <a:tr><a:tc><a:txBody><a:bodyPr/><a:lstStyle/><a:p>
            <a:r><a:rPr><a:hlinkClick r:id="rIdTable"/></a:rPr><a:t>Table link text</a:t></a:r>
          </a:p></a:txBody></a:tc><a:tc><a:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>Other</a:t></a:r></a:p></a:txBody></a:tc></a:tr>
        </a:tbl></a:graphicData></a:graphic></p:graphicFrame>
      </p:spTree></p:cSld></p:sld>'''
    slide_rels = f'''<Relationships xmlns="{PR}">
      <Relationship Id="rIdShape" Type="{R}/hyperlink" Target="https://example.test/SHAPE_LINK_NEEDLE" TargetMode="External"/>
      <Relationship Id="rIdBody" Type="{R}/hyperlink" Target="https://example.test/PPT_LINK_NEEDLE" TargetMode="External"/>
      <Relationship Id="rIdHover" Type="{R}/hyperlink" Target="https://example.test/HOVER_LINK_NEEDLE" TargetMode="External"/>
      <Relationship Id="rIdSlideJump" Type="{P}/slide" Target="../slides/slide2.xml"/>
      <Relationship Id="rIdOrphan" Type="{R}/hyperlink" Target="https://example.test/ORPHAN_LINK" TargetMode="External"/>
      <Relationship Id="rIdNotes" Type="{P}/notesSlide" Target="{notes_target}"/>
      <Relationship Id="rIdTable" Type="{R}/hyperlink" Target="https://example.test/TABLE_LINK_NEEDLE" TargetMode="External"/>
    </Relationships>'''
    notes = f'''<p:notes xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}"><p:cSld><p:spTree><p:sp>
      <p:nvSpPr><p:cNvPr id="4" name="notes"/></p:nvSpPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p>
        <a:r><a:rPr><a:hlinkClick r:id="rIdNote"/></a:rPr><a:t>Notes link text</a:t></a:r>
      </a:p></p:txBody>
    </p:sp></p:spTree></p:cSld></p:notes>'''
    notes_rels = f'''<Relationships xmlns="{PR}">
      <Relationship Id="rIdNote" Type="{R}/hyperlink" Target="https://example.test/NOTE_LINK_NEEDLE" TargetMode="External"/>
    </Relationships>'''
    notes_part = posixpath.normpath(posixpath.join("ppt/slides", notes_target))
    notes_rels_part = posixpath.join(
        posixpath.dirname(notes_part), "_rels", posixpath.basename(notes_part) + ".rels"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/_rels/presentation.xml.rels", presentation_rels)
        archive.writestr("ppt/slides/slide1.xml", slide)
        archive.writestr("ppt/slides/_rels/slide1.xml.rels", slide_rels)
        archive.writestr(notes_part, notes)
        archive.writestr(notes_rels_part, notes_rels)


class PresentationLinkTests(unittest.TestCase):
    def test_page_limit_keeps_text_within_allowed_slides(self):
        from unittest.mock import patch
        from offline_documents import extract_document
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'two-slides.pptx'
            pptx_file(path)
            with zipfile.ZipFile(path) as archive:
                files = {name: archive.read(name) for name in archive.namelist()}
            presentation = ET.fromstring(files['ppt/presentation.xml'])
            ET.SubElement(presentation.find(f'{{{P}}}sldIdLst'), f'{{{P}}}sldId',
                          {'id': '257', f'{{{R}}}id': 'second'})
            files['ppt/presentation.xml'] = ET.tostring(presentation)
            rels = ET.fromstring(files['ppt/_rels/presentation.xml.rels'])
            ET.SubElement(rels, f'{{{PR}}}Relationship',
                          {'Id': 'second', 'Type': R + '/slide', 'Target': 'slides/slide2.xml'})
            files['ppt/_rels/presentation.xml.rels'] = ET.tostring(rels)
            files['ppt/slides/slide2.xml'] = files['ppt/slides/slide1.xml'].replace(
                b'Body link text', b'SECOND_SLIDE_NOT_READ')
            with zipfile.ZipFile(path, 'w') as archive:
                for name, raw in files.items(): archive.writestr(name, raw)
            with patch('offline_documents.MAX_PAGES', 1):
                result = extract_document(path, '.pptx')
            self.assertTrue(result['truncated'])
            # Check the original paragraph, rather than its link metadata copy.
            self.assertTrue(any(section['location'] == '幻灯片 1' and
                                'Body link text' in section['text'] for section in result['sections']))
            self.assertIn('PPT_LINK_NEEDLE', result['text'])
            self.assertNotIn('SECOND_SLIDE_NOT_READ', result['text'])

    def _parts(self, path):
        with zipfile.ZipFile(path) as archive:
            slide = ET.fromstring(archive.read("ppt/slides/slide1.xml"))
            notes = ET.fromstring(archive.read("ppt/notesSlides/notesSlide1.xml"))
            slide_links = read_presentation_links(archive, "ppt/slides/slide1.xml", _xml)
            note_links = read_presentation_links(archive, "ppt/notesSlides/notesSlide1.xml", _xml)
        return slide, notes, slide_links, note_links

    def test_raw_relationships_keep_targets_and_ignore_orphan_rels(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "links.pptx"
            pptx_file(path)
            slide, _, relationships, _ = self._parts(path)
            self.assertEqual(relationships["rIdBody"]["target"], "https://example.test/PPT_LINK_NEEDLE")
            self.assertEqual(relationships["rIdBody"]["mode"], "External")
            self.assertEqual(relationships["rIdSlideJump"]["target"], "../slides/slide2.xml")
            self.assertIn("rIdOrphan", relationships)

    def test_referenced_links_are_located_and_orphans_are_not_emitted(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "links.pptx"
            pptx_file(path)
            slide, notes, relationships, note_links = self._parts(path)
            result = TextResult()
            extract_presentation_links(slide, result, "幻灯片 1", relationships)
            extract_presentation_links(notes, result, "幻灯片 1 备注", note_links)
            data = result.result()
            text = data["text"]
            self.assertIn("PPT_LINK_NEEDLE", text)
            self.assertIn("TABLE_LINK_NEEDLE", text)
            self.assertIn("SHAPE_LINK_NEEDLE", text)
            self.assertIn("NOTE_LINK_NEEDLE", text)
            self.assertIn("幻灯片 1 表格 1 行 1 列 1", text)
            self.assertIn("形状 shape-link id=2", text)
            self.assertIn("Body link text", text)
            self.assertIn("HOVER_LINK_NEEDLE", text)
            self.assertIn("悬停链接", text)
            self.assertIn("文档内幻灯片跳转", text)
            self.assertIn("ppaction://hlinkshowjump?jump=nextslide", text)
            self.assertNotIn("ORPHAN_LINK", text)
            self.assertTrue(any("rIdMissing" in notice for notice in data["notices"]))

    def test_page_truncation_flag_does_not_skip_links_under_actual_limits(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "links.pptx"
            pptx_file(path)
            slide, _, relationships, _ = self._parts(path)
            result = TextResult()
            result.truncated = True
            extract_presentation_links(slide, result, "幻灯片 1", relationships)
            self.assertIn("PPT_LINK_NEEDLE", result.result()["text"])

    def test_action_without_relationship_is_reported_as_action_only(self):
        root = ET.fromstring(f'''<a:p xmlns:a="{A}" xmlns:r="{R}">
          <a:r><a:rPr><a:hlinkClick action="ppaction://hlinkshowjump?jump=nextslide"/></a:rPr><a:t>Next</a:t></a:r>
        </a:p>''')
        result = TextResult()
        extract_presentation_links(root, result, "幻灯片 1", {})
        text = result.result()["text"]
        self.assertIn("动作声明，无关系目标", text)
        self.assertNotIn("关系缺失", text)
        self.assertIn("ppaction://hlinkshowjump?jump=nextslide", text)

    def test_field_link_uses_actual_field_text_without_default_pr_inheritance(self):
        root = ET.fromstring(f'''<a:p xmlns:a="{A}" xmlns:r="{R}">
          <a:fld id="field-1" type="slidenum">
            <a:rPr><a:hlinkClick r:id="rIdField"/></a:rPr><a:t>Field link text</a:t>
          </a:fld>
        </a:p>''')
        result = TextResult()
        extract_presentation_links(root, result, "幻灯片 1", {
            "rIdField": {"target": "https://example.test/FIELD_LINK", "type": R + "/hyperlink", "mode": "External"},
        })
        text = result.result()["text"]
        self.assertIn("Field link text", text)
        self.assertIn("FIELD_LINK", text)

    def test_real_character_budget_marks_unread_links_truncated(self):
        root = ET.fromstring(
            f'''<a:p xmlns:a="{A}" xmlns:r="{R}">
              <a:r><a:rPr><a:hlinkClick r:id="rIdLong"/></a:rPr><a:t>{"x" * MAX_TEXT}</a:t></a:r>
              <a:r><a:rPr><a:hlinkClick r:id="rIdAfter"/></a:rPr><a:t>after</a:t></a:r>
            </a:p>'''
        )
        relationships = {
            "rIdLong": {"target": "long", "type": "", "mode": None},
            "rIdAfter": {"target": "after", "type": "", "mode": None},
        }
        result = TextResult()
        extract_presentation_links(root, result, "幻灯片 1", relationships)
        data = result.result()
        self.assertTrue(data["truncated"])
        self.assertNotIn("目标=after", data["text"])

    def test_real_section_budget_marks_unread_links_truncated(self):
        paragraphs = []
        relationships = {}
        for index in range(MAX_SECTIONS + 1):
            relation_id = "rId" + str(index)
            paragraphs.append(
                f'<a:r><a:rPr><a:hlinkClick r:id="{relation_id}"/></a:rPr><a:t>x</a:t></a:r>'
            )
            relationships[relation_id] = {"target": str(index), "type": "", "mode": None}
        root = ET.fromstring(f'<a:p xmlns:a="{A}" xmlns:r="{R}">{"".join(paragraphs)}</a:p>')
        result = TextResult()
        extract_presentation_links(root, result, "幻灯片 1", relationships)
        data = result.result()
        self.assertTrue(data["truncated"])
        self.assertEqual(len(data["sections"]), MAX_SECTIONS)
        self.assertNotIn("目标=1000", data["text"])

    def test_extract_document_uses_notes_relationship_type_and_keeps_contexts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "custom-notes.pptx"
            pptx_file(path, notes_target="../custom/notes-special.xml")
            data = extract_document(path, ".pptx")
            text = data["text"]
            self.assertIn("Body link text", text)
            self.assertIn("TABLE_LINK_NEEDLE", text)
            self.assertIn("SHAPE_LINK_NEEDLE", text)
            self.assertIn("NOTE_LINK_NEEDLE", text)
            self.assertIn("幻灯片 1 备注", text)


if __name__ == "__main__":
    unittest.main()
