"""Evidence-based content coverage, separate from review and publication state."""
from collections import Counter


def summarize(reports):
    result = Counter()
    result['groups'] = len(reports)
    for report in reports:
        result['rows'] += report.get('exportedRows', 0)
        result['business'] += report.get('businessRows', 0)
        result['truncatedGroups'] += bool(report.get('truncatedByLimit'))
        result['partialMessages'] += sum(n for key, n in report.get('parseStatuses', {}).items() if key not in ('ok', 'plain_system'))
        media = report.get('mediaSummary')
        if media is None: result['unknownMedia'] += 1
        for category in ('images', 'attachments', 'videos', 'voice'):
            item = (media or {}).get(category, {})
            total, original = item.get('total', 0), item.get('original' if category == 'images' else 'ready', 0)
            result['mediaTotal'] += total; result['original'] += original
            result[category+'Total'] += total; result[category+'Original'] += original
            result['preview'] += item.get('thumbnail', 0)+item.get('localPreview', 0)+item.get('previewOnly', 0)
            result['missing'] += item.get('missing', 0)
            result['notChecked'] += item.get('notLoaded', 0)
        document = report.get('attachmentTextSummary')
        if document is None: result['unknownDocuments'] += 1
        result['documentTotal'] += (document or {}).get('total', 0)
        states = (document or {}).get('extractionStatuses', {})
        result['withText'] += sum(states.get(key, 0) for key in ('extracted', 'partial', 'ocr_extracted'))
        result['partialText'] += states.get('partial', 0)
        result['ocrText'] += (document or {}).get('ocrTextReferences', states.get('ocr_extracted', 0))
        result['previewOCR'] += (document or {}).get('previewOCRReferences',0)
        result['passwordNeeded'] += states.get('encrypted', 0)+states.get('password_rejected', 0)
        quality = report.get('contentQuality')
        if quality is None: result['unknownQuality'] += 1
        result['gaps'] += (quality or {}).get('counts', {}).get('occurrences', 0)
        quotes = (quality or {}).get('quoteLinks', {})
        result['linked'] += quotes.get('linked', 0); result['unlinked'] += quotes.get('unlinked', 0)
        result['supplemented'] += report.get('supplementalMediaSearch', {}).get('recoveredReferences', 0)
    return result


def compact_values(value):
    if not value['groups']: return ('—', '—', '—', '—')
    def short(number): return f'{number/10000:.1f}万' if number >= 10000 else f'{number:,}'
    return (f"{value['rows']:,}",
            '待核查' if value['unknownMedia'] else f"{short(value['original'])} / {short(value['mediaTotal'])}",
            '待核查' if value['unknownDocuments'] else f"{short(value['withText'])} / {short(value['documentTotal'])}",
            '待核查' if value['unknownQuality'] else f"{value['gaps']:,}")


def open_overview(app):
    import os
    from pathlib import Path
    import tkinter as tk
    from tkinter import ttk, messagebox
    from offline_theme import CANVAS, SURFACE, TEXT, TEXT_MUTED, BORDER, ACCENT, WARNING
    if app.closing: return
    dialog = tk.Toplevel(app.root)
    dialog.title('解析与导出完整性概览')
    dialog.transient(app.root)
    dialog.geometry('880x640')
    dialog.minsize(720, 530)
    dialog.configure(background=CANVAS)
    outer = tk.Frame(dialog, background=CANVAS)
    outer.pack(fill='both', expand=True, padx=22, pady=18)
    outer.columnconfigure(0, weight=1); outer.rowconfigure(3, weight=1)
    heading = tk.Frame(outer, background=CANVAS); heading.grid(row=0, column=0, sticky='ew')
    tk.Label(heading, text='解析与导出完整性', font=('Microsoft YaHei UI', 17, 'bold'), background=CANVAS, foreground=TEXT).pack(side='left')
    scope = tk.StringVar(value='当前群' if app.active_chat else '所选群')
    select = ttk.Combobox(heading, textvariable=scope, values=('当前群', '所选群'), state='readonly', width=10)
    select.pack(side='right')
    summary = tk.StringVar()
    label = tk.Label(outer, textvariable=summary, justify='left', anchor='w', background=CANVAS, foreground=TEXT_MUTED)
    label.grid(row=1, column=0, sticky='ew', pady=(10, 12))
    label.bind('<Configure>', lambda event: label.configure(wraplength=max(200, event.width)))
    metrics = tk.Frame(outer, background=CANVAS); metrics.grid(row=2, column=0, sticky='ew', pady=(0, 14))
    values=[]
    for index, title in enumerate(('源记录 / 条', '原件取得 / 引用', '有正文 / 文档引用', '需补齐或核对 / 处')):
        metrics.columnconfigure(index, weight=1, uniform='metric')
        card=tk.Frame(metrics, background=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        card.grid(row=0,column=index,sticky='ew',padx=(0,8 if index<3 else 0))
        tk.Label(card,text=title,background=SURFACE,foreground=TEXT_MUTED,font=('Microsoft YaHei UI',9),anchor='w').pack(fill='x',padx=12,pady=(10,4))
        variable=tk.StringVar(value='—');values.append(variable)
        tk.Label(card,textvariable=variable,background=SURFACE,foreground=WARNING if index==3 else ACCENT,font=('Microsoft YaHei UI',15,'bold'),anchor='w').pack(fill='x',padx=12,pady=(0,10))
    table_area=ttk.Frame(outer);table_area.grid(row=3,column=0,sticky='nsew')
    table_area.columnconfigure(0,weight=1);table_area.rowconfigure(0,weight=1)
    table=ttk.Treeview(table_area,columns=('item','coverage','meaning'),show='headings',selectmode='browse')
    for key,title,width in (('item','检查项',110),('coverage','当前结果',260),('meaning','如何用于分析',360)):
        table.heading(key,text=title,anchor='w');table.column(key,width=width,minwidth=80,stretch=key=='meaning')
    table.grid(row=0,column=0,sticky='nsew')
    scrollbar=ttk.Scrollbar(table_area,orient='vertical',command=table.yview);scrollbar.grid(row=0,column=1,sticky='ns');table.configure(yscrollcommand=scrollbar.set)
    footer=tk.StringVar()
    note=tk.Label(outer,textvariable=footer,background=CANVAS,foreground=TEXT_MUTED,anchor='w',justify='left')
    note.grid(row=4,column=0,sticky='ew',pady=(12,8));note.bind('<Configure>',lambda e:note.configure(wraplength=max(200,e.width)))
    actions=ttk.Frame(outer);actions.grid(row=5,column=0,sticky='ew')
    def open_gap(filename='补齐清单.csv'):
        chat=app.active_chat
        pending=app.session._reviews.get(chat) or app.session._published_reviews.get(chat)
        root=app.session._exports.get(chat) or (pending or {}).get('root')
        path=Path(root)/filename if root else None
        if path and path.is_file():os.startfile(path)
        else:messagebox.showinfo('暂无清单','先选择当前群并重新解析，旧版结果可能没有补齐清单。',parent=dialog)
    gap=ttk.Button(actions,text='打开当前群补齐清单',command=open_gap);gap.pack(side='left')
    originals=ttk.Button(actions,text='按文件补原件',command=lambda:open_gap('原件补齐优先清单.csv'));originals.pack(side='left',padx=8)
    ttk.Button(actions,text='关闭',command=dialog.destroy).pack(side='right')
    def refresh(*_):
        chats=([app.active_chat] if app.active_chat else []) if scope.get()=='当前群' else sorted(app.selected_chats)
        reports=[app.parsed_results[c]['report'] for c in chats if c in app.parsed_results]
        value=summarize(reports)
        for variable,text in zip(values,compact_values(value)):variable.set(text)
        reviewed=sum(app.session.review_status(c) in ('已复核','已导出') for c in chats)
        exported=sum(app.session.review_status(c)=='已导出' for c in chats)
        names=reports[0].get('chat',{}).get('name','') if len(chats)==1 and reports else scope.get()
        summary.set(f'{names}\n范围内 {len(chats)} 群 · 已解析 {len(reports)} · 未解析 {len(chats)-len(reports)} · 已复核 {reviewed} · 已正式导出 {exported}')
        rows=[('记录覆盖',f"{value['rows']:,} 条；限制截断 {value['truncatedGroups']} 群",'仅代表所选日期与本地副本，不证明客户端全部历史'),
              ('图片原件',f"{value['imagesOriginal']:,} / {value['imagesTotal']:,}",'预览和缩略图不计为原图'),
              ('附件原件',f"{value['attachmentsOriginal']:,} / {value['attachmentsTotal']:,}",'取得原件与成功读取正文分别统计'),
              ('媒体缺口',f"未取得 {value['missing']:,}；仅预览 {value['preview']:,}",f"另有 {value['notChecked']:,} 处未核查，不能当成不存在"),
              ('正文范围',f"有文字 {value['withText']:,}；其中部分 {value['partialText']:,}",'分母含附件和启用OCR的图片引用；有文字不等于全文'),
              ('OCR / 密码',f"OCR有文字 {value['ocrText']:,}；待密码 {value['passwordNeeded']:,}",'OCR需核对数字和表格；未知密码不会被猜测'),
              ('预览图正文',f"{value['previewOCR']:,} 处已识别",'只利用现有预览文字，原图仍列为未取得'),
              ('引用来源',f"已关联 {value['linked']:,}；未关联 {value['unlinked']:,}",'来源关联覆盖不是回复率'),
              ('消息适配',f"部分适配 {value['partialMessages']:,} 条",'保留原始字段，不把解码状态当业务准确率'),
              ('补充目录',f"本次补齐 {value['supplemented']:,} 处",'来自用户所选目录，经原件摘要校验'),
              ('正式导出',f"{exported} / {len(chats)} 群",'人工复核后保存；已保存不等于全部内容已还原')]
        table.delete(*table.get_children())
        for row in rows:table.insert('', 'end', values=row)
        unknown=value['unknownMedia']+value['unknownDocuments']+value['unknownQuality']
        footer.set('图片、附件、正文、缺口按引用出现次数统计，同一文件可能多次出现；不同缺口不可相加为缺失文件数。'
                   + (' 部分旧版统计未提供，缺失项应重新解析核查。' if unknown else ''))
        gap.configure(state='normal' if app.active_chat in app.parsed_results else 'disabled')
        originals.configure(state='normal' if app.active_chat in app.parsed_results else 'disabled')
    def show_detail(_):
        selected=table.selection()
        if selected:
            item=table.item(selected[0],'values')
            footer.set(' · '.join(map(str,item))+'\n媒体、正文按引用出现次数统计；导出完成不代表内容全部恢复。')
    table.bind('<<TreeviewSelect>>',show_detail)
    select.bind('<<ComboboxSelected>>',refresh)
    refresh()
    return dialog
