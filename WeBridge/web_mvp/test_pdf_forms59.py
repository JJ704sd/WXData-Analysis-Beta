"""Regression coverage for stored PDF AcroForm values."""
from pathlib import Path
import tempfile
import unittest

from offline_documents import extract_document


FORM_CUSTOMER_NEEDLE = 'FORM_CUSTOMER_NEEDLE'


def _name(value):
    from pypdf.generic import NameObject
    return NameObject(value)


def _field(writer, page, field_type, field_name, value, *, options=None, parent=None, flags=None, alternate_name=None):
    from pypdf.generic import ArrayObject, DictionaryObject, NumberObject, TextStringObject
    data = {
        _name('/Type'): _name('/Annot'), _name('/Subtype'): _name('/Widget'),
        _name('/Rect'): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(100), NumberObject(20)]),
        _name('/P'): page.indirect_reference,
    }
    if field_name is not None:
        data[_name('/T')] = TextStringObject(field_name)
    if alternate_name is not None:
        data[_name('/TU')] = TextStringObject(alternate_name)
    if field_type is not None:
        data[_name('/FT')] = _name(field_type)
    if value is not None:
        data[_name('/V')] = value
    if options is not None:
        data[_name('/Opt')] = options
    if parent is not None:
        data[_name('/Parent')] = parent
    if flags is not None:
        data[_name('/Ff')] = NumberObject(flags)
    reference = writer._add_object(DictionaryObject(data))
    page.setdefault(_name('/Annots'), ArrayObject()).append(reference)
    return reference


def pdf_file(path, text=FORM_CUSTOMER_NEEDLE):
    """Create a small filled form used by the four-entry export fixtures."""
    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, DictionaryObject, TextStringObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=300)
    fields = []
    fields.append(_field(writer, page, '/Tx', 'customer', TextStringObject(text), alternate_name='客户名称'))
    fields.append(_field(writer, page, '/Ch', 'stage', TextStringObject('approved'), options=ArrayObject([
        ArrayObject([TextStringObject('draft'), TextStringObject('草稿')]),
        ArrayObject([TextStringObject('approved'), TextStringObject('已批准')]),
    ])))
    fields.append(_field(writer, page, '/Btn', 'urgent', _name('/Yes')))
    parent = DictionaryObject({_name('/FT'): _name('/Tx'), _name('/T'): TextStringObject('parent'),
                               _name('/V'): TextStringObject('继承值')})
    parent_ref = writer._add_object(parent)
    child = _field(writer, page, None, 'child', None, parent=parent_ref)
    parent[_name('/Kids')] = ArrayObject([child])
    fields.append(parent_ref)
    writer._root_object[_name('/AcroForm')] = DictionaryObject({_name('/Fields'): ArrayObject(fields)})
    with Path(path).open('wb') as stream:
        writer.write(stream)


class PdfForms59Tests(unittest.TestCase):
    def test_filled_fields_are_extracted_and_default_is_not(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'filled.pdf'
            pdf_file(path)
            result = extract_document(path, '.pdf')
            self.assertIn(FORM_CUSTOMER_NEEDLE, result['text'])
            self.assertIn('客户名称', result['text'])
            self.assertIn('approved', result['text'])
            self.assertIn('已批准', result['text'])
            self.assertIn('urgent', result['text'])
            self.assertIn('继承值', result['text'])
            self.assertNotIn('template-only', result['text'])

    def test_choice_multi_off_password_signature_and_xfa_are_bounded(self):
        from pypdf import PdfWriter
        from pypdf.generic import ArrayObject, ByteStringObject, DictionaryObject, NumberObject, TextStringObject
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'fields.pdf'
            writer = PdfWriter()
            page = writer.add_blank_page(width=400, height=300)
            choices = ArrayObject([ArrayObject([TextStringObject('a'), TextStringObject('甲')]),
                                   ArrayObject([TextStringObject('b'), TextStringObject('乙')])])
            multi = _field(writer, page, '/Ch', 'multi', ArrayObject([TextStringObject('a'), TextStringObject('b')]), options=choices)
            off = _field(writer, page, '/Btn', 'off', _name('/Off'))
            password = _field(writer, page, '/Tx', 'secret', TextStringObject('DO_NOT_EXPORT'), flags=1 << 13)
            signature = _field(writer, page, '/Sig', 'signature', DictionaryObject({
                _name('/Type'): _name('/Sig'), _name('/Contents'): ByteStringObject(b'SIGNED_VALUE')
            }))
            unknown = _field(writer, page, '/Tx', 'unknown-bytes', ByteStringObject(bytes([0x00, 0x9f, 0xad])))
            action = DictionaryObject({_name('/S'): _name('/JavaScript'), _name('/JS'): TextStringObject('DO_NOT_RUN')})
            page[_name('/AA')] = DictionaryObject({_name('/O'): action})
            writer._root_object[_name('/AcroForm')] = DictionaryObject({
                _name('/Fields'): ArrayObject([multi, off, password, signature, unknown]),
                _name('/XFA'): TextStringObject('dynamic-data'),
            })
            with path.open('wb') as stream:
                writer.write(stream)
            result = extract_document(path, '.pdf')
            self.assertIn('甲', result['text']); self.assertIn('乙', result['text'])
            self.assertIn('未选', result['text'])
            self.assertIn('密码字段已填写', result['text'])
            self.assertNotIn('DO_NOT_EXPORT', result['text'])
            self.assertNotIn('SIGNED_VALUE', result['text'])
            self.assertNotIn('DO_NOT_RUN', result['text'])
            self.assertTrue(any('XFA' in notice for notice in result['notices']))
            self.assertTrue(any('签名字段' in notice for notice in result['notices']))
            self.assertTrue(any('unknown-bytes' in notice for notice in result['notices']))

    def test_page_association_and_pages_beyond_bound(self):
        from pypdf import PdfWriter
        from pypdf.generic import ArrayObject, DictionaryObject, TextStringObject
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'pages.pdf'
            writer = PdfWriter()
            first = writer.add_blank_page(width=400, height=300)
            second = writer.add_blank_page(width=400, height=300)
            parent = DictionaryObject({_name('/FT'): _name('/Tx'), _name('/T'): TextStringObject('repeat'),
                                       _name('/V'): TextStringObject('ONCE')})
            parent_ref = writer._add_object(parent)
            kid1 = _field(writer, first, None, None, None, parent=parent_ref)
            kid2 = _field(writer, second, None, None, None, parent=parent_ref)
            parent[_name('/Kids')] = ArrayObject([kid1, kid2])
            for _ in range(99):
                writer.add_blank_page(width=400, height=300)
            late = writer.add_blank_page(width=400, height=300)
            late_field = _field(writer, late, '/Tx', 'late', TextStringObject('LATE_VALUE'))
            writer._root_object[_name('/AcroForm')] = DictionaryObject({_name('/Fields'): ArrayObject([parent_ref, late_field])})
            with path.open('wb') as stream:
                writer.write(stream)
            result = extract_document(path, '.pdf')
            self.assertEqual(result['text'].count('ONCE'), 1)
            self.assertIn('第 1、2 页', result['text'])
            self.assertNotIn('LATE_VALUE', result['text'])
            self.assertTrue(result['truncated'])

    def test_named_siblings_with_same_value_are_kept(self):
        from pypdf import PdfWriter
        from pypdf.generic import ArrayObject, DictionaryObject, TextStringObject
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'siblings.pdf'
            writer = PdfWriter(); page = writer.add_blank_page(width=400, height=300)
            first = _field(writer, page, '/Tx', 'billing.name', TextStringObject('同值'))
            second = _field(writer, page, '/Tx', 'shipping.name', TextStringObject('同值'))
            writer._root_object[_name('/AcroForm')] = DictionaryObject({
                _name('/Fields'): ArrayObject([first, second])
            })
            with path.open('wb') as stream:
                writer.write(stream)
            result = extract_document(path, '.pdf')
            self.assertEqual(result['text'].count('同值'), 2)

    def test_text_limit_is_shared_with_form_sections(self):
        import offline_documents
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'limited.pdf'
            pdf_file(path, text='1234567890')
            old_limit = offline_documents.MAX_TEXT
            offline_documents.MAX_TEXT = 8
            try:
                result = extract_document(path, '.pdf')
            finally:
                offline_documents.MAX_TEXT = old_limit
            self.assertLessEqual(result['characters'], 8)
            self.assertTrue(result['truncated'])

    def test_malformed_form_metadata_keeps_native_text(self):
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import ArrayObject, DictionaryObject, NumberObject, TextStringObject
        from test_offline_documents import pdf_file as native_pdf_file
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.pdf'
            native_pdf_file(source, 'NATIVE_BODY_MUST_SURVIVE')
            for name, fields in (
                ('malformed', ArrayObject([NumberObject(12)])),
                ('invalid-flags', None),
            ):
                writer = PdfWriter(clone_from=source)
                if fields is None:
                    field = _field(writer, writer.pages[0], '/Tx', 'review', TextStringObject('TEST_FIELD'))
                    field.get_object()[_name('/Ff')] = TextStringObject('invalid')
                    fields = ArrayObject([field])
                writer._root_object[_name('/AcroForm')] = DictionaryObject({_name('/Fields'): fields})
                output = Path(folder) / (name + '.pdf')
                with output.open('wb') as stream:
                    writer.write(stream)
                result = extract_document(output, '.pdf')
                self.assertIn('NATIVE_BODY_MUST_SURVIVE', result['text'])
                self.assertEqual(result['extractionStatus'], 'partial')

    def test_null_form_is_absent_and_invalid_form_preserves_native_text(self):
        from pypdf import PdfWriter
        from pypdf.generic import NullObject, NumberObject
        from test_offline_documents import pdf_file as native_pdf_file
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.pdf'
            native_pdf_file(source, 'NATIVE_BODY_MUST_SURVIVE')
            for value, expected in ((NullObject(), 'extracted'), (NumberObject(12), 'partial')):
                writer = PdfWriter(clone_from=source)
                writer._root_object[_name('/AcroForm')] = value
                output = Path(folder) / (expected + '.pdf')
                writer.write(output)
                result = extract_document(output, '.pdf')
                self.assertIn('NATIVE_BODY_MUST_SURVIVE', result['text'])
                self.assertEqual(result['extractionStatus'], expected)

    def test_empty_fields_do_not_bypass_the_field_count_limit(self):
        from pypdf import PdfWriter
        from pypdf.generic import ArrayObject, DictionaryObject, TextStringObject
        from test_offline_documents import pdf_file as native_pdf_file
        from offline_documents import MAX_SECTIONS
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'source.pdf'
            native_pdf_file(source, 'NATIVE_BODY_MUST_SURVIVE')
            writer = PdfWriter(clone_from=source)
            fields = [_field(writer, writer.pages[0], '/Tx', 'empty-'+str(number), None)
                      for number in range(MAX_SECTIONS)]
            fields.append(_field(writer, writer.pages[0], '/Tx', 'beyond-limit',
                                 TextStringObject('BEYOND_FIELD_LIMIT')))
            writer._root_object[_name('/AcroForm')] = DictionaryObject({_name('/Fields'): ArrayObject(fields)})
            output = Path(folder) / 'bounded.pdf'
            writer.write(output)
            result = extract_document(output, '.pdf')
            self.assertTrue(result['truncated'])
            self.assertIn('NATIVE_BODY_MUST_SURVIVE', result['text'])
            self.assertNotIn('BEYOND_FIELD_LIMIT', result['text'])


if __name__ == '__main__':
    unittest.main()
