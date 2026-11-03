"""Local, resumable search index for exported conversations.

The index never changes an export. A conversation becomes searchable only after
its complete source file has been indexed and its fingerprint rechecked.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from contextlib import contextmanager
import hashlib
import json
import os
import sqlite3
import threading
from zipfile import BadZipFile


MAX_ROW = 32 * 1024 * 1024
SEPARATOR = "\x1f"
SCHEMA_VERSION = 2
PAGE_SIZE = 40


def searchable_fields(row):
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    quoted = quote.get("message") if isinstance(quote.get("message"), dict) else {}
    media = row.get("media") if isinstance(row.get("media"), dict) else {}
    asset = row.get("asset") if isinstance(row.get("asset"), dict) else {}
    links = row.get("links") if isinstance(row.get("links"), list) else []
    return [str(value or "") for value in (row.get("text"), row.get("description"),
            quoted.get("text"), media.get("filename"), asset.get("filename"), *links)]


def excerpt(value, query, limit):
    value = str(value or "")
    if len(value) <= limit:
        return value
    position = value.casefold().find(query.casefold()) if query else -1
    start = max(0, position - limit // 3) if position >= 0 else 0
    return ("…" if start else "") + value[start:start + limit] + ("…" if start + limit < len(value) else "")


def source_fingerprint(chat):
    source = chat.archive if chat.archive is not None else chat.directory / "messages.jsonl"
    stat = source.stat()
    identity = f"{source.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{chat.prefix}|{chat.chat_id}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def create_search_acceleration(con):
    """FTS5 trigram is optional; ordinary SQLite can still search the records."""
    existed = bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='records_fts'").fetchone())
    try:
        con.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(
                       search_text, content='records', content_rowid='id', tokenize='trigram')""")
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "no such module: fts5" in message or "no such tokenizer: trigram" in message:
            return False
        raise
    con.executescript("""
        CREATE TRIGGER IF NOT EXISTS records_ai AFTER INSERT ON records BEGIN
          INSERT INTO records_fts(rowid,search_text) VALUES(new.id,new.search_text);
        END;
        CREATE TRIGGER IF NOT EXISTS records_ad AFTER DELETE ON records BEGIN
          INSERT INTO records_fts(records_fts,rowid,search_text) VALUES('delete',old.id,old.search_text);
        END;
    """)
    if not existed and con.execute("SELECT 1 FROM records LIMIT 1").fetchone():
        con.execute("INSERT INTO records_fts(records_fts) VALUES('rebuild')")
    return True


class GlobalSearchIndex:
    def __init__(self, catalog, directory, opener):
        self.catalog = catalog
        self.directory = Path(directory)
        self.database = self.directory / "global-search.sqlite3"
        self.lock_path = self.directory / "index.lock"
        self.opener = opener
        self.guard = threading.RLock()
        self.thread = None
        self.lock_file = None
        self.fatal_error = ""
        self.stop_event = threading.Event()

    def stop(self):
        """Request cooperative shutdown; incomplete sources resume on the next start."""
        self.stop_event.set()
        worker = self.thread
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=3)

    def exists(self):
        return self.database.is_file()

    @contextmanager
    def _connection(self):
        con = sqlite3.connect(self.database, timeout=30)
        try:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA busy_timeout=30000")
            con.execute("PRAGMA synchronous=NORMAL")
            with con:
                yield con
        finally:
            con.close()

    def _lock(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+b")
        if handle.seek(0, 2) == 0:
            handle.write(b"0"); handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return None
        return handle

    @staticmethod
    def _unlock(handle):
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def _setup(self):
        with self._connection() as con:
            existing = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='index_meta'").fetchone()
            if existing:
                row = con.execute("SELECT version FROM index_meta LIMIT 1").fetchone()
                if row is None or row["version"] != SCHEMA_VERSION:
                    raise ValueError("本地搜索索引版本不兼容，请使用独立的数据目录")
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript("""
            CREATE TABLE IF NOT EXISTS index_meta(version INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS sources(
              chatkey TEXT PRIMARY KEY, chat_id TEXT NOT NULL, name TEXT NOT NULL,
              kind TEXT NOT NULL, source TEXT NOT NULL, client TEXT NOT NULL, origin TEXT NOT NULL, fingerprint TEXT NOT NULL,
              status TEXT NOT NULL, active INTEGER NOT NULL, expected INTEGER NOT NULL,
              processed INTEGER NOT NULL DEFAULT 0, indexed INTEGER NOT NULL DEFAULT 0,
              skipped INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', updated INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS records(
              id INTEGER PRIMARY KEY AUTOINCREMENT, chatkey TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL,
              message_id TEXT NOT NULL, time TEXT NOT NULL, day TEXT NOT NULL,
              sender TEXT NOT NULL, sender_fold TEXT NOT NULL, kind TEXT NOT NULL,
              has_links INTEGER NOT NULL, is_mini INTEGER NOT NULL, fields_json TEXT NOT NULL, search_text TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS records_source_position ON records(chatkey,start);
            CREATE INDEX IF NOT EXISTS records_time ON records(day,time DESC,id DESC);
            """)
            row = con.execute("SELECT version FROM index_meta LIMIT 1").fetchone()
            if row is None:
                con.execute("INSERT INTO index_meta VALUES(?)", (SCHEMA_VERSION,))
            create_search_acceleration(con)

    def _sync(self, recover=False, retry_errors=False):
        self.catalog.refresh()
        with self.catalog.lock:
            chats = list(self.catalog.chats.values())
        with self._connection() as con:
            con.execute("UPDATE sources SET active=0")
            for chat in chats:
                try:
                    fingerprint = source_fingerprint(chat) if chat.available else ""
                    problem = "" if fingerprint else "导出消息文件不可用"
                except (OSError, ValueError):
                    fingerprint, problem = "", "导出消息文件不可用"
                old = con.execute("SELECT fingerprint,status FROM sources WHERE chatkey=?", (chat.key,)).fetchone()
                if old is None:
                    status = "error" if problem else "pending"
                    con.execute("""INSERT INTO sources(chatkey,chat_id,name,kind,source,client,origin,fingerprint,status,active,expected,error,updated)
                                   VALUES(?,?,?,?,?,?,?,?,?,1,?,?,?)""",
                                (chat.key, chat.chat_id, chat.name, chat.kind, chat.source,
                                 getattr(chat, "client", "unknown"), getattr(chat, "origin", ""), fingerprint, status,
                                 chat.rows, problem, chat.updated))
                    continue
                status = old["status"]
                if problem:
                    status = "error"
                elif old["fingerprint"] != fingerprint or status == "removed" or (recover and status == "building") or (retry_errors and status == "error"):
                    status = "pending"
                con.execute("""UPDATE sources SET chat_id=?,name=?,kind=?,source=?,client=?,origin=?,fingerprint=?,status=?,
                               active=1,expected=?,error=CASE WHEN ?<>'' THEN ? WHEN ?='pending' THEN '' ELSE error END,
                               updated=? WHERE chatkey=?""",
                            (chat.chat_id, chat.name, chat.kind, chat.source, getattr(chat, "client", "unknown"),
                             getattr(chat, "origin", ""), fingerprint, status, chat.rows,
                             problem, problem, status, chat.updated, chat.key))
            con.execute("UPDATE sources SET status='removed' WHERE active=0 AND status<>'removed'")

    def prepare(self, retry_errors=False):
        with self.guard:
            if self.stop_event.is_set():
                raise ValueError("查看器已关闭，请重新打开")
            if self.thread and self.thread.is_alive():
                self._sync(retry_errors=retry_errors)
                return self.status()
            handle = self._lock()
            if handle is None:
                # Another process may be indexing while this one has learned of
                # a newly added source. Queue it without disturbing the owner.
                if self.database.is_file():
                    try:
                        self._sync(retry_errors=retry_errors)
                    except sqlite3.Error:
                        pass
                return self.status()
            try:
                self._setup()
                self._sync(recover=True, retry_errors=retry_errors)
                with self._connection() as con:
                    pending = con.execute("SELECT 1 FROM sources WHERE active=1 AND status='pending' LIMIT 1").fetchone()
                if not pending:
                    self._unlock(handle)
                    return self.status()
                self.fatal_error = ""
                self.lock_file = handle
                self.thread = threading.Thread(target=self._work, name="global-search-index", daemon=True)
                self.thread.start()
                return self.status()
            except sqlite3.Error as exc:
                self._unlock(handle)
                raise ValueError("本地搜索索引无法读取，请关闭查看器后检查索引目录。") from exc
            except Exception:
                self._unlock(handle)
                raise

    def _work(self):
        released = False
        try:
            while not self.stop_event.is_set():
                with self.guard:
                    with self._connection() as con:
                        row = con.execute("""SELECT chatkey,fingerprint FROM sources WHERE active=1 AND status='pending'
                                             ORDER BY updated DESC,chatkey LIMIT 1""").fetchone()
                    if not row:
                        handle, self.lock_file = self.lock_file, None
                        self.thread = None
                        self._unlock(handle)
                        released = True
                        return
                self._build(row["chatkey"], row["fingerprint"])
        except Exception:
            self.fatal_error = "搜索索引准备中断，可重试。"
        finally:
            if not released:
                with self.guard:
                    handle, self.lock_file = self.lock_file, None
                    self.thread = None
                    self._unlock(handle)

    def _build(self, chatkey, fingerprint):
        processed = indexed = skipped = 0
        try:
            chat = self.catalog.get(chatkey)
            with self._connection() as con:
                changed = con.execute("""UPDATE sources SET status='building',processed=0,indexed=0,skipped=0,error=''
                                         WHERE chatkey=? AND active=1 AND status='pending' AND fingerprint=?""",
                                      (chatkey, fingerprint)).rowcount
                if not changed:
                    return
                con.execute("DELETE FROM records WHERE chatkey=?", (chatkey,))
            with self.opener(chat, "messages.jsonl") as (stream, _size):
                batch = []
                while True:
                    if self.stop_event.is_set():
                        return
                    start = stream.tell()
                    raw = stream.readline(MAX_ROW + 2)
                    if not raw:
                        break
                    if len(raw) > MAX_ROW:
                        raise ValueError("单条消息超过可读取大小")
                    if not raw.strip():
                        continue
                    processed += 1
                    try:
                        row = json.loads(raw.decode("utf-8-sig"))
                        if not isinstance(row, dict) or str(row.get("chatId") or "") != chat.chat_id:
                            skipped += 1
                            continue
                    except (UnicodeError, json.JSONDecodeError):
                        skipped += 1
                        continue
                    fields = searchable_fields(row)
                    when = str(row.get("time") or "")
                    day = when[:10]
                    try:
                        if date.fromisoformat(day).isoformat() != day:
                            day = ""
                    except ValueError:
                        day = ""
                    sender = str(row.get("senderName") or "")
                    links = row.get("links") if isinstance(row.get("links"), list) else []
                    batch.append((chatkey, start, start + len(raw.rstrip(b"\r\n")), str(row.get("id") or ""),
                                  when, day, sender, sender.casefold(), str(row.get("kind") or "unknown"),
                                  int(bool(links)), int(bool(row.get("miniProgram"))),
                                  json.dumps(fields, ensure_ascii=False, separators=(",", ":")),
                                  SEPARATOR.join(value.casefold() for value in fields)))
                    indexed += 1
                    if len(batch) >= 1200:
                        if not self._write_batch(chatkey, fingerprint, batch, processed, indexed, skipped):
                            self._discard(chatkey)
                            return
                        batch.clear()
                if batch and not self._write_batch(chatkey, fingerprint, batch, processed, indexed, skipped):
                    self._discard(chatkey)
                    return
            if self.stop_event.is_set():
                return
            current = source_fingerprint(chat)
            with self._connection() as con:
                state = con.execute("SELECT fingerprint,status,active FROM sources WHERE chatkey=?", (chatkey,)).fetchone()
                if state and state["active"] and state["status"] == "building" and state["fingerprint"] == fingerprint and current == fingerprint:
                    con.execute("UPDATE sources SET status='ready',processed=?,indexed=?,skipped=?,error='' WHERE chatkey=?",
                                (processed, indexed, skipped, chatkey))
                else:
                    con.execute("DELETE FROM records WHERE chatkey=?", (chatkey,))
                    if state and state["active"] and state["status"] == "building":
                        con.execute("UPDATE sources SET status='pending',fingerprint=?,processed=0,indexed=0 WHERE chatkey=?", (current, chatkey))
        except (OSError, ValueError, KeyError, BadZipFile, sqlite3.Error):
            with self._connection() as con:
                con.execute("DELETE FROM records WHERE chatkey=?", (chatkey,))
                con.execute("""UPDATE sources SET status='error',processed=?,indexed=0,skipped=?,error='资料读取失败，请检查导出文件'
                               WHERE chatkey=? AND active=1 AND status IN ('pending','building') AND fingerprint=?""",
                            (processed, skipped, chatkey, fingerprint))

    def _write_batch(self, chatkey, fingerprint, batch, processed, indexed, skipped):
        with self._connection() as con:
            state = con.execute("SELECT fingerprint,status,active FROM sources WHERE chatkey=?", (chatkey,)).fetchone()
            if not state or not state["active"] or state["status"] != "building" or state["fingerprint"] != fingerprint:
                return False
            con.executemany("""INSERT INTO records(chatkey,start,end,message_id,time,day,sender,sender_fold,kind,
                               has_links,is_mini,fields_json,search_text) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", batch)
            con.execute("UPDATE sources SET processed=?,indexed=?,skipped=? WHERE chatkey=?",
                        (processed, indexed, skipped, chatkey))
        return True

    def _discard(self, chatkey):
        with self._connection() as con:
            con.execute("DELETE FROM records WHERE chatkey=?", (chatkey,))

    def _coverage(self, con):
        rows = list(con.execute("SELECT status,processed,indexed,skipped,name,error FROM sources WHERE active=1"))
        counts = {key: sum(row["status"] == key for row in rows) for key in ("ready", "building", "pending", "error")}
        current = next((row["name"] for row in rows if row["status"] == "building"), "")
        return {"total": len(rows), "ready": counts["ready"], "building": counts["building"],
                "pending": counts["pending"], "failed": counts["error"],
                "indexedMessages": sum(row["indexed"] for row in rows if row["status"] == "ready"),
                "skipped": sum(row["skipped"] for row in rows if row["status"] == "ready"),
                "current": current, "error": self.fatal_error}

    def status(self):
        if not self.database.is_file():
            return {"state":"idle", "total":len(self.catalog.snapshot()["chats"]), "ready":0,
                    "building":0, "pending":0, "failed":0, "indexedMessages":0, "skipped":0, "current":"", "error":""}
        try:
            with self._connection() as con:
                coverage = self._coverage(con)
        except sqlite3.Error as exc:
            code = getattr(exc, "sqlite_errorcode", 0) & 0xff
            transient = code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
            if "no such table" in str(exc).lower():
                try:
                    handle = self._lock()
                    transient = handle is None
                    self._unlock(handle)
                except OSError:
                    transient = False
            return {"state":"building" if transient else "partial", "total":len(self.catalog.snapshot()["chats"]),
                    "ready":0, "building":0, "pending":0, "failed":0, "indexedMessages":0,
                    "skipped":0, "current":"", "localWorker":False,
                    "error":"" if transient else "本地搜索索引无法读取，请关闭查看器后检查索引目录。"}
        running = bool(self.thread and self.thread.is_alive())
        coverage["state"] = ("building" if running or coverage["building"] or coverage["pending"]
                             else "partial" if coverage["failed"] or coverage["error"] else "ready")
        coverage["localWorker"] = running
        return coverage

    def query(self, parameters):
        query = parameters.get("q", "").strip()
        sender = parameters.get("sender", "").strip()
        source = parameters.get("source", "")
        scope = parameters.get("scope", "all")
        client = parameters.get("client", "all")
        category = parameters.get("category", "all")
        start_date, end_date = parameters.get("from", ""), parameters.get("to", "")
        if not query:
            raise ValueError("请输入要搜索的内容")
        if len(query) > 200 or len(sender) > 200 or SEPARATOR in query or "\x00" in query:
            raise ValueError("搜索内容无效或过长")
        if (scope not in ("all", "group", "direct") or client not in ("all", "wechat", "wecom", "unknown")
                or category not in ("all", "file", "media", "link", "miniprogram")):
            raise ValueError("搜索范围无效")
        for value in (start_date, end_date):
            if value:
                try:
                    if date.fromisoformat(value).isoformat() != value:
                        raise ValueError()
                except ValueError:
                    raise ValueError("日期格式无效") from None
        if start_date and end_date and start_date > end_date:
            raise ValueError("开始日期不能晚于结束日期")
        page = int(parameters.get("page", "1"))
        if page < 1 or page > 100000:
            raise ValueError("搜索页码无效")
        self.prepare()
        wanted = query.casefold()
        where, args = ["s.active=1", "s.status='ready'", "instr(d.search_text,?)>0"], [wanted]
        join = ""
        with self._connection() as con:
            has_fts = bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='records_fts'").fetchone())
        if len(wanted) >= 3 and has_fts:
            join = " JOIN records_fts ON records_fts.rowid=d.id"
            where.append("records_fts MATCH ?")
            args.append('"' + wanted.replace('"', '""') + '"')
        if scope != "all": where.append("s.kind=?"); args.append(scope)
        if client == "unknown": where.append("s.client IN ('unknown','conflict')")
        elif client != "all": where.append("s.client=?"); args.append(client)
        if source: where.append("s.source=?"); args.append(source)
        if sender: where.append("instr(d.sender_fold,?)>0"); args.append(sender.casefold())
        if start_date: where.append("d.day>=?"); args.append(start_date)
        if end_date: where.append("d.day<>'' AND d.day<=?"); args.append(end_date)
        if category == "file": where.append("d.kind='file'")
        if category == "media": where.append("d.kind IN ('image','video')")
        if category == "link": where.append("(d.kind='link' OR d.has_links=1)")
        if category == "miniprogram": where.append("(d.kind='miniprogram' OR d.is_mini=1)")
        statement = " FROM records d JOIN sources s ON s.chatkey=d.chatkey" + join + " WHERE " + " AND ".join(where)
        with self._connection() as con:
            con.execute("BEGIN")
            coverage = self._coverage(con)
            total = con.execute("SELECT count(*)" + statement, args).fetchone()[0]
            rows = list(con.execute("""SELECT d.id,d.chatkey,d.start,d.end,d.time,d.sender,d.kind,d.fields_json,
                                  s.name,s.source,s.client,s.origin,s.kind AS chat_kind,s.fingerprint""" + statement +
                                  " ORDER BY (d.day='') ASC,d.time DESC,d.id DESC LIMIT ? OFFSET ?",
                                  [*args, PAGE_SIZE, (page - 1) * PAGE_SIZE]))
            con.commit()
        items = []
        for row in rows:
            fields = json.loads(row["fields_json"])
            links = fields[5:]
            matching_links = [link for link in links if wanted in link.casefold()]
            items.append({"recordId":row["id"], "chatKey":row["chatkey"], "chatName":row["name"],
                          "chatKind":row["chat_kind"], "source":row["source"], "client":row["client"],
                          "origin":row["origin"], "fingerprint":row["fingerprint"],
                          "start":row["start"], "end":row["end"], "time":row["time"],
                          "sender":row["sender"] or "发言人未知", "kind":row["kind"],
                          "text":excerpt(fields[0], query, 500), "description":excerpt(fields[1], query, 240),
                          "quote":{"text":excerpt(fields[2], query, 240)},
                          "asset":{"filename":fields[3] or fields[4]},
                          "links":[excerpt(link, query, 300) for link in (matching_links or links)[:3]]})
        return {"items":items, "total":total, "page":page, "pageSize":PAGE_SIZE, "coverage":coverage}

    def resolve(self, record_id, chatkey, fingerprint):
        with self._connection() as con:
            row = con.execute("""SELECT d.chatkey,d.start,d.end,d.message_id,s.fingerprint FROM records d
                                 JOIN sources s ON s.chatkey=d.chatkey
                                 WHERE d.id=? AND d.chatkey=? AND s.fingerprint=? AND s.active=1 AND s.status='ready'""",
                              (record_id, chatkey, fingerprint)).fetchone()
        if not row:
            raise ValueError("搜索结果已变化，请重新搜索")
        chat = self.catalog.get(chatkey)
        try:
            current = source_fingerprint(chat)
        except OSError:
            raise ValueError("原始导出文件不可用，请重新搜索") from None
        if current != fingerprint:
            self.prepare()
            raise ValueError("原始导出已变化，请等待索引更新后重新搜索")
        return chat, dict(row)
