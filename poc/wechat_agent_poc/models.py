from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Mapping
import re

SCHEMA_VERSION = "1"
MODES = ("offline", "read_only", "draft_only", "manual_send")
ACTIONS = ("draft", "ignore", "needs_review")
TASK_STATUSES = (
    "draft",
    "needs_review",
    "approved",
    "sending",
    "verified",
    "rejected",
    "expired",
    "failed",
    "uncertain",
)
IS_SELF = ("true", "false", "unknown")
IDENTITY = ("resolved", "unknown", "ambiguous")
PARSE_STATUS = ("ok", "decode_error", "missing_decoder")
MENTION_SELF = ("true", "false", "unknown")

Mode = Literal["offline", "read_only", "draft_only", "manual_send"]
Action = Literal["draft", "ignore", "needs_review"]
TaskStatus = Literal[
    "draft",
    "needs_review",
    "approved",
    "sending",
    "verified",
    "rejected",
    "expired",
    "failed",
    "uncertain",
]
IsSelf = Literal["true", "false", "unknown"]
IdentityStatus = Literal["resolved", "unknown", "ambiguous"]
ParseStatus = Literal["ok", "decode_error", "missing_decoder"]
MentionSelf = Literal["true", "false", "unknown"]


@dataclass(frozen=True)
class Binding:
    account_alias: str
    account_wxid: str
    group_alias: str
    conversation_key: str
    binding_version: str
    member_features: tuple[str, ...]
    display_name: str
    required_detail_tokens: tuple[str, ...]
    self_sender_key: str = ""

    def matches_window(self, window: Mapping[str, Any]) -> tuple[bool, str]:
        if window.get("account_wxid") != self.account_wxid and window.get("account_alias") != self.account_alias:
            return False, "account_mismatch"
        name = str(window.get("display_name") or "")
        located = str(window.get("located_by") or "")
        if located == "uia_selected_chat_title":
            observed = re.sub(r"\s*\(\d+\)\s*$", "", name).strip()
            if observed != self.display_name:
                return False, "group_renamed"
            if window.get("focus") is False:
                return False, "focus_changed"
            conversation = str(window.get("conversation_key") or "")
            if conversation and conversation != self.conversation_key:
                return False, "conversation_mismatch"
            if window.get("rebuilt") is True:
                return False, "window_rebuilt"
            return True, "ok"
        if name and name != self.display_name:
            return False, "group_renamed"
        details = str(window.get("details") or "")
        missing = [token for token in self.required_detail_tokens if token and token not in details and token not in name]
        members = set(window.get("member_features") or [])
        missing_members = [item for item in self.member_features if item not in members]
        conversation = str(window.get("conversation_key") or "")
        if conversation and conversation != self.conversation_key:
            return False, "conversation_mismatch"
        if window.get("focus") is False:
            return False, "focus_changed"
        if window.get("rebuilt") is True:
            return False, "window_rebuilt"
        if missing:
            return False, "group_details_incomplete"
        if missing_members:
            return False, "member_features_incomplete"
        if window.get("located_by") == "name_search_only":
            return False, "name_search_insufficient"
        return True, "ok"


@dataclass
class Event:
    schema_version: str
    event_key: str
    account_alias: str
    conversation_key: str
    source_shard: str
    source_table: str
    source_message_id: int | None
    sender_key: str | None
    is_self: IsSelf
    source_time: int | None
    observed_at: str
    text: str | None
    evidence_ref: str | None
    identity_status: IdentityStatus
    is_historical: bool
    parse_status: ParseStatus = "ok"
    is_replay: bool = False
    runtime_id: str | None = None
    halt_reason: str | None = None
    mention_self: MentionSelf = "unknown"
    mentioned_keys: tuple[str, ...] = ()
    mention_all: bool = False
    mention_field: str | None = None
    mention_reason: str = "mention_field_absent"

    def eligible_for_reply(self) -> tuple[bool, str]:
        if self.halt_reason:
            return False, self.halt_reason
        if self.is_historical:
            return False, "historical"
        if self.is_replay:
            return False, "replay"
        if self.is_self != "false":
            return False, "self_or_unknown"
        if self.identity_status != "resolved":
            return False, "identity_unresolved"
        if self.source_message_id is None:
            return False, "missing_native_id"
        if self.parse_status != "ok" or self.text is None:
            return False, "undecodable_text"
        return True, "ok"

    def eligible_for_mention_reply(self) -> tuple[bool, str]:
        ok, reason = self.eligible_for_reply()
        if not ok:
            return False, reason
        if self.mention_self == "unknown":
            return False, "mention_unknown"
        if self.mention_all:
            return False, "mention_all"
        if self.mention_self != "true":
            return False, "mention_not_self_only"
        if len(self.mentioned_keys) != 1:
            return False, "mention_not_self_only"
        return True, "ok"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Event:
        return cls(
            schema_version=str(data.get("schema_version") or SCHEMA_VERSION),
            event_key=str(data["event_key"]),
            account_alias=str(data["account_alias"]),
            conversation_key=str(data["conversation_key"]),
            source_shard=str(data["source_shard"]),
            source_table=str(data["source_table"]),
            source_message_id=_optional_int(data.get("source_message_id")),
            sender_key=_optional_str(data.get("sender_key")),
            is_self=_is_self(data.get("is_self")),
            source_time=_optional_int(data.get("source_time")),
            observed_at=str(data["observed_at"]),
            text=_optional_str(data.get("text")),
            evidence_ref=_optional_str(data.get("evidence_ref")),
            identity_status=_identity(data.get("identity_status")),
            is_historical=bool(data.get("is_historical")),
            parse_status=_parse_status(data.get("parse_status")),
            is_replay=bool(data.get("is_replay")),
            runtime_id=_optional_str(data.get("runtime_id")),
            halt_reason=_optional_str(data.get("halt_reason")),
            mention_self=_mention_self(data.get("mention_self")),
            mentioned_keys=tuple(str(item) for item in (data.get("mentioned_keys") or ())),
            mention_all=bool(data.get("mention_all")),
            mention_field=_optional_str(data.get("mention_field")),
            mention_reason=str(data.get("mention_reason") or "mention_field_absent"),
        )


@dataclass
class Decision:
    action: Action
    text: str | None
    evidence_keys: list[str]
    reason: str
    event_key: str
    conversation_key: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WindowState:
    account_alias: str
    account_wxid: str
    display_name: str
    conversation_key: str
    details: str
    member_features: tuple[str, ...]
    focus: bool = True
    rebuilt: bool = False
    located_by: str = "binding_map"

    def as_mapping(self) -> dict[str, Any]:
        return {
            "account_alias": self.account_alias,
            "account_wxid": self.account_wxid,
            "display_name": self.display_name,
            "conversation_key": self.conversation_key,
            "details": self.details,
            "member_features": list(self.member_features),
            "focus": self.focus,
            "rebuilt": self.rebuilt,
            "located_by": self.located_by,
        }


@dataclass
class SendResult:
    status: Literal["not_sent", "local_ok", "unknown"]
    note: str = ""
    local_bubble: bool = False


@dataclass
class ReceiverEvidence:
    wecom_confirmed: bool
    wechat_confirmed: bool
    note: str = ""
    recorded_by: str = ""


@dataclass
class Halt:
    reason: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


class HaltError(RuntimeError):
    def __init__(self, halt: Halt):
        super().__init__(halt.message)
        self.halt = halt


def event_key(
    account: str,
    shard: str,
    table: str,
    message_id: int | None,
    conversation_key: str = "",
) -> str:
    native = "null" if message_id is None else str(int(message_id))
    group = conversation_key or "-"
    return f"{account}|{group}|{shard}|{table}|{native}"


def _optional_int(value: Any) -> int | None:
    if value is None or value == "" or value == "null":
        return None
    return int(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text


def _is_self(value: Any) -> IsSelf:
    if value is True or value == "true":
        return "true"
    if value is False or value == "false":
        return "false"
    return "unknown"


def _identity(value: Any) -> IdentityStatus:
    text = str(value or "unknown")
    if text in IDENTITY:
        return text  # type: ignore[return-value]
    return "unknown"


def _parse_status(value: Any) -> ParseStatus:
    text = str(value or "ok")
    if text in PARSE_STATUS:
        return text  # type: ignore[return-value]
    return "ok"


def _mention_self(value: Any) -> MentionSelf:
    text = str(value or "unknown")
    if text in MENTION_SELF:
        return text  # type: ignore[return-value]
    return "unknown"
