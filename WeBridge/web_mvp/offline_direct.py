"""Helpers for selecting supported one-to-one conversations.

The database adapters expose both group and direct conversation rows.  This
module keeps the policy boundary in one place: a non-group row is not enough
to prove that it is a supported person-to-person conversation, and known
system/public-service accounts are excluded from the selectable list.
"""

import json
from pathlib import Path
import sys

from offline_chat import ArchiveError


SYSTEM_IDS = frozenset({
    'filehelper', 'fmessage', 'floatbottle', 'medianote', 'newsapp',
    'weixin', 'weixingzhushou', 'qqmail', 'notification', 'mphelper',
})
SYSTEM_PREFIXES = ('gh_', 'sys_', 'service_', 'notification_')
NON_DIRECT_TYPES = frozenset({'group', 'chatroom', 'room', 'multi', 'multi_chat'})


def validate_conversation_mode(value):
    if value not in ('group', 'direct'):
        raise ArchiveError("会话模式必须为 group 或 direct。")
    return value


def resolve_packaged_mode(requested=None):
    """Honor the mode of a private Skill even when its EXE is opened directly."""
    if requested is not None:
        validate_conversation_mode(requested)
    if getattr(sys, 'frozen', False):
        executable = Path(sys.executable).resolve()
        root = executable.parent.parent if executable.parent.name.lower() == 'bin' else executable.parent
        manifest = root / 'runtime.json'
        if manifest.is_file():
            try:
                if manifest.stat().st_size > 65536:
                    raise ValueError('oversized runtime')
                with manifest.open('rb') as stream:
                    content = stream.read(65537)
                if len(content) > 65536:
                    raise ValueError('oversized runtime')
                runtime = json.loads(content.decode('utf-8-sig'))
                if not isinstance(runtime, dict):
                    raise ValueError('invalid runtime')
                if runtime.get('privateAnalysis') is True:
                    if runtime.get('conversationMode') != 'direct' or requested not in (None, 'direct'):
                        raise ArchiveError('此私聊 Skill 固定使用 direct 模式，请使用原群聊入口处理群聊。')
                    return 'direct'
            except (OSError, ValueError, AttributeError, RecursionError) as error:
                if isinstance(error, ArchiveError):
                    raise
                raise ArchiveError('无法校验私聊运行清单，请使用完整交付包。') from error
    return requested or 'group'


def system_account_reason(row):
    """Return a stable exclusion reason, or ``None`` for a person candidate."""
    identifier = str(row.get('id') or '').strip().casefold()
    if identifier in SYSTEM_IDS or identifier.startswith(SYSTEM_PREFIXES):
        return 'system_or_public_account'
    conversation_type = str(row.get('conversationType') or '').strip().casefold()
    if conversation_type in ('system', 'service', 'public_account', 'customer_service'):
        return 'system_or_public_account'
    return None


def is_supported_direct(row):
    """Whether a conversation row can be selected by the private-chat UI."""
    conversation_type = str(row.get('conversationType') or '').strip().casefold()
    return (row.get('conversationKind') == 'direct' and
            conversation_type not in NON_DIRECT_TYPES and
            not system_account_reason(row))


def decorate(row, archive=None):
    """Add conservative identity fields without guessing the relationship."""
    value = dict(row)
    value['conversationMode'] = 'direct' if row.get('conversationKind') == 'direct' else 'group'
    if row.get('conversationKind') == 'direct':
        value['relationship'] = row.get('relationship') or 'unknown'
        value['relationshipBasis'] = row.get('relationshipBasis') or 'unverified'
        value['identityStatus'] = row.get('identityStatus') or 'unknown'
        value['peerId'] = None
        if archive is not None:
            state = getattr(archive, 'state', {}) or {}
            own = state.get('self_id', '')
            cid = str(row.get('id') or '')
            members = (getattr(archive, 'member_names', {}) or {}).get(cid, {})
            other = set(members) - {own}
            if own and own in members and len(members) == 2 and len(other) == 1:
                value.update(peerId=next(iter(other)), identityStatus='verified_participant_mapping',
                             directEvidence='conversation_user_table')
            elif cid != own and cid in (state.get('contacts') or {}):
                value.update(peerId=cid, identityStatus='contact_mapping', directEvidence='contact_index')
    return value


def _has_person_evidence(archive, row):
    """Require contact/mapping evidence before exposing a direct candidate."""
    if row.get('directEvidence') or row.get('contactKnown') is True:
        return True
    identifier = str(row.get('id') or '')
    state = getattr(archive, 'state', {}) or {}
    contacts = state.get('contacts', {}) or {}
    if identifier in contacts:
        return True
    member_names = getattr(archive, 'member_names', {}) or {}
    members = member_names.get(identifier)
    if isinstance(members, dict) and 1 <= len(members) <= 2:
        return True
    # A display name that differs from the opaque identifier is an adapter
    # supplied mapping, provided the adapter marked its origin.
    return row.get('nameBasis') in ('contact', 'user_table', 'conversation_user_table')


def conversations(archive, mode):
    """List only conversations belonging to ``mode``.

    The adapter is asked for all rows so a caller cannot accidentally treat an
    adapter's group-only default as a complete direct list.
    """
    validate_conversation_mode(mode)
    rows = archive.groups(include_direct=True).get('conversations', [])
    if mode == 'group':
        return [decorate(row) for row in rows if row.get('conversationKind') == 'group']
    result = []
    for row in rows:
        if is_supported_direct(row) and _has_person_evidence(archive, row):
            value = decorate(row, archive)
            if not value.get('peerId'):
                continue
            if row.get('id') == (getattr(archive, 'state', {}) or {}).get('self_id'):
                continue
            result.append(value)
    return result


def select(archive, *, chat_id=None, chat_name=None, mode='group'):
    """Select a conversation and enforce mode/name semantics.

    Direct names are exact and unique.  The group adapter's historical suffix
    fallback is intentionally unavailable to direct selections.
    """
    validate_conversation_mode(mode)
    if mode == 'direct' and chat_name is not None and not isinstance(chat_name, str):
        raise ArchiveError('私聊联系人名称必须为文本。')
    if mode == 'group':
        # Keep the established group selector, including its explicit suffix
        # compatibility, while still rejecting a direct ID in group mode.
        selected = archive.select_chat(chat_id=chat_id, chat_name=chat_name)
        if selected.get('conversationKind') != 'group':
            raise ArchiveError('请选择当前账号的群聊 ID。')
        return decorate(selected)
    rows = conversations(archive, mode)
    if chat_id is not None:
        matches = [row for row in rows if row.get('id') == chat_id]
    elif chat_name is not None:
        matches = [row for row in rows if row.get('name') == chat_name]
    else:
        matches = []
    if len(matches) != 1:
        label = '私聊联系人' if mode == 'direct' else '群聊'
        raise ArchiveError(f'{label}不存在或名称重复，请使用唯一的会话 ID。')
    return matches[0]


def assert_selected(archive, chat_id, mode):
    """Validate an ID against the current mode before reading or exporting."""
    return select(archive, chat_id=chat_id, mode=mode)
