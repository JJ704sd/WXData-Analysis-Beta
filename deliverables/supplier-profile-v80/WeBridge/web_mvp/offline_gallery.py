"""Paged native picture cards, including missing media and nested forwards."""
import tkinter as tk
from tkinter import ttk

from offline_images import image_state
from offline_view import read_image
from offline_theme import (BORDER, CANVAS, SURFACE, SURFACE_SOFT, TEXT, TEXT_FAINT, TEXT_MUTED,
                           BODY, BODY_BOLD, CAPTION, SUCCESS, WARNING, DANGER, apply_theme)

COLORS = {'original': '#067647', 'preview': WARNING, 'local_preview': WARNING,
          'missing': DANGER, 'not_loaded': TEXT_MUTED}


class ImageGallery(ttk.Frame):
    def __init__(self, parent, *, open_image, show_source, notify):
        super().__init__(parent)
        self.open_image, self.show_source, self.notify = open_image, show_source, notify
        self.view = None
        self.page = None
        self.photos = []
        self.card_count = 0
        self.summary = tk.StringVar(value='选择群聊后，在此集中查看图片，包含转发和引用中的图片。')
        self.position = tk.StringVar()
        top = tk.Frame(self, background=SURFACE)
        top.pack(fill='x', padx=16, pady=(12, 0))
        tk.Label(top, textvariable=self.summary, font=BODY, background=SURFACE,
                 foreground=TEXT, anchor='w').pack(anchor='w')
        tk.Label(top, text='点图片或“查看大图”放大；“定位原消息”查看上下文。同图多次引用保留出处，缩略图不会补成原图。',
                 font=CAPTION, background=SURFACE, foreground=TEXT_FAINT, anchor='w').pack(anchor='w', pady=(5, 0))
        controls = tk.Frame(top, background=SURFACE)
        controls.pack(fill='x', pady=(8, 0))
        self.previous = ttk.Button(controls, text='上一页图片', command=lambda: self.load(self.page['offset'] - 24))
        self.previous.pack(side='left')
        self.following = ttk.Button(controls, text='下一页图片', command=lambda: self.load(self.page['offset'] + 24))
        self.following.pack(side='left', padx=8)
        tk.Label(controls, textvariable=self.position, font=CAPTION, background=SURFACE,
                 foreground=TEXT_MUTED).pack(side='right')
        area = tk.Frame(self, background=SURFACE)
        area.pack(fill='both', expand=True, padx=16, pady=(8, 12))
        self.canvas = tk.Canvas(area, highlightthickness=0, background=SURFACE_SOFT)
        scrollbar = ttk.Scrollbar(area, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        self.cards = tk.Frame(self.canvas, background=SURFACE_SOFT)
        self.window = self.canvas.create_window(0, 0, anchor='nw', window=self.cards)
        self.cards.bind('<Configure>', lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', self.resize)
        # Bind only this gallery's descendants, never the whole application.
        self.canvas.bind('<MouseWheel>', self.wheel)
        self.clear()

    def clear(self):
        self.view, self.page = None, None
        self.photos.clear()
        self.card_count = 0
        self.position.set('')
        self.previous.configure(state='disabled')
        self.following.configure(state='disabled')
        self.summary.set('选择群聊后，在此集中查看图片，包含转发和引用中的图片。')
        for child in self.cards.winfo_children():
            child.destroy()

    def set_source(self, view):
        if self.view is not None and self.view.root == view.root:
            return
        self.clear()
        self.view = view
        counts = view.image_counts
        self.summary.set(f'共 {len(view.image_index)} 个图片引用：原图 {counts["original"]} · '
                         f'仅缩略图 {counts["preview"]} · 本地预览 {counts["local_preview"]} · 缺失 {counts["missing"]} · 未加载 {counts["not_loaded"]}（含转发、引用）')

    def wheel(self, event):
        self.canvas.yview_scroll(-int(event.delta / 120), 'units')
        return 'break'

    def resize(self, event):
        self.canvas.itemconfigure(self.window, width=event.width)
        columns = max(1, min(3, event.width // 235))
        for index, card in enumerate(self.cards.winfo_children()):
            card.grid(row=index // columns, column=index % columns, padx=6, pady=6, sticky='nsew')
        for column in range(3):
            self.cards.columnconfigure(column, weight=1 if column < columns else 0)

    def load(self, offset=0):
        if self.view is None:
            return
        self.page = self.view.image_page(offset)
        for child in self.cards.winfo_children():
            child.destroy()
        self.photos.clear()
        self.card_count = 0
        for index, entry in enumerate(self.page['images'], self.page['offset'] + 1):
            self.card(entry, index)
        self.resize(type('Size', (), {'width': max(240, self.canvas.winfo_width())})())
        self.canvas.yview_moveto(0)
        total, offset = self.page['total'], self.page['offset']
        self.position.set(f'{offset + 1 if total else 0}–{offset + self.card_count} / {total}')
        self.previous.configure(state='normal' if offset > 0 else 'disabled')
        self.following.configure(state='normal' if offset + 24 < total else 'disabled')

    def card(self, entry, index):
        from PIL import ImageTk
        asset = entry['asset']
        state, label = image_state(asset)
        card = tk.Frame(self.cards, background=SURFACE, padx=12, pady=12,
                        highlightbackground=BORDER, highlightthickness=1)
        self.card_count += 1
        def text(value, **options):
            options.setdefault('background', SURFACE)
            widget = tk.Label(card, text=value, anchor='w', justify='left', wraplength=208, **options)
            widget.pack(fill='x', pady=(0, 5))
            return widget
        text(f'{index:02d}  {label}', foreground=COLORS[state], font=BODY_BOLD)
        preview = tk.Frame(card, width=210, height=148, background=SURFACE_SOFT)
        preview.pack(fill='x', pady=5)
        preview.pack_propagate(False)
        if asset.get('status') == 'ready':
            try:
                photo = ImageTk.PhotoImage(read_image(self.view.root, asset, (208, 146)), master=self)
                self.photos.append(photo)
                image = tk.Label(preview, image=photo, background=SURFACE_SOFT, cursor='hand2')
                image.pack(expand=True)
                image.bind('<Button-1>', lambda _event, a=asset: self.open_image(a))
                ttk.Button(card, text='查看大图' + ('（仅缩略图）' if state == 'preview' else ''),
                           command=lambda a=asset: self.open_image(a)).pack(fill='x', pady=(2, 6))
            except (OSError, ValueError) as error:
                tk.Label(preview, text='预览失败\n' + str(error), wraplength=200,
                         foreground=COLORS['missing'], background=SURFACE_SOFT).pack(expand=True)
        else:
            placeholder = '本次未加载媒体\n请包含附件后重新导出' if state == 'not_loaded' else '图片未取得\n本地没有可显示的文件'
            tk.Label(preview, text=placeholder, foreground=COLORS[state],
                     background=SURFACE_SOFT, justify='center').pack(expand=True)
        text(entry['senderName'] + '\n' + (entry['time'] or entry['rootTime']), foreground=TEXT, font=BODY)
        text(entry['context'], foreground=TEXT_MUTED, font=CAPTION)
        if entry['trail']:
            text('外层消息：' + entry['rootSenderName'] + '\n' + entry['rootTime'],
                 foreground=TEXT_FAINT, font=CAPTION)
        if asset.get('reason'):
            text(asset['reason'], foreground=COLORS[state], font=CAPTION)
        ttk.Button(card, text='定位原消息', command=lambda e=entry: self.show_source(e)).pack(fill='x', pady=(4, 0))
        def bind_wheel(widget):
            widget.bind('<MouseWheel>', self.wheel)
            for child in widget.winfo_children():
                bind_wheel(child)
        bind_wheel(card)
