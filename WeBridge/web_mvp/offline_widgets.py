"""Native message tree, inline image previews and nested record reading."""
from datetime import datetime
import os
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from offline_chat import BEIJING, QUOTE_NOTES
from offline_view import attachment_path, document_path, read_image
from offline_gallery import ImageGallery
from offline_images import quote_link_text
from offline_theme import (BORDER, CANVAS, SURFACE, SURFACE_SOFT, TEXT, TEXT_FAINT, TEXT_MUTED,
                           BODY, BODY_BOLD, CAPTION, WARNING, DANGER)

KINDS = {'text': '文字', 'image': '图片', 'file': '附件', 'record': '转发聊天', 'quote': '引用',
         'video': '视频', 'voice': '语音', 'emoji': '表情', 'system': '系统', 'link': '链接',
         'revoke': '撤回', 'card': '分享消息', 'unsupported': '未支持类型'}


def summary(row):
    return ' '.join((row.get('text') or row.get('media', {}).get('filename') or KINDS.get(row['kind'], row['kind'])).split())[:100]


def asset_status(row):
    asset = row.get('asset', {})
    if asset.get('previewOnly'):
        return '本地预览（非原图）' if asset.get('previewVariant') == 'local_converted' else '仅缩略图/封面'
    return {'ready': '完整原件', 'pending': '尚缺原件', 'unavailable': '暂无法读取'}.get(asset.get('status'), '')


class MessageBrowser(ttk.Frame):
    def __init__(self, parent, *, notify=None, opener=None, show_source=None):
        super().__init__(parent)
        self.notify = notify or (lambda _text: None)
        self.opener = opener or os.startfile
        self.rows = {}
        self.view_root = None
        self.photos = []
        self.last_image_count = 0
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill='both', expand=True)
        conversation = ttk.Frame(self.tabs, style='Surface.TFrame')
        self.tabs.add(conversation, text='  聊天记录与上下文  ')
        self.gallery = ImageGallery(self.tabs, open_image=self.image_window,
                                    show_source=show_source or (lambda _entry: None), notify=self.notify)
        self.tabs.add(self.gallery, text='  图片总览（含转发、引用）  ')
        self.tabs.bind('<<NotebookTabChanged>>', self._tab_changed)
        panes = ttk.PanedWindow(conversation, orient='vertical')
        panes.pack(fill='both', expand=True)
        listing = ttk.Frame(panes, style='Surface.TFrame')
        details = ttk.Frame(panes, style='Surface.TFrame')
        panes.add(listing, weight=4)
        panes.add(details, weight=1)
        self.tree = ttk.Treeview(listing, columns=('time', 'kind', 'state'), selectmode='browse', height=12)
        self.tree.heading('#0', text='发送者与消息（点击箭头展开转发聊天）', anchor='w')
        self.tree.heading('time', text='时间')
        self.tree.heading('kind', text='类型')
        self.tree.heading('state', text='本机附件')
        self.tree.column('#0', width=350, minwidth=180, anchor='w')
        self.tree.column('time', width=112, minwidth=90, stretch=False)
        self.tree.column('kind', width=60, minwidth=54, stretch=False)
        self.tree.column('state', width=92, minwidth=80, stretch=False)
        vertical = ttk.Scrollbar(listing, orient='vertical', command=self.tree.yview)
        horizontal = ttk.Scrollbar(listing, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        listing.rowconfigure(0, weight=1)
        listing.columnconfigure(0, weight=1)
        self.detail = ScrolledText(details, wrap='word', height=5, state='disabled', relief='flat', padx=14, pady=10,
                                   background=SURFACE, foreground=TEXT, font=BODY, highlightthickness=0,
                                   borderwidth=0, spacing1=1, spacing3=3)
        self.detail.pack(fill='both', expand=True)
        self.detail.tag_configure('meta', foreground=TEXT_MUTED, font=CAPTION, spacing1=8, spacing3=5)
        self.detail.tag_configure('notice', foreground=WARNING, spacing3=5)
        self.detail.tag_configure('danger', foreground=DANGER, spacing3=5)
        self.detail.tag_configure('sender', foreground=TEXT, font=BODY_BOLD, spacing1=8)
        self.tree.bind('<<TreeviewSelect>>', self.selected)
        self.clear()

    def _tab_changed(self, _event=None):
        if self.tabs.select() == str(self.gallery) and self.gallery.page is None:
            self.gallery.load()

    def set_gallery(self, view):
        self.gallery.set_source(view)
        self.tabs.tab(self.gallery, text=f' 图片总览 · {len(view.image_index)} 项（含转发、引用） ')
        self._tab_changed()

    def reveal(self, entry):
        self.tabs.select(0)
        for item in self.tree.get_children():
            if self.rows[item].get('id') == entry['rootMessageId']:
                self.tree.item(item, open=True)
                chosen = item
                for kind, index in entry.get('nodePath', []):
                    children = self.tree.get_children(chosen)
                    offset = 0 if kind == 'quote' else int(bool(self.rows[chosen].get('quote'))) + index
                    if offset >= len(children):
                        break
                    chosen = children[offset]
                    self.tree.item(chosen, open=True)
                self.tree.selection_set(chosen)
                self.tree.see(chosen)
                self.selected()
                break

    def clear(self, clear_gallery=True):
        self.rows.clear()
        self.tree.delete(*self.tree.get_children())
        self.detail.configure(state='normal')
        self.detail.delete('1.0', 'end')
        self.detail.insert('end', '选择左侧群聊，点击消息查看全文、图片和附件。\n转发聊天可逐层展开。')
        self.detail.configure(state='disabled')
        self.photos.clear()
        self.view_root = None
        if clear_gallery:
            self.gallery.clear()
            self.tabs.tab(self.gallery, text=' 图片总览（含转发、引用） ')

    def load(self, value):
        self.clear(clear_gallery=False)
        self.view_root = value['viewRoot']
        def insert(row, parent='', depth=0):
            sender = '自己' if row.get('isSelf') is True else row.get('senderName') or '身份未知'
            stamp = row.get('time') or ''
            if row.get('timestamp'):
                stamp = datetime.fromtimestamp(row['timestamp'], BEIJING).strftime('%m-%d %H:%M:%S')
            item = self.tree.insert(parent, 'end', text=sender + '：' + summary(row),
                                    values=(stamp, KINDS.get(row['kind'], row['kind']), asset_status(row)))
            self.rows[item] = row
            if row.get('quote'):
                quote = row['quote']
                insert({**quote['message'], 'senderName': quote['message'].get('senderName') or quote.get('senderName') or '引用来源'}, item, depth + 1)
            for child in row.get('record', {}).get('items', []):
                insert(child, item, depth + 1)
            return item
        for row in value['messages']:
            insert(row)
        roots = self.tree.get_children()
        if roots:
            chosen = roots[-1]
            if all(self.rows[item]['kind'] == 'record' for item in roots):
                def has_image(row):
                    return (row.get('asset', {}).get('status') == 'ready' and row.get('asset', {}).get('mime', '').startswith('image/')) or any(has_image(child) for child in row.get('record', {}).get('items', []))
                chosen = next((item for item in reversed(roots) if has_image(self.rows[item])), chosen)
                def expand(item):
                    if self.rows[item].get('record'):
                        self.tree.item(item, open=True)
                        for child in self.tree.get_children(item):
                            expand(child)
                expand(chosen)
            self.tree.selection_set(chosen)
            self.tree.focus(chosen)
            self.tree.see(chosen)
            self.tree.focus_set()
            self.selected()

    def selected(self, _event=None):
        selected = self.tree.selection()
        if not selected or selected[0] not in self.rows:
            return
        for child in self.detail.winfo_children():
            child.destroy()
        self.detail.configure(state='normal')
        self.detail.delete('1.0', 'end')
        self.photos.clear()
        self.last_image_count = 0
        self._render(self.rows[selected[0]])
        self.detail.configure(state='disabled')
        self.detail.see('1.0')

    def _button(self, label, command):
        button = ttk.Button(self.detail, text=label, command=command)
        self.detail.window_create('end', window=button, padx=3, pady=5)
        self.detail.insert('end', ' ')

    def _render(self, row, depth=0, budget=None):
        budget = budget if budget is not None else [1000]
        if budget[0] <= 0:
            return
        budget[0] -= 1
        own = '自己' if row.get('isSelf') is True else row.get('senderName') or '身份未知'
        prefix = '  ' * depth
        self.detail.insert('end', prefix + own + '  ' + str(row.get('time') or '') + '  ' + KINDS.get(row['kind'], row['kind']) + '\n', 'meta')
        self.detail.insert('end', (row.get('text') or '') + '\n')
        if row.get('description'):
            self.detail.insert('end', row['description'] + '\n')
        mentions = row.get('mentions', {})
        if mentions.get('ids'):
            self.detail.insert('end', '结构化 @：' + ', '.join(mentions['ids']) + '\n', 'meta')
        for link in row.get('links', []):
            self.detail.insert('end', link + '\n')
        if row.get('quote') and quote_link_text(row['quote']):
            self.detail.insert('end', quote_link_text(row['quote']) + '\n', 'meta')
        if row.get('quote'):
            quote = row['quote']
            self.detail.insert('end', '引用 · ' + quote.get('senderName', '') + '\n', 'meta')
            if quote.get('resolutionStatus') == 'linked_local_source':
                self.detail.insert('end', '已关联同群本地原消息\n', 'meta')
            elif quote.get('resolutionStatus') not in ('embedded', None):
                self.detail.insert('end', '引用正文未取得：' + QUOTE_NOTES.get(quote['resolutionStatus'], '来源尚未确认') + '\n', 'notice')
            self._render(quote['message'], depth + 1, budget)
        asset = row.get('asset')
        if asset:
            self.detail.insert('end', asset_status(row) + (' · ' + asset['reason'] if asset.get('reason') else '') + '\n', 'notice')
            if asset.get('status') == 'ready':
                if asset.get('mime', '').startswith('image/'):
                    try:
                        from PIL import ImageTk
                        image = ImageTk.PhotoImage(read_image(self.view_root, asset, (720, 430) if depth == 0 else (560, 280)), master=self)
                        self.photos.append(image)
                        self.detail.image_create('end', image=image)
                        self.detail.insert('end', '\n')
                        self.last_image_count += 1
                        self._button('放大图片' + ('（非原图预览）' if asset.get('previewOnly') else ''), lambda a=asset: self.image_window(a))
                    except (OSError, ValueError) as error:
                        self.detail.insert('end', '图片未能显示：' + str(error) + '\n', 'notice')
                self._button('打开附件' if row['kind'] == 'file' else '打开本地文件', lambda a=asset: self.open_attachment(a))
                self.detail.insert('end', '\n')
        record = row.get('record')
        document = row.get('attachmentContent')
        if document:
            self.detail.insert('end', '原文件：' + document['fileStatusText'] + '；正文：' + document['extractionStatusText'] + '\n', 'notice')
            for notice in document['notices']:
                self.detail.insert('end', notice + '\n', 'notice')
            if document['text']:
                self.detail.insert('end', '附件正文（材料内容）：\n' + document['text'][:20000] + '\n')
                if len(document['text']) > 20000:
                    self.detail.insert('end', '窗口先显示20000字符；已提取的完整正文见JSONL、JSON和MD导出。\n', 'notice')
        if record:
            self.detail.insert('end', prefix + f'转发聊天 · {record.get("count", len(record.get("items", [])))} 条\n', 'meta')
            if record.get('status') != 'ok':
                self.detail.insert('end', '解析状态：' + record.get('status', 'unknown') + '\n', 'notice')
            for child in record.get('items', []):
                self._render(child, depth + 1, budget)
            if budget[0] <= 0:
                self.detail.insert('end', '此处先显示 1000 项；可在上方树中选择任一条目查看完整内容。\n', 'notice')

    def open_attachment(self, asset):
        try:
            target = document_path(self.view_root, asset)
            self.opener(str(target))
            self.notify('已交给系统默认程序打开：' + target.name)
            return target
        except (OSError, ValueError) as error:
            self.notify(str(error))
            messagebox.showerror('无法打开附件', str(error), parent=self)
            return None

    def image_window(self, asset):
        try:
            view_root = self.view_root
            attachment_path(view_root, asset)
            from PIL import ImageTk
            window = tk.Toplevel(self)
            window.title(('本地预览' if asset.get('previewVariant') == 'local_converted' else '缩略图') + '（原图未取得）' if asset.get('previewOnly') else '图片原件')
            window.geometry(f'{min(1000, window.winfo_screenwidth() - 80)}x{min(720, window.winfo_screenheight() - 120)}')
            bar = ttk.Frame(window, padding=8)
            bar.pack(fill='x')
            area = ttk.Frame(window)
            area.pack(fill='both', expand=True)
            canvas = tk.Canvas(area, background='#e2e8f0', highlightthickness=0)
            xscroll = ttk.Scrollbar(area, orient='horizontal', command=canvas.xview)
            yscroll = ttk.Scrollbar(area, orient='vertical', command=canvas.yview)
            canvas.configure(xscrollcommand=xscroll.set, yscrollcommand=yscroll.set)
            canvas.grid(row=0, column=0, sticky='nsew')
            yscroll.grid(row=0, column=1, sticky='ns')
            xscroll.grid(row=1, column=0, sticky='ew')
            area.rowconfigure(0, weight=1)
            area.columnconfigure(0, weight=1)
            def display(original=False):
                try:
                    bounds = (40000, 40000) if original else (max(300, canvas.winfo_width() - 20), max(200, canvas.winfo_height() - 20))
                    image = ImageTk.PhotoImage(read_image(view_root, asset, bounds), master=window)
                    canvas.delete('all')
                    canvas.create_image(10, 10, anchor='nw', image=image)
                    canvas.photo = image
                    canvas.configure(scrollregion=canvas.bbox('all'))
                except (OSError, ValueError) as error:
                    self.notify('图片未能显示：' + str(error))
            ttk.Button(bar, text='适应窗口', command=display).pack(side='left')
            ttk.Button(bar, text='原始尺寸 1:1', command=lambda: display(True)).pack(side='left', padx=8)
            ttk.Label(bar, text='可滚动查看；缩略图不会被标成原图。').pack(side='left')
            window.update_idletasks()
            display()
            return window
        except (OSError, ValueError) as error:
            self.notify('图片未能显示：' + str(error))
            return None
