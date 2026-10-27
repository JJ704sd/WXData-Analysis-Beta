#!/usr/bin/env python3
"""JSON stdin/stdout bridge, executed only inside the isolated Linux client."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET

from db_probe import context, query
from wal_snapshot_probe import decode
from native_object_roundtrip import integer, blob
from native_send_once import fields, signed
from rich_content import parse_content

HERE = Path(__file__).resolve().parent
EXPECTED = 'e40116597803a5e0d9fc4a2b727df4b8f7a3dd6de1c336140ea373be2b6626e9'
GROUP_PATTERN = re.compile(r'^[A-Za-z0-9_-]+@(?:im\.)?chatroom$')


def auth():
    token = Path('/data/auth-token').read_text().strip()
    req = urllib.request.Request('http://127.0.0.1:6174/api/status/auth', headers={'Authorization':'Bearer '+token})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=15) as response:
        return json.load(response)


def current(expected=None):
    state = auth()
    if state.get('status') != 'logged_in':
        raise ValueError('微信尚未登录，请先扫码或在手机确认登录。')
    account = state.get('loggedInUser')
    if not account or (expected and expected != account):
        raise ValueError('登录账号发生变化，请刷新页面后重新选择群聊。')
    root, keys = context()
    if root.parent.name != account:
        raise ValueError('登录状态与数据库账号不一致，请重新连接。')
    self_id = account.rsplit('_',1)[0]
    return account, self_id, root, keys


def copied_query(path, key, sql):
    wal = Path(str(path)+'-wal')
    for _ in range(3):
        before = (path.stat().st_size,path.stat().st_mtime_ns,
                  (wal.stat().st_size,wal.stat().st_mtime_ns) if wal.exists() else None)
        body = path.read_bytes()
        journal = wal.read_bytes() if wal.exists() else None
        after = (path.stat().st_size,path.stat().st_mtime_ns,
                 (wal.stat().st_size,wal.stat().st_mtime_ns) if wal.exists() else None)
        if before == after and (journal is None or journal == wal.read_bytes()):
            with tempfile.TemporaryDirectory(prefix='wechat-mvp-read-') as directory:
                dest = Path(directory)/path.name
                dest.write_bytes(body)
                if journal is not None: Path(str(dest)+'-wal').write_bytes(journal)
                return query(dest,key,sql)
        time.sleep(.05)
    raise ValueError('消息库正在更新，请稍后刷新。')


def quote(value):
    return "'"+value.replace("'","''")+"'"


def contacts(root,keys,ids):
    if not ids: return {}
    values=','.join(quote(x) for x in ids)
    rows=copied_query(root/'contact/contact.db',keys['contact.db'],
        f'SELECT username,nick_name,remark FROM contact WHERE username IN ({values});')
    return {r['username']:(r.get('remark') or r.get('nick_name') or r['username']) for r in rows}


def groups(root,keys):
    rows=copied_query(root/'contact/contact.db',keys['contact.db'],
        "SELECT c.username,c.nick_name,c.remark,r.owner FROM contact c "
        "JOIN chat_room r ON r.username=c.username "
        "WHERE (c.username LIKE '%@chatroom' OR c.username LIKE '%@im.chatroom') "
        "AND COALESCE(c.delete_flag,0)=0 ORDER BY c.nick_name;")
    return [{'id':r['username'],'name':r.get('remark') or r.get('nick_name') or r['username']} for r in rows]


def members(root,keys,group):
    if not GROUP_PATTERN.fullmatch(group): raise ValueError('无效的群聊标识。')
    rows=copied_query(root/'contact/contact.db',keys['contact.db'],
        f'SELECT hex(ext_buffer) AS data FROM chat_room WHERE username={quote(group)};')
    if not rows: raise ValueError('当前账号没有这个群聊的成员信息，请先在微信里打开该群。')
    items=[]
    for raw in fields(bytes.fromhex(rows[0]['data'] or '')).get(1,[]):
        row=fields(raw)
        username=row.get(1,[b''])[0].decode('utf-8')
        if username:
            display=row.get(2,[b''])[0].decode('utf-8')
            items.append({'id':username,'name':display,'kind':'企业微信' if username.endswith('@openim') else '个人微信'})
    names=contacts(root,keys,[x['id'] for x in items])
    for row in items: row['name']=row['name'] or names.get(row['id']) or row['id']
    return items


def messages(root,keys,group,self_id):
    if not GROUP_PATTERN.fullmatch(group): raise ValueError('无效的群聊标识。')
    table='Msg_'+hashlib.md5(group.encode()).hexdigest()
    result=[]
    for source in sorted((root/'message').glob('message_*.db')):
        if not source.stem[8:].isdigit() or source.name not in keys: continue
        # An absent group table in older shards is expected. Check first using
        # immutable metadata; actual messages always use temporary DB+WAL copies.
        exists=copied_query(source,keys[source.name],f"SELECT name FROM sqlite_master WHERE name='{table}';")
        if not exists: continue
        rows=copied_query(source,keys[source.name],f'''SELECT m.local_id,m.server_id,m.local_type,m.create_time,
            hex(m.message_content) AS content_hex,hex(m.source) AS source_hex,n.user_name AS sender
            FROM "{table}" m LEFT JOIN Name2Id n ON m.real_sender_id=n.rowid
            ORDER BY m.local_id DESC LIMIT 150;''')
        for row in rows:
            try:
                text=decode(row['content_hex']); xml=decode(row['source_hex'])
            except (ValueError,UnicodeError): continue
            sender=row.get('sender') or ''
            if ':\n' in text:
                prefix,body=text.split(':\n',1)
                if prefix==sender: text=body
            at=[]
            try:
                node=ET.fromstring(xml).find('atuserlist') if xml else None
                at=[x.strip() for x in (node.text or '').split(',')] if node is not None else []
            except ET.ParseError: pass
            kind=int(row['local_type']) & 0xffffffff
            presentation=parse_content(text,int(row['local_type']))
            sid=str(row['server_id'])
            result.append({'id':sid if sid!='0' else f'{source.name}:{row["local_id"]}',
                'serverId':sid,'senderId':sender,'timestamp':int(row['create_time']),
                'localId':row['local_id'],'dbName':source.name,**presentation,
                'type':kind,'isSelf':sender==self_id,'mentionSelf':self_id in at,
                'mentionEveryone':'notify@all' in at,'source':'wechat'})
    names=contacts(root,keys,list({x['senderId'] for x in result if x['senderId']}))
    unique={}
    for row in result:
        row['senderName']=names.get(row['senderId'],row['senderId'] or '未知成员')
        unique[row['id']]=row
    return sorted(unique.values(),key=lambda r:(r['timestamp'],r['id']))[-200:]


def send(params, account, self_id, root, keys):
    group=params['groupId']; text=params['text'].strip(); ids=params.get('mentionIds',[])
    if not text or len(text)>2000: raise ValueError('消息需为1到2000字。')
    people={m['id']:m for m in members(root,keys,group)}
    if len(ids)>20 or len(ids)!=len(set(ids)) or any(x not in people or x==self_id for x in ids):
        raise ValueError('请从当前群成员中选择有效的 @ 对象。')
    text += ''.join('  @'+people[x]['name'] for x in ids)
    if len(text.encode('utf-8'))>6000: raise ValueError('消息含@后过长，请缩短内容。')
    source='<msgsource>'
    if ids:
        from xml.sax.saxutils import escape
        source+='<atuserlist>'+escape(','.join(ids))+'</atuserlist>'
    source+='<alnode><fr>1</fr></alnode></msgsource>'
    client_id=secrets.randbelow(0x7ffffffe)+1
    child=blob(1,blob(1,group))+blob(2,text)+integer(3,1)+integer(4,int(time.time()))+integer(5,client_id)+blob(6,source)
    payload=integer(1,1)+blob(2,child)
    binary=Path('/opt/wechat/wechat')
    if hashlib.sha256(binary.read_bytes()).hexdigest()!=EXPECTED:
        raise ValueError('客户端版本不匹配，已停止原生发送。')
    pids=subprocess.check_output(['pgrep','-x','wechat'],text=True).split()
    if len(pids)!=1 or Path('/proc/'+pids[0]+'/exe').resolve()!=binary:
        raise ValueError('无法确认独立微信进程。')
    # native_object_roundtrip already applies the Python 3.10 typing shim.
    import frida
    manifest=json.loads((HERE/'bridge-manifest.json').read_text())
    lib=HERE/manifest['library']
    if hashlib.sha256(lib.read_bytes()).hexdigest()!=manifest['librarySha256']:
        raise ValueError('回调库校验失败。')
    js=(HERE/'native_send_once.js').read_text().replace('/tmp/libwechat_lab_completion.so',str(lib)).replace("getModuleByName('libwechat_lab_completion.so')","getModuleByName('"+lib.name+"')")
    result={'status':'unknown','clientMsgId':client_id,'text':text,'mentionIds':ids}
    with open('/tmp/wechat-mvp-send.lock','w') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise ValueError('另一条消息正在发送，请稍后再试。')
        current(account)
        session=frida.attach(int(pids[0]));script=None
        try:
            script=session.create_script(js);script.load()
            result['submissionAttempted']=True
            result['task']=script.exports_sync.sendonce(list(payload))
            end=time.monotonic()+50
            while time.monotonic()<end:
                state=script.exports_sync.status()
                if state.get('invoked',0)>0 and state.get('liveCallbacks')==0: break
                time.sleep(.1)
            raw=state.pop('responseBytes',[]);result['completion']=state
            if raw:
                top=fields(raw);base=fields(top.get(1,[b''])[0]);rows=top.get(3,[])
                base_ret=signed(base[1][0]) if 1 in base else None
                result['baseRet']=base_ret
                if len(rows)==1:
                    ack=fields(rows[0]);to=fields(ack.get(2,[b''])[0]).get(1,[b''])[0].decode()
                    ret=signed(ack[1][0]) if 1 in ack else None
                    server_id=ack.get(8,[0])[0]
                    if base_ret==0 and ret==0 and to==group and ack.get(4,[None])[0]==client_id and server_id:
                        result.update(status='sent',serverId=str(server_id))
                    elif base_ret not in (0,None) or ret not in (0,None):
                        result.update(status='failed',error=f'微信服务器拒绝：{base_ret}/{ret}')
            if result['status']=='unknown':result['error']='未能确认服务器最终结果，请先查看群聊，不要直接重复发送。'
        except Exception:
            result['error']='原生调用未能返回明确结果，请查看群聊确认；系统不会自动重发。'
        finally:
            if script:
                try:script.unload()
                except Exception:pass
            session.detach()
    return result


def main():
    params=json.load(sys.stdin);action=params['action']
    if action=='auth': return auth()
    account,self_id,root,keys=current(params.get('account'))
    if action=='groups':
        return {'account':account,'selfId':self_id,'name':contacts(root,keys,[self_id]).get(self_id,self_id),'groups':groups(root,keys)}
    if action=='members':return {'members':members(root,keys,params['groupId'])}
    if action=='group_info':
        group=params['groupId'];people=members(root,keys,group)
        rows=copied_query(root/'contact/contact.db',keys['contact.db'],f'SELECT owner FROM chat_room WHERE username={quote(group)};')
        owner=rows[0].get('owner') if rows else ''
        return {'id':group,'name':contacts(root,keys,[group]).get(group,group),
                'ownerId':owner,'ownerName':contacts(root,keys,[owner]).get(owner,owner) if owner else '未同步',
                'memberCount':len(people),'members':people}
    if action=='messages':return {'messages':messages(root,keys,params['groupId'],self_id)}
    if action=='media':
        from media_container import resolve_media
        group=params['groupId'];dbname=params['dbName'];localid=int(params['localId']);sid=str(params['serverId'])
        if not GROUP_PATTERN.fullmatch(group) or not re.fullmatch(r'message_[0-9]+\.db',dbname) or not sid.isdigit():
            raise ValueError('无效的附件消息标识。')
        table='Msg_'+hashlib.md5(group.encode()).hexdigest()
        rows=copied_query(root/'message'/dbname,keys[dbname],f'SELECT local_type,create_time,hex(message_content) AS data FROM "{table}" WHERE local_id={localid} AND server_id={int(sid)} LIMIT 1;')
        if not rows:raise ValueError('附件消息已变化或不在本地缓存中。')
        source=rows[0];raw=decode(source['data']);row={**parse_content(raw,source['local_type']),
            'raw':raw,'localId':localid,'serverId':sid,'timestamp':int(source['create_time'])}
        if row['kind']=='revoke':raise ValueError('这条消息已撤回。')
        return resolve_media(root,keys,group,row,params.get('part',''))
    if action=='send':return send(params,account,self_id,root,keys)
    raise ValueError('未知操作。')


if __name__=='__main__':
    try: print(json.dumps({'ok':True,'data':main()},ensure_ascii=False))
    except Exception as exc:
        # Expected user errors are safe; never forward Frida/SQLCipher internals.
        message=str(exc) if isinstance(exc,ValueError) else '客户端暂不可用，请检查登录状态后重试。'
        print(json.dumps({'ok':False,'error':message},ensure_ascii=False))
        sys.exit(1)
