"""Choose reviewed conversation IDs and a Beijing time interval."""
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import messagebox, ttk

from offline_calendar import DatePicker
from offline_chat import ArchiveError, BEIJING
from offline_period import period_range


PRESETS = ('今天', '昨天', '近7天', '近30天', '本周', '上周', '本月', '上月', '自定义')


def preset_dates(preset, *, today=None):
    """Calendar days in Beijing; current week/month stop at today."""
    today = today or datetime.now(BEIJING).date()
    week = today - timedelta(days=today.weekday())
    month = today.replace(day=1)
    previous_month_end = month - timedelta(days=1)
    ranges = {
        '今天': (today, today),
        '昨天': (today - timedelta(days=1), today - timedelta(days=1)),
        '近7天': (today - timedelta(days=6), today),
        '近30天': (today - timedelta(days=29), today),
        '本周': (week, today),
        '上周': (week - timedelta(days=7), week - timedelta(days=1)),
        '本月': (month, today),
        '上月': (previous_month_end.replace(day=1), previous_month_end),
    }
    return tuple(day.isoformat() for day in ranges[preset])


class PeriodDialog(tk.Toplevel):
    def __init__(self, parent, chats, *, selected=(), start='', end=''):
        super().__init__(parent)
        self.result = None
        self.chats = chats
        self.title('指定对象与时段分析')
        self.transient(parent)
        self.geometry('700x540')
        self.minsize(540, 420)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        ttk.Label(self, text='选择已复核导出的联系人或群聊（可多选）').grid(row=0, column=0, sticky='w', padx=16, pady=12)
        frame = ttk.Frame(self)
        frame.grid(row=1, column=0, sticky='nsew', padx=16)
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(frame, columns=('name', 'id'), show='headings', selectmode='extended')
        self.tree.heading('name', text='对象')
        self.tree.heading('id', text='完整会话 ID（区分重名）')
        self.tree.column('name', width=220, minwidth=160)
        self.tree.column('id', width=360, minwidth=280)
        self.tree.grid(row=0, column=0, sticky='nsew')
        y = ttk.Scrollbar(frame, orient='vertical', command=self.tree.yview)
        y.grid(row=0, column=1, sticky='ns')
        x = ttk.Scrollbar(frame, orient='horizontal', command=self.tree.xview)
        x.grid(row=1, column=0, sticky='ew')
        self.tree.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        for index, chat in enumerate(chats):
            self.tree.insert('', 'end', iid=str(index), values=(chat['name'], chat['id']))
            if chat['id'] in selected:
                self.tree.selection_add(str(index))
        dates = ttk.Frame(self)
        dates.grid(row=2, column=0, sticky='ew', padx=16, pady=12)
        self.preset = tk.StringVar(value='自定义')
        self.applying_preset = False
        ttk.Label(dates, text='常用范围').grid(row=0, column=0, padx=(0, 8), pady=(0, 6))
        self.preset_box = ttk.Combobox(dates, textvariable=self.preset, values=PRESETS, state='readonly', width=22)
        self.preset_box.grid(row=0, column=1, columnspan=4, sticky='w', pady=(0, 6))
        self.preset_box.bind('<<ComboboxSelected>>', self.apply_preset)
        self.dates, self.clocks = [], []
        today = datetime.now(BEIJING).date().isoformat()
        for row, (label, value) in enumerate((('开始', start or today), ('结束', end or today)), 1):
            date = tk.StringVar(value=value[:10])
            clock = tk.StringVar(value=value[11:])
            self.dates.append(date)
            self.clocks.append(clock)
            ttk.Label(dates, text=label).grid(row=row, column=0, padx=(0, 8), pady=4)
            ttk.Entry(dates, textvariable=date, width=12).grid(row=row, column=1)
            ttk.Button(dates, text='日历', width=5, command=lambda value=date: self.pick_date(value)).grid(row=row, column=2, padx=6)
            ttk.Entry(dates, textvariable=clock, width=10).grid(row=row, column=3)
            ttk.Label(dates, text='可选 HH:MM[:SS]').grid(row=row, column=4, padx=6)
        for variable in (*self.dates, *self.clocks):
            variable.trace_add('write', self.mark_custom)
        ttk.Label(self, text='北京时间。近7／30天含今天，本周从周一开始，本周／本月至今天。\n快捷范围按整天选择；可继续修改日期和时刻，结束分钟／秒包含在内。',
                  wraplength=500).grid(row=3, column=0, sticky='w', padx=16)
        footer = ttk.Frame(self)
        footer.grid(row=4, column=0, sticky='ew', padx=16, pady=12)
        ttk.Button(footer, text='全选', command=lambda: self.tree.selection_set(self.tree.get_children())).pack(side='left')
        ttk.Button(footer, text='下一步：选择保存位置', command=self.submit).pack(side='right')
        ttk.Button(footer, text='取消', command=self.destroy).pack(side='right', padx=8)
        self.bind('<Escape>', lambda _event: self.destroy())
        self.grab_set()

    def mark_custom(self, *_args):
        if not self.applying_preset:
            self.preset.set('自定义')

    def apply_preset(self, _event=None):
        if self.preset.get() == '自定义':
            return
        self.applying_preset = True
        start, end = preset_dates(self.preset.get())
        self.dates[0].set(start)
        self.dates[1].set(end)
        for clock in self.clocks:
            clock.set('')
        self.applying_preset = False

    def pick_date(self, variable):
        picker = DatePicker(self, variable)
        self.wait_window(picker)
        if self.winfo_exists():
            self.grab_set()

    def submit(self):
        selected = [self.chats[int(index)]['id'] for index in self.tree.selection()]
        start, end = [date.get().strip() + (' ' + clock.get().strip() if clock.get().strip() else '')
                      for date, clock in zip(self.dates, self.clocks)]
        try:
            if not selected:
                raise ArchiveError('请选择至少一个联系人或群聊。')
            period_range(start, end)
        except ArchiveError as error:
            messagebox.showerror('请检查分析范围', str(error), parent=self)
            return
        self.result = {'chats': selected, 'start': start, 'end': end}
        self.destroy()
