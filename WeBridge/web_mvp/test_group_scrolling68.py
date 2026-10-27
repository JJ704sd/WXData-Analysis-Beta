"""Visible, draggable navigation for overflowing native group/message tables."""
from tkinter import font as tkfont
from tkinter import ttk
import unittest

from test_group_ui42 import GroupUI42Tests


class GroupScrolling68Tests(unittest.TestCase):
    setUp = GroupUI42Tests.setUp
    tearDown = GroupUI42Tests.tearDown
    app = GroupUI42Tests.app

    def bars(self, tree):
        return {str(widget.cget('orient')): widget for widget in tree.master.winfo_children()
                if isinstance(widget, ttk.Scrollbar)}

    def drag_to_end(self, bar):
        vertical = str(bar.cget('orient')) == 'vertical'
        length = bar.winfo_height() if vertical else bar.winfo_width()
        cross = (bar.winfo_width() if vertical else bar.winfo_height()) // 2
        if vertical:
            start = next(point for point in range(length) if 'thumb' in bar.identify(cross, point))
        else:
            start = next(point for point in range(length) if 'thumb' in bar.identify(point, cross))
        position = (cross, start + 2) if vertical else (start + 2, cross)
        target = (cross, length - 2) if vertical else (length - 2, cross)
        bar.event_generate('<ButtonPress-1>', x=position[0], y=position[1])
        bar.event_generate('<B1-Motion>', x=target[0], y=target[1])
        bar.event_generate('<ButtonRelease-1>', x=target[0], y=target[1])
        self.root.update()

    def test_group_and_message_scrollbars_remain_draggable_at_small_window_size(self):
        self.root.tk.call('tk', 'scaling', 1.75)
        app = self.app('wecom')
        self.root.geometry('1000x700')
        for index in range(80):
            app.tree.insert('', 'end', values=('☐', '合成业务讨论群', '未解析', index,
                                              'R:synthetic-group-' + str(index), '未解析'))
            app.browser.tree.insert('', 'end', text='合成消息 ' + str(index),
                                    values=('10-26 12:34:56', '附件', '完整原件'))
        # A user can widen a message column by dragging its header separator.
        app.browser.tree.column('#0', width=720, minwidth=720)
        self.root.update()
        for tree in (app.tree, app.browser.tree):
            with self.subTest(table=str(tree)):
                bars = self.bars(tree)
                self.assertGreaterEqual(bars['vertical'].winfo_width(), 12)
                self.assertGreaterEqual(bars['horizontal'].winfo_height(), 12)
                self.assertLess(tree.xview()[1], 1)
                self.drag_to_end(bars['horizontal'])
                self.assertGreater(tree.xview()[0], 0)
                self.assertAlmostEqual(tree.xview()[1], 1, places=2)
                self.drag_to_end(bars['vertical'])
                self.assertGreater(tree.yview()[0], 0)
                self.assertAlmostEqual(tree.yview()[1], 1, places=2)
                last = tree.bbox(tree.get_children()[-1], 'state')
                self.assertTrue(last)
                self.assertGreaterEqual(last[0], 0)
                self.assertLessEqual(last[0] + last[2], tree.winfo_width())

    def test_long_group_name_can_be_read_in_full_after_horizontal_navigation(self):
        app = self.app('wecom')
        name = '合成项目业务沟通群' * 4
        app.groups = [{'id': 'R:' + 'synthetic-group-' * 4, 'name': name, 'localMessageCount': 1}]
        app.render_groups()
        self.root.geometry('1000x700')
        self.root.update()
        item = app.tree.get_children()[0]
        displayed = app.tree.item(item, 'values')[1]
        font = tkfont.Font(font=ttk.Style(self.root).lookup('Treeview', 'font'))
        self.assertGreaterEqual(app.tree.column('name', 'width'), font.measure(displayed) + 16)
        self.assertGreaterEqual(app.tree.column('id', 'width'), font.measure(app.tree.item(item, 'values')[4]) + 16)
        self.assertLess(app.tree.xview()[1], 1)
