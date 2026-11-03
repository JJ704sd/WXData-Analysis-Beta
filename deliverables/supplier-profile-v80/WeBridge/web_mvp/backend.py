"""Local durable state, daily scheduler and explicit @-reply policy."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import threading
import time
import urllib.request
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT=Path(__file__).resolve().parents[1]
try:
    TZ=ZoneInfo('Asia/Shanghai')
except ZoneInfoNotFoundError:
    # Windows does not ship the IANA database. Scheduling current daily jobs
    # needs China's present UTC+8 offset, not historical timezone transitions.
    TZ=timezone(timedelta(hours=8),name='Asia/Shanghai')


class BridgeError(Exception): pass
class OutcomeUnknown(Exception): pass


class Adapter:
    mode='live'

    def call(self,action,**params):
        try:
            run=subprocess.run(['docker','exec','-i','wechat-lab-linux','python3','/opt/wechat-lab-mvp/container_bridge.py'],
                input=json.dumps({'action':action,**params}),text=True,encoding='utf-8',capture_output=True,timeout=85 if action in ('send','media') else 35)
            result=json.loads(run.stdout.strip().splitlines()[-1])
        except (subprocess.TimeoutExpired,ValueError,IndexError,OSError):
            if action=='send':raise OutcomeUnknown('发送结果尚未确认，请查看群聊；系统不会自动重发。')
            raise BridgeError('无法连接微信实验容器，请检查 Docker 与客户端。')
        if not result.get('ok'):raise BridgeError(result.get('error','微信接口暂不可用。'))
        return result['data']

    def auth(self):
        try:
            token=(ROOT/'.secrets/linux-token').read_text().strip()
            req=urllib.request.Request('http://127.0.0.1:16174/api/status/auth',headers={'Authorization':'Bearer '+token})
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=8) as response:
                return json.load(response)
        except Exception: return {'status':'unavailable','error':'无法连接实验容器，请检查 Docker。'}

    def logout(self):
        token=(ROOT/'.secrets/linux-token').read_text().strip()
        req=urllib.request.Request('http://127.0.0.1:16174/api/status/logout',data=b'{}',
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=90) as response:
                result=json.load(response)
        except Exception:raise BridgeError('退出连接中断，请刷新检查实际登录状态。')
        if not result.get('success'):raise BridgeError('客户端未完成退出，请检查 Linux 微信后重试。')
        return result


class Store:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.lock=threading.RLock()
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS messages(account TEXT,group_id TEXT,id TEXT,payload TEXT,
                PRIMARY KEY(account,group_id,id));
            CREATE TABLE IF NOT EXISTS replies(account TEXT,group_id TEXT,enabled INTEGER DEFAULT 0,
                text TEXT DEFAULT '',mention_sender INTEGER DEFAULT 1,cooldown INTEGER DEFAULT 30,
                activated_at REAL DEFAULT 0,last_queued_at REAL DEFAULT 0,PRIMARY KEY(account,group_id));
            CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY,account TEXT,group_id TEXT,text TEXT,
                mentions TEXT,origin TEXT,status TEXT,created_at REAL,result TEXT DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,account TEXT,group_id TEXT,text TEXT,
                mentions TEXT,clock TEXT,enabled INTEGER,last_date TEXT DEFAULT '',last_result TEXT DEFAULT '',created_at REAL);
            CREATE TABLE IF NOT EXISTS subscriptions(account TEXT PRIMARY KEY,group_ids TEXT,updated_at REAL);
            ''')
            db.execute("UPDATE outbox SET status='unknown',result=? WHERE status='sending'",
                (json.dumps({'error':'服务在发送期间重启，结果未知；不会自动重发。'}),))
            # Unsent queued actions belong to the previous running session.
            db.execute("UPDATE outbox SET status='cancelled',result=? WHERE status='queued'",
                (json.dumps({'error':'服务已重启，待发送操作已取消。'}),))
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=15);db.row_factory=sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def rows(self,sql,args=()):
        with self.lock,self.connect() as db:return [dict(r) for r in db.execute(sql,args)]

    def ingest(self,account,group,messages):
        fresh=[]
        with self.lock,self.connect() as db:
            for message in messages:
                cursor=db.execute('INSERT OR IGNORE INTO messages VALUES(?,?,?,?)',
                    (account,group,message['id'],json.dumps(message,ensure_ascii=False)))
                if cursor.rowcount:fresh.append(message)
                else:db.execute('UPDATE messages SET payload=? WHERE account=? AND group_id=? AND id=?',
                    (json.dumps(message,ensure_ascii=False),account,group,message['id']))
                target=message.get('revokedServerId')
                if message.get('kind')=='revoke' and target:
                    previous=db.execute('SELECT payload FROM messages WHERE account=? AND group_id=? AND id=?',(account,group,target)).fetchone()
                    if previous:
                        prior=json.loads(previous[0])
                        for field in ('media','record','quote'):prior.pop(field,None)
                        prior.update(kind='revoke',text=message['text'],mentionSelf=False)
                        db.execute('UPDATE messages SET payload=? WHERE account=? AND group_id=? AND id=?',
                            (json.dumps(prior,ensure_ascii=False),account,group,target))
        return fresh

    def watched(self,account):
        rows=self.rows('SELECT group_ids FROM subscriptions WHERE account=?',(account,))
        return json.loads(rows[0]['group_ids']) if rows else None

    def set_watched(self,account,groups):
        with self.lock,self.connect() as db:
            old=db.execute('SELECT group_ids FROM subscriptions WHERE account=?',(account,)).fetchone()
            previous=json.loads(old[0]) if old else []
            for group in set(previous)-set(groups):
                db.execute('UPDATE replies SET enabled=0 WHERE account=? AND group_id=?',(account,group))
                db.execute("UPDATE outbox SET status='cancelled' WHERE account=? AND group_id=? AND origin='reply' AND status='queued'",(account,group))
            db.execute('INSERT OR REPLACE INTO subscriptions VALUES(?,?,?)',(account,json.dumps(groups),time.time()))

    def pause_account(self,account):
        with self.lock,self.connect() as db:
            db.execute('UPDATE replies SET enabled=0 WHERE account=?',(account,))
            db.execute("UPDATE outbox SET status='cancelled' WHERE account=? AND status='queued'",(account,))

    def reply(self,account,group):
        rows=self.rows('SELECT * FROM replies WHERE account=? AND group_id=?',(account,group))
        return rows[0] if rows else {'account':account,'group_id':group,'enabled':0,'text':'','mention_sender':1,'cooldown':30,'activated_at':0,'last_queued_at':0}

    def set_reply(self,account,group,enabled,text,cooldown=30,now=None):
        now=time.time() if now is None else now
        if enabled and not text.strip():raise ValueError('开启前请填写固定回复话术。')
        if len(text)>2000 or not 5<=cooldown<=3600:raise ValueError('话术最多2000字，回复间隔为5到3600秒。')
        old=self.reply(account,group)
        activated=now if enabled and not old['enabled'] else old['activated_at']
        with self.lock,self.connect() as db:
            db.execute('INSERT OR REPLACE INTO replies VALUES(?,?,?,?,?,?,?,?)',
                (account,group,int(enabled),text.strip(),1,cooldown,activated,old['last_queued_at']))
            if not enabled:
                db.execute("UPDATE outbox SET status='cancelled' WHERE account=? AND group_id=? AND origin='reply' AND status='queued'",(account,group))
        return self.reply(account,group)

    @staticmethod
    def _insert(db,id,account,group,text,mentions,origin,now):
        return db.execute('INSERT OR IGNORE INTO outbox(id,account,group_id,text,mentions,origin,status,created_at) VALUES(?,?,?,?,?,?,?,?)',
            (id,account,group,text,json.dumps(mentions),origin,'queued',now)).rowcount

    def enqueue(self,account,group,text,mentions,origin='manual',id=None,now=None):
        if not text.strip() or len(text)>2000:raise ValueError('消息需为1到2000字。')
        if not isinstance(mentions,list) or len(mentions)>20 or any(not isinstance(x,str) for x in mentions):raise ValueError('无效的@成员。')
        id=id or str(uuid.uuid4());now=time.time() if now is None else now
        if not isinstance(id,str) or not 8<=len(id)<=160:raise ValueError('无效的请求编号。')
        with self.lock,self.connect() as db:
            existing=db.execute('SELECT * FROM outbox WHERE id=?',(id,)).fetchone()
            if existing and (existing['account']!=account or existing['group_id']!=group or existing['text']!=text or json.loads(existing['mentions'])!=mentions):
                raise ValueError('请求编号与内容冲突，请刷新页面。')
            self._insert(db,id,account,group,text,mentions,origin,now)
        return self.rows('SELECT * FROM outbox WHERE id=?',(id,))[0]

    def auto_reply(self,account,group,message,now=None):
        now=time.time() if now is None else now
        if message.get('isSelf') or not message.get('mentionSelf') or not message.get('senderId'):return False
        if message.get('kind') in ('revoke','system'):return False
        if message.get('serverId') in (None,'0'):return False
        with self.lock,self.connect() as db:
            rule=db.execute('SELECT * FROM replies WHERE account=? AND group_id=?',(account,group)).fetchone()
            if not rule or not rule['enabled'] or message['timestamp']<int(rule['activated_at']):return False
            if now-rule['last_queued_at']<rule['cooldown']:return False
            id='reply:'+hashlib.sha256((account+'|'+group+'|'+message['id']).encode()).hexdigest()
            inserted=self._insert(db,id,account,group,rule['text'],[message['senderId']],'reply',now)
            if inserted:db.execute('UPDATE replies SET last_queued_at=? WHERE account=? AND group_id=?',(now,account,group))
            return bool(inserted)

    def add_job(self,account,group,text,mentions,clock,now=None):
        now=time.time() if now is None else now
        try:hour,minute=map(int,clock.split(':'))
        except (ValueError,AttributeError):raise ValueError('请选择每天的发送时间。')
        if not 0<=hour<24 or not 0<=minute<60:raise ValueError('发送时间无效。')
        if not text.strip() or len(text)>2000:raise ValueError('消息需为1到2000字。')
        current=datetime.fromtimestamp(now,TZ);due=current.replace(hour=hour,minute=minute,second=0,microsecond=0)
        last=current.date().isoformat() if due.timestamp()<=now else ''
        id=str(uuid.uuid4())
        with self.lock,self.connect() as db:
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)',
                (id,account,group,text.strip(),json.dumps(mentions),f'{hour:02}:{minute:02}',1,last,'',now))
        return id

    def schedule_due(self,account,now=None):
        now=time.time() if now is None else now;today=datetime.fromtimestamp(now,TZ)
        date=today.date().isoformat();queued=[]
        with self.lock,self.connect() as db:
            for row in db.execute('SELECT * FROM jobs WHERE enabled=1 AND account=?',(account,)).fetchall():
                if row['last_date']==date:continue
                hour,minute=map(int,row['clock'].split(':'))
                due=today.replace(hour=hour,minute=minute,second=0,microsecond=0).timestamp()
                if now<due:continue
                if now-due>120:
                    db.execute('UPDATE jobs SET last_date=?,last_result=? WHERE id=?',
                        (date,'已错过本次时间，未补发',row['id']));continue
                id='daily:'+row['id']+':'+date
                if self._insert(db,id,account,row['group_id'],row['text'],json.loads(row['mentions']),'schedule',now):queued.append(id)
                db.execute('UPDATE jobs SET last_date=?,last_result=? WHERE id=?',(date,'已加入发送队列',row['id']))
        return queued

    def claim(self):
        with self.lock,self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT * FROM outbox WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if not row:return None
            db.execute("UPDATE outbox SET status='sending' WHERE id=? AND status='queued'",(row['id'],))
            return dict(row)

    def finish(self,id,result):
        status=result.get('status','unknown')
        if status not in ('sent','failed','unknown','cancelled'):status='unknown'
        with self.lock,self.connect() as db:
            db.execute('UPDATE outbox SET status=?,result=? WHERE id=?',(status,json.dumps(result,ensure_ascii=False),id))
            if id.startswith('daily:'):
                job_id=id.split(':')[1]
                label={'sent':'发送成功','failed':'发送失败','unknown':'结果未知','cancelled':'已取消'}[status]
                db.execute('UPDATE jobs SET last_result=? WHERE id=?',(label,job_id))

    def cancel_job(self,account,id):
        with self.lock,self.connect() as db:
            if not db.execute('SELECT 1 FROM jobs WHERE id=? AND account=?',(id,account)).fetchone():raise ValueError('任务不存在。')
            db.execute('UPDATE jobs SET enabled=0 WHERE id=?',(id,))
            db.execute("UPDATE outbox SET status='cancelled' WHERE account=? AND id LIKE ? AND status='queued'",(account,'daily:'+id+':%'))

    def outbox(self,account,group=None):
        sql='SELECT * FROM outbox WHERE account=?';args=[account]
        if group:sql+=' AND group_id=?';args.append(group)
        sql+=' ORDER BY created_at DESC LIMIT 100'
        rows=self.rows(sql,args)
        for row in rows:row['mentions']=json.loads(row['mentions']);row['result']=json.loads(row['result'])
        return rows

    def jobs(self,account):
        rows=self.rows('SELECT * FROM jobs WHERE account=? ORDER BY created_at DESC',(account,))
        today=datetime.now(TZ)
        for row in rows:
            row['mentions']=json.loads(row['mentions']);h,m=map(int,row['clock'].split(':'))
            next_run=today.replace(hour=h,minute=m,second=0,microsecond=0)
            if row['last_date']==today.date().isoformat() or next_run<=today:next_run+=timedelta(days=1)
            row['nextRun']=next_run.timestamp() if row['enabled'] else None
        return rows


class Engine:
    def __init__(self,store,adapter):
        self.store=store;self.adapter=adapter;self.lock=threading.RLock();self.stop=threading.Event()
        self.read_only=bool(getattr(adapter,'read_only',False))
        self.account=None;self.self_id=None;self.name='未登录';self.group_list=[];self.selected=None
        self.connection={'status':'loading'};self.last_sync=0;self.error=None;self.baseline=set();self.sync_lock=threading.Lock()
        self.connection_generation=0;self.group_generation=0
        self.logging_out=False;self.account_action_lock=threading.Lock();self.send_lock=threading.Lock()
        self.threads=[]

    def start(self):
        targets=(self.poll_loop,) if self.read_only else (self.poll_loop,self.send_loop,self.schedule_loop)
        automatic=getattr(self,'windows_auto_reply',None)
        if self.read_only and automatic:targets+=(automatic.notifications.run,)
        for target in targets:
            thread=threading.Thread(target=target,daemon=True);thread.start();self.threads.append(thread)

    def refresh_connection(self,force=False):
        with self.lock:
            if self.logging_out:return None
            self.connection_generation+=1;generation=self.connection_generation
        state=self.adapter.auth()
        account=(state.get('sourceId') if state.get('status')=='snapshot_ready' else None) if self.read_only else (state.get('loggedInUser') if state.get('status')=='logged_in' else None)
        with self.lock:
            # An older auth query may finish after logout or a newer refresh.
            if self.logging_out or generation!=self.connection_generation:return None
            changed=account!=self.account;self.connection=state
            if changed:
                self.account=account;self.self_id=None;self.name='未登录';self.group_list=[];self.selected=None;self.baseline.clear()
                self.group_generation+=1
            load_groups=bool(account and (changed or force or not self.group_list))
            if load_groups:
                self.group_generation+=1;group_generation=self.group_generation
        if load_groups:
            data=self.adapter.call('groups',account=account)
            with self.lock:
                # An auth-only poll must not discard a requested group refresh.
                # A newer group load or account transition still invalidates it.
                if self.account!=account or self.logging_out or group_generation!=self.group_generation:return None
                self.group_list=data['groups'];self.self_id=data['selfId'];self.name=data['name']
                if self.store.watched(account) is None:
                    # Migrate existing MVP rules; new accounts require an explicit selection.
                    prior=self.store.rows('SELECT DISTINCT group_id FROM replies WHERE account=?',(account,))
                    self.store.set_watched(account,[r['group_id'] for r in prior if r['group_id'] in {g['id'] for g in self.group_list}])
                if not self.selected:
                    self.selected=next(iter(self.store.watched(account) or []),None)
        return account

    def validate(self,account,group):
        with self.lock:
            ready_status='snapshot_ready' if self.read_only else 'logged_in'
            if self.logging_out or not account or self.account!=account or self.connection.get('status')!=ready_status:raise ValueError('数据源未就绪或账号已变化，请刷新页面。')
            if group not in {x['id'] for x in self.group_list}:raise ValueError('请选择当前账号的有效群聊。')

    def sync_group(self,account,group):
        if self.read_only:return
        with self.sync_lock:
            self.validate(account,group)
            if group not in (self.store.watched(account) or []):return
            data=self.adapter.call('messages',account=account,groupId=group)
            # A logout/account change may finish while the client query is running.
            self.validate(account,group)
            if group not in (self.store.watched(account) or []):return
            fresh=self.store.ingest(account,group,data['messages'])
            key=(account,group)
            if key in self.baseline:
                for row in fresh:self.store.auto_reply(account,group,row)
            self.baseline.add(key)
            with self.lock:self.last_sync=time.time();self.error=None

    def set_watched(self,account,groups):
        if not isinstance(groups,list) or any(not isinstance(g,str) for g in groups):raise ValueError('请选择有效的群聊。')
        with self.lock:
            if account!=self.account or not account:raise ValueError('账号已变化。')
            available={g['id'] for g in self.group_list}
            if not set(groups)<=available:raise ValueError('群列表已变化，请刷新后选择。')
        with self.sync_lock:
            with self.lock:
                if self.logging_out or account!=self.account:raise ValueError('账号已变化，请刷新。')
            self.store.set_watched(account,list(dict.fromkeys(groups)))
            self.baseline={key for key in self.baseline if key[0]!=account or key[1] in groups}
        return self.store.watched(account)

    def logout(self,account):
        if self.read_only:raise ValueError('数据库副本为只读数据源，不操作微信登录状态。')
        with self.account_action_lock:
            with self.lock:
                if account!=self.account or not account:raise ValueError('账号已变化，请刷新。')
                self.logging_out=True;self.connection_generation+=1;self.group_generation+=1
            try:
                # Let an already submitted send finish; never begin another during logout.
                with self.send_lock:
                    self.store.pause_account(account)
                    result=self.adapter.logout()
                return result
            finally:
                with self.lock:self.logging_out=False
                self.refresh_connection(force=True)

    def set_reply(self,account,group,enabled,text,cooldown):
        if self.read_only:raise ValueError('数据库副本只支持读取，不支持自动回复。')
        self.validate(account,group)
        if enabled and self.store.watched(account) is not None and group not in self.store.watched(account):
            raise ValueError('请先勾选读取这个群聊，再开启自动回复。')
        if enabled and not self.store.reply(account,group)['enabled']:
            # Baseline before activation so existing mentions are never replayed.
            with self.sync_lock:
                self.validate(account,group)
                if group not in (self.store.watched(account) or []):raise ValueError('请先勾选读取这个群聊。')
                rows=self.adapter.call('messages',account=account,groupId=group)['messages']
                self.validate(account,group)
                self.store.ingest(account,group,rows);self.baseline.add((account,group))
                return self.store.set_reply(account,group,enabled,text,cooldown)
        return self.store.set_reply(account,group,enabled,text,cooldown)

    def poll_loop(self):
        while not self.stop.is_set():
            try:
                database_service=getattr(self,'database_service',None)
                if database_service:database_service.check_for_changes()
                account=self.refresh_connection()
                automatic=getattr(self,'windows_auto_reply',None)
                if automatic:automatic.tick()
                scheduler=getattr(self,'windows_scheduler',None)
                if scheduler:scheduler.tick()
                if account and not self.read_only:
                    groups=set(self.store.watched(account) or [])
                    for group in groups:
                        if self.logging_out or group not in (self.store.watched(account) or []):continue
                        try:self.validate(account,group);self.sync_group(account,group)
                        except (ValueError,BridgeError) as exc:
                            with self.lock:self.error=str(exc)
            except Exception as exc:
                automatic=getattr(self,'windows_auto_reply',None)
                if automatic:automatic.pause_all()
                scheduler=getattr(self,'windows_scheduler',None)
                if scheduler:scheduler.pause_all()
                with self.lock:self.error=str(exc) if isinstance(exc,(ValueError,BridgeError)) else '同步暂时失败，正在重试。'
            self.stop.wait(2)

    def schedule_loop(self):
        if self.read_only:return
        while not self.stop.is_set():
            with self.lock:
                account=self.account if self.connection.get('status')=='logged_in' and not self.logging_out else None
            if account:self.store.schedule_due(account)
            self.stop.wait(1)

    def send_loop(self):
        if self.read_only:return
        while not self.stop.is_set():
            row=None
            with self.send_lock:
                if not self.logging_out:row=self.store.claim()
                if row:
                    try:
                        state=self.adapter.auth()
                        if state.get('status')!='logged_in' or state.get('loggedInUser')!=row['account']:
                            raise BridgeError('账号不在线或已经切换，未发送。')
                        result=self.adapter.call('send',account=row['account'],groupId=row['group_id'],text=row['text'],mentionIds=json.loads(row['mentions']))
                    except BridgeError as exc:result={'status':'failed','error':str(exc)}
                    except Exception:result={'status':'unknown','error':'未确认发送结果，系统不会自动重发，请先查看群聊。'}
                    self.store.finish(row['id'],result)
            if not row:self.stop.wait(.4)

    def snapshot(self):
        with self.lock:
            result={'connection':self.connection,'account':self.account,'selfId':self.self_id,'name':self.name,
                'groups':self.group_list,'selected':self.selected,'lastSync':self.last_sync,'syncError':self.error,'timezone':'Asia/Shanghai','loggingOut':self.logging_out}
        account=result['account']
        result['jobs']=self.store.jobs(account) if account and not self.read_only else []
        scheduler=getattr(self,'windows_scheduler',None)
        if account and scheduler:result['jobs']=scheduler.list(account)
        result['outbox']=self.store.outbox(account) if account and not self.read_only else []
        result['watchedGroups']=self.store.watched(account) or [] if account else []
        return result
