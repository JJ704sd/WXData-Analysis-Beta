"""Multi-group workflow on invented records; no real accounts or human conclusions."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from offline_chat import ArchiveError
from offline_desktop import DesktopSession
from offline_review import REVIEW_CHECKS
from test_database_adapter import create_metadata, create_shard, GROUP, MIXED
from test_wecom_archive import fixture


def multi_group_fixture(source, client):
    if client == 'wechat':
        source.mkdir()
        create_metadata(source)
        create_shard(source, rows=[{'text': '群一合成正文'}])
        create_shard(source, 'message/message_1.db', group=MIXED, rows=[{'text': '群二合成正文'}])
        with closing(sqlite3.connect(source / 'contact/contact.db')) as db, db:
            db.execute('update contact set remark=?', ('同名合成群',))
        return [GROUP, MIXED]
    fixture(source)
    second = 'R:synthetic-second'
    with closing(sqlite3.connect(source / 'session.db')) as db, db:
        db.execute('insert into conversation_table values(?,?,?,?)', (second, '企微合成群', '', 1700000000))
    with closing(sqlite3.connect(source / 'message.db')) as db, db:
        db.execute('insert into message_table select message_id+100,server_id+100,sequence+100,sender_id,?,content_type,send_time,flag,content,extra_content,local_extra_content from message_table', (second,))
    return ['R:synthetic', second]


UI_SETUP = '''
import json,sys,time,threading,tkinter as tk
from pathlib import Path
from local_chat_app import ChatApp
from offline_desktop import DesktopSession
base=Path(sys.argv[1]);client,first,second=sys.argv[2:]
root=tk.Tk();session=DesktopSession(base,client=client)
def pump(predicate):
    deadline=time.monotonic()+15
    while not predicate():
        assert time.monotonic()<deadline,'UI timed out'
        root.update();time.sleep(.01)
def widgets(parent):
    result=[]
    for child in parent.winfo_children():result.append(child);result.extend(widgets(child))
    return result
def button(parent,text):return next(w for w in widgets(parent) if w.winfo_class()=='TButton' and w.cget('text')==text)
'''


class MultiGroupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-multiselect-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def test_review_batches_are_independent_and_reload_invalidates_all(self):
        for client in ('wechat', 'wecom'):
            with self.subTest(client=client):
                source = self.base / client
                first, second = multi_group_fixture(source, client)
                session = DesktopSession(self.base / (client + '-profile'), client=client)
                session.open_source({'sourceRoot': str(source)})
                a = session.prepare_review(first, copy_media=False)
                b = session.prepare_review(second, copy_media=False)
                checks = {key: True for key in REVIEW_CHECKS}
                with self.assertRaises(ArchiveError):
                    session.confirm_export(a['review']['reviewId'], checks, chat=first)
                session.activate_review(first)
                self.assertEqual(session.pending_review['summary'], a['review'])
                session.prepare_review(second, start='2099-01-01', copy_media=False)
                with self.assertRaises(ArchiveError):
                    session.confirm_export(b['review']['reviewId'], checks, chat=second)
                session.activate_review(first)
                exported = session.confirm_export(a['review']['reviewId'], checks, '合成多选测试', chat=first)
                session.activate_review(second)
                self.assertEqual(session.pending_review['summary']['exportedRows'], 0)
                self.assertIsNone(session.last_export)
                session.activate_review(first)
                self.assertIsNone(session.pending_review)
                self.assertEqual(session.last_export, Path(exported['output']))
                session.open_source({'sourceRoot': str(source)})
                for chat in (first, second):
                    session.activate_review(chat)
                    self.assertIsNone(session.pending_review)
                    self.assertIsNone(session.last_export)

    def run_ui(self, client, code):
        source = self.base / client
        ids = multi_group_fixture(source, client)
        profile = self.base / (client + '-profile')
        profile.mkdir()
        (profile / 'settings.local.json').write_text(json.dumps({'version': 1, 'client': client,
            'source': {'sourceRoot': str(source)}}), encoding='utf8')
        project = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        env['PYTHONPATH'] = os.pathsep.join(str(project / p) for p in ('.runtime/offline-build-deps', 'web_mvp', 'scripts'))
        process = subprocess.run([sys.executable, '-c', UI_SETUP + code, str(profile), client, *ids],
                                 env=env, capture_output=True, timeout=40)
        self.assertEqual(process.returncode, 0, process.stderr.decode('utf8', errors='replace'))

    def test_windows_keep_picks_across_search_and_review_each_group(self):
        code = '''
receipt=base/'requests/named.json'
app=ChatApp(root,session,open_chat=first,open_result=receipt)
parsed_ready=app.parsed_ready
def finish_group(value):
    parsed_ready(value)
    assert app.batch_running and not app.busy
app.parsed_ready=finish_group
def review(chat,confirm):
    app.tree.selection_set(chat);app.selection();pump(lambda:not app.read_busy)
    app.review_button.invoke();root.update()
    dialog=next(w for w in root.winfo_children() if isinstance(w,tk.Toplevel))
    checks=[w for w in widgets(dialog) if w.winfo_class()=='TCheckbutton']
    assert len(checks)==4 and not any(root.getvar(w.cget('variable')) for w in checks)
    if not confirm:
        button(dialog,'返回检查').invoke();root.update();return
    for w in checks:w.invoke()
    [w for w in widgets(dialog) if w.winfo_class()=='Text'][-1].insert('end','合成自动化测试，不代表真实人工复核')
    button(dialog,'确认复核完成').invoke()
    pump(lambda:not app.busy)
try:
    pump(lambda:len(app.parsed_results)==1 and not app.busy and not app.read_busy and not app.batch_running)
    original=app.parsed_results[first]['review']['reviewId']
    app.search.set(first);root.update();app.tree.see(first);root.update()
    x,y,w,h=app.tree.bbox(first,'pick')
    app.tree.event_generate('<Button-1>',x=x+w//2,y=y+h//2);root.update()
    assert app.selected_chats=={first},'checkbox did not select group'
    app.tree.focus_force();app.tree.event_generate('<space>');root.update()
    assert not app.selected_chats,'space did not clear checkbox'
    app.tree.event_generate('<space>');root.update()
    assert app.selected_chats=={first}
    app.search.set(second);root.update();app.select_all_button.invoke()
    assert app.selected_chats=={first,second},'search lost hidden picks'
    assert '隐藏 1' in app.selection_text.get()
    app.copy_group_id()
    assert set(root.clipboard_get().splitlines())=={first,second}
    app.parse_button.invoke()
    pump(lambda:not app.busy and not app.read_busy and not app.batch_running)
    root.update()
    assert app.active_chat==first,'background queue interrupted the existing reading target'
    assert set(app.parsed_results)=={first,second}
    assert app.parsed_results[first]['review']['reviewId']==original,'existing preview was needlessly reparsed'
    assert all(app.parse_states[c]=='待复核' for c in (first,second))
    assert not (base/'exports').exists(),'parse auto-exported before confirmation'
    assert json.loads(receipt.read_text(encoding='utf8'))['chatId']==first
    assert json.loads(receipt.read_text(encoding='utf8'))['state']=='awaiting_review'
    app.search.set('');root.update();review(first,False)
    assert not (base/'exports').exists(),'cancel exported a group'
    first_review=app.parsed_results[first]['review']['reviewId']
    app.tree.selection_set(second);app.selection()
    assert session.pending_review['summary']['chat']['id']==second
    app.next_review_button.invoke();root.update()
    assert app.active_chat==first and session.pending_review['summary']['reviewId']==first_review
    review(second,True)
    assert not (base/'exports').exists(),'saving review unexpectedly exported'
    assert app.parse_states[second]=='已复核'
    assert app.export_button.instate(['disabled']),'unreviewed selected group was ignored'
    assert json.loads(receipt.read_text(encoding='utf8'))['state']=='awaiting_review','other group overwrote named receipt'
    app.tree.selection_set(first);app.selection()
    assert session.last_export is None and session.pending_review['summary']['reviewId']==first_review
    assert all(b.instate(['disabled']) for b in app.export_file_buttons.values()),'other group export leaked'
    review(first,True)
    assert not (base/'exports').exists(),'last review automatically exported'
    assert app.export_button.instate(['!disabled'])
    app.export_button.invoke();pump(lambda:not app.busy)
    assert len(list((base/'exports').iterdir()))==1,'one click did not create a single batch'
    result=json.loads(receipt.read_text(encoding='utf8'))
    assert result['state']=='completed' and result['chatId']==first
    for record in (base/'exports').rglob('review.json'):
        output=record.parent
        data=json.loads((output/'review.json').read_text(encoding='utf8'))
        rows=json.loads((output/'messages.json').read_text(encoding='utf8'))
        assert all(row['chatId']==data['chat']['id'] for row in rows)
        assert (output/'messages.jsonl').is_file() and (output/'messages.md').is_file()
    assert len(list((base/'exports').rglob('review.json')))==2
    opened=[]
    import local_chat_app
    local_chat_app.os.startfile=opened.append
    for chat in (first,second):
        app.show_chat(chat)
        expected=session.last_export
        for kind in ('JSON','JSONL','Markdown'):app.export_file_buttons[kind].invoke()
        app.folder_button.invoke()
        assert all(Path(p).parent==expected for p in opened[-4:-1]) and Path(opened[-1])==expected,'file links belong to another group'
        assert str(expected) in app.result_text.get(),'displayed path differs from current group buttons'
    assert app.export_button.instate(['disabled']),'completed groups could be exported twice'
    app.clear_selection_button.invoke()
    assert not app.selected_chats
finally:root.destroy()
'''
        for client in ('wechat', 'wecom'):
            with self.subTest(client=client):
                self.run_ui(client, code)

    def test_bulk_review_dialog_is_explicit_and_keeps_named_receipt_awaiting_export(self):
        code = '''
receipt=base/'requests/named.json'
app=ChatApp(root,session,open_chat=first,open_result=receipt)
def open_bulk(count):
    app.batch_review_button.invoke();root.update()
    dialog=next(w for w in root.winfo_children() if isinstance(w,tk.Toplevel))
    checks=[w for w in widgets(dialog) if w.winfo_class()=='TCheckbutton']
    assert len(checks)==4 and not any(root.tk.getboolean(root.getvar(w.cget('variable'))) for w in checks)
    assert button(dialog,f'确认本次 {count} 群复核').instate(['disabled'])
    return dialog
try:
    pump(lambda:len(app.parsed_results)==1 and not app.busy and not app.batch_running)
    app.selected_chats.update((first,second));app.selection_changed()
    assert app.batch_review_button.instate(['disabled']),'unparsed selected group can be approved'
    app.start_batch([second]);pump(lambda:not app.busy and not app.batch_running)
    app.search.set(second);root.update()
    assert '隐藏 1' in app.selection_text.get()
    dialog=open_bulk(2)
    report=next(w for w in widgets(dialog) if w.winfo_class()=='Text' and w.cget('state')=='disabled').get('1.0','end')
    assert first in report and second in report and '解析状态' in report and '本次复核 2 群' in report
    button(dialog,'全选复核项').invoke();button(dialog,'返回检查').invoke();root.update()
    assert all(session.review_status(c)=='待复核' for c in (first,second))
    dialog=open_bulk(2)
    button(dialog,'全选复核项').invoke()
    assert not (base/'exports').exists() and all(session.review_status(c)=='待复核' for c in (first,second))
    next(w for w in widgets(dialog) if w.winfo_class()=='Text' and w.cget('state')=='normal').insert('end','合成批量确认，不代表人工验收')
    button(dialog,'确认本次 2 群复核').invoke();pump(lambda:not app.busy)
    assert all(app.parse_states[c]=='已复核' for c in (first,second))
    assert not (base/'exports').exists() and app.export_button.instate(['!disabled'])
    result=json.loads(receipt.read_text(encoding='utf8'))
    assert result['chatId']==first and result['state']=='awaiting_review' and result['reviewConfirmed'] is True
    app.export_button.invoke();pump(lambda:not app.busy)
    records=list((base/'exports').rglob('review.json'))
    assert len(records)==2
    approvals=[json.loads(p.read_text(encoding='utf8')) for p in records]
    assert len({r['batchReview']['id'] for r in approvals})==1
    assert all(r['batchReview']['count']==2 and r['note']=='合成批量确认，不代表人工验收' for r in approvals)
    original={str(p):p.read_bytes() for p in records}
    app.start_batch([second]);pump(lambda:not app.busy and not app.batch_running)
    dialog=open_bulk(1)
    assert any(w.winfo_class()=='TLabel' and '已复核或已导出 1 群' in w.cget('text') for w in widgets(dialog))
    button(dialog,'全选复核项').invoke();button(dialog,'确认本次 1 群复核').invoke();pump(lambda:not app.busy)
    assert all(Path(p).read_bytes()==data for p,data in original.items()),'previous exported review was overwritten'
    assert json.loads(receipt.read_text(encoding='utf8'))['state']=='completed','another group overwrote named receipt'
finally:root.destroy()
'''
        self.run_ui('wecom',code)

    def test_restart_restores_picks_preview_and_review_without_exporting(self):
        code = '''
from offline_review import REVIEW_CHECKS
app=ChatApp(root,session)
try:
    pump(lambda:bool(app.groups) and not app.busy)
    app.select_visible();app.start_batch([first,second]);pump(lambda:not app.busy and not app.batch_running)
    session.activate_review(first)
    saved=session.approve_review(app.parsed_results[first]['review']['reviewId'],{key:True for key in REVIEW_CHECKS},'合成历史复核')
    app.review_ready(saved);app.show_chat(second)
    old_ids={chat:result['review']['reviewId'] for chat,result in app.parsed_results.items()}
    for token in root.tk.call('after','info'):root.after_cancel(token)
    app.close()
    root=tk.Tk();session=DesktopSession(base,client=client);app=ChatApp(root,session)
    pump(lambda:len(app.parsed_results)==2 and not app.busy)
    assert app.selected_chats=={first,second} and app.active_chat==second
    assert {chat:result['review']['reviewId'] for chat,result in app.parsed_results.items()}==old_ids
    assert app.parse_states[first]=='已复核' and app.parse_states[second]=='待复核'
    assert '上次副本' in app.completion_text.get() and '副本时间' in app.completion_detail.get()
    assert app.browser.rows and session.pending_review['summary']['chat']['id']==second,(len(app.browser.rows),session.pending_review['summary']['chat']['id'] if session.pending_review else None,app.status.get(),app.parsed_results[second]['view'].keys())
    assert app.export_button.instate(['disabled']) and not (base/'exports').exists()
    app.batch_review_button.invoke();root.update()
    dialog=next(w for w in root.winfo_children() if isinstance(w,tk.Toplevel))
    button(dialog,'全选复核项').invoke();button(dialog,'确认本次 1 群复核').invoke();pump(lambda:not app.busy)
    app.export_button.invoke();pump(lambda:not app.busy)
    assert len(list((base/'exports').rglob('review.json')))==2
finally:
    if root.winfo_exists():root.destroy()
'''
        for client in ('wechat','wecom'):
            with self.subTest(client=client):self.run_ui(client,code)

    def test_completed_groups_can_be_read_while_the_next_group_is_still_parsing(self):
        code = '''
root.withdraw()
app=ChatApp(root,session)
gate=threading.Event();started=threading.Event()
prepare=session.prepare_review
def delayed(chat,**options):
    if chat==second:
        started.set()
        assert gate.wait(12),'synthetic second group was not released'
    return prepare(chat,**options)
session.prepare_review=delayed
try:
    pump(lambda:bool(app.groups) and not app.busy)
    app.selected_chats.update((first,second))
    app.start_batch([first,second])
    pump(started.is_set)
    assert app.active_chat==first,'completed group was replaced by the next queued target'
    assert '已解析 1 个群' in app.completion_text.get() and '刚完成' in app.completion_detail.get()
    assert app.tree.set(first,'review')=='已解析·未复核' and app.tree.item(first,'tags')==('parsed',)
    assert app.tree.set(second,'review')=='解析中…'
    app.completed_groups_button.invoke();root.update()
    assert app.review_filter.get()=='已解析可查看' and set(app.tree.get_children())=={first}
    app.review_filter.set('全部群聊');app.render_groups()
    assert app.tree.instate(['!disabled']),'completed groups cannot be selected during parsing'
    original=app.parsed_results[first]['review']['reviewId']
    assert app.browser.rows and all(row['chatId']==first for row in app.browser.rows.values() if 'chatId' in row)
    app.message_search.set('没有匹配的合成词');app.message_search_button.invoke()
    pump(lambda:app.page_total==0)
    assert app.batch_running and not gate.is_set(),'reading waited for the entire queue'
    app.message_clear_button.invoke();pump(lambda:app.page_total>0)
    app.tree.selection_set(second);app.selection();root.update()
    assert app.active_chat==second and app.page_total==0
    app.tree.selection_set(first);app.selection();root.update()
    assert app.active_chat==first and app.page_total>0
    app.toggle_group(first)
    assert first not in app.selected_chats and app.batch_ids==[first,second]
    gate.set();pump(lambda:not app.batch_running and not app.busy and not app.read_busy)
    assert app.active_chat==first,'background completion interrupted the group being read'
    assert app.parsed_results[first]['review']['reviewId']==original
    assert session.pending_review['summary']['chat']['id']==first
    assert not (base/'exports').exists(),'reading exported without human review'
finally:
    gate.set()
    if app.busy:pump(lambda:not app.busy)
    root.destroy()
'''
        for client in ('wechat','wecom'):
            with self.subTest(client=client):
                self.run_ui(client,code)

    def test_select_all_toggle_and_menu_keep_hidden_choices_scoped(self):
        code = '''
root.withdraw();app=ChatApp(root,session)
try:
    pump(lambda:bool(app.groups) and not app.busy)
    all_ids={group['id'] for group in app.groups}
    app.search.set(first);root.update();app.toggle_group(first)
    assert app.selected_chats=={first}
    app.search.set(second);root.update();app.select_all_button.invoke()
    assert app.selected_chats=={first,second}
    assert '取消全选' in app.select_all_button.cget('text')
    app.select_all_button.invoke()
    assert app.selected_chats=={first},'cancel current results cleared hidden picks or did nothing'
    assert app.tree.heading('pick','text')=='☐'
    root.tk.call(app.tree.heading('pick','command'))
    assert app.selected_chats=={first,second} and app.tree.heading('pick','text')=='☑'
    app.selection_menu.invoke('反选当前结果')
    assert app.selected_chats=={first}
    app.selection_menu.invoke('全选全部群聊（含隐藏）')
    assert app.selected_chats==all_ids
    app.selection_menu.invoke('取消当前结果勾选')
    assert app.selected_chats==all_ids-{second}
    app.search.set('');root.update()
    assert app.tree.heading('pick','text')=='▣','partial selection is not visible'
    app.selection_menu.invoke('取消全部勾选')
    assert not app.selected_chats and app.tree.heading('pick','text')=='☐'
    assert not (base/'exports').exists()
finally:root.destroy()
'''
        for client in ('wechat','wecom'):
            with self.subTest(client=client):
                self.run_ui(client,code)

    def test_late_read_result_cannot_replace_another_group_or_a_newer_search(self):
        code = '''
root.withdraw();app=ChatApp(root,session)
gate=threading.Event();started=threading.Event()
try:
    pump(lambda:bool(app.groups) and not app.busy)
    app.start_batch([first,second]);pump(lambda:not app.busy and not app.read_busy and not app.batch_running)
    app.show_chat(first)
    view=session.views[first];page=view.page
    def delayed(*args,**options):
        if options.get('query')=='迟到的合成搜索':
            started.set();assert gate.wait(8),'synthetic cache read was not released'
        return page(*args,**options)
    view.page=delayed
    app.message_search.set('迟到的合成搜索');app.message_search_button.invoke();pump(started.is_set)
    app.show_chat(second);expected=app.parsed_results[second]['report']['businessRows']
    gate.set();pump(lambda:not app.read_busy)
    assert app.active_chat==second and app.page_total==expected and app.browser.rows,'stale read replaced another group'
    app.show_chat(first);gate.clear();started.clear()
    app.message_search.set('迟到的合成搜索');app.message_search_button.invoke();pump(started.is_set)
    app.message_clear_button.invoke();gate.set();pump(lambda:not app.read_busy)
    assert app.page_total>0 and not app.message_search.get(),'older search overwrote the latest request'
    assert not (base/'exports').exists()
finally:
    gate.set()
    if app.read_busy:pump(lambda:not app.read_busy)
    root.destroy()
'''
        self.run_ui('wechat',code)

    def test_review_shortcuts_and_visible_states_remain_scoped_to_current_group(self):
        code = '''
root.tk.call('tk','scaling',1.75)
app=ChatApp(root,session)
def scope(value):
    app.review_filter.set(value)
    app.review_filter_choice.event_generate('<<ComboboxSelected>>');root.update()
def dialog_for(chat):
    app.show_chat(chat);app.review_button.invoke();root.update()
    dialog=next(w for w in root.winfo_children() if isinstance(w,tk.Toplevel))
    checks=[w for w in widgets(dialog) if w.winfo_class()=='TCheckbutton']
    assert len(checks)==4
    return dialog,checks
def checked(checks):return sum(root.tk.getboolean(root.getvar(w.cget('variable'))) for w in checks)
def approve(chat):
    dialog,checks=dialog_for(chat)
    assert checked(checks)==0,'another group inherited review checks'
    assert button(dialog,'确认复核完成').instate(['disabled'])
    assert button(dialog,'清空勾选').instate(['disabled'])
    for caption in ('全选复核项','清空勾选','返回检查','确认复核完成'):
        w=button(dialog,caption)
        assert w.winfo_ismapped() and 0<=w.winfo_rooty()-dialog.winfo_rooty()<dialog.winfo_height()-w.winfo_height(),caption
    button(dialog,'全选复核项').invoke();root.update()
    assert checked(checks)==4 and button(dialog,'确认复核完成').instate(['!disabled'])
    assert button(dialog,'全选复核项').instate(['disabled'])
    assert any(w.winfo_class()=='TLabel' and w.cget('text')=='已勾选 4/4 · 仅当前群' for w in widgets(dialog))
    assert session.review_status(chat)=='待复核' and not (base/'exports').exists(),'select-all saved or exported'
    button(dialog,'清空勾选').invoke();root.update()
    assert checked(checks)==0 and button(dialog,'确认复核完成').instate(['disabled'])
    button(dialog,'全选复核项').invoke();checks[0].invoke();root.update()
    assert checked(checks)==3 and button(dialog,'确认复核完成').instate(['disabled'])
    button(dialog,'全选复核项').invoke()
    button(dialog,'确认复核完成').invoke();pump(lambda:not app.busy)
    assert not (base/'exports').exists(),'saving checklist exported automatically'
try:
    pump(lambda:len(app.groups)==2 and not app.busy)
    assert app.parse_button.instate(['disabled']) and '先勾选' in app.workflow_hint.get()
    app.search.set(first);root.update()
    assert app.search_clear_button.instate(['!disabled'])
    app.search_clear_button.invoke();root.update()
    assert not app.search.get() and not app.parsed_results
    app.show_chat(first);app.search.set(second);root.update()
    assert app.parse_button.cget('text')=='解析搜索结果' and app.parse_button.instate(['!disabled'])
    app.search.set('no-synthetic-match');root.update()
    assert app.parse_button.instate(['disabled']),'empty search could parse the previously viewed group'
    app.search_clear_button.invoke();root.update()
    app.select_visible();root.update()
    assert app.parse_button.cget('text')=='解析待处理 2 群' and '未解析' in app.workflow_hint.get()
    assert all(app.tree.set(c,'review')=='未解析' for c in (first,second))
    app.parse_button.invoke();pump(lambda:not app.busy and not app.batch_running)
    assert app.parse_button.cget('text')=='查看待复核群' and '未复核' in app.workflow_hint.get()
    assert app.current_group_id.get()==app.active_chat
    app.copy_current_id_button.invoke();assert root.clipboard_get()==app.active_chat
    for size in ('1200x760','1000x700'):
        root.geometry(size);root.update()
        x,y,w,h=app.tree.bbox(first,'review')
        assert w>0 and 0<=x<x+w<=app.tree.winfo_width(),'review column requires horizontal scrolling'
        assert app.current_review_badge.winfo_ismapped() and app.review_filter_choice.winfo_ismapped()
        assert app.completion_banner.winfo_ismapped() and app.completion_banner.winfo_height()<root.winfo_height()*.16
        assert app.browser.tree.winfo_height()>=100,'compact layout left too little space for messages'
        assert 0<=app.export_button.winfo_rooty()-root.winfo_rooty()<root.winfo_height()-app.export_button.winfo_height()
    assert app.current_review_state.get()=='未复核'
    approve(first)
    assert app.tree.set(first,'review')=='已复核' and app.tree.set(second,'review')=='已解析·未复核'
    assert app.current_review_state.get()=='已复核'
    assert '未复核 1' in app.review_summary.get() and '已复核 1' in app.review_summary.get()
    scope('所选已复核');assert set(app.tree.get_children())=={first}
    app.start_date.set('2099-01-01');root.update()
    assert not app.tree.get_children() and app.current_review_state.get()=='未复核'
    assert '范围已变' in app.workflow_hint.get()
    assert app.export_button.instate(['disabled'])
    app.start_date.set('');root.update()
    assert set(app.tree.get_children())=={first} and app.current_review_state.get()=='已复核'
    app.next_review_button.invoke();root.update()
    assert app.active_chat==second and app.review_filter.get()=='所选未复核'
    assert app.selected_chats=={first,second} and set(app.tree.get_children())=={second}
    dialog,checks=dialog_for(second)
    button(dialog,'全选复核项').invoke();button(dialog,'返回检查').invoke();root.update()
    assert session.review_status(second)=='待复核','cancel saved select-all'
    approve(second)
    assert not app.tree.get_children() and app.current_review_state.get()=='已复核'
    assert '全部复核' in app.workflow_hint.get()
    assert app.export_button.instate(['!disabled'])
    scope('所选已复核');assert set(app.tree.get_children())=={first,second}
    app.toggle_group(second);root.update()
    assert set(app.tree.get_children())=={first} and app.selected_chats=={first}
    assert app.current_review_state.get()=='已复核' and '未勾选' in app.current_review_hint.get()
    scope('全部群聊');app.toggle_group(second);root.update()
    dialog,checks=dialog_for(first)
    assert checked(checks)==4,'saved review was not restored'
    button(dialog,'返回检查').invoke();root.update()
    app.start_parse(first);pump(lambda:not app.busy and not app.batch_running)
    assert app.tree.set(first,'review')=='已解析·未复核' and app.tree.set(second,'review')=='已复核'
    assert app.export_button.instate(['disabled'])
    approve(first);app.export_button.invoke();pump(lambda:not app.busy)
    assert all(app.tree.set(c,'review')=='已复核' for c in (first,second))
    assert '已复核 2（含已导出 2）' in app.review_summary.get()
    assert app.current_review_state.get()=='已复核' and '已导出' in app.current_review_hint.get()
    assert '所选群已导出' in app.workflow_hint.get()
    app.work('reload',lambda:session.open_source(session.settings['source']),app.ready)
    pump(lambda:not app.busy)
    assert all(app.tree.set(c,'review')=='未解析' for c in (first,second))
    assert not app.selected_chats and app.current_review_state.get()=='未选群'
finally:root.destroy()
'''
        for client in ('wechat', 'wecom'):
            with self.subTest(client=client):
                self.run_ui(client, code)

    def test_default_parse_preserves_reviews_and_reparse_is_explicit(self):
        code = '''
from offline_review import REVIEW_CHECKS
app=ChatApp(root,session)
try:
    pump(lambda:len(app.groups)==2 and not app.busy)
    app.start_parse(first);pump(lambda:not app.busy and not app.batch_running)
    original=app.parsed_results[first]['review']['reviewId']
    approval=session.approve_review(original,{k:True for k in REVIEW_CHECKS},'合成复核，不代表真实人工验收',chat=first)
    app.review_ready(approval);root.update()
    app.select_visible();root.update()
    assert app.parse_button.cget('text')=='解析待处理 1 群'
    app.parse_button.invoke();pump(lambda:not app.busy and not app.batch_running)
    assert app.batch_ids==[second] and app.batch_reused==1
    assert app.parsed_results[first]['review']['reviewId']==original and session.review_status(first)=='已复核'
    second_id=app.parsed_results[second]['review']['reviewId']
    app.parse_button.invoke();root.update()
    assert app.active_chat==second and not app.busy and not app.batch_running
    assert app.parsed_results[second]['review']['reviewId']==second_id
    app.search.set(first);app.search_entry.focus_force();root.update()
    app.search_entry.event_generate('<Return>');root.update()
    assert app.active_chat==first and app.selected_chats=={first,second} and not app.busy
    assert session.review_status(first)=='已复核' and app.parsed_results[second]['review']['reviewId']==second_id
    app.search_clear_button.invoke();app.clear_selection();app.toggle_group(first);root.update()
    app.start_date.set('2099-01-01');root.update()
    app.parse_button.invoke();pump(lambda:not app.busy and not app.batch_running)
    assert app.parsed_results[first]['review']['reviewId']!=original and session.review_status(first)=='待复核'
    assert app.parsed_results[second]['review']['reviewId']==second_id,'range change reparsed an unselected group'
    app.show_chat(second);app.select_visible();root.update()
    before={c:app.parsed_results[c]['review']['reviewId'] for c in (first,second)}
    assert app.reparse_button.instate(['!disabled'])
    app.reparse_button.invoke();pump(lambda:not app.busy and not app.batch_running)
    assert all(app.parsed_results[c]['review']['reviewId']!=before[c] for c in (first,second))
    assert all(session.review_status(c)=='待复核' for c in (first,second))
    assert not (base/'exports').exists(),'a navigation or reparse action exported before human confirmation'
finally:root.destroy()
'''
        for client in ('wechat', 'wecom'):
            with self.subTest(client=client):
                self.run_ui(client, code)

    def test_queue_continues_after_error_and_stops_after_current_group(self):
        code = '''
app=ChatApp(root,session)
original=session.prepare_review
gate=threading.Event()
try:
    pump(lambda:bool(app.groups) and not app.busy)
    def fail(chat,**kwargs):
        if chat==first:raise PermissionError('private-error-do-not-display')
        return original(chat,**kwargs)
    session.prepare_review=fail
    app.start_batch([first,second,second])
    pump(lambda:not app.batch_running and not app.busy)
    assert app.parse_states[first]=='失败' and app.parse_states[second]=='待复核'
    assert len(app.parsed_results)==1
    assert 'private-error' not in app.parse_errors[first]
    def delayed(chat,**kwargs):
        assert gate.wait(10),'test failed to release current group'
        return original(chat,**kwargs)
    session.prepare_review=delayed
    app.start_batch([first,second])
    assert app.busy and app.cancel_batch_button.instate(['!disabled'])
    app.cancel_batch_button.invoke();gate.set()
    pump(lambda:not app.batch_running and not app.busy)
    assert app.parse_states[first]=='待复核' and app.parse_states[second]=='待复核'
    assert len(app.parsed_results)==2,'stopping lost earlier preview'
    assert not (base/'exports').exists()
    app.clear_selection();app.search.set('');root.update();app.select_visible()
    assert app.selected_chats
    app.work('reload',lambda:session.open_source(session.settings['source']),app.ready)
    pump(lambda:not app.busy)
    assert not app.selected_chats and not app.parsed_results and not app.parse_states
    assert session.pending_review is None
finally:gate.set();root.destroy()
'''
        for client in ('wechat', 'wecom'):
            with self.subTest(client=client):
                self.run_ui(client, code)


if __name__ == '__main__':
    unittest.main()
