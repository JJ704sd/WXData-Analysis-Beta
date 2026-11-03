"""Opt-in, isolated synthetic check of the packaged supplier desktop workflow."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog


def _widgets(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _widgets(child)


def _button(window, text):
    matches = [widget for widget in _widgets(window)
               if widget.winfo_class() in ('Button', 'TButton') and widget.cget('text') == text]
    if len(matches) != 1:
        raise AssertionError(f'Expected one {text!r} button; found {len(matches)}')
    return matches[0]


def run_check(task_path, receipt_path, data_dir, client, mode, app_class):
    """Drive real Tk widgets; substitute only the native folder and browser boundaries."""
    from offline_desktop import DesktopSession
    from offline_period_ui import PeriodDialog
    from offline_supplier_ui import SupplierSpeakerDialog

    task_path, receipt_path = Path(task_path).resolve(), Path(receipt_path).resolve()
    base, data_dir = task_path.parent, Path(data_dir).resolve()
    task = json.loads(task_path.read_text(encoding='utf-8'))
    reports = Path(task['reports']).resolve()
    if (not data_dir.is_relative_to(base) or not reports.is_relative_to(base)
            or not receipt_path.is_relative_to(base) or reports.is_relative_to(data_dir)
            or receipt_path.is_relative_to(data_dir) or receipt_path.is_relative_to(reports)
            or receipt_path.exists()):
        raise ValueError('Test profile, report and new receipt must stay in the isolated task folder.')
    if task['client'] != client or task['mode'] != mode:
        raise ValueError('Test task does not match the package client and mode.')
    reports.mkdir(parents=True, exist_ok=True)
    before = set(reports.iterdir())
    state = {'steps': [], 'errors': [], 'opened': [], 'pickerCalls': []}
    root = tk.Tk()
    root.withdraw()

    def unexpected(kind, title, detail, **_kwargs):
        raise AssertionError(f'Unexpected {kind}: {title}: {detail}')

    def choose_folder(**kwargs):
        state['pickerCalls'].append(kwargs.get('title'))
        return str(reports)

    def place_dialog(dialog, _parent):
        dialog.geometry('400x170-3000+20')
        dialog.deiconify()

    originals = (filedialog.askdirectory, messagebox.showerror, messagebox.showinfo,
                 simpledialog._place_window, os.startfile)
    filedialog.askdirectory = choose_folder
    messagebox.showerror = lambda title, detail, **kw: unexpected('error', title, detail, **kw)
    messagebox.showinfo = lambda title, detail, **kw: unexpected('info', title, detail, **kw)
    simpledialog._place_window = place_dialog
    os.startfile = lambda path: state['opened'].append(str(path))

    def error(kind, value, tb):
        state['errors'].append(''.join(traceback.format_exception(kind, value, tb)))
        for child in root.winfo_children():
            if isinstance(child, tk.Toplevel):
                child.destroy()

    def drive():
        try:
            for window in root.winfo_children():
                if isinstance(window, PeriodDialog) and 'period' not in state['steps']:
                    selected = [iid for iid in window.tree.get_children()
                                if window.tree.item(iid, 'values')[1] == task['chatId']]
                    if len(selected) != 1:
                        raise AssertionError('Expected exactly one matching conversation')
                    window.tree.selection_set(selected)
                    window.dates[0].set(task['start'])
                    window.dates[1].set(task['end'])
                    state['steps'].append('period')
                    _button(window, '下一步：填写供应商标识').invoke()
                elif isinstance(window, simpledialog._QueryString) and 'supplier' not in state['steps']:
                    window.entry.insert(0, task['supplierLabel'])
                    state['steps'].append('supplier')
                    _button(window, 'OK').invoke()
                elif isinstance(window, SupplierSpeakerDialog) and 'speaker' not in state['steps']:
                    selected = [str(i) for i, item in enumerate(window.choices)
                                if item['chatId'] == task['chatId'] and item['senderId'] == task['senderId']]
                    if len(selected) != 1:
                        raise AssertionError('Expected exactly one matching group speaker')
                    window.tree.selection_set(selected)
                    state['steps'].append('speaker')
                    _button(window, '确认所选发言人').invoke()
        except Exception:
            error(*sys.exc_info())
        if not state['errors'] and not state['opened']:
            root.after(50, drive)

    try:
        session = DesktopSession(data_dir, client=client, conversation_mode=mode)
        app = app_class(root, session)
        root.report_callback_exception = error
        root.withdraw()

        def pump_until(predicate, seconds):
            deadline = time.monotonic() + seconds
            while not predicate():
                root.update()
                if state['errors']:
                    raise AssertionError(state['errors'][0])
                if time.monotonic() > deadline:
                    raise TimeoutError(f"steps={state['steps']}, status={app.status.get()}")
                time.sleep(.01)

        pump_until(lambda: bool(app.groups) and not app.busy and not app.read_busy, 30)
        if session.review_status(task['chatId']) != '已导出':
            raise AssertionError('Selected synthetic conversation has not been formally exported')
        menu = root.nametowidget(app.scan_button['menu'])
        selected = [i for i in range(menu.index('end') + 1)
                    if menu.type(i) != 'separator' and menu.entrycget(i, 'label') == '供应商画像材料…']
        if len(selected) != 1:
            raise AssertionError('Supplier menu item missing or ambiguous')
        root.after(50, drive)
        root.after(20000, lambda: error(TimeoutError, TimeoutError('dialog timeout'), None)
                   if not state['opened'] else None)
        menu.invoke(selected[0])
        pump_until(lambda: bool(state['opened']), 30)
        expected_steps = ['period', 'supplier', 'speaker'] if mode == 'group' else ['period', 'supplier']
        if state['steps'] != expected_steps or len(state['pickerCalls']) != 1:
            raise AssertionError(f"Unexpected workflow: {state['steps']}")
        created = set(reports.iterdir()) - before
        if len(created) != 1:
            raise AssertionError('Expected one new report directory')
        folder = created.pop()
        if Path(state['opened'][0]) != folder / 'report.html':
            raise AssertionError('Browser target does not match the generated report')
        report = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
        rows = [json.loads(line) for line in (folder / 'evidence.jsonl').read_text(encoding='utf-8').splitlines()]
        matched = [row for row in rows if row['messageId'] == task['messageId']]
        if len(matched) != 1:
            raise AssertionError('Expected one quotation evidence row')
        row = matched[0]
        relation = 'selected_group_speaker' if mode == 'group' else 'selected_direct_peer'
        if row['senderId'] != task['senderId'] or row['supplierRelation'] != relation:
            raise AssertionError('Supplier attribution does not match the selected identity')
        pricing = next(item for item in report['dimensions'] if item['label'] == '报价与费用')
        if row['evidenceId'] not in pricing['evidenceIds'] or not row['sources'][0]['reviewId'] or not row['sources'][0]['line']:
            raise AssertionError('Quotation or review evidence is missing')
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        for item in manifest['files']:
            if hashlib.sha256((folder / item['path']).read_bytes()).hexdigest() != item['sha256']:
                raise AssertionError('Report manifest checksum mismatch')
        result = {'ok': True, 'client': client, 'mode': mode, 'steps': state['steps'],
                  'attributedMessages': report['overview']['attributedMessages'], 'selectedRelation': relation,
                  'reviewAndLine': True, 'outputManifestVerified': True,
                  'outputFiles': sorted(item['path'] for item in manifest['files']),
                  'nativeFolderPickerStubbed': True, 'browserLaunchStubbed': True}
    except Exception as exc:
        result = {'ok': False, 'client': client, 'mode': mode, 'steps': state['steps'],
                  'error': ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))}
    finally:
        filedialog.askdirectory, messagebox.showerror, messagebox.showinfo, simpledialog._place_window, os.startfile = originals
        root.destroy()
    with receipt_path.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    return 0 if result['ok'] else 2
