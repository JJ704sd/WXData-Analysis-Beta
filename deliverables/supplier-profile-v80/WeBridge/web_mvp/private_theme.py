"""Calm, readable visual language for the private-chat desktop surfaces.

The shared archive theme remains untouched. These styles are installed only
in a private-chat interpreter; native controls keep their keyboard behavior.
"""
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

try:
    import offline_theme as archive_theme
except ModuleNotFoundError:
    from . import offline_theme as archive_theme

CANVAS = '#F5F7F6'
SURFACE = '#FFFFFF'
SURFACE_SOFT = '#F0F5F2'
SURFACE_HOVER = '#EAF2ED'
BORDER = '#E2EAE5'
BORDER_STRONG = '#A4CABB'
TEXT = '#17332A'
TEXT_MUTED = '#718078'
TEXT_FAINT = '#8F9D95'
ACCENT = '#16A271'
ACCENT_HOVER = '#11845C'
ACCENT_SOFT = '#E8F5EE'
ACCENT_TEXT = '#127552'
SUCCESS = ACCENT_TEXT
SUCCESS_SOFT = ACCENT_SOFT
WARNING = '#976321'
WARNING_SOFT = '#FFF5E5'
DANGER = '#B94949'
DANGER_SOFT = '#FBEDED'
INFO = '#427B68'
INFO_SOFT = '#EAF4EF'
FONT_FAMILY = 'Microsoft YaHei UI'
DISPLAY = (FONT_FAMILY, 22, 'bold')
TITLE = (FONT_FAMILY, 14, 'bold')
BODY = (FONT_FAMILY, 11)
BODY_BOLD = (FONT_FAMILY, 11, 'bold')
CAPTION = (FONT_FAMILY, 9)
CAPTION_BOLD = (FONT_FAMILY, 9, 'bold')
METRIC = (FONT_FAMILY, 30, 'bold')
GAP_XS, GAP_SM, GAP_MD, GAP_LG, GAP_XL = 4, 8, 12, 16, 24


def _background(parent):
    try:
        return parent.cget('background')
    except tk.TclError:
        style = ttk.Style(parent)
        return style.lookup(parent.cget('style') or parent.winfo_class(), 'background') or CANVAS


class RoundedPanel(tk.Frame):
    """Geometry-propagating native panel with a quiet rounded outline."""
    def __init__(self, parent, *, surface=SURFACE, border=BORDER, padding=16):
        super().__init__(parent, background=_background(parent), borderwidth=0)
        self.surface, self.border = surface, border
        self.background_canvas = tk.Canvas(self, background=self.cget('background'),
                                          highlightthickness=0, borderwidth=0)
        self.background_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.inner = tk.Frame(self, background=surface, borderwidth=0)
        self.inner.pack(fill='both', expand=True, padx=max(2, padding), pady=max(2, padding))
        self.bind('<Configure>', self._draw, add='+')

    def _draw(self, event):
        if event.widget is not self:
            return
        self.background_canvas.delete('panel')
        archive_theme.rounded_rectangle(
            self.background_canvas, 1, 1, max(2, event.width - 1),
            max(2, event.height - 1), min(16, event.height // 2),
            fill=self.surface, outline=self.border or self.surface, tags='panel')


def card(parent, *, border=BORDER, surface=SURFACE, padding=16):
    outer = RoundedPanel(parent, border=border, surface=surface, padding=padding)
    return outer, outer.inner


def label(parent, **kwargs):
    kwargs.setdefault('background', _background(parent))
    kwargs.setdefault('foreground', TEXT)
    kwargs.setdefault('font', BODY)
    kwargs.setdefault('anchor', 'w')
    return tk.Label(parent, **kwargs)


def apply_theme(root):
    style = archive_theme.apply_theme(root)
    root.configure(background=CANVAS)
    root.option_add('*Font', BODY)
    for key, value in {'background': SURFACE, 'foreground': TEXT,
                       'selectBackground': ACCENT_SOFT, 'selectForeground': TEXT,
                       'font': BODY}.items():
        root.option_add('*TCombobox*Listbox.' + key, value)
    style.configure('.', background=CANVAS, foreground=TEXT, font=BODY,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                    focuscolor=BORDER_STRONG, fieldbackground=SURFACE)
    for name in ('TButton', 'TMenubutton'):
        style.configure(name, background=SURFACE, foreground=TEXT, font=BODY,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                        padding=(13, 8), focuscolor=BORDER_STRONG)
        style.map(name, background=[('disabled', SURFACE_SOFT), ('pressed', ACCENT_SOFT),
                                   ('active', SURFACE_HOVER)],
                  foreground=[('disabled', TEXT_FAINT)],
                  bordercolor=[('disabled', BORDER), ('active', BORDER_STRONG)],
                  lightcolor=[('disabled', BORDER), ('active', BORDER_STRONG)],
                  darkcolor=[('disabled', BORDER), ('active', BORDER_STRONG)])
    style.configure('Accent.TButton', background=ACCENT, foreground=SURFACE,
                    font=BODY_BOLD, bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT,
                    padding=(16, 9), focuscolor=ACCENT_HOVER)
    for option in ('background', 'bordercolor', 'lightcolor', 'darkcolor'):
        style.map('Accent.TButton', **{option: [('disabled', '#BFDECE'),
                                               ('pressed', ACCENT_TEXT), ('active', ACCENT_HOVER)]})
    style.map('Accent.TButton', foreground=[('disabled', '#F7FBF9')])
    for name, background in (('Quiet.TButton', SURFACE), ('Tool.TButton', SURFACE),
                             ('Ghost.TButton', CANVAS), ('Day.TButton', SURFACE)):
        style.configure(name, background=background, foreground=TEXT_MUTED, font=BODY,
                        borderwidth=0, bordercolor=background, lightcolor=background,
                        darkcolor=background, relief='flat', padding=(9, 7), focuscolor=BORDER_STRONG)
        style.map(name, background=[('disabled', background), ('pressed', ACCENT_SOFT),
                                   ('active', SURFACE_HOVER)],
                  foreground=[('disabled', TEXT_FAINT), ('active', ACCENT_TEXT)],
                  bordercolor=[('disabled', background), ('active', BORDER_STRONG)],
                  lightcolor=[('disabled', background), ('active', background)],
                  darkcolor=[('disabled', background), ('active', background)])
    for name in ('TEntry', 'TCombobox', 'TSpinbox'):
        style.configure(name, fieldbackground=SURFACE, background=SURFACE, foreground=TEXT,
                        font=BODY, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                        padding=(10, 7), insertcolor=TEXT, arrowcolor=TEXT_MUTED)
        style.map(name, bordercolor=[('focus', ACCENT)], lightcolor=[('focus', ACCENT)],
                  darkcolor=[('focus', ACCENT)], foreground=[('disabled', TEXT_FAINT)],
                  fieldbackground=[('disabled', SURFACE_SOFT), ('readonly', SURFACE)])
    style.configure('Quiet.TEntry', font=CAPTION, foreground=TEXT_MUTED, padding=(2, 2),
                    bordercolor=SURFACE, lightcolor=SURFACE, darkcolor=SURFACE)
    style.configure('TCheckbutton', background=SURFACE, foreground=TEXT, font=BODY,
                    focuscolor=BORDER_STRONG, indicatorcolor=SURFACE, indicatorbackground=SURFACE)
    style.map('TCheckbutton', background=[('active', SURFACE)],
              indicatorcolor=[('selected', ACCENT), ('disabled', BORDER)],
              foreground=[('disabled', TEXT_FAINT)])
    style.configure('TNotebook', background=CANVAS, bordercolor=CANVAS,
                    lightcolor=CANVAS, darkcolor=CANVAS, borderwidth=0, tabmargins=(0, 0, 0, 12))
    style.configure('TNotebook.Tab', background=CANVAS, foreground=TEXT_MUTED,
                    font=BODY_BOLD, padding=(22, 11), focuscolor=BORDER_STRONG)
    style.map('TNotebook.Tab', background=[('selected', ACCENT_SOFT), ('active', SURFACE_HOVER)],
              foreground=[('selected', ACCENT_TEXT), ('active', TEXT)], expand=[('selected', (0, 0, 0, 0))])
    style.configure('Treeview', background=SURFACE, fieldbackground=SURFACE, foreground=TEXT,
                    font=BODY, rowheight=38, bordercolor=SURFACE, lightcolor=SURFACE, darkcolor=SURFACE)
    style.map('Treeview', background=[('selected', ACCENT_SOFT)], foreground=[('selected', ACCENT_TEXT)])
    style.configure('Treeview.Heading', background=SURFACE_SOFT, foreground=TEXT_MUTED,
                    font=CAPTION_BOLD, padding=(10, 10), lightcolor=SURFACE_SOFT, darkcolor=SURFACE_SOFT)
    style.map('Treeview.Heading', background=[('active', SURFACE_HOVER)], foreground=[('active', TEXT)])
    for direction in ('Horizontal', 'Vertical'):
        style.configure(direction + '.TScrollbar', troughcolor=SURFACE_SOFT,
                        background='#C8D7CE', bordercolor=SURFACE_SOFT,
                        lightcolor='#C8D7CE', darkcolor='#C8D7CE', width=9)
        style.configure(direction + '.TProgressbar', background=ACCENT, troughcolor=BORDER,
                        bordercolor=BORDER, lightcolor=ACCENT, darkcolor=ACCENT)
    style.configure('TSeparator', background=BORDER)
    style.configure('Surface.TFrame', background=SURFACE)
    for name, font, color, background in (
        ('Title.TLabel', DISPLAY, TEXT, CANVAS), ('Heading.TLabel', TITLE, TEXT, SURFACE),
        ('Section.TLabel', CAPTION_BOLD, TEXT_MUTED, SURFACE), ('Sub.TLabel', CAPTION, TEXT_MUTED, CANVAS),
        ('Hint.TLabel', CAPTION, TEXT_FAINT, SURFACE), ('Status.TLabel', BODY, TEXT, CANVAS),
        ('Completion.TLabel', BODY_BOLD, ACCENT_TEXT, INFO_SOFT),
        ('CompletionDetail.TLabel', CAPTION, TEXT_MUTED, INFO_SOFT)):
        style.configure(name, font=font, foreground=color, background=background)
    style.configure('Completion.TFrame', background=INFO_SOFT)
    style.configure('ReviewDone.TLabel', foreground=SUCCESS)
    style.configure('ReviewPending.TLabel', foreground=WARNING)
    style.configure('ReviewNone.TLabel', foreground=TEXT_MUTED)
    return style


def retheme_widgets(root):
    """Adapt legacy *instance* widgets without mutating archive module globals.

    The message reader is shared with group mode. It can retain its behavior
    while the private instance uses larger text and this palette.
    """
    names = ('CANVAS', 'SURFACE', 'SURFACE_SOFT', 'SURFACE_HOVER', 'BORDER', 'BORDER_STRONG',
             'TEXT', 'TEXT_MUTED', 'TEXT_FAINT', 'ACCENT', 'ACCENT_HOVER', 'ACCENT_SOFT',
             'ACCENT_TEXT', 'SUCCESS', 'SUCCESS_SOFT', 'WARNING', 'WARNING_SOFT',
             'DANGER', 'DANGER_SOFT', 'INFO', 'INFO_SOFT')
    colors = {getattr(archive_theme, name).lower(): globals()[name] for name in names}
    for widget in (root,):
        for option in ('background', 'foreground', 'highlightbackground', 'highlightcolor',
                       'selectbackground', 'selectforeground', 'insertbackground'):
            try:
                old = str(widget.cget(option)).lower()
                if old in colors:
                    widget.configure(**{option: colors[old]})
            except tk.TclError:
                pass
        try:
            font = tkfont.Font(root=widget, font=widget.cget('font'))
            size = font.actual('size')
            if 0 < size < 10:
                widget.configure(font=(FONT_FAMILY, 9 if size <= 8 else 11, font.actual('weight')))
        except tk.TclError:
            pass
        if isinstance(widget, archive_theme.BrandMark):
            widget.fill = ACCENT
            widget.redraw()
        elif isinstance(widget, archive_theme.StatusPill):
            widget.TONES = {key: tuple(colors.get(value.lower(), value) for value in pair)
                            for key, pair in archive_theme.StatusPill.TONES.items()}
            widget.redraw()
        elif isinstance(widget, archive_theme.SearchGlyph):
            widget.color = TEXT_FAINT
            widget.redraw()
    for child in root.winfo_children():
        retheme_widgets(child)
