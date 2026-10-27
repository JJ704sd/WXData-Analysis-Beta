"""Shared visual language for the native desktop surfaces.

One place defines colour, type and spacing so the main window, the message
browser, the date picker and the picture gallery read as a single product.
Tk cannot round its built-in widgets, so cards are drawn as a one pixel frame
around a flat surface and status chips are painted on a canvas.
"""
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# ---------------------------------------------------------------- colour ----
CANVAS = '#F1F4F9'          # window background
SURFACE = '#FFFFFF'         # cards, panels, fields
SURFACE_SOFT = '#F7F9FC'    # table headers, quiet fills
SURFACE_HOVER = '#EDF2F9'   # hover state
BORDER = '#E1E7F0'          # hairline
BORDER_STRONG = '#C7D2E1'   # emphasised border, focus ring
TEXT = '#111827'            # primary
TEXT_MUTED = '#5B6B82'      # secondary
TEXT_FAINT = '#8D9AAC'      # tertiary, hints
ACCENT = '#2563EB'          # primary action
ACCENT_HOVER = '#1D4ED8'
ACCENT_SOFT = '#E8F0FF'     # selected row, accent tint
ACCENT_TEXT = '#1D4ED8'
SUCCESS = '#0E9F6E'
SUCCESS_SOFT = '#E4F7EF'
WARNING = '#B45309'
WARNING_SOFT = '#FDF3E3'
DANGER = '#DC2626'
DANGER_SOFT = '#FDECEC'
INFO = '#0E7490'
INFO_SOFT = '#E4F4F8'

# ------------------------------------------------------------------ type ----
FONT_FAMILY = 'Microsoft YaHei UI'
DISPLAY = (FONT_FAMILY, 12, 'bold')
TITLE = (FONT_FAMILY, 11, 'bold')
BODY = (FONT_FAMILY, 9)
BODY_BOLD = (FONT_FAMILY, 9, 'bold')
CAPTION = (FONT_FAMILY, 8)
CAPTION_BOLD = (FONT_FAMILY, 8, 'bold')

# --------------------------------------------------------------- spacing ----
GAP_XS, GAP_SM, GAP_MD, GAP_LG = 4, 8, 12, 16


def card(parent, *, border=BORDER, surface=SURFACE, padding=0):
    """Return (outer, inner): a hairline bordered panel with a flat interior."""
    outer = tk.Frame(parent, background=border)
    inner = tk.Frame(outer, background=surface)
    inner.pack(fill='both', expand=True, padx=1, pady=1)
    if padding:
        inner.configure(padx=padding)
    return outer, inner


def rounded_rectangle(canvas, x1, y1, x2, y2, radius, **options):
    """A rounded rectangle on a Tk canvas; Tk has no native rounded shape."""
    points = [x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
              x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
              x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1]
    return canvas.create_polygon(points, smooth=True, **options)


class StatusPill(tk.Canvas):
    """A rounded status chip; Tk cannot round a ttk label."""

    TONES = {
        'none': (TEXT_MUTED, '#EDF1F7'),
        'pending': (WARNING, WARNING_SOFT),
        'done': (SUCCESS, SUCCESS_SOFT),
        'info': (INFO, INFO_SOFT),
        'accent': (ACCENT_TEXT, ACCENT_SOFT),
        'danger': (DANGER, DANGER_SOFT),
    }

    def __init__(self, parent, *, text='', tone='none', font=BODY_BOLD,
                 background=SURFACE, padx=9, pady=3):
        self.font = font
        self.background = background
        self.padx, self.pady = padx, pady
        self.tone = tone
        self.text = text
        self.metrics = tkfont.Font(root=parent, family=font[0], size=font[1],
                                    weight=font[2] if len(font) > 2 else 'normal')
        super().__init__(parent, height=self.metrics.metrics('linespace') + pady * 2,
                         width=self.metrics.measure(text) + padx * 2,
                         highlightthickness=0, borderwidth=0, background=background)
        self.bind('<Configure>', lambda _event: self.redraw())

    def set(self, text, tone=None):
        self.text = text
        if tone is not None:
            self.tone = tone
        self.configure(width=self.metrics.measure(text) + self.padx * 2)
        self.redraw()

    def set_tone(self, tone):
        self.tone = tone
        self.redraw()

    def redraw(self):
        self.delete('all')
        foreground, fill = self.TONES.get(self.tone, self.TONES['none'])
        width = max(self.winfo_width(), self.metrics.measure(self.text) + self.padx * 2)
        height = self.winfo_height()
        rounded_rectangle(self, 1, 1, width - 1, height - 1, min(height // 2, 9),
                          fill=fill, outline='')
        self.create_text(width // 2, height // 2, text=self.text, fill=foreground,
                         font=self.font)


class BrandMark(tk.Canvas):
    """A rounded app mark drawn with the client initial."""

    def __init__(self, parent, initial, *, size=30, background=CANVAS, fill=ACCENT):
        super().__init__(parent, width=size, height=size, highlightthickness=0,
                         borderwidth=0, background=background)
        self.initial, self.fill, self.size = initial, fill, size
        self.bind('<Configure>', lambda _event: self.redraw())

    def redraw(self):
        self.delete('all')
        size = self.winfo_width() or self.size
        rounded_rectangle(self, 0, 0, size - 1, size - 1, 8, fill=self.fill, outline='')
        self.create_text(size // 2, size // 2 + 1, text=self.initial, fill='#FFFFFF',
                         font=(FONT_FAMILY, 11, 'bold'))


class StatusDot(tk.Canvas):
    """A small dot that shows whether the app is idle, working or finished."""

    COLORS = {'idle': '#C3CEDD', 'busy': ACCENT, 'ok': SUCCESS, 'error': DANGER}

    def __init__(self, parent, *, size=9, background=SURFACE):
        super().__init__(parent, width=size, height=size, highlightthickness=0,
                         borderwidth=0, background=background)
        self.size = size
        self.state = 'idle'
        self.bind('<Configure>', lambda _event: self.redraw())
        self.redraw()

    def set_state(self, state):
        self.state = state
        self.redraw()

    def redraw(self):
        self.delete('all')
        size = self.winfo_width() or self.size
        self.create_oval(1, 1, size - 1, size - 1, fill=self.COLORS.get(self.state, self.COLORS['idle']),
                         outline='')


class SearchGlyph(tk.Canvas):
    """A drawn magnifier; the CJK UI font has no dependable search glyph."""

    def __init__(self, parent, *, size=13, color=TEXT_FAINT, background=SURFACE):
        super().__init__(parent, width=size, height=size, highlightthickness=0,
                         borderwidth=0, background=background)
        self.size, self.color = size, color
        self.bind('<Configure>', lambda _event: self.redraw())

    def redraw(self):
        self.delete('all')
        size = self.winfo_width() or self.size
        radius = size * 0.34
        self.create_oval(size * 0.12, size * 0.12, size * 0.12 + radius * 2, size * 0.12 + radius * 2,
                         outline=self.color, width=1)
        self.create_line(size * 0.12 + radius * 1.5, size * 0.12 + radius * 1.5,
                         size - 1, size - 1, fill=self.color, width=1)


def _without_arrow(element):
    """Remove the clam menubutton arrow so the label can carry its own caret."""
    name, options = element
    if name in ('Menubutton.indicator', 'Menubutton.downarrow'):
        return None
    children = options.get('children')
    if children:
        kept = [item for item in (_without_arrow(child) for child in children) if item is not None]
        options = dict(options, children=kept)
    return (name, options)


def _scrollbar(style, name):
    """Keep the native arrows and thumb with a usable minimum thickness."""
    style.configure(name, troughcolor='#E9EEF5', background='#C2CEDD',
                    bordercolor='#E9EEF5', lightcolor='#C2CEDD', darkcolor='#C2CEDD',
                    arrowcolor=TEXT_MUTED, arrowsize=14, borderwidth=1, width=14)


def apply_theme(root):
    """Install the visual language on a Tk root and return the ttk style."""
    style = ttk.Style(root)
    if 'clam' in style.theme_names():
        style.theme_use('clam')
    root.configure(background=CANVAS)
    root.option_add('*Font', BODY)
    root.option_add('*TCombobox*Listbox.background', SURFACE)
    root.option_add('*TCombobox*Listbox.foreground', TEXT)
    root.option_add('*TCombobox*Listbox.selectBackground', ACCENT_SOFT)
    root.option_add('*TCombobox*Listbox.selectForeground', TEXT)
    root.option_add('*TCombobox*Listbox.font', BODY)

    style.configure('.', background=CANVAS, foreground=TEXT, font=BODY,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                    focuscolor=BORDER_STRONG, fieldbackground=SURFACE)

    # Buttons: a quiet outlined default, one solid accent, and a text style.
    # width=0 makes every button hug its label instead of clam's fixed default.
    style.configure('TButton', background=SURFACE, foreground=TEXT, font=BODY,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                    borderwidth=1, relief='solid', padding=(9, 4), focuscolor=BORDER_STRONG, width=0)
    style.map('TButton',
              background=[('disabled', SURFACE_SOFT), ('pressed', '#E4EBF5'), ('active', SURFACE_HOVER)],
              foreground=[('disabled', '#AEB9C8')],
              bordercolor=[('disabled', '#EDF1F6'), ('active', BORDER_STRONG)],
              lightcolor=[('disabled', '#EDF1F6'), ('active', BORDER_STRONG)],
              darkcolor=[('disabled', '#EDF1F6'), ('active', BORDER_STRONG)])
    style.configure('TMenubutton', background=SURFACE, foreground=TEXT, font=BODY,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                    borderwidth=1, relief='solid', padding=(10, 4), arrowcolor=TEXT_MUTED,
                    arrowsize=9, arrowpadding=0, focuscolor=BORDER_STRONG, width=0)
    style.map('TMenubutton',
              background=[('disabled', SURFACE_SOFT), ('pressed', '#E4EBF5'), ('active', SURFACE_HOVER)],
              foreground=[('disabled', '#AEB9C8')],
              bordercolor=[('disabled', '#EDF1F6'), ('active', BORDER_STRONG)])
    # Drop the clam arrow element; the label carries its own caret instead.
    style.layout('TMenubutton', [item for item in
                                 (_without_arrow(element) for element in style.layout('TMenubutton'))
                                 if item is not None])
    style.configure('Accent.TButton', background=ACCENT, foreground='#FFFFFF',
                    font=BODY_BOLD, bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT,
                    borderwidth=1, relief='solid', padding=(11, 4), focuscolor=ACCENT, width=0)
    style.map('Accent.TButton',
              background=[('disabled', '#C3D3F2'), ('pressed', '#1B45BE'), ('active', ACCENT_HOVER)],
              foreground=[('disabled', '#F0F4FE')],
              bordercolor=[('disabled', '#C3D3F2'), ('active', ACCENT_HOVER)],
              lightcolor=[('disabled', '#C3D3F2'), ('active', ACCENT_HOVER)],
              darkcolor=[('disabled', '#C3D3F2'), ('active', ACCENT_HOVER)])
    style.configure('Quiet.TButton', background=SURFACE, foreground=TEXT_MUTED, font=BODY,
                    bordercolor=SURFACE, lightcolor=SURFACE, darkcolor=SURFACE,
                    borderwidth=0, relief='flat', padding=(4, 2), focuscolor=SURFACE, width=0)
    style.map('Quiet.TButton',
              background=[('disabled', SURFACE), ('pressed', SURFACE_HOVER), ('active', SURFACE_HOVER)],
              foreground=[('disabled', '#B4BFCE'), ('active', TEXT)],
              bordercolor=[('disabled', SURFACE), ('active', SURFACE)],
              lightcolor=[('disabled', SURFACE), ('active', SURFACE)],
              darkcolor=[('disabled', SURFACE), ('active', SURFACE)])
    style.configure('Day.TButton', background=SURFACE, foreground=TEXT, font=BODY,
                    bordercolor=SURFACE, lightcolor=SURFACE, darkcolor=SURFACE,
                    borderwidth=0, relief='flat', padding=(6, 4), focuscolor=SURFACE, width=0)
    style.map('Day.TButton',
              background=[('pressed', ACCENT_SOFT), ('active', SURFACE_HOVER)],
              foreground=[('active', ACCENT_TEXT)],
              bordercolor=[('disabled', SURFACE), ('active', SURFACE)],
              lightcolor=[('disabled', SURFACE), ('active', SURFACE)],
              darkcolor=[('disabled', SURFACE), ('active', SURFACE)])
    style.configure('Tool.TButton', background=SURFACE, foreground=TEXT_MUTED, font=BODY,
                    bordercolor=SURFACE, lightcolor=SURFACE, darkcolor=SURFACE,
                    borderwidth=0, relief='flat', padding=(6, 3), focuscolor=BORDER_STRONG, width=0)
    style.map('Tool.TButton',
              background=[('disabled', SURFACE), ('pressed', SURFACE_HOVER), ('active', SURFACE_HOVER)],
              foreground=[('disabled', '#B4BFCE'), ('active', ACCENT_TEXT)],
              bordercolor=[('disabled', SURFACE), ('active', SURFACE)],
              lightcolor=[('disabled', SURFACE), ('active', SURFACE)],
              darkcolor=[('disabled', SURFACE), ('active', SURFACE)])
    style.configure('Ghost.TButton', background=CANVAS, foreground=TEXT_MUTED, font=BODY,
                    bordercolor=CANVAS, lightcolor=CANVAS, darkcolor=CANVAS,
                    borderwidth=1, relief='solid', padding=(8, 3), focuscolor=BORDER_STRONG, width=0)
    style.map('Ghost.TButton',
              background=[('disabled', CANVAS), ('pressed', '#E4EBF5'), ('active', SURFACE_HOVER)],
              foreground=[('disabled', '#B4BFCE'), ('active', TEXT)],
              bordercolor=[('disabled', CANVAS), ('active', BORDER)],
              lightcolor=[('disabled', CANVAS), ('active', BORDER)],
              darkcolor=[('disabled', CANVAS), ('active', BORDER)])

    # Fields.
    style.configure('TEntry', fieldbackground=SURFACE, foreground=TEXT, font=BODY,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                    borderwidth=1, padding=(6, 4), insertcolor=TEXT)
    style.map('TEntry', bordercolor=[('focus', BORDER_STRONG)],
              lightcolor=[('focus', BORDER_STRONG)], darkcolor=[('focus', BORDER_STRONG)],
              fieldbackground=[('readonly', SURFACE_SOFT), ('disabled', SURFACE_SOFT)],
              foreground=[('disabled', TEXT_FAINT)])
    style.configure('TCombobox', fieldbackground=SURFACE, background=SURFACE, foreground=TEXT,
                    font=BODY, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                    borderwidth=1, padding=(6, 4), arrowcolor=TEXT_MUTED, arrowsize=12,
                    selectbackground=SURFACE, selectforeground=TEXT)
    style.map('TCombobox', fieldbackground=[('readonly', SURFACE), ('disabled', SURFACE_SOFT)],
              foreground=[('disabled', TEXT_FAINT)],
              bordercolor=[('focus', BORDER_STRONG), ('hover', BORDER_STRONG)],
              lightcolor=[('focus', BORDER_STRONG), ('hover', BORDER_STRONG)],
              darkcolor=[('focus', BORDER_STRONG), ('hover', BORDER_STRONG)],
              arrowcolor=[('disabled', '#B4BFCE'), ('hover', ACCENT)])
    style.configure('TSpinbox', fieldbackground=SURFACE, foreground=TEXT, font=BODY,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                    borderwidth=1, padding=(4, 3), arrowcolor=TEXT_MUTED, arrowsize=10)
    style.map('TSpinbox', bordercolor=[('focus', BORDER_STRONG)],
              lightcolor=[('focus', BORDER_STRONG)], darkcolor=[('focus', BORDER_STRONG)])
    # A borderless field for status bars: selectable text that still reads as text.
    style.configure('Quiet.TEntry', fieldbackground=SURFACE, foreground=TEXT_MUTED, font=CAPTION,
                    bordercolor=SURFACE, lightcolor=SURFACE, darkcolor=SURFACE,
                    borderwidth=0, padding=(2, 2), insertcolor=TEXT)
    style.map('Quiet.TEntry', bordercolor=[('focus', BORDER_STRONG)],
              lightcolor=[('focus', BORDER_STRONG)], darkcolor=[('focus', BORDER_STRONG)],
              fieldbackground=[('readonly', SURFACE), ('disabled', SURFACE)],
              foreground=[('disabled', TEXT_FAINT)])

    # Checkboxes and radio buttons.
    style.configure('TCheckbutton', background=SURFACE, foreground=TEXT, font=BODY,
                    focuscolor=SURFACE, indicatorcolor=SURFACE, indicatorbackground=SURFACE)
    style.map('TCheckbutton', background=[('active', SURFACE)],
              indicatorcolor=[('selected', ACCENT), ('disabled', '#E4E9F1')],
              foreground=[('disabled', TEXT_FAINT)])

    # Text surfaces.
    style.configure('TNotebook', background=CANVAS, borderwidth=0, bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER, tabmargins=(0, 0, 0, 0))
    style.configure('TNotebook.Tab', background=SURFACE_SOFT, foreground=TEXT_MUTED,
                    font=BODY, borderwidth=0, padding=(16, 7), focuscolor=SURFACE_SOFT)
    style.map('TNotebook.Tab',
              background=[('selected', SURFACE), ('active', '#EEF3FA')],
              foreground=[('selected', TEXT), ('active', TEXT)],
              expand=[('selected', (1, 1, 1, 0))])
    style.configure('TLabelFrame', background=SURFACE, bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER, borderwidth=1, relief='solid',
                    font=BODY, padding=8)
    style.configure('TLabelFrame.Label', background=SURFACE, foreground=TEXT_MUTED,
                    font=CAPTION_BOLD)

    # Scrolling.
    _scrollbar(style, 'Vertical.TScrollbar')
    _scrollbar(style, 'Horizontal.TScrollbar')

    # Tables.
    style.configure('Treeview', background=SURFACE, fieldbackground=SURFACE, foreground=TEXT,
                    font=BODY, borderwidth=0, bordercolor=BORDER, lightcolor=SURFACE,
                    darkcolor=SURFACE, relief='flat', rowheight=26)
    style.map('Treeview',
              background=[('selected', ACCENT_SOFT)],
              foreground=[('selected', ACCENT_TEXT)])
    style.configure('Treeview.Heading', background=SURFACE_SOFT, foreground=TEXT_MUTED,
                    font=CAPTION_BOLD, borderwidth=0, relief='flat', padding=(8, 7),
                    lightcolor=SURFACE_SOFT, darkcolor=SURFACE_SOFT)
    style.map('Treeview.Heading', background=[('active', '#E9EFF7')],
              foreground=[('active', TEXT)])

    # Progress and separators.
    style.configure('Horizontal.TProgressbar', background=ACCENT, troughcolor='#E4EAF3',
                    bordercolor='#E4EAF3', lightcolor=ACCENT, darkcolor=ACCENT,
                    thickness=4, borderwidth=0)
    style.configure('Vertical.TProgressbar', background=ACCENT, troughcolor='#E4EAF3',
                    bordercolor='#E4EAF3', lightcolor=ACCENT, darkcolor=ACCENT,
                    thickness=4, borderwidth=0)
    style.configure('TSeparator', background=BORDER)

    # Semantic label styles used across the window.
    style.configure('Surface.TFrame', background=SURFACE)
    style.configure('Title.TLabel', font=DISPLAY, foreground=TEXT, background=CANVAS)
    style.configure('Heading.TLabel', font=TITLE, foreground=TEXT, background=SURFACE)
    style.configure('Section.TLabel', font=CAPTION_BOLD, foreground=TEXT_MUTED, background=SURFACE)
    style.configure('Sub.TLabel', font=CAPTION, foreground=TEXT_MUTED, background=CANVAS)
    style.configure('Hint.TLabel', font=CAPTION, foreground=TEXT_FAINT, background=SURFACE)
    style.configure('Status.TLabel', font=BODY, foreground=TEXT, background=CANVAS)
    style.configure('ReviewPending.TLabel', foreground=WARNING, font=BODY_BOLD)
    style.configure('ReviewDone.TLabel', foreground=SUCCESS, font=BODY_BOLD)
    style.configure('ReviewNone.TLabel', foreground=TEXT_MUTED, font=BODY_BOLD)
    style.configure('Completion.TFrame', background=INFO_SOFT)
    style.configure('Completion.TLabel', background=INFO_SOFT, foreground='#0B5C73',
                    font=BODY_BOLD)
    style.configure('CompletionDetail.TLabel', background=INFO_SOFT, foreground='#3C7F92',
                    font=CAPTION)
    return style
