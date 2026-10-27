"""PPTX table extraction contract for local attachment parsing."""
from pathlib import Path
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET

from offline_documents import TextResult, extract_document
from offline_presentation_tables import extract_slide_text


A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"


def pptx_file(path):
    """Write a synthetic OOXML deck with normal text and one table."""
    presentation = f'''<p:presentation xmlns:p="{P}" xmlns:r="{R}">
      <p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>
    </p:presentation>'''
    rels = f'''<Relationships xmlns="{PR}">
      <Relationship Id="rId1" Type="{P}/slide" Target="slides/slide1.xml"/>
    </Relationships>'''
    slide = f'''<p:sld xmlns:p="{P}" xmlns:a="{A}">
      <p:cSld><p:spTree>
        <p:sp><p:txBody><a:p><a:r><a:t>PPT_TABLE_BEFORE</a:t></a:r><a:br/><a:r><a:t>context</a:t></a:r></a:p></p:txBody></p:sp>
        <p:graphicFrame><a:graphic><a:graphicData uri="table"><a:tbl>
          <a:tblGrid><a:gridCol/><a:gridCol/><a:gridCol/></a:tblGrid>
          <a:tr>
            <a:tc><a:txBody><a:p><a:r><a:t>PPT_TABLE_NEEDLE</a:t></a:r></a:p></a:txBody></a:tc>
            <a:tc><a:txBody><a:p><a:r><a:t>   </a:t></a:r></a:p><a:p><a:r><a:t>\t </a:t></a:r></a:p></a:txBody></a:tc>
            <a:tc><a:txBody><a:p><a:r><a:t>Qty</a:t></a:r></a:p></a:txBody></a:tc>
          </a:tr>
          <a:tr>
            <a:tc><a:txBody><a:p><a:r><a:t>Line A</a:t></a:r></a:p><a:p><a:r><a:t>Detail A</a:t></a:r></a:p></a:txBody></a:tc>
            <a:tc hMerge="true"><a:txBody><a:p><a:r><a:t>saved continuation</a:t></a:r></a:p></a:txBody></a:tc>
            <a:tc><a:txBody><a:p><a:r><a:t>50</a:t></a:r></a:p></a:txBody></a:tc>
          </a:tr>
          <a:tr>
            <a:tc gridSpan="2"><a:txBody><a:p><a:r><a:t>Total</a:t></a:r></a:p></a:txBody></a:tc>
            <a:tc hMerge="true"><a:txBody><a:p/></a:txBody></a:tc>
            <a:tc><a:txBody><a:p><a:r><a:t>100</a:t></a:r></a:p></a:txBody></a:tc>
          </a:tr>
          <a:tr>
            <a:tc rowSpan="2"><a:txBody><a:p><a:r><a:t>Delivery terms</a:t></a:r></a:p></a:txBody></a:tc>
            <a:tc><a:txBody><a:p><a:r><a:t>First payment</a:t></a:r></a:p></a:txBody></a:tc>
            <a:tc><a:txBody><a:p><a:r><a:t>7 days</a:t></a:r></a:p></a:txBody></a:tc>
          </a:tr>
          <a:tr>
            <a:tc vMerge="true"><a:txBody><a:p/></a:txBody></a:tc>
            <a:tc><a:txBody><a:p><a:r><a:t>Balance</a:t></a:r></a:p></a:txBody></a:tc>
            <a:tc><a:txBody><a:p><a:r><a:t>Acceptance</a:t></a:r></a:p></a:txBody></a:tc>
          </a:tr>
        </a:tbl></a:graphicData></a:graphic></p:graphicFrame>
        <p:sp><p:txBody><a:p><a:r><a:t>PPT_TABLE_AFTER</a:t></a:r></a:p></p:txBody></p:sp>
      </p:spTree></p:cSld>
    </p:sld>'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/_rels/presentation.xml.rels", rels)
        archive.writestr("ppt/slides/slide1.xml", slide)


class PresentationTableTests(unittest.TestCase):
    def _slide(self, path):
        with zipfile.ZipFile(path) as archive:
            return ET.fromstring(archive.read("ppt/slides/slide1.xml"))

    def test_table_is_one_section_per_row_and_preserves_cell_semantics(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "table.pptx"
            pptx_file(path)
            result = TextResult()
            extract_slide_text(self._slide(path), result, "幻灯片 1")
            text = result.result()["text"]
            self.assertIn("[幻灯片 1]\nPPT_TABLE_BEFORE\ncontext", text)
            self.assertIn("幻灯片 1 表格 1 行 1", text)
            self.assertIn("C1=PPT_TABLE_NEEDLE\tC2=（空）\tC3=Qty", text)
            self.assertIn("C1=Line A\nDetail A", text)
            self.assertIn("C2=saved continuation（合并延续单元格保存文字）", text)
            self.assertIn("声明合并范围 R3C1:R3C2", text)
            self.assertIn("C2=（合并延续单元格，空）", text)
            self.assertIn("声明合并范围 R4C1:R5C1", text)
            self.assertEqual(text.count("PPT_TABLE_NEEDLE"), 1)
            self.assertEqual(text.count("Total"), 1)
            self.assertEqual(text.count("Delivery terms"), 1)
            self.assertIn("[幻灯片 1]\nPPT_TABLE_AFTER", text)

    def test_invalid_span_keeps_text_and_records_notice(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "table.pptx"
            pptx_file(path)
            with zipfile.ZipFile(path) as archive:
                root = ET.fromstring(archive.read("ppt/slides/slide1.xml"))
            cell = root.find(f".//{{{A}}}tbl/{{{A}}}tr/{{{A}}}tc")
            cell.set("gridSpan", "bad")
            result = TextResult()
            extract_slide_text(root, result, "幻灯片 1")
            data = result.result()
            self.assertIn("PPT_TABLE_NEEDLE", data["text"])
            self.assertTrue(any("gridSpan" in notice for notice in data["notices"]))

    def test_false_merge_flags_are_not_continuations_and_nonempty_spaces_remain(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "table.pptx"
            pptx_file(path)
            root = self._slide(path)
            for cell in root.findall(f".//{{{A}}}tc"):
                if "hMerge" in cell.attrib:
                    cell.set("hMerge", "false")
                if "vMerge" in cell.attrib:
                    cell.set("vMerge", "0")
            first_text = root.find(f".//{{{A}}}tbl/{{{A}}}tr[2]//{{{A}}}t")
            first_text.text = "  keep surrounding spaces  "
            result = TextResult()
            extract_slide_text(root, result, "幻灯片 1")
            text = result.result()["text"]
            self.assertNotIn("合并延续单元格", text)
            self.assertIn("C1=  keep surrounding spaces  ", text)


    def test_output_limit_stops_table_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "table.pptx"
            pptx_file(path)
            root = self._slide(path)
            first_text = root.find(f".//{{{A}}}tbl/{{{A}}}tr/{{{A}}}tc/{{{A}}}txBody/{{{A}}}p/{{{A}}}r/{{{A}}}t")
            first_text.text = "LIMIT_TABLE_" + ("x" * 200000)
            result = TextResult()
            extract_slide_text(root, result, "幻灯片 1")
            data = result.result()
            self.assertTrue(data["truncated"])
            self.assertIn("幻灯片 1 表格 1 行 1", data["text"])
            self.assertNotIn("幻灯片 1 表格 1 行 2", data["text"])
            self.assertNotIn("PPT_TABLE_AFTER", data["text"])

    def test_large_span_is_annotated_without_cell_expansion(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "table.pptx"
            pptx_file(path)
            root = self._slide(path)
            cell = root.find(f".//{{{A}}}tbl/{{{A}}}tr/{{{A}}}tc")
            cell.set("gridSpan", "1000000")
            result = TextResult()
            extract_slide_text(root, result, "幻灯片 1")
            text = result.result()["text"]
            self.assertIn("声明合并范围 R1C1:R1C1000000", text)
            self.assertNotIn("C1000000=", text)

    def test_real_ooxml_extract_document_includes_table_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "table.pptx"
            pptx_file(path)
            data = extract_document(path, ".pptx")
            self.assertIn("PPT_TABLE_NEEDLE", data["text"])
            self.assertIn("幻灯片 1 表格 1 行 1", data["text"])
            self.assertEqual(data["text"].count("PPT_TABLE_NEEDLE"), 1)


if __name__ == "__main__":
    unittest.main()
