"""Local, evidence-bound storage for private-chat profile candidates.

This module deliberately does not call a model.  It accepts only a formally
reviewed direct-chat export, validates model-produced observations against the
export's references, and publishes confirmed versions atomically.
"""
from __future__ import annotations

from datetime import datetime, timezone
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
from typing import Any
import uuid
from bisect import bisect_right
import re


class ProfileError(ValueError):
    """Public fail-closed error for invalid sources, candidates, or identities."""


SCHEMA_VERSION = 1
_SUBJECTS = {"peer", "self", "relationship"}
_CATEGORIES = {"identity", "background", "needs", "communication", "interests",
               "events", "commitments", "follow_up"}
_BASES = {"explicit", "observed", "uncertain"}
_MAX_OBSERVATIONS = 1000
_MAX_TEXT = 12000
_MAX_NOTE = 4000
_MAX_FILES = 20000
_MAX_FILE_BYTES = 64 * 1024 * 1024
_MAX_JSONL_ROWS = 500000
_ATTACHMENT_FULLTEXT_THRESHOLD = 3000
_ID_RE = {
    "req": re.compile(r"^req-[0-9a-f]{24}$"),
    "cand": re.compile(r"^cand-[0-9a-f]{32}$"),
    "src": re.compile(r"^src-[0-9a-f]{32}$"),
    "link": re.compile(r"^link-[0-9a-f]{32}$"),
    "rev": re.compile(r"^rev-[0-9a-f]{32}$"),
}
_REVIEW_CHECKS = {"accountGroup", "rangeCount", "content", "gaps"}
_PUBLISH_REWRITTEN = {"manifest.json", "解析结果.md"}
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    _assert_no_link_ancestors(path)
    if path.exists() and _is_link(path):
        raise ProfileError("画像存储文件不能是链接。")
    if path.parent.exists() and _is_link(path.parent):
        raise ProfileError("画像存储目录不能是链接。")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ProfileError(f"无法创建画像存储目录：{path.parent.name}") from exc
    temp = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        raise ProfileError(f"无法原子写入画像存储文件：{path.name}") from exc
    finally:
        try:
            if temp.exists() and not _is_link(temp):
                temp.unlink()
        except OSError:
            pass


def _write_text(path: Path, value: str) -> None:
    _assert_no_link_ancestors(path)
    if path.exists() and _is_link(path):
        raise ProfileError("画像存储文件不能是链接。")
    temp = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text(value, encoding="utf-8")
        os.replace(temp, path)
    except (OSError, UnicodeError) as exc:
        raise ProfileError(f"无法原子写入画像存储文本：{path.name}") from exc
    finally:
        try:
            if temp.exists() and not _is_link(temp):
                temp.unlink()
        except OSError:
            pass


def _restore_bytes(path: Path, raw: bytes | None) -> None:
    if raw is None:
        if path.exists() and not _is_link(path):
            path.unlink()
        return
    temp = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".rollback.tmp")
    try:
        temp.write_bytes(raw)
        os.replace(temp, path)
    finally:
        try:
            if temp.exists() and not _is_link(temp):
                temp.unlink()
        except OSError:
            pass


def _read_json(path: Path) -> Any:
    try:
        raw = _read_limited(path, _MAX_FILE_BYTES)
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise ProfileError(f"无法读取画像存储文件：{path.name}") from exc
    if not isinstance(value, dict):
        raise ProfileError(f"画像存储文件必须是JSON对象：{path.name}")
    _check_json_depth(value)
    return value


def _check_json_depth(value: Any) -> None:
    # Some Python JSON decoders accept deeper trees than later hashing/rendering.
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if isinstance(item, (dict, list)):
            if depth > 100:
                raise ProfileError("JSON嵌套层数超过限制。")
            children = item.values() if isinstance(item, dict) else item
            pending.extend((child, depth + 1) for child in children if isinstance(child, (dict, list)))


def _sha256_file(path: Path) -> str:
    digest, _ = _file_digest(path, _MAX_FILE_BYTES)
    return digest


def _file_digest(path: Path, limit: int) -> tuple[str, int]:
    _assert_no_link_ancestors(path)
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(min(1024 * 1024, limit - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise ProfileError(f"画像版本文件过大：{path.name}")
                digest.update(chunk)
    except OSError as exc:
        raise ProfileError(f"无法读取画像版本文件：{path.name}") from exc
    return digest.hexdigest(), total


def _text(value: Any, name: str, *, required: bool = True, limit: int = _MAX_TEXT) -> str:
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
        raise ProfileError(f"{name}格式无效。")
    return value


def _safe_inventory(root: Path) -> list[dict[str, Any]]:
    _assert_no_link_ancestors(root)
    if not root.is_dir() or _is_link(root):
        raise ProfileError("正式导出目录不存在或不是目录。")
    items = []
    total_bytes = 0
    for path in sorted(root.rglob("*")):
        if _is_link(path):
            raise ProfileError("正式导出不能包含符号链接。")
        if path.is_file():
            if len(items) >= _MAX_FILES:
                raise ProfileError("正式导出文件数量或单文件大小超出限制。")
            digest, size = _file_digest(path, _MAX_FILE_BYTES)
            total_bytes += size
            if total_bytes > _MAX_FILE_BYTES * 16:
                raise ProfileError("正式导出文件数量或单文件大小超出限制。")
            rel = path.relative_to(root).as_posix()
            items.append({"path": rel, "bytes": size, "sha256": digest})
    return items


def _is_link(path: Path) -> bool:
    """Treat junctions as links too; Windows junctions may evade is_symlink."""
    try:
        return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())
    except OSError as exc:
        raise ProfileError("无法检查路径安全性。") from exc


def _assert_no_link_ancestors(path: Path) -> None:
    path = Path(path).absolute()
    if any(_is_link(item) for item in (path, *path.parents)):
        raise ProfileError("路径不能包含符号链接或Junction。")


def _read_limited(path: Path, limit: int) -> bytes:
    _assert_no_link_ancestors(path)
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
    except OSError as exc:
        raise ProfileError(f"无法读取文件：{path.name}") from exc
    if len(raw) > limit:
        raise ProfileError(f"文件过大：{path.name}")
    return raw


def _valid_id(value: Any, kind: str) -> str:
    if not isinstance(value, str) or not _ID_RE[kind].fullmatch(value):
        raise ProfileError(f"{kind}标识格式无效。")
    return value


class _FileLock:
    def __init__(self, root: Path, name: str = "store"):
        self.path = root / ("." + name + ".lock")
        self.thread_lock = None
        self.fd = None

    @staticmethod
    def _owner_alive(path: Path) -> bool:
        try:
            raw = _read_limited(path, 4096).decode("ascii").strip().split("\n", 1)[0]
            pid = int(raw)
            if pid <= 0:
                return True
            if os.name == "nt":
                if pid > 0xFFFFFFFF:
                    return True
                # os.kill(pid, 0) is not a harmless liveness probe on Windows:
                # it can deliver CTRL_C_EVENT to a shared console.  Request
                # only SYNCHRONIZE and query the process handle instead.
                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                from ctypes import wintypes
                kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                kernel32.OpenProcess.restype = wintypes.HANDLE
                kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                kernel32.WaitForSingleObject.restype = wintypes.DWORD
                kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
                kernel32.CloseHandle.restype = wintypes.BOOL
                handle = kernel32.OpenProcess(0x00100000, False, pid)
                if not handle:
                    # ERROR_INVALID_PARAMETER means the PID no longer exists.
                    # Access denied and all other failures stay conservative.
                    return ctypes.get_last_error() != 87
                try:
                    # WAIT_OBJECT_0 means the process is signalled (exited).
                    return int(kernel32.WaitForSingleObject(handle, 0)) != 0
                except Exception:
                    return True
                finally:
                    try:
                        kernel32.CloseHandle(handle)
                    except Exception:
                        pass
            os.kill(pid, 0)
            return True
        except ValueError:
            return True
        except PermissionError:
            return True
        except ProcessLookupError:
            return False
        except (OSError, UnicodeError):
            return True

    def _stale(self) -> bool:
        try:
            return time.time() - self.path.stat().st_mtime > 60 and not self._owner_alive(self.path)
        except OSError:
            return False

    def __enter__(self):
        _assert_no_link_ancestors(self.path)
        if _is_link(self.path):
            raise ProfileError("画像库锁文件不能是链接。")
        key = str(self.path.parent.resolve())
        with _LOCKS_GUARD:
            self.thread_lock = _LOCKS.setdefault(key, threading.RLock())
        self.thread_lock.acquire()
        deadline = time.monotonic() + 10
        try:
            while True:
                try:
                    self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    try:
                        os.write(self.fd, str(os.getpid()).encode("ascii"))
                    except BaseException:
                        os.close(self.fd)
                        self.fd = None
                        try:
                            self.path.unlink()
                        except OSError:
                            pass
                        raise
                    return self
                except FileExistsError:
                    if self._stale():
                        try:
                            self.path.unlink()
                            continue
                        except OSError:
                            pass
                    if time.monotonic() >= deadline:
                        raise ProfileError("画像库正被其他确认操作占用，请稍后重试。")
                    time.sleep(0.02)
        except BaseException:
            if self.thread_lock is not None:
                self.thread_lock.release()
                self.thread_lock = None
            raise

    def __exit__(self, *_):
        try:
            if self.fd is not None:
                os.close(self.fd)
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        finally:
            if self.thread_lock is not None:
                self.thread_lock.release()


def _load_lines(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    try:
        raw = _read_limited(path, _MAX_FILE_BYTES)
        if raw.startswith(b"\xef\xbb\xbf"):
            raise ProfileError("消息材料JSONL不应包含UTF-8 BOM。")
        text = raw.decode("utf-8")
        for number, line in enumerate(text.splitlines(), 1):
            if number > _MAX_JSONL_ROWS:
                raise ProfileError("消息材料行数超出限制。")
            if not line.strip():
                raise ProfileError("消息材料JSONL不能包含空白行。")
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ProfileError("消息材料JSONL每行必须是JSON对象。")
            _check_json_depth(value)
            rows.append(value)
    except ProfileError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        raise ProfileError("消息材料不是有效 JSONL。") from exc
    return rows


def _has_long_attachment_text(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"attachment", "attachmentContent"} and isinstance(child, dict):
                text = child.get("text")
                if isinstance(text, str) and len(text) > _ATTACHMENT_FULLTEXT_THRESHOLD:
                    return True
            if _has_long_attachment_text(child):
                return True
    elif isinstance(value, list):
        return any(_has_long_attachment_text(child) for child in value)
    return False


def _message_key(row: dict[str, Any], number: int) -> str:
    for key in ("id", "messageId", "localId", "serverId"):
        value = row.get(key)
        if value not in (None, "", 0):
            return f"{key}:{value}"
    return f"row:{number}"


def _message_hash(row: dict[str, Any]) -> str:
    # Keep quote/forward/attachment/raw fields: those are material changes.
    # Only analysis presentation fields are excluded from the stable identity.
    value = {key: value for key, value in row.items()
             if key not in {"ref", "chunk", "chatName", "analysisRef"}}
    return _digest(value)


_ANALYSIS_IDENTITY_FIELDS = frozenset({
    "id", "messageId", "localId", "serverId", "ref", "chunk",
    "timestamp", "time", "sender", "senderId", "isSelf", "direction",
    "chatId", "chatName", "analysisRef",
})


def _analysis_hash(row: dict[str, Any], ref_to_message_id: dict[int, str]) -> str:
    """Hash corpus semantics while ignoring export-local presentation fields."""
    value = {key: child for key, child in row.items()
             if key not in _ANALYSIS_IDENTITY_FIELDS}
    reply_to = value.get("replyTo")
    if isinstance(reply_to, dict):
        reply_to = dict(reply_to)
        value["replyTo"] = reply_to
        ref = reply_to.get("ref")
        if type(ref) is int and ref > 0 and ref in ref_to_message_id:
            reply_to["ref"] = ref_to_message_id[ref]
    revokes = value.get("revokes")
    if type(revokes) is int and revokes > 0 and revokes in ref_to_message_id:
        value["revokes"] = ref_to_message_id[revokes]
    return _digest(value)


def _canonical_identity(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        client, account, peer = value.get("client"), value.get("accountKey"), value.get("peerId")
    elif isinstance(value, (list, tuple)) and len(value) == 3:
        client, account, peer = value
    else:
        raise ProfileError("身份必须包含 client、accountKey 和 peerId。")
    client = _text(client, "client", limit=32).lower()
    account = _text(account, "accountKey", limit=512)
    peer = _text(peer, "peerId", limit=512)
    if client not in {"wechat", "wecom"}:
        raise ProfileError("client必须为wechat或wecom。")
    return {"client": client, "accountKey": account, "peerId": peer}


def _time_range(rows: Any) -> dict[str, str | None]:
    times = [row.get("time") for row in rows if isinstance(row, dict)
             and isinstance(row.get("time"), str) and row.get("time")]
    return {"start": min(times) if times else None, "end": max(times) if times else None}


def _stable_message_id(value: Any) -> str:
    if (type(value) not in (str, int) or (type(value) is str and not value) or
            (type(value) is int and value == 0)):
        raise ProfileError("源消息稳定ID格式无效。")
    return str(value)


class ProfileStore:
    """A small file-backed profile store with source and revision isolation."""

    def __init__(self, root):
        self.root = Path(root).expanduser().absolute()
        _assert_no_link_ancestors(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ("sources", "requests", "candidates", "profiles", "links"):
            path = self.root / name
            if path.exists() and (_is_link(path) or not path.is_dir()):
                raise ProfileError("画像库目录结构不安全。")
            path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _identity_key(identity: dict[str, str]) -> str:
        return _digest(identity)

    def _profile_dir(self, identity: dict[str, str]) -> Path:
        path = self.root / "profiles" / self._identity_key(identity)
        _assert_no_link_ancestors(path)
        if path.exists() and _is_link(path):
            raise ProfileError("画像目录不能是链接。")
        return path

    def _current_pointer(self, identity: dict[str, str]) -> Path:
        return self._profile_dir(identity) / "current.json"

    def _read_current(self, identity: dict[str, str]) -> dict[str, Any] | None:
        pointer = self._current_pointer(identity)
        if _is_link(pointer):
            raise ProfileError("当前画像指针不能是链接。")
        if not pointer.is_file():
            return None
        pointer_data = _read_json(pointer)
        if not isinstance(pointer_data, dict) or pointer_data.get("schemaVersion") != SCHEMA_VERSION:
            raise ProfileError("当前画像指针损坏。")
        pointer_identity = _canonical_identity(pointer_data.get("identity", {}))
        if pointer_identity != identity:
            raise ProfileError("当前画像指针身份不一致。")
        revision = _valid_id(pointer_data.get("revision"), "rev")
        pointer_candidate = _valid_id(pointer_data.get("candidateId"), "cand")
        pointer_request = _valid_id(pointer_data.get("requestId"), "req")
        base = (pointer.parent / "revisions").resolve()

        def checked_path(field: str) -> Path:
            filename = pointer_data.get(field)
            if (not isinstance(filename, str) or Path(filename).is_absolute() or
                    any(part in {"", ".", ".."} for part in Path(filename).parts)):
                raise ProfileError("当前画像指针损坏。")
            path = (pointer.parent / filename).resolve()
            if not path.is_relative_to(base) or _is_link(path) or not path.is_file():
                raise ProfileError("当前画像指针越界或文件不安全。")
            return path

        profile_path = checked_path("profileFile")
        markdown_path = checked_path("markdownFile")
        confirmation_path = checked_path("confirmationFile")
        digests = pointer_data.get("sha256")
        if (not isinstance(digests, dict) or
                any(not isinstance(digests.get(field), str) or
                    not re.fullmatch(r"[0-9a-f]{64}", digests[field])
                    for field in ("profileFile", "markdownFile", "confirmationFile"))):
            raise ProfileError("当前画像指针缺少版本文件摘要。")
        paths = {"profileFile": profile_path, "markdownFile": markdown_path,
                 "confirmationFile": confirmation_path}
        if any(_sha256_file(path) != digests[field] for field, path in paths.items()):
            raise ProfileError("当前画像版本文件摘要不一致。")
        profile = _read_json(profile_path)
        confirmation = _read_json(confirmation_path)
        if (not isinstance(profile, dict) or profile.get("revision") != revision or
                profile.get("client") != identity["client"] or
                profile.get("accountKey") != identity["accountKey"] or
                profile.get("peerId") != identity["peerId"] or
                profile.get("candidateId") != pointer_candidate or
                profile.get("requestId") != pointer_request):
            raise ProfileError("当前画像指针与版本内容不一致。")
        if (not isinstance(confirmation, dict) or
                confirmation.get("candidateId") != profile.get("candidateId") or
                confirmation.get("confirmedBy") != profile.get("confirmedBy") or
                confirmation.get("confirmedAt") != profile.get("confirmedAt") or
                confirmation.get("note", "") != profile.get("note", "")):
            raise ProfileError("当前画像确认记录与版本内容不一致。")
        try:
            markdown = _read_limited(markdown_path, _MAX_FILE_BYTES).decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        except (OSError, UnicodeError) as exc:
            raise ProfileError("当前画像Markdown无法读取。") from exc
        if markdown != self._markdown(profile):
            raise ProfileError("当前画像Markdown与结构化版本不一致。")
        return profile

    @staticmethod
    def _find_value(*values):
        return next((value for value in values if isinstance(value, str) and value.strip()), "")

    def _formal_export(self, export_root, *, require_analysis: bool = True) -> dict[str, Any]:
        if not isinstance(export_root, (str, os.PathLike)):
            raise ProfileError("正式导出目录路径格式无效。")
        try:
            root = Path(export_root).expanduser().absolute()
        except (TypeError, ValueError, OSError) as exc:
            raise ProfileError("正式导出目录路径格式无效。") from exc
        inventory = _safe_inventory(root)
        manifest_path, review_path = root / "manifest.json", root / "review.json"
        if not manifest_path.is_file() or not review_path.is_file():
            raise ProfileError("画像材料必须是含manifest.json和review.json的正式导出。")
        manifest, review = _read_json(manifest_path), _read_json(review_path)
        if not isinstance(manifest, dict) or not isinstance(review, dict):
            raise ProfileError("正式导出元数据格式无效。")
        human = manifest.get("humanReview")
        if (review.get("status") != "confirmed" or not isinstance(human, dict) or
                human.get("status") != "confirmed"):
            raise ProfileError("源聊天尚未完成人工复核，不能进入画像材料。")
        summary = review.get("summary") if isinstance(review.get("summary"), dict) else {}
        chat = manifest.get("chat") or review.get("chat") or summary.get("chat")
        if not isinstance(chat, dict):
            raise ProfileError("正式导出缺少会话身份。")
        kind = str(chat.get("conversationKind") or manifest.get("conversationKind") or
                   manifest.get("conversationMode") or "").lower()
        chat_id = self._find_value(chat.get("id"), manifest.get("chatId"))
        if kind in {"group", "chatroom", "room"} or "chatroom" in chat_id.lower():
            raise ProfileError("群聊导出不能进入私聊画像。")
        if kind not in {"direct", "private", "one_to_one", "1to1"} and manifest.get("conversationMode") != "direct":
            raise ProfileError("正式导出未证明是一对一私聊。")
        if not chat_id:
            raise ProfileError("正式导出缺少稳定会话ID。")
        peer_id = self._find_value(review.get("peerId"), manifest.get("peerId"))
        if not peer_id:
            raise ProfileError("正式导出缺少可靠的对方身份映射。")
        client = self._find_value(manifest.get("client"), review.get("client"),
                                  manifest.get("sourceClient"))
        self_id = self._find_value(review.get("selfId"), manifest.get("selfId"))
        source_fingerprint = self._find_value(review.get("sourceFingerprint"), manifest.get("sourceFingerprint"))
        account_seed = self_id or source_fingerprint
        if client not in {"wechat", "wecom"} or not account_seed:
            raise ProfileError("正式导出缺少可靠客户端或账号身份。")
        account = "acct-" + hashlib.sha256((client + ":" + account_seed).encode("utf-8")).hexdigest()[:32]
        identity = _canonical_identity({"client": client, "accountKey": account, "peerId": peer_id})
        source_limitations = []
        for container in (manifest, review, summary):
            if not isinstance(container, dict):
                continue
            for field in ("limitations", "warnings"):
                values = container.get(field, [])
                if isinstance(values, str):
                    values = [values]
                if isinstance(values, list):
                    source_limitations.extend(
                        value for value in values
                        if isinstance(value, str) and value.strip())
        if not self_id:
            source_limitations.append(
                "缺少可靠本人身份ID；消息方向统一为unknown，不能形成明确self/peer归因。")
        source_limitations = list(dict.fromkeys(source_limitations))
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise ProfileError("正式导出缺少完整文件摘要。")
        actual = {item["path"]: item for item in inventory}
        artifact_map = {}
        for item in artifacts:
            if (not isinstance(item, dict) or not isinstance(item.get("path"), str) or
                    Path(item["path"]).is_absolute() or ".." in Path(item["path"]).parts):
                raise ProfileError("正式导出摘要格式无效。")
            if item["path"] == "manifest.json":
                # offline_review excludes the final, rewritten manifest from
                # the artifact list; old self-digests are never trusted.
                continue
            current = actual.get(item["path"])
            if (current is None or item.get("sha256") != current.get("sha256") or
                    item.get("bytes") != current.get("bytes")):
                raise ProfileError("正式导出文件摘要已变化，请重新复核。")
            artifact_map[item["path"]] = item
        if set(artifact_map) != set(actual) - {"manifest.json"}:
            raise ProfileError("正式导出文件未完整纳入摘要。")
        reviewed = review.get("reviewedArtifacts")
        if not isinstance(reviewed, list) or not reviewed:
            raise ProfileError("正式导出缺少已复核文件清单。")
        for item in reviewed:
            if isinstance(item, dict) and item.get("path") in _PUBLISH_REWRITTEN:
                # offline_review records the pending manifest before publish;
                # publish_review rewrites it with humanReview/artifacts, so
                # that one historical self-digest cannot be compared here.
                continue
            if (not isinstance(item, dict) or item.get("path") not in actual or
                    item.get("sha256") != actual[item["path"]]["sha256"] or
                    item.get("bytes") != actual[item["path"]]["bytes"]):
                raise ProfileError("已复核源文件发生变化，请重新导出。")
        checks = review.get("checks")
        if (not isinstance(checks, dict) or set(checks) != _REVIEW_CHECKS or
                any(value is not True for value in checks.values())):
            raise ProfileError("正式导出缺少完整人工复核检查。")
        if review.get("confirmationBasis") not in {"explicit_review_checklist", "explicit_batch_review_checklist"}:
            raise ProfileError("正式导出缺少明确人工复核依据。")
        manifest_review = manifest.get("humanReview", {})
        review_id = review.get("reviewId")
        if not isinstance(review_id, str) or manifest_review.get("reviewId") != review_id:
            raise ProfileError("正式导出复核记录不匹配。")
        if "chat" in review and review["chat"] != chat:
            raise ProfileError("正式导出会话身份与复核记录不一致。")
        for key in ("startDate", "endDate"):
            if review.get(key) != manifest.get(key):
                raise ProfileError("正式导出日期范围与复核记录不一致。")
        for key in ("client", "selfId", "accountKey", "peerId", "sourceFingerprint"):
            if key in review and key in manifest and review[key] != manifest[key]:
                raise ProfileError("正式导出账号来源与复核记录不一致。")
        required_files = {"messages.jsonl"}
        if require_analysis:
            required_files |= {"analysis/index.json", "analysis/corpus.jsonl"}
        reviewed_paths = {item.get("path") for item in reviewed if isinstance(item, dict)}
        if not required_files <= set(artifact_map) or not required_files <= reviewed_paths:
            raise ProfileError("正式导出缺少画像所需关键材料。")
        if client == "wechat" and peer_id != chat_id:
            raise ProfileError("个微私聊的对方身份必须与会话ID一致。")
        if client == "wecom" and peer_id != chat_id:
            members_path = root / "members.json"
            if not members_path.is_file() or "members.json" not in artifact_map or "members.json" not in reviewed_paths:
                raise ProfileError("企微会话与对方身份缺少已复核成员映射。")
            members_data = _read_json(members_path)
            members = members_data.get("members") if isinstance(members_data, dict) else None
            member_ids = [item.get("id") for item in members if isinstance(item, dict)] if isinstance(members, list) else []
            if (len(member_ids) != len(set(member_ids)) or set(member_ids) != {self_id, peer_id}):
                raise ProfileError("企微会话未证明只包含当前账号和指定对方。")
        message_rows = _load_lines(root / "messages.jsonl")
        corpus_rows = _load_lines(root / "analysis/corpus.jsonl")
        if require_analysis and not corpus_rows:
            raise ProfileError("正式导出缺少分析材料。")
        message_by_id = {}
        for row in message_rows:
            message_id = row.get("id")
            if message_id is None:
                message_id = row.get("messageId")
            normalized_message_id = _stable_message_id(message_id)
            if row.get("chatId") not in (None, chat_id):
                raise ProfileError("源消息跨越了其他会话。")
            sender = row.get("senderId")
            if self_id and sender and sender not in (self_id, peer_id):
                raise ProfileError("私聊源消息缺少可靠发送者身份。")
            direction = row.get("direction", "unknown")
            if self_id:
                if sender == peer_id and direction != "incoming":
                    raise ProfileError("对方消息的发送方向不一致。")
                if sender == self_id and direction != "outgoing":
                    raise ProfileError("本人消息的发送方向不一致。")
            elif direction != "unknown":
                # Without a reliable self identity, direction cannot safely
                # be attributed to either side of the conversation.
                raise ProfileError("缺少可靠本人身份时方向必须为unknown。")
            if not sender and direction != "unknown":
                raise ProfileError("发送者未知时方向必须为unknown。")
            if normalized_message_id in message_by_id:
                raise ProfileError("源消息稳定ID重复。")
            message_by_id[normalized_message_id] = row
        records = []
        analysis_refs: dict[str, Any] = {}
        analysis_rows: dict[str, dict[str, Any]] = {}
        seen = set()
        for number, corpus in enumerate(corpus_rows, 1):
            message_id = corpus.get("id")
            if message_id is None:
                message_id = corpus.get("messageId")
            normalized_message_id = _stable_message_id(message_id)
            if normalized_message_id not in message_by_id:
                raise ProfileError("分析材料引用了不存在的源消息。")
            row = {**message_by_id[normalized_message_id], **{
                key: corpus[key] for key in ("ref", "chunk") if key in corpus}}
            if corpus.get("chatId") not in (None, chat_id):
                raise ProfileError("分析材料跨越了其他会话。")
            if corpus.get("senderId") not in (None, row.get("senderId")):
                raise ProfileError("分析材料发送者与源消息不一致。")
            if corpus.get("direction") not in (None, row.get("direction", "unknown")):
                raise ProfileError("分析材料发送方向与源消息不一致。")
            key = _message_key(row, number)
            if key in seen:
                continue
            seen.add(key)
            analysis_rows[key] = corpus
            if not isinstance(row.get("ref"), (int, str)):
                raise ProfileError("分析材料缺少真实ref。")
            analysis_refs[normalized_message_id] = row["ref"]
            records.append({"key": key, "hash": _message_hash(row), "ref": row["ref"],
                            "time": row.get("time"), "senderId": row.get("senderId"),
                            "senderName": row.get("senderName", ""), "text": row.get("text", ""),
                            "kind": row.get("kind", ""), "direction": row.get("direction", "unknown"),
                            "chatId": chat_id, "messageId": normalized_message_id,
                            "chunk": row.get("chunk", "") or "", "file": "messages.jsonl",
                            "analysisFile": "analysis/corpus.jsonl"})
        analysis_index = _read_json(root / "analysis/index.json") if (root / "analysis/index.json").is_file() else {}
        chunks = analysis_index.get("chunks", []) if isinstance(analysis_index, dict) else []
        required_chunks = []
        chunk_refs: dict[str, list[Any]] = {}
        for chunk in chunks:
            if isinstance(chunk, dict):
                value = chunk.get("file")
                if isinstance(value, str):
                    chunk_path = Path(value)
                    artifact_path = value if value.replace("\\", "/").startswith("analysis/") else (Path("analysis") / chunk_path).as_posix()
                    if (chunk_path.is_absolute() or ".." in chunk_path.parts or
                            not (root / artifact_path).is_file() or artifact_path not in artifact_map):
                        raise ProfileError("分析分段路径无效或未纳入文件摘要。")
                    required_chunks.append(value)
                    refs = []
                    if isinstance(chunk.get("refs"), list):
                        refs = list(chunk["refs"])
                    chunk_refs[value] = refs
            elif isinstance(chunk, str):
                chunk_path = Path(chunk)
                artifact_path = chunk if chunk.replace("\\", "/").startswith("analysis/") else (Path("analysis") / chunk_path).as_posix()
                if (chunk_path.is_absolute() or ".." in chunk_path.parts or
                        not (root / artifact_path).is_file() or artifact_path not in artifact_map):
                    raise ProfileError("分析分段路径无效或未纳入文件摘要。")
                required_chunks.append(chunk)
        ranges = []
        for name, refs in chunk_refs.items():
            if (len(refs) == 2 and all(type(ref) is int for ref in refs)
                    and 0 < refs[0] <= refs[1]):
                ranges.append((refs[0], refs[1], name))
            elif len(refs) == 1 and type(refs[0]) is int and refs[0] > 0:
                ranges.append((refs[0], refs[0], name))
        ranges.sort()
        if records and not ranges:
            raise ProfileError("分析材料缺少已验证的分段引用范围。")
        if any(left[1] >= right[0] for left, right in zip(ranges, ranges[1:])):
            raise ProfileError("分析分段引用范围重叠。")
        starts = [item[0] for item in ranges]
        for row in records:
            if type(row["ref"]) is not int or row["ref"] < 1:
                raise ProfileError("分析引用必须是正整数。")
            position = bisect_right(starts, row["ref"]) - 1
            if position >= 0 and row["ref"] <= ranges[position][1]:
                mapped = ranges[position][2]
                if row.get("chunk") and row["chunk"] != mapped:
                    raise ProfileError("分析记录与分段索引不一致。")
                row["chunk"] = mapped
            elif ranges:
                raise ProfileError("分析记录不在分段引用范围内。")
        if len({row["ref"] for row in records}) != len(records):
            raise ProfileError("分析引用序号重复。")
        ref_to_message_id = {row["ref"]: row["messageId"] for row in records}
        analysis_hashes = {
            key: _analysis_hash(analysis_rows[key], ref_to_message_id)
            for key in analysis_rows
        }
        # Manifest/review timestamps and report text can vary between exports;
        # the digest is based on logical messages and analysis materials.
        material_files = {item["path"]: item["sha256"] for item in inventory
                          if item["path"] == "messages.jsonl" or item["path"].startswith("analysis/")}
        source_digest = _digest({"identity": identity, "records": records,
                                 "chunks": required_chunks, "files": material_files})
        # The corpus intentionally excludes emoji/media-only messages, while
        # the reviewed export and inspect_export contract cover the complete
        # messages.jsonl.  Derive the displayed coverage from every validated
        # source message so leading/trailing non-analysis records are not lost.
        return {"root": root, "manifest": manifest, "review": review, "identity": identity,
                "selfId": self_id, "chatId": chat_id,
                "inventory": inventory, "records": records, "chunks": sorted(set(required_chunks)),
                "chunkRefs": chunk_refs,
                "timeRange": _time_range(message_rows),
                "sourceDigest": source_digest, "chat": chat,
                "sourceLimitations": source_limitations,
                "messageRows": message_rows, "analysisRefs": analysis_refs,
                "analysisHashes": analysis_hashes}

    def inspect_export(self, export_root) -> dict[str, Any]:
        """Return validated read-only export statistics without creating state."""
        checked = self._formal_export(export_root, require_analysis=False)
        return {"identity": checked["identity"], "chat": checked["chat"],
                "reviewId": checked["review"].get("reviewId") or checked["review"].get("id"),
                "sourceDigest": checked["sourceDigest"],
                "sourceLimitations": list(checked.get("sourceLimitations", [])),
                "timeRange": checked["timeRange"],
                "messages": checked.get("messageRows", []),
                "analysisRefs": checked.get("analysisRefs", {})}

    def _source_path(self, source_id: str) -> Path:
        path = self.root / "sources" / (_valid_id(source_id, "src") + ".json")
        if _is_link(path):
            raise ProfileError("源材料索引不能是链接。")
        return path

    def _verify_source(self, source: dict[str, Any]) -> dict[str, Any]:
        source_id = _valid_id(source.get("sourceId"), "src")
        export_root = source.get("exportRoot")
        if not isinstance(export_root, str) or not export_root.strip():
            raise ProfileError("源材料导出目录路径无效。")
        try:
            root = Path(export_root)
        except (TypeError, ValueError, OSError) as exc:
            raise ProfileError("源材料导出目录路径无效。") from exc
        checked = self._formal_export(root)
        if export_root != str(checked["root"]):
            raise ProfileError("源材料导出目录路径不规范。")
        if checked["sourceDigest"] != source.get("sourceDigest"):
            raise ProfileError("源导出摘要已变化，请重新准备画像材料。")
        expected_review_id = checked["review"].get("reviewId") or checked["review"].get("id")
        expected = "src-" + _digest({"identity": checked["identity"],
                                     "sourceDigest": checked["sourceDigest"],
                                     "reviewId": expected_review_id})[:32]
        if source_id != expected or source.get("identity") != checked["identity"]:
            raise ProfileError("源材料索引身份不一致。")
        if source.get("reviewId") != expected_review_id:
            raise ProfileError("源材料索引复核标识不一致。")
        legacy_time_range = _time_range(checked.get("records", []))
        if (source.get("timeRange") != checked.get("timeRange") and
                source.get("timeRange") != legacy_time_range):
            raise ProfileError("源材料时间范围与已复核导出不一致。")
        if any(source.get(key) != checked.get(key) for key in
               ("records", "chunks", "chunkRefs", "selfId", "chatId", "sourceLimitations")):
            raise ProfileError("源材料索引与已复核导出不一致。")
        return checked

    def _prior_records(self, identity: dict[str, str]) -> dict[str, str]:
        result = {}
        current = self._read_current(identity)
        if not current or current.get("identity", identity) != identity:
            return result
        for source_id in current.get("sourceIds", []):
            value = _read_json(self._source_path(source_id))
            checked = self._verify_source(value)
            if checked.get("identity") == identity:
                result.update(self._delta_hashes(checked))
        return result

    @staticmethod
    def _delta_hashes(checked: dict[str, Any]) -> dict[str, str]:
        return {
            row["key"]: _digest({
                "recordHash": row["hash"],
                "analysisHash": checked["analysisHashes"][row["key"]],
            })
            for row in checked["records"]
        }

    @staticmethod
    def _request_seed(*, source: dict[str, Any], base_revision: str,
                      identity: dict[str, str], delta: dict[str, Any],
                      required_chunks: list[str]) -> dict[str, Any]:
        return {"source": {"sourceId": source["sourceId"],
                            "reviewId": source.get("reviewId"),
                            "sourceDigest": source["sourceDigest"],
                            "chatId": source.get("chatId"),
                            "timeRange": source.get("timeRange", {}),
                            "chunks": list(source.get("chunks", [])),
                            "allowedRefs": list(source.get("allowedRefs", [])),
                            "sourceLimitations": list(source.get("sourceLimitations", []))},
                "baseRevision": base_revision, "identity": identity,
                "delta": {"status": delta.get("status"), "mode": delta.get("mode"),
                          "new": list(delta.get("new", [])),
                          "overlap": list(delta.get("overlap", [])),
                          "changed": list(delta.get("changed", []))},
                "chunks": list(required_chunks)}

    def _attachment_supplement(self, export_root: Path, request_id: str,
                               selected_chunks: list[str], allowed_refs: list[Any]) -> Path | None:
        """Rebuild the request-scoped full-text handoff from verified corpus rows."""
        corpus_path = export_root / "analysis" / "corpus.jsonl"
        rows = _load_lines(corpus_path)
        allowed = {str(value) for value in allowed_refs}
        chunks = set(selected_chunks)
        selected = []
        for row in rows:
            ref = row.get("ref")
            if str(ref) not in allowed:
                continue
            chunk = row.get("chunk")
            if chunks and chunk and chunk not in chunks:
                continue
            if _has_long_attachment_text(row):
                selected.append(_json(row))
        path = self.root / "requests" / request_id / "附件全文补充.jsonl"
        if selected:
            _write_text(path, "\n".join(selected) + "\n")
            return path
        return None

    def prepare(self, export_root) -> dict[str, Any]:
        checked = self._formal_export(export_root)
        identity = checked["identity"]
        review_id = checked["review"].get("reviewId") or checked["review"].get("id")
        source_id = "src-" + _digest({"identity": identity, "sourceDigest": checked["sourceDigest"],
                                       "reviewId": review_id})[:32]
        source_path = self._source_path(source_id)
        source_time_range = checked.get("timeRange", {})
        if source_path.is_file():
            # Preserve the historical derived range when reopening a source
            # written by 1.0-1.2.  Its ID and any bound request were derived
            # from that range even though the underlying messages are the
            # same; rewriting it would strand the old request ID.
            previous_source = _read_json(source_path)
            self._verify_source(previous_source)
            source_time_range = previous_source.get("timeRange", source_time_range)
        source = {"schemaVersion": 1, "sourceId": source_id, "identity": identity,
                  "client": identity["client"], "accountKey": identity["accountKey"],
                  "peerId": identity["peerId"], "exportRoot": str(checked["root"]),
                  "sourceDigest": checked["sourceDigest"], "inventory": checked["inventory"],
                  "records": checked["records"], "chunks": checked["chunks"],
                  "chunkRefs": checked.get("chunkRefs", {}),
                  "timeRange": source_time_range, "chatId": checked.get("chatId"),
                  "selfId": checked.get("selfId", ""),
                  "reviewId": checked["review"].get("reviewId") or checked["review"].get("id"),
                  "sourceLimitations": checked.get("sourceLimitations", [])}
        prior = self._prior_records(identity)
        _write_json(self._source_path(source_id), source)
        current = self._delta_hashes(checked)
        new = sorted(key for key in current if key not in prior)
        overlap = sorted(key for key in current if key in prior and current[key] == prior[key])
        changed = sorted(key for key in current if key in prior and current[key] != prior[key])
        delta_status = "unchanged" if not new and not changed else "ready"
        delta_keys = set(new) | set(changed)
        delta_mode = "initial_full" if not prior else "changed_chunks"
        if not prior:
            selected_chunks = list(source.get("chunks", []))
        else:
            selected_chunks = sorted({row.get("chunk") for row in checked["records"]
                                      if row.get("key") in delta_keys and row.get("chunk")})
            # A legacy export may not retain chunk on corpus rows.  It is safer
            # to expose all material than to claim an incomplete delta.
            if delta_keys and not selected_chunks:
                selected_chunks = list(source.get("chunks", []))
                delta_mode = "full_material_no_chunk_mapping"
        source_records = checked["records"]
        mapped_records = [row for row in source_records if row.get("chunk") in selected_chunks]
        if source_records and not mapped_records and selected_chunks:
            # No per-row chunk metadata: this request is explicitly full
            # material, never a falsely narrow incremental request.
            allowed_refs = [row.get("ref") for row in source_records]
        else:
            allowed_refs = [row.get("ref") for row in mapped_records]
        current_profile = self._read_current(identity)
        base_revision = current_profile.get("revision", "") if current_profile else ""
        # Requests are deterministic for the same source and current base, so a
        # repeated prepare is safe and does not create duplicate work.
        source_item = {"sourceId": source_id, "reviewId": source.get("reviewId"),
                       "sourceDigest": source["sourceDigest"],
                       "chatId": checked.get("chatId"), "timeRange": source.get("timeRange", {}),
                       "chunks": selected_chunks, "allowedRefs": allowed_refs,
                       "sourceLimitations": checked.get("sourceLimitations", [])}
        request_seed = self._request_seed(
            source=source_item, base_revision=base_revision, identity=identity,
            delta={"status": delta_status, "mode": delta_mode, "new": new,
                   "overlap": overlap, "changed": changed}, required_chunks=selected_chunks)
        request_id = "req-" + _digest(request_seed)[:24]
        request_path = self.root / "requests" / (request_id + ".json")
        if _is_link(request_path):
            raise ProfileError("分析请求文件不能是链接。")
        if request_path.is_file():
            request = self._request(request_id)
        else:
            request = {"schemaVersion": 1, "requestId": request_id, **identity,
                       "baseRevision": base_revision,
                       "sources": [source_item],
                       "requiredChunks": selected_chunks,
                       "delta": {"status": delta_status, "mode": delta_mode, "new": new, "overlap": overlap,
                                 "changed": changed, "chunks": selected_chunks},
                       "status": delta_status, "sourceDigest": checked["sourceDigest"],
                       "sourceLimitations": checked.get("sourceLimitations", [])}
            _write_json(request_path, request)
        current_profile_file = None
        if current_profile:
            pointer = _read_json(self._current_pointer(identity))
            current_profile_file = str((self._current_pointer(identity).parent /
                                        pointer["profileFile"]).absolute())
        material_files = []
        for chunk in selected_chunks:
            chunk_path = Path(chunk)
            relative = chunk if chunk.replace("\\", "/").startswith("analysis/") else (Path("analysis") / chunk_path).as_posix()
            material_files.append(str((checked["root"] / relative).absolute()))
        supplement = self._attachment_supplement(checked["root"], request_id,
                                                  selected_chunks, allowed_refs)
        if supplement is not None:
            material_files.append(str(supplement.absolute()))
        material_notes = [
            "请读取materialFiles中的全部分析分段，并按request.sources[].allowedRefs保留真实证据引用。",
        ]
        if supplement is not None:
            material_notes.append(
                "附件全文补充文件只包含本次请求范围内正文超过3000字的完整corpus行；请一并读取并保留其中的ref、chunk及引用来源。")
        else:
            material_notes.append("本次请求范围内没有需要单独补充的超3000字附件正文。")
        return {**request, "requestFile": str(request_path),
                "sourceFiles": [str(self._source_path(source_id))],
                "materialFiles": material_files,
                "materialNotes": material_notes,
                "currentProfileFile": current_profile_file}

    def _request(self, request_id: str) -> dict[str, Any]:
        request_id = _valid_id(request_id, "req")
        path = self.root / "requests" / (request_id + ".json")
        if not path.is_file():
            raise ProfileError("分析请求不存在。")
        request = _read_json(path)
        if request.get("requestId") != request_id:
            raise ProfileError("分析请求标识不一致。")
        if request.get("schemaVersion") != SCHEMA_VERSION or not isinstance(request.get("baseRevision"), str):
            raise ProfileError("分析请求版本或基线字段损坏。")
        identity = {key: request.get(key) for key in ("client", "accountKey", "peerId")}
        _canonical_identity(identity)
        delta = request.get("delta")
        if (not isinstance(delta, dict) or
                not all(isinstance(delta.get(key), list) for key in ("new", "changed", "overlap")) or
                delta.get("status") not in {"ready", "unchanged"} or
                delta.get("mode") not in {"initial_full", "changed_chunks", "full_material_no_chunk_mapping"}):
            raise ProfileError("分析请求增量字段损坏。")
        if request.get("status") != delta.get("status"):
            raise ProfileError("分析请求状态与增量字段不一致。")
        required = request.get("requiredChunks")
        if not isinstance(required, list) or any(not isinstance(value, str) or Path(value).is_absolute() or ".." in Path(value).parts for value in required):
            raise ProfileError("分析请求分段字段损坏。")
        if required != list(dict.fromkeys(required)):
            raise ProfileError("分析请求分段字段重复。")
        if delta.get("chunks") != required:
            raise ProfileError("分析请求分段与增量字段不一致。")
        sources = request.get("sources")
        if not isinstance(sources, list) or len(sources) != 1 or not isinstance(sources[0], dict):
            raise ProfileError("分析请求必须恰好绑定一个源材料。")
        item = sources[0]
        source_id = _valid_id(item.get("sourceId"), "src")
        source_path = self._source_path(source_id)
        if not source_path.is_file():
            raise ProfileError("分析请求引用的源材料不存在。")
        source = _read_json(source_path)
        checked = self._verify_source(source)
        if source.get("sourceId") != source_id or checked.get("sourceDigest") != item.get("sourceDigest"):
            raise ProfileError("分析请求源摘要或标识不一致。")
        if checked.get("identity") != identity:
            raise ProfileError("分析请求身份与源材料不一致。")
        if any(chunk not in checked.get("chunks", []) for chunk in required):
            raise ProfileError("分析请求包含源材料不存在的分段。")
        if item.get("chunks") != required:
            raise ProfileError("分析请求源分段与请求覆盖范围不一致。")
        records = [row for row in checked.get("records", []) if isinstance(row, dict)]
        mapped = [row for row in records if row.get("chunk") in required]
        expected_allowed = ([row.get("ref") for row in records] if records and required and not mapped
                            else [row.get("ref") for row in mapped])
        legacy_time_range = _time_range(checked.get("records", []))
        if (item.get("timeRange") != checked.get("timeRange") and
                item.get("timeRange") != legacy_time_range):
            raise ProfileError("分析请求时间范围与canonical导出不一致。")
        expected_item = {"sourceId": source_id,
                         "reviewId": checked["review"].get("reviewId") or checked["review"].get("id"),
                         "sourceDigest": checked["sourceDigest"], "chunks": required,
                         "timeRange": item.get("timeRange"), "chatId": checked.get("chatId"),
                         "allowedRefs": expected_allowed,
                         "sourceLimitations": checked.get("sourceLimitations", [])}
        if item != expected_item:
            raise ProfileError("分析请求源材料元数据与canonical导出不一致。")
        if request.get("sourceDigest") != checked["sourceDigest"]:
            raise ProfileError("分析请求顶层源摘要不一致。")
        if request.get("sourceLimitations") != checked.get("sourceLimitations", []):
            raise ProfileError("分析请求源限制说明不一致。")
        expected = "req-" + _digest(self._request_seed(
            source=expected_item, base_revision=request["baseRevision"], identity=identity,
            delta=delta, required_chunks=required))[:24]
        if expected != request_id:
            raise ProfileError("分析请求内容与requestId摘要不一致。")
        return request

    request = _request

    def _source_refs(self, request: dict[str, Any]) -> dict[str, dict[Any, dict[str, Any]]]:
        result = {}
        for source_item in request.get("sources", []):
            source_id = _valid_id(source_item.get("sourceId"), "src")
            source = _read_json(self._source_path(source_id))
            checked_source = self._verify_source(source)
            refs: dict[Any, dict[str, Any]] = {}
            allowed_values = source_item.get("allowedRefs")
            allowed = set(allowed_values) if isinstance(allowed_values, list) else set()
            allowed.update(str(value) for value in list(allowed))
            for row in checked_source.get("records", []):
                if not isinstance(row, dict):
                    continue
                detail = {key: row.get(key) for key in
                          ("ref", "messageId", "time", "senderId", "senderName", "kind", "direction", "text", "chatId", "file", "analysisFile", "chunk")}
                ref = row.get("ref")
                if ref not in allowed and str(ref) not in allowed:
                    continue
                refs[ref] = detail
                try:
                    refs[int(ref)] = detail
                except (TypeError, ValueError):
                    pass
            result[source_id] = refs
        return result

    def _observation(self, item: Any, request: dict[str, Any], source_refs: dict[str, dict[Any, dict[str, Any]]]) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise ProfileError("候选观察项格式无效。")
        subject = item.get("subject")
        category = item.get("category")
        basis = item.get("basis")
        if subject not in _SUBJECTS or category not in _CATEGORIES or basis not in _BASES:
            raise ProfileError("候选观察项的subject、category或basis无效。")
        text = _text(item.get("text"), "候选观察内容")
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ProfileError("每条画像观察必须包含至少一个证据引用。")
        if len(evidence) > 100:
            raise ProfileError("单条观察的证据引用过多。")
        checked = []
        for citation in evidence:
            if not isinstance(citation, dict) or not isinstance(citation.get("sourceId"), str):
                raise ProfileError("证据引用格式无效。")
            source_id, ref = citation["sourceId"], citation.get("ref")
            if (source_id not in source_refs or isinstance(ref, bool) or
                    not isinstance(ref, (int, str)) or ref not in source_refs[source_id]):
                raise ProfileError("证据引用不存在或不属于本次请求。")
            detail = source_refs[source_id][ref]
            checked.append({"sourceId": source_id, **detail})
        if basis == "explicit" and subject in {"peer", "self"}:
            expected_direction = "incoming" if subject == "peer" else "outgoing"
            if not any(row.get("direction") == expected_direction and
                       row.get("kind") not in {"record", "system", "revoke"}
                       and (row.get("kind") != "quote" or row.get("text", "").strip())
                       for row in checked):
                raise ProfileError("明确表达须有该主体直接发言依据；仅有第三方转发、被引原文、系统事件或未知方向不能代替。")
        result = {"subject": subject, "category": category, "text": text, "basis": basis,
                  "evidence": checked}
        if "status" in item:
            result["status"] = _text(item["status"], "观察状态", limit=64)
        return result

    def stage(self, request_id, candidate) -> dict[str, Any]:
        request = self._request(request_id)
        if request.get("status") == "unchanged":
            raise ProfileError("本次材料没有新增或变更，无需生成画像候选。")
        if not isinstance(candidate, dict) or candidate.get("schemaVersion") != SCHEMA_VERSION:
            raise ProfileError("候选版本或结构无效。")
        if candidate.get("requestId") != request_id:
            raise ProfileError("候选未绑定当前分析请求。")
        if not isinstance(candidate.get("baseRevision"), str) or candidate.get("baseRevision") != request.get("baseRevision", ""):
            raise ProfileError("候选基线版本已过期。")
        covered = candidate.get("coveredChunks")
        required = request.get("requiredChunks", [])
        if not isinstance(covered, list) or sorted(set(covered)) != sorted(set(required)):
            raise ProfileError("候选未覆盖请求要求的全部分析分段。")
        limitations = candidate.get("limitations", [])
        if not isinstance(limitations, list) or len(limitations) > 100:
            raise ProfileError("候选限制说明格式无效。")
        if any(not isinstance(value, str) or len(value) > _MAX_TEXT for value in limitations):
            raise ProfileError("候选限制说明格式无效。")
        source_limitations = request.get("sourceLimitations", [])
        if (not isinstance(source_limitations, list) or
                any(not isinstance(value, str) or len(value) > _MAX_TEXT for value in source_limitations)):
            raise ProfileError("请求源限制说明格式无效。")
        limitations = list(dict.fromkeys(source_limitations + limitations))
        if len(limitations) > 100:
            raise ProfileError("候选限制说明格式无效。")
        source_refs = self._source_refs(request)
        observations = candidate.get("observations", [])
        if not isinstance(observations, list) or len(observations) > _MAX_OBSERVATIONS:
            raise ProfileError("候选观察数量超出限制。")
        observations = [self._observation(item, request, source_refs) for item in observations]
        normalized = {"schemaVersion": SCHEMA_VERSION, "requestId": request_id,
                      "baseRevision": request.get("baseRevision", ""),
                      "coveredChunks": sorted(set(covered)), "observations": observations,
                      "limitations": list(limitations)}
        candidate_id = "cand-" + _digest(normalized)[:32]
        candidate_path = self.root / "candidates" / (candidate_id + ".json")
        if _is_link(candidate_path):
            raise ProfileError("候选文件不能是链接。")
        identity = _canonical_identity(request)
        current = self._read_current(identity)
        current_revision = current.get("revision", "") if current else ""
        if current_revision != request.get("baseRevision", ""):
            if not (current and current.get("candidateId") == candidate_id and candidate_path.is_file()):
                raise ProfileError("当前画像已更新，请以新基线重新分析。")
        old_obs = current.get("observations", []) if current else []
        old_keys = {_digest(item) for item in old_obs}
        new_obs = [item for item in observations if _digest(item) not in old_keys]
        potential_conflicts = []
        for item in new_obs:
            related = [old for old in old_obs if old.get("subject") == item.get("subject") and
                       old.get("category") == item.get("category") and old.get("text") != item.get("text")]
            if related:
                potential_conflicts.append({"new": item, "existing": related,
                                            "status": "unresolved_for_human_review"})
        preview = {"candidateId": candidate_id, "requestId": request_id,
                   "baseRevision": normalized["baseRevision"], "identity": identity,
                   "status": "candidate", "newObservations": new_obs,
                   "existingObservations": old_obs, "limitations": list(limitations),
                   "sourceIds": [item["sourceId"] for item in request.get("sources", [])],
                   "potentialConflicts": potential_conflicts}
        value = {**normalized, "candidateId": candidate_id, "identity": identity,
                 "status": "candidate", "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "proposal": observations, "changes": new_obs, "previous": old_obs,
                 "potentialConflicts": potential_conflicts, "preview": preview}
        if candidate_path.is_file():
            existing = _read_json(candidate_path)
            if existing.get("observations") != observations or existing.get("coveredChunks") != normalized["coveredChunks"]:
                raise ProfileError("候选标识冲突，请重新生成候选。")
            value = existing
        else:
            _write_json(candidate_path, value)
        preview_path = candidate_path.with_suffix(".preview.json")
        _write_json(preview_path, preview)
        return {"candidateId": candidate_id, "status": value.get("status", "candidate"),
                "candidateFile": str(candidate_path), "previewFile": str(preview_path),
                "requestId": request_id, "baseRevision": normalized["baseRevision"],
                "diff": {"new": new_obs, "existing": old_obs,
                          "limitations": list(limitations), "potentialConflicts": potential_conflicts}}

    def candidate(self, candidate_id) -> dict[str, Any]:
        """Return a UI-ready candidate after rechecking its request and evidence."""
        candidate_id = _valid_id(candidate_id, "cand")
        path = self.root / "candidates" / (candidate_id + ".json")
        if _is_link(path):
            raise ProfileError("候选文件不能是链接。")
        if not path.is_file():
            raise ProfileError("候选不存在。")
        value = _read_json(path)
        if value.get("candidateId") != candidate_id:
            raise ProfileError("候选标识不一致。")
        self._validate_candidate_again(value)
        identity = _canonical_identity(value.get("identity", {}))
        current = self._read_current(identity)
        old = current.get("observations", []) if current else []
        changes = [item for item in value.get("observations", [])
                   if _digest(item) not in {_digest(row) for row in old}]
        return {**value, "previous": old, "changes": changes,
                "previewFile": str(path.with_suffix(".preview.json"))}

    # Alias kept for callers that prefer an explicit getter name.
    get_candidate = candidate

    def _validate_candidate_again(self, value: dict[str, Any], *, allow_published: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
        request = self._request(value.get("requestId", ""))
        if value.get("baseRevision", "") != request.get("baseRevision", ""):
            raise ProfileError("候选基线版本已过期。")
        identity = _canonical_identity(value.get("identity", {}))
        if any(request.get(key) != identity[key] for key in ("client", "accountKey", "peerId")):
            raise ProfileError("候选身份与请求身份不一致。")
        if value.get("status") not in {"candidate", "confirmed"}:
            raise ProfileError("候选状态不可确认。")
        if sorted(set(value.get("coveredChunks", []))) != sorted(set(request.get("requiredChunks", []))):
            raise ProfileError("候选覆盖范围已失效。")
        source_refs = self._source_refs(request)
        observations = value.get("observations")
        if not isinstance(observations, list) or len(observations) > _MAX_OBSERVATIONS:
            raise ProfileError("已保存候选观察数量无效。")
        for item in observations:
            self._observation(item, request, source_refs)
        candidate_limitations = value.get("limitations", [])
        source_limitations = request.get("sourceLimitations", [])
        if (not isinstance(candidate_limitations, list) or
                not isinstance(source_limitations, list) or
                any(not isinstance(item, str) or len(item) > _MAX_TEXT
                    for item in candidate_limitations + source_limitations)):
            raise ProfileError("已保存候选限制说明无效。")
        limitations = list(dict.fromkeys(source_limitations + candidate_limitations))
        if candidate_limitations != limitations:
            raise ProfileError("已保存候选缺少源限制说明。")
        normalized = {"schemaVersion": SCHEMA_VERSION, "requestId": request["requestId"],
                      "baseRevision": request.get("baseRevision", ""),
                      "coveredChunks": sorted(set(value.get("coveredChunks", []))),
                      "observations": [self._observation(item, request, source_refs) for item in observations],
                      "limitations": limitations}
        expected_id = "cand-" + _digest(normalized)[:32]
        if value.get("candidateId") != expected_id:
            raise ProfileError("候选内容与candidateId摘要不一致。")
        current = self._read_current(identity)
        if (current.get("revision", "") if current else "") != request.get("baseRevision", ""):
            if not ((value.get("status") == "confirmed" or allow_published) and
                    current and current.get("candidateId") == value.get("candidateId")):
                raise ProfileError("当前画像已更新，请以新基线重新分析。")
        return request, identity

    @staticmethod
    def _markdown(profile: dict[str, Any]) -> str:
        lines = ["# 私聊画像", "", f"客户端：{profile['client']}；账号：{profile['accountKey']}；对方ID：{profile['peerId']}",
                 f"版本：{profile['revision']}；确认人：{profile.get('confirmedBy', '')}",
                 f"覆盖时间：{profile.get('timeRange', {}).get('start') or '未知'} 至 {profile.get('timeRange', {}).get('end') or '未知'}", ""]
        for subject in ("peer", "self", "relationship"):
            rows = [item for item in profile.get("observations", []) if item.get("subject") == subject]
            if not rows:
                continue
            lines += [f"## {subject}", ""]
            for item in rows:
                cites = "、".join(f"{e['sourceId']}#{e['ref']}" for e in item.get("evidence", []))
                lines.append(f"- [{item['category']}/{item['basis']}] {item['text']}（依据：{cites}）")
            lines.append("")
        if profile.get("limitations"):
            lines += ["## 限制", ""] + [f"- {value}" for value in profile["limitations"]] + [""]
        return "\n".join(lines)

    def confirm(self, candidate_id, *, confirmed_by, note="") -> dict[str, Any]:
        candidate_id = _valid_id(candidate_id, "cand")
        confirmed_by = _text(confirmed_by, "confirmed_by", limit=256)
        note = _text(note, "note", required=False, limit=_MAX_NOTE)
        with _FileLock(self.root, "confirm"):
            return self._confirm_locked(candidate_id, confirmed_by=confirmed_by, note=note)

    def _confirm_locked(self, candidate_id, *, confirmed_by, note="") -> dict[str, Any]:
        candidate_path = self.root / "candidates" / (candidate_id + ".json")
        if _is_link(candidate_path):
            raise ProfileError("候选文件不能是链接。")
        if not candidate_path.is_file():
            raise ProfileError("候选不存在。")
        candidate = _read_json(candidate_path)
        candidate_identity = _canonical_identity(candidate.get("identity", {}))
        existing_current = self._read_current(candidate_identity)
        if existing_current and existing_current.get("candidateId") == candidate_id:
            # A crash after pointer publication but before the candidate receipt
            # is recoverable and remains idempotent; still recheck source files.
            self._validate_candidate_again(candidate, allow_published=True)
            candidate["status"] = "confirmed"
            candidate["confirmedBy"] = existing_current.get("confirmedBy", "")
            candidate["confirmedAt"] = existing_current.get("confirmedAt", "")
            _write_json(candidate_path, candidate)
            return {**existing_current, "profile": existing_current, "status": "confirmed"}
        # A confirmed candidate is an idempotent receipt.  Read it back before
        # comparing the request base with the now-advanced current revision.
        if candidate.get("status") == "confirmed":
            identity = candidate_identity
            profile = self._read_current(identity)
            if profile and profile.get("candidateId") == candidate_id:
                return {**profile, "profile": profile, "status": "confirmed"}
        request, identity = self._validate_candidate_again(candidate)
        old = self._read_current(identity)
        old_obs = old.get("observations", []) if old else []
        merged = list(old_obs)
        seen = {_digest(item) for item in merged}
        for item in candidate.get("observations", []):
            if _digest(item) not in seen:
                merged.append(item)
                seen.add(_digest(item))
        revision = "rev-" + _digest({"candidateId": candidate_id, "base": request.get("baseRevision", "")})[:32]
        # Keep a unique source set, moving each newly confirmed source to the
        # end.  Reusing an older reviewed batch after a newer batch must make
        # that older batch authoritative for the next incremental comparison.
        source_ids = list(dict.fromkeys((old or {}).get("sourceIds", [])))
        for item in request.get("sources", []):
            source_id = item["sourceId"]
            source_ids = [value for value in source_ids if value != source_id]
            source_ids.append(source_id)
        coverage = list((old or {}).get("coverage", []))
        for item in request.get("sources", []):
            source = _read_json(self._source_path(item["sourceId"]))
            checked_source = self._verify_source(source)
            entry = {"sourceId": item["sourceId"], "chatId": checked_source.get("chatId"),
                     "chunks": item.get("chunks", []), "timeRange": checked_source.get("timeRange", {})}
            if entry not in coverage:
                coverage.append(entry)
        times = [value for entry in coverage for value in (entry.get("timeRange", {}).get("start"),
                                                            entry.get("timeRange", {}).get("end")) if value]
        limitations = list(dict.fromkeys((old or {}).get("limitations", []) + candidate.get("limitations", [])))
        if candidate.get("potentialConflicts"):
            limitations.append("同一subject/category下存在内容不同的观察；未自动判定冲突，保留给人工复核。")
        profile = {"schemaVersion": 1, **identity, "revision": revision,
                   "previousRevision": old.get("revision", "") if old else "",
                   "candidateId": candidate_id, "requestId": candidate.get("requestId"),
                   "confirmedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "confirmedBy": confirmed_by, "note": note, "observations": merged,
                   "limitations": limitations,
                   "sourceIds": source_ids, "coverage": coverage,
                   "timeRange": {"start": min(times) if times else None, "end": max(times) if times else None}}
        directory = self._profile_dir(identity)
        revision_dir = directory / "revisions" / revision
        if revision_dir.exists():
            if (_is_link(revision_dir) or not revision_dir.is_dir() or
                    any(_is_link(revision_dir / name) or not (revision_dir / name).is_file()
                        for name in ("profile.json", "profile.md", "confirmation.json"))):
                raise ProfileError("已存在的画像版本目录不完整或不安全。")
            existing = _read_json(revision_dir / "profile.json")
            confirmation = _read_json(revision_dir / "confirmation.json")
            try:
                existing_confirmed_at = _text(existing.get("confirmedAt"), "confirmedAt", limit=128)
            except ProfileError as exc:
                raise ProfileError("已存在的画像版本缺少有效确认时间。") from exc
            expected_existing = {**profile, "confirmedAt": existing_confirmed_at}
            expected_confirmation = {"candidateId": candidate_id,
                                     "confirmedBy": profile["confirmedBy"],
                                     "confirmedAt": existing_confirmed_at,
                                     "note": profile["note"]}
            if existing != expected_existing or confirmation != expected_confirmation:
                raise ProfileError("已存在的画像版本内容不一致，拒绝恢复。")
            try:
                markdown = _read_limited(revision_dir / "profile.md", _MAX_FILE_BYTES).decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
            except (OSError, UnicodeError) as exc:
                raise ProfileError("已存在的画像版本Markdown无法读取。") from exc
            if markdown != self._markdown(existing):
                raise ProfileError("已存在的画像版本Markdown不一致。")
            profile = existing
        else:
            directory.mkdir(parents=True, exist_ok=True)
            temp = Path(tempfile.mkdtemp(prefix=".profile-", dir=str(directory)))
            try:
                rev_temp = temp / revision
                rev_temp.mkdir(parents=True)
                _write_json(rev_temp / "profile.json", profile)
                (rev_temp / "profile.md").write_text(self._markdown(profile), encoding="utf-8")
                _write_json(rev_temp / "confirmation.json", {"candidateId": candidate_id,
                           "confirmedBy": confirmed_by, "confirmedAt": profile["confirmedAt"], "note": note})
                revision_dir.parent.mkdir(parents=True, exist_ok=True)
                os.replace(rev_temp, revision_dir)
            finally:
                if temp.exists():
                    shutil.rmtree(temp, ignore_errors=True)
        profile_rel = f"revisions/{revision}/profile.json"
        markdown_rel = f"revisions/{revision}/profile.md"
        confirmation_rel = f"revisions/{revision}/confirmation.json"
        pointer = {"schemaVersion": 1, "identity": identity, "revision": revision,
                   "candidateId": profile["candidateId"], "requestId": profile["requestId"],
                   "profileFile": profile_rel, "markdownFile": markdown_rel,
                   "confirmationFile": confirmation_rel,
                   "sha256": {"profileFile": _sha256_file(directory / profile_rel),
                              "markdownFile": _sha256_file(directory / markdown_rel),
                              "confirmationFile": _sha256_file(directory / confirmation_rel)}}
        pointer_path = self._current_pointer(identity)
        old_pointer = (_read_limited(pointer_path, _MAX_FILE_BYTES)
                       if pointer_path.is_file() and not _is_link(pointer_path) else None)
        try:
            _write_json(pointer_path, pointer)
            candidate["status"] = "confirmed"
            candidate["confirmedBy"] = confirmed_by
            candidate["confirmedAt"] = profile["confirmedAt"]
            _write_json(candidate_path, candidate)
        except Exception:
            # A failed candidate receipt must not leave a new current pointer.
            try:
                if old_pointer is None:
                    if pointer_path.exists() and not _is_link(pointer_path):
                        pointer_path.unlink()
                else:
                    temp = pointer_path.with_name("." + pointer_path.name + ".rollback.tmp")
                    temp.write_bytes(old_pointer)
                    os.replace(temp, pointer_path)
            except OSError as rollback_error:
                raise ProfileError("画像发布失败且回滚失败，请保留历史版本并人工恢复指针。") from rollback_error
            raise
        return {**profile, "profile": profile, "status": "confirmed",
                "profileFile": str(directory / pointer["profileFile"]),
                "markdownFile": str(directory / pointer["markdownFile"])}

    def current(self, client, account_key, peer_id) -> dict[str, Any] | None:
        identity = _canonical_identity({"client": client, "accountKey": account_key, "peerId": peer_id})
        return self._read_current(identity)

    def overview(self, client, account_key) -> dict[str, Any]:
        with _FileLock(self.root, "overview"):
            return self._overview_locked(client, account_key)

    def _overview_locked(self, client, account_key) -> dict[str, Any]:
        client = _text(client, "client", limit=32).lower()
        account_key = _text(account_key, "accountKey", limit=512)
        profiles = []
        for directory in (self.root / "profiles").iterdir():
            if _is_link(directory) or not directory.is_dir() or directory.name == "overview":
                continue
            pointer = directory / "current.json"
            if _is_link(pointer):
                raise ProfileError("当前画像指针不能是链接。")
            if not pointer.is_file():
                continue
            # An unreadable pointer cannot prove that it belongs to another
            # account, so fail closed.  Only a valid pointer whose directory
            # hash matches its identity can be treated as foreign and skipped
            # without reading that account's version contents.
            pointer_data = _read_json(pointer)
            identity = _canonical_identity(pointer_data.get("identity", {}))
            if self._identity_key(identity) != directory.name:
                raise ProfileError("当前画像目录与指针身份不一致。")
            if identity.get("client") != client or identity.get("accountKey") != account_key:
                continue
            profile = self._read_current(identity)
            if profile is None:
                raise ProfileError("当前画像指针缺少版本内容。")
            profiles.append(profile)
        observations, seen = [], set()
        coverage = []
        for profile in sorted(profiles, key=lambda item: item.get("peerId", "")):
            coverage.append({"peerId": profile["peerId"], "revision": profile["revision"],
                             "sourceIds": profile.get("sourceIds", []),
                             "timeRange": profile.get("timeRange", {}),
                             "coverage": profile.get("coverage", [])})
            for item in profile.get("observations", []):
                if item.get("subject") not in {"self", "relationship"}:
                    continue
                value = {**item, "peerId": profile["peerId"], "revision": profile["revision"]}
                key = _digest(value)
                if key not in seen:
                    seen.add(key)
                    observations.append(value)
        overview = {"schemaVersion": 1, "client": client, "accountKey": account_key,
                    "observations": observations, "coverage": coverage,
                    "contacts": [item["peerId"] for item in coverage],
                    "timeRange": {"start": min((item.get("timeRange", {}).get("start") for item in coverage
                                                  if item.get("timeRange", {}).get("start")), default=None),
                                  "end": max((item.get("timeRange", {}).get("end") for item in coverage
                                                if item.get("timeRange", {}).get("end")), default=None)},
                    "limitations": ["本人总览仅汇编当前账号下已确认画像中的self/relationship观察；不代表未覆盖联系人。"]}
        overview_dir = self.root / "profiles" / "overview"
        if overview_dir.exists() and (_is_link(overview_dir) or not overview_dir.is_dir()):
            raise ProfileError("本人总览目录不安全。")
        overview_dir.mkdir(parents=True, exist_ok=True)
        target = overview_dir / _digest({"client": client, "accountKey": account_key})
        json_path, markdown_path = target.with_suffix(".json"), target.with_suffix(".md")
        if _is_link(json_path) or _is_link(markdown_path):
            raise ProfileError("本人总览文件不能是链接。")
        old_json = _read_limited(json_path, _MAX_FILE_BYTES) if json_path.is_file() else None
        old_markdown = _read_limited(markdown_path, _MAX_FILE_BYTES) if markdown_path.is_file() else None
        markdown = ("# 本人沟通习惯总览\n\n" +
                    f"客户端：{client}；账号：{account_key}\n\n" +
                    "\n".join(f"- [{item['peerId']}/{item['category']}/{item['basis']}] {item['text']}" for item in observations) +
                    "\n\n覆盖联系人：" + "、".join(item["peerId"] for item in coverage) + "\n")
        try:
            _write_json(json_path, overview)
            _write_text(markdown_path, markdown)
        except Exception as exc:
            try:
                _restore_bytes(json_path, old_json)
                _restore_bytes(markdown_path, old_markdown)
            except OSError as rollback_error:
                raise ProfileError("本人总览写入失败且回滚失败，请保留历史文件并人工恢复。") from rollback_error
            if isinstance(exc, ProfileError):
                raise
            raise ProfileError("本人总览写入失败，未保留不完整文件。") from exc
        return {**overview, "jsonFile": str(json_path), "markdownFile": str(markdown_path)}

    def link(self, identities, *, confirmed_by, note="") -> dict[str, Any]:
        if not isinstance(identities, (list, tuple)) or len(identities) < 2:
            raise ProfileError("身份关联至少需要两个已建档身份。")
        normalized = []
        for value in identities:
            identity = _canonical_identity(value)
            if self._read_current(identity) is None:
                raise ProfileError("只能关联已有正式画像的身份。")
            normalized.append(identity)
        keys = sorted({self._identity_key(value) for value in normalized})
        if len(keys) < 2:
            raise ProfileError("身份关联至少需要两个不同身份。")
        confirmed_by = _text(confirmed_by, "confirmed_by", limit=256)
        note = _text(note, "note", required=False, limit=_MAX_NOTE)
        link_id = "link-" + _digest(keys)[:32]
        path = self.root / "links" / (link_id + ".json")
        if _is_link(path):
            raise ProfileError("身份关联文件不能是链接。")
        if path.is_file():
            return _read_json(path)
        value = {"schemaVersion": 1, "linkId": link_id, "status": "confirmed",
                 "identities": normalized, "identityKeys": keys,
                 "confirmedBy": confirmed_by, "note": note,
                 "confirmedAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        _write_json(path, value)
        return {**value, "file": str(path)}


__all__ = ["ProfileError", "ProfileStore"]
