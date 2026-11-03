"""Regression coverage for stored PDF comment annotations."""

from pathlib import Path
import tempfile
import unittest

from offline_documents import TextResult
from offline_pdf_links import extract_pdf_annotations


PDF_COMMENT_NEEDLE = 'PDF_COMMENT_NEEDLE'
PDF_REPLY_NEEDLE = 'PDF_REPLY_NEEDLE'


def _name(value):
    from pypdf.generic import NameObject
    return NameObject(value)


def _text_stream(writer, page, text):
    from pypdf.generic import DecodedStreamObject, DictionaryObject

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


def _comment(writer, page, subtype, contents, number, *, reply_to=None, rich_text=None, flags=35):
    from pypdf.generic import ArrayObject, DictionaryObject, NumberObject, TextStringObject

    data = {
        _name('/Type'): _name('/Annot'),
        _name('/Subtype'): _name(subtype),
        _name('/Rect'): ArrayObject([NumberObject(10 + number), NumberObject(210),
                                     NumberObject(40 + number), NumberObject(230)]),
        _name('/T'): TextStringObject('Author' + str(number)),
        _name('/M'): TextStringObject("D:2026102512" + str(number).zfill(2) + "00+08'00'"),
        _name('/CreationDate'): TextStringObject("D:2026102511" + str(number).zfill(2) + "00+08'00'"),
        _name('/Subj'): TextStringObject('Subject' + str(number)),
        _name('/NM'): TextStringObject('comment-' + str(number)),
        _name('/State'): _name('/Completed'),
        _name('/StateModel'): _name('/Review'),
        _name('/F'): NumberObject(flags),
        _name('/P'): page.indirect_reference,
    }
    if contents is not None:
        data[_name('/Contents')] = TextStringObject(contents)
    if rich_text is not None:
        data[_name('/RC')] = TextStringObject(rich_text)
    if subtype == '/Highlight':
        data[_name('/QuadPoints')] = ArrayObject([
            NumberObject(10), NumberObject(220), NumberObject(110), NumberObject(220),
            NumberObject(10), NumberObject(205), NumberObject(110), NumberObject(205),
        ])
    if reply_to is not None:
        data[_name('/IRT')] = reply_to
        data[_name('/RT')] = _name('/R')
    reference = writer._add_object(DictionaryObject(data))
    page.setdefault(_name('/Annots'), ArrayObject()).append(reference)
    return reference


def pdf_file(path, text='PDF comment body'):
    """Create a PDF with stored comments, a link, a widget, and ordinary text."""
    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, DictionaryObject, NumberObject, TextStringObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=300)
    _text_stream(writer, page, text)
    original = _comment(writer, page, '/Text', PDF_COMMENT_NEEDLE, 1)
    for number, subtype in enumerate(('/FreeText', '/Highlight', '/Underline', '/Squiggly',
                                      '/StrikeOut', '/Caret'), 2):
        _comment(writer, page, subtype, subtype[1:] + ' stored comment', number)
    _comment(writer, page, '/Text', PDF_REPLY_NEEDLE, 8, reply_to=original)
    _comment(writer, page, '/FreeText', None, 9, rich_text='<body>rich text only</body>')
    link = DictionaryObject({
        _name('/Type'): _name('/Annot'), _name('/Subtype'): _name('/Link'),
        _name('/Rect'): ArrayObject([NumberObject(10), NumberObject(150), NumberObject(110), NumberObject(170)]),
        _name('/A'): DictionaryObject({
            _name('/S'): _name('/URI'),
            _name('/URI'): TextStringObject('https://supplier.example/comments63'),
        }),
    })
    page.setdefault(_name('/Annots'), ArrayObject()).append(writer._add_object(link))
    widget = DictionaryObject({
        _name('/Type'): _name('/Annot'), _name('/Subtype'): _name('/Widget'),
        _name('/Rect'): ArrayObject([NumberObject(10), NumberObject(120), NumberObject(110), NumberObject(140)]),
        _name('/P'): page.indirect_reference, _name('/FT'): _name('/Tx'),
        _name('/T'): TextStringObject('commentReference'), _name('/V'): TextStringObject('FORM_VALUE_63'),
    })
    widget_reference = writer._add_object(widget)
    page[_name('/Annots')].append(widget_reference)
    writer._root_object[_name('/AcroForm')] = DictionaryObject({
        _name('/Fields'): ArrayObject([widget_reference]),
    })
    with Path(path).open('wb') as stream:
        writer.write(stream)


class PdfComments63Tests(unittest.TestCase):
    def test_comment_types_metadata_flags_and_same_page_reply(self):
        from pypdf import PdfReader

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'comments.pdf'
            pdf_file(path)
            result = TextResult()
            emitted = extract_pdf_annotations(PdfReader(path, strict=False), result,
                                              max_pages=100, max_sections=1000, max_text=200000)
            data = result.result()
            self.assertEqual(emitted, 10)
            self.assertEqual(data['text'].count(PDF_COMMENT_NEEDLE), 1)
            self.assertEqual(data['text'].count(PDF_REPLY_NEEDLE), 1)
            for subtype in ('Text', 'FreeText', 'Highlight', 'Underline', 'Squiggly', 'StrikeOut', 'Caret'):
                self.assertIn('PDF批注 第 1 页 · 注释', data['text'])
                self.assertIn('· ' + subtype, data['text'])
            self.assertIn('作者/T: Author1', data['text'])
            self.assertIn('修改日期/M: D:20261025120100+08\'00\'', data['text'])
            self.assertIn('创建日期/CreationDate: D:20261025110100+08\'00\'', data['text'])
            self.assertIn('主题/Subj: Subject1', data['text'])
            self.assertIn('标识/NM: comment-1', data['text'])
            self.assertIn('状态/State: Completed', data['text'])
            self.assertIn('状态模型/StateModel: Review', data['text'])
            self.assertIn('文件声明标志（原始）: F=35；Invisible=已声明；Hidden=已声明；NoView=已声明', data['text'])
            self.assertIn('回复关系: 同页注释 1；RT=R', data['text'])
            self.assertIn('https://supplier.example/comments63', data['text'])
            self.assertNotIn('FORM_VALUE_63', data['text'])

            from offline_documents import extract_document
            document = extract_document(path, '.pdf')
            document_text = document['text']
            self.assertEqual(document_text.count(PDF_COMMENT_NEEDLE), 1)
            self.assertEqual(document_text.count(PDF_REPLY_NEEDLE), 1)
            self.assertEqual(document_text.count('https://supplier.example/comments63'), 1)
            self.assertEqual(document_text.count('FORM_VALUE_63'), 1)
            locations = [section['location'] for section in document['sections']]
            self.assertEqual(locations[0], '第 1 页')
            self.assertTrue(locations[1].startswith('PDF表单 第 1 页'))
            self.assertTrue(locations[2].startswith('PDF批注 第 1 页'))
            self.assertTrue(locations[-1].startswith('第 1 页 链接'))

    def test_rich_text_without_contents_is_reported_without_expansion(self):
        from pypdf import PdfReader

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'comments.pdf'
            pdf_file(path)
            result = TextResult()
            extract_pdf_annotations(PdfReader(path, strict=False), result,
                                    max_pages=100, max_sections=1000, max_text=200000)
            data = result.result()
            self.assertNotIn('rich text only', data['text'])
            self.assertTrue(any('只有RC富文本而无Contents' in notice for notice in data['notices']))

    def test_invalid_flag_and_unsupported_contents_keep_comment_body(self):
        from pypdf import PdfWriter
        from pypdf.generic import ByteStringObject, FloatObject, NameObject
        from offline_documents import extract_document

        with tempfile.TemporaryDirectory() as folder:
            for case in ('fractional-flags', 'unsupported-contents'):
                writer = PdfWriter()
                page = writer.add_blank_page(width=400, height=300)
                _text_stream(writer, page, 'COMMENT_NATIVE_BODY')
                reference = _comment(writer, page, '/Text', 'COMMENT_BODY_MUST_SURVIVE', 1)
                if case == 'fractional-flags':
                    reference.get_object()[NameObject('/F')] = FloatObject('1.5')
                else:
                    reference.get_object()[NameObject('/Contents')] = ByteStringObject(b'\x00\x9f\xad')
                path = Path(folder) / (case + '.pdf')
                with path.open('wb') as stream:
                    writer.write(stream)
                result = extract_document(path, '.pdf')
                self.assertIn('COMMENT_NATIVE_BODY', result['text'])
                self.assertEqual(result['extractionStatus'], 'partial')
                if case == 'fractional-flags':
                    self.assertIn('COMMENT_BODY_MUST_SURVIVE', result['text'])
                    self.assertTrue(any('F标志值类型未支持' in notice for notice in result['notices']))
                else:
                    self.assertNotIn('COMMENT_BODY_MUST_SURVIVE', result['text'])
                    self.assertTrue(any('Contents类型未支持' in notice for notice in result['notices']))
                    self.assertNotIn('\\x9f', result['text'])

    def test_empty_contents_is_valid_and_rc_only_stays_a_gap(self):
        from pypdf import PdfReader, PdfWriter

        with tempfile.TemporaryDirectory() as folder:
            for case, rich_text in (('empty', None), ('empty-with-rc', '<body>rich text</body>')):
                writer = PdfWriter()
                page = writer.add_blank_page(width=400, height=300)
                _text_stream(writer, page, 'EMPTY_CONTENTS_NATIVE_BODY')
                _comment(writer, page, '/Text', '', 1, rich_text=rich_text)
                path = Path(folder) / (case + '.pdf')
                with path.open('wb') as stream:
                    writer.write(stream)
                result = TextResult()
                extract_pdf_annotations(PdfReader(path, strict=False), result,
                                        max_pages=100, max_sections=100, max_text=200000)
                data = result.result()
                self.assertNotIn('Contents类型未支持', data['notices'])
                if rich_text is None:
                    self.assertEqual(data['notices'], [])
                else:
                    self.assertTrue(any('只有RC富文本而无Contents' in notice
                                        for notice in data['notices']))

    def test_annotation_budget_counts_links_comments_and_non_link_entries(self):
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import ArrayObject, NumberObject

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'comments.pdf'
            pdf_file(source)
            writer = PdfWriter(clone_from=source)
            writer.pages[0][_name('/Annots')] = ArrayObject([NumberObject(number) for number in range(20)])
            path = Path(folder) / 'bounded.pdf'
            writer.write(path)
            result = TextResult()
            extract_pdf_annotations(PdfReader(path, strict=False), result,
                                    max_pages=100, max_sections=2, max_text=200000)
            self.assertEqual(result.sections, [])
            self.assertEqual(len(result.notices), 2)
            self.assertTrue(result.truncated)

    def test_forward_and_direct_invalid_replies_are_not_guessed(self):
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import ArrayObject, NumberObject

        with tempfile.TemporaryDirectory() as folder:
            writer = PdfWriter()
            page = writer.add_blank_page(width=400, height=300)
            _text_stream(writer, page, 'PDF reply body')
            parent = _comment(writer, page, '/Text', 'forward parent', 1)
            child = _comment(writer, page, '/Text', 'forward child', 2, reply_to=parent)
            invalid = _comment(writer, page, '/Text', 'direct invalid ref', 3,
                               reply_to=NumberObject(999))
            page[_name('/Annots')] = ArrayObject([child, parent, invalid])
            path = Path(folder) / 'reply-order.pdf'
            writer.write(path)
            result = TextResult()
            extract_pdf_annotations(PdfReader(path, strict=False), result,
                                    max_pages=100, max_sections=100, max_text=200000)
            text = result.result()['text']
            self.assertIn('回复关系: 同页注释 2；RT=R', text)
            self.assertIn('回复关系: 未定位（仅同页原始注释索引）；RT=R', text)


if __name__ == '__main__':
    unittest.main()
