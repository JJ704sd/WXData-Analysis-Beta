"""Visibility metadata for the two spreadsheet formats supported offline.

The document worker reads cell values and delegates only the display of
visibility metadata to this module.  OOXML stores column ranges directly;
xlrd exposes BIFF column ranges as a sparse map.  Both are compressed here so
that a worksheet-wide hidden range is represented once and queried without
materialising every column.
"""
from bisect import bisect_right
import re


_HIDDEN = frozenset(("1", "true", "on"))
_CELL_REF = re.compile(r"^\$?([A-Za-z]+)\$?\d+$")
_S = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
_STATE_LABELS = {
    "hidden": "隐藏工作表 state=hidden",
    "veryHidden": "隐藏工作表 state=veryHidden",
}


def _compress(intervals):
    """Return sorted, adjacent inclusive intervals."""
    ordered = []
    for start, end in intervals:
        start, end = int(start), int(end)
        if start < 1 or end < start:
            raise ValueError('visibility interval must be positive and ordered')
        ordered.append((start, end))
    ordered.sort()
    if not ordered:
        return ()
    merged = [list(ordered[0])]
    for start, end in ordered[1:]:
        current = merged[-1]
        if start <= current[1] + 1:
            current[1] = max(current[1], end)
        else:
            merged.append([start, end])
    return tuple((start, end) for start, end in merged)


def _contains(intervals, starts, value):
    if not intervals:
        return False
    index = bisect_right(starts, value) - 1
    return index >= 0 and value <= intervals[index][1]


def _column_name(one_based):
    if one_based < 1:
        raise ValueError('column number must be positive')
    result = []
    while one_based:
        one_based, remainder = divmod(one_based - 1, 26)
        result.append(chr(65 + remainder))
    return ''.join(reversed(result))


def _column_number(ref):
    match = _CELL_REF.fullmatch(ref)
    if match is None:
        return None
    number = 0
    for char in match.group(1).upper():
        number = number * 26 + ord(char) - 64
    return number


def _range_text(intervals, formatter=str):
    values = []
    for start, end in intervals:
        values.append(formatter(start) if start == end else formatter(start) + ':' + formatter(end))
    return ','.join(values)


class Visibility:
    """Compressed visibility metadata shared by XLSX and XLS workers."""

    def __init__(self, name, state, hidden_rows=(), hidden_columns=(),
                 default_row_hidden=False, visible_row_overrides=(),
                 unlocated_hidden_rows=0, zero_height=None):
        self.state = state
        self.sheet_name = name if state == 'visible' else name + ' [' + _STATE_LABELS.get(
            state, '工作表状态=' + state) + ']'
        self.hidden_row_ranges = _compress(hidden_rows)
        self.hidden_column_ranges = _compress(hidden_columns)
        self.default_row_hidden = bool(default_row_hidden)
        self.visible_row_overrides = _compress(visible_row_overrides)
        self.unlocated_hidden_rows = int(unlocated_hidden_rows)
        self.zero_height = zero_height
        self._row_starts = tuple(start for start, _ in self.hidden_row_ranges)
        self._visible_row_starts = tuple(start for start, _ in self.visible_row_overrides)
        self._column_starts = tuple(start for start, _ in self.hidden_column_ranges)

    def _hidden_row(self, one_based_row):
        if not isinstance(one_based_row, int):
            return False, False
        if _contains(self.hidden_row_ranges, self._row_starts, one_based_row):
            return True, False
        if self.default_row_hidden and not _contains(self.visible_row_overrides,
                                                     self._visible_row_starts, one_based_row):
            return True, True
        return False, False

    def _hidden_column(self, one_based_column):
        return _contains(self.hidden_column_ranges, self._column_starts, one_based_column)

    def describe(self, out):
        """Add one bounded metadata section when any visibility is present."""
        parts = []
        if self.state != 'visible':
            parts.append('工作表状态=' + self.state)
        if self.hidden_row_ranges:
            parts.append('隐藏行=' + _range_text(self.hidden_row_ranges))
        if self.unlocated_hidden_rows:
            parts.append('隐藏行=有 ' + str(self.unlocated_hidden_rows) + ' 行未记录行号')
        if self.default_row_hidden:
            parts.append('默认隐藏行=未显式记录的行')
            if self.visible_row_overrides:
                parts.append('显式可见行=' + _range_text(self.visible_row_overrides))
        if self.zero_height is not None:
            parts.append('默认隐藏行设置 zeroHeight=' + self.zero_height)
        if self.hidden_column_ranges:
            parts.append('隐藏列=' + _range_text(self.hidden_column_ranges, _column_name))
        if parts:
            out.add('工作表 ' + self.sheet_name + ' 隐藏状态', '；'.join(parts))

    def row_label(self, one_based_row, hidden=None):
        label = '工作表 ' + self.sheet_name + ' 行 ' + str(one_based_row)
        if hidden is None:
            hidden, default = self._hidden_row(one_based_row)
            if hidden:
                label += ' [' + ('默认隐藏行' if default else '隐藏行') + ']'
        elif hidden:
            label += ' [隐藏行]'
        return label

    def cell_text(self, ref, value, column=None):
        text = str(ref) + '=' + str(value)
        if column is None:
            column = _column_number(str(ref))
        if column is not None and self._hidden_column(int(column)):
            text += ' [隐藏列]'
        return text


def xlsx_visibility(sheet_element, sheet_root):
    """Build visibility metadata from workbook ``sheet`` and worksheet XML."""
    name = sheet_element.get('name', '')
    state = sheet_element.get('state', 'visible')
    hidden_rows = []
    hidden_columns = []
    unlocated_hidden_rows = 0
    sheet_data = sheet_root.find(_S + 'sheetData')
    if sheet_data is not None:
        for row in sheet_data.findall(_S + 'row'):
            if row.get('hidden', '0').lower() not in _HIDDEN:
                continue
            row_number = row.get('r')
            if row_number is None:
                unlocated_hidden_rows += 1
                continue
            row_number = int(row_number)
            hidden_rows.append((row_number, row_number))
    cols = sheet_root.find(_S + 'cols')
    if cols is not None:
        for col in cols.findall(_S + 'col'):
            if col.get('hidden', '0').lower() in _HIDDEN:
                hidden_columns.append((int(col.get('min')), int(col.get('max'))))
    sheet_format = sheet_root.find(_S + 'sheetFormatPr')
    zero_height = sheet_format.get('zeroHeight') if sheet_format is not None and 'zeroHeight' in sheet_format.attrib else None
    return Visibility(name, state, hidden_rows, hidden_columns,
                      unlocated_hidden_rows=unlocated_hidden_rows,
                      zero_height=zero_height)


def xls_visibility(xlrd_sheet):
    """Build visibility metadata from an xlrd sheet loaded with formatting."""
    state = {0: 'visible', 1: 'hidden', 2: 'veryHidden'}[xlrd_sheet.visibility]
    hidden_rows = []
    visible_rows = []
    for row, info in xlrd_sheet.rowinfo_map.items():
        if info.hidden:
            hidden_rows.append((row + 1, row + 1))
        elif xlrd_sheet.default_row_hidden:
            visible_rows.append((row + 1, row + 1))
    hidden_columns = [(column + 1, column + 1)
                      for column, info in xlrd_sheet.colinfo_map.items() if info.hidden]
    return Visibility(xlrd_sheet.name, state, _compress(hidden_rows),
                      _compress(hidden_columns), bool(xlrd_sheet.default_row_hidden),
                      _compress(visible_rows))


__all__ = ['Visibility', 'xlsx_visibility', 'xls_visibility']
