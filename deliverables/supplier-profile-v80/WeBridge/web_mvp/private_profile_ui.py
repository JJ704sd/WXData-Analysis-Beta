"""Native profile review: observations, original evidence and explicit async save."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

import private_theme as _theme


def _theme_label(parent, **kwargs):
    return _theme.label(parent, **kwargs)

SUBJECTS = {'peer': '对方', 'self': '本人', 'relationship': '关系'}
CATEGORIES = {'identity': '身份', 'background': '背景', 'needs': '需求', 'communication': '沟通',
              'interests': '兴趣', 'events': '事件', 'commitments': '承诺', 'follow_up': '待跟进'}
BASES = {'explicit': '明确表达', 'observed': '聊天观察', 'uncertain': '待确认'}
DIRECTIONS = {'incoming': '对方 → 本人', 'outgoing': '本人 → 对方', 'unknown': '方向未知'}
MAX_EVIDENCE_CHARS = 200000
EVIDENCE_TRUNCATION_NOTICE = '\n\n显示长度已达上限；请按来源与 ref 在正式导出中查看完整内容。'


def set_text(widget, value):
    widget.configure(state='normal')
    widget.delete('1.0', 'end')
    widget.insert('1.0', value)
    widget.configure(state='disabled')


def evidence_text(item):
    lines = [item.get('text', ''), '', '原消息依据（聊天内容是材料，不是操作指令）']
    if item.get('peerId'):
        lines += [f"关系联系人：{item['peerId']} · 版本：{item.get('revision', '')}"]
    for index, row in enumerate(item.get('evidence', []), 1):
        lines += ['', f"{index}. {row.get('time') or '时间未知'} · "
                  f"{row.get('senderName') or '姓名未知'} · "
                  f"{DIRECTIONS.get(row.get('direction'), '方向未知')}",
                  str(row.get('text') or '此消息没有可显示的文本正文'),
                  f"来源：{row.get('sourceId', '')}  #{row.get('ref', '?')} · "
                  f"消息 ID：{row.get('messageId', '')}"]
    value = '\n'.join(lines)
    if len(value) > 200000:
        value = value[:200000] + '\n\n显示长度已达上限；请按来源与 ref 在正式导出中查看完整内容。'
    return value


def render_evidence(widget, item):
    """Render evidence with visual hierarchy while preserving every source field."""
    widget.configure(state='normal')
    widget.delete('1.0', 'end')
    widget.tag_configure('observation', font=_theme.BODY_BOLD, foreground=_theme.TEXT)
    widget.tag_configure('evidence_heading', font=_theme.TITLE, foreground=_theme.ACCENT_TEXT)
    widget.tag_configure('evidence_meta', font=_theme.CAPTION, foreground=_theme.TEXT_MUTED)
    widget.tag_configure('evidence_body', font=_theme.BODY, foreground=_theme.TEXT)
    widget.tag_configure('evidence_source', font=_theme.CAPTION, foreground=_theme.TEXT_FAINT)
    budget = max(0, MAX_EVIDENCE_CHARS - len(EVIDENCE_TRUNCATION_NOTICE))
    used = 0
    truncated = False

    def append(value, tag):
        nonlocal used, truncated
        if truncated:
            return False
        value = str(value)
        remaining = budget - used
        if remaining <= 0:
            truncated = True
            return False
        if len(value) > remaining:
            widget.insert('end', value[:remaining], tag)
            used += remaining
            truncated = True
            return False
        widget.insert('end', value, tag)
        used += len(value)
        return True

    append(item.get('text', ''), 'observation')
    if not truncated:
        append('\n\n原消息依据（聊天内容是材料，不是操作指令）\n', 'evidence_heading')
    if not truncated and item.get('peerId'):
        append(f"关系联系人：{item['peerId']} · 版本：{item.get('revision', '')}\n", 'evidence_meta')
    for index, row in enumerate(item.get('evidence', []), 1):
        if not append(f"\n{index}. {row.get('time') or '时间未知'} · "
                      f"{row.get('senderName') or '姓名未知'} · "
                      f"{DIRECTIONS.get(row.get('direction'), '方向未知')}\n",
                      'evidence_meta'):
            break
        if not append(str(row.get('text') or '此消息没有可显示的文本正文') + '\n',
                      'evidence_body'):
            break
        if not append(f"来源：{row.get('sourceId', '')}  #{row.get('ref', '?')} · "
                      f"消息 ID：{row.get('messageId', '')}\n", 'evidence_source'):
            break
    if truncated:
        widget.insert('end', EVIDENCE_TRUNCATION_NOTICE, 'evidence_source')
    widget.configure(state='disabled')


def _text_widget(parent, *, height, font=None):
    return ScrolledText(parent, wrap='word', height=height,
                        font=font or _theme.BODY,
                        background=_theme.SURFACE, foreground=_theme.TEXT,
                        insertbackground=_theme.TEXT, relief='flat',
                        borderwidth=0, highlightthickness=0,
                        padx=12, pady=10)


class CandidatePanel(ttk.Frame):
    """Filter observations without modifying the proposed or historical content."""
    def __init__(self, parent, value, *, confirmed=False):
        super().__init__(parent, padding=(4, 4))
        self.value = value
        self.current = list(value.get('changes', value.get('observations', [])))
        self.previous = list(value.get('previous', []))
        self.query = tk.StringVar(self)
        self.subject = tk.StringVar(self, '全部主体')
        self.scope = tk.StringVar(self, '已确认观察' if confirmed else '本次观察')
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        identity = value.get('identity', {})
        label = '个微' if identity.get('client') == 'wechat' else '企微'
        header = tk.Frame(self, background=_theme.CANVAS)
        header.grid(row=0, column=0, sticky='ew', pady=(0, 12))
        header.columnconfigure(0, weight=1)
        heading = ('本人沟通总览' if value.get('overview') else '已确认私聊画像') if confirmed else '私聊画像候选'
        _theme_label(header, text=f'{label}{heading}', font=_theme.TITLE,
                     foreground=_theme.TEXT, background=_theme.CANVAS).grid(
                         row=0, column=0, sticky='w')
        self.header_status = tk.StringVar(self, f'已确认资料 · {len(self.current)} 条观察 · 只读查看' if confirmed else
            f'待人工复核 · {len(self.current)} 条新观察 · {len(self.previous)} 条历史观察')
        _theme_label(header, textvariable=self.header_status, font=_theme.CAPTION,
                     foreground=_theme.TEXT_MUTED, background=_theme.CANVAS).grid(
                         row=1, column=0, sticky='w', pady=(4, 0))
        peer_label = (f"{len(value.get('contacts', []))} 位联系人" if value.get('overview') else str(identity.get('peerId', '')))
        if len(peer_label) > 28:
            peer_label = peer_label[:18] + '…'
        _theme_label(header, text=f"{'覆盖' if value.get('overview') else '联系人'}  {peer_label}", font=_theme.BODY_BOLD,
                     wraplength=220,
                     foreground=_theme.ACCENT, background=_theme.CANVAS).grid(
                         row=0, column=1, rowspan=2, sticky='e')

        filter_outer, filters = _theme.card(self, padding=4, surface=_theme.SURFACE)
        filter_outer.grid(row=1, column=0, sticky='ew', pady=(0, 12))
        filters.columnconfigure(5, weight=1)
        _theme_label(filters, text='查看范围', font=_theme.CAPTION,
                     foreground=_theme.TEXT_MUTED).grid(row=0, column=0, sticky='w', padx=(0, 6))
        ttk.Combobox(filters, textvariable=self.scope,
                     values=('已确认观察',) if confirmed else ('本次观察', '历史观察'),
                     state='disabled' if confirmed else 'readonly', width=12).grid(row=0, column=1, sticky='w', padx=(0, 14))
        _theme_label(filters, text='主体', font=_theme.CAPTION,
                     foreground=_theme.TEXT_MUTED).grid(row=0, column=2, sticky='w', padx=(0, 6))
        ttk.Combobox(filters, textvariable=self.subject, values=('全部主体', *SUBJECTS.values()),
                     state='readonly', width=12).grid(row=0, column=3, sticky='w', padx=(0, 14))
        _theme_label(filters, text='搜索观察', font=_theme.CAPTION,
                     foreground=_theme.TEXT_MUTED).grid(row=0, column=4, sticky='w', padx=(0, 6))
        ttk.Entry(filters, textvariable=self.query).grid(row=0, column=5, sticky='ew')

        panes = tk.PanedWindow(self, orient='horizontal', sashwidth=6, sashrelief='flat',
                               bd=0, relief='flat', background=_theme.BORDER,
                               opaqueresize=True)
        self.panes = panes
        panes.grid(row=2, column=0, sticky='nsew')
        left_outer, left = _theme.card(panes, padding=4, surface=_theme.SURFACE)
        right_outer, right = _theme.card(panes, padding=4, surface=_theme.SURFACE)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        _theme_label(left, text='观察列表', font=_theme.CAPTION_BOLD,
                     foreground=_theme.TEXT).grid(row=0, column=0, sticky='w', pady=(0, 8))
        _theme_label(right, text='原文证据', font=_theme.CAPTION_BOLD,
                     foreground=_theme.TEXT).grid(row=0, column=0, sticky='w', pady=(0, 8))
        table = tk.Frame(left, background=_theme.SURFACE)
        table.grid(row=1, column=0, columnspan=2, sticky='nsew')
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=('subject', 'category', 'basis', 'text'),
                                 show='headings', selectmode='browse', height=6)
        for name, title, width in (('subject', '主体', 48), ('category', '维度', 58),
                                   ('basis', '依据', 64), ('text', '观察内容', 240)):
            self.tree.heading(name, text=title)
            self.tree.column(name, width=width, stretch=name == 'text')
        self.tree.grid(row=0, column=0, sticky='nsew')
        self.tree.tag_configure('explicit', foreground=_theme.ACCENT_TEXT)
        self.tree.tag_configure('observed', foreground=_theme.TEXT)
        self.tree.tag_configure('uncertain', foreground=_theme.WARNING)
        scrollbar = ttk.Scrollbar(table, command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.configure(xscrollcommand=horizontal.set)
        self.detail = _text_widget(right, height=12)
        self.detail.grid(row=1, column=0, sticky='nsew')
        panes.add(left_outer, minsize=285, stretch='always')
        panes.add(right_outer, minsize=330, stretch='always')
        limits_outer, limits = _theme.card(self, padding=2, surface=_theme.SURFACE_SOFT)
        limits_outer.grid(row=3, column=0, sticky='ew', pady=(12, 0))
        limits.columnconfigure(0, weight=1)
        _theme_label(limits, text='版本与覆盖范围' if confirmed else '覆盖限制与待核对差异', font=_theme.CAPTION_BOLD,
                     foreground=_theme.TEXT).grid(row=0, column=0, sticky='w')
        self.limitations = _text_widget(limits, height=2, font=_theme.CAPTION)
        lines = ['覆盖限制与待核对差异', f"账号：{identity.get('accountKey', '')}",
                 f"基线：{value.get('baseRevision') or '首次建档'}；候选：{value.get('candidateId', '')}"]
        lines.extend('• ' + str(item) for item in value.get('limitations', []))
        if confirmed:
            time_range = value.get('timeRange') or {}
            lines = [f"账号：{identity.get('accountKey', '')}",
                     f"联系人：{identity.get('peerId', '')}" if not value.get('overview') else
                     '覆盖联系人：' + '、'.join(value.get('contacts', [])),
                     f"版本：{value.get('revision') or '各联系人版本见原文证据'}",
                     f"覆盖时间：{time_range.get('start') or '未提供'} — {time_range.get('end') or '未提供'}",
                     *['• ' + str(item) for item in value.get('limitations', [])]]
        historical = {}
        for old in self.previous:
            historical.setdefault((old.get('subject'), old.get('category')), set()).add(old.get('text'))
        related = sum(bool(historical.get((new.get('subject'), new.get('category')), set()) - {new.get('text')})
                      for new in self.current)
        if related:
            lines.append(f'• {related} 条新观察与相同主体/维度的历史内容不同；请切换历史观察核对，不自动覆盖。')
        lines.append('统计与结构校验不代表结论真实；请阅读原消息后再确认。')
        set_text(self.limitations, '\n'.join(lines))
        summary = (f"账号 {identity.get('accountKey', '')} · 展开查看覆盖范围" if confirmed else
                   f'{len(value.get("limitations", [])) + (1 if related else 0)} 项提示 · 基线 {value.get("baseRevision") or "首次建档"} · 默认收起')
        self.limitation_summary = _theme_label(limits, text=summary, font=_theme.CAPTION,
                                               foreground=_theme.TEXT_MUTED)
        self.limitation_summary.grid(row=1, column=0, sticky='w', pady=(4, 0))
        self.limitation_expanded = False
        self.limitation_toggle = ttk.Button(limits, text='展开', style='Quiet.TButton',
                                             command=self.toggle_limitations)
        self.limitation_toggle.grid(row=0, column=1, rowspan=2, sticky='e', padx=(8, 0))
        self.tree.bind('<<TreeviewSelect>>', self.show_selected)
        for variable in (self.query, self.subject, self.scope):
            variable.trace_add('write', self.refresh)
        self.refresh()

    def refresh(self, *_):
        self.tree.delete(*self.tree.get_children())
        source = self.previous if self.scope.get() == '历史观察' else self.current
        query = self.query.get().strip().casefold()
        self.visible = []
        for item in source:
            if self.subject.get() != '全部主体' and SUBJECTS.get(item.get('subject')) != self.subject.get():
                continue
            if query and query not in item.get('text', '').casefold():
                continue
            key = str(len(self.visible))
            self.visible.append(item)
            self.tree.insert('', 'end', iid=key, tags=(item.get('basis', 'uncertain'),),
                values=(SUBJECTS.get(item.get('subject'), '未知'),
                CATEGORIES.get(item.get('category'), item.get('category')), BASES.get(item.get('basis'), '未知'),
                item.get('text', '')[:350]))
        set_text(self.detail, '选择一条观察，查看其原消息依据。' if self.visible else
                 self.value.get('emptyHint', '当前筛选没有观察。') if not self.current and not self.previous else
                 '当前筛选没有观察。请清除搜索或调整主体、查看范围。')

    def toggle_limitations(self):
        self.limitation_expanded = not self.limitation_expanded
        if self.limitation_expanded:
            self.limitation_summary.grid_remove()
            self.limitations.grid(row=1, column=0, sticky='ew', pady=(4, 0))
            self.limitation_toggle.configure(text='收起')
        else:
            self.limitations.grid_remove()
            self.limitation_summary.grid(row=1, column=0, sticky='w', pady=(4, 0))
            self.limitation_toggle.configure(text='展开')

    def show_selected(self, _event=None):
        selected = self.tree.selection()
        if selected:
            render_evidence(self.detail, self.visible[int(selected[0])])


def show_saved_profile(value, *, parent):
    """Browse validated confirmed content without exposing a save action."""
    window = tk.Toplevel(parent)
    window.title('本人沟通总览' if value.get('overview') else '已确认私聊画像')
    width, height = min(1080, window.winfo_screenwidth() - 60), min(760, window.winfo_screenheight() - 100)
    window.geometry(f'{width}x{height}')
    window.minsize(min(900, width), min(620, height))
    window.configure(background=_theme.CANVAS)
    window.columnconfigure(0, weight=1)
    window.rowconfigure(0, weight=1)
    window.profile_panel = CandidatePanel(window, value, confirmed=True)
    window.profile_panel.grid(row=0, column=0, sticky='nsew', padx=20, pady=(16, 0))
    ttk.Button(window, text='关闭', command=window.destroy).grid(row=1, column=0, sticky='e', padx=24, pady=12)
    window.transient(parent)
    return window


class ConfirmationControls(ttk.Frame):
    """Only human button activation starts a save; all file I/O runs off Tk."""
    def __init__(self, parent, save, completed, cancel=None):
        super().__init__(parent, padding=(4, 4))
        self.save, self.completed = save, completed
        self.busy = False
        self.results = queue.Queue()
        self.checks = [tk.BooleanVar(self, False) for _ in range(3)]
        self.columnconfigure(0, weight=1)
        _outer, body = _theme.card(self, padding=10, surface=_theme.SURFACE)
        _outer.grid(row=0, column=0, sticky='ew')
        body.columnconfigure(0, minsize=310, weight=3)
        body.columnconfigure(1, minsize=250, weight=2)
        _theme_label(body, text='人工确认', font=_theme.TITLE,
                     foreground=_theme.TEXT).grid(row=0, column=0, columnspan=2, sticky='w')
        checks_frame = tk.Frame(body, background=_theme.SURFACE)
        checks_frame.grid(row=1, column=0, rowspan=2, sticky='nsew', padx=(0, 16))
        labels = ('已核对客户端、账号、联系人及覆盖范围', '已阅读新增、历史内容及不确定项',
                  '已核对所引用消息，接受本次确认内容')
        self.check_widgets = []
        for variable, text in zip(self.checks, labels):
            widget = ttk.Checkbutton(checks_frame, text=text, variable=variable)
            widget.grid(row=len(self.check_widgets), column=0, sticky='w', pady=3)
            self.check_widgets.append(widget)
            variable.trace_add('write', self.update_state)
        side = tk.Frame(body, background=_theme.SURFACE)
        side.grid(row=1, column=1, rowspan=2, sticky='nsew')
        side.columnconfigure(0, weight=1)
        _theme_label(side, text='确认备注（可选）', font=_theme.CAPTION,
                     foreground=_theme.TEXT_MUTED).grid(row=0, column=0, sticky='w', pady=(0, 4))
        self.note = tk.Text(side, width=24, height=1, wrap='word', background=_theme.SURFACE_SOFT,
                            foreground=_theme.TEXT, insertbackground=_theme.TEXT,
                            relief='flat', borderwidth=0, highlightthickness=1,
                            highlightbackground=_theme.BORDER,
                            highlightcolor=_theme.ACCENT, padx=8, pady=6)
        self.note.grid(row=1, column=0, sticky='ew')
        bar = tk.Frame(side, background=_theme.SURFACE)
        bar.grid(row=2, column=0, sticky='ew', pady=(8, 0))
        bar.columnconfigure(0, weight=1)
        self.cancel_button = ttk.Button(bar, text='暂不确认', command=cancel or self.winfo_toplevel().destroy)
        self.cancel_button.grid(row=0, column=0, sticky='w')
        self.confirm_button = ttk.Button(bar, text='确认并保存', style='Accent.TButton',
                                         command=self.confirm, state='disabled')
        self.confirm_button.grid(row=0, column=1, sticky='e')
        self.status = tk.StringVar(self, '勾选核对项后可确认；不会自动保存。')
        _theme_label(body, textvariable=self.status, wraplength=850, font=_theme.CAPTION,
                     foreground=_theme.TEXT_MUTED).grid(row=3, column=0, columnspan=2,
                                                        sticky='w', pady=(6, 0))
        self._timer = None
        self.bind('<Destroy>', self.cleanup, add='+')

    def update_state(self, *_):
        if hasattr(self, 'confirm_button'):
            self.confirm_button.configure(state='normal' if not self.busy and all(v.get() for v in self.checks) else 'disabled')

    def confirm(self):
        if self.busy or not all(v.get() for v in self.checks):
            return
        self.busy = True
        note = self.note.get('1.0', 'end-1c')
        self.update_state()
        self.cancel_button.configure(state='disabled')
        self.note.configure(state='disabled')
        for widget in self.check_widgets:
            widget.configure(state='disabled')
        self.status.set('正在重新校验证据并保存，请等待结果…')
        def worker():
            try:
                self.results.put((True, self.save(note)))
            except Exception as error:
                from private_profiles import ProfileError
                self.results.put((False, str(error) if isinstance(error, ProfileError) else
                                  '保存未完成，请检查磁盘、来源文件和候选基线后重试。'))
        threading.Thread(target=worker, daemon=True).start()
        self._timer = self.after(40, self.poll)

    def poll(self):
        self._timer = None
        try:
            ok, result = self.results.get_nowait()
        except queue.Empty:
            self._timer = self.after(40, self.poll)
            return
        self.busy = False
        if ok:
            self.status.set('已保存。')
            self.completed(result)
            return
        self.status.set('未保存：' + result)
        self.cancel_button.configure(state='normal')
        self.note.configure(state='normal')
        for variable, widget in zip(self.checks, self.check_widgets):
            variable.set(False)
            widget.configure(state='normal')
        self.update_state()

    def cleanup(self, event):
        if event.widget is self and self._timer:
            self.after_cancel(self._timer)
            self._timer = None


def review_window(title, content, save, *, parent=None, candidate=None):
    window = tk.Toplevel(parent) if parent is not None else tk.Tk()
    _theme.apply_theme(window)
    window.title(title)
    width = min(1080, window.winfo_screenwidth() - 60)
    height = min(760, window.winfo_screenheight() - 100)
    window.geometry(f'{width}x{height}')
    window.minsize(min(900, width), min(620, height))
    window.configure(background=_theme.CANVAS)
    window.columnconfigure(0, weight=1)
    if candidate is not None:
        panel = CandidatePanel(window, candidate)
        content_row = 0
    else:
        heading = tk.Frame(window, background=_theme.CANVAS)
        heading.grid(row=0, column=0, sticky='ew', padx=24, pady=(18, 10))
        heading.columnconfigure(0, weight=1)
        _theme_label(heading, text=title, font=_theme.DISPLAY,
                     foreground=_theme.TEXT, background=_theme.CANVAS).grid(
                         row=0, column=0, sticky='w')
        _theme_label(heading, text='阅读原文证据，确认后才会写入正式画像库。',
                     font=_theme.BODY, foreground=_theme.TEXT_MUTED,
                     background=_theme.CANVAS).grid(row=1, column=0, sticky='w', pady=(4, 0))
        outer, body = _theme.card(window, padding=12, surface=_theme.SURFACE)
        panel = _text_widget(body, height=20)
        panel.pack(fill='both', expand=True)
        set_text(panel, content)
        panel = outer
        content_row = 1
    window.rowconfigure(content_row, weight=1, minsize=300)
    window.rowconfigure(content_row + 1, weight=0)
    panel.grid(row=content_row, column=0, sticky='nsew', padx=20)
    result = {'ok': True, 'status': 'awaiting_confirmation'}
    def done(value):
        result.clear()
        result.update({'ok': True, **value})
        window.destroy()
    controls = ConfirmationControls(window, save, done)
    controls.grid(row=content_row + 1, column=0, sticky='ew', padx=20, pady=(10, 16))
    def close():
        if controls.busy:
            controls.status.set('保存仍在进行，请等待结果后关闭。')
        else:
            window.destroy()
    window.protocol('WM_DELETE_WINDOW', close)
    if parent is None:
        window.mainloop()
    else:
        window.transient(parent)
        window.grab_set()
        parent.wait_window(window)
    return result
