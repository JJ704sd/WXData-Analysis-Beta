"""Regression coverage for BIFF8 hyperlink and note metadata."""

import struct
import tempfile
import unittest
from pathlib import Path

from offline_documents import TextResult, extract_document
from offline_legacy_spreadsheet_annotations import extract_xls_annotations
from offline_spreadsheet_visibility import xls_visibility
from test_offline_documents import compound_file


def _record(kind, payload=b''):
    return struct.pack('<HH', kind, len(payload)) + payload


def _bof(kind):
    return _record(0x0809, struct.pack('<HHHHII', 0x0600, kind, 0x0DBB, 0x07CC, 0, 6))


def _unicode_nul(value):
    return struct.pack('<I', len(value) + 1) + (value + '\0').encode('utf-16-le')


def _hlink(row, first_col, last_col, display, target, *, kind='url', textmark=''):
    hyperlink_guid = bytes.fromhex('D0C9EA79F9BACE118C8200AA004BA90B')
    url_guid = bytes.fromhex('E0C9EA79F9BACE118C8200AA004BA90B')
    payload = (struct.pack('<4H', row, row, first_col, last_col) + hyperlink_guid
               + b'\x02\0\0\0')
    if kind == 'url':
        options = 0x15 | (0x8 if textmark else 0)
        payload += struct.pack('<i', options) + _unicode_nul(display) + url_guid
        target_bytes = (target + '\0').encode('utf-16-le')
        payload += struct.pack('<I', len(target_bytes)) + target_bytes
    elif kind == 'workbook':
        options = 0x1C
        payload += struct.pack('<i', options) + _unicode_nul(display) + _unicode_nul(textmark)
    else:
        options = 0x15
        payload += struct.pack('<i', options) + _unicode_nul(display)
        file_guid = bytes.fromhex('0303000000000000C000000000000046')
        short_path = b'LINK.TXT'
        extended = target.encode('utf-16-le')
        payload += file_guid + struct.pack('<Hi', 0, len(short_path) + 1)
        payload += short_path + b'\0' + bytes(24)
        payload += struct.pack('<i', 1) + struct.pack('<i', len(extended))
        payload += b'\x03\0' + extended
    if kind == 'url' and textmark:
        payload += _unicode_nul(textmark)
    return _record(0x01B8, payload)


def _quicktip(row, first_col, last_col, text):
    payload = struct.pack('<5H', 0x0800, row, row, first_col, last_col)
    return _record(0x0800, payload + text.encode('utf-16-le') + b'\0\0')


def _comment_records(row, col, text, author, object_id):
    obj = _record(0x005D, struct.pack('<HHHHH', 0x0015, 18, 0x0019, object_id, 0) + bytes(12))
    txo = _record(0x01B6, struct.pack('<HH6sHHH', 0, 0, bytes(6), len(text), 0, 0))
    continued = _record(0x003C, b'\0' + text.encode('latin1'))
    note = (_record(0x001C, struct.pack('<4H', row, col, 0, object_id)
                   + struct.pack('<H', len(author)) + b'\0' + author.encode('latin1')))
    return obj + txo + continued + note


def _label(row, col, value):
    raw = value.encode('latin1')
    return _record(0x0204, struct.pack('<HHHHB', row, col, 0, len(raw), 0) + raw)


def _row(row, hidden):
    flags = 0x20 if hidden else 0
    return _record(0x0208, struct.pack('<HHHHHHHH', row, 0, 3, 0, 0, 0, flags, 0))


def _colinfo(first, last, hidden):
    return _record(0x007D, struct.pack('<HHHHHH', first, last, 2048, 0,
                                       1 if hidden else 0, 0))


def _xls_bytes(*, empty_author=False):
    sheet = _bof(0x0010) + _record(0x0200, struct.pack('<IIHHH', 0, 5, 0, 5, 0))
    sheet += _colinfo(1, 1, True) + _row(1, True)
    sheet += _label(0, 0, 'URL') + _label(0, 1, 'LOCAL') + _label(1, 0, 'WORKBOOK')
    sheet += _hlink(0, 0, 1, 'URL_DISPLAY', 'https://example.test/legacy', textmark='part-2')
    sheet += _quicktip(0, 0, 1, 'URL_TOOLTIP')
    sheet += _hlink(0, 1, 1, 'LOCAL_DISPLAY', r'C:\archive\source.txt', kind='local')
    sheet += _hlink(1, 0, 0, 'WORKBOOK_DISPLAY', '', kind='workbook', textmark='Sheet2!B7')
    sheet += _comment_records(2, 1, 'NOTE_BODY', '' if empty_author else 'NOTE_AUTHOR', 1)
    sheet += _record(0x000A)
    prefix = _bof(0x0005)
    name = b'audit'
    bound = _record(0x0085, struct.pack('<IBBBB', len(prefix) + 4 + 8 + len(name) + 4, 1, 0, len(name), 0) + name)
    return prefix + bound + _record(0x000A) + sheet


def xls_file(path, *, empty_author=False):
    """Write the real CFB/BIFF8 fixture used by annotation and four-link tests."""
    compound_file(Path(path), {'Workbook': _xls_bytes(empty_author=empty_author)})
    return path


def _sheet(path, *, empty_author=False):
    xls_file(path, empty_author=empty_author)
    import xlrd
    book = xlrd.open_workbook(path, formatting_info=True, on_demand=True)
    return book, book.sheet_by_index(0)


class LegacyAnnotations65Tests(unittest.TestCase):
    def test_real_biff8_links_keep_ranges_targets_marks_display_and_tooltip(self):
        with tempfile.TemporaryDirectory() as folder:
            book, sheet = _sheet(Path(folder) / 'links.xls')
            out = TextResult()
            visibility = xls_visibility(sheet)
            extract_xls_annotations(sheet, visibility, out)
            result = out.result()
            book.release_resources()
        self.assertIn('A1:B1', result['text'])
        self.assertIn('https://example.test/legacy', result['text'])
        self.assertIn('文本标记：part-2', result['text'])
        self.assertIn('显示文字：URL_DISPLAY', result['text'])
        self.assertIn('悬停提示：URL_TOOLTIP', result['text'])
        self.assertIn(r'C:\archive\source.txt', result['text'])
        self.assertIn('类型：workbook', result['text'])
        self.assertIn('Sheet2!B7', result['text'])
        self.assertIn('[隐藏列]', result['text'])
        self.assertIn('[隐藏行]', result['text'])
        self.assertIn('隐藏工作表 state=hidden', visibility.sheet_name)

    def test_note_keeps_body_and_empty_author(self):
        with tempfile.TemporaryDirectory() as folder:
            book, sheet = _sheet(Path(folder) / 'notes.xls', empty_author=True)
            out = TextResult()
            extract_xls_annotations(sheet, xls_visibility(sheet), out)
            result = out.result()
            book.release_resources()
        self.assertIn('批注 B3', result['text'])
        self.assertIn('作者：（空）', result['text'])
        self.assertIn('正文：NOTE_BODY', result['text'])

    def test_writer_limit_stops_annotation_scan(self):
        with tempfile.TemporaryDirectory() as folder:
            book, sheet = _sheet(Path(folder) / 'limited.xls')
            out = TextResult()
            out.characters = 199990
            extract_xls_annotations(sheet, xls_visibility(sheet), out)
            result = out.result()
            book.release_resources()
        self.assertTrue(result['truncated'])
        self.assertNotIn('LOCAL_DISPLAY', result['text'])

    def test_extract_document_includes_legacy_annotations_after_integration(self):
        with tempfile.TemporaryDirectory() as folder:
            result = extract_document(xls_file(Path(folder) / 'integrated.xls'), '.xls')
        self.assertIn('https://example.test/legacy', result['text'])
        self.assertIn('文本标记：part-2', result['text'])
        self.assertIn('NOTE_AUTHOR', result['text'])
        self.assertIn('NOTE_BODY', result['text'])


if __name__ == '__main__':
    unittest.main()
