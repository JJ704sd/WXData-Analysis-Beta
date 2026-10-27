"""One-click native Windows UI. Python, Tcl/Tk and codecs are bundled in EXE."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

PROJECT = Path(__file__).resolve().parents[1]
if not getattr(sys, 'frozen', False):
    sys.path.insert(0, str(PROJECT / 'web_mvp'))

from offline_chat import ArchiveError, BEIJING, date_range
from offline_calendar import DatePicker
from offline_accounts import group_choices, same_account_source
from offline_desktop import DesktopSession, safe_issue
from private_profiles import ProfileError
from offline_widgets import MessageBrowser
from offline_theme import (ACCENT, ACCENT_SOFT, ACCENT_TEXT, BODY, BODY_BOLD, BORDER, BORDER_STRONG,
                           CANVAS, CAPTION, DISPLAY, INFO, INFO_SOFT, SURFACE, SURFACE_SOFT, TEXT,
                           TEXT_FAINT, TEXT_MUTED, TITLE, WARNING, WARNING_SOFT, BrandMark,
                           SearchGlyph, StatusDot, StatusPill, apply_theme, card)
import offline_startup as startup
import private_theme as private_theme

from chat_release import GROUP_RELEASE, GROUP_VERSION, PRIVATE_VERSION

GUI_RELEASE = GROUP_RELEASE

FILTERS = {'业务阅读': 'business', '全部': None, '图片': 'image', '附件': 'file', '转发聊天': 'record', '引用': 'quote',
           '视频': 'video', '待补齐图片': 'missing_images', '表情': 'emoji', '待补齐媒体': 'missing_media'}
SHORTCUTS = ('全部', '图片', '附件', '转发聊天', '引用', '视频', '业务阅读', '待补齐图片', '表情')


class ChatApp:
    def ui_text(self, text):
        """Translate fixed interface labels only; never pass names or message bodies."""
        if getattr(self.session, 'conversation_mode', 'group') == 'direct':
            return text.replace('群聊', '会话').replace('群', '会话')
        return text

    def __init__(self, root, session, *, check_report=None, check_chat=None,
                 open_chat=None, open_chat_name=None, open_result=None):
        self.root, self.session = root, session
        root.report_callback_exception = self.callback_error
        self.check_report, self.check_chat = check_report, check_chat
        self.open_request = {'chat_id': open_chat, 'chat_name': open_chat_name} if open_chat or open_chat_name else None
        self.open_result_path = Path(open_result).resolve() if open_result else None
        self.auto_parse_result = None
        if self.open_result_path:
            folder = (session.directory / 'requests').resolve()
            if (self.open_result_path.parent != folder or not folder.is_relative_to(session.directory)
                    or self.open_result_path.suffix != '.json'):
                raise ValueError('解析状态文件必须位于程序数据目录的 requests 子目录。')
            folder.mkdir(parents=True, exist_ok=True)
            with self.open_result_path.open('x', encoding='utf-8') as handle:
                json.dump({'state': 'starting', 'processId': os.getpid()}, handle)
        self.events = queue.Queue()
        self.read_events = queue.Queue()
        self.export_events = queue.Queue()
        self.read_busy = False
        self.read_serial = 0
        self.read_pending = None
        self.busy = False
        self.closing = False
        self.destroyed = False
        self.groups = []
        self.active_chat = None
        self.parsed_chat = None
        self.selected_chats = set()
        self.parsed_results = {}
        self.parse_states = {}
        self.parse_errors = {}
        self.batch_queue = []
        self.restored_selection_pending = False
        self.batch_ids = []
        self.batch_running = False
        self.batch_reused = 0
        self.batch_cancel_requested = False
        self.receipt_chat = None
        self.batch_output = None
        if session.conversation_mode == 'group':
            self.export_directory_text = tk.StringVar(value=str(session.export_directory))
        self.last_completed_chat = None
        self.resumed_snapshot = ''
        self.pending_config = {}
        self.source_issue = ''
        self.source_dialog = None
        self.source_controls = []
        self.source_progress = None
        self.account_scan = {}
        self.account_choices_update = None
        self.page_offset = 0
        self.page_total = 0
        self.display_release = '私聊 ' + PRIVATE_VERSION if session.conversation_mode == 'direct' else GUI_RELEASE
        self.check_data = {'ok': False, 'nativeWindowCreated': True, 'client': session.client,
                           'conversationMode': session.conversation_mode, 'release': self.display_release}
        self.client_title = ('企微' if session.client == 'wecom' else '个微') + (
            '私聊分析 · 画像 ' + PRIVATE_VERSION if getattr(session, 'conversation_mode', 'group') == 'direct'
            else '本地聊天解析 · 业务阅读 · ' + GUI_RELEASE)
        root.title(self.client_title)
        width, height = min(1220, root.winfo_screenwidth() - 60), min(820, root.winfo_screenheight() - 110)
        root.geometry(f'{width}x{height}+20+20')
        root.minsize(min(1000, width), min(700, height))
        root.protocol('WM_DELETE_WINDOW', self.close)
        if getattr(session, 'conversation_mode', 'group') == 'direct':
            private_theme.apply_theme(root)
        else:
            apply_theme(root)
        self.search = tk.StringVar()
        self.start_date = tk.StringVar()
        self.end_date = tk.StringVar()
        self.copy_media = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value='正在查找已有配置并准备本地副本…')
        self.subtitle = tk.StringVar(value=self.ui_text('列群 → 选择群 ID → 解析 → 人工复核 → 导出'))
        self.group_title = tk.StringVar(value='联系人' if session.conversation_mode == 'direct' else '本地群聊')
        self.selection_text = tk.StringVar(value=self.ui_text('已选 0 个群'))
        self.review_summary = tk.StringVar(value='勾选 → 解析 → 人工复核 → 导出')
        self.workflow_hint = tk.StringVar(value=self.ui_text('先勾选需要解析的群聊。'))
        self.current_group_id = tk.StringVar()
        self.review_filter = tk.StringVar(value=self.ui_text('全部群聊'))
        self.current_review_state = tk.StringVar(value=self.ui_text('未选群'))
        self.current_review_hint = tk.StringVar(value=self.ui_text('单击群名查看，勾选群聊决定导出范围'))
        self.completion_text = tk.StringVar(value=self.ui_text('尚无已解析群'))
        self.completion_detail = tk.StringVar(value=self.ui_text('每群完成后可立即查看，人工复核后再正式导出。'))
        self.account_text = tk.StringVar(value=self.ui_text('确认账号 → 选择群 ID → 解析 → 人工复核 → 导出'))
        self.group_hint = tk.StringVar(value=('配置账号后，搜索联系人并选择会话。' if session.conversation_mode == 'direct'
                                              else '配置账号后，输入要解析的群名。'))
        self.chat_title = tk.StringVar(value=('选择联系人后查看记录' if session.conversation_mode == 'direct'
                                              else self.ui_text('输入目标群名后按 Enter')))
        self.result_text = tk.StringVar(value='')
        self.filter_kind = tk.StringVar(value='业务阅读')
        self.page_size = tk.StringVar(value='300')
        self.message_search = tk.StringVar()
        self.page_text = tk.StringVar(value='')
        self._layout()
        for variable in (self.start_date, self.end_date, self.copy_media):
            variable.trace_add('write', lambda *_: self.range_changed())
        for number, label in enumerate(SHORTCUTS, 1):
            root.bind(f'<Alt-Key-{number}>', lambda _event, label=label: self.choose_kind(label))
        root.bind('<Alt-o>', lambda _event: self.open_selected())
        root.bind('<Alt-v>', lambda _event: self.open_selected(image=True))
        root.bind('<Control-f>', lambda _event: self.search_entry.focus_set())
        self.search.trace_add('write', lambda *_: self.render_groups())
        root.after(80, self._drain)
        root.after(120, lambda: self.work('加载数据',
            lambda: session.bootstrap(resume=not bool(self.open_request or self.check_report)), self.ready))

    def publish_open(self, state, **fields):
        if not self.open_result_path:
            return
        value = {'state': state, 'processId': os.getpid(), **fields}
        if self.open_request:
            value['requestedChatName'] = self.open_request.get('chat_name')
        fd, temporary = tempfile.mkstemp(prefix='.open-', dir=self.open_result_path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
            os.replace(temporary, self.open_result_path)
        finally:
            if Path(temporary).exists():
                Path(temporary).unlink()

    def _layout(self):
        is_private = getattr(self.session, 'conversation_mode', 'group') == 'direct'
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        # -- application bar ------------------------------------------------
        appbar = tk.Frame(self.root, background=SURFACE)
        appbar.grid(row=0, column=0, sticky='ew')
        self.appbar = appbar
        appbar.grid_columnconfigure(1, weight=1)
        appbar.grid_columnconfigure(2, weight=0)
        client = '企业微信' if self.session.client == 'wecom' else '个人微信'
        mark = BrandMark(appbar, '企' if self.session.client == 'wecom' else '微', background=SURFACE)
        mark.grid(row=0, column=0, rowspan=2, padx=(16, 12), pady=9)
        titles = tk.Frame(appbar, background=SURFACE)
        titles.grid(row=0, column=1, sticky='sw', pady=(9, 0))
        if is_private:
            title_font = private_theme.DISPLAY if self.root.winfo_width() >= 1120 else (
                private_theme.FONT_FAMILY, 18, 'bold')
            private_theme.label(titles, text='私聊分析与画像', font=title_font,
                                background=private_theme.SURFACE,
                                foreground=private_theme.TEXT).pack(side='left')
        else:
            tk.Label(titles, text='群聊解析与分析', font=DISPLAY, background=SURFACE,
                     foreground=TEXT).pack(side='left')
        tk.Label(titles, text=client + ' · ' + self.display_release, font=CAPTION, background=SURFACE_SOFT,
                 foreground=TEXT_MUTED, padx=9, pady=2).pack(side='left', padx=(10, 0))
        account_label = tk.Label(appbar, textvariable=self.account_text, font=CAPTION,
                                 background=SURFACE, foreground=TEXT_MUTED, anchor='w')
        account_label.grid(row=1, column=1, sticky='nw', pady=(2, 9))
        self.account_label = account_label
        if not is_private:
            account_label.configure(width=1)
            account_label.grid_configure(sticky='ew')
        if is_private:
            account_label.grid_remove()
            steps = tk.Frame(appbar, background=private_theme.SURFACE)
            steps.grid(row=1, column=1, sticky='w', pady=(4, 10))
            self.private_step_labels = []
            for index, text in enumerate(('解析记录', '复核导出', '分析画像'), 1):
                step = private_theme.label(steps, text=f'{index:02d}  {text}',
                    font=private_theme.CAPTION_BOLD, padx=8, pady=3,
                    background=private_theme.SURFACE_SOFT, foreground=private_theme.TEXT_MUTED)
                step.pack(side='left', padx=(0, 8))
                self.private_step_labels.append(step)
        actions = tk.Frame(appbar, background=SURFACE)
        actions.grid(row=0, column=2, rowspan=2, sticky='e', padx=(16, 16), pady=12)
        self.progress = ttk.Progressbar(actions, mode='indeterminate', length=72 if is_private else 110)
        self.progress.pack(side='right', padx=(0, 14))
        self.scan_button = ttk.Menubutton(actions, text='更多设置 ▾')
        menu = tk.Menu(self.scan_button, tearoff=False)
        menu.add_command(label='手动导入数据副本', command=lambda: self.source_settings(manual=True))
        menu.add_command(label='打开诊断日志', command=self.open_diagnostics)
        if getattr(self.session, 'conversation_mode', 'group') == 'direct':
            menu.add_separator()
            menu.add_command(label='准备当前会话的画像材料', command=self.prepare_profile_material)
            menu.add_command(label='导入宿主候选并复核…', command=self.import_profile_candidate)
            menu.add_command(label='复核画像候选…', command=self.open_profile_candidate)
            menu.add_command(label='查看已确认画像', command=self.open_saved_profile)
            menu.add_command(label='本人沟通总览', command=lambda: self.open_saved_profile(overview=True))
            menu.add_command(label='下一个待复核联系人', command=self.next_review)
            menu.add_command(label='修复导出清单', command=self.repair_export_metadata)
        else:
            menu.add_command(label='修复导出清单与分析入口', command=self.repair_export_metadata)
            from offline_content_ui import open_content_settings
            menu.add_command(label='内容补齐：原件、OCR、密码…', command=lambda: open_content_settings(self))
        menu.add_separator()
        label = '企微' if self.session.client == 'wecom' else '个微'
        menu.add_command(label=f'清除{label}账号缓存…', command=self.clear_account_cache)
        self.scan_button.configure(menu=menu)
        self.scan_button.pack(side='right')
        if not is_private:
            self.diagnostics_button=ttk.Button(actions,text='导出日志',command=self.export_diagnostics)
            self.diagnostics_button.pack(side='right',padx=(0,8))
        self.source_button = ttk.Button(actions, text='账号设置', command=self.scan)
        self.source_button.pack(side='right', padx=8)
        self.refresh_button = ttk.Button(actions, text=self.ui_text('刷新此群与媒体'), command=self.refresh)
        if is_private:
            menu.add_command(label='刷新当前会话与媒体', command=self.refresh)
            menu.add_separator()
            menu.add_command(label='打开业务全文', command=lambda: self.open_result(False))
            menu.add_command(label='打开图片网页', command=self.open_image_page)
            menu.add_command(label='打开文件所在目录', command=lambda: self.open_result(True))
            menu.add_command(label='打开批次目录', command=self.open_batch)
            for kind in ('JSON', 'JSONL', 'Markdown'):
                menu.add_command(label=f'打开 {kind} 导出', command=lambda kind=kind: self.open_export_file(kind))
        else:
            menu.add_command(label='刷新当前群与媒体', command=self.refresh)
        tk.Frame(self.root, background=BORDER, height=1).grid(row=1, column=0, sticky='ew')

        # -- body -----------------------------------------------------------
        body = tk.Frame(self.root, background=private_theme.CANVAS if is_private else CANVAS)
        body.grid(row=2, column=0, sticky='nsew')
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        if is_private:
            self.sidebar, panel = private_theme.card(body, border=private_theme.BORDER,
                                                     surface=private_theme.SURFACE, padding=12)
            self.sidebar.configure(width=330)
            self.sidebar.grid(row=0, column=0, sticky='nsw', padx=(12, 8), pady=12)
        else:
            self.sidebar, panel = card(body)
            self.sidebar.configure(width=330)
            self.sidebar.grid(row=0, column=0, sticky='nsw', padx=(12, 8), pady=12)
        self.sidebar.pack_propagate(False)

        if is_private:
            private_theme.label(panel, textvariable=self.group_title, font=private_theme.TITLE,
                                background=private_theme.SURFACE, foreground=private_theme.TEXT,
                                anchor='w').pack(fill='x', pady=(2, 12))
        else:
            tk.Label(panel, textvariable=self.group_title, font=TITLE, background=SURFACE,
                     foreground=TEXT, anchor='w').pack(fill='x', padx=14, pady=(14, 10))

        search_field = tk.Frame(panel, background=BORDER)
        search_field.pack(fill='x', padx=14)
        search_inner = tk.Frame(search_field, background=SURFACE)
        search_inner.pack(fill='both', expand=True, padx=1, pady=1)
        SearchGlyph(search_inner).pack(side='left', padx=(8, 4))
        self.search_entry = ttk.Entry(search_inner, textvariable=self.search)
        self.search_entry.pack(side='left', fill='both', expand=True, padx=(0, 4), ipady=2)
        self.search_entry.bind('<Return>', self.locate_search_result)
        self.search_clear_button = ttk.Button(search_inner, text='✕', width=2, style='Quiet.TButton',
                                              command=lambda: self.search.set(''), state='disabled')
        self.search_clear_button.pack(side='right', padx=(0, 2))

        parse_bar = tk.Frame(panel, background=SURFACE)
        parse_bar.pack(fill='x', padx=14, pady=(10, 0))
        self.parse_button = ttk.Button(parse_bar, text=self.ui_text('先选择群聊'), style='Accent.TButton',
                                       command=self.choose_search_result, state='disabled')
        self.parse_button.pack(side='left', fill='x', expand=True)
        self.reparse_button = ttk.Button(parse_bar, text='重新解析', command=self.reparse_targets, state='disabled')
        self.reparse_button.pack(side='right', padx=(8, 0))

        picks = tk.Frame(panel, background=SURFACE)
        picks.pack(fill='x', padx=14, pady=(6, 0))
        self.select_all_button = ttk.Button(picks, text='全选当前结果', command=self.toggle_visible,
                                            style='Quiet.TButton')
        self.select_all_button.pack(side='left', padx=(0, 2))
        self.clear_selection_button = ttk.Button(picks, text='清空选择', command=self.clear_selection,
                                                 style='Quiet.TButton')
        self.clear_selection_button.pack(side='left', padx=(0, 2))
        self.selection_menu_button = ttk.Menubutton(picks, text='更多 ▾', style='Quiet.TButton')
        self.selection_menu_button.pack(side='right')
        self.selection_menu = tk.Menu(self.selection_menu_button, tearoff=False)
        self.selection_menu.add_command(label='全选当前结果', command=self.select_visible)
        self.selection_menu.add_command(label='取消当前结果勾选', command=self.deselect_visible)
        self.selection_menu.add_command(label='反选当前结果', command=self.invert_visible)
        self.selection_menu.add_separator()
        self.selection_menu.add_command(label=self.ui_text('全选全部群聊（含隐藏）'), command=self.select_all_groups)
        self.selection_menu.add_command(label='取消全部勾选', command=self.clear_selection)
        self.selection_menu_button.configure(menu=self.selection_menu)

        selection_bar = tk.Frame(panel, background=SURFACE)
        selection_bar.pack(fill='x', padx=14, pady=(2, 0))
        tk.Label(selection_bar, textvariable=self.selection_text, font=CAPTION,
                 background=SURFACE, foreground=TEXT_MUTED).pack(side='left')
        self.cancel_batch_button = ttk.Button(selection_bar, text='停止队列', command=self.cancel_batch,
                                               state='disabled', style='Quiet.TButton')
        self.cancel_batch_button.pack(side='right')

        self.review_filter_choice = ttk.Combobox(panel, textvariable=self.review_filter,
            values=(self.ui_text('全部群聊'), '已解析可查看', self.ui_text('所选群聊'), '所选未复核', '所选已复核'), state='readonly')
        self.review_filter_choice.pack(fill='x', padx=14, pady=(8, 0))
        self.review_filter_choice.bind('<<ComboboxSelected>>', lambda _event: self.render_groups())
        tk.Label(panel, textvariable=self.group_hint, wraplength=288, font=CAPTION,
                 background=SURFACE, foreground=TEXT_FAINT, justify='left').pack(fill='x', padx=14, pady=(6, 8))

        table = tk.Frame(panel, background=BORDER)
        table.pack(fill='both', expand=True, padx=14)
        table_inner = tk.Frame(table, background=SURFACE)
        table_inner.pack(fill='both', expand=True, padx=1, pady=1)
        tree_frame = tk.Frame(table_inner, background=SURFACE)
        tree_frame.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(tree_frame, columns=('pick', 'name', 'review', 'count', 'id', 'state'),
                                  show='headings', selectmode='browse')
        self.tree.heading('pick', text='☐', command=self.toggle_visible)
        self.tree.column('pick', width=28, minwidth=28, stretch=False, anchor='center')
        self.tree.heading('name', text=self.ui_text('群聊'), anchor='w')
        self.tree.column('name', width=220, minwidth=160, stretch=False, anchor='w')
        self.tree.heading('review', text='解析 / 复核')
        self.tree.column('review', width=92, minwidth=84, stretch=False, anchor='center')
        self.tree.heading('count', text='记录')
        self.tree.column('count', width=46, minwidth=40, stretch=False, anchor='e')
        self.tree.heading('id', text=self.ui_text('群 ID'), anchor='w')
        self.tree.column('id', width=150, minwidth=110, anchor='w')
        self.tree.heading('state', text='解析状态')
        self.tree.column('state', width=78, minwidth=68, stretch=False)
        self.tree.tag_configure('unreviewed', foreground=TEXT)
        self.tree.tag_configure('reviewed', foreground='#067647', background='#EDF9F3')
        self.tree.tag_configure('parsed', foreground='#1D4ED8', background=ACCENT_SOFT)
        self.tree.tag_configure('parsing', foreground=WARNING, background=WARNING_SOFT)
        self.tree.tag_configure('queued', foreground=TEXT_FAINT)
        self.tree.tag_configure('failed', foreground='#B42318', background='#FDECEC')
        scroll = ttk.Scrollbar(tree_frame, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        horizontal = ttk.Scrollbar(tree_frame, orient='horizontal', command=self.tree.xview)
        self.tree.configure(xscrollcommand=horizontal.set)
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        self.tree.grid(row=0, column=0, sticky='nsew')
        scroll.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        ttk.Button(panel, text=self.ui_text('复制所选群 ID'), command=self.copy_group_id,
                   style='Quiet.TButton').pack(fill='x', padx=14, pady=(8, 12))
        self.tree.bind('<<TreeviewSelect>>', self.selection)
        self.tree.bind('<Button-1>', self.toggle_group_click)
        self.tree.bind('<space>', self.toggle_focused_group)
        self.tree.bind('<Control-a>', lambda _event: self.select_visible())
        self.tree.bind('<Control-Shift-A>', lambda _event: self.deselect_visible())
        self.tree.bind('<Control-i>', lambda _event: self.invert_visible())
        self.tree.bind('<Return>', lambda _event: self.choose_search_result())
        self.tree.bind('<Double-1>', lambda _event: 'break')

        # -- reading pane ---------------------------------------------------
        if is_private:
            right_outer, right = private_theme.card(body, border=private_theme.BORDER,
                                                    surface=private_theme.SURFACE, padding=10)
            right_outer.grid(row=0, column=1, sticky='nsew', padx=(0, 12), pady=12)
        else:
            right = tk.Frame(body, background=CANVAS)
            right.grid(row=0, column=1, sticky='nsew', padx=(0, 12), pady=12)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(3, weight=3 if is_private else 1, minsize=220 if is_private else 0)
        self._private_reading_pane = right if is_private else None

        title = tk.Frame(right, background=CANVAS)
        title.grid(row=0, column=0, sticky='ew', pady=(0, 8))
        title_line = tk.Frame(title, background=CANVAS)
        title_line.pack(fill='x')
        self.next_review_button = ttk.Button(title_line, text=self.ui_text('下一个未复核群'), command=self.next_review,
                                             state='disabled')
        self.next_review_button.pack(side='right')
        self.current_review_badge = StatusPill(title_line, text=self.ui_text('未选群'), tone='none', background=CANVAS)
        self.current_review_badge.pack(side='right' if is_private else 'left', padx=(12, 0))
        title_value = self.chat_title
        if is_private:
            self.private_chat_display = tk.StringVar(self.root)
            def update_private_title(*_):
                from tkinter import font as tkfont
                value = self.chat_title.get()
                width = max(140, title_line.winfo_width() - 210)
                font = tkfont.Font(root=self.root, font=private_theme.TITLE)
                shown = value
                while shown and font.measure(shown + ('…' if shown != value else '')) > width:
                    shown = shown[:-1]
                self.private_chat_display.set(shown + ('…' if shown != value else ''))
            self.chat_title.trace_add('write', update_private_title)
            update_private_title()
            title_value = self.private_chat_display
        else:
            self.group_title_line = title_line
            self.group_chat_display = tk.StringVar(self.root)
            def update_group_title(*_):
                value = self.chat_title.get()
                width = max(80, title_line.winfo_width() -
                            self.next_review_button.winfo_reqwidth() -
                            self.current_review_badge.winfo_reqwidth() -
                            self.group_identity_button.winfo_reqwidth() - 34)
                font = tkfont.Font(root=self.root, font=TITLE)
                shown = value
                while shown and font.measure(shown + ('…' if shown != value else '')) > width:
                    shown = shown[:-1]
                self.group_chat_display.set(shown + ('…' if shown != value else ''))
            self.group_identity_button = ttk.Button(title_line, text='群资料 ▾', style='Quiet.TButton',
                                                     command=self.toggle_group_identity)
            self.group_identity_button.pack(side='right', padx=(6, 0))
            self.chat_title.trace_add('write', update_group_title)
            title_line.bind('<Configure>', update_group_title)
            update_group_title()
            title_value = self.group_chat_display
        self.current_title_label = tk.Label(title_line, textvariable=title_value,
                 font=private_theme.TITLE if is_private else TITLE, background=CANVAS,
                 foreground=TEXT, anchor='w', justify='left')
        self.current_title_label.pack(side='left', fill='x', expand=True)
        if is_private:
            self.next_review_button.pack_forget()
            self.private_identity_button = ttk.Button(title_line, text='会话资料 ▾', style='Quiet.TButton',
                                                       command=self.toggle_private_identity)
            self.private_identity_button.pack(side='right', padx=(6, 0))
            title_line.bind('<Configure>', update_private_title)

        identity_line = tk.Frame(title, background=CANVAS)
        identity_line.pack(fill='x', pady=(4 if is_private else 7, 0))
        self.private_identity_line = identity_line if is_private else None
        self.copy_current_id_button = ttk.Button(identity_line, text='复制 ID', width=6,
                                                 style='Quiet.TButton' if is_private else 'TButton',
                                                 command=self.copy_current_group_id, state='disabled')
        self.copy_current_id_button.pack(side='right', padx=(8, 0))
        tk.Label(identity_line, text=self.ui_text('群 ID'), font=private_theme.CAPTION if is_private else CAPTION,
                 background=CANVAS,
                 foreground=TEXT_FAINT).pack(side='left', padx=(0, 8))
        self.group_id_entry = ttk.Entry(identity_line, textvariable=self.current_group_id, state='readonly',
                                        style='Quiet.TEntry' if is_private else 'TEntry')
        self.group_id_entry.pack(side='left', fill='x', expand=True)
        if not is_private:
            self.group_identity_line = identity_line
            identity_line.pack_forget()
            self.group_full_name = tk.Label(title, textvariable=self.chat_title, font=CAPTION,
                                           background=CANVAS, foreground=TEXT_MUTED, anchor='w', justify='left')
            self.group_full_name.bind('<Configure>', lambda event:
                                      self.group_full_name.configure(wraplength=max(100, event.width)))

        if is_private:
            identity_line.pack_forget()
            private_actions = ttk.Frame(title)
            self.private_actions = private_actions
            private_actions.pack(fill='x', pady=(8, 0))
            self.private_insights_button = ttk.Button(private_actions, text='聊天足迹', style='Quiet.TButton', command=self.open_private_insights, state='disabled')
            self.private_prepare_button = ttk.Button(private_actions, text='准备画像材料', style='Quiet.TButton', command=self.prepare_profile_material, state='disabled')
            self.private_candidate_button = ttk.Button(private_actions, text='导入候选…', style='Quiet.TButton', command=self.import_profile_candidate)
            for button in (self.private_insights_button, self.private_prepare_button, self.private_candidate_button):
                button.pack(side='left', padx=(0, 6))
            self.private_library_button = ttk.Menubutton(private_actions, text='画像库 ▾', style='Quiet.TButton')
            library = tk.Menu(self.private_library_button, tearoff=False)
            library.add_command(label='查看已确认画像', command=self.open_saved_profile)
            library.add_command(label='本人沟通总览', command=lambda: self.open_saved_profile(overview=True))
            library.add_command(label='复核已暂存候选…', command=self.open_profile_candidate)
            self.private_library_button.configure(menu=library)
            self.private_library_button.pack(side='right')
        self.current_hint_row = tk.Frame(title, background=CANVAS)
        self.current_hint_row.pack(fill='x', pady=(6, 0))
        tk.Label(self.current_hint_row, textvariable=self.current_review_hint, font=CAPTION,
                 background=CANVAS, foreground=TEXT_MUTED).pack(side='left')

        self.completion_banner = tk.Frame(title, background=INFO_SOFT)
        self.completion_banner.pack(fill='x', pady=(8, 0))
        tk.Frame(self.completion_banner, background=INFO, width=3).pack(side='left', fill='y')
        banner_text = tk.Frame(self.completion_banner, background=INFO_SOFT)
        banner_text.pack(side='left', fill='both', expand=True, padx=(11, 8), pady=4)
        notice_top = tk.Frame(banner_text, background=INFO_SOFT)
        notice_top.pack(fill='x')
        self.completed_groups_button = ttk.Button(notice_top, text=self.ui_text('查看已解析群'), style='Accent.TButton',
                                                  command=self.show_completed_groups, state='disabled')
        self.completed_groups_button.pack(side='right', padx=(10, 0))
        notice_heading = tk.Label(notice_top, textvariable=self.completion_text, font=BODY_BOLD,
                                  background=INFO_SOFT, foreground='#0B5C73', anchor='w')
        notice_heading.pack(side='left', fill='x', expand=True)
        notice_detail = tk.Label(banner_text, textvariable=self.completion_detail, font=CAPTION,
                                  background=INFO_SOFT, foreground='#3C7F92', anchor='w')
        notice_detail.pack(fill='x')
        if not is_private:
            notice_detail.pack_forget()
            self.completeness_values = [tk.StringVar(value='—') for _ in range(4)]
            self.group_quality_labels = []
            self._quality_compact = False
            metrics = tk.Frame(banner_text, background=INFO_SOFT)
            metrics.pack(fill='x', pady=(4, 2))
            for index, label in enumerate(('当前群记录', '原件 / 引用', '正文 / 引用', '待补齐或核对')):
                metrics.columnconfigure(index, weight=1, uniform='quality')
                item = tk.Frame(metrics, background=INFO_SOFT)
                item.grid(row=0, column=index, sticky='ew', padx=(0, 8))
                caption = tk.Label(item, text=label, background=INFO_SOFT, foreground=TEXT_MUTED, font=CAPTION, anchor='w')
                caption.pack(fill='x')
                self.group_quality_labels.append(caption)
                tk.Label(item, textvariable=self.completeness_values[index], background=INFO_SOFT,
                         foreground=WARNING if index==3 else TEXT, font=BODY_BOLD, anchor='w').pack(fill='x')
            from offline_completeness import open_overview
            self.completeness_button = ttk.Button(metrics, text='完整性概览', command=lambda: open_overview(self))
            self.completeness_button.grid(row=0, column=4, padx=(6, 0))
        self.completion_banner.bind('<Configure>', lambda event: (
            notice_heading.configure(wraplength=max(160, event.width - self.completed_groups_button.winfo_reqwidth() - 48)),
            notice_detail.configure(wraplength=max(160, event.width - 42))))
        self.completion_banner.pack_forget()
        self.root.bind('<Configure>', self.adapt_layout, add='+')

        reading = tk.Frame(right, background=CANVAS)
        reading.grid(row=1, column=0, sticky='ew', pady=(0, 6))
        reading.grid_columnconfigure(3, weight=1)
        tk.Label(reading, text='消息', font=CAPTION, background=CANVAS,
                 foreground=TEXT_FAINT).grid(row=0, column=0, padx=(0, 8))
        self.kind_choice = ttk.Combobox(reading, textvariable=self.filter_kind, values=list(FILTERS),
                                        state='readonly', width=11)
        self.kind_choice.grid(row=0, column=1)
        self.kind_choice.bind('<<ComboboxSelected>>', lambda _event: self.read_page())
        tk.Label(reading, text='关键词', font=CAPTION, background=CANVAS,
                 foreground=TEXT_FAINT).grid(row=0, column=2, padx=(14, 8))
        self.message_search_entry = ttk.Entry(reading, textvariable=self.message_search, width=12)
        self.message_search_entry.grid(row=0, column=3, sticky='ew')
        self.message_search_entry.bind('<Return>', lambda _event: self.read_page(0))
        self.message_search_button = ttk.Button(reading, text='筛选', width=3, command=lambda: self.read_page(0))
        self.message_search_button.grid(row=0, column=4, padx=(8, 4))
        self.message_clear_button = ttk.Button(reading, text='清除', width=3, command=self.clear_message_search)
        self.message_clear_button.grid(row=0, column=5)
        self.date_toggle_button = ttk.Button(reading, text='日期 / 媒体 ▾', command=self.toggle_date_filters)
        self.date_toggle_button.grid(row=0, column=6, padx=(14, 0))

        date_outer = tk.Frame(right, background=BORDER)
        self.date_frame = date_outer
        self.date_frame.grid(row=2, column=0, sticky='ew', pady=(0, 10))
        date_inner = tk.Frame(date_outer, background=SURFACE)
        date_inner.pack(fill='both', expand=True, padx=1, pady=1)
        dates = tk.Frame(date_inner, background=SURFACE)
        dates.pack(fill='x', padx=12, pady=(10, 0))
        options = tk.Frame(date_inner, background=SURFACE)
        self.date_controls = []
        for label, variable in (('开始', self.start_date), ('结束', self.end_date)):
            tk.Label(dates, text=label, font=CAPTION, background=SURFACE,
                     foreground=TEXT_FAINT).pack(side='left', padx=(0, 6))
            entry = ttk.Entry(dates, textvariable=variable, width=11)
            entry.pack(side='left', padx=(0, 4))
            button = ttk.Button(dates, text='日历', width=5,
                                 command=lambda variable=variable, label=label: self.choose_date(variable, label))
            button.pack(side='left', padx=(0, 10))
            self.date_controls += [entry, button]
        action_parent = options
        self.apply_date_button = ttk.Button(action_parent, text='应用日期', command=self.apply_dates)
        self.apply_date_button.pack(side='left', padx=(0, 6))
        self.clear_date_button = ttk.Button(action_parent, text='清空', width=5, command=self.clear_dates)
        self.clear_date_button.pack(side='left')
        self.date_controls += [self.apply_date_button, self.clear_date_button]
        options.pack(fill='x', padx=12, pady=(8, 10))
        self.media_check = ttk.Checkbutton(options, text='包含本机缓存图片和附件', variable=self.copy_media)
        self.media_check.pack(side='left')
        self.date_controls.append(self.media_check)
        tk.Label(options if is_private else date_inner,
                 text='日期同时用于阅读与导出，结束日含当天。', font=CAPTION, background=SURFACE,
                 foreground=TEXT_FAINT, wraplength=260).pack(
                     side='left' if is_private else 'top', padx=14, pady=0 if is_private else (0, 8),
                     anchor='w')
        self.date_frame.grid_remove()

        self.browser = MessageBrowser(right, notify=self.status.set, show_source=self.show_image_source)
        self.browser.grid(row=3, column=0, sticky='nsew')
        if is_private:
            self.browser.tree.configure(height=8)
            browser_clear = self.browser.clear
            def clear_private_view(clear_gallery=True, _clear=browser_clear):
                _clear(clear_gallery=clear_gallery)
                self.browser.detail.configure(state='normal')
                self.browser.detail.delete('1.0', 'end')
                self.browser.detail.insert('end', '选择左侧联系人，点击消息查看原文、图片和附件。\n转发聊天可逐层展开。')
                self.browser.detail.configure(state='disabled')
            self.browser.clear = clear_private_view
            self.browser.clear()
            browser_load = self.browser.load
            def load_private_view(value, _load=browser_load):
                result = _load(value)
                private_theme.retheme_widgets(self.root)
                return result
            self.browser.load = load_private_view
            self.private_empty_background = tk.Frame(self.browser, background=private_theme.SURFACE)
            self.private_empty_panel = tk.Frame(self.private_empty_background, background=private_theme.SURFACE,
                                                 padx=20, pady=18)
            private_theme.label(self.private_empty_panel, text='从一段对话开始',
                font=(private_theme.FONT_FAMILY, 18, 'bold'), foreground=private_theme.TEXT,
                background=private_theme.SURFACE, anchor='center').pack(fill='x', pady=(0, 8))
            self.private_empty_details = tk.Frame(self.private_empty_panel,
                                                   background=private_theme.SURFACE)
            self.private_empty_details.pack(fill='x')
            private_theme.label(self.private_empty_details,
                text='选一位联系人，留住消息，也留下理解。', font=private_theme.BODY,
                foreground=private_theme.TEXT_MUTED, background=private_theme.SURFACE,
                anchor='center', wraplength=400).pack(fill='x', pady=(0, 14))
            for number, instruction in (('01', '在左侧选择联系人与日期'),
                                        ('02', '解析记录，核对后导出'),
                                        ('03', '交给宿主分析，确认后沉淀画像')):
                private_theme.label(self.private_empty_details, text=f'{number}   {instruction}',
                    font=private_theme.BODY, foreground=private_theme.ACCENT_TEXT,
                    background=private_theme.SURFACE, anchor='center',
                    wraplength=400).pack(fill='x', pady=4)
            self.private_empty_panel.place(relx=.5, rely=.5, anchor='center', relwidth=.9)
            def fit_empty_panel(event=None):
                height = self.browser.winfo_height()
                if height < 90 or (self.active_chat and self.parsed_chat == self.active_chat):
                    self.private_empty_panel.place_forget()
                    self.private_empty_background.place_forget()
                    return
                if height < 245:
                    self.private_empty_details.pack_forget()
                    self.private_empty_panel.configure(pady=8)
                else:
                    self.private_empty_details.pack(fill='x')
                    self.private_empty_panel.configure(pady=18)
                self.private_empty_background.place(x=0, y=0, relwidth=1, relheight=1)
                self.private_empty_panel.place(relx=.5, rely=.5, anchor='center', relwidth=.9)
            self._fit_private_empty = fit_empty_panel
            self.browser.bind('<Configure>', fit_empty_panel, add='+')
        else:
            self.group_empty_panel = tk.Frame(self.browser, background=SURFACE, padx=20, pady=14)
            tk.Label(self.group_empty_panel, text='从一组有用的对话开始', font=TITLE,
                     foreground=TEXT, background=SURFACE).pack(pady=(0, 8))
            self.group_empty_detail = tk.Label(self.group_empty_panel,
                text='01  选择群聊和日期\n02  解析记录，阅读并复核\n03  导出文件，交给助手分析',
                font=BODY, foreground=TEXT_MUTED, background=SURFACE, justify='left')
            self.group_empty_detail.pack()
            self.browser.bind('<Configure>', lambda _event: self.fit_group_empty(), add='+')

        pagination = tk.Frame(right, background=CANVAS)
        pagination.grid(row=4, column=0, sticky='ew', pady=(6, 0))
        tk.Label(pagination, textvariable=self.page_text, font=CAPTION, background=CANVAS,
                 foreground=TEXT_MUTED).pack(side='left')
        pager = tk.Frame(pagination, background=CANVAS)
        pager.pack(side='right')
        self.first_button = ttk.Button(pager, text='首页', width=5, command=lambda: self.read_page(0))
        self.previous_button = ttk.Button(pager, text='上一页', width=6,
                                          command=lambda: self.read_page(max(0, self.page_offset - int(self.page_size.get()))))
        self.next_button = ttk.Button(pager, text='下一页', width=6,
                                       command=lambda: self.read_page(self.page_offset + int(self.page_size.get())))
        self.last_button = ttk.Button(pager, text='末页', width=5, command=lambda: self.read_page())
        for widget in (self.first_button, self.previous_button, self.next_button, self.last_button):
            widget.pack(side='left', padx=(0, 4))
        tk.Label(pager, text='每页', font=CAPTION, background=CANVAS,
                 foreground=TEXT_FAINT).pack(side='left', padx=(10, 6))
        self.page_size_choice = ttk.Combobox(pager, textvariable=self.page_size, values=('100', '300', '500'),
                                             width=5, state='readonly')
        self.page_size_choice.pack(side='left')
        self.page_size_choice.bind('<<ComboboxSelected>>', lambda _event: self.read_page())

        exports = tk.Frame(right, background=CANVAS)
        exports.grid(row=5, column=0, sticky='ew', pady=(6, 0))
        ttk.Separator(exports).pack(fill='x', pady=(0, 6))
        summary = tk.Frame(exports, background=CANVAS)
        summary.pack(fill='x', pady=(0, 4))
        self.batch_folder_button = ttk.Button(summary, text='打开批次目录', command=self.open_batch, state='disabled')
        self.batch_review_button = ttk.Button(summary, text=self.ui_text('批量复核所选群'), command=self.review_selected,
                                               state='disabled')
        self.batch_review_button.pack(side='right', padx=(0, 8))
        summary_text = tk.Frame(summary, background=CANVAS)
        summary_text.pack(side='left', fill='x', expand=True)
        counts = tk.Label(summary_text, textvariable=self.review_summary, font=CAPTION, background=CANVAS,
                          foreground=TEXT_MUTED, wraplength=420, anchor='w')
        counts.pack(anchor='w')
        guidance = tk.Label(summary_text, textvariable=self.workflow_hint, font=BODY_BOLD, background=CANVAS,
                            foreground=ACCENT_TEXT, wraplength=420, anchor='w')
        guidance.pack(anchor='w')
        self.review_count_label, self.workflow_hint_label = counts, guidance
        summary_text.bind('<Configure>', lambda event: [label.configure(wraplength=max(180, event.width))
                                                        for label in (counts, guidance)])
        if not is_private:
            location = tk.Frame(exports, background=CANVAS)
            location.pack(fill='x', pady=(0, 5))
            tk.Label(location, text='导出到', font=CAPTION, background=CANVAS,
                     foreground=TEXT_MUTED).pack(side='left', padx=(0, 8))
            self.export_location_button = ttk.Button(location, text='选择位置…',
                command=self.choose_export_directory)
            self.export_location_button.pack(side='right', padx=(8, 0))
            ttk.Entry(location, textvariable=self.export_directory_text, state='readonly').pack(
                side='left', fill='x', expand=True)
        actions = tk.Frame(exports, background=CANVAS)
        actions.pack(fill='x')
        self.export_button = ttk.Button(actions, text=self.ui_text('一键导出所选群'), style='Accent.TButton',
                                        command=self.export_selected, state='disabled')
        self.export_button.pack(side='right')
        self.review_button = ttk.Button(actions, text=self.ui_text('复核当前群'), command=self.review_current, state='disabled')
        self.review_button.pack(side='right', padx=(0, 8))
        self.export_file_buttons = {}
        for kind in ('JSON', 'JSONL', 'Markdown'):
            button = ttk.Button(actions, text=kind, style='Tool.TButton',
                                command=lambda kind=kind: self.open_export_file(kind), state='disabled')
            button.pack(side='left', padx=(0, 4))
            self.export_file_buttons[kind] = button
        if is_private:
            for button in self.export_file_buttons.values():
                button.pack_forget()
            self.folder_button = ttk.Button(actions, text='打开已保存文件', style='Tool.TButton',
                                            command=self.open_saved_location, state='disabled')
            self.folder_button.pack(side='left')
        else:
            for button in self.export_file_buttons.values():
                button.pack_forget()
            self.group_file_button = ttk.Menubutton(actions, text='打开文件 ▾', style='Quiet.TButton')
            self.group_file_menu = tk.Menu(self.group_file_button, tearoff=False)
            self.group_file_menu.add_command(label='业务全文', command=lambda: self.open_result(False))
            self.group_file_menu.add_command(label='图片网页', command=self.open_image_page)
            for kind in ('JSON', 'JSONL', 'Markdown'):
                self.group_file_menu.add_command(label=kind, command=lambda kind=kind: self.open_export_file(kind))
            self.group_file_menu.add_separator()
            self.group_file_menu.add_command(label='打开批次目录', command=self.open_batch)
            self.group_file_menu.add_command(label='跨群分析材料', command=self.open_batch_analysis)
            self.group_file_menu.add_separator()
            self.group_file_menu.add_command(label='另存当前群到指定位置…', command=self.save_current_export_copy)
            self.group_file_menu.add_separator()
            self.group_file_menu.add_command(label='补齐清单与处理建议', command=lambda: self.open_export_file('补齐说明'))
            self.group_file_menu.add_command(label='补齐清单表格', command=lambda: self.open_export_file('补齐表格'))
            self.group_file_button.configure(menu=self.group_file_menu)
            self.group_file_button.pack(side='left')

        # -- status bar -----------------------------------------------------
        tk.Frame(self.root, background=BORDER, height=1).grid(row=3, column=0, sticky='ew')
        bottom = tk.Frame(self.root, background=SURFACE)
        bottom.grid(row=4, column=0, sticky='ew')
        self.footer = bottom
        bottom.grid_columnconfigure(1, weight=1)
        self.status_dot = StatusDot(bottom)
        self.status_dot.grid(row=0, column=0, padx=(16, 8), pady=(5, 0))
        ttk.Entry(bottom, textvariable=self.status, state='readonly',
                  style='Quiet.TEntry').grid(row=0, column=1, sticky='ew', pady=(5, 0))
        self.open_button = ttk.Button(bottom, text='业务全文', command=lambda: self.open_result(False),
                                      state='disabled')
        self.open_button.grid(row=0, column=2, padx=(12, 4), pady=(5, 4))
        self.image_page_button = ttk.Button(bottom, text='图片网页', command=self.open_image_page, state='disabled')
        self.image_page_button.grid(row=0, column=3, padx=(0, 16), pady=(5, 4))
        file_row = tk.Frame(bottom, background=SURFACE)
        file_row.grid(row=1, column=0, columnspan=4, sticky='ew', padx=16, pady=(0, 7))
        self.file_row = file_row
        tk.Label(file_row, text='文件', font=CAPTION, background=SURFACE,
                 foreground=TEXT_FAINT).pack(side='left', padx=(0, 8))
        ttk.Entry(file_row, textvariable=self.result_text, state='readonly',
                  style='Quiet.TEntry').pack(side='left', fill='x', expand=True)
        self.repair_metadata_button = ttk.Button(file_row, text='修复导出清单', style='Quiet.TButton',
                                                  command=self.repair_export_metadata, state='disabled')
        self.repair_metadata_button.pack(side='right', padx=(8, 0))
        if not is_private:
            self.folder_button = ttk.Button(file_row, text='打开目录', style='Quiet.TButton',
                                            command=self.open_saved_location, state='disabled')
            self.folder_button.pack(side='right', padx=(8, 0))
            self.open_button.grid_remove()
            self.image_page_button.grid_remove()
        if is_private:
            self.open_button.grid_remove()
            self.image_page_button.grid_remove()
            self.file_row.grid_remove()
        if is_private:
            private_theme.retheme_widgets(self.root)
            self._update_private_reading_minimum()

    def adapt_layout(self, event):
        """Keep the reading surface usable when the window is short or scaled up."""
        if event.widget is not self.root:
            return
        try:
            scale = float(self.root.tk.call('tk', 'scaling') or 1)
        except (TypeError, ValueError):
            scale = 1.0
        # Only a strongly scaled display loses logical rows; ordinary DPI keeps every line.
        compact = scale >= 1.6 and event.height < 880
        if hasattr(self, 'group_quality_labels') and self._quality_compact != compact:
            self._quality_compact = compact
            for label in self.group_quality_labels:
                if compact: label.pack_forget()
                else: label.pack(fill='x', before=label.master.winfo_children()[1])
            self.update_completion_notice()
        hint_options = {'fill': 'x', 'pady': (6, 0)}
        if self.completion_banner.winfo_manager():
            hint_options['before'] = self.completion_banner
        self._toggle_pack(self.current_hint_row, event.height >= 840, **hint_options)
        self._toggle_pack(self.review_count_label, event.height >= 760,
                          anchor='w', before=self.workflow_hint_label)
        # The completion notice always stays: it is the only always-visible progress cue.
        self._toggle_pack(self.workflow_hint_label, not compact, anchor='w')
        private = getattr(self.session, 'conversation_mode', 'group') == 'direct'
        if compact or private:
            self.account_label.grid_remove()
            if self.session.last_export or self.batch_output:
                self.file_row.grid()
            else:
                self.file_row.grid_remove()
        else:
            self.account_label.grid()
            if self.session.last_export or self.batch_output:
                self.file_row.grid()
            else:
                self.file_row.grid_remove()
        self._update_private_reading_minimum()

    def toggle_group_identity(self):
        visible = bool(self.group_identity_line.winfo_manager())
        if visible:
            self.group_identity_line.pack_forget()
            self.group_full_name.pack_forget()
        else:
            self.group_full_name.pack(fill='x', pady=(4, 0), after=self.group_title_line)
            self.group_identity_line.pack(fill='x', pady=(4, 0), after=self.group_full_name)
        self.group_identity_button.configure(text='群资料 ▾' if visible else '群资料 ▴')

    def fit_group_empty(self):
        height = self.browser.winfo_height()
        if height < 90 or (self.active_chat and self.parsed_chat == self.active_chat):
            self.group_empty_panel.place_forget()
            return
        self._toggle_pack(self.group_empty_detail, height >= 190)
        self.group_empty_panel.place(relx=.5, rely=.5, anchor='center', relwidth=.96)

    def toggle_private_identity(self):
        line = self.private_identity_line
        if line is None:
            return
        if line.winfo_manager():
            line.pack_forget()
            self.private_identity_button.configure(text='会话资料 ▾')
        else:
            line.pack(fill='x', pady=(4, 0), before=self.private_actions)
            self.private_identity_button.configure(text='会话资料 ▴')
        self._update_private_reading_minimum()

    @staticmethod
    def _toggle_pack(widget, visible, **options):
        if visible:
            if not widget.winfo_manager():
                widget.pack(**options)
        elif widget.winfo_manager():
            widget.pack_forget()

    def toggle_date_filters(self, show=None):
        visible = bool(self.date_frame.winfo_manager())
        show = not visible if show is None else show
        if show:
            self.date_frame.grid()
        else:
            self.date_frame.grid_remove()
        self.date_toggle_button.configure(text='日期 / 媒体 ▴' if show else '日期 / 媒体 ▾')
        self._update_private_reading_minimum()

    def _update_private_reading_minimum(self):
        """Keep the direct-chat reader readable without forcing small windows past the footer."""
        if getattr(self.session, 'conversation_mode', 'group') != 'direct' or self._private_reading_pane is None:
            return
        date_open = bool(self.date_frame.winfo_manager())
        banner_open = bool(self.completion_banner.winfo_manager())
        identity_open = bool(self.private_identity_line.winfo_manager())
        minimum = 0 if date_open or banner_open or identity_open else 220
        self._private_reading_pane.grid_rowconfigure(3, minsize=minimum)

    def clear_message_search(self):
        self.message_search.set('')
        self.read_page(0)

    def work(self, label, function, callback, on_error=None, diagnostic=None):
        if self.busy or self.closing or (self.read_busy and not self.batch_running):
            return
        self.busy = True
        self.status.set(label + '，请稍候…')
        self.progress.start(12)
        if self.source_progress is not None:
            self.source_progress.start(12)
        self.update_buttons()
        def run():
            startup.operation({'parsed_ready':'parse','batch_export_ready':'export'}.get(callback.__name__,callback.__name__),**(diagnostic or {}))
            try:
                result=function()
                startup.completed()
                self.events.put((callback, result, None, on_error))
            except Exception as error:
                # ProfileError is the profile service's user-facing validation contract;
                # unexpected exceptions still use the generic, redacted data error.
                issue = str(error) if isinstance(error, ProfileError) else safe_issue(error)
                fault=startup.failure(error)
                if self.session.conversation_mode=='group':issue+=' 诊断编号：'+fault+'，可点击“导出日志”。'
                self.events.put((callback, None, issue, on_error))
        threading.Thread(target=run, daemon=True, name='local-chat-reader').start()

    def can_browse(self):
        return not self.closing and (not self.busy or self.batch_running)

    def read_view(self, chat, function, callback):
        """Read a completed cache independently of the serial parsing worker."""
        if not self.can_browse() or chat != self.active_chat or chat not in self.parsed_results:
            return
        self.read_serial += 1
        self.read_pending = (self.read_serial, chat, self.session.views.get(chat), function, callback)
        self.start_view_read()

    def start_view_read(self):
        if self.read_busy or self.read_pending is None or self.closing:
            return
        token, chat, view, function, callback = self.read_pending
        self.read_pending = None
        self.read_busy = True
        self.update_buttons()
        def run():
            reading_view = view
            startup.operation('read_view',target=chat)
            try:
                if reading_view is None:
                    reading_view = self.session.review_view(chat)
                value, issue = function(reading_view), None
                startup.completed()
            except Exception as error:
                value, issue = None, safe_issue(error)
                fault=startup.failure(error)
                if self.session.conversation_mode=='group':issue+=' 诊断编号：'+fault+'，请导出日志。'
            self.read_events.put((token, chat, reading_view, callback, value, issue))
        threading.Thread(target=run, daemon=True, name='local-chat-cache-reader').start()

    def drain_view_read(self):
        try:
            token, chat, view, callback, value, issue = self.read_events.get_nowait()
        except queue.Empty:
            return
        self.read_busy = False
        if (not self.closing and token == self.read_serial and chat == self.active_chat
                and self.session.views.get(chat) is view):
            if issue:
                self.status.set('读取未完成：' + issue)
            else:
                try:
                    callback(value)
                except Exception as error:
                    self.callback_error(type(error), error, error.__traceback__)
        self.start_view_read()
        self.update_buttons()

    def callback_error(self, kind, value, tb):
        fault=startup.failure(value,'ui_callback_error',tb)
        self.status.set('界面操作未完成，可重试；诊断编号：'+fault+'，请导出诊断日志反馈错误。')

    def private_ready(self):
        if self.busy or self.read_busy or self.batch_running or self.closing:
            return False
        if not self.session.last_export:
            messagebox.showinfo('先导出记录', '请先人工复核并正式导出当前私聊，再进行统计或准备画像材料。', parent=self.root)
            return False
        return True

    def prepare_profile_material(self):
        if not self.private_ready():
            return
        from private_chat import default_store, _bound, read_input
        from private_profiles import ProfileStore
        from private_insights_ui import show_material_request
        export_root = Path(self.session.last_export)
        client = self.session.client
        def prepare():
            _bound(read_input(export_root / 'review.json', 16 * 1024 * 1024).get('client'), client)
            return ProfileStore(default_store()).prepare(export_root)
        def completed(value):
            self.status.set('本次材料无变化，无需重复生成候选。' if value.get('status') == 'unchanged' else
                            '画像材料已准备；由宿主分析后生成候选，人工确认后才入库。')
            if value.get('status') == 'unchanged':
                show_material_request(value, parent=self.root)
                return
            show_material_request(
                value,
                parent=self.root,
                on_import_candidate=lambda _request_value: self.import_profile_candidate(
                    export_root=export_root,
                    bound_client=client,
                    expected_request_id=value.get('requestId'),
                ),
            )
        self.work('校验正式导出并准备画像材料', prepare, completed)

    def _private_import_context_matches(self, export_root, client):
        current = self.session.last_export
        if not current or self.closing or self.session.client != client:
            return False
        try:
            return Path(current).absolute() == Path(export_root).absolute()
        except (OSError, TypeError, ValueError):
            return False

    def import_profile_candidate(self, *, export_root=None, expected_request_id=None,
                                 bound_client=None):
        """Import a host candidate into the current store, then open human review."""
        if self.busy or self.read_busy or self.batch_running or self.closing:
            if not self.closing:
                self.status.set('请等待当前操作完成后导入画像候选。')
            return False
        current_export = self.session.last_export
        if not current_export:
            messagebox.showinfo('先导出记录', '请先人工复核并正式导出当前私聊，再导入宿主候选。', parent=self.root)
            return False
        export_root = Path(export_root or current_export).absolute()
        client = bound_client or self.session.client
        if not self._private_import_context_matches(export_root, client):
            messagebox.showerror('材料会话已改变', '当前客户端或导出选择已改变，请返回对应会话后重试。', parent=self.root)
            return False
        from private_chat import default_store, show_candidate, stage_candidate_file
        from private_profiles import ProfileStore
        selected_path = filedialog.askopenfilename(
            parent=self.root,
            title='选择宿主生成的画像候选 JSON',
            filetypes=[('候选 JSON', '*.json'), ('所有文件', '*.*')],
        )
        if not selected_path:
            return False
        # The native picker runs a nested event loop. Recheck both operation state
        # and the captured material identity before any background staging begins.
        if self.busy or self.read_busy or self.batch_running or self.closing:
            if not self.closing:
                self.status.set('当前操作或窗口状态已变化，候选未暂存；请完成后重试。')
            return False
        if not self._private_import_context_matches(export_root, client):
            messagebox.showerror('材料会话已改变', '当前客户端或导出选择已改变，候选未暂存。请返回对应会话后重试。', parent=self.root)
            return False
        candidate_path = Path(selected_path).absolute()
        store = ProfileStore(default_store())

        def load():
            if (self.closing or self.batch_running or self.read_busy or
                    not self._private_import_context_matches(export_root, client)):
                raise ProfileError('当前客户端或导出选择已改变，候选未暂存。')
            return stage_candidate_file(
                store,
                candidate_path,
                export_root=export_root,
                bound_client=client,
                expected_request_id=expected_request_id,
            )

        def completed(value):
            result = show_candidate(store, value, parent=self.root, bound_client=client)
            self.status.set('画像已确认并保存。' if result.get('status') == 'confirmed'
                            else '画像候选尚未确认。')

        self.work('校验并暂存宿主画像候选', load, completed)
        return True

    def open_saved_profile(self, *, overview=False):
        if not self.private_ready():
            return
        from private_chat import default_store, load_saved_profile
        from private_profiles import ProfileStore
        from private_profile_ui import show_saved_profile
        export_root, client = Path(self.session.last_export), self.session.client
        def load():
            return load_saved_profile(ProfileStore(default_store()), export_root,
                                      bound_client=client, overview=overview)
        self.work('读取已确认画像与消息依据', load,
                  lambda value: show_saved_profile(value, parent=self.root))

    def open_private_insights(self):
        if not self.private_ready():
            return
        from private_chat import default_store, _bound
        from private_profiles import ProfileStore
        from private_insights import summarize
        from private_insights_ui import show_insights
        export_root = Path(self.session.last_export)
        client = self.session.client
        def load():
            source = ProfileStore(default_store()).inspect_export(export_root)
            _bound(source['identity']['client'], client)
            return source, summarize(source)
        def completed(value):
            source, summary = value
            show_insights(source, summary, parent=self.root,
                          save_report=lambda report: self.save_private_report(report, export_root))
        self.work('校验正式导出并统计消息', load, completed)

    def save_private_report(self, summary, export_root):
        if self.busy or self.read_busy or self.batch_running or self.closing:
            self.status.set('请等待当前操作完成后保存报告。')
            return False
        from private_chat import default_store, validate_report_destination
        from private_insights import write_report
        folder = filedialog.askdirectory(parent=self.root, title='选择报告保存位置（自动新建报告目录）')
        if not folder:
            return False
        # Native dialogs run a nested event loop; the main window may have
        # started another operation while the destination picker was open.
        if self.busy or self.read_busy or self.batch_running or self.closing:
            self.status.set('请等待当前操作完成后保存报告。')
            return False
        output = Path(folder) / ('私聊统计-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        def save():
            validate_report_destination(output, export_root, default_store())
            return write_report(summary, output)
        def completed(value):
            self.status.set('已保存私聊统计报告：' + str(output))
            messagebox.showinfo('统计报告已保存', 'JSON、Markdown 和离线 HTML 已保存到：\n' + str(output), parent=self.root)
        self.work('保存私聊统计报告', save, completed)
        return True

    def open_profile_candidate(self):
        if self.busy or self.read_busy or self.batch_running or self.closing:
            return
        from private_chat import default_store, show_candidate, _bound
        from private_profiles import ProfileStore
        root = default_store()
        path = filedialog.askopenfilename(parent=self.root, title='选择待复核候选 JSON',
            initialdir=str(root / 'candidates'), filetypes=[('候选 JSON', '*.json')])
        if not path:
            return
        selected = Path(path).absolute()
        if selected.parent != (root / 'candidates').absolute() or selected.is_symlink() or selected.is_junction():
            messagebox.showerror('候选位置不匹配', '请选择当前画像库 candidates 目录中的候选。', parent=self.root)
            return
        store = ProfileStore(root)
        client = self.session.client
        def load():
            value = store.candidate(selected.stem)
            _bound(value['identity']['client'], client)
            return value
        def completed(value):
            result = show_candidate(store, value, parent=self.root, bound_client=client)
            self.status.set('画像已确认并保存。' if result.get('status') == 'confirmed' else '画像候选尚未确认。')
        self.work('校验候选身份与原消息证据', load, completed)

    def open_diagnostics(self):
        path = startup.path()
        if path and path.is_file():
            try: os.startfile(str(path))
            except OSError: messagebox.showinfo('诊断日志', str(path), parent=self.root)

    def export_diagnostics_to(self,path):
        import shutil
        summary={'release':'private-'+PRIVATE_VERSION if self.session.conversation_mode=='direct' else GUI_RELEASE,'client':self.session.client,'conversationMode':self.session.conversation_mode,
                 'selected':len(self.selected_chats),'parsed':len(self.parsed_results),
                 'failed':sum(state=='失败' for state in self.parse_states.values()),'queued':len(self.batch_queue),
                 'batchTotal':len(self.batch_ids),'batchFailed':len(getattr(self,'batch_failed',[])),
                 'batchSucceeded':len(getattr(self,'batch_succeeded',[])),
                 'busy':self.busy,'batchRunning':self.batch_running,'copyMedia':self.copy_media.get(),
                 'sourceLoaded':self.session.archive is not None}
        source=self.session.settings.get('source',{})
        for key in ('ocrImages','ocrEnabled'):summary[key]=bool(source.get(key))
        for key,folder in (('dataFreeBytes',self.session.directory),('exportFreeBytes',self.session.export_directory)):
            try:summary[key]=shutil.disk_usage(folder).free
            except OSError:pass
        return startup.export_bundle(path,summary)

    def export_diagnostics(self):
        target=filedialog.asksaveasfilename(parent=self.root,title='导出诊断日志（不含聊天正文和密钥）',
            defaultextension='.zip',filetypes=[('诊断日志 ZIP','*.zip')],
            initialfile=self.session.client+'-diagnostics-'+('private-'+PRIVATE_VERSION if self.session.conversation_mode=='direct' else GUI_RELEASE)+'-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.zip')
        if not target:return
        try:
            result=self.export_diagnostics_to(target)
        except OSError as error:
            fault=startup.failure(error,'diagnostic_export_failed')
            messagebox.showerror('日志未能保存','请检查所选位置的写入权限与空间。解析队列未受影响。\n诊断编号：'+fault,parent=self.root)
            return
        messagebox.showinfo('诊断日志已导出',f"包含 {result['events']} 条事件、{result['errors']} 条异常。\n"+target+
            '\n\n可将此ZIP传回排查；解析中的日志只覆盖当前时刻。',parent=self.root)

    def report_operation_progress(self,event):
        startup.progress(event)
        self.export_events.put(event)

    def clear_account_cache(self):
        if self.busy or self.read_busy or self.batch_running or self.closing:
            self.status.set('请先等待当前操作完成，或停止解析队列，再清理账号缓存。')
            return
        label = '企微' if self.session.client == 'wecom' else '个微'
        text = (f'清除此工具的{label}账号缓存？\n\n'
                '将删除本客户端工具中的所有账号配置、缓存密钥、请求记录、解析副本、预览和复核缓存。'
                '尚未导出的复核结果也会清除。\n\n'
                '原始微信数据、已导出文件、其他客户端配置和此目录外的文件均保留。\n\n'
                f'清理目录：{self.session.directory}\n\n请先关闭其他解析工具窗口。清理无法撤销，确认后返回首次配置界面。')
        if not messagebox.askyesno('清除账号缓存', text, parent=self.source_dialog or self.root, icon='warning'):
            return
        self.close_source_settings(reason='cache_reset')
        self.work('清除' + label + '工具账号缓存', self.session.clear_account_cache, self.cache_cleared)

    def cache_cleared(self, value):
        # End a named Skill request with a redacted receipt instead of restoring it.
        self.open_request = None
        if self.open_result_path:
            try:
                self.open_result_path.parent.mkdir(parents=True, exist_ok=True)
                self.publish_open('failed', error='用户已清除账号缓存，请重新发起任务。', code='profile_reset')
            except OSError:
                pass
        self.open_result_path = None
        self.groups = []
        self.clear_chat()
        self.selected_chats.clear()
        self.parsed_results.clear()
        self.parse_states.clear()
        self.parse_errors.clear()
        self.last_completed_chat = self.batch_output = self.receipt_chat = None
        self.resumed_snapshot = ''
        self.batch_ids.clear()
        self.batch_queue.clear()
        self.batch_cancel_requested = False
        self.pending_config = {}
        self.account_scan = {}
        self.account_text.set('当前账号未配置 · 请重新选择本人账号')
        self.search.set('')
        self.review_filter.set(self.ui_text('全部群聊'))
        self.render_groups()
        self.subtitle.set(value['message'])
        self.ready({'status': 'needs_source', 'message': value['message']})
        if not value['ok']:
            messagebox.showerror('缓存未完全清理', value['message'], parent=self.root)

    def _drain(self):
        self.drain_export_progress()
        try:
            callback, value, issue, on_error = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.drain_export_progress()
            self.progress.stop()
            if self.source_progress is not None:
                self.source_progress.stop()
            if self.closing and not self.read_busy:
                self.save_progress_selection()
                self.destroyed = True
                self.root.destroy()
                return
            if issue:
                self.status.set('未完成：' + issue)
                if on_error:
                    on_error(issue)
                elif self.open_request:
                    self.publish_open('needs_source' if self.source_dialog is not None else 'failed', error=issue)
                if self.check_report and not on_error:
                    self.finish_check({'error': issue})
                    return
            else:
                try:
                    callback(value)
                except Exception as error:
                    self.callback_error(type(error), error, error.__traceback__)
                    if on_error:
                        on_error(safe_issue(error))
                if self.destroyed:
                    return
            self.update_buttons()
        self.drain_view_read()
        if self.closing and not self.busy and not self.read_busy:
            self.save_progress_selection()
            self.destroyed = True
            self.root.destroy()
            return
        self.root.after(80, self._drain)

    def drain_export_progress(self):
        """Consume worker notifications without completing the running operation."""
        latest = None
        while True:
            try:
                latest = self.export_events.get_nowait()
            except queue.Empty:
                break
        if latest is None or not self.busy or self.closing:
            return
        name = latest.get('chatName', '')
        if len(name) > 36:
            name = name[:35] + '…'
        if latest.get('operation') == 'parse':
            labels = {'parsing': '解析记录', 'finalizing': '整理分析材料', 'media_search': '核查本机媒体缓存',
                      'indexing': '准备阅读', 'saving': '保存复核进度',
                      'supplement_search': '核查补充原件目录', 'attachment_text': '提取附件与OCR文字'}
            position = len(self.batch_ids) - len(self.batch_queue)
            detail = (f"正在解析 {position}/{len(self.batch_ids)} · 已处理 {latest.get('processed', 0):,} 条 · "
                      + labels[latest['stage']])
            if latest['stage'] == 'attachment_text':
                detail += f" · 已处理 {latest.get('processedFiles', 0):,} 个文件"
            if latest['stage'] == 'supplement_search':
                detail += f" · 已核查 {latest.get('filesChecked', 0):,} 个文件 · 已补齐 {latest.get('recoveredReferences', 0):,} 处"
            if latest['stage'] == 'media_search':
                detail += f" · 已核查 {latest.get('files', 0):,} 个文件 · 本轮读取 {latest.get('bytes', 0) / 1048576:.0f} MiB"
        else:
            labels = {'validating': '核对复核结果', 'exporting': '正在保存', 'saved': '已保存',
                      'failed': '当前群保存未完成', 'analysis': '生成跨群分析材料', 'completed': '整理导出结果'}
            detail = f"已保存 {latest['completed']}/{latest['total']} 群 · {labels[latest['stage']]}"
        if name:
            detail += '：' + name
        self.status.set(detail)
        self.workflow_hint.set(detail)

    def update_buttons(self):
        state = 'disabled' if self.busy or self.read_busy or self.closing or self.batch_running else 'normal'
        browse_state = 'normal' if self.can_browse() else 'disabled'
        self.status_dot.set_state('busy' if self.busy or self.read_busy or self.batch_running
                                  else 'ok' if self.session.last_export else 'idle')
        if self.busy or self.read_busy or self.batch_running:
            self.progress.pack(side='right', padx=(0, 14))
        else:
            self.progress.pack_forget()
        for widget, original_state in self.source_controls:
            widget.configure(state='disabled' if state == 'disabled' else original_state)
        for widget in (self.refresh_button, self.source_button, self.scan_button):
            widget.configure(state=state)
        for widget in self.date_controls:
            widget.configure(state=state)
        if hasattr(self, 'private_insights_button'):
            ready = state if self.session.last_export else 'disabled'
            self.private_insights_button.configure(state=ready)
            self.private_prepare_button.configure(state=ready)
            self.private_candidate_button.configure(state=ready)
            self.private_library_button.configure(state=state)
            self.private_identity_button.configure(state=state)
            self._fit_private_empty()
            phase = 2 if self.session.last_export else 1 if self.session.pending_review else 0
            for index, label in enumerate(self.private_step_labels):
                label.configure(background=private_theme.ACCENT_SOFT if index == phase else private_theme.SURFACE,
                                foreground=private_theme.ACCENT_TEXT if index <= phase else private_theme.TEXT_MUTED)
            self.repair_metadata_button.configure(state=state if self.batch_output else 'disabled')
            if self.session.last_export or self.batch_output:
                self.file_row.grid()
            else:
                self.file_row.grid_remove()
        else:
            self.group_identity_button.configure(state=state)
            self.export_location_button.configure(state=state)
            self.fit_group_empty()
            self.group_file_button.configure(state=state if self.session.last_export or self.batch_output else 'disabled')
            for index, available in ((0, bool(self.session.last_export)), (1, bool(self.session.last_export)),
                                     (6, bool(self.batch_output)),
                                     (7, bool(self.batch_output and
                                              (self.batch_output / 'batch-analysis' / 'README.md').is_file())),
                                     (11, bool(self.session.last_export and (self.session.last_export / '补齐清单.md').is_file())),
                                     (12, bool(self.session.last_export and (self.session.last_export / '补齐清单.csv').is_file()))):
                self.group_file_menu.entryconfigure(index, state='normal' if available else 'disabled')
            if self.session.last_export or self.batch_output:
                self.file_row.grid()
            else:
                self.file_row.grid_remove()
        self.repair_metadata_button.configure(state=state if self.batch_output else 'disabled')
        self.search_entry.configure(state=browse_state)
        self.search_clear_button.configure(state=browse_state if self.search.get() else 'disabled')
        self.review_filter_choice.configure(state='readonly' if browse_state == 'normal' else 'disabled')
        targets, needed = self.parse_plan()
        self.parse_button.configure(state=state if targets else 'disabled')
        self.reparse_button.configure(state=state if any(chat in self.parsed_results for chat in targets) else 'disabled')
        self.select_all_button.configure(state=browse_state if self.tree.get_children() else 'disabled')
        self.clear_selection_button.configure(state=browse_state if self.selected_chats else 'disabled')
        self.selection_menu_button.configure(state=browse_state if self.groups else 'disabled')
        self.cancel_batch_button.configure(state='normal' if self.batch_running and not self.batch_cancel_requested and not self.closing else 'disabled')
        if self.batch_running and not self.batch_cancel_requested and not self.closing:
            self.cancel_batch_button.pack(side='right')
        else:
            self.cancel_batch_button.pack_forget()
        pending = self.review_targets()
        self.next_review_button.configure(state=browse_state if any(key != self.active_chat for key in pending) else 'disabled',
            text=f"{self.ui_text('下一个未复核群 · ')}{len(pending)}" if len(pending) > 1 else
                 self.ui_text('查看待复核群 · 1') if pending and pending[0] != self.active_chat else
                 self.ui_text('当前群待复核') if pending else self.ui_text('暂无待复核群'))
        self.tree.state(['disabled'] if browse_state == 'disabled' else ['!disabled'])
        readable = self.active_chat and self.parsed_chat == self.active_chat
        self.kind_choice.configure(state='readonly' if browse_state == 'normal' and readable else 'disabled')
        self.previous_button.configure(state='normal' if browse_state == 'normal' and readable and self.page_offset > 0 else 'disabled')
        self.next_button.configure(state='normal' if browse_state == 'normal' and readable and self.page_offset + int(self.page_size.get()) < self.page_total else 'disabled')
        self.first_button.configure(state=self.previous_button.cget('state'))
        self.last_button.configure(state=self.next_button.cget('state'))
        self.message_search_entry.configure(state=browse_state if readable else 'disabled')
        self.message_search_button.configure(state=browse_state if readable else 'disabled')
        self.message_clear_button.configure(state=browse_state if readable else 'disabled')
        self.page_size_choice.configure(state='readonly' if browse_state == 'normal' else 'disabled')
        self.review_button.configure(state='normal' if state == 'normal' and readable and self.session.pending_review else 'disabled')
        reviewed = sum(self.review_label(chat) == '已复核' for chat in self.selected_chats)
        exported = sum(self.review_label(chat) == '已复核' and self.session.review_status(chat) == '已导出' for chat in self.selected_chats)
        ready = reviewed - exported
        waiting = len(self.selected_chats) - reviewed
        self.review_summary.set(f"所选 {len(self.selected_chats)}{self.ui_text(' 群 · 未复核 ')}{waiting} · 已复核 {reviewed}（含已导出 {exported}）")
        dirty = self.active_chat in self.selected_chats and not self.range_matches()
        if dirty:
            self.review_summary.set(self.review_summary.get() + self.ui_text(' · 当前群范围已变，请重新解析并复核'))
        eligible = ready > 0 and waiting == 0 and not dirty
        unparsed = sum(chat not in self.parsed_results for chat in self.selected_chats)
        self.batch_review_button.configure(text=f"批量复核 · {waiting}{self.ui_text(' 群')}" if waiting else self.ui_text('批量复核所选群'),
            state='normal' if state == 'normal' and waiting and not unparsed and not dirty and
                  all(self.session.review_status(chat) in ('待复核', '已复核', '已导出') for chat in self.selected_chats) else 'disabled')
        if self.closing:
            hint = '正在完成当前操作，随后关闭窗口。'
        elif self.batch_running:
            position = len(self.batch_ids) - len(self.batch_queue)
            hint = (f"后台解析 {position}/{len(self.batch_ids)} · 已完成 {len(self.batch_succeeded)}{self.ui_text(' 群可查看；结束或停止后复核。')}")
        elif self.busy:
            hint = '正在处理本地数据，请稍候。'
        elif dirty:
            hint = '当前范围已变，请重新解析并复核。'
        elif not self.selected_chats:
            hint = self.ui_text('先勾选群聊，解析后复核。')
        elif unparsed:
            hint = f"还有 {unparsed}{self.ui_text(' 群未解析；其他已有结果将复用。')}"
        elif waiting:
            hint = f"还有 {waiting}{self.ui_text(' 群未复核，可“批量复核”或“复核当前群”。')}"
        elif ready:
            hint = f"{self.ui_text('所选群已全部复核，可导出 ')}{ready}{self.ui_text(' 群。')}"
        else:
            hint = self.ui_text('所选群已导出，可查看文件或打开目录。')
        self.workflow_hint.set(hint)
        self.export_button.configure(state='normal' if state == 'normal' and eligible else 'disabled')
        self.batch_folder_button.configure(state=state if self.batch_output else 'disabled')
        result_state = 'normal' if state == 'normal' and self.session.last_export else 'disabled'
        self.open_button.configure(state=result_state)
        self.folder_button.configure(state=result_state)
        if self.batch_output:
            self.folder_button.configure(state=state)
        self.image_page_button.configure(state=result_state)
        for kind, button in self.export_file_buttons.items():
            name = {'JSON': 'messages.json', 'JSONL': 'messages.jsonl', 'Markdown': 'messages.md'}[kind]
            exists = self.session.last_export and (self.session.last_export / name).is_file()
            button.configure(state=result_state if exists else 'disabled')
            if self.session.conversation_mode == 'group':
                self.group_file_menu.entryconfigure(('JSON', 'JSONL', 'Markdown').index(kind) + 2,
                                                    state='normal' if exists else 'disabled')
        self.update_review_display()
        self.update_parse_caption()
        self.update_completion_notice()

    def ready(self, value):
        if value['status'] != 'ready':
            startup.record('waiting_for_account_configuration', automaticClose=False)
            self.status.set(value['message'])
            self.source_issue = value['message']
            self.pending_config = value.get('config', {})
            self.publish_open('needs_source', message=value['message'])
            if self.check_report:
                self.finish_check({'error': value['message']})
            elif self.source_dialog is None:
                self.root.after(100, self.scan)
            return
        self.close_source_settings(reason='validated')
        startup.record('account_configuration_validated')
        self.pending_config = {}
        self.source_issue = ''
        self.groups = value['groups']
        self.clear_chat()
        self.selected_chats.clear()
        self.parsed_results.clear()
        self.last_completed_chat = None
        self.parse_states.clear()
        self.parse_errors.clear()
        self.batch_output = None
        restored = value.get('restoredReviews') or value.get('restoredPublished') or value.get('resumeSelection')
        if restored:
            self.parsed_results.update(self.session.restored_results)
            self.selected_chats.update(value.get('resumeSelection', []))
            self.parse_states.update({chat: self.session.review_status(chat) for chat in self.parsed_results})
            self.last_completed_chat = value.get('resumeActiveChat')
            self.batch_output = self.session.last_batch_root
        self.restored_selection_pending = bool(value.get('resumeSelection'))
        self.review_filter.set(self.ui_text('全部群聊'))
        self.search.set((self.open_request or {}).get('chat_name') or '')
        source = Path(self.session.archive.media_config.get('sourceRoot', ''))
        label = source.parent.name if source.name in ('db_storage', 'Data') else source.name
        nickname = value.get('accountProfile', {}).get('accountNickname') or '昵称未取得'
        if self.session.client == 'wecom':
            profile = value.get('accountProfile', {})
            nickname += ' · ' + (profile.get('companyName') or profile.get('companyShortName') or '企业名称未获取')
        self.account_text.set('当前账号：' + nickname + ' · 目录：' + label + self.ui_text(' · 全部本地群 ') + str(len(self.groups)) + self.ui_text(' 个 · 按群 ID 选择'))
        self.render_groups()
        stamp = datetime.fromisoformat(value['snapshotTime']).astimezone(BEIJING).strftime('%Y-%m-%d %H:%M:%S')
        self.resumed_snapshot = stamp if restored else ''
        self.subtitle.set(value['message'] + ' · 副本时间 ' + stamp + '（北京时间）')
        self.status.set(self.ui_text('已列出全部本地群聊；选择群 ID 后解析，人工确认后导出。') + (' ' + value['notice'] if value.get('notice') else ''))
        if self.restored_selection_pending:
            self.status.set(f'已恢复 {len(self.selected_chats)} 个勾选，尚未开始解析；请调整范围，点击解析后确认本次数量。')
        self.result_text.set(('已保存文件：' + str(self.batch_output)) if self.batch_output
                             else '本机工作目录：' + str(self.session.directory))
        self.check_data.update(groups=len(self.groups), sourceStatus=value['message'], snapshotTime=value['snapshotTime'])
        if self.open_request:
            try:
                chat = self.session.select_chat(**self.open_request)
            except ValueError:
                self.status.set(self.ui_text('指定群不存在或名称重复，请按群 ID 选中正确群，再点“解析所选群”。'))
                self.publish_open('needs_chat', message=self.status.get())
                if self.check_report:
                    self.finish_check({'error': self.status.get()})
                return
            self.start_parse(chat['id'])
            return
        if self.check_chat:
            candidates = [group['id'] for group in self.groups if group['name'] == self.check_chat]
            if len(candidates) != 1:
                self.finish_check({'error': '检查会话不存在或名称不唯一。'})
                return
            self.start_parse(candidates[0])
        elif self.check_report:
            self.finish_check({'ok': True})
        else:
            if value.get('resumeActiveChat') in self.parsed_results:
                self.show_chat(value['resumeActiveChat'])
            self.search_entry.focus_set()

    def clear_chat(self):
        self.read_serial += 1
        self.read_pending = None
        self.active_chat = self.parsed_chat = None
        self.session.activate_review(None)
        self.auto_parse_result = None
        self.chat_title.set(self.ui_text('勾选多个群后解析，单击群名查看结果'))
        self.root.title(self.client_title)
        self.browser.clear()
        self.page_text.set('')
        self.result_text.set('')
        self.page_offset = self.page_total = 0

    def render_groups(self):
        selected = self.active_chat
        for item in self.tree.get_children():
            self.tree.delete(item)
        query = self.search.get().strip()
        recent_key = 'lastDirectChat' if getattr(self.session, 'conversation_mode', 'group') == 'direct' else 'lastChat'
        choices = group_choices(self.groups, query, self.session.settings.get(recent_key))
        scope = self.review_filter.get()
        if scope == '已解析可查看':
            available = set(self.completed_groups())
            choices = [group for group in choices if group['id'] in available]
        elif scope != self.ui_text('全部群聊'):
            choices = [group for group in choices if group['id'] in self.selected_chats]
        if scope in ('所选未复核', '所选已复核'):
            label = '未复核' if scope == '所选未复核' else '已复核'
            choices = [group for group in choices if self.review_label(group['id']) == label]
        self.group_title.set(f'{scope} · {len(choices)} 个' if scope != self.ui_text('全部群聊') else
                             f"{self.ui_text('匹配群聊 · ')}{len(choices)} 个" if query else f"{self.ui_text('全部本地群聊 · ')}{len(choices)} 个")
        self.group_hint.set((self.ui_text('已完成群可立即阅读；本次队列固定。\n勾选用于下次解析或导出。') if self.batch_running else
                            '表头勾选可全选/取消；更多中可反选。\n搜索保留隐藏勾选，回车只定位。') if choices else
                            self.ui_text('没有匹配群聊；切换“全部群聊”或清除搜索。已有勾选保留。'))
        name_font = tkfont.Font(font=ttk.Style(self.root).lookup('Treeview', 'font'))
        name_width, id_width = 220, 150
        for group in choices:
            name = ('上次查看 · ' if group['recent'] else '') + group['choiceLabel']
            name_width = max(name_width, name_font.measure(name) + 24)
            id_width = max(id_width, name_font.measure(group['id']) + 24)
            count = group.get('localMessageCount', '有记录' if group.get('hasLocalMessages') else '暂无')
            review, tag = self.list_status(group['id'])
            self.tree.insert('', 'end', iid=group['id'], values=('☑' if group['id'] in self.selected_chats else '☐', name, review, count, group['id'], self.parse_states.get(group['id'], '未解析')),
                             tags=(tag,))
        self.tree.column('name', width=name_width)
        self.tree.column('id', width=id_width, stretch=False)
        if selected and self.tree.exists(selected):
            self.tree.selection_set(selected)
        self.update_selection_text()
        self.update_buttons()

    def review_label(self, chat):
        if chat == self.active_chat and not self.range_matches():
            return '未复核'
        return '已复核' if self.session.review_status(chat) in ('已复核', '已导出') else '未复核'

    def list_status(self, chat):
        phase = self.parse_states.get(chat, '未解析')
        if phase == '解析中':
            return '解析中…', 'parsing'
        if phase == '排队中':
            return phase, 'queued'
        if phase == '失败':
            return '解析失败', 'failed'
        if self.review_label(chat) == '已复核':
            return '已复核', 'reviewed'
        if chat in self.parsed_results:
            return '已解析·未复核', 'parsed'
        return phase, 'unreviewed'

    def completed_groups(self):
        return [group['id'] for group in self.groups if group['id'] in self.parsed_results
                and self.parse_states.get(group['id']) not in ('排队中', '解析中', '失败')]

    def update_completion_notice(self):
        if hasattr(self, 'completeness_values'):
            from offline_completeness import summarize, compact_values
            current = self.parsed_results.get(self.active_chat)
            values = compact_values(summarize([current['report']] if current else []))
            for index, (variable, text) in enumerate(zip(self.completeness_values, values)):
                if self._quality_compact and index in (1, 2): text = text.split(' / ')[0]
                variable.set((('记录 ', '原件 ', '正文 ', '待核对 ')[index] if self._quality_compact else '') + text)
        chats = self.completed_groups()
        if chats and not self.completion_banner.winfo_manager():
            self.completion_banner.pack(fill='x', pady=(8, 0))
        elif not chats and self.completion_banner.winfo_manager():
            self.completion_banner.pack_forget()
        if getattr(self.session, 'conversation_mode', 'group') == 'direct':
            self._update_private_reading_minimum()
        self.completion_text.set(f"✓ 已解析 {len(chats)}{self.ui_text(' 个群，可立即查看')}" if chats else self.ui_text('尚无已解析群，完成后将在此提示'))
        if chats and self.resumed_snapshot:
            self.completion_text.set(f"✓ 已恢复 {len(chats)}{self.ui_text(' 个群 · 上次副本')}")
        self.completed_groups_button.configure(text=f"{self.ui_text('查看已解析群 · ')}{len(chats)}",
            state='normal' if chats and self.can_browse() else 'disabled')
        latest = self.parsed_results.get(self.last_completed_chat)
        if latest and self.last_completed_chat in chats:
            self.completion_detail.set(f'刚完成：{latest["chat"]["name"]} · {latest["report"]["exportedRows"]} 条')
        else:
            self.completion_detail.set('蓝色：已解析待复核 · 黄色：解析中 · 绿色：已复核')
        if self.resumed_snapshot:
            self.completion_detail.set(f'副本时间：{self.resumed_snapshot} · 新消息请点击刷新')

    def show_completed_groups(self):
        chats = self.completed_groups()
        if not chats or not self.can_browse():
            return
        self.review_filter.set('已解析可查看')
        self.search.set('')
        self.render_groups()
        self.focus_chat(self.active_chat if self.active_chat in chats else chats[0])

    def range_changed(self):
        if self.review_filter.get() in ('所选未复核', '所选已复核'):
            self.render_groups()
        else:
            self.update_buttons()

    def update_review_display(self):
        for chat in self.tree.get_children():
            label, tag = self.list_status(chat)
            self.tree.set(chat, 'review', label)
            self.tree.item(chat, tags=(tag,))
        chat = self.active_chat
        self.current_group_id.set(chat or '')
        self.copy_current_id_button.configure(state='normal' if chat and not self.closing else 'disabled')
        if not chat:
            self.current_review_state.set(self.ui_text('未选群'))
            self.current_review_badge.set(self.ui_text('未选群'), 'none')
            self.current_review_hint.set(self.ui_text('单击群名查看；勾选群聊决定解析与导出范围'))
            return
        label = self.review_label(chat)
        self.current_review_state.set(label)
        self.current_review_badge.set(label, 'done' if label == '已复核' else 'pending')
        selected = '已勾选' if chat in self.selected_chats else '未勾选'
        phase = self.parse_states.get(chat, '未解析')
        if not self.range_matches():
            hint = '范围已变化，请重新解析并复核'
        elif phase in ('排队中', '解析中'):
            hint = phase + '，请稍候'
        elif self.session.review_status(chat) == '已导出':
            hint = self.ui_text('已导出，可查看当前群文件')
        elif label == '已复核':
            hint = self.ui_text('复核已保存，可一键导出所选群')
        elif phase == '待复核':
            hint = self.ui_text('已解析，点击“复核当前群”')
        else:
            hint = '请先解析，再进行人工复核'
        self.current_review_hint.set(selected + ' · ' + hint)
        self.review_button.configure(text='查看/修改复核' if label == '已复核' and self.session.pending_review else self.ui_text('复核当前群'))

    def selection_changed(self):
        self.save_progress_selection()
        if self.review_filter.get() != self.ui_text('全部群聊'):
            self.render_groups()
        else:
            self.update_selection_text()
            self.update_buttons()

    def save_progress_selection(self):
        try:
            self.session.remember_selection([group['id'] for group in self.groups if group['id'] in self.selected_chats],
                                            self.active_chat, persist=not self.busy)
        except ValueError as error:
            self.status.set(safe_issue(error))

    def update_selection_text(self):
        visible = set(self.tree.get_children())
        hidden = len(self.selected_chats - visible)
        self.selection_text.set(f"已选 {len(self.selected_chats)}{self.ui_text(' 个群')}" + (f'（隐藏 {hidden}）' if hidden else ''))
        picked = self.selected_chats & visible
        all_picked = bool(visible) and picked == visible
        self.tree.heading('pick', text='☑' if all_picked else '▣' if picked else '☐')
        self.select_all_button.configure(text='取消全选当前结果' if all_picked else '全选当前结果')
        self.update_parse_caption()

    def update_parse_caption(self):
        targets, needed = self.parse_plan()
        if self.batch_running:
            text = f'正在解析 {len(self.batch_ids) - len(self.batch_queue)}/{len(self.batch_ids)}'
        elif needed:
            text = f"解析待处理 {len(needed)}{self.ui_text(' 群')}" if self.selected_chats else '解析搜索结果' if self.search.get().strip() else self.ui_text('解析当前群')
        elif targets:
            text = self.ui_text('查看待复核群') if any(self.session.review_status(chat) == '待复核' for chat in targets) else '查看已有结果'
        else:
            text = self.ui_text('先选择群聊')
        self.parse_button.configure(text=text)

    def parse_targets(self):
        if self.selected_chats:
            return [group['id'] for group in self.groups if group['id'] in self.selected_chats]
        query = self.search.get().strip()
        selected = self.tree.selection()
        if not query:
            chat = selected[0] if selected else self.active_chat
            return [chat] if chat and any(group['id'] == chat for group in self.groups) else []
        choices = self.tree.get_children()
        if selected:
            return [selected[0]]
        exact = [group['id'] for group in self.groups if group['id'] in choices and
                 (group['name'] == query or group['id'] == query)]
        matches = exact or list(choices)
        return [matches[0]] if len(matches) == 1 else []

    def parse_plan(self):
        targets = self.parse_targets()
        options = (self.start_date.get().strip() or None, self.end_date.get().strip() or None, self.copy_media.get())
        needed = []
        for chat in targets:
            summary = self.parsed_results.get(chat, {}).get('review')
            if (not summary or self.session.review_status(chat) == '未解析' or options !=
                    (summary['startDate'], summary['endDate'], summary['copyMediaRequested'])):
                needed.append(chat)
        return targets, needed

    def toggle_group(self, chat):
        if not self.can_browse() or not self.tree.exists(chat):
            return
        if chat in self.selected_chats:
            self.selected_chats.remove(chat)
        else:
            self.selected_chats.add(chat)
        self.tree.set(chat, 'pick', '☑' if chat in self.selected_chats else '☐')
        self.selection_changed()

    def toggle_group_click(self, event):
        if not self.can_browse():
            return 'break'
        item = self.tree.identify_row(event.y)
        if item and self.tree.identify_column(event.x) == '#1':
            self.toggle_group(item)
            self.tree.focus_set()
            if self.tree.exists(item):
                self.tree.focus(item)
                self.tree.selection_set(item)
            return 'break'

    def toggle_focused_group(self, _event=None):
        self.toggle_group(self.tree.focus())
        return 'break'

    def select_visible(self):
        if self.can_browse():
            self.selected_chats.update(self.tree.get_children())
            for chat in self.tree.get_children():
                self.tree.set(chat, 'pick', '☑')
            self.selection_changed()
        return 'break'

    def toggle_visible(self):
        visible = set(self.tree.get_children())
        return self.deselect_visible() if visible and visible <= self.selected_chats else self.select_visible()

    def deselect_visible(self):
        if self.can_browse():
            self.selected_chats.difference_update(self.tree.get_children())
            self.render_groups()
        return 'break'

    def invert_visible(self):
        if self.can_browse():
            self.selected_chats.symmetric_difference_update(self.tree.get_children())
            self.render_groups()
        return 'break'

    def select_all_groups(self):
        if self.can_browse():
            self.selected_chats.update(group['id'] for group in self.groups)
            self.render_groups()
        return 'break'

    def clear_selection(self):
        if self.can_browse():
            self.selected_chats.clear()
            for chat in self.tree.get_children():
                self.tree.set(chat, 'pick', '☐')
            self.selection_changed()

    def copy_group_id(self):
        selected = [group['id'] for group in self.groups if group['id'] in self.selected_chats] or list(self.tree.selection())
        if selected:
            self.root.clipboard_clear()
            self.root.clipboard_append('\n'.join(selected))
            self.status.set(f"已复制 {len(selected)}{self.ui_text(' 个群 ID，每行一个。')}")

    def copy_current_group_id(self):
        if self.active_chat:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.active_chat)
            self.status.set(self.ui_text('已复制当前查看群的 ID。'))

    def locate_search_result(self, _event=None):
        if not self.can_browse():
            return 'break'
        query = self.search.get().strip()
        choices = self.tree.get_children()
        exact = [group['id'] for group in self.groups if group['id'] in choices and
                 (group['name'] == query or group['id'] == query)] if query else []
        matches = exact or list(choices)
        if len(matches) == 1:
            self.focus_chat(matches[0])
        elif matches:
            self.group_hint.set(f"找到 {len(matches)}{self.ui_text(' 个候选；请按群 ID 勾选或单击查看。')}")
            self.status.set(self.ui_text('搜索只定位群聊；请确认群 ID 后点击解析。'))
            self.tree.focus_set()
        else:
            self.status.set(self.ui_text('没有匹配群聊，请修改搜索或点击“清除”。'))
        return 'break'

    def choose_search_result(self, _event=None):
        if self.busy or self.batch_running or self.closing:
            return
        targets, needed = self.parse_plan()
        if needed:
            self.start_batch(needed, reused=len(targets) - len(needed))
        elif targets:
            pending = [chat for chat in targets if self.session.review_status(chat) == '待复核']
            if pending:
                chat = pending[(pending.index(self.active_chat) + 1) % len(pending)] if self.active_chat in pending else pending[0]
            else:
                chat = self.active_chat if self.active_chat in targets else targets[0]
            self.focus_chat(chat)
        else:
            self.locate_search_result()

    def reparse_targets(self):
        if not self.busy and not self.batch_running and not self.closing:
            self.start_batch(self.parse_targets())

    def selection(self, _event=None):
        if not self.can_browse():
            if self.active_chat and self.tree.exists(self.active_chat) and self.tree.selection() != (self.active_chat,):
                self.tree.selection_set(self.active_chat)
            return
        selected = self.tree.selection()
        if not selected or selected[0] == self.active_chat:
            return
        self.show_chat(selected[0])

    def show_chat(self, chat):
        self.clear_chat()
        self.active_chat = chat
        self.session.activate_review(chat)
        group = next(group for group in self.groups if group['id'] == chat)
        self.chat_title.set(group['name'])
        self.root.title(self.client_title + ' — ' + group['name'])
        self.filter_kind.set('业务阅读')
        self.message_search.set('')
        result = self.parsed_results.get(chat)
        if result:
            self.auto_parse_result = result
            self.parsed_chat = chat
            summary = result['review']
            self.start_date.set(summary['startDate'] or '')
            self.end_date.set(summary['endDate'] or '')
            self.copy_media.set(summary['copyMediaRequested'])
            if 'messages' not in result['view']:
                if self.session.last_export:
                    self.result_text.set('已保存文件：' + str(self.session.last_export))
                elif self.batch_output:
                    self.result_text.set('已保存文件所在批次：' + str(self.batch_output))
                size = int(self.page_size.get())
                if self.session.conversation_mode == 'direct':
                    self.work('恢复已解析会话阅读', lambda: self.session.preview(chat, kind='business', page_size=size),
                              lambda page: self.restored_view_ready(chat, page))
                    return
                self.status.set(self.ui_text('正在准备当前群阅读，解析队列继续运行…') if self.batch_running else
                                self.ui_text('正在准备当前群阅读…'))
                self.read_view(chat, lambda view: view.page(kind='business', page_size=size),
                               lambda page: self.restored_view_ready(chat, page))
                return
            self.preview_ready(result['view'])
            self.result_text.set((self.ui_text('当前群文件目录：') + str(self.session.last_export)) if self.session.last_export else
                                 self.review_label(chat) + self.ui_text('群 ID：') + chat + ' · 源记录 ' + str(result['report']['exportedRows']) + ' 条')
            self.status.set((self.ui_text('已导出当前群。') if self.session.last_export else
                             self.ui_text('当前群已复核，勾选所需群后一键导出。') if self.parse_states.get(chat) == '已复核' else
                             self.ui_text('当前群解析完成，等待人工复核。')) + result['report']['mediaSummaryText'])
        else:
            self.status.set(self.parse_errors.get(chat) or (self.ui_text('当前群 ID：') + chat + self.ui_text('。勾选要解析的群后点击解析。')))
        self.update_buttons()

    def restored_view_ready(self, chat, page):
        self.parsed_results[chat]['view'] = page
        self.show_chat(chat)

    def start_parse(self, chat):
        self.start_batch([chat])

    def start_batch(self, chats, *, reused=0):
        if self.busy or self.batch_running or self.closing:
            return
        known = {group['id'] for group in self.groups}
        chats = list(dict.fromkeys(chat for chat in chats if chat in known))
        if not chats:
            return
        if self.restored_selection_pending and not self.open_request and not self.check_report:
            if not messagebox.askyesno('确认本次解析范围', f'已恢复上次勾选；本次将解析 {len(chats)} 个会话。\n请核对选择范围，是否开始？', parent=self.root):
                return
            self.restored_selection_pending = False
        self.save_progress_selection()
        start, end = self.start_date.get().strip() or None, self.end_date.get().strip() or None
        try:
            date_range(start, end)
        except ValueError as error:
            self.status.set(str(error))
            return
        self.batch_ids = chats
        self.batch_reused = reused
        self.batch_queue = list(chats)
        self.batch_options = (start, end, self.copy_media.get(), int(self.page_size.get()))
        self.batch_running = True
        self.batch_cancel_requested = False
        self.batch_succeeded = []
        self.batch_failed = []
        self.batch_previous_states = {chat: self.parse_states.get(chat, '未解析') for chat in chats}
        if self.open_request and self.receipt_chat is None:
            self.receipt_chat = chats[0]
        for chat in chats:
            self.parse_states[chat] = '排队中'
        self.render_groups()
        self.parse_next()

    def cancel_batch(self):
        if self.batch_running:
            self.batch_cancel_requested = True
            self.status.set(self.ui_text('已请求停止；当前群解析结束后停止，已完成结果保留。'))
            self.update_buttons()

    def parse_next(self):
        if self.closing:
            return
        if self.batch_cancel_requested or not self.batch_queue:
            stopped = len(self.batch_queue)
            for chat in self.batch_queue:
                previous = self.batch_previous_states[chat]
                self.parse_states[chat] = previous if previous in ('待复核', '已复核', '已导出') else '已停止'
            self.batch_queue = []
            self.batch_running = False
            if self.batch_succeeded and not self.active_chat:
                self.show_chat(self.batch_succeeded[0])
            self.session.activate_review(self.active_chat)
            self.render_groups()
            self.status.set(f"本次解析完成 {len(self.batch_succeeded)}{self.ui_text(' 个群，复用 ')}{self.batch_reused} 个，失败 {len(self.batch_failed)} 个，停止 {stopped}{self.ui_text(' 个。逐群复核后再导出。')}")
            self.update_buttons()
            return
        chat = self.batch_queue.pop(0)
        self.parse_errors.pop(chat, None)
        self.parse_states[chat] = '解析中'
        if not self.active_chat or self.active_chat == chat:
            self.show_chat(chat)
        else:
            self.session.activate_review(self.active_chat)
        self.render_groups()
        group = next(group for group in self.groups if group['id'] == chat)
        if chat == self.receipt_chat:
            self.publish_open('parsing', chatName=group['name'], chatId=chat)
        start, end, media, size = self.batch_options
        position = len(self.batch_ids) - len(self.batch_queue)
        group_mode = self.session.conversation_mode == 'group'
        options = ({'load_view': bool(self.check_report),
                    'progress': lambda event: self.report_operation_progress({'operation': 'parse', **event})}
                   if group_mode else {})
        self.work(f'正在解析 {position}/{len(self.batch_ids)}：{group["name"]}',
                  lambda: self.session.prepare_review(chat, start=start, end=end, copy_media=media,
                                                      page_size=size, **options),
                  self.parsed_ready, on_error=lambda issue: self.parse_failed(chat, issue),
                  diagnostic={'target':chat,'position':position,'total':len(self.batch_ids)})

    def parse_failed(self, chat, issue):
        previous = self.session.review_status(chat)
        preserved = chat in self.parsed_results and previous in ('待复核', '已复核', '已导出')
        if preserved:
            self.parse_states[chat] = previous
            issue = '本次重新解析失败，已保留上次结果。' + issue
            if self.active_chat == chat:
                self.show_chat(chat)
        else:
            self.parsed_results.pop(chat, None)
            if self.active_chat == chat:
                self.clear_chat()
            self.parse_states[chat] = '失败'
        self.parse_errors[chat] = issue
        self.batch_failed.append(chat)
        if chat == self.receipt_chat:
            self.publish_open('failed', ok=False, chatId=chat, error=issue)
            self.open_request = None
        if self.check_report:
            self.finish_check({'error': issue})
            return
        self.root.after(1, self.parse_next)

    def parsed_ready(self, value):
        chat = value['chat']['id']
        self.parsed_results[chat] = value
        self.last_completed_chat = chat
        self.parse_states[chat] = '待复核'
        self.batch_succeeded.append(chat)
        if self.active_chat == chat or not self.active_chat:
            self.show_chat(chat)
        else:
            self.session.activate_review(self.active_chat)
        self.check_data['parseAndOpen'] = True
        if chat == self.receipt_chat:
            self.publish_open('awaiting_review', ok=False, chatName=value['chat']['name'], chatId=value['chat']['id'],
                          viewRoot=value['view']['viewRoot'], reviewId=value['review']['reviewId'],
                          exportedRows=value['report']['exportedRows'], businessRows=value['report']['businessRows'],
                          message='解析完成，请在窗口中人工复核并确认导出。',
                          groups=len(self.groups), mediaSummary=value['report']['mediaSummary'],
                          mediaSummaryText=value['report']['mediaSummaryText'])
            self.open_request = None
        if self.check_report and self.check_chat:
            self.root.after(50, self.verify_browser)
        elif self.check_report:
            self.finish_check({'ok': True, 'reviewRequired': True})
        else:
            self.status.set(self.ui_text('解析完成。可批量复核所选群或复核当前群；确认后再“一键导出所选群”。'))
            if self.active_chat == chat:
                self.result_text.set(self.ui_text('待复核群 ID：') + chat + ' · 源记录 ' + str(value['report']['exportedRows']) + ' 条')
            self.root.after(1, self.parse_next)

    def review_targets(self):
        return [group['id'] for group in self.groups if self.parse_states.get(group['id']) == '待复核' and
                (not self.selected_chats or group['id'] in self.selected_chats)]

    def next_review(self):
        if not self.can_browse():
            return
        pending = self.review_targets()
        if not pending:
            return
        chat = pending[(pending.index(self.active_chat) + 1) % len(pending)] if self.active_chat in pending else pending[0]
        self.focus_chat(chat)

    def focus_chat(self, chat):
        if not self.tree.exists(chat):
            self.search.set('')
        if not self.tree.exists(chat):
            self.review_filter.set('所选未复核' if chat in self.selected_chats and self.review_label(chat) == '未复核' else
                                   self.ui_text('所选群聊') if chat in self.selected_chats else self.ui_text('全部群聊'))
            self.render_groups()
        self.tree.selection_set(chat)
        self.tree.see(chat)
        self.show_chat(chat)

    def read_page(self, offset=None):
        if not self.active_chat or self.parsed_chat != self.active_chat or not self.can_browse():
            return
        chat, kind = self.active_chat, FILTERS[self.filter_kind.get()]
        size, query = int(self.page_size.get()), self.message_search.get()
        self.read_view(chat, lambda view: view.page(offset, kind, page_size=size, query=query), self.preview_ready)

    def choose_date(self, variable, label):
        if not self.busy and not self.batch_running:
            return DatePicker(self.root, variable, '选择' + label + '日期（北京时间）')

    def clear_dates(self):
        self.start_date.set('')
        self.end_date.set('')
        self.status.set(self.ui_text('已清空日期。点击“应用日期”后重新解析当前群全部本地时间范围。'))

    def apply_dates(self):
        if self.active_chat:
            self.start_parse(self.active_chat)
        else:
            self.status.set(self.ui_text('请先搜索并选中目标群；日期选择后用于该群阅读和三格式导出。'))

    def choose_kind(self, label):
        if self.active_chat and self.parsed_chat == self.active_chat and self.can_browse():
            self.filter_kind.set(label)
            self.read_page()

    def open_selected(self, image=False):
        if not self.can_browse():
            return
        selection = self.browser.tree.selection()
        row = self.browser.rows.get(selection[0], {}) if selection else {}
        asset = row.get('asset', {})
        if asset.get('status') != 'ready' and row.get('record'):
            pending = list(reversed(row['record']['items']))
            while pending:
                child = pending.pop()
                candidate = child.get('asset', {})
                if candidate.get('status') == 'ready' and (candidate.get('mime', '').startswith('image/') if image else child['kind'] == 'file'):
                    asset = candidate
                    break
                pending.extend(reversed(child.get('record', {}).get('items', [])))
        if asset.get('status') == 'ready':
            if image and asset.get('mime', '').startswith('image/'):
                self.browser.image_window(asset)
            elif not image:
                self.browser.open_attachment(asset)

    def preview_ready(self, value):
        self.browser.load(value)
        self.browser.set_gallery(self.session.views[self.active_chat])
        self.page_offset, self.page_total = value['offset'], value['total']
        first = self.page_offset + 1 if self.page_total else 0
        self.page_text.set(f'{first}–{self.page_offset + len(value["messages"])} / {self.page_total} 条')
        self.status.set(f'业务阅读 {value["report"]["businessRows"]} 条（源记录 {value["report"]["exportedRows"]} 条）。'
                        + value['report']['mediaSummaryText'])
        search = value['report'].get('localMediaSearch', {})
        if search.get('status') == 'checked':
            self.result_text.set(f'本地补齐检查：{search["filesScanned"]} 个文件，新增补齐 {search["recoveredReferences"]} 项。'
                                 + f'已关联引用来源 {value["report"].get("quoteSourceStatuses", {}).get("linked_local_source", 0)} 条。'
                                 + ('扫描范围未全部完成，详见核查清单。' if search.get('limitReached') or search.get('unreadableFiles') or search.get('oversizeFiles') else '可筛选“待补齐图片”查看原因；重新检查时点击“更新本地记录”。'))
        self.check_data['previewRows'] = len(value['messages'])
        if self.check_report and not self.open_request and self.auto_parse_result is None:
            if self.check_chat:
                self.root.after(50, self.verify_browser)
            else:
                self.finish_check({'ok': True})

    def show_image_source(self, entry):
        if not self.can_browse() or not self.active_chat:
            return
        def show(value):
            self.filter_kind.set('全部')
            self.preview_ready(value)
            self.browser.reveal(entry)
        self.read_view(self.active_chat, lambda view: view.page_for_message(entry['rootMessageId']), show)

    def open_image_page(self):
        if self.session.last_export:
            target = self.session.last_export / 'images.html'
            if target.is_file():
                try:
                    os.startfile(str(target))
                except OSError:
                    messagebox.showerror('无法打开图片总览', '请从导出目录手动打开 images.html。', parent=self.root)

    def verify_browser(self):
        """Exercise real Tk image/record widgets; replace only the OS open boundary."""
        view = self.session.views[self.active_chat]
        results = {'imagesShown': 0, 'nestedImagesShown': 0, 'recordNodesExpanded': 0,
                   'documentOpenCallbacks': 0, 'imageWindowsCreated': 0, 'errors': []}
        launched = []
        original_opener = self.browser.opener
        self.browser.opener = launched.append
        try:
            for kind in ('image', 'file', 'record', 'emoji'):
                offset = 0
                while True:
                    page = view.page(offset, kind)
                    self.browser.load(page)
                    for item, row in list(self.browser.rows.items()):
                        if row.get('record'):
                            self.browser.tree.item(item, open=True)
                            if len(self.browser.tree.get_children(item)) != len(row['record']['items']):
                                raise ValueError('转发树条目数量不一致。')
                            results['recordNodesExpanded'] += 1
                        asset = row.get('asset', {})
                        if asset.get('status') != 'ready':
                            continue
                        if asset.get('mime', '').startswith('image/'):
                            self.browser.tree.selection_set(item)
                            self.browser.selected()
                            if not self.browser.photos or not all(photo.width() > 0 and photo.height() > 0 for photo in self.browser.photos):
                                raise ValueError('图片未创建可显示的原生位图。')
                            results['imagesShown'] += 1
                            if '.' in row.get('part', ''):
                                results['nestedImagesShown'] += 1
                                window = self.browser.image_window(asset)
                                if window is None:
                                    raise ValueError('嵌套图片放大窗口未创建。')
                                window.update_idletasks()
                                window.destroy()
                                results['imageWindowsCreated'] += 1
                        elif row['kind'] == 'file':
                            from offline_view import OPEN_EXTENSIONS
                            if Path(asset.get('path', '')).suffix.lower() not in OPEN_EXTENSIONS:
                                results['unsupportedOpenFormats'] = results.get('unsupportedOpenFormats', 0) + 1
                                continue
                            if self.browser.open_attachment(asset) is None:
                                raise ValueError('附件未交给打开回调。')
                    offset += page['pageSize']
                    if offset >= page['total']:
                        break
            results['documentOpenCallbacks'] = len(launched)
            results['linkedQuoteSourcesShown'] = 0
            for offset in range(0, view.page(0, 'quote')['total'], 100):
                self.browser.load(view.page(offset, 'quote'))
                for item in self.browser.tree.get_children():
                    row = self.browser.rows[item]
                    if row.get('quote', {}).get('resolutionStatus') == 'linked_local_source':
                        self.browser.tree.item(item, open=True)
                        self.browser.tree.selection_set(item)
                        self.browser.selected()
                        if not self.browser.tree.get_children(item):
                            raise ValueError('引用来源未生成原生查看条目。')
                        results['linkedQuoteSourcesShown'] += 1
            results['missingMediaMessages'] = view.page(0, 'missing_media')['total']
            results['missingImageMessages'] = view.page(0, 'missing_images')['total']
            results['businessRows'] = view.page(0, 'business')['total']
            self.browser.set_gallery(view)
            self.browser.tabs.select(self.browser.gallery)
            self.root.update_idletasks()
            results['galleryImageReferences'] = len(view.image_index)
            results['galleryCounts'] = dict(view.image_counts)
            results['galleryPicturesShown'] = 0
            results['gallerySourceLinksChecked'] = 0
            for offset in range(0, len(view.image_index), 24):
                self.browser.gallery.load(offset)
                page = self.browser.gallery.page
                if self.browser.gallery.card_count != len(page['images']):
                    raise ValueError('图片总览卡片数量不一致。')
                ready = sum(entry['asset'].get('status') == 'ready' for entry in page['images'])
                if len(self.browser.gallery.photos) != ready:
                    raise ValueError('图片总览中有可用图片未显示。')
                results['galleryPicturesShown'] += ready
                for entry in page['images']:
                    self.browser.load(view.page_for_message(entry['rootMessageId']))
                    self.browser.reveal(entry)
                    chosen = self.browser.tree.selection()
                    if not chosen:
                        raise ValueError('图片未能定位原始消息。')
                    results['gallerySourceLinksChecked'] += 1
            self.check_data['nativeReading'] = results
        except Exception as error:
            results['errors'].append(str(error))
            self.finish_check({'error': str(error), 'nativeReading': results})
            return
        finally:
            self.browser.opener = original_opener
        if self.auto_parse_result is not None:
            report = self.auto_parse_result['report']
            self.finish_check({'ok': True, 'reviewRequired': True, 'exportedRows': report['exportedRows'],
                               'output': self.auto_parse_result['view']['viewRoot']})
        else:
            self.finish_check({'ok': True, 'reviewRequired': True})

    def range_matches(self):
        result = self.parsed_results.get(self.active_chat)
        if not result:
            return True
        summary = result['review']
        return ((self.start_date.get().strip() or None, self.end_date.get().strip() or None, self.copy_media.get()) ==
                (summary['startDate'], summary['endDate'], summary['copyMediaRequested']))

    def review_current(self):
        if not self.active_chat or self.busy or self.batch_running or not self.session.pending_review:
            return
        summary = self.session.pending_review['summary']
        chat = self.active_chat
        if summary['chat']['id'] != chat:
            self.status.set(self.ui_text('复核群与当前群不一致，请重新选择对应群。'))
            return
        if ((self.start_date.get().strip() or None, self.end_date.get().strip() or None, self.copy_media.get()) !=
                (summary['startDate'], summary['endDate'], summary['copyMediaRequested'])):
            self.status.set('日期或媒体选项已变化，请重新解析并复核。')
            return
        self.show_review_dialog([chat])

    def review_selected(self):
        if self.busy or self.read_busy or self.batch_running or self.closing:
            return
        selected = [group['id'] for group in self.groups if group['id'] in self.selected_chats]
        if not selected or any(chat not in self.parsed_results or self.session.review_status(chat) == '未解析' for chat in selected):
            self.status.set(self.ui_text('请先勾选群聊并完成解析，再批量复核。'))
            return
        if self.active_chat in selected and not self.range_matches():
            self.status.set(self.ui_text('当前群日期或媒体选项已变化，请重新解析并复核。'))
            return
        targets = [chat for chat in selected if self.session.review_status(chat) == '待复核']
        if not targets:
            self.status.set(self.ui_text('所选群已全部复核，原确认记录已保留，可一键导出。'))
            return
        self.show_review_dialog(targets, bulk=True, skipped=len(selected) - len(targets))

    def show_review_dialog(self, chats, *, bulk=False, skipped=0):
        from offline_review import REVIEW_CHECKS
        parsed = [self.parsed_results[chat] for chat in chats]
        chat = chats[0]
        review_ids = {item['chat']['id']: item['review']['reviewId'] for item in parsed}
        dialog = tk.Toplevel(self.root)
        dialog.title(f"批量人工复核 · {len(chats)}{self.ui_text(' 群')}" if bulk else self.ui_text('人工复核 · 保存确认后可一键导出所选群'))
        dialog.geometry('740x660')
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.configure(background=CANVAS)
        dialog.bind('<Escape>', lambda _event: dialog.destroy())
        shell = tk.Frame(dialog, background=CANVAS)
        shell.pack(fill='both', expand=True, padx=16, pady=16)
        panel, box = card(shell)
        panel.pack(fill='both', expand=True)
        ttk.Label(box, text=f"一次确认下列 {len(chats)}{self.ui_text(' 个群的复核结果')}" if bulk else self.ui_text('请核对本次账号、群 ID 与解析结果'),
                  font=DISPLAY, style='Title.TLabel').pack(anchor='w', padx=20, pady=(18, 0))
        ttk.Label(box, text=(f"已复核或已导出 {skipped}{self.ui_text(' 群保留原记录；本次确认不导出。')}" if bulk else
                  self.ui_text('本次仅保存当前群复核结果；全部所选群复核后，点击主窗口“一键导出所选群”。')),
                  style='Sub.TLabel').pack(anchor='w', padx=20, pady=(4, 0))
        review_controls = ttk.Frame(box)
        review_controls.pack(side='bottom', fill='x', padx=20, pady=(10, 18))
        report_box = ttk.Frame(box)
        report_box.pack(fill='both', expand=True, padx=20, pady=12)
        report_scroll = ttk.Scrollbar(report_box, orient='vertical')
        report_scroll.pack(side='right', fill='y')
        text = tk.Text(report_box, height=11, wrap='word', relief='flat', borderwidth=0, padx=14, pady=10,
                       yscrollcommand=report_scroll.set, background=SURFACE, foreground=TEXT, font=BODY,
                       highlightthickness=1, highlightbackground=BORDER, highlightcolor=BORDER_STRONG,
                       spacing1=1, spacing3=2)
        text.pack(side='left', fill='both', expand=True)
        report_scroll.configure(command=text.yview)
        if bulk:
            text.insert('end', f"本次复核 {len(chats)}{self.ui_text(' 群，共 ')}{sum((item['review']['exportedRows'] for item in parsed))}{self.ui_text(' 条源记录。\n下列每群的范围、解析状态和缺口均属于本次确认；共同备注将保存到每个群。\n\n')}")
        for index, item in enumerate(parsed, 1):
            summary, report = item['review'], item['report']
            if bulk:
                text.insert('end', f'—— {index}/{len(chats)} · {summary["chat"]["name"]} ——\n')
            text.insert('end', f"客户端：{summary['client']}\n账号目录：{summary['accountDirectory']}\n本人 ID：{summary['selfId'] or '未知'}{self.ui_text('\n群名称：')}{summary['chat']['name']}{self.ui_text('\n群 ID：')}{summary['chat']['id']}\n副本时间：{summary['snapshotTime']}\n日期：{summary['startDate'] or '不限'} 至 {summary['endDate'] or '不限'}（北京时间）\n源记录：{summary['exportedRows']} 条\n"
                    + report['mediaSummaryText'] + '\n解析状态：' + json.dumps(report['parseStatuses'], ensure_ascii=False)
                    + '\n解码状态：' + json.dumps(report['decodeStatuses'], ensure_ascii=False)
                    + ('\n' + report['attachmentTextSummaryText'] if report.get('attachmentTextSummaryText') else '')
                    + ('\n内容补齐项（按引用）：' + str(report['contentQuality']['counts'].get('occurrences', 0))
                       + '；导出完成不等于原件与正文完整取得。' if report.get('contentQuality') else '')
                    + '\n成员资料：仅为本地已保存资料，不保证当前全量成员。\n'
                    + '\n'.join(report['warnings'] + report['limitations']) + '\n\n')
        text.configure(state='disabled')
        approval = {} if bulk else self.session.pending_review.get('approval', {})
        checks = {key: tk.BooleanVar(value=approval.get('checks', {}).get(key, False)) for key in REVIEW_CHECKS}
        check_actions = ttk.Frame(review_controls)
        check_actions.pack(fill='x', pady=(0, 3))
        check_count = tk.StringVar()
        def set_checks(value):
            for variable in checks.values():
                variable.set(value)
        check_all = ttk.Button(check_actions, text='全选复核项', command=lambda: set_checks(True))
        check_all.pack(side='left')
        clear_checks = ttk.Button(check_actions, text='清空勾选', command=lambda: set_checks(False))
        clear_checks.pack(side='left', padx=6)
        ttk.Label(check_actions, textvariable=check_count, style='Sub.TLabel').pack(side='right')
        for key, label in REVIEW_CHECKS.items():
            if bulk:
                label = label.replace(self.ui_text('当前账号、群名称与群 ID'), self.ui_text('本次所有群的账号、群名称与群 ID'))
            ttk.Checkbutton(review_controls, text=self.ui_text(label), variable=checks[key]).pack(anchor='w', pady=3)
        ttk.Label(review_controls, text=f"共同备注（保存到本次 {len(chats)}{self.ui_text(' 个群，可记录接受的缺口）')}" if bulk else
                  '复核备注（可记录已接受的缺口）').pack(anchor='w', pady=(8, 0))
        note = tk.Text(review_controls, height=3, wrap='word', relief='flat', borderwidth=0, padx=10, pady=8,
                       background=SURFACE, foreground=TEXT, font=BODY, highlightthickness=1,
                       highlightbackground=BORDER, highlightcolor=BORDER_STRONG)
        note.pack(fill='x', pady=5)
        note.insert('end', approval.get('note', ''))
        buttons = ttk.Frame(review_controls)
        buttons.pack(fill='x', pady=6)

        def confirm():
            values = {key: variable.get() for key, variable in checks.items()}
            if not all(values.values()):
                messagebox.showinfo('尚未完成复核', '请人工检查并勾选全部复核项。', parent=dialog)
                return
            comment = note.get('1.0', 'end-1c')
            dialog.destroy()
            if bulk:
                self.work(f"保存 {len(chats)}{self.ui_text(' 群人工复核')}",
                          lambda: self.session.approve_reviews(review_ids, values, comment), self.review_ready,
                          on_error=self.batch_review_failed)
            else:
                self.work(self.ui_text('保存当前群人工复核'),
                          lambda: self.session.approve_review(review_ids[chat], values, comment, chat=chat), self.review_ready)

        ttk.Button(buttons, text='返回检查', command=dialog.destroy).pack(side='left')
        confirm_button = ttk.Button(buttons, text=f"确认本次 {len(chats)}{self.ui_text(' 群复核')}" if bulk else '确认复核完成', style='Accent.TButton', command=confirm)
        confirm_button.pack(side='right')
        def update_checks(*_):
            count = sum(variable.get() for variable in checks.values())
            check_count.set(f'已勾选 {count}/{len(checks)} · ' + (f"应用本次 {len(chats)}{self.ui_text(' 群')}" if bulk else self.ui_text('仅当前群')))
            check_all.configure(state='disabled' if count == len(checks) else 'normal')
            clear_checks.configure(state='normal' if count else 'disabled')
            confirm_button.configure(state='normal' if count == len(checks) else 'disabled')
        for variable in checks.values():
            variable.trace_add('write', update_checks)
        update_checks()

    def review_ready(self, value):
        results = value.get('reviews', [value])
        for item in results:
            self.parse_states[item['chat']['id']] = '已复核'
        self.render_groups()
        if 'reviews' in value:
            self.status.set(f"已保存 {len(results)}{self.ui_text(' 群的人工复核；尚未导出。可点击“一键导出所选群”。')}")
            self.result_text.set(f"批量复核 {len(results)}{self.ui_text(' 群 · 原有复核记录保留 · 尚未导出')}")
        else:
            chat = value['chat']['id']
            self.status.set('已保存“' + value['chat']['name'] + self.ui_text('”的人工复核；尚未导出。完成所选群复核后点击“一键导出所选群”。'))
            self.result_text.set(self.ui_text('已复核群 ID：') + chat + ' · 确认时间：' + value['review']['confirmedAt'])
        for item in results:
            if item['chat']['id'] == self.receipt_chat:
                self.publish_review_receipt(item)

    def batch_review_failed(self, issue):
        self.sync_review_states()
        self.status.set('批量复核未完成：' + issue)

    def publish_review_receipt(self, value):
        chat = value['chat']['id']
        if chat == self.receipt_chat:
            parsed = self.parsed_results[chat]
            report = parsed['report']
            self.publish_open('awaiting_review', ok=False, chatId=chat, chatName=value['chat']['name'],
                              reviewId=value['review']['reviewId'], reviewConfirmed=True,
                              viewRoot=parsed['view']['viewRoot'], exportedRows=report['exportedRows'],
                              businessRows=report['businessRows'], mediaSummary=report['mediaSummary'],
                              mediaSummaryText=report['mediaSummaryText'],
                              message=self.ui_text('人工复核已保存，等待用户点击“一键导出所选群”。'))

    def choose_export_directory(self):
        if self.busy or self.read_busy or self.batch_running or self.closing:
            return
        current = self.session.export_directory
        folder = filedialog.askdirectory(parent=self.root, title='选择群聊导出位置（自动新建批次目录）',
                                         initialdir=str(current if current.is_dir() else current.parent), mustexist=True)
        if not folder:
            return
        def saved(path):
            self.export_directory_text.set(str(path))
            self.status.set('已记住导出位置；后续批次保存到此目录，已有文件保留原位置。')
        self.work('正在保存导出位置', lambda: self.session.set_export_directory(folder), saved)

    def save_current_export_copy(self):
        if self.busy or self.read_busy or self.batch_running or self.closing:
            return
        chat = self.active_chat
        if not chat or self.session.review_status(chat) != '已导出':
            messagebox.showinfo('先导出当前群', '先完成人工复核并导出当前群，再另存副本。', parent=self.root)
            return
        current = self.session.export_directory
        folder = filedialog.askdirectory(parent=self.root, title='另存当前群记录、媒体和附件',
                                         initialdir=str(current if current.is_dir() else current.parent), mustexist=True)
        if not folder:
            return
        def save():
            self.session.set_export_directory(folder)
            return self.session.save_export_copy(chat)
        def saved(value):
            self.export_directory_text.set(str(self.session.export_directory))
            self.status.set('当前群副本已保存，原导出及复核记录保留。')
            self.result_text.set('副本保存位置：' + value['output'])
        self.work('正在另存当前群记录与附件', save, saved)

    def export_selected(self):
        if self.busy or self.batch_running or self.closing:
            return
        chats = [group['id'] for group in self.groups if group['id'] in self.selected_chats]
        if not chats or any(self.parse_states.get(chat) not in ('已复核', '已导出') for chat in chats):
            self.status.set(self.ui_text('请先勾选所需群，并完成所有所选群的人工复核。'))
            return
        if self.active_chat in chats and not self.range_matches():
            self.status.set(self.ui_text('当前群日期或媒体选项已变化，请重新解析并复核。'))
            return
        export = (lambda: self.session.export_reviewed(chats, progress=self.report_operation_progress)) if (
            self.session.conversation_mode == 'group') else lambda: self.session.export_reviewed(chats)
        self.work(f"一键导出所选 {len(chats)}{self.ui_text(' 个群')}",
                  export,
                  self.batch_export_ready, on_error=self.batch_export_failed)

    def sync_review_states(self):
        for chat in self.parsed_results:
            self.parse_states[chat] = self.session.review_status(chat)
        self.session.activate_review(self.active_chat)
        self.render_groups()

    def batch_export_failed(self, issue):
        self.sync_review_states()
        self.batch_output = self.session.last_batch_root or self.batch_output
        completed = sum(self.session.review_status(chat) == '已导出' for chat in self.selected_chats)
        self.status.set(f'导出未全部完成；已保存 {completed} 个会话，复核缓存保留。' + issue)
        if self.batch_output:
            self.result_text.set('已保存文件所在批次：' + str(self.batch_output))
        self.update_buttons()

    def batch_export_ready(self, value):
        metadata_errors = value.get('metadataErrors', [])
        source_issue = next((item['error'] for item in metadata_errors if item['file'] == 'source_snapshot'), '')
        for exported in value['exports']:
            self.export_ready(exported, complete=not source_issue)
        self.batch_output = Path(value['output']) if value['output'] else self.batch_output
        self.sync_review_states()
        if self.active_chat in self.parsed_results and not source_issue:
            self.show_chat(self.active_chat)
        detail = (('已保存文件保留，但源副本最终校验未通过；请重新加载数据后继续。' + source_issue) if source_issue else
                  ('正文已保存，导出清单或跨群分析材料未完整生成；可点击“修复导出清单”。'
                   if self.session.conversation_mode == 'group' else
                   '正文已保存，导出清单未完整生成；可点击“修复导出清单”。') if metadata_errors else
                  self.ui_text('失败群保留复核结果，可重试；已完成群不会重复导出。') if value['failed'] else
                  'JSON / JSONL / Markdown、复核记录与缺口说明已保存。')
        self.status.set(f"本次导出 {len(value['exports'])}{self.ui_text(' 群，失败 ')}{len(value['failed'])}{self.ui_text(' 群，已导出跳过 ')}{len(value['alreadyExported'])}{self.ui_text(' 群。')}" + detail)
        if not self.session.last_export:
            self.result_text.set('本次批次目录：' + str(self.batch_output) if self.batch_output else self.ui_text('所选群已导出，未重复生成文件。'))
        self.update_buttons()
        if source_issue:
            if self.receipt_chat:
                self.publish_open('needs_source', ok=False, chatId=self.receipt_chat, message=detail,
                                  output=str(self.batch_output) if self.batch_output else '')
            if self.check_report:
                self.finish_check({'ok': False, 'error': detail,
                                   'output': str(self.batch_output) if self.batch_output else ''})

    def repair_export_metadata(self):
        if self.busy or self.read_busy or self.batch_running or self.closing:
            return
        target = self.batch_output or self.session.last_batch_root
        if not target:
            messagebox.showinfo('暂无导出批次', '先完成已复核会话的导出，再修复对应批次的清单。', parent=self.root)
            return
        def completed(value):
            self.batch_output = Path(value['output'])
            self.result_text.set('导出批次：' + str(self.batch_output))
            scope = '导出清单与跨群分析入口' if self.session.conversation_mode == 'group' else '导出清单'
            source_issue = next((item['error'] for item in value.get('metadataErrors', [])
                                 if item['file'] == 'source_snapshot'), '')
            self.status.set(('已保存文件保留，但源副本最终校验未通过；请重新加载数据后继续。' + source_issue) if source_issue else
                            scope + '已修复，已保存的会话正文保持原样。' if value.get('metadataComplete') else
                            '会话正文仍保留，' + scope + '修复未完成；请检查磁盘空间或文件权限后重试。')
            if value.get('failed') and not source_issue:
                self.status.set(self.status.get() + f'仍有 {len(value["failed"])} 个会话未导出，请重试。')
            self.update_buttons()
        self.work('根据已验证的导出重建清单', lambda: self.session.repair_export_metadata(target), completed)

    def open_batch(self):
        if self.batch_output and not self.busy:
            try:
                os.startfile(str(self.batch_output))
            except OSError:
                messagebox.showinfo('批次目录', str(self.batch_output), parent=self.root)

    def open_batch_analysis(self):
        if self.busy or not self.batch_output:
            return
        target = self.batch_output / 'batch-analysis' / 'README.md'
        try:
            os.startfile(str(target))
        except OSError:
            self.status.set('无法打开跨群分析材料，可尝试修复导出清单，或在批次目录中查看。')

    def open_saved_location(self):
        if self.session.last_export:
            self.open_result(True)
        else:
            self.open_batch()

    def export_ready(self, value, *, complete=True):
        report = value['report']
        chat = report['chat']['id']
        self.parse_states[chat] = '已导出'
        self.render_groups()
        self.status.set(('导出完成：' if complete else '正文已保存：') +
                        f'业务阅读 {report["businessRows"]} 条，原始记录 {report["exportedRows"]} 条。'
                        + report['mediaSummaryText'])
        self.result_text.set('已生成 JSONL / JSON / MD。保存位置：' + value['output'])
        if complete and chat == self.receipt_chat:
            self.publish_open('completed', ok=True, chatName=report['chat']['name'], chatId=report['chat']['id'],
                          output=value['output'], exportFiles=value['exportFiles'], reporting=value['reporting'],
                          humanReview=value['humanReview'], exportedRows=report['exportedRows'],
                          businessRows=report['businessRows'], mediaSummary=report['mediaSummary'], mediaSummaryText=report['mediaSummaryText'])
        if complete and self.check_report:
            self.finish_check({'ok': True, 'exportedRows': report['exportedRows'],
                               'mediaStatuses': report['mediaStatuses'], 'decodeStatuses': report['decodeStatuses'],
                               'mediaSummary': report['mediaSummary'], 'mediaSummaryText': report['mediaSummaryText'],
                               'reporting': report['reporting'],
                               'localMediaSearch': report.get('localMediaSearch'),
                               'mediaRecoveryFile': report.get('mediaRecoveryFile'),
                               'businessReadingFile': report.get('businessReadingFile'),
                               'businessRows': report.get('businessRows'),
                               'businessMediaStatuses': report.get('businessMediaStatuses'),
                               'quoteSourceStatuses': report.get('quoteSourceStatuses'), 'output': value['output']})

    def refresh(self):
        config = self.session.archive.media_config if self.session.archive else self.session.settings.get('source')
        if config:
            target = self.active_chat
            def refreshed(value):
                self.ready(value)
                if target and any(group['id'] == target for group in self.groups):
                    self.start_parse(target)
            self.work(self.ui_text('更新副本和目标群缓存'), lambda: self.session.open_source(config), refreshed)
        else:
            self.source_settings()

    def scan(self):
        self.work('识别当前客户端占用与本地账号昵称', self.session.scan_accounts, self.scan_ready)

    def scan_live_names(self):
        purpose = '昵称与企业名称' if self.session.client == 'wecom' else '昵称'
        self.work(f'只读临时取得运行账号资料密钥并识别{purpose}（不保存密钥）',
                  lambda: self.session.scan_accounts(live_names=True), self.scan_ready)

    def scan_ready(self, value):
        self.account_scan = value
        from offline_accounts import account_scan_hint
        self.status.set(account_scan_hint(value))
        self.source_settings(value['accounts'])

    def source_settings(self, accounts=None, *, manual=False):
        if self.busy or self.read_busy:
            return
        if self.source_dialog is not None:
            if accounts is not None and self.account_choices_update is not None:
                self.account_choices_update(accounts)
            if manual:
                self.source_tabs.select(self.source_manual_tab)
            self.source_dialog.lift()
            self.source_dialog.focus_set()
            return
        dialog = tk.Toplevel(self.root)
        self.source_dialog = dialog
        startup.record('configuration_dialog_opened', automaticClose=False)
        dialog.title('选择本地数据')
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        dialog.protocol('WM_DELETE_WINDOW', self.close_source_settings)
        dialog.configure(background=CANVAS)
        head = tk.Frame(dialog, background=CANVAS)
        head.pack(fill='x', padx=20, pady=(16, 0))
        tk.Label(head, text='选择本地数据', font=DISPLAY, background=CANVAS,
                 foreground=TEXT).pack(anchor='w')
        ttk.Label(head, text='此窗口不会计时关闭；完成配置或主动关闭前会保持打开。',
                  style='Sub.TLabel').pack(anchor='w', pady=(3, 0))
        ttk.Label(head, textvariable=self.status, wraplength=740).pack(anchor='w', pady=(6, 0))
        self.source_progress = ttk.Progressbar(head, mode='indeterminate')
        self.source_progress.pack(fill='x', pady=(4, 0))
        setup_tabs = ttk.Notebook(head)
        self.source_tabs = setup_tabs
        setup_tabs.pack(fill='both', expand=True, pady=(12, 0))
        automatic_tab, manual_tab = ttk.Frame(setup_tabs), ttk.Frame(setup_tabs)
        self.source_manual_tab = manual_tab
        setup_tabs.add(automatic_tab, text=' 自动配置（推荐） ')
        setup_tabs.add(manual_tab, text=' 手动选择 / 离线副本 ')
        if manual:
            setup_tabs.select(manual_tab)
        automatic = ttk.Frame(automatic_tab, padding=20)
        automatic.pack(fill='x')
        client_label = '企微' if self.session.client == 'wecom' else '个微'
        ttk.Label(automatic, text=f'首次使用：登录本人{client_label}后，一键自动配置', font=TITLE,
                  style='Title.TLabel').pack(anchor='w')
        setup_hint = ('只读所选企微账号，尝试恢复并校验数据库密钥；图片附件通过缓存摘要匹配。配置与个微分开保存。'
                      if self.session.client == 'wecom' else '先复用并校验本机已有密钥；没有时，只读尝试从所选账号已登录微信恢复数据库和图片密钥。')
        ttk.Label(automatic, text=setup_hint,
                  wraplength=720, style='Sub.TLabel').pack(anchor='w', pady=(5, 8))
        if self.source_issue:
            ttk.Label(automatic, text=self.source_issue, foreground=WARNING, wraplength=720).pack(anchor='w', pady=(0, 8))
        frame = ttk.Frame(manual_tab, padding=20)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='手动配置（已有密钥或离线副本）：也可在下方选择文件。原始数据只读。').grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 15))
        config = dict(self.pending_config or (self.session.archive.media_config if self.session.archive else self.session.settings.get('source', {})))
        accounts = accounts if accounts is not None else self.account_scan.get('accounts', [])
        if not config.get('sourceRoot'):
            preferred = self.account_scan.get('recommendedSourceRoot')
            if preferred and any(item['sourceRoot'] == preferred for item in accounts):
                config['sourceRoot'] = preferred
            elif len(accounts) == 1:
                config['sourceRoot'] = accounts[0]['sourceRoot']
        entries = {}
        fields = [('sourceRoot', '账号数据或明文副本目录', True), ('keyFile', '已有数据库密钥文件', False),
                  ('imageKeyFile', '图片密钥（组合文件可留空）', False), ('selfId', '本人微信 ID（可选，不填保持未知）', None)]
        hints = {'sourceRoot': '选单个账号的 db_storage 或它的上一级账号目录；里面有 message、contact 等库。',
                 'keyFile': '选此前准备的密钥 JSON，不是微信密码，也不是任意 JSON；已解密副本可留空。',
                 'imageKeyFile': '用于解开部分图片缓存；若上面的 JSON 已含图片密钥，此处留空即可。'}
        if self.session.client == 'wecom':
            fields = [('sourceRoot', '企微账号目录 / Data / 明文副本', True), ('keyFile', '已有企微数据库密钥（可留空）', False),
                      ('selfId', '本人企微 ID（可选）', None)]
            hints['sourceRoot'] = r'例如：文档\WXWork\账号目录；Data 内有 message.db、session.db、user.db。'
            hints['keyFile'] = '自动配置成功后无需找文件；离线密文需匹配的企微 wecomKeys JSON，不能使用个微密钥。'
        for index, (key, label, folder) in enumerate(fields):
            row = 1 + index * 2
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', padx=(0, 12), pady=7)
            variable = tk.StringVar(value=config.get(key, ''))
            entries[key] = variable
            ttk.Entry(frame, textvariable=variable, width=52).grid(row=row, column=1, sticky='ew')
            if folder is not None:
                def browse(variable=variable, folder=folder):
                    path = filedialog.askdirectory(parent=dialog) if folder else filedialog.askopenfilename(parent=dialog, filetypes=[('JSON 文件', '*.json'), ('所有文件', '*.*')])
                    if path:
                        variable.set(path)
                ttk.Button(frame, text='选择…', command=browse).grid(row=row, column=2, padx=(8, 0))
            if key in hints:
                ttk.Label(frame, text=hints[key], style='Sub.TLabel', wraplength=650).grid(row=row + 1, column=0, columnspan=3, sticky='w')
        choices = {}
        selected = tk.StringVar()
        picker = ttk.Frame(automatic)
        picker.pack(fill='x', pady=(0, 8))
        ttk.Label(picker, text='选择要解析的账号').pack(anchor='w', pady=(0, 6))
        chooser = ttk.Combobox(picker, textvariable=selected, state='readonly', width=72)
        chooser.pack(fill='x')
        selection_status = tk.StringVar()
        ttk.Label(picker, textvariable=selection_status, wraplength=720, style='Sub.TLabel').pack(anchor='w', pady=(6, 0))
        saved_source = self.session.settings.get('source', {}).get('sourceRoot')
        account_items = []
        company_summary = tk.StringVar()
        if self.session.client == 'wecom':
            ttk.Label(picker, textvariable=company_summary, wraplength=720, style='Sub.TLabel').pack(anchor='w', pady=(3, 0))
        def show_selection(*_):
            source = entries['sourceRoot'].get()
            item = next((item for item in account_items if same_account_source(item['sourceRoot'], source, self.session.client)), {})
            selected.set(next((label for label, path in choices.items() if same_account_source(path, source, self.session.client)), ''))
            saved = same_account_source(source, saved_source, self.session.client)
            prefix = ('保留已保存配置；' if saved else '当前选择尚未保存；') if source else '请选择账号；'
            state = item.get('processState')
            from offline_accounts import account_process_hint, account_scan_hint
            detail = account_process_hint(item, self.account_scan, self.session.client)
            detail = account_scan_hint(self.account_scan) + detail
            if 'messageDbBytes' in item:
                detail += f'消息库 {item["messageDbBytes"] / 1048576:.1f} MiB，最后写入 {item["messageDbModifiedAt"][:10]}。'
            if item.get('stale'):
                detail += '较旧的历史候选，最后写入 ' + item.get('messageDbModifiedAt', '')[:10] + '；此标签仅供选择参考。'
            preferred = self.account_scan.get('recommendedSourceRoot')
            can_choose = bool(preferred and not same_account_source(source, preferred, self.session.client))
            if can_choose:
                detail += '已确认另一个运行目录，可点击“选择已确认的运行账号”。'
            elif self.account_scan.get('activeAccountCount', 0) > 1:
                detail += '多个账号同时在用，请按账号目录选择。'
            if self.account_scan.get('scanIncomplete'):
                detail += '部分目录归属未确认，暂不推荐唯一账号。'
            nickname = item.get('accountNickname')
            if self.session.client == 'wecom':
                company = item.get('companyName') or item.get('companyShortName')
                detail += f'企业：{company}。' if company else '企业名称未获取；可点击“识别运行账号昵称与企业”补充。'
                if company and item.get('companyBasis', '').startswith('self_corp_list_table:'):
                    detail += '企业来源：本地本人企业列表，账号ID精确匹配；企业信息不代表当前进程在用。'
            if nickname:
                basis = ('当前持有进程及资料库校验' if item.get('profileBasis', '').startswith('live_owner:') else
                         '已配置本人ID' if item.get('profileBasis', '').startswith('configured_self_id') else '账号目录ID精确匹配')
                detail += f'本地昵称/名称：{nickname}；来源：{basis}。'
            else:
                if state == 'active':
                    detail += ('可点击“识别运行账号昵称与企业”；' if self.session.client == 'wecom' else
                               '可点击“临时取资料密钥并识别运行账号昵称”；') + '无需切换或保存账号配置。'
                elif item.get('profileStatus') == 'needs_key':
                    detail += '昵称未取得：现有密钥不适用于该资料库；请先完成此账号配置，再重新识别。'
                else:
                    detail += '昵称未取得；请核对本人ID、资料访问权限或先完成该账号配置，再重新识别。'
            desired = 'normal' if can_choose else 'disabled'
            choose_running.configure(state=desired)
            self.source_controls[:] = [(widget, desired if widget is choose_running else original)
                                      for widget, original in self.source_controls]
            message = prefix + detail
            selection_status.set(message)
            self.status.set(f'发现 {len(account_items)} 个账号目录，确认 {self.account_scan.get("activeAccountCount", 0)} 个在用。' + message)
        def select_account(_event=None):
            path = choices.get(selected.get())
            if path and not same_account_source(entries['sourceRoot'].get(), path, self.session.client):
                for key in ('keyFile', 'imageKeyFile', 'selfId'):
                    if key in entries:
                        entries[key].set('')
                entries['sourceRoot'].set(path)
            show_selection()
        chooser.bind('<<ComboboxSelected>>', select_account)
        def select_running():
            preferred = self.account_scan.get('recommendedSourceRoot')
            label = next((label for label, path in choices.items() if same_account_source(path, preferred, self.session.client)), None)
            if label:
                selected.set(label)
                select_account()
        choose_running = ttk.Button(picker, text='选择已确认的运行账号', command=select_running)
        choose_running.pack(anchor='w', pady=(6, 0))
        def update_accounts(items):
            account_items[:] = items
            choices.clear()
            if self.session.client == 'wecom':
                known = sum(bool(item.get('companyName') or item.get('companyShortName')) for item in items)
                pending = sum(item.get('processState') == 'active' and not (item.get('companyName') or item.get('companyShortName')) for item in items)
                company_summary.set(f'企业已识别 {known}/{len(items)}；' +
                                    (f'运行账号还有 {pending} 个未识别，可点击下方“识别运行账号昵称与企业”。' if pending else
                                     '其余账号尚无可读企业资料；可重新识别运行账号，或提供对应资料密钥。' if known < len(items) else '可按企业名称选择对应账号。'))
            states = {'active': '运行中，归属已确认', 'inactive': '所选库未查到占用',
                      'ambiguous': '多个进程占用', 'unavailable': '归属未确认'}
            for index, item in enumerate(items):
                state = states.get(item.get('processState'), '归属未确认')
                pids = ','.join(str(pid) for pid in item.get('processIds', []))
                nickname = item.get('accountNickname') or '昵称未取得'
                if item.get('stale'):
                    nickname += ' · 历史候选'
                if self.session.client == 'wecom':
                    nickname += ' · ' + (item.get('companyShortName') or item.get('companyName') or '企业名称未获取')
                label = f'{index + 1}. {nickname} · {item["directoryLabel"]} · {state}' + (' · PID ' + pids if pids else '')
                if same_account_source(item['sourceRoot'], saved_source, self.session.client):
                    label += ' · 已保存配置'
                label += f' · {len(item.get("databases", []))}个数据库'
                if 'messageDbBytes' in item:
                    label += f' · 消息库 {item["messageDbBytes"] / 1048576:.1f} MiB · {item["messageDbModifiedAt"][:10]}'
                choices[label] = item['sourceRoot']
            chooser.configure(values=list(choices))
            if not entries['sourceRoot'].get():
                preferred = self.account_scan.get('recommendedSourceRoot')
                if preferred and preferred in choices.values():
                    entries['sourceRoot'].set(preferred)
                elif len(choices) == 1:
                    entries['sourceRoot'].set(next(iter(choices.values())))
            show_selection()
        self.account_choices_update = update_accounts
        update_accounts(accounts)
        entries['sourceRoot'].trace_add('write', show_selection)
        ttk.Label(automatic, text='对应数据库目录', style='Sub.TLabel').pack(anchor='w', pady=(3, 0))
        ttk.Label(automatic, textvariable=entries['sourceRoot'], wraplength=720).pack(anchor='w', pady=(3, 8))
        identify_actions = ttk.Frame(automatic)
        identify_actions.pack(fill='x', pady=(0, 6))
        ttk.Button(identify_actions, text='重新识别运行账号', command=self.scan).pack(side='left')
        identify_text = '识别运行账号昵称与企业' if self.session.client == 'wecom' else '临时取资料密钥并识别运行账号昵称'
        ttk.Button(identify_actions, text=identify_text, command=self.scan_live_names).pack(side='left', padx=(8, 0))
        purpose = '本人昵称与企业名称' if self.session.client == 'wecom' else '本人昵称'
        ttk.Label(automatic, text=f'上方识别按钮会临时取资料密钥，只读访问已确认归属的运行进程，仅用于{purpose}，不保存密钥、不读聊天正文。',
                  wraplength=720, style='Sub.TLabel').pack(anchor='w', pady=(0, 8))
        include_media = tk.BooleanVar(value=config.get('includeMedia', True))
        include_voice = tk.BooleanVar(value=config.get('includeVoice', False))
        ttk.Checkbutton(frame, text='包含附件索引（需要对应数据库密钥）', variable=include_media).grid(row=10, column=0, columnspan=3, sticky='w', pady=(12, 5))
        if self.session.client == 'wechat':
            ttk.Checkbutton(frame, text='包含语音原件库（需要额外匹配密钥）', variable=include_voice).grid(row=11, column=0, columnspan=3, sticky='w')
        ttk.Label(frame, text='“匹配”指密钥能通过所选数据库的解密校验，改文件名不会产生密钥。只保存文件路径。', style='Sub.TLabel').grid(row=12, column=0, columnspan=3, sticky='w', pady=(8, 14))
        def form_value():
            value = {key: variable.get().strip() for key, variable in entries.items()}
            if config.get('sourceRoot') and not same_account_source(value['sourceRoot'], config['sourceRoot'], self.session.client):
                for key in ('keyFile', 'imageKeyFile', 'selfId'):
                    if value.get(key) == config.get(key):
                        value[key] = ''
            if config.get('sourceRoot') and same_account_source(value['sourceRoot'], config['sourceRoot'], self.session.client):
                from offline_desktop import CONTENT_FIELDS
                value.update({key: config[key] for key in CONTENT_FIELDS if key in config})
            value.update(includeMedia=include_media.get(), includeVoice=include_voice.get())
            return value
        def load():
            value = form_value()
            self.pending_config = value
            self.work('创建并校验本地副本', lambda: self.session.open_source(value), self.ready)
        ttk.Button(frame, text='保存并加载', style='Accent.TButton', command=load).grid(row=13, column=2, sticky='e')
        def configure_automatically():
            value = form_value()
            if not value['sourceRoot']:
                self.status.set('请先选择一个账号；微信刚登录时可点击“重新识别运行账号”，已有副本可切到手动页。')
                return
            self.pending_config = value
            startup.record('automatic_configuration_requested')
            self.work('自动校验数据、匹配密钥并加载',
                      lambda: self.session.automatic_setup(value, allow_process_read=True), self.ready)
        ttk.Button(automatic, text='一键自动配置', style='Accent.TButton', command=configure_automatically).pack(anchor='w')
        ttk.Separator(automatic, orient='horizontal').pack(fill='x', pady=(12, 0))
        def choose_workbench():
            path = filedialog.askopenfilename(parent=dialog, title='选择已有工作台的 database-config.json', filetypes=[('JSON 文件', '*.json')])
            if path:
                self.work('加载已有工作台配置', lambda: self.session.open_workbench(path), self.ready)
        if self.session.client == 'wechat':
            ttk.Button(frame, text='使用已有工作台配置…', command=choose_workbench).grid(row=13, column=0, columnspan=2, sticky='w')
        def import_legacy():
            path = filedialog.askopenfilename(parent=dialog, title='选择本人旧版的 settings.local.json',
                                             filetypes=[('旧版账号配置', 'settings.local.json')])
            if path:
                self.work('校验并迁移所选旧配置', lambda: self.session.import_settings(path), self.ready)
        ttk.Button(frame, text='导入旧版配置…', command=import_legacy).grid(row=15, column=0, columnspan=3,
                                                                      sticky='w', pady=(8, 0))
        def recover():
            value = form_value()
            if not value['sourceRoot']:
                messagebox.showinfo('先选择数据', '请选择本人在本机已登录微信使用的实际账号数据目录；只有离线副本时请提供已有密钥。', parent=dialog)
                return
            self.pending_config = value
            def run_recovery():
                if self.session.client == 'wecom':
                    return self.session.automatic_setup(value, allow_process_read=True)
                from portable_key_recovery import recover_key_file
                result = recover_key_file(value['sourceRoot'], self.session.directory,
                    allow_process_read=True, include_media=value['includeMedia'],
                    include_voice=value.get('includeVoice', False))
                value['keyFile'] = result['keyFile']
                if result['imageRecovery'] == 'verified_local_samples':
                    value['imageKeyFile'] = ''
                info = self.session.open_source(value)
                info['notice'] = '本机数据库密钥已恢复并通过副本校验。' + (
                    '图片密钥也已通过本地样本验证。' if result['imageRecovery'] == 'verified_local_samples'
                    else '图片密钥未恢复，保留已有图片密钥和可读媒体；其余缺口会明确标注。')
                return info
            self.work('只读访问所选账号的本机微信并校验密钥', run_recovery, self.ready)
        ttk.Button(frame, text=f'没有密钥：从本机已登录{client_label}尝试恢复', command=recover).grid(row=14, column=0, columnspan=3, sticky='w', pady=(14, 0))
        def remember_controls(parent):
            for child in parent.winfo_children():
                if isinstance(child, (ttk.Button, ttk.Entry, ttk.Combobox, ttk.Checkbutton)):
                    self.source_controls.append((child, str(child.cget('state'))))
                remember_controls(child)
        remember_controls(dialog)
        footer = ttk.Frame(dialog, padding=(20, 0, 20, 10))
        footer.pack(fill='x')
        label = '企微' if self.session.client == 'wecom' else '个微'
        clear_button = ttk.Button(footer, text=f'清除{label}账号缓存…', command=self.clear_account_cache)
        clear_button.pack(side='right')
        self.source_controls.append((clear_button, 'normal'))

    def close_source_settings(self, reason='user'):
        if self.busy:
            self.status.set('正在处理本地配置，请等待完成后再关闭；当前窗口会保持打开。')
            return
        dialog = self.source_dialog
        self.source_dialog = None
        self.source_controls = []
        self.source_progress = None
        self.account_choices_update = None
        if dialog is not None:
            startup.record('configuration_dialog_closed', reason=reason)
            dialog.destroy()

    def open_export_file(self, kind):
        if self.busy or self.batch_running or not self.session.last_export:
            return
        names = {'JSONL': 'messages.jsonl', 'JSON': 'messages.json', 'Markdown': 'messages.md',
                 '补齐说明': '补齐清单.md', '补齐表格': '补齐清单.csv'}
        name = names.get(kind)
        if not name:
            return
        target = self.session.last_export / name
        if not target.is_file():
            messagebox.showinfo('此导出未包含该文件', '请使用新版重新解析并完成人工复核，再保存结果。', parent=self.root)
            return
        try:
            os.startfile(str(target))
        except OSError:
            try:
                # A missing file association must not require installing an editor.
                notepad = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/notepad.exe'
                subprocess.Popen([str(notepad), str(target)])
            except OSError:
                messagebox.showerror('无法打开文件', '文件已导出，请在导出目录用文本查看器打开。', parent=self.root)

    def open_result(self, folder):
        if not self.session.last_export:
            return
        target = self.session.last_export if folder else self.session.last_export / '业务阅读.txt'
        try:
            os.startfile(str(target))
        except OSError:
            messagebox.showerror('无法打开文件', '请从导出目录中手动打开结果。', parent=self.root)

    def finish_check(self, value):
        self.check_data.update(value)
        self.check_report.parent.mkdir(parents=True, exist_ok=True)
        self.check_report.write_text(json.dumps(self.check_data, ensure_ascii=False, indent=2), encoding='utf-8')
        self.destroyed = True
        self.root.destroy()

    def close(self):
        startup.record('window_close_requested', busy=self.busy)
        self.save_progress_selection()
        if self.busy or self.read_busy:
            self.closing = True
            self.batch_cancel_requested = True
            self.read_pending = None
            self.read_serial += 1
            self.status.set('正在完成当前本地操作，完成后自动关闭…')
            self.update_buttons()
        else:
            self.closing = self.destroyed = True
            self.root.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workbench', type=Path)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--client', choices=('wechat', 'wecom'))
    parser.add_argument('--conversation-mode', choices=('group', 'direct'))
    parser.add_argument('--capabilities', action='store_true')
    parser.add_argument('--export-diagnostics',type=Path,help='导出最近诊断日志ZIP，不打开窗口或读取聊天数据')
    parser.add_argument('--task-json', type=Path, help='执行本地 JSON 任务，无需打开窗口')
    parser.add_argument('--launch-desktop-json', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--result-json', type=Path, help='任务结果写入不存在的新 JSON 文件')
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--open-chat', help='解析并打开指定会话ID；首次设置后自动继续')
    selection.add_argument('--open-chat-name', help='解析并打开名称唯一的群；首次设置后自动继续')
    parser.add_argument('--open-result-json', type=Path, help='在本机 requests 目录写入解析进度与完成结果')
    parser.add_argument('--check-launch', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--check-export-chat', help=argparse.SUPPRESS)
    parser.add_argument('--diagnose-accounts', type=Path, help='只读检查客户端进程与数据库归属，输出脱敏 JSON')
    parser.add_argument('--scan-root', action='append', help='本次账号发现根目录，可重复指定；不改已保存配置')
    args = parser.parse_args(argv)
    if args.scan_root and (len(args.scan_root) > 16 or not all(Path(root).is_absolute() and '\0' not in root for root in args.scan_root)):
        parser.error('--scan-root 需要至多 16 个绝对目录路径。')
    if args.export_diagnostics and (args.task_json or args.launch_desktop_json or args.result_json or args.open_chat or args.open_chat_name or args.check_launch):
        parser.error('--export-diagnostics 须单独使用，可附带 --client 和 --data-dir。')
    if args.open_result_json and not (args.open_chat or args.open_chat_name):
        parser.error('--open-result-json 需要指定待解析会话。')
    if (args.task_json or args.result_json) and (args.open_chat or args.open_chat_name):
        parser.error('批处理任务与解析查看入口不能同时指定。')
    from offline_profiles import ClientBindingError, default_directory, resolve_client
    try:
        args.client = resolve_client(args.client)
        from offline_direct import resolve_packaged_mode
        args.conversation_mode = resolve_packaged_mode(args.conversation_mode)
    except (ClientBindingError, ArchiveError) as error:
        parser.error(str(error))
    if args.capabilities:
        private = args.conversation_mode == 'direct'
        value = {'schemaVersion': 1, 'privateAnalysis': private, 'conversationMode': args.conversation_mode,
                 'role': 'desktop', 'version': PRIVATE_VERSION if private else GROUP_VERSION}
        if not private:
            value['release'] = GUI_RELEASE
            value['diagnosticExport'] = True
            value['contentRecovery'] = {'supplementDirectory': True, 'windowsOCR': True,
                'passwordDocuments': ['pdf', 'docx', 'xlsx', 'pptx'], 'automaticCloudDownload': False}
        if args.result_json:
            with args.result_json.open('x', encoding='utf8') as stream:
                json.dump(value, stream)
        elif sys.stdout is not None:
            print(json.dumps(value))
        return 0
    directory = args.data_dir or default_directory(args.client)
    if args.diagnose_accounts:
        if args.export_diagnostics or args.task_json or args.result_json or args.open_chat or args.open_chat_name or args.check_launch or args.launch_desktop_json:
            parser.error('--diagnose-accounts 须单独使用，可附带 --client。')
        from offline_accounts import identify_accounts, diagnostic_report
        if args.client == 'wecom':
            from wecom_snapshot import discover
        else:
            from offline_chat import discover
        value = diagnostic_report(identify_accounts(discover(args.scan_root), args.client), args.client)
        with args.diagnose_accounts.open('x', encoding='utf8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        return 0
    if args.export_diagnostics:
        startup.begin(directory,args.client)
        startup.export_bundle(args.export_diagnostics,{'release':'private-'+PRIVATE_VERSION if args.conversation_mode=='direct' else GUI_RELEASE,'client':args.client,'conversationMode':args.conversation_mode})
        return 0
    if args.launch_desktop_json:
        if not args.result_json or args.task_json or args.open_chat or args.open_chat_name or args.check_launch:
            parser.error('Invalid desktop launch arguments.')
        from offline_launcher import launch
        return launch(args.launch_desktop_json, args.result_json, directory, args.client)
    if args.task_json or args.result_json:
        if not args.task_json or not args.result_json:
            parser.error('--task-json 和 --result-json 必须同时提供。')
        from offline_batch import run_file
        return run_file(args.task_json, args.result_json, directory, client=args.client)
    startup.begin(directory, args.client)
    startup.record('window_initializing', diagnosticAutoExit=bool(args.check_launch))
    root = tk.Tk()
    session = DesktopSession(directory, args.workbench if args.client == 'wechat' else None,
                             client=args.client, conversation_mode=args.conversation_mode, scan_roots=args.scan_root)
    ChatApp(root, session, check_report=args.check_launch, check_chat=args.check_export_chat,
            open_chat=args.open_chat, open_chat_name=args.open_chat_name, open_result=args.open_result_json)
    root.mainloop()
    startup.record('window_closed')
    return 0


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
