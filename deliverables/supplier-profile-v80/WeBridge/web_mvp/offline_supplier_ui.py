"""Explicit per-conversation group speaker selection for supplier materials."""
import tkinter as tk
from tkinter import ttk


class SupplierSpeakerDialog(tk.Toplevel):
    def __init__(self, parent, choices):
        super().__init__(parent)
        self.result = None
        self.choices = choices
        self.title('确认供应商群聊发言人')
        self.geometry('850x520')
        self.minsize(560, 380)
        self.transient(parent)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        ttk.Label(self, text='仅选择你确认属于此供应商的群成员；同一成员在不同群需分别选择。\n'
                            '不选择成员时，群消息仅作为上下文，未确认本人身份时请特别核对。',
                  wraplength=760).grid(row=0, column=0, sticky='w', padx=16, pady=12)
        frame = ttk.Frame(self)
        frame.grid(row=1, column=0, sticky='nsew', padx=16)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(frame, columns=('chat', 'name', 'id', 'count'),
                                 show='headings', selectmode='extended')
        for column, label, width in [('chat', '群聊（完整 ID）', 270), ('name', '发言人显示名', 180),
                                     ('id', '完整发言人 ID', 250), ('count', '材料条数', 75)]:
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, minwidth=60)
        self.tree.grid(row=0, column=0, sticky='nsew')
        y = ttk.Scrollbar(frame, orient='vertical', command=self.tree.yview)
        x = ttk.Scrollbar(frame, orient='horizontal', command=self.tree.xview)
        y.grid(row=0, column=1, sticky='ns')
        x.grid(row=1, column=0, sticky='ew')
        self.tree.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        for index, item in enumerate(choices):
            self.tree.insert('', 'end', iid=str(index),
                             values=(item['chatName'] + ' (' + item['chatId'] + ')',
                                     ' / '.join(item['names']), item['senderId'], item['messages']))
        footer = ttk.Frame(self)
        footer.grid(row=2, column=0, sticky='ew', padx=16, pady=12)
        ttk.Button(footer, text='确认所选发言人', command=self.submit).pack(side='right')
        ttk.Button(footer, text='取消', command=self.destroy).pack(side='right', padx=8)
        self.bind('<Escape>', lambda _event: self.destroy())
        self.grab_set()

    def submit(self):
        result = {}
        for index in self.tree.selection():
            item = self.choices[int(index)]
            result.setdefault(item['chatId'], []).append(item['senderId'])
        self.result = result
        self.destroy()
