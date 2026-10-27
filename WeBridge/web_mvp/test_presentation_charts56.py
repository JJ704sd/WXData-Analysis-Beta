import hashlib
from pathlib import Path
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as ET

from offline_documents import AttachmentExtractor, TextResult
from offline_presentation_charts import extract_chart


C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"


def _chart_xml(chart):
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<c:chartSpace xmlns:c="{C}" xmlns:a="{A}">
  <c:chart><c:title><c:tx><c:strRef><c:f>Sheet1!$B$1</c:f><c:strCache>
    <c:ptCount val="1"/><c:pt idx="0"><c:v>季度销售额</c:v></c:pt>
  </c:strCache></c:strRef></c:tx></c:title>
    <c:plotArea>{chart}</c:plotArea>
  </c:chart>
</c:chartSpace>'''.encode()


def _bar_chart_xml():
    return '''<c:barChart>
      <c:barDir val="col"/>
      <c:ser><c:idx val="0"/><c:order val="0"/>
        <c:tx><c:v>华东</c:v></c:tx>
        <c:cat><c:strRef><c:strCache>
          <c:ptCount val="3"/><c:pt idx="0"><c:v>一季度</c:v></c:pt>
          <c:pt idx="2"><c:v>三季度</c:v></c:pt>
        </c:strCache></c:strRef></c:cat>
        <c:val><c:numRef><c:numCache><c:formatCode>0.0</c:formatCode>
          <c:ptCount val="3"/><c:pt idx="0"><c:v>10</c:v></c:pt>
          <c:pt idx="1"><c:v>20</c:v></c:pt>
        </c:numCache></c:numRef></c:val>
      </c:ser>
    </c:barChart>'''


def _scatter_chart_xml():
    return '''<c:scatterChart>
      <c:scatterStyle val="lineMarker"/>
      <c:ser><c:idx val="0"/><c:order val="0"/>
        <c:tx><c:v>趋势</c:v></c:tx>
        <c:xVal><c:numRef><c:numCache><c:formatCode>0</c:formatCode>
          <c:ptCount val="2"/><c:pt idx="1"><c:v>2025</c:v></c:pt>
          <c:pt idx="3"><c:v>2027</c:v></c:pt>
        </c:numCache></c:numRef></c:xVal>
        <c:yVal><c:numRef><c:numCache><c:formatCode>0%</c:formatCode>
          <c:ptCount val="2"/><c:pt idx="1"><c:v>0.2</c:v></c:pt>
          <c:pt idx="3"><c:v>0.4</c:v></c:pt>
        </c:numCache></c:numRef></c:yVal>
      </c:ser>
    </c:scatterChart>'''


def _line_chart_xml():
    return '''<c:lineChart>
      <c:ser><c:idx val="1"/><c:order val="1"/>
        <c:tx><c:strRef><c:f>Sheet1!$C$1</c:f><c:strCache>
          <c:ptCount val="1"/><c:pt idx="0"><c:v>华南缓存</c:v></c:pt>
        </c:strCache></c:strRef></c:tx>
        <c:cat><c:strLit><c:pt idx="0"><c:v>一季度</c:v></c:pt></c:strLit></c:cat>
        <c:val><c:numLit><c:pt idx="0"><c:v>8</c:v></c:pt></c:numLit></c:val>
      </c:ser>
    </c:lineChart>'''


def _pie_chart_xml():
    return '''<c:pieChart>
      <c:ser><c:idx val="2"/><c:order val="2"/>
        <c:tx><c:v>占比</c:v></c:tx>
        <c:cat><c:strLit><c:pt idx="0"><c:v>其他</c:v></c:pt></c:strLit></c:cat>
        <c:val><c:numLit><c:pt idx="0"><c:v>5</c:v></c:pt></c:numLit></c:val>
      </c:ser>
    </c:pieChart>'''


def _pptx(path, chart_xml, unreferenced_chart_xml=None):
    presentation = f'''<p:presentation xmlns:p="{P}" xmlns:r="{R}">
      <p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>
    </p:presentation>'''
    presentation_rels = f'''<Relationships xmlns="{PR}">
      <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
    </Relationships>'''
    slide = f'''<p:sld xmlns:p="{P}" xmlns:r="{R}" xmlns:a="{A}" xmlns:c="{C}">
      <p:cSld><p:spTree><p:graphicFrame><a:graphic>
        <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">
          <c:chart r:id="rId1"/>
        </a:graphicData>
      </a:graphic></p:graphicFrame></p:spTree></p:cSld>
    </p:sld>'''
    unreferenced_relation = (
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart" '
        'Target="../charts/chart2.xml"/>'
        if unreferenced_chart_xml is not None else ''
    )
    slide_rels = f'''<Relationships xmlns="{PR}">
      <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart" Target="../charts/chart1.xml"/>
      {unreferenced_relation}
    </Relationships>'''
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/_rels/presentation.xml.rels", presentation_rels)
        archive.writestr("ppt/slides/slide1.xml", slide)
        archive.writestr("ppt/slides/_rels/slide1.xml.rels", slide_rels)
        archive.writestr("ppt/charts/chart1.xml", chart_xml)
        if unreferenced_chart_xml is not None:
            archive.writestr("ppt/charts/chart2.xml", unreferenced_chart_xml)


def _row(path, filename):
    data = path.read_bytes()
    return {
        "id": "synthetic-chart-message",
        "chatId": "R:synthetic-chart",
        "chatName": "合成图表群",
        "kind": "file",
        "asset": {
            "status": "ready",
            "filename": filename,
            "path": "assets/" + filename,
            "digest": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        },
    }


class PresentationChartTests(unittest.TestCase):
    def test_attachment_extracts_chart_only_slide(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "assets").mkdir()
            path = root / "assets" / "chart-only.pptx"
            _pptx(path, _chart_xml(_bar_chart_xml()), _chart_xml('<c:lineChart><c:ser><c:tx><c:v>未引用系列</c:v></c:tx></c:ser></c:lineChart>'))

            row = _row(path, path.name)
            AttachmentExtractor().attach(row, root, True)
            content = row["attachmentContent"]

            self.assertEqual(content["extractionStatus"], "partial")
            self.assertIn("季度销售额", content["text"])
            self.assertIn("系列：华东", content["text"])
            self.assertIn("一季度", content["text"])
            self.assertIn("值：10", content["text"])
            self.assertIn("缓存未重算", content["text"])
            self.assertNotIn("未引用系列", content["text"])

    def test_helper_keeps_sparse_indices_and_xy_pairs(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "charts.pptx"
            _pptx(path, _chart_xml(_bar_chart_xml() + _line_chart_xml() + _pie_chart_xml() + _scatter_chart_xml()))
            out = TextResult()
            with zipfile.ZipFile(path) as archive:
                root = ET.fromstring(archive.read("ppt/charts/chart1.xml"))
                count = extract_chart(root, out, "幻灯片 1")

            self.assertEqual(count, 4)
            result = out.result()
            self.assertEqual(result["extractionStatus"], "partial")
            self.assertIn("一季度", result["text"])
            self.assertIn("idx=1 类别：（缺失） 值：20", result["text"])
            self.assertIn("idx=2 类别：三季度 值：（缺失）", result["text"])
            self.assertIn("x=2025", result["text"])
            self.assertIn("y=0.2", result["text"])
            self.assertIn("数值格式：0.0", result["text"])
            self.assertIn("数值格式：0%", result["text"])
            self.assertIn("图表类型：line", result["text"])
            self.assertIn("图表类型：pie", result["text"])
            self.assertIn("值：8", result["text"])
            self.assertIn("值：5", result["text"])
            self.assertIn("系列：华南缓存", result["text"])

    def test_helper_reports_missing_cache_without_guessing_values(self):
        chart = '''<c:lineChart><c:ser><c:tx><c:v>无缓存系列</c:v></c:tx>
          <c:cat><c:strRef><c:f>Sheet1!$A$1:$A$2</c:f></c:strRef></c:cat>
          <c:val><c:numRef><c:f>Sheet1!$B$1:$B$2</c:f></c:numRef></c:val>
        </c:ser></c:lineChart>'''
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "missing-cache.pptx"
            _pptx(path, _chart_xml(chart))
            out = TextResult()
            with zipfile.ZipFile(path) as archive:
                root = ET.fromstring(archive.read("ppt/charts/chart1.xml"))
                self.assertEqual(extract_chart(root, out, "幻灯片 1"), 1)
            result = out.result()

            self.assertEqual(result["extractionStatus"], "partial")
            self.assertIn("数据缓存缺失", result["text"])
            self.assertNotIn("值：", result["text"])
            self.assertIn("部分系列缺少图表缓存", "\n".join(result["notices"]))


if __name__ == "__main__":
    unittest.main()
