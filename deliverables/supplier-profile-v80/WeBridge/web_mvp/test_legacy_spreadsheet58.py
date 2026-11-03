"""Regression coverage for legacy BIFF8 spreadsheet semantics."""
import struct
import tempfile
import unittest
from pathlib import Path

from offline_documents import extract_document
from test_offline_documents import compound_file


def _record(kind, payload=b''):
    return struct.pack('<HH', kind, len(payload)) + payload


def _bof(kind):
    return _record(0x0809, struct.pack('<HHHHII', 0x0600, kind, 0x0DBB, 0x07CC, 0, 6))


def _xf(format_id):
    data = bytearray(20)
    struct.pack_into('<HHH', data, 0, 0, format_id, 0)
    return _record(0x00E0, data)


def _number(row, col, xf_index, value):
    return _record(0x0203, struct.pack('<HHHd', row, col, xf_index, value))


def _bool_error(row, col, xf_index, value, is_error):
    return _record(0x0205, struct.pack('<HHHBB', row, col, xf_index, value, is_error))


def xls_bytes(*, datemode=0, unknown_error=False, include_false=False,
              sparse_dimensions=False, trailing_blank=False):
    """Return an invented BIFF8 workbook used by document/export tests."""
    custom = _record(0x041E, struct.pack('<HHB', 164, 6, 0) + b'000000')
    xfs = b''.join((_xf(0), _xf(14), _xf(22), _xf(10), _xf(164)))
    column_count = 8 if unknown_error or include_false else 7
    dimension_last_row = 20000 if sparse_dimensions else 1
    dimension_last_col = 256 if sparse_dimensions else column_count
    sheet = _bof(0x0010) + _record(0x0200, struct.pack('<IIHHH', 0, dimension_last_row, 0, dimension_last_col, 0))
    sheet += _number(0, 0, 1, 45292.5)
    sheet += _number(0, 1, 2, 45292.5)
    sheet += _number(0, 2, 3, 0.125)
    sheet += _number(0, 3, 4, 123)
    sheet += _bool_error(0, 4, 0, 1, 0)
    sheet += _bool_error(0, 5, 0, 7, 1)
    sheet += _number(0, 6, 1, 60)
    if unknown_error:sheet += _bool_error(0, 7, 0, 99, 1)
    elif include_false:sheet += _bool_error(0, 7, 0, 0, 0)
    if trailing_blank:sheet += _record(0x0201, struct.pack('<HHH', 19999, 255, 0))
    sheet += _record(0x000A)
    prefix = _bof(0x0005) + _record(0x0022, struct.pack('<H', datemode)) + custom + xfs
    name = b'audit'
    bound = _record(0x0085, struct.pack('<IBBBB', len(prefix) + 4 + 8 + len(name) + 4, 0, 0, len(name), 0) + name)
    return prefix + bound + _record(0x000A) + sheet


def xls_file(path, *, datemode=0, unknown_error=False, include_false=False,
             sparse_dimensions=False, trailing_blank=False):
    """Write the synthetic BIFF8 workbook for reuse by export fixtures."""
    compound_file(Path(path), {'Workbook': xls_bytes(datemode=datemode, unknown_error=unknown_error,
                                                      include_false=include_false,
                                                      sparse_dimensions=sparse_dimensions,
                                                      trailing_blank=trailing_blank)})


class LegacySpreadsheet58Tests(unittest.TestCase):
    def test_biff8_cell_types_and_format_codes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'semantics.xls'
            xls_file(path)
            text = extract_document(path, '.xls')['text']
        self.assertIn('A1=2024-01-01', text)
        self.assertNotIn('A1=2024-01-01T', text)
        self.assertIn('B1=2024-01-01T12:00:00', text)
        self.assertIn('C1=12.50%', text)
        self.assertIn('D1=000123', text)
        self.assertIn('E1=TRUE', text)
        self.assertIn('F1=#DIV/0!', text)
        self.assertIn('G1=60', text)
        self.assertIn('无法真实表示', text)

    def test_1904_datemode_and_unknown_error_remain_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'semantics.xls'
            xls_file(path, datemode=1, unknown_error=True)
            text = extract_document(path, '.xls')['text']
        self.assertIn('A1=2028-01-02', text)
        self.assertIn('G1=1904-03-01', text)
        self.assertIn('H1=#ERROR(99)', text)
        self.assertIn('错误码: 99', text)

    def test_false_boolean_comes_from_biff_cell(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'semantics.xls'
            xls_file(path, include_false=True)
            text = extract_document(path, '.xls')['text']
        self.assertIn('H1=FALSE', text)

    def test_sparse_trailing_blank_keeps_original_rows_and_columns(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'sparse.xls'
            xls_file(path, sparse_dimensions=True, trailing_blank=True)
            result = extract_document(path, '.xls')
        self.assertEqual(len(result['sections']), 1)
        self.assertIn('A1=2024-01-01', result['text'])
        self.assertIn('G1=60', result['text'])


if __name__ == '__main__':
    unittest.main()
