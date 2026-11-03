"""A local, read-only browser for WeBridge conversation exports."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import sqlite3
from collections import Counter, deque
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from threading import RLock, Timer
from urllib.parse import parse_qs, urlparse
from zipfile import ZipFile, BadZipFile
import webbrowser
from global_search import GlobalSearchIndex, searchable_fields, source_fingerprint


BASE = Path(__file__).resolve().parent
DEFAULT_ROOT = BASE / "exports"
STATE_DIR = BASE / ".chat-viewer-data"
CONFIG = STATE_DIR / "sources.json"
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}
MAX_ROW = 32 * 1024 * 1024


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def relative_path(value: str) -> str:
    value = str(value).replace("\\", "/")
    parts = PurePosixPath(value).parts
    if not value or value.startswith("/") or not parts or any(p in (".", "..") or ":" in p for p in parts):
        raise ValueError("导出目录中的文件路径无效")
    return "/".join(parts)


@dataclass(frozen=True)
class Conversation:
    key: str
    chat_id: str
    name: str
    kind: str
    source: str
    rows: int
    updated: int
    directory: Path | None = None
    archive: Path | None = None
    prefix: str = ""
    available: bool = True
    client: str = "unknown"
    review_status: str = "未提供"
    origin: str = ""
    preview: bool = False

    def public(self):
        return {"key": self.key, "name": self.name, "kind": self.kind,
                "source": self.source, "rows": self.rows, "updated": self.updated,
                "available": self.available, "client": self.client, "reviewStatus": self.review_status,
                "origin": self.origin, "preview": self.preview}


def export_metadata(manifest, review):
    manifest = manifest if isinstance(manifest, dict) else {}
    review = review if isinstance(review, dict) else {}
    coverage = manifest.get("coverage") if isinstance(manifest.get("coverage"), dict) else {}
    declared = {str(value).strip().lower() for value in
                (manifest.get("client"), coverage.get("client"), review.get("client")) if value}
    declared &= {"wechat", "wecom"}
    family = str(coverage.get("storageFamily") or "").lower()
    if family.startswith("wechat desktop "):
        declared.add("wechat")
    elif family.startswith("wecom "):
        declared.add("wecom")
    client = next(iter(declared)) if len(declared) == 1 else "conflict" if len(declared) > 1 else "unknown"
    human = manifest.get("humanReview") if isinstance(manifest.get("humanReview"), dict) else {}
    if review.get("_readError"):
        status = "复核信息无法读取"
    elif (review.get("status") == "confirmed" and human.get("status") == "confirmed"
            and review.get("reviewId") and review.get("reviewId") == human.get("reviewId")):
        status = "已标记复核"
    elif review.get("status") == "confirmed" or human.get("status") == "confirmed":
        status = "复核信息不完整"
    elif review.get("status") or human.get("status"):
        status = "未完成复核"
    else:
        status = "未提供"
    identities = {str(value) for value in (manifest.get("selfId"), review.get("selfId")) if value}
    fallback = {str(value) for value in (manifest.get("sourceFingerprint"), review.get("sourceFingerprint")) if value}
    if len(identities) > 1 or (not identities and len(fallback) > 1):
        origin = "来源标识冲突"
    else:
        identity = next(iter(identities or fallback), "")
        origin = "来源 " + hashlib.sha256((client + ":" + identity).encode("utf-8")).hexdigest()[:6] if identity else ""
    return client, status, origin


def make_conversation(chat, rows, source, *, directory=None, archive=None, prefix="", available=True,
                      manifest=None, review=None):
    kind = chat.get("conversationKind")
    if kind not in ("group", "direct"):
        return None
    chat_id = str(chat.get("id") or "")
    if not chat_id:
        return None
    identity = f"{archive or directory}|{prefix}|{chat_id}"
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    try:
        updated = int(chat.get("lastMessageTime") or 0)
    except (TypeError, ValueError):
        updated = 0
    client, review_status, origin = export_metadata(manifest, review)
    return Conversation(key, chat_id, str(chat.get("name") or "未命名会话"), kind, source,
                        max(0, int(rows or 0)), updated, directory, archive, prefix, available,
                        client, review_status, origin)


@contextmanager
def open_conversation_file(chat: Conversation, filename: str):
    relative = relative_path(filename)
    if chat.archive is not None:
        with ZipFile(chat.archive) as archive:
            member = chat.prefix.rstrip("/") + "/" + relative if chat.prefix else relative
            info = archive.getinfo(member)
            if info.is_dir():
                raise FileNotFoundError(relative)
            with archive.open(info) as stream:
                yield stream, info.file_size
    elif chat.directory is not None:
        base = chat.directory.resolve(strict=True)
        path = (base / Path(relative)).resolve(strict=True)
        if not path.is_relative_to(base) or not path.is_file():
            raise ValueError("文件不在会话导出目录内")
        with path.open("rb") as stream:
            yield stream, path.stat().st_size
    else:
        raise FileNotFoundError("会话没有可读取的导出文件")


def read_conversation_json(chat, filename, limit=20 * 1024 * 1024):
    with open_conversation_file(chat, filename) as (stream, size):
        if size > limit:
            raise ValueError("导出信息文件过大")
        return json.loads(stream.read().decode("utf-8-sig"))


def discover_directory(root: Path, label: str, issues=None):
    """Recognize single exports, batches and ZIPs without following directory links."""
    issues = issues if issues is not None else []
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("请选择导出文件夹")
    conversations = []
    for current, dirs, files in os.walk(root, followlinks=False):
        path = Path(current)
        depth = len(path.relative_to(root).parts)
        dirs[:] = [name for name in dirs if name not in {"assets", "analysis", "batch-analysis", "node_modules", ".chat-viewer-data"}
                   and not (path / name).is_symlink()]
        if depth >= 4:
            dirs[:] = []
        if "manifest.json" in files and "messages.jsonl" in files:
            try:
                manifest = read_json(path / "manifest.json")
                if not isinstance(manifest, dict): raise ValueError("导出元数据格式无效")
                review = {}
                if "review.json" in files:
                    try: review = read_json(path / "review.json")
                    except (OSError, UnicodeError, ValueError):
                        review = {"_readError":True}; issues.append(f"复核信息无法读取：{path.name}")
                source = label if path == root or path.parent == root else f"{label} / {path.parent.name}"
                chat = make_conversation(manifest.get("chat") or {}, manifest.get("exportedRows"), source,
                                         directory=path, available=True, manifest=manifest, review=review)
                if chat: conversations.append(chat)
                else: issues.append(f"会话类型或标识不明确：{path.name}")
            except (OSError, UnicodeError, ValueError, TypeError) as exc:
                issues.append(f"导出元数据无法读取：{path.name}（{exc}）")
            dirs[:] = []
            if len(conversations) > 2000:
                raise ValueError("一次接入的会话超过 2000 个，请选择范围更小的目录")
            continue
        for name in sorted(files):
            if name.lower().endswith(".zip"):
                try:
                    conversations.extend(discover_zip(path / name, f"{label} / {Path(name).stem}", issues))
                except (OSError, ValueError, BadZipFile) as exc:
                    issues.append(f"压缩资料无法读取：{name}（{exc}）")
        if len(conversations) > 2000:
            raise ValueError("一次接入的会话超过 2000 个，请选择范围更小的目录")
    return conversations


def discover_zip(archive: Path, label: str, issues=None):
    issues = issues if issues is not None else []
    conversations = []
    with ZipFile(archive) as zipped:
        listed = zipped.namelist()
        names = set(listed)
        duplicates = {name for name, count in Counter(listed).items() if count > 1 and
                      (name.endswith(("/manifest.json", "/messages.jsonl", "/review.json")) or
                       name in ("manifest.json", "messages.jsonl", "review.json"))}
        for name in sorted(n for n in names if n == "manifest.json" or n.endswith("/manifest.json")):
            try:
                prefix = relative_path(name.rsplit("/", 1)[0]) if "/" in name else ""
                messages_name = f"{prefix}/messages.jsonl" if prefix else "messages.jsonl"
                if messages_name not in names: continue
                if name in duplicates or messages_name in duplicates:
                    issues.append(f"压缩包包含同名导出文件：{archive.name}"); continue
                info = zipped.getinfo(name)
                if info.file_size > 10 * 1024 * 1024: raise ValueError("导出元数据过大")
                manifest = json.loads(zipped.read(info).decode("utf-8-sig"))
                if not isinstance(manifest, dict): raise ValueError("导出元数据格式无效")
                review_name = f"{prefix}/review.json" if prefix else "review.json"
                review = {}
                if review_name in names:
                    try:
                        if review_name in duplicates: raise ValueError("复核文件重复")
                        info = zipped.getinfo(review_name)
                        if info.file_size > 10 * 1024 * 1024: raise ValueError("复核元数据过大")
                        review = json.loads(zipped.read(info).decode("utf-8-sig"))
                    except (OSError, UnicodeError, ValueError, KeyError):
                        review = {"_readError":True}; issues.append(f"复核信息无法读取：{archive.name}")
                chat = make_conversation(manifest.get("chat") or {}, manifest.get("exportedRows"), label,
                                         archive=archive, prefix=prefix, manifest=manifest, review=review)
                if chat: conversations.append(chat)
                else: issues.append(f"会话类型或标识不明确：{archive.name}")
            except (ValueError, KeyError, OSError, UnicodeError, TypeError) as exc:
                issues.append(f"压缩包中的导出无法读取：{archive.name}（{exc}）")
                continue
            if len(conversations) > 2000:
                raise ValueError("一次接入的会话超过 2000 个，请选择范围更小的资料")
    return conversations


def discover_path(path: Path, label: str, issues=None):
    path = path.expanduser().resolve(strict=True)
    if path.is_dir(): return discover_directory(path, label, issues)
    if path.is_file() and path.suffix.lower() == ".zip":
        try: return discover_zip(path, label, issues)
        except BadZipFile: raise ValueError("ZIP 压缩包无效或不完整") from None
    raise ValueError("请选择导出文件夹或 ZIP 压缩包")


class Catalog:
    def __init__(self, default_root=DEFAULT_ROOT, config=CONFIG, initial_paths=None):
        self.default_root = Path(default_root)
        self.config = Path(config)
        self.initial_paths = [str(Path(path).expanduser().resolve()) for path in (initial_paths or [])]
        self.lock = RLock()
        self.chats = {}
        self.issues = []
        self.refresh()

    def refresh(self):
        chats, issues = [], []
        try:
            settings = read_json(self.config) if self.config.is_file() else {}
            if not isinstance(settings, dict): raise ValueError("资料设置格式无效")
            extras = settings.get("paths", settings.get("directories", []))
            if not isinstance(extras, list): raise ValueError("资料设置格式无效")
            roots = ([str(self.default_root)] if self.default_root.is_dir() else []) + self.initial_paths + extras
            paths = []
            for raw in roots:
                try:
                    path = Path(raw).expanduser().resolve(strict=True)
                    if path not in paths: paths.append(path)
                except (OSError, TypeError, ValueError) as exc:
                    issues.append(f"已添加的资料无法读取：{exc}")
            repeated = Counter(path.name.casefold() for path in paths)
            for path in paths:
                try:
                    label = f"资料：{path.name}"
                    if repeated[path.name.casefold()] > 1:
                        suffix = hashlib.sha256(str(path).casefold().encode("utf-8")).hexdigest()[:4]
                        label += f" · {path.parent.name} {suffix}"
                    chats.extend(discover_path(path, label, issues))
                except (OSError, ValueError, BadZipFile) as exc:
                    issues.append(f"已添加的资料无法读取：{exc}")
        except (OSError, ValueError, TypeError):
            issues.append("已添加的资料设置无法读取")
        with self.lock:
            unique = {}
            for chat in chats:
                unique.setdefault(chat.key, chat)
            self.chats = unique
            self.issues = issues

    def add_directory(self, raw_path):
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("请输入导出文件夹或 ZIP 路径")
        path = Path(raw_path.strip().strip('"')).expanduser().resolve(strict=True)
        warnings = []
        found = discover_path(path, f"资料：{path.name}", warnings)
        if not found:
            raise ValueError("没有找到包含 manifest.json 和 messages.jsonl 的群聊或私聊导出")
        with self.lock:
            added = [chat for chat in found if chat.key not in self.chats]
            if not added:
                raise ValueError("这些会话已经在列表中")
            settings = read_json(self.config) if self.config.is_file() else {}
            old = settings.get("paths", settings.get("directories", []))
            directories = list(dict.fromkeys([str(Path(item).resolve()) for item in old] + [str(path)]))
            self.config.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.config.with_suffix(".tmp")
            tmp.write_text(json.dumps({"paths": directories}, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.config)
            self.refresh()
        return {"found": len(added), "direct": sum(c.kind == "direct" for c in added),
                "group": sum(c.kind == "group" for c in added), "warnings":len(warnings)}

    def get(self, key):
        with self.lock:
            chat = self.chats.get(key)
        if not chat:
            raise ValueError("找不到该会话，请刷新列表")
        if not chat.available:
            raise ValueError("该会话的导出文件不完整")
        return chat

    def snapshot(self):
        with self.lock:
            chats = list(self.chats.values())
            issues = list(self.issues)
        chats.sort(key=lambda c: (-c.updated, c.name, c.source))
        return {"chats": [chat.public() for chat in chats], "issues": issues}


def read_previous_lines(stream, size, cursor, limit):
    """Return complete records in file order, with byte offsets for paging and source inspection."""
    if cursor is None:
        cursor = size
    if cursor < 0 or cursor > size:
        raise ValueError("消息位置无效，请重新打开会话")
    start, end = cursor, 0
    data = b""
    lines = []
    while len(lines) < limit:
        if end == 0 and start > 0:
            new_start = max(0, start - 512 * 1024)
            stream.seek(new_start)
            part = stream.read(start - new_start)
            data = part + data
            end += len(part)
            start = new_start
        while end > 0 and data[end - 1:end] in (b"\n", b"\r"):
            end -= 1
        if end == 0 and start == 0:
            break
        split = data.rfind(b"\n", 0, end)
        if split < 0 and start > 0:
            if len(data) > MAX_ROW:
                raise ValueError("单条消息超过可读取大小")
            new_start = max(0, start - 512 * 1024)
            stream.seek(new_start)
            part = stream.read(start - new_start)
            data = part + data
            end += len(part)
            start = new_start
            continue
        line_start = split + 1
        raw = data[line_start:end].strip(b"\r")
        if raw:
            lines.append((start + line_start, start + end, raw))
        end = line_start
        if split < 0:
            break
    older = lines[-1][0] if lines else 0
    return list(reversed(lines)), older


def present_message(row, start, end):
    quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
    quoted = quote.get("message") if isinstance(quote.get("message"), dict) else {}
    asset = row.get("asset") if isinstance(row.get("asset"), dict) else {}
    media = row.get("media") if isinstance(row.get("media"), dict) else {}
    links = [str(link) for link in row.get("links", []) if isinstance(link, str)] if isinstance(row.get("links"), list) else []
    asset_path = asset.get("path") or ""
    try:
        asset_path = relative_path(asset_path) if asset_path and relative_path(asset_path).startswith("assets/") else ""
    except ValueError:
        asset_path = ""
    return {"id": str(row.get("id") or ""), "time": str(row.get("time") or ""),
            "sender": str(row.get("senderName") or "发言人未知"), "senderId": str(row.get("senderId") or ""),
            "direction": row.get("direction") if row.get("direction") in ("incoming", "outgoing") else "unknown",
            "kind": str(row.get("kind") or "unknown"), "text": str(row.get("text") or ""),
            "links": links,
            "quote": {"sender": str(quote.get("senderName") or ""), "text": str(quoted.get("text") or ""),
                      "status": str(quote.get("resolutionStatus") or "")},
            "asset": {"path": asset_path, "status": str(asset.get("status") or ""),
                      "reason": str(asset.get("reason") or ""), "filename": str(media.get("filename") or asset.get("filename") or ""),
                      "previewOnly": bool(asset.get("previewOnly"))},
            "parseStatus": str(row.get("parseStatus") or ""), "start": start, "end": end}


def parse_line(chat, start, end, raw):
    try:
        row = json.loads(raw.decode("utf-8-sig"))
        if str(row.get("chatId") or "") != chat.chat_id:
            raise ValueError("消息的会话标识与导出不一致")
        return present_message(row, start, end)
    except (UnicodeError, json.JSONDecodeError, ValueError, AttributeError):
        return {"kind": "invalid", "text": "此条记录无法读取，请核对原始导出文件。",
                "direction": "unknown", "sender": "", "time": "", "start": start, "end": end}


def messages(chat, cursor=None, limit=40):
    with open_conversation_file(chat, "messages.jsonl") as (stream, size):
        lines, older = read_previous_lines(stream, size, cursor, limit)
    rows = [parse_line(chat, start, end, raw) for start, end, raw in lines]
    return {"messages": rows, "older": older if older > 0 else None}


def forward_lines(stream, size, cursor, limit):
    if cursor < 0 or cursor > size:
        raise ValueError("消息位置无效，请重新打开会话")
    stream.seek(cursor)
    lines = []
    while len(lines) < limit:
        start = stream.tell()
        raw = stream.readline(MAX_ROW + 2)
        if not raw:
            break
        if len(raw) > MAX_ROW:
            raise ValueError("单条消息超过可读取大小")
        clean = raw.rstrip(b"\r\n")
        if clean:
            lines.append((start, start + len(clean), clean))
    next_cursor = stream.tell()
    return lines, next_cursor if next_cursor < size else None


def messages_after(chat, cursor, limit=40):
    with open_conversation_file(chat, "messages.jsonl") as (stream, size):
        lines, newer = forward_lines(stream, size, cursor, limit)
    return {"messages": [parse_line(chat, start, end, raw) for start, end, raw in lines], "newer": newer}


def message_context(chat, start, end, radius=20):
    with open_conversation_file(chat, "messages.jsonl") as (stream, size):
        if start < 0 or end <= start or end > size or end - start > MAX_ROW:
            raise ValueError("消息位置无效，请重新搜索")
        if start:
            stream.seek(start - 1)
            if stream.read(1) != b"\n":
                raise ValueError("消息位置已变化，请重新搜索")
        previous, older = read_previous_lines(stream, size, start, radius)
        current_and_next, newer = forward_lines(stream, size, start, radius + 1)
        if not current_and_next or current_and_next[0][:2] != (start, end):
            raise ValueError("消息位置已变化，请重新搜索")
        rows = [parse_line(chat, a, b, raw) for a, b, raw in previous + current_and_next]
        if not rows or next((r for r in rows if r["start"] == start), {}).get("kind") == "invalid":
            raise ValueError("消息记录无法读取，请重新搜索")
    return {"messages": rows, "older": older if older > 0 else None, "newer": newer, "target": start}


def search_messages(chat, parameters, limit=40):
    query = parameters.get("q", "").strip()
    sender = parameters.get("sender", "").strip()
    sender_id = parameters.get("senderId", "").strip()
    category = parameters.get("category", "all")
    start_date, end_date = parameters.get("from", ""), parameters.get("to", "")
    if len(query) > 200 or len(sender) > 200 or len(sender_id) > 512:
        raise ValueError("搜索内容过长")
    if category not in ("all", "file", "media", "link", "miniprogram"):
        raise ValueError("未知的消息类型")
    for value in (start_date, end_date):
        if value:
            try:
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError()
            except ValueError:
                raise ValueError("日期格式无效") from None
    if start_date and end_date and start_date > end_date:
        raise ValueError("开始日期不能晚于结束日期")
    before = int(parameters["before"]) if parameters.get("before") else None
    simple = not (query or sender or sender_id or start_date or end_date) and category == "all"
    if simple:
        page = messages(chat, before, limit)
        return {"items": list(reversed([search_result(row) for row in page["messages"]])),
                "older": page["older"], "total": None, "skipped": 0}
    wanted, sender_wanted = query.casefold(), sender.casefold()

    def matches(row):
        kind = row.get("kind") or "unknown"
        links = row.get("links") if isinstance(row.get("links"), list) else []
        if category == "file" and kind != "file": return False
        if category == "media" and kind not in ("image", "video"): return False
        if category == "link" and kind != "link" and not links: return False
        if category == "miniprogram" and kind != "miniprogram" and not row.get("miniProgram"): return False
        if sender_id and str(row.get("senderId") or "") != sender_id: return False
        if sender_wanted and sender_wanted not in str(row.get("senderName") or "").casefold(): return False
        if start_date or end_date:
            day = str(row.get("time") or "")[:10]
            try:
                if date.fromisoformat(day).isoformat() != day: return False
            except ValueError:
                return False
            if start_date and day < start_date: return False
            if end_date and day > end_date: return False
        return not wanted or any(wanted in value.casefold() for value in searchable_fields(row))

    def decoded(raw):
        try:
            row = json.loads(raw.decode("utf-8-sig"))
            return row if isinstance(row, dict) and str(row.get("chatId") or "") == chat.chat_id else None
        except (UnicodeError, json.JSONDecodeError):
            return None

    hits, count, skipped = deque(maxlen=limit), 0, 0
    with open_conversation_file(chat, "messages.jsonl") as (stream, size):
        if before is not None and (before < 0 or before > size):
            raise ValueError("搜索位置无效，请重新搜索")
        # The UI already has the exact initial count. For subsequent pages of a
        # regular file, walk backwards until one extra match proves there is a
        # previous page. ZIP streams retain the original forward scan because
        # backwards seeks repeatedly decompress the archive.
        if before is not None and parameters.get("pageOnly") == "1" and chat.archive is None:
            if before:
                stream.seek(before - 1)
                if stream.read(1) != b"\n":
                    raise ValueError("搜索位置无效，请重新搜索")
            cursor, found = before, []
            while cursor > 0 and len(found) <= limit:
                lines, cursor = read_previous_lines(stream, size, cursor, 2048)
                if not lines:
                    break
                for start, end, raw in reversed(lines):
                    row = decoded(raw)
                    if row is None:
                        skipped += 1
                    elif matches(row):
                        found.append(search_result(present_message(row, start, end), query))
                        if len(found) > limit:
                            break
            return {"items": found[:limit], "older": found[limit - 1]["start"] if len(found) > limit else None,
                    "total": None, "skipped": skipped}
        while True:
            pos = stream.tell()
            if before is not None and pos >= before:
                break
            raw = stream.readline(MAX_ROW + 2)
            if not raw:
                break
            if len(raw) > MAX_ROW:
                raise ValueError("单条消息超过可读取大小")
            row = decoded(raw)
            if row is None:
                skipped += 1
                continue
            if not matches(row): continue
            clean = raw.rstrip(b"\r\n")
            hits.append(present_message(row, pos, pos + len(clean)))
            count += 1
    values = list(hits)
    return {"items": list(reversed([search_result(row, query) for row in values])),
            "older": values[0]["start"] if count > limit else None, "total": count, "skipped": skipped}


def search_excerpt(value, query, limit):
    value = str(value or "")
    if len(value) <= limit:
        return value
    position = value.casefold().find(query.casefold()) if query else -1
    start = max(0, position - limit // 3) if position >= 0 else 0
    return ("…" if start else "") + value[start:start + limit] + ("…" if start + limit < len(value) else "")


def search_result(row, query=""):
    value = dict(row)
    value["text"] = search_excerpt(value.get("text"), query, 500)
    value["quote"] = dict(value.get("quote") or {})
    if "text" in value["quote"]:
        value["quote"]["text"] = search_excerpt(value["quote"]["text"], query, 240)
    links = value.get("links") or []
    matching_links = [link for link in links if query and query.casefold() in link.casefold()]
    value["links"] = [search_excerpt(link, query, 300) for link in (matching_links or links)[:3]]
    return value


def detail(chat):
    manifest = read_conversation_json(chat, "manifest.json")
    members = []
    member_warning = ""
    try:
        data = read_conversation_json(chat, "members.json")
        member_entries = [{"name": str(item.get("name") or "未命名"), "id": str(item.get("id") or "")}
                          for item in data.get("members", []) if isinstance(item, dict)]
        members = [item["name"] for item in member_entries]
        if data.get("historicalSpeakersAreMembers"):
            member_warning = "部分名称来自历史发言记录，不能确认当前成员状态。"
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        member_entries = []
    return {"chat": chat.public(), "created": str(manifest.get("createdAt") or ""),
            "members": members, "memberEntries": member_entries, "memberWarning": member_warning,
            "truncated": bool(manifest.get("truncatedByLimit")),
            "mediaSummary": str(manifest.get("mediaSummaryText") or "")}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def allowed(self):
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def send_bytes(self, data, kind, status=200, disposition=None):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, value, status=200):
        self.send_bytes(json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def do_GET(self):
        if not self.allowed():
            self.send_error(403)
            return
        url = urlparse(self.path)
        params = {key: value[0] for key, value in parse_qs(url.query).items()}
        try:
            if url.path in ASSETS:
                filename, content_type = ASSETS[url.path]
                bundled = globals().get("_BUNDLED_ASSETS")
                data = bundled[filename].encode("utf-8") if bundled is not None else (BASE / filename).read_bytes()
                self.send_bytes(data, content_type)
            elif url.path == "/api/conversations":
                self.send_json(self.server.catalog.snapshot())
            elif url.path == "/api/detail":
                self.send_json(detail(self.server.catalog.get(params.get("chat", ""))))
            elif url.path == "/api/messages":
                chat = self.server.catalog.get(params.get("chat", ""))
                cursor = int(params["before"]) if "before" in params else None
                self.send_json(messages(chat, cursor))
            elif url.path == "/api/messages-after":
                self.send_json(messages_after(self.server.catalog.get(params.get("chat", "")), int(params["after"])))
            elif url.path == "/api/message-context":
                self.send_json(message_context(self.server.catalog.get(params.get("chat", "")), int(params["start"]), int(params["end"])))
            elif url.path == "/api/search":
                self.send_json(search_messages(self.server.catalog.get(params.get("chat", "")), params))
            elif url.path == "/api/global/status":
                self.send_json(self.server.global_index.status())
            elif url.path == "/api/global/search":
                self.send_json(self.server.global_index.query(params))
            elif url.path == "/api/global/context":
                chat, record = self.server.global_index.resolve(int(params["record"]), params["chat"], params["fingerprint"])
                context = message_context(chat, record["start"], record["end"])
                target = next((row for row in context["messages"] if row["start"] == record["start"]), None)
                if not target or target.get("id", "") != record["message_id"] or source_fingerprint(chat) != record["fingerprint"]:
                    raise ValueError("原始记录已变化，请重新搜索")
                self.send_json(context | {"chatKey":chat.key})
            elif url.path == "/api/record":
                chat = self.server.catalog.get(params.get("chat", ""))
                start, end = int(params["start"]), int(params["end"])
                with open_conversation_file(chat, "messages.jsonl") as (stream, size):
                    if start < 0 or end <= start or end > size or end - start > MAX_ROW:
                        raise ValueError("记录位置无效")
                    stream.seek(start)
                    row = json.loads(stream.read(end - start).decode("utf-8-sig"))
                if str(row.get("chatId") or "") != chat.chat_id:
                    raise ValueError("记录不属于当前会话")
                self.send_json(row)
            elif url.path == "/api/asset":
                chat = self.server.catalog.get(params.get("chat", ""))
                path = relative_path(params.get("path", ""))
                if not path.startswith("assets/"):
                    raise ValueError("文件不在导出附件目录中")
                with open_conversation_file(chat, path) as (stream, size):
                    if size > 100 * 1024 * 1024:
                        raise ValueError("附件超过本地预览大小限制")
                    data = stream.read()
                mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
                if mime not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
                    mime = "application/octet-stream"
                self.send_bytes(data, mime, disposition=None if mime.startswith("image/") else 'attachment; filename="attachment"')
            else:
                self.send_error(404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except (ValueError, KeyError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc) or "资料读取失败"}, 400)

    def do_POST(self):
        expected = f"http://127.0.0.1:{self.server.server_port}"
        if (not self.allowed() or self.headers.get("Origin") != expected
                or self.headers.get("Content-Type") != "application/json"):
            self.send_error(403)
            return
        try:
            if self.path not in ("/api/sources", "/api/global/prepare"):
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16384:
                raise ValueError("资料目录无效")
            data = json.loads(self.rfile.read(length))
            if self.path == "/api/global/prepare":
                self.send_json(self.server.global_index.prepare(retry_errors=data.get("retry") is True))
            else:
                result = self.server.catalog.add_directory(data.get("path"))
                if self.server.global_index.exists():
                    try:
                        self.server.global_index.prepare()
                    except (OSError, ValueError, sqlite3.Error):
                        result["indexWarning"] = "资料已接入，全局索引未能更新，请稍后重试"
                self.send_json(result)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except (ValueError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc) or "无法接入资料"}, 400)


def main():
    parser = argparse.ArgumentParser(description="本地聊天记录查看器")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--source", action="append", default=[], help="导出文件夹或 ZIP，可重复指定")
    parser.add_argument("--data-dir", default=str(STATE_DIR), help="保存本地设置和搜索索引的目录")
    args = parser.parse_args()
    data_dir = Path(args.data_dir).expanduser().resolve()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.catalog = Catalog(config=data_dir / "sources.json", initial_paths=args.source)
    server.global_index = GlobalSearchIndex(server.catalog, data_dir / "global-search-index", open_conversation_file)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"会话档案已启动：{url}", flush=True)
    print("关闭此窗口即可停止；原始导出文件不会被修改。", flush=True)
    if not args.no_browser:
        Timer(0.8, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
