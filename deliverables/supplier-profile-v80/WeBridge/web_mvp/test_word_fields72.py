import tempfile
import unittest
import zipfile
from pathlib import Path

from offline_documents import extract_document
from offline_word_fields import FieldLinks, hyperlink_target


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx(path, body):
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)
    return path


class PublicWordFieldExtraction72Tests(unittest.TestCase):
    def test_header_field_keeps_its_part_location_and_does_not_replace_body(self):
        relationships = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
        package_relationships = 'http://schemas.openxmlformats.org/package/2006/relationships'
        body = ('<w:p><w:r><w:t>正文保留</w:t></w:r></w:p>'
                '<w:sectPr><w:headerReference xmlns:r="' + relationships + '" r:id="header"/></w:sectPr>')
        with tempfile.TemporaryDirectory(prefix="word-header-fields72-") as folder:
            path = _docx(Path(folder) / 'header.docx', body)
            with zipfile.ZipFile(path, 'a') as archive:
                archive.writestr('word/_rels/document.xml.rels',
                    '<Relationships xmlns="' + package_relationships + '"><Relationship Id="header" '
                    'Type="' + relationships + '/header" Target="header1.xml"/></Relationships>')
                archive.writestr('word/header1.xml', '<w:hdr xmlns:w="' + W + '"><w:p>'
                    '<w:fldSimple w:instr=\' HYPERLINK "https://example.test/header" \'>'
                    '<w:r><w:t>页眉条款</w:t></w:r></w:fldSimple></w:p></w:hdr>')
            result = extract_document(path, '.docx')
        self.assertEqual(result['sections'], [
            {'location': '段落 1', 'text': '正文保留'},
            {'location': 'word/header1.xml', 'text': '页眉条款 [链接: https://example.test/header]'},
        ])

    def test_public_extract_document_keeps_simple_field_text_and_link_target(self):
        body = (
            '<w:p><w:fldSimple w:instr=\' HYPERLINK "https://example.test/policy" \'>'
            '<w:r><w:t>Policy</w:t></w:r></w:fldSimple></w:p>'
        )
        with tempfile.TemporaryDirectory(prefix="word-fields72-") as folder:
            result = extract_document(_docx(Path(folder) / "simple.docx", body), ".docx")
        self.assertIn("Policy [链接: https://example.test/policy]", result["text"])

    def test_public_extract_document_keeps_complex_field_text_and_link_target(self):
        body = (
            '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText xml:space="preserve"> HYPER</w:instrText></w:r>'
            '<w:r><w:instrText xml:space="preserve">LINK "https://example.test/a b" </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            '<w:r><w:t>Policy</w:t></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
        )
        with tempfile.TemporaryDirectory(prefix="word-fields72-") as folder:
            result = extract_document(_docx(Path(folder) / "complex.docx", body), ".docx")
        self.assertIn("Policy [链接: https://example.test/a b]", result["text"])


class WordFieldParser72Tests(unittest.TestCase):
    def test_hyperlink_target_is_case_insensitive_and_keeps_spaces(self):
        self.assertEqual(
            hyperlink_target('  hyperlink "https://example.test/a b"  '),
            "https://example.test/a b",
        )

    def test_hyperlink_target_preserves_windows_path_and_bookmark(self):
        self.assertEqual(
            hyperlink_target('HYPERLINK "C:\\Policy Files\\review.docx" \\l "Section 2"'),
            r"C:\Policy Files\review.docx#Section 2",
        )
        self.assertEqual(hyperlink_target(r'HYPERLINK \l "内部书签"'), "#内部书签")

    def test_hyperlink_target_ignores_optional_metadata_switches(self):
        self.assertEqual(
            hyperlink_target(
                'HYPERLINK "https://example.test/policy" '
                '\\o "Policy tooltip with spaces" \\t "_blank" \\l "top"'
            ),
            "https://example.test/policy#top",
        )

    def test_unknown_or_incomplete_instruction_is_not_guessed(self):
        self.assertEqual(hyperlink_target('MERGEFIELD "https://example.test/no"'), "")
        self.assertEqual(hyperlink_target('HYPERLINK "https://example.test/no'), "")
        self.assertEqual(hyperlink_target('HYPERLINK'), "")

    def test_complex_field_returns_marker_only_after_separate_and_end(self):
        fields = FieldLinks()
        self.assertEqual(fields.control("begin"), "")
        fields.instruction(" HYPER")
        fields.instruction('LINK "https://example.test/policy" ')
        self.assertEqual(fields.control("end"), "")

        fields = FieldLinks()
        fields.control("begin")
        fields.instruction(" HYPER")
        fields.instruction('LINK "https://example.test/policy" ')
        self.assertEqual(fields.control("separate"), "")
        fields.instruction(' MERGEFIELD "https://example.test/wrong" ')
        self.assertEqual(fields.control("end"), " [链接: https://example.test/policy]")

    def test_nested_non_hyperlink_field_does_not_change_outer_link(self):
        fields = FieldLinks()
        fields.control("begin")
        fields.instruction(' HYPERLINK "https://example.test/outer" ')
        fields.control("separate")
        fields.control("begin")
        fields.instruction(' DATE "yyyy" ')
        fields.control("separate")
        self.assertEqual(fields.control("end"), "")
        self.assertEqual(fields.control("end"), " [链接: https://example.test/outer]")


if __name__ == "__main__":
    unittest.main()
