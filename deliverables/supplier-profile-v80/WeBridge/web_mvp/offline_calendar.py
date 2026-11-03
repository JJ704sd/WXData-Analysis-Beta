"""Small native date picker; only Python's bundled calendar and Tk are used."""
import calendar
from datetime import date
import tkinter as tk
from tkinter import ttk

from offline_theme import (BORDER, CANVAS, SURFACE, SURFACE_HOVER, TEXT, TEXT_FAINT, TEXT_MUTED,
                           BODY, BODY_BOLD, CAPTION, ACCENT, ACCENT_SOFT, apply_theme)


class DatePicker(tk.Toplevel):
    def __init__(self, parent, variable, title='选择日期'):
        super().__init__(parent)
        self.variable = variable
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.configure(background=CANVAS)
        apply_theme(self)
        try:
            selected = date.fromisoformat(variable.get())
        except ValueError:
            selected = date.today()
        self.year, self.month = selected.year, selected.month
        self.selected = selected

        head = tk.Frame(self, background=SURFACE)
        head.pack(fill='x')
        self.caption = tk.Label(head, text='', font=('Microsoft YaHei UI', 11, 'bold'),
                                 background=SURFACE, foreground=TEXT)
        self.caption.pack(side='left', padx=16, pady=12)
        ttk.Button(head, text='›', width=3, style='Quiet.TButton',
                   command=lambda: self.move(1)).pack(side='right', padx=(0, 8), pady=10)
        ttk.Button(head, text='‹', width=3, style='Quiet.TButton',
                   command=lambda: self.move(-1)).pack(side='right', padx=(0, 4), pady=10)

        body = tk.Frame(self, background=SURFACE)
        body.pack(fill='both', expand=True)
        jump = tk.Frame(body, background=SURFACE)
        jump.pack(fill='x', padx=16, pady=(2, 8))
        self.year_value = tk.StringVar(value=str(self.year))
        self.month_value = tk.StringVar(value=str(self.month))
        tk.Label(jump, text='跳转', font=CAPTION, background=SURFACE,
                 foreground=TEXT_FAINT).pack(side='left', padx=(0, 8))
        ttk.Spinbox(jump, from_=1, to=9999, width=6, textvariable=self.year_value).pack(side='left')
        tk.Label(jump, text='年', font=CAPTION, background=SURFACE,
                 foreground=TEXT_MUTED).pack(side='left', padx=4)
        ttk.Combobox(jump, values=tuple(map(str, range(1, 13))), width=4, state='readonly',
                     textvariable=self.month_value).pack(side='left')
        tk.Label(jump, text='月', font=CAPTION, background=SURFACE,
                 foreground=TEXT_MUTED).pack(side='left', padx=4)
        ttk.Button(jump, text='前往', command=self.jump).pack(side='right')

        self.days = tk.Frame(body, background=SURFACE)
        self.days.pack(padx=14, pady=(0, 8))
        for column, label in enumerate('一二三四五六日'):
            tk.Label(self.days, text=label, font=CAPTION, background=SURFACE, foreground=TEXT_FAINT,
                     width=4, anchor='center').grid(row=0, column=column, padx=2, pady=(0, 4))
        self.draw()

        tk.Frame(self, background=BORDER, height=1).pack(fill='x', side='bottom')
        bottom = tk.Frame(self, background=SURFACE)
        bottom.pack(fill='x', side='bottom', padx=14, pady=(0, 12))
        ttk.Button(bottom, text='今天', command=lambda: self.choose(date.today())).pack(side='left')
        ttk.Button(bottom, text='清空日期', command=lambda: self.choose(None)).pack(side='left', padx=8)
        ttk.Button(bottom, text='取消', command=self.destroy).pack(side='right')
        self.bind('<Escape>', lambda _event: self.destroy())
        self.update_idletasks()
        self.geometry(f'+{parent.winfo_rootx()+80}+{parent.winfo_rooty()+80}')
        self.grab_set()

    def move(self, step):
        ordinal = self.year * 12 + self.month - 1 + step
        year, month = divmod(ordinal, 12)
        if 1 <= year <= 9999:
            self.year, self.month = year, month + 1
            self.draw()

    def draw(self):
        self.caption.configure(text=f'{self.year}年 {self.month}月')
        self.year_value.set(str(self.year))
        self.month_value.set(str(self.month))
        for child in self.days.winfo_children()[7:]:
            child.destroy()
        for row, week in enumerate(calendar.monthcalendar(self.year, self.month), 1):
            for column, day in enumerate(week):
                if day:
                    current = date(self.year, self.month, day)
                    chosen = current == self.selected
                    ttk.Button(self.days, text=str(day), width=4,
                               style='Accent.TButton' if chosen else 'Day.TButton',
                               command=lambda day=day: self.choose(date(self.year, self.month, day))
                               ).grid(row=row, column=column, padx=2, pady=2)

    def jump(self):
        try:
            target = date(int(self.year_value.get()), int(self.month_value.get()), 1)
        except ValueError:
            return
        self.year, self.month = target.year, target.month
        self.draw()

    def choose(self, value):
        self.variable.set(value.isoformat() if value else '')
        self.destroy()
