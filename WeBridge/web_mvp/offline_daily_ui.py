"""Native daily-report desktop; worker threads never call Tk widgets."""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import queue
import re
import threading
import traceback
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from offline_calendar import DatePicker
from offline_daily import BEIJING, DailyReportCancelled, DailyReportError, build_daily_report
from offline_daily_html import _business_name
from offline_theme import (ACCENT, BODY, BORDER, CANVAS, CAPTION, DISPLAY, SURFACE,
                           TEXT, TEXT_MUTED, apply_theme, card)


class DailyReportApp:
    def __init__(self, root, client, version, *, options=None, settings_directory=None,
                 builder=build_daily_report, opener=None):
        self.root, self.client, self.version = root, client, version
        self.label = '个微' if client == 'wechat' else '企微'
        self.builder, self.opener = builder, opener or self._open_path
        base = Path(os.environ.get('LOCALAPPDATA') or (Path.home() / '.local' / 'share'))
        self.settings_directory = Path(settings_directory) if settings_directory else base / 'LocalChatDailyReports' / client
        self.log_path = self.settings_directory / 'desktop.log'
        self.events, self.cancel_event = queue.Queue(), threading.Event()
        self.busy, self.closing, self.closed = False, False, False
        self.result, self.followups, self.roots, self.controls = None, [], [], []
        self.today = datetime.now(BEIJING).date()
        self.date = tk.StringVar(root, value=self.today.isoformat())
        self.lookback = tk.StringVar(root, value='7')
        self.output_parent = tk.StringVar(root, value=str(Path.home() / 'Documents' / 'LocalChatDailyReports'))
        self.names = tk.StringVar(root)
        self.auto_open = tk.BooleanVar(root, value=True)
        self.status = tk.StringVar(root, value='添加已复核的导出目录，选择日期后生成日报。')
        self.source_count = tk.StringVar(root, value='尚未添加导出')
        self.search = tk.StringVar(root)
        self.filter = tk.StringVar(root, value='全部线索')
        self.result_note = tk.StringVar(root, value='生成后可在这里查看概览、筛选线索，并打开完整 HTML 日报。')
        self.result_count = tk.StringVar(root, value='')
        self.metrics = [tk.StringVar(root, value='—') for _ in range(4)]
        self._load_settings()
        if options:
            if options.date:
                self.date.set(options.date)
            if options.export_root:
                self.roots = []
                for path in options.export_root:
                    self._add_path(path)
            if options.my_name:
                self.names.set('，'.join(options.my_name))
            if options.lookback_days is not None:
                self.lookback.set(str(options.lookback_days))
            if options.output:
                self.output_parent.set(str(options.output.parent))
        self._build()
        self._refresh_roots()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.bind('<Control-Return>', lambda _event: self.generate())
        self.poll_id = root.after(80, self.drain_events)

    def _text(self, parent, text, *, font=BODY, color=TEXT_MUTED, **packing):
        label = tk.Label(parent, text=text, font=font, background=SURFACE, foreground=color,
                         anchor='w', justify='left')
        label.pack(**packing)
        return label

    def _button(self, parent, text, command, *, editing=False, **kwargs):
        button = ttk.Button(parent, text=text, command=command, **kwargs)
        if editing:
            self.controls.append(button)
        return button

    def _build(self):
        root = self.root
        root.title(self.label + '本地消息日报 · ' + self.version)
        root.geometry('1100x760')
        root.minsize(900, 650)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)
        apply_theme(root)
        header = tk.Frame(root, background=CANVAS)
        header.grid(row=0, column=0, sticky='ew', padx=20, pady=(16, 10))
        tk.Label(header, text=self.label + ' · 每日消息工作台', font=DISPLAY,
                 background=CANVAS, foreground=TEXT).pack(side='left')
        tk.Label(header, text='概览  /  待跟进  /  HTML 日报', font=CAPTION,
                 background=CANVAS, foreground=TEXT_MUTED).pack(side='right')
        self.tabs = ttk.Notebook(root)
        self.tabs.grid(row=1, column=0, sticky='nsew', padx=20)
        self.prepare = tk.Frame(self.tabs, background=CANVAS)
        self.results = tk.Frame(self.tabs, background=SURFACE)
        self.tabs.add(self.prepare, text='  准备日报  ')
        self.tabs.add(self.results, text='  概览与待跟进  ')
        self.prepare.columnconfigure(0, weight=1, minsize=320)
        self.prepare.columnconfigure(1, weight=1, minsize=370)
        self.prepare.rowconfigure(0, weight=1)
        self._build_sources()
        self._build_settings()
        self._build_results()
        footer = tk.Frame(root, background=CANVAS)
        footer.grid(row=2, column=0, sticky='ew', padx=20, pady=(10, 14))
        footer.columnconfigure(0, weight=1)
        self.status_label = tk.Label(footer, textvariable=self.status, font=BODY,
                                     background=CANVAS, foreground=TEXT, anchor='w', justify='left', wraplength=590)
        self.status_label.grid(row=0, column=0, sticky='ew', padx=(0, 12))
        self.generate_button = self._button(footer, '生成日报', self.generate, style='Accent.TButton')
        self.generate_button.grid(row=0, column=1, padx=4)
        self.cancel_button = self._button(footer, '取消生成', self.cancel, state='disabled')
        self.cancel_button.grid(row=0, column=2, padx=4)
        self.progress = ttk.Progressbar(footer, mode='indeterminate')
        self.progress.grid(row=1, column=0, columnspan=3, sticky='ew', pady=(9, 6))
        tools = tk.Frame(footer, background=CANVAS)
        tools.grid(row=2, column=0, columnspan=3, sticky='ew')
        self.open_report_button = self._button(tools, '打开 HTML 日报', lambda: self.open_result('htmlFile'), state='disabled')
        self.open_report_button.pack(side='left')
        self.folder_button = self._button(tools, '打开结果目录', lambda: self.open_result('output'), state='disabled')
        self.folder_button.pack(side='left', padx=8)
        self.copy_button = self._button(tools, '复制结果路径', self.copy_result, state='disabled')
        self.copy_button.pack(side='left')
        self._button(tools, '排查日志', self.open_log, style='Quiet.TButton').pack(side='right')

    def _build_sources(self):
        outer, body = card(self.prepare)
        outer.grid(row=0, column=0, sticky='nsew', pady=(10, 0), padx=(0, 10))
        self._text(body, '1  选择消息材料', font=DISPLAY, color=TEXT, fill='x', padx=14, pady=(14, 8))
        self._text(body, '添加原助手已复核的正式导出或批次目录。\n可合并同一账号的私聊与群聊。', fill='x', padx=14)
        bar = tk.Frame(body, background=SURFACE)
        bar.pack(fill='x', padx=14, pady=12)
        self._button(bar, '添加目录…', self.choose_source, editing=True).pack(side='left')
        self._button(bar, '移除选中', self.remove_sources, editing=True).pack(side='left', padx=6)
        self._button(bar, '清空', self.clear_sources, editing=True).pack(side='left')
        frame = tk.Frame(body, background=SURFACE)
        frame.pack(fill='both', expand=True, padx=14)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.source_list = tk.Listbox(frame, selectmode='extended', exportselection=False, font=BODY,
                                     background=SURFACE, foreground=TEXT, borderwidth=0, highlightthickness=1,
                                     highlightbackground=BORDER, activestyle='none', height=6)
        self.source_list.grid(row=0, column=0, sticky='nsew')
        vertical = ttk.Scrollbar(frame, orient='vertical', command=self.source_list.yview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=self.source_list.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        self.source_list.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.source_list.bind('<Delete>', lambda _event: self.remove_sources())
        ttk.Label(body, textvariable=self.source_count, style='Hint.TLabel').pack(fill='x', padx=14, pady=(10, 5))
        self._text(body, '生成时核对复核状态及文件完整性。', font=CAPTION, fill='x', padx=14, pady=(0, 14))

    def _build_settings(self):
        outer, body = card(self.prepare)
        outer.grid(row=0, column=1, sticky='nsew', pady=(10, 0))
        canvas = tk.Canvas(body, background=SURFACE, highlightthickness=0, width=370)
        scrollbar = ttk.Scrollbar(body, orient='vertical', command=canvas.yview)
        scrollbar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        self.settings_body = form = tk.Frame(canvas, background=SURFACE, padx=14)
        window = canvas.create_window(0, 0, window=form, anchor='nw')
        form.bind('<Configure>', lambda _event: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(window, width=event.width))
        # Wheel handling stays local to this card and does not steal table scrolling.
        def wheel(event):
            canvas.yview_scroll(-int(event.delta / 120), 'units')
            return 'break'
        self._text(form, '2  设置日报', font=DISPLAY, color=TEXT, fill='x', pady=(14, 10))
        self._text(form, '日报日期 · 北京时间', fill='x', pady=(0, 6))
        row = tk.Frame(form, background=SURFACE)
        row.pack(fill='x')
        field = ttk.Entry(row, textvariable=self.date, width=13)
        field.pack(side='left', fill='x', expand=True)
        self.controls.append(field)
        self._button(row, '日历', self.pick_date, editing=True).pack(side='left', padx=(6, 0))
        for text, offset in (('今天', 0), ('昨天', 1)):
            self._button(row, text, lambda n=offset: self.date.set((datetime.now(BEIJING).date() - timedelta(days=n)).isoformat()),
                         editing=True).pack(side='left', padx=(4, 0))
        self._text(form, '待跟进回看 · 含当天，1～90 天', fill='x', pady=(14, 6))
        spin = ttk.Spinbox(form, from_=1, to=90, textvariable=self.lookback, width=7)
        spin.pack(anchor='w')
        self.controls.append(spin)
        self._text(form, '结果保存位置', fill='x', pady=(14, 6))
        row = tk.Frame(form, background=SURFACE)
        row.pack(fill='x')
        field = ttk.Entry(row, textvariable=self.output_parent)
        field.pack(side='left', fill='x', expand=True)
        self.controls.append(field)
        self._button(row, '浏览…', self.choose_output, editing=True).pack(side='left', padx=(6, 0))
        self._text(form, '每次自动新建日期目录，保留已有日报。', font=CAPTION, fill='x', pady=(5, 0))
        self._text(form, '群内本人称呼 · 可选，用逗号分隔', fill='x', pady=(14, 6))
        field = ttk.Entry(form, textvariable=self.names)
        field.pack(fill='x')
        self.controls.append(field)
        checkbox = ttk.Checkbutton(form, text='完成后自动打开 HTML 日报', variable=self.auto_open)
        checkbox.pack(anchor='w', pady=(16, 12))
        self.controls.append(checkbox)
        self._text(form, '只整理选定导出；待跟进线索需人工核对。\n生成期间可以取消，所填内容会保留。', font=CAPTION, fill='x', pady=(0, 14))
        def bind_wheel(widget):
            widget.bind('<MouseWheel>', wheel)
            for child in widget.winfo_children():
                bind_wheel(child)
        bind_wheel(form)

    def _build_results(self):
        self.results.columnconfigure(0, weight=1)
        self.results.rowconfigure(3, weight=1)
        metrics = tk.Frame(self.results, background=SURFACE)
        metrics.grid(row=0, column=0, sticky='ew', padx=14, pady=14)
        for index, title in enumerate(('当天消息', '活跃会话', '本人相关待跟进', '信息待补充')):
            metrics.columnconfigure(index, weight=1, uniform='metric')
            outer, body = card(metrics)
            outer.grid(row=0, column=index, sticky='ew', padx=(0 if index == 0 else 7, 0))
            tk.Label(body, textvariable=self.metrics[index], background=SURFACE, foreground=ACCENT,
                     font=('Microsoft YaHei UI', 20, 'bold')).pack(anchor='w', padx=12, pady=(8, 2))
            self._text(body, title, font=CAPTION, fill='x', padx=12, pady=(0, 10))
        ttk.Label(self.results, textvariable=self.result_note, style='Hint.TLabel', wraplength=850).grid(row=1, column=0, sticky='ew', padx=14)
        bar = tk.Frame(self.results, background=SURFACE)
        bar.grid(row=2, column=0, sticky='ew', padx=14, pady=10)
        ttk.Label(bar, text='搜索线索', style='Section.TLabel').pack(side='left')
        self.search_entry = ttk.Entry(bar, textvariable=self.search)
        self.search_entry.pack(side='left', fill='x', expand=True, padx=8)
        self.filter_box = ttk.Combobox(bar, textvariable=self.filter, state='readonly', width=17,
                                       values=('全部线索', '本人相关待跟进', '对象或身份待确认', '处理信号待核对'))
        self.filter_box.pack(side='left')
        self._button(bar, '清除筛选', self.clear_filters).pack(side='left', padx=(8, 0))
        self.search.trace_add('write', lambda *_: self.filter_followups())
        self.filter.trace_add('write', lambda *_: self.filter_followups())
        frame = tk.Frame(self.results, background=SURFACE)
        frame.grid(row=3, column=0, sticky='nsew', padx=14)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        columns = ('conversation', 'sender', 'time', 'state', 'request')
        self.followup_tree = ttk.Treeview(frame, columns=columns, show='headings', height=5, selectmode='browse')
        for name, title, width in zip(columns, ('会话', '发起人', '时间', '当前进展', '请求内容'), (145, 100, 130, 240, 330)):
            self.followup_tree.heading(name, text=title)
            self.followup_tree.column(name, width=width, minwidth=width, stretch=name == 'request')
        self.followup_tree.grid(row=0, column=0, sticky='nsew')
        vertical = ttk.Scrollbar(frame, orient='vertical', command=self.followup_tree.yview)
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=self.followup_tree.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        self.followup_tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.followup_tree.bind('<<TreeviewSelect>>', self.show_detail)
        ttk.Label(self.results, textvariable=self.result_count, style='Hint.TLabel').grid(row=4, column=0, sticky='ew', padx=14, pady=5)
        detail_frame = tk.Frame(self.results, background=SURFACE)
        detail_frame.grid(row=5, column=0, sticky='ew', padx=14, pady=(0, 12))
        self.detail = tk.Text(detail_frame, height=4, wrap='word', font=BODY, borderwidth=1,
                              relief='solid', background=CANVAS, foreground=TEXT, state='disabled')
        self.detail.pack(side='left', fill='x', expand=True)
        detail_scroll = ttk.Scrollbar(detail_frame, orient='vertical', command=self.detail.yview)
        detail_scroll.pack(side='right', fill='y')
        self.detail.configure(yscrollcommand=detail_scroll.set)
        self._set_detail('选择一条线索查看完整请求；引用和后续回应可在 HTML 日报中展开核对。')

    def _add_path(self, value):
        path = Path(str(value).strip().strip('"')).expanduser().absolute()
        if os.path.normcase(str(path)) not in {os.path.normcase(str(item)) for item in self.roots}:
            self.roots.append(path)

    def _refresh_roots(self):
        self.source_list.delete(0, 'end')
        for path in self.roots:
            self.source_list.insert('end', str(path))
        self.source_count.set(f'已添加 {len(self.roots)} 个目录 · 待生成时校验')

    def choose_source(self):
        if self.busy:
            return
        path = filedialog.askdirectory(parent=self.root, title='选择已复核的正式导出或批次目录', mustexist=True)
        if path:
            self._add_path(path)
            self._refresh_roots()

    def remove_sources(self):
        if not self.busy:
            selected = set(self.source_list.curselection())
            self.roots = [path for index, path in enumerate(self.roots) if index not in selected]
            self._refresh_roots()

    def clear_sources(self):
        if not self.busy:
            self.roots.clear()
            self._refresh_roots()

    def choose_output(self):
        if not self.busy:
            path = filedialog.askdirectory(parent=self.root, title='选择日报保存位置')
            if path:
                self.output_parent.set(path)

    def pick_date(self):
        if not self.busy:
            return DatePicker(self.root, self.date, '选择日报日期')

    def _load_settings(self):
        try:
            path = self.settings_directory / 'ui-settings.json'
            if path.stat().st_size > 131072:
                return
            saved = json.loads(path.read_text(encoding='utf-8'))
            for key, variable in (('outputParent', self.output_parent), ('names', self.names)):
                if isinstance(saved.get(key), str):
                    variable.set(saved[key])
            if type(saved.get('lookback')) is int and 1 <= saved['lookback'] <= 90:
                self.lookback.set(str(saved['lookback']))
            if type(saved.get('autoOpen')) is bool:
                self.auto_open.set(saved['autoOpen'])
            if isinstance(saved.get('roots'), list):
                for value in saved['roots'][:100]:
                    if isinstance(value, str) and value.strip():
                        self._add_path(value)
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def _save_settings(self):
        try:
            self.settings_directory.mkdir(parents=True, exist_ok=True)
            values = {'outputParent': self.output_parent.get(), 'names': self.names.get(),
                      'lookback': int(self.lookback.get()), 'autoOpen': self.auto_open.get(), 'roots': list(map(str, self.roots))}
            temporary = self.settings_directory / 'ui-settings.tmp'
            temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(self.settings_directory / 'ui-settings.json')
        except (OSError, ValueError):
            self._log('settings_not_saved', traceback.format_exc())

    def _log(self, event, detail=''):
        try:
            self.settings_directory.mkdir(parents=True, exist_ok=True)
            if self.log_path.exists() and self.log_path.stat().st_size > 2_000_000:
                self.log_path.replace(self.log_path.with_suffix('.previous.log'))
            with self.log_path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'time': datetime.now(BEIJING).isoformat(), 'event': event, 'detail': detail}, ensure_ascii=False) + '\n')
        except OSError:
            pass

    def _set_busy(self, busy):
        self.busy = busy
        for widget in self.controls:
            widget.configure(state='disabled' if busy else 'normal')
        self.source_list.configure(state='disabled' if busy else 'normal')
        self.generate_button.configure(state='disabled' if busy else 'normal')
        self.cancel_button.configure(state='normal' if busy else 'disabled')
        self.progress.start(12) if busy else self.progress.stop()

    def generate(self):
        if self.busy:
            return
        try:
            if not self.roots:
                raise DailyReportError('请先添加已复核的导出目录。')
            day = self.date.get().strip()
            selected = datetime.strptime(day, '%Y-%m-%d').date()
            if selected.isoformat() != day:
                raise ValueError('date')
            lookback = int(self.lookback.get())
            if not 1 <= lookback <= 90:
                raise DailyReportError('回看范围须为 1 至 90 天。')
            if not self.output_parent.get().strip():
                raise DailyReportError('请选择结果保存位置。')
            parent = Path(self.output_parent.get().strip().strip('"')).expanduser().absolute()
            output = parent / (day + '-' + self.client + '-' + datetime.now(BEIJING).strftime('%H%M%S-%f'))
            names = [value.strip() for value in re.split('[,，;；\n]', self.names.get()) if value.strip()]
        except (ValueError, DailyReportError) as error:
            self.status.set(str(error) if isinstance(error, DailyReportError) else '请填写有效的日期（YYYY-MM-DD）和回看天数。')
            self.tabs.select(self.prepare)
            return
        self.cancel_event.clear()
        self._save_settings()
        self._set_busy(True)
        self.status.set('正在核对材料，请稍候…')
        self._log('generation_started', str(output))
        roots = tuple(self.roots)
        def work():
            try:
                result = self.builder(roots, client=self.client, day=day, output=output, version=self.version,
                                      lookback_days=lookback, my_names=names,
                                      progress=lambda event: self.events.put(('progress', event)), cancel=self.cancel_event.is_set)
            except DailyReportCancelled:
                self._log('generation_cancelled')
                self.events.put(('cancelled', None))
            except Exception as error:
                self._log('generation_failed', traceback.format_exc())
                text = str(error) if isinstance(error, DailyReportError) else '生成失败，请检查目录、权限和可用空间；详细原因见排查日志。'
                self.events.put(('error', text))
            else:
                # A preview failure must not turn an already published report into a failed generation.
                try:
                    report = json.loads(Path(result['jsonFile']).read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    self._log('preview_unavailable', traceback.format_exc())
                    report = None
                self._log('generation_completed', str(result['output']))
                self.events.put(('done', (result, report)))
        self.worker = threading.Thread(target=work, name='daily-report-worker', daemon=False)
        self.worker.start()

    def cancel(self):
        if self.busy:
            self.cancel_event.set()
            self.cancel_button.configure(state='disabled')
            self.status.set('正在停止；当前处理阶段结束后清理临时结果…')

    def drain_events(self):
        while True:
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'progress':
                if self.cancel_event.is_set():
                    continue
                phase = data['phase']
                text = {'checking': '正在核对已复核材料', 'reading': '正在读取消息',
                        'organizing': '正在整理概览与待跟进', 'writing': '正在生成 HTML 日报'}.get(phase, '正在生成日报')
                if data.get('total'):
                    text += f" · 第 {data['current']} / {data['total']} 个导出"
                if 'messages' in data:
                    text += f" · 已读取 {data['messages']:,} 条"
                self.status.set(text + '…')
            else:
                self._set_busy(False)
                if kind == 'done':
                    result, report = data
                    self.present_result(result, report)
                    if self.auto_open.get() and not self.closing:
                        self.open_result('htmlFile')
                elif kind == 'cancelled':
                    self.status.set('已取消，未发布本次日报；所填内容已保留。')
                else:
                    self.status.set(data)
                    self.tabs.select(self.prepare)
                if self.closing:
                    self._destroy()
                    return
        if not self.closed:
            self.poll_id = self.root.after(80, self.drain_events)

    def present_result(self, result, report):
        self.result = result
        self.followups = report['followups'] if report else []
        overview = result['overview']
        for variable, key in zip(self.metrics, ('messages', 'activeConversations', 'personalFollowups', 'messagesWithGaps')):
            variable.set(f'{overview[key]:,}')
        self.result_note.set(f"{result['date']} · 仅统计选定材料。待跟进与处理信号需人工核对。" +
                             ('' if report else ' 列表预览暂不可用，请打开 HTML 日报。'))
        self.status.set('日报已生成，可打开完整报告或继续选择其他日期。')
        self.clear_filters()
        for button in (self.open_report_button, self.folder_button, self.copy_button):
            button.configure(state='normal')
        self.tabs.select(self.results)

    def clear_filters(self):
        self.search.set('')
        self.filter.set('全部线索')
        self.filter_followups()

    def filter_followups(self):
        query, choice = self.search.get().strip().casefold(), self.filter.get()
        rows = []
        self.followup_tree.delete(*self.followup_tree.get_children())
        for index, row in enumerate(self.followups):
            personal = row['target'] == 'self' and row['state'] not in {'completion_signal', 'withdrawal_signal'}
            if choice == '本人相关待跟进' and not personal:
                continue
            if choice == '对象或身份待确认' and row['target'] != 'unknown':
                continue
            if choice == '处理信号待核对' and row['state'] not in {'completion_signal', 'withdrawal_signal'}:
                continue
            name = _business_name(row['chatName'], row['chatId'], '会话名称待确认')
            if query and query not in ' '.join((name, row['senderName'], row['text'], row['stateLabel'])).casefold():
                continue
            rows.append((index, row, name))
        for index, row, name in rows[:500]:
            self.followup_tree.insert('', 'end', iid=str(index), values=(name, row['senderName'],
                row['time'][:16].replace('T', ' '), row['stateLabel'], row['text'].replace('\n', ' ')[:140]))
        self.result_count.set(f'匹配 {len(rows):,} 条 · ' + ('展示前 500 条，其余请在 HTML 日报中查看' if len(rows) > 500 else '选中线索查看完整请求'))
        self._set_detail('当前筛选下没有线索。' if not rows else '选择一条线索查看完整请求；处理进展需结合 HTML 日报的原文依据核对。')

    def _set_detail(self, text):
        self.detail.configure(state='normal')
        self.detail.delete('1.0', 'end')
        self.detail.insert('1.0', text)
        self.detail.configure(state='disabled')

    def show_detail(self, _event=None):
        selected = self.followup_tree.selection()
        if selected:
            row = self.followups[int(selected[0])]
            name = _business_name(row['chatName'], row['chatId'], '会话名称待确认')
            self._set_detail(f"{name} · {row['senderName']} · {row['time'][:16].replace('T', ' ')}\n{row['stateLabel']}\n\n{row['text']}")

    @staticmethod
    def _open_path(path):
        if path.suffix.lower() == '.html':
            if not webbrowser.open(path.resolve().as_uri()):
                raise OSError('No browser accepted the report')
        elif os.name == 'nt':
            os.startfile(str(path))
        else:
            webbrowser.open(path.resolve().as_uri())

    def open_result(self, key):
        if self.result:
            try:
                self.opener(Path(self.result[key]))
            except (OSError, ValueError):
                self._log('open_failed', traceback.format_exc())
                self.status.set('文件已生成，自动打开未成功；可复制结果路径手动打开。')

    def copy_result(self):
        if self.result:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.result['output'])
            self.status.set('结果目录已复制。')

    def open_log(self):
        try:
            self._log('diagnostics_opened')
            self.opener(self.log_path)
        except OSError:
            self.status.set('暂时无法打开日志，请检查本机文件打开方式。')

    def close(self):
        if self.busy:
            if not self.closing and messagebox.askyesno('停止并关闭', '正在生成日报。停止后关闭窗口？', parent=self.root):
                self.closing = True
                self.cancel()
            return
        self._save_settings()
        self._destroy()

    def _destroy(self):
        self.closed = True
        self.root.after_cancel(self.poll_id)
        self.root.destroy()


def run_daily_desktop(client, options, version):
    checking = options.check_desktop
    if checking and checking.exists():
        return 2
    root = tk.Tk()
    if checking:
        root.withdraw()
    app = DailyReportApp(root, client, version, options=options,
                         settings_directory=checking.parent / '.desktop-check-unused' if checking else None)
    if checking:
        try:
            root.update_idletasks()
            picker = app.pick_date()
            picker.update_idletasks()
            picker.destroy()
            details = {'ok': True, 'client': client, 'version': version, 'desktopUI': True,
                       'tkVersion': root.tk.call('info', 'patchlevel'),
                       'tabs': len(app.tabs.tabs()), 'datePickerInitialized': True,
                       'reportColumns': list(app.followup_tree['columns']),
                       'requiresPythonInstallation': not getattr(__import__('sys'), 'frozen', False)}
            checking.parent.mkdir(parents=True, exist_ok=True)
            with checking.open('x', encoding='utf-8') as stream:
                stream.write(json.dumps(details, ensure_ascii=False, indent=2) + '\n')
        except OSError:
            return 2
        finally:
            app._destroy()
        return 0
    root.mainloop()
    return 0
