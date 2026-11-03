"""Slide-part visibility follows text, notes, charts, links and OCR locations."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

from offline_documents import extract_document
from test_presentation_links62 import A, P, R, PR, pptx_file as link_deck
from test_presentation_charts56 import C, _chart_xml, _bar_chart_xml


def pptx_file(path, show='0'):
    link_deck(path)
    with zipfile.ZipFile(path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    slide = ET.fromstring(files['ppt/slides/slide1.xml'])
    if show is not None:
        slide.set('show', show)
    tree = slide.find(f'{{{P}}}cSld/{{{P}}}spTree')
    frame = ET.SubElement(tree, f'{{{P}}}graphicFrame')
    graphic = ET.SubElement(frame, f'{{{A}}}graphic')
    data = ET.SubElement(graphic, f'{{{A}}}graphicData', {'uri': C})
    ET.SubElement(data, f'{{{C}}}chart', {f'{{{R}}}id': 'chart'})
    files['ppt/slides/slide1.xml'] = ET.tostring(slide)
    rels = ET.fromstring(files['ppt/slides/_rels/slide1.xml.rels'])
    ET.SubElement(rels, f'{{{PR}}}Relationship',
                  {'Id': 'chart', 'Type': R + '/chart', 'Target': '../charts/chart1.xml'})
    files['ppt/slides/_rels/slide1.xml.rels'] = ET.tostring(rels)
    files['ppt/charts/chart1.xml'] = _chart_xml(_bar_chart_xml())
    # The slide list, not filenames or relationship order, controls numbering.
    presentation = ET.fromstring(files['ppt/presentation.xml'])
    slides = presentation.find(f'{{{P}}}sldIdLst')
    slides.insert(0, ET.Element(f'{{{P}}}sldId', {'id': '257', f'{{{R}}}id': 'visible'}))
    files['ppt/presentation.xml'] = ET.tostring(presentation)
    rels = ET.fromstring(files['ppt/_rels/presentation.xml.rels'])
    ET.SubElement(rels, f'{{{PR}}}Relationship',
                  {'Id': 'visible', 'Type': R + '/slide', 'Target': 'slides/slide2.xml'})
    files['ppt/_rels/presentation.xml.rels'] = ET.tostring(rels)
    files['ppt/slides/slide2.xml'] = (f'<p:sld xmlns:p="{P}" xmlns:a="{A}">'
        '<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>VISIBLE_SLIDE65</a:t>'
        '</a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>').encode()
    with zipfile.ZipFile(path, 'w') as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)


class PresentationVisibility65Tests(unittest.TestCase):
    def test_hidden_slide_retains_all_material_with_visibility(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'visibility.pptx'
            pptx_file(path)
            result = extract_document(path, '.pptx')
            visible = [s for s in result['sections'] if 'VISIBLE_SLIDE65' in s['text']]
            self.assertEqual([s['location'] for s in visible], ['幻灯片 1'])
            hidden = [s for s in result['sections'] if 'VISIBLE_SLIDE65' not in s['text']]
            self.assertTrue(hidden)
            self.assertTrue(all(s['location'].startswith('幻灯片 2 [隐藏幻灯片]') for s in hidden))
            for marker in ('Body link text', 'Table link text', 'Notes link text',
                           'PPT_LINK_NEEDLE', 'NOTE_LINK_NEEDLE', '季度销售额'):
                self.assertIn(marker, result['text'])

    def test_only_slide_part_false_values_hide_the_slide(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'values.pptx'
            for value, expected in [('0', True), ('false', True), ('1', False), ('true', False), (None, False)]:
                with self.subTest(show=value):
                    pptx_file(path, value)
                    result = extract_document(path, '.pptx')
                    body = next(s for s in result['sections'] if s['text'].startswith('Body link text'))
                    self.assertEqual('[隐藏幻灯片]' in body['location'], expected)

    def test_page_budget_keeps_original_slide_order(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'limited.pptx'
            pptx_file(path)
            with patch('offline_documents.MAX_PAGES', 1):
                result = extract_document(path, '.pptx')
            self.assertTrue(result['truncated'])
            self.assertIn('VISIBLE_SLIDE65', result['text'])
            self.assertNotIn('Body link text', result['text'])

    def test_embedded_image_walker_receives_hidden_slide_and_note_labels(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'images.pptx'
            pptx_file(path)
            with patch('offline_document_images.extract_embedded_images') as images:
                extract_document(path, '.pptx')
            parts = dict(images.call_args.args[1])
            self.assertEqual(parts['ppt/slides/slide1.xml'], '幻灯片 2 [隐藏幻灯片]')
            self.assertEqual(parts['ppt/notesSlides/notesSlide1.xml'], '幻灯片 2 [隐藏幻灯片] 备注')


if __name__ == '__main__':
    unittest.main()
