"""Small redacted lifecycle log for diagnosing portable Windows startup."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import traceback
from collections import deque, Counter
import hashlib
import platform
import re
import struct
import sys
import threading
import time
import uuid
import zipfile

_path=None
_lock=threading.RLock()
_memory=deque(maxlen=4000)
_context=threading.local()
_salt=uuid.uuid4().hex
_write_failed=False
NUMBERS={'pid','position','total','processed','files','bytes','processedFiles','filesChecked',
         'recoveredReferences','completed','elapsedMs','errno','winerror'}
FLAGS={'frozen','automaticClose','diagnosticAutoExit','busy'}
TOKENS={'event','eventId','session','client','exceptionType','operation','stage','targetToken',
        'faultId','sqliteErrorName','reason'}
STAGES={'starting','parsing','finalizing','media_search','indexing','saving','supplement_search',
        'attachment_text','validating','exporting','saved','failed','analysis','completed'}


def _clean(value):
    """Allowlist old and new records; never bundle arbitrary legacy fields."""
    result={}
    for key,item in value.items():
        if key in NUMBERS and isinstance(item,int):result[key]=item
        elif key in FLAGS and isinstance(item,bool):result[key]=item
        elif key in TOKENS and isinstance(item,str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',item):result[key]=item
        elif key=='time' and isinstance(item,str) and re.fullmatch(r'[0-9T:+.Z-]{1,40}',item):result[key]=item
        elif key=='frames' and isinstance(item,list):
            frames=[]
            for frame in item[-32:]:
                if not isinstance(frame,dict):continue
                name=str(frame.get('file','')).replace('\\','/').rsplit('/',1)[-1]
                function=frame.get('function','')
                if re.fullmatch(r'[A-Za-z0-9_.<>-]{1,100}',name) and isinstance(frame.get('line'),int):
                    frames.append({'file':name,'line':frame['line'],
                        'function':function if isinstance(function,str) and re.fullmatch(r'[A-Za-z0-9_.<>-]{1,100}',function) else '<redacted>'})
            result['frames']=frames
    return result


def begin(directory,client):
    global _path,_write_failed
    if _path is not None:return _path
    _memory.clear();_write_failed=False
    name='startup-'+datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d-%H%M%S')+'-'+str(os.getpid())+'.jsonl'
    # Never create logs in a caller-supplied account/data directory before its
    # source binding has been validated. Keep diagnostics in a separate store.
    diagnostic_root=Path(os.environ.get('LOCALAPPDATA') or tempfile.gettempdir())/'LocalChatArchiveDiagnostics'/client
    for folder in (diagnostic_root,Path(tempfile.gettempdir())/'LocalChatArchive-logs'):
        try:
            folder.mkdir(parents=True,exist_ok=True);_path=folder/name
            with _path.open('x',encoding='utf8'):pass
            break
        except OSError:_path=None
    record('process_started',client=client,frozen=bool(getattr(__import__('sys'),'frozen',False)))
    return _path


def path():return _path


def record(event,**fields):
    global _write_failed
    value=_clean({'time':datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='milliseconds'),
           'pid':os.getpid(),'event':event,**fields,'eventId':uuid.uuid4().hex,'session':_salt})
    with _lock:
        _memory.append(value)
        if _path is None:
            _write_failed=True
            return
        try:
            with _path.open('a',encoding='utf8') as stream:stream.write(json.dumps(value,ensure_ascii=False)+'\n')
        except OSError:_write_failed=True


def error(event,kind,tb):
    # Never serialize exception messages, locals, command lines, proxy URLs or keys.
    frames=[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name} for f in traceback.extract_tb(tb)]
    record(event,exceptionType=kind.__name__,frames=frames)


def operation(name,*,target=None,position=None,total=None):
    _context.value={'operation':name,'stage':'starting'}
    if target is not None:
        _context.value['targetToken']=hashlib.sha256((_salt+str(target)).encode()).hexdigest()[:12]
    for key,value in (('position',position),('total',total)):
        if value is not None:_context.value[key]=value
    _context.started=_context.last=time.monotonic()
    record('operation_started',**_context.value)


def progress(event):
    context=getattr(_context,'value',None)
    if context is None:return
    if event.get('stage') in STAGES:context['stage']=event['stage']
    if event.get('chatId') is not None:
        context['targetToken']=hashlib.sha256((_salt+str(event['chatId'])).encode()).hexdigest()[:12]
    for key in NUMBERS-{'pid','errno','winerror','elapsedMs'}:
        if isinstance(event.get(key),int):context[key]=event[key]
    if time.monotonic()-_context.last>=10:
        record('operation_progress',**context)
        _context.last=time.monotonic()


def completed():
    value=dict(getattr(_context,'value',{}))
    value['elapsedMs']=int((time.monotonic()-getattr(_context,'started',time.monotonic()))*1000)
    record('operation_completed',**value)
    _context.value=None


def failure(error,event='operation_failed',tb=None):
    value=dict(getattr(_context,'value',None) or {})
    value.update(exceptionType=type(error).__name__,faultId=uuid.uuid4().hex[:10])
    value['frames']=[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name}
                     for f in traceback.extract_tb(tb or error.__traceback__)]
    for key in ('errno','winerror'):
        if isinstance(getattr(error,key,None),int):value[key]=getattr(error,key)
    if getattr(error,'sqlite_errorname',None):value['sqliteErrorName']=error.sqlite_errorname
    record(event,**value)
    return value['faultId']


def export_bundle(destination,summary):
    """Snapshot recent redacted events; no database, settings or media reads."""
    coverage={'maxLogFiles':5,'maxBytesPerLog':2*1024*1024,'filesRead':0,
              'truncatedFiles':0,'malformedLines':0,'unreadableFiles':0}
    events=[]
    with _lock:
        if _path is not None:
            try:
                paths=sorted(_path.parent.glob('startup-*.jsonl'),key=lambda p:p.stat().st_mtime,reverse=True)[:5]
            except OSError:paths=[];coverage['unreadableFiles']+=1
            for source in reversed(paths):
                try:
                    with source.open('rb') as stream:
                        size=os.fstat(stream.fileno()).st_size
                        if size>coverage['maxBytesPerLog']:
                            stream.seek(size-coverage['maxBytesPerLog']);stream.readline()
                            coverage['truncatedFiles']+=1
                        content=stream.read(coverage['maxBytesPerLog'])
                    coverage['filesRead']+=1
                    for line in content.splitlines():
                        try:
                            row=json.loads(line)
                            if not isinstance(row,dict):raise ValueError('not an event')
                            events.append(_clean(row))
                        except (ValueError,UnicodeError):coverage['malformedLines']+=1
                except OSError:coverage['unreadableFiles']+=1
        events.extend(_memory)
        coverage['diskWriteFailed']=_write_failed
        coverage['inMemoryEvents']=len(_memory)
    unique=[];seen=set()
    for row in events:
        identity=row.get('eventId')
        if identity and identity in seen:continue
        if identity:seen.add(identity)
        unique.append(row)
    safe_summary={k:v for k,v in summary.items() if
        (k in {'selected','parsed','failed','queued','batchTotal','batchFailed','batchSucceeded','dataFreeBytes','exportFreeBytes'} and isinstance(v,int)) or
        (k in {'busy','batchRunning','copyMedia','ocrImages','ocrEnabled','sourceLoaded'} and isinstance(v,bool)) or
        (k in {'client','release','conversationMode'} and isinstance(v,str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,30}',v))}
    info={'schemaVersion':1,'exportedAt':datetime.now().astimezone().isoformat(),
          'environment':{'system':platform.system(),'release':platform.release(),'version':platform.version(),
                         'python':platform.python_version(),'bits':struct.calcsize('P')*8,'frozen':bool(getattr(sys,'frozen',False))},
          'current':safe_summary,'coverage':coverage,'events':len(unique)}
    try:
        with Path(sys.executable).open('rb') as stream:info['executableSha256']=hashlib.file_digest(stream,'sha256').hexdigest()
    except OSError:info['executableSha256']=None
    errors=[row for row in unique if row.get('exceptionType')]
    counts=Counter((row.get('exceptionType'),row.get('stage','unknown')) for row in errors)
    lines=['# 群聊解析诊断日志','',f"版本：{safe_summary.get('release','unknown')} · 客户端：{safe_summary.get('client','unknown')}",
        f'共 {len(unique)} 条事件、{len(errors)} 条异常（覆盖最近日志，可能跨运行）。',
        '本包只含诊断数据，不含聊天正文、原件、账号配置或密钥。目标以当前进程匿名标记表示。',
        '旧版本未记录的后台异常不能追溯；解析中导出的日志只覆盖导出时刻。',
        f"覆盖：最多5个日志；截断 {coverage['truncatedFiles']} 个，损坏行 {coverage['malformedLines']}，不可读 {coverage['unreadableFiles']} 个。",'',
        '| 异常类型 | 失败阶段 | 次数 |','| --- | --- | ---: |']
    lines.extend(f'| {kind} | {stage} | {count} |' for (kind,stage),count in counts.items())
    lines+=['','查看 events.jsonl 中 exceptionType、frames、stage、position、faultId；详情不得仅凭通用提示判断为密钥错误。','']
    destination=Path(destination)
    fd,name=tempfile.mkstemp(prefix='.diagnostics-',suffix='.zip',dir=destination.parent);os.close(fd)
    temporary=Path(name)
    try:
        with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr('summary.md','\n'.join(lines))
            bundle.writestr('diagnostics.json',json.dumps(info,ensure_ascii=False,indent=2))
            bundle.writestr('events.jsonl',''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in unique))
        with zipfile.ZipFile(temporary) as bundle:
            if bundle.testzip() is not None:raise OSError('diagnostics_zip_verification_failed')
        os.replace(temporary,destination)
    finally:temporary.unlink(missing_ok=True)
    return {'events':len(unique),'errors':len(errors),'coverage':coverage}
