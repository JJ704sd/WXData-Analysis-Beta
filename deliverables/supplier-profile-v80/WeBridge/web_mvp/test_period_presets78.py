"""Calendar boundaries and editable presets in the existing period dialog."""
from datetime import date
import tkinter as tk
import unittest
from unittest.mock import patch

from offline_period_ui import PRESETS, PeriodDialog, preset_dates


class PeriodPresetTests(unittest.TestCase):
    def test_calendar_ranges_include_today_and_handle_year_and_leap_boundaries(self):
        expected = {
            '今天': ('2026-10-28', '2026-10-28'),
            '昨天': ('2026-10-27', '2026-10-27'),
            '近7天': ('2026-10-22', '2026-10-28'),
            '近30天': ('2026-09-29', '2026-10-28'),
            '本周': ('2026-10-26', '2026-10-28'),
            '上周': ('2026-10-19', '2026-10-25'),
            '本月': ('2026-10-01', '2026-10-28'),
            '上月': ('2026-09-01', '2026-09-30'),
        }
        self.assertEqual(set(PRESETS) - {'自定义'}, set(expected))
        for preset, interval in expected.items():
            with self.subTest(preset=preset):
                self.assertEqual(preset_dates(preset, today=date(2026, 10, 28)), interval)
        for today, preset, interval in [
            (date(2024, 3, 1), '上月', ('2024-02-01', '2024-02-29')),
            (date(2026, 1, 1), '上月', ('2025-12-01', '2025-12-31')),
            (date(2026, 1, 1), '上周', ('2025-12-22', '2025-12-28')),
            (date(2026, 10, 26), '本周', ('2026-10-26', '2026-10-26')),
        ]:
            with self.subTest(today=today, preset=preset):
                self.assertEqual(preset_dates(preset, today=today), interval)

    def test_native_preset_selection_keeps_custom_edits_and_small_window_usable(self):
        root = tk.Tk()
        self.addCleanup(root.destroy)
        dialog = PeriodDialog(root, [{'id': 'synthetic-chat', 'name': '合成会话'}],
                              selected=['synthetic-chat'], start='2026-10-25 10:30', end='2026-10-26 17:00')
        dialog.geometry('540x420')
        root.update()
        self.assertEqual(dialog.preset.get(), '自定义')
        self.assertEqual(dialog.clocks[0].get(), '10:30')
        original = preset_dates
        with patch('offline_period_ui.preset_dates', side_effect=lambda value: original(value, today=date(2026, 10, 28))):
            for preset in PRESETS[:-1]:
                dialog.preset.set(preset)
                dialog.preset_box.event_generate('<<ComboboxSelected>>')
                root.update()
                self.assertEqual(tuple(value.get() for value in dialog.dates), original(preset, today=date(2026, 10, 28)))
                self.assertEqual([value.get() for value in dialog.clocks], ['', ''])
                self.assertEqual(dialog.preset.get(), preset)
        self.assertTrue(dialog.preset_box.winfo_ismapped())
        self.assertLessEqual(dialog.preset_box.winfo_rootx() + dialog.preset_box.winfo_width(),
                             dialog.winfo_rootx() + dialog.winfo_width())
        dialog.dates[0].set('2026-09-15')
        dialog.clocks[0].set('09:15')
        self.assertEqual(dialog.preset.get(), '自定义')
        dialog.apply_preset()
        self.assertEqual(dialog.clocks[0].get(), '09:15')
        dialog.submit()
        self.assertEqual(dialog.result, {'chats': ['synthetic-chat'], 'start': '2026-09-15 09:15', 'end': '2026-09-30'})
