"""Regression coverage for PDF link annotation extraction."""

from pathlib import Path
import tempfile
import unittest

from offline_documents import TextResult, extract_document
from offline_pdf_links import extract_pdf_annotations


PDF_LINK_NEEDLE = 'PDF_LINK_NEEDLE'
PDF_DEST_NEEDLE = 'PDF_DEST_NEEDLE'


def _name(value):
    from pypdf.generic import NameObject
    return NameObject(value)


def _annotation(writer, page, rect=None, *, action=None, destination=None, contents=None):
    from pypdf.generic import ArrayObject, DictionaryObject, NumberObject, TextStringObject

    values = {
        _name('/Type'): _name('/Annot'),
        _name('/Subtype'): _name('/Link'),
    }
    if rect is not None:
        values[_name('/Rect')] = ArrayObject([NumberObject(value) for value in rect])
    if action is not None:
        values[_name('/A')] = action
    if destination is not None:
        values[_name('/Dest')] = destination
    if contents is not None:
        values[_name('/Contents')] = TextStringObject(contents)
    reference = writer._add_object(DictionaryObject(values))
    page.setdefault(_name('/Annots'), ArrayObject()).append(reference)
    return reference


def _text_stream(writer, page, text):
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    font = DictionaryObject({
        _name('/Type'): _name('/Font'),
        _name('/Subtype'): _name('/Type1'),
        _name('/BaseFont'): _name('/Helvetica'),
    })
    page[_name('/Resources')] = DictionaryObject({
        _name('/Font'): DictionaryObject({_name('/F1'): writer._add_object(font)})
    })
    safe = text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
    stream = DecodedStreamObject()
    stream.set_data(('BT /F1 12 Tf 10 200 Td (' + safe + ') Tj ET').encode('ascii'))
    page[_name('/Contents')] = writer._add_object(stream)


def _form_field(writer, page):
    from pypdf.generic import ArrayObject, DictionaryObject, NumberObject, TextStringObject

    field = DictionaryObject({
        _name('/Type'): _name('/Annot'),
        _name('/Subtype'): _name('/Widget'),
        _name('/Rect'): ArrayObject([NumberObject(10), NumberObject(80), NumberObject(110), NumberObject(100)]),
        _name('/P'): page.indirect_reference,
        _name('/FT'): _name('/Tx'),
        _name('/T'): TextStringObject('linkForm'),
        _name('/V'): TextStringObject('FORM_PDF_LINK_62'),
    })
    reference = writer._add_object(field)
    page.setdefault(_name('/Annots'), ArrayObject()).append(reference)
    return reference


def pdf_file(path, text='PDF quotation body'):
    """Create a small PDF containing URI, internal and form annotations."""
    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, DictionaryObject, TextStringObject

    writer = PdfWriter()
    first = writer.add_blank_page(width=400, height=300)
    second = writer.add_blank_page(width=400, height=300)
    third = writer.add_blank_page(width=400, height=300)
    _text_stream(writer, first, text)
    _text_stream(writer, second, 'PDF ordinary body')
    _text_stream(writer, third, 'PDF third body')

    uri_action = writer._add_object(DictionaryObject({
        _name('/S'): _name('/URI'),
        _name('/URI'): TextStringObject('https://supplier.example/PDF_LINK_NEEDLE'),
    }))
    _annotation(writer, first, [10, 20, 110, 40], action=uri_action, contents='外部链接说明')
    _annotation(writer, first, [10, 50, 110, 70], action=DictionaryObject({
        _name('/S'): _name('/GoTo'),
        _name('/D'): ArrayObject([second.indirect_reference, _name('/Fit')]),
    }), contents='内部页链接')
    _annotation(writer, second, [15, 25, 115, 45], destination=TextStringObject(PDF_DEST_NEEDLE),
                contents='命名目的地')
    _annotation(writer, second, None, action=DictionaryObject({
        _name('/S'): _name('/URI'),
        _name('/URI'): TextStringObject('relative/contract.pdf'),
    }))
    _annotation(writer, third, [20, 30, 120, 50], action=DictionaryObject({
        _name('/S'): _name('/JavaScript'),
        _name('/JS'): TextStringObject('DO_NOT_EXECUTE_PDF_LINK_62'),
    }))
    form_reference = _form_field(writer, first)
    writer._root_object[_name('/URI')] = DictionaryObject({
        _name('/Base'): TextStringObject('https://supplier.example/base/'),
    })
    writer._root_object[_name('/AcroForm')] = DictionaryObject({
        _name('/Fields'): ArrayObject([form_reference]),
    })
    with Path(path).open('wb') as stream:
        writer.write(stream)


class PdfLinks62Tests(unittest.TestCase):
    def test_uri_internal_named_destination_rect_and_contents(self):
        from pypdf import PdfReader

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'links.pdf'
            pdf_file(path)
            reader = PdfReader(path, strict=False)
            result = TextResult()
            emitted = extract_pdf_annotations(reader, result, max_pages=100, max_sections=1000, max_text=200000)
            data = result.result()
            self.assertEqual(emitted, 5)
            self.assertIn('https://supplier.example/PDF_LINK_NEEDLE', data['text'])
            self.assertIn('内部目的地: 第 2 页 /Fit', data['text'])
            self.assertIn(PDF_DEST_NEEDLE, data['text'])
            self.assertIn('文件坐标 Rect: 10, 20, 110, 40', data['text'])
            self.assertIn('外部链接说明', data['text'])
            self.assertIn('URI基础（根 /URI/Base）: https://supplier.example/base/', data['text'])
            self.assertIn('relative/contract.pdf', data['text'])
            self.assertIn('位置缺失', data['text'])
            self.assertIn('动作类型: JavaScript（未解析）', data['text'])
            self.assertNotIn('DO_NOT_EXECUTE_PDF_LINK_62', data['text'])
            self.assertEqual([section['location'] for section in data['sections']], [
                '第 1 页 链接 1', '第 1 页 链接 2', '第 2 页 链接 1', '第 2 页 链接 2',
                '第 3 页 链接 1',
            ])

    def test_widget_is_left_to_form_extractor(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'forms-and-links.pdf'
            pdf_file(path)
            result = extract_document(path, '.pdf')
            self.assertIn('PDF quotation body', result['text'])
            self.assertIn('FORM_PDF_LINK_62', result['text'])
            self.assertEqual(result['text'].count('FORM_PDF_LINK_62'), 1)

    def test_bad_annotation_keeps_other_link_and_reports_notice(self):
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import ArrayObject, NumberObject

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.pdf'
            pdf_file(source)
            writer = PdfWriter(clone_from=source)
            writer.pages[0][_name('/Annots')].append(NumberObject(99))
            writer.pages[2][_name('/Annots')] = NumberObject(7)
            path = Path(folder) / 'malformed.pdf'
            writer.write(path)
            result = TextResult()
            extract_pdf_annotations(PdfReader(path, strict=False), result,
                                    max_pages=100, max_sections=1000, max_text=200000)
            data = result.result()
            self.assertIn('https://supplier.example/PDF_LINK_NEEDLE', data['text'])
            self.assertIn('PDF第 1 页注释 4 不是字典', '\n'.join(data['notices']))
            self.assertIn('PDF第 3 页 /Annots 不是数组', '\n'.join(data['notices']))

    def test_page_and_text_limits_are_shared_with_existing_output(self):
        from pypdf import PdfReader

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'limited.pdf'
            pdf_file(path)
            reader = PdfReader(path, strict=False)
            result = TextResult()
            result.add('第 1 页', '已有正文')
            extract_pdf_annotations(reader, result, max_pages=2, max_sections=3, max_text=200)
            data = result.result()
            self.assertEqual(data['sections'][0]['location'], '第 1 页')
            self.assertIn('第 1 页 链接 1', data['text'])
            self.assertNotIn('第 3 页 链接', data['text'])
            self.assertTrue(data['truncated'])

            short = TextResult()
            extract_pdf_annotations(reader, short, max_pages=100, max_sections=1000, max_text=12)
            self.assertLessEqual(short.characters, 12)
            self.assertTrue(short.truncated)

    def test_pdf_types_and_xyz_null_parameters_are_preserved(self):
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import ArrayObject, DictionaryObject, NullObject, NumberObject, TextStringObject

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.pdf'
            pdf_file(source)
            writer = PdfWriter(clone_from=source)
            second = writer.pages[1]
            bad = _annotation(writer, second, [10, 60, 110, 80], action=DictionaryObject({
                _name('/S'): _name('/URI'), _name('/URI'): NumberObject(123),
            }))
            bad_object = bad.get_object()
            bad_object[_name('/Contents')] = NumberObject(99)
            bad_object[_name('/Rect')] = ArrayObject([TextStringObject('1'), NumberObject(2),
                                                       NumberObject(3), NumberObject(4)])
            _annotation(writer, second, [10, 90, 110, 110], destination=ArrayObject([
                writer.pages[2].indirect_reference, _name('/XYZ'), NullObject(),
                NumberObject(100), NullObject(),
            ]))
            path = Path(folder) / 'typed.pdf'
            writer.write(path)
            result = TextResult()
            extract_pdf_annotations(PdfReader(path, strict=False), result,
                                    max_pages=100, max_sections=1000, max_text=200000)
            data = result.result()
            self.assertIn('URI: 未提供', data['text'])
            self.assertNotIn('URI: 123', data['text'])
            self.assertNotIn('链接说明: 99', data['text'])
            self.assertIn('Rect含不支持的坐标值', '\n'.join(data['notices']))
            self.assertIn('内部目的地: 第 3 页 /XYZ null 100 null', data['text'])

    def test_bad_entries_are_checked_and_bounded_without_output_sections(self):
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import ArrayObject, NumberObject

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.pdf'
            pdf_file(source)
            writer = PdfWriter(clone_from=source)
            writer.pages[0][_name('/Annots')] = ArrayObject([NumberObject(number) for number in range(20)])
            path = Path(folder) / 'bad-entries.pdf'
            writer.write(path)
            result = TextResult()
            extract_pdf_annotations(PdfReader(path, strict=False), result,
                                    max_pages=100, max_sections=2, max_text=200000)
            self.assertEqual(result.sections, [])
            self.assertEqual(len(result.notices), 2)
            self.assertTrue(result.truncated)


if __name__ == '__main__':
    unittest.main()
