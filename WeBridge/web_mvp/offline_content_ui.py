"""Account-scoped content recovery options for the native workbench."""
import hashlib
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk


def open_content_settings(app):
    if app.busy or app.read_busy or app.batch_running or app.closing:
        return
    if not app.session.archive:
        app.status.set('请先选择并加载账号，再设置内容补齐。')
        return
    from offline_desktop import safe_issue
    config = app.session.archive.media_config
    passwords = dict(config.get('_documentPasswords', {}))
    dialog = tk.Toplevel(app.root)
    dialog.title('内容补齐 · 原件、OCR 与密码')
    dialog.transient(app.root)
    dialog.minsize(700, 490)
    frame = ttk.Frame(dialog, padding=24)
    frame.pack(fill='both', expand=True)
    frame.columnconfigure(0, weight=1)
    ttk.Label(frame, text='让缺失材料回到对应消息', font=('Microsoft YaHei UI', 15, 'bold')).grid(row=0, column=0, columnspan=2, sticky='w')
    ttk.Label(frame, text='设置仅用于当前账号；保存后重新解析生效，原有导出保留。', style='Sub.TLabel').grid(row=1, column=0, columnspan=2, sticky='w', pady=(6, 20))
    root = tk.StringVar(value=config.get('supplementRoot', ''))
    password_file = tk.StringVar(value=config.get('documentPasswordFile', ''))
    def pick(variable, folder):
        value = filedialog.askdirectory(parent=dialog, title='选择客户端另存或备份原件的文件夹') if folder else filedialog.askopenfilename(parent=dialog, title='选择密码JSON文件', filetypes=[('JSON', '*.json')])
        if value: variable.set(value)
    ttk.Label(frame, text='补充原件目录').grid(row=2, column=0, sticky='w')
    ttk.Entry(frame, textvariable=root).grid(row=3, column=0, sticky='ew', pady=6)
    ttk.Button(frame, text='选择文件夹', command=lambda: pick(root, True)).grid(row=3, column=1, padx=(10, 0))
    ttk.Label(frame, text='先在客户端下载或另存原图、附件，或选择备份目录。按原件摘要匹配，不按同名猜测。\n以后新放入的文件会在下一次解析时核查；未下载且无备份的原件仍列入补齐清单。', wraplength=650, style='Sub.TLabel').grid(row=4, column=0, columnspan=2, sticky='w', pady=(0, 16))
    pdf = tk.BooleanVar(value=config.get('ocrEnabled', False))
    images = tk.BooleanVar(value=config.get('ocrImages', False))
    ttk.Checkbutton(frame, text='文档 OCR：自动识别图片附件、PDF 扫描内容及 Office 内嵌图片', variable=pdf).grid(row=5, column=0, columnspan=2, sticky='w')
    ttk.Checkbutton(frame, text='聊天图片：识别原图或可用预览中的文字（预览会单独标注）', variable=images).grid(row=6, column=0, columnspan=2, sticky='w', pady=(6, 0))
    ttk.Label(frame, text='识别结果保留页码、文档位置和文字行，进入阅读、搜索与分析材料。\n多页 TIFF 自动逐页识别；数字、单位和表格关系须对照原件复核。', wraplength=650, style='Sub.TLabel').grid(row=7, column=0, columnspan=2, sticky='w', pady=(6, 18))
    count = tk.StringVar()
    def show_count(): count.set(f'当前会话已提供 {len(passwords)} 个文件的密码；退出程序后清除。')
    show_count()
    def register():
        filename = filedialog.askopenfilename(parent=dialog, title='选择需要打开密码的原附件（可选导出目录中的文件）', filetypes=[('支持的加密文档', '*.pdf *.docx *.xlsx *.pptx')])
        if not filename: return
        from offline_documents import MAX_FILE
        try:
            path = Path(filename)
            if path.stat().st_size > MAX_FILE:
                messagebox.showinfo('正文提取大小限制', '当前正文提取支持20MB以内的单个文件；原件仍可完整导出。', parent=dialog)
                return
            with path.open('rb') as stream: digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        except OSError:
            messagebox.showerror('读取失败', '无法读取所选文件。', parent=dialog)
            return
        password = simpledialog.askstring('提供已知打开密码', '文件：' + path.name + '\n密码只用于这个文件，不保存到配置或导出。', show='●', parent=dialog)
        if password is not None:
            if password: passwords[digest] = password
            else: passwords.pop(digest, None)
            show_count()
    controls = ttk.Frame(frame)
    controls.grid(row=8, column=0, columnspan=2, sticky='w')
    ttk.Button(controls, text='为附件提供密码…', command=register).pack(side='left')
    ttk.Button(controls, text='清除会话密码', command=lambda: (passwords.clear(), show_count())).pack(side='left', padx=8)
    ttk.Label(frame, textvariable=count, style='Sub.TLabel').grid(row=9, column=0, columnspan=2, sticky='w', pady=(6, 12))
    ttk.Label(frame, text='可选：批量密码 JSON 文件（只记住路径，请勿随导出分享）').grid(row=10, column=0, columnspan=2, sticky='w')
    ttk.Entry(frame, textvariable=password_file).grid(row=11, column=0, sticky='ew', pady=6)
    ttk.Button(frame, text='选择文件', command=lambda: pick(password_file, False)).grid(row=11, column=1, padx=(10, 0))
    def save():
        try:
            app.session.set_content_options({'supplementRoot': root.get().strip(),
                'documentPasswordFile': password_file.get().strip(), 'ocrEnabled': pdf.get(), 'ocrImages': images.get()}, passwords)
        except Exception as error:
            messagebox.showerror('设置未保存', safe_issue(error), parent=dialog)
            return
        app.status.set('补齐设置已保存。请重新解析所选群；补齐后复核并导出。')
        dialog.destroy()
    ttk.Button(frame, text='保存补齐设置', style='Accent.TButton', command=save).grid(row=12, column=1, sticky='e', pady=(16, 0))
    dialog.grab_set()
