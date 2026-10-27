"""Read-only, evidence-bounded statistics for a reviewed direct-chat export.

The input is the dictionary returned by ``ProfileStore.inspect_export``.  This
module deliberately reports observable message fields only; it does not score
people, infer a relationship, or include message bodies in generated reports.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone, timedelta
import html
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from offline_images import message_nodes
    from offline_content_status import content_issues
    from offline_documents import TEXT_LABELS
except ModuleNotFoundError:  # package import from the project root
    from .offline_images import message_nodes
    from .offline_content_status import content_issues
    from .offline_documents import TEXT_LABELS


BEIJING = timezone(timedelta(hours=8))
MAX_QUERY_LENGTH = 200
MAX_PAGE_SIZE = 200
ATTACHMENT_TEXT_STATUSES = frozenset({"extracted", "partial", "ocr_extracted"})


def _messages(source: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = source.get("messages", [])
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def _parse_time(value: Any) -> tuple[datetime | None, str]:
    """Return a Beijing-aware datetime and one of valid/unknown/invalid."""
    if value is None:
        return None, "unknown"
    if not isinstance(value, str):
        return None, "invalid"
    if not value.strip():
        return None, "unknown"
    stamp = value.strip()
    if stamp.endswith(("Z", "z")):
        stamp = stamp[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(stamp)
    except (TypeError, ValueError, OverflowError):
        return None, "invalid"
    try:
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=BEIJING)
        else:
            # A valid ISO value can still overflow while converting an extreme
            # offset into Beijing time. Treat that as an invalid timestamp so
            # one malformed row cannot abort the whole report.
            moment = moment.astimezone(BEIJING)
    except (ValueError, OverflowError):
        return None, "invalid"
    return moment, "valid"


def _is_emoji_char(char: str) -> bool:
    code = ord(char)
    return (
        0x1F000 <= code <= 0x1FAFF
        or 0x2600 <= code <= 0x27BF
        or 0x2300 <= code <= 0x23FF
        or 0x2B00 <= code <= 0x2BFF
        or 0xFE0F == code
        or 0x1F3FB <= code <= 0x1F3FF
    )


def _is_emoji_message(row: Mapping[str, Any]) -> bool:
    if str(row.get("kind") or "").casefold() == "emoji":
        return True
    text = row.get("text")
    if not isinstance(text, str):
        return False
    visible = [char for char in text if not char.isspace() and char != "\ufe0f"]
    # A text message containing an emoji remains a text message; only a
    # standalone emoji payload is separated from the textual analysis set.
    return bool(visible) and all(_is_emoji_char(char) or char == "\u200d" for char in visible)


def _direction(value: Any) -> str:
    value = str(value or "").casefold().strip()
    if value in {"outgoing", "outbound", "sent", "send", "self", "me"}:
        return "outgoing"
    if value in {"incoming", "inbound", "received", "receive", "peer"}:
        return "incoming"
    return "unknown"


def _kind(row: Mapping[str, Any]) -> str:
    value = row.get("kind")
    if value is None or value == "":
        value = row.get("localType")
    return str(value or "unknown")


def _status_is_gap(value: Any, error_keys: Iterable[str], row: Mapping[str, Any],
                   good_statuses: frozenset[str]) -> bool:
    for key in error_keys:
        error = row.get(key)
        if error not in (None, "", False, [], {}):
            return True
    status = str(value or "").casefold().strip()
    return bool(status) and status not in good_statuses


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def _distribution(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    days: Counter[str] = Counter()
    months: Counter[str] = Counter()
    hours: Counter[str] = Counter()
    valid_moments: list[datetime] = []
    unknown = invalid = 0
    for row in rows:
        moment, state = _parse_time(row.get("time"))
        if state == "unknown":
            unknown += 1
            continue
        if state == "invalid":
            invalid += 1
            continue
        assert moment is not None
        valid_moments.append(moment)
        day = moment.strftime("%Y-%m-%d")
        days[day] += 1
        months[day[:7]] += 1
        hours[moment.strftime("%H")] += 1
    observed = {
        "start": min(valid_moments).isoformat() if valid_moments else None,
        "end": max(valid_moments).isoformat() if valid_moments else None,
    }
    return {
        "byDay": _counter_dict(days),
        "byMonth": _counter_dict(months),
        "byHour": _counter_dict(hours),
        "activeDays": len(days),
        "unknownTimeCount": unknown,
        "invalidTimeCount": invalid,
        "observedTimeRange": observed,
    }


def _content_coverage(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    issues: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    observed = gaps = messages_with_gaps = 0
    documents = with_text = no_text = unknown_status = 0
    for row in rows:
        has_gap = False
        for node, _trail, _path in message_nodes(row):
            observed += 1
            codes, _reasons, _actions = content_issues(node)
            if codes:
                issues.update(codes)
                gaps += 1
                has_gap = True
            document = node.get('attachmentContent') or {}
            if node.get('kind') == 'file' or document:
                documents += 1
                with_text += bool(document.get('text', '').strip())
                status = document.get('extractionStatus')
                if status:
                    statuses[status] += 1
                    no_text += status == 'no_text'
                else:
                    unknown_status += 1
        messages_with_gaps += has_gap
    return {'schemaVersion': 1, 'unit': 'reference_occurrence',
            'observedOccurrences': observed, 'gapOccurrences': gaps,
            'messagesWithGaps': messages_with_gaps, 'issueCounts': _counter_dict(issues),
            'documents': {'observed': documents, 'withText': with_text, 'noText': no_text,
                          'unknownStatus': unknown_status, 'statuses': _counter_dict(statuses)}}


def content_coverage_rows(summary: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """Use one set of labels and units in the native view and saved reports."""
    coverage = summary.get('contentCoverage')
    if coverage is None:
        return [('内容完整性', '该版本未统计', '请重新生成统计以核查内容缺口。')]
    documents = coverage['documents']
    rows = [
        ('观察到的内容节点', str(coverage['observedOccurrences']), '按引用出现次数；含根消息、转发条目和引用来源。'),
        ('需补齐或核对', str(coverage['gapOccurrences']), '每个有缺口的节点只计一次；同一文件多处出现分别统计。'),
        ('涉及根消息', str(coverage['messagesWithGaps']), '含任一内容缺口的最外层消息数，不增加消息总数。'),
        ('附件及图片 OCR 节点', str(documents['observed']), '附件消息或有正文提取记录的图片；不是去重文件数。'),
        ('已取得文字', str(documents['withText']), '确有非空正文；部分提取和 OCR 文字也计入，不表示完整或准确。'),
        ('无可提取文字', str(documents['noText']), '明确记录未取得文字；不表示原件没有业务内容。'),
        ('提取状态未记录', str(documents['unknownStatus']), '旧材料或缺失提取元数据，不能当作成功读取。'),
    ]
    labels = {'original_unavailable': '原件未取得（含仅有预览）', 'media_not_checked': '未核查媒体',
              'message_partial': '消息结构未完整适配', 'quote_unlinked': '引用来源未关联',
              'forward_partial': '转发记录未完整读取', 'document_not_checked': '文档提取状态未记录',
              'message_decode_partial': '消息解码存在缺口'}
    for code, count in coverage['issueCounts'].items():
        label = labels.get(code)
        if label is None:
            label = TEXT_LABELS.get(code.removeprefix('document_'), code)
        rows.append(('缺口 · ' + label, str(count), '按引用出现次数；一处可有多个缺口，分类不可相加为文件数。'))
    for status, count in documents['statuses'].items():
        rows.append(('正文状态 · ' + TEXT_LABELS.get(status, status), str(count), '保留文件记录的提取状态；OCR 尝试不等于取得文字。'))
    return rows


def summarize(source: Mapping[str, Any]) -> dict[str, Any]:
    """Summarize every reviewed message in stable source order."""
    rows = _messages(source)
    source_refs = source.get("analysisRefs") or {}
    analysis_coverage = len(source_refs) if isinstance(source_refs, Mapping) else 0
    directions: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    emoji_rows: list[Mapping[str, Any]] = []
    non_emoji_rows: list[Mapping[str, Any]] = []
    parse_gaps = decode_gaps = 0
    for row in rows:
        directions[_direction(row.get("direction"))] += 1
        kinds[_kind(row)] += 1
        if _is_emoji_message(row):
            emoji_rows.append(row)
        else:
            non_emoji_rows.append(row)
        if _status_is_gap(row.get("parseStatus"), ("parseError", "parse_error"), row,
                          frozenset({"ok", "plain_system"})):
            parse_gaps += 1
        if _status_is_gap(row.get("decodeStatus"), ("decodeError", "decode_error"), row,
                          frozenset({"ok"})):
            decode_gaps += 1

    distribution = _distribution(rows)
    non_emoji_distribution = _distribution(non_emoji_rows)
    counts = {
        "total": len(rows),
        "outgoing": directions["outgoing"],
        "incoming": directions["incoming"],
        "unknown": directions["unknown"],
        "emoji": len(emoji_rows),
        "nonEmoji": len(non_emoji_rows),
    }
    result: dict[str, Any] = {
        "identity": dict(source.get("identity") or {}),
        "chat": dict(source.get("chat") or {}),
        "reviewId": source.get("reviewId"),
        "sourceDigest": source.get("sourceDigest"),
        "sourceLimitations": list(source.get("sourceLimitations") or []),
        "exportTimeRange": source.get("timeRange"),
        "timeRange": source.get("timeRange"),
        "totalMessages": len(rows),
        "counts": counts,
        "directions": _counter_dict(directions),
        "messageTypes": _counter_dict(kinds),
        "analysisCoverageCount": analysis_coverage,
        "analysisCoverage": {"messageCount": analysis_coverage},
        "emojiMessages": len(emoji_rows),
        "nonEmojiMessages": len(non_emoji_rows),
        "parseGapCount": parse_gaps,
        "decodeGapCount": decode_gaps,
        "gaps": {"parse": parse_gaps, "decode": decode_gaps},
        "contentCoverage": _content_coverage(rows),
        "byDay": distribution["byDay"],
        "byMonth": distribution["byMonth"],
        "byHour": distribution["byHour"],
        "activeDays": distribution["activeDays"],
        "activeDayCount": distribution["activeDays"],
        "unknownTimeCount": distribution["unknownTimeCount"],
        "invalidTimeCount": distribution["invalidTimeCount"],
        "observedTimeRange": distribution["observedTimeRange"],
        "nonEmoji": {
            "total": len(non_emoji_rows),
            "directions": _counter_dict(Counter(_direction(row.get("direction")) for row in non_emoji_rows)),
            "messageTypes": _counter_dict(Counter(_kind(row) for row in non_emoji_rows)),
            "byDay": non_emoji_distribution["byDay"],
            "byMonth": non_emoji_distribution["byMonth"],
            "byHour": non_emoji_distribution["byHour"],
            "activeDays": non_emoji_distribution["activeDays"],
        },
    }
    return result


def resolve_message_reference(source: Mapping[str, Any], row: Mapping[str, Any]) -> tuple[Any, int | None]:
    """Return the stable message ID and its verified analysis reference.

    Raw row ``ref`` values are deliberately ignored.  Only a positive,
    non-boolean integer from ``analysisRefs`` is an evidence location.
    ``messageId`` is used when the row has no ``id`` value so callers can
    still locate unmapped source text without presenting an unverified ref.
    """
    message_id = row.get("id")
    if message_id is None:
        message_id = row.get("messageId")
    refs = source.get("analysisRefs") or {}
    if not isinstance(refs, Mapping) or message_id is None:
        return message_id, None
    keys = (message_id, str(message_id))
    valid_refs: list[int] = []
    for key in keys:
        try:
            candidate = refs[key]
        except (KeyError, TypeError):
            continue
        if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate > 0:
            valid_refs.append(candidate)
    if valid_refs and len(set(valid_refs)) == 1:
        return message_id, valid_refs[0]
    return message_id, None


def _search_snippet(value: str, needle: str) -> str:
    folded = value.casefold()
    position = folded.find(needle)
    if position < 0:
        return value[:160] + ("…" if len(value) > 160 else "")
    end_position = position + len(needle)
    if len(folded) != len(value):
        folded_positions = []
        for index, char in enumerate(value):
            folded_positions.extend([index] * len(char.casefold()))
        position, end_position = folded_positions[position], folded_positions[end_position - 1] + 1
    start = max(0, position - 60)
    end = min(len(value), end_position + 60)
    return ("…" if start else "") + value[start:end] + ("…" if end < len(value) else "")


def _searchable_node_fields(node: Mapping[str, Any], *, include_text: bool) -> Iterable[tuple[str, str, bool]]:
    if include_text and isinstance(node.get("text"), str) and node["text"]:
        yield "正文", node["text"], False
    if isinstance(node.get("title"), str) and node["title"]:
        yield "标题", node["title"], False
    if isinstance(node.get("description"), str) and node["description"]:
        yield "卡片描述", node["description"], False
    record = node.get("record")
    if isinstance(record, Mapping):
        if isinstance(record.get("title"), str) and record["title"]:
            yield "转发标题", record["title"], False
        if isinstance(record.get("description"), str) and record["description"]:
            yield "转发说明", record["description"], False
    for group, label, fields in (
            ("miniProgram", "小程序信息", ("appId", "username", "pagePath")),
            ("contact", "名片信息", ("nickname", "username", "alias")),
            ("location", "位置信息", ("label", "poiname", "x", "y"))):
        for field in fields:
            value = node.get(group, {}).get(field)
            if value:
                yield label, value, False
    links = node.get("links")
    if isinstance(links, list):
        for link in links:
            if isinstance(link, str) and link:
                yield "链接", link, False
    attachment = node.get("attachmentContent")
    if isinstance(attachment, Mapping) and attachment.get("extractionStatus") in ATTACHMENT_TEXT_STATUSES:
        attachment_text = attachment.get("text")
        if isinstance(attachment_text, str) and attachment_text:
            asset = node.get("asset") if isinstance(node.get("asset"), Mapping) else {}
            media = node.get("media") if isinstance(node.get("media"), Mapping) else {}
            filename = asset.get("filename") or media.get("filename") or ""
            label = "附件正文" + (f" · {filename}" if filename else "")
            yield label, attachment_text, bool(attachment.get("requiresReview") or
                                               attachment.get("extractionStatus") in {"partial", "ocr_extracted"})


def _match_sender(root: Mapping[str, Any], node: Mapping[str, Any], path: Iterable[tuple[str, int]]) -> str:
    sender = node.get("senderName") or node.get("sender")
    if sender:
        return str(sender)
    steps = list(path)
    if not steps or steps[-1][0] != "quote":
        return ""
    current = root
    for kind, index in steps[:-1]:
        if kind == "record":
            current = current["record"]["items"][index]
        else:
            current = current["quote"]["message"]
    quote = current.get("quote") or {}
    return str(quote.get("senderName") or quote.get("sender") or quote.get("senderId") or "")


def _match_source(trail: Iterable[str], sender: str, field: str) -> str:
    context = " / ".join(trail) if trail else "当前消息"
    if trail and sender:
        context += " · " + sender
    return context + " · " + field


def search_messages(source: Mapping[str, Any], query: str, *, offset: int = 0,
                    limit: int = 50) -> dict[str, Any]:
    """Search reviewed message text and reader-facing nested content with bounded paging."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be non-empty")
    if len(query) > MAX_QUERY_LENGTH:
        raise ValueError("query is too long")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError("limit must be between 1 and 200")
    needle = query.casefold()
    matches = []
    total = 0
    for row in _messages(source):
        text = row.get("text")
        root_text_match = isinstance(text, str) and needle in text.casefold()
        nested_match = None
        if not root_text_match:
            for node, trail, path in message_nodes(row):
                for field, value, requires_review in _searchable_node_fields(node, include_text=bool(path)):
                    if needle not in value.casefold():
                        continue
                    nested_match = (node, trail, path, field, value, requires_review)
                    break
                if nested_match is not None:
                    break
        if not root_text_match and nested_match is None:
            continue
        total += 1
        if total <= offset or len(matches) >= limit:
            continue
        message_id, ref = resolve_message_reference(source, row)
        item = {
            "messageId": message_id,
            "ref": ref,
            "time": row.get("time"),
            "senderName": row.get("senderName", row.get("sender", "")) or "",
            "direction": _direction(row.get("direction")),
            "kind": _kind(row),
            "text": text,
        }
        if nested_match is not None:
            node, trail, path, field, value, requires_review = nested_match
            item.update(matchSource=_match_source(trail, _match_sender(row, node, path), field),
                        snippet=_search_snippet(value, needle), matchPath=[list(part) for part in path])
            if requires_review:
                item["matchRequiresReview"] = True
                document = node['attachmentContent']
                ocr_text = (document.get('extractionStatus') == 'ocr_extracted' or
                            document.get('ocr', {}).get('recognizedPages') or document.get('ocrSections') or
                            any(section.get('ocr') for section in document.get('sections', [])))
                item['matchReviewText'] = 'OCR文字待核对' if ocr_text else '附件内容待核对'
        matches.append(item)
    return {"query": query, "total": total, "offset": offset, "limit": limit,
            "items": matches}


def _safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _bar_rows(title: str, values: Mapping[str, Any]) -> str:
    entries = [(str(key), int(value)) for key, value in values.items() if isinstance(value, int)]
    if not entries:
        return f"<section><h2>{html.escape(title)}</h2><p>无可用数据</p></section>"
    maximum = max(value for _, value in entries) or 1
    lines = [f"<section><h2>{html.escape(title)}</h2><div class=\"bars\">"]
    for key, value in entries:
        width = max(1, round(value * 100 / maximum)) if value else 0
        lines.append(
            f"<div class=\"bar-row\"><span>{html.escape(key)}</span>"
            f"<i style=\"width:{width}%\"></i><b>{value}</b></div>"
        )
    lines.append("</div></section>")
    return "".join(lines)


def _markdown(summary: Mapping[str, Any]) -> str:
    counts = summary.get("counts") or {}
    gaps = summary.get("gaps") or {}
    limitations = summary.get("sourceLimitations") or []
    limitation_lines = "\n".join(f"- {item}" for item in limitations) or "- 无额外记录"
    coverage_lines = '\n'.join(f'- {label}：{value}。{meaning}'
                               for label, value, meaning in content_coverage_rows(summary))
    return "\n".join([
        "# 私聊客观统计报告", "",
        "本报告仅统计已复核导出范围内的消息字段，不构成性格、关系或意图画像。",
        "", "## 统计口径", "",
        f"- 消息总数：{counts.get('total', 0)}（含表情消息 {counts.get('emoji', 0)}，非表情消息 {counts.get('nonEmoji', 0)}）",
        f"- 画像阅读材料覆盖消息数：{summary.get('analysisCoverageCount', 0)}（仅为覆盖数，不改变消息总数）",
        f"- 客户端/联系人：{summary.get('identity', {}).get('client', '未知')} / {summary.get('chat', {}).get('name') or summary.get('chat', {}).get('id') or summary.get('identity', {}).get('peerId', '未知')}",
        f"- reviewId：{summary.get('reviewId') or '未提供'}；sourceDigest：{summary.get('sourceDigest') or '未提供'}",
        f"- 方向：发出 {counts.get('outgoing', 0)}，收到 {counts.get('incoming', 0)}，未知 {counts.get('unknown', 0)}",
        f"- 消息类型：{json.dumps(summary.get('messageTypes', {}), ensure_ascii=False, sort_keys=True)}",
        f"- 活跃日：{summary.get('activeDays', 0)}；无时间 {summary.get('unknownTimeCount', 0)}；无效时间 {summary.get('invalidTimeCount', 0)}",
        f"- parse 缺口：{gaps.get('parse', 0)}；decode 缺口：{gaps.get('decode', 0)}",
        f"- 导出范围：{json.dumps(summary.get('exportTimeRange'), ensure_ascii=False, sort_keys=True)}",
        "", "## 内容完整性", "", coverage_lines,
        "", "## 来源限制", "", limitation_lines,
        "", "## 分布", "", f"- 按日：{json.dumps(summary.get('byDay', {}), ensure_ascii=False, sort_keys=True)}",
        f"- 按月：{json.dumps(summary.get('byMonth', {}), ensure_ascii=False, sort_keys=True)}",
        f"- 按小时：{json.dumps(summary.get('byHour', {}), ensure_ascii=False, sort_keys=True)}", "",
    ])


def _html_report(summary: Mapping[str, Any]) -> str:
    counts = summary.get("counts") or {}
    limitations = summary.get("sourceLimitations") or []
    limitation_html = "".join(f"<li>{html.escape(str(item))}</li>" for item in limitations) or "<li>无额外记录</li>"
    identity = summary.get("identity") or {}
    chat = summary.get("chat") or {}
    client = html.escape(str(identity.get("client") or "未知客户端"))
    contact = html.escape(str(chat.get("name") or chat.get("id") or identity.get("peerId") or "未知联系人"))
    review_id = html.escape(str(summary.get("reviewId") or "未提供"))
    source_digest = html.escape(str(summary.get("sourceDigest") or "未提供"))
    cards = []
    for label, key in (("消息总数", "total"), ("发出", "outgoing"), ("收到", "incoming"),
                       ("未知方向", "unknown"), ("表情消息", "emoji"), ("非表情消息", "nonEmoji")):
        cards.append(
            f"<div class=\"card\"><small>{html.escape(label)}</small>"
            f"<strong>{html.escape(str(counts.get(key, 0)))}</strong></div>"
        )
    gaps = summary.get("gaps") or {}
    coverage_table = ''.join('<tr>' + ''.join('<td>' + html.escape(value) + '</td>'
                            for value in row) + '</tr>' for row in content_coverage_rows(summary))
    parts = [
        "<!doctype html>\n<html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>我们的聊天足迹 · 私聊客观统计</title>\n"
        "<style>*{box-sizing:border-box}body{font-family:'Microsoft YaHei UI',system-ui,sans-serif;"
        "max-width:1080px;margin:0 auto;padding:48px 32px;color:#17332a;line-height:1.7;"
        "background:linear-gradient(135deg,#edf7f0,#f8faf8 40%,#fff9ef);background-attachment:fixed}"
        "h1{font-size:38px;letter-spacing:-1px;margin:6px 0 12px;line-height:1.3}"
        "h2{font-size:19px;margin:0 0 20px}.eyebrow{color:#127552;letter-spacing:3px;font-size:12px}"
        ".note,small{color:#718078}.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));"
        "gap:16px;margin:28px 0}.card,section{background:#ffffffdf;border:1px solid #e2eae5;"
        "border-radius:20px;padding:24px;box-shadow:0 6px 24px #17332a05}section{margin:20px 0}"
        ".card:first-child{grid-column:span 2;background:#173f31;color:white}"
        ".card:first-child small{color:#b7d5c4}.card small{display:block;font-size:13px}"
        ".card strong{display:block;font-size:36px;line-height:1.4;font-weight:650}"
        ".bars{display:grid;gap:12px;max-height:400px;overflow:auto;padding-right:8px}"
        ".bar-row{display:grid;grid-template-columns:110px 1fr 56px;align-items:center;gap:14px;font-size:13px}"
        ".bar-row i{display:block;height:10px;background:linear-gradient(90deg,#aadcc4,#16a271);"
        "border-radius:5px}.bar-row b{text-align:right;font-variant-numeric:tabular-nums}"
        "details{padding:12px 0;font-size:13px;color:#718078;overflow-wrap:anywhere}summary{cursor:pointer;color:#127552}"
        "section p,li{overflow-wrap:anywhere}li{margin:6px 0}"
        ".coverage{width:100%;border-collapse:collapse;font-size:13px}.coverage td,.coverage th{"
        "padding:8px;text-align:left;vertical-align:top;border-bottom:1px solid #e2eae5;overflow-wrap:anywhere}"
        "@media(max-width:650px){body{padding:24px 18px}h1{font-size:30px}.cards{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}"
        ".card,section{padding:18px}.bar-row{grid-template-columns:84px 1fr 42px;gap:8px}}"
        "@media print{body{background:white;padding:0}.bars{max-height:none;overflow:visible}.card,section{box-shadow:none;break-inside:avoid}}"
        "</style></head>\n<body><div class=\"eyebrow\">私聊客观统计 · LOCAL ARCHIVE</div><h1>我们的聊天足迹</h1>",
        f"<p>{client} · {contact}</p><details><summary>查看来源与复核标识</summary>reviewId：{review_id} "
        f"<br>sourceDigest：{source_digest}</details>",
        "<p class=\"note\">仅统计已复核导出范围；不构成性格、关系或意图画像。</p>",
        "<div class=\"cards\">", "".join(cards),
        f"<div class=\"card\"><small>画像阅读材料覆盖</small><strong>"
        f"{html.escape(str(summary.get('analysisCoverageCount', 0)))}</strong></div></div>",
        f"<section><h2>来源限制</h2><ul>{limitation_html}</ul></section>",
        '<section><h2>内容完整性</h2><table class="coverage"><thead><tr><th>检查项</th>'
        '<th>当前结果</th><th>口径与建议</th></tr></thead><tbody>' + coverage_table + '</tbody></table></section>',
        _bar_rows("按日分布", summary.get("byDay") or {}),
        _bar_rows("按月分布", summary.get("byMonth") or {}),
        _bar_rows("按小时分布", summary.get("byHour") or {}),
        _bar_rows("消息类型", summary.get("messageTypes") or {}),
        f"<section><h2>时间与缺口</h2><p>导出范围：{html.escape(str(summary.get('exportTimeRange')))}</p>",
        f"<p>活跃日：{html.escape(str(summary.get('activeDays', 0)))}；观察到的北京时间范围："
        f"{html.escape(str(summary.get('observedTimeRange')))}；无时间 "
        f"{html.escape(str(summary.get('unknownTimeCount', 0)))}；无效时间 "
        f"{html.escape(str(summary.get('invalidTimeCount', 0)))}；parse 缺口 "
        f"{html.escape(str(gaps.get('parse', 0)))}；decode 缺口 "
        f"{html.escape(str(gaps.get('decode', 0)))}。</p></section>",
        "</body></html>\n",
    ]
    return "".join(parts)


def write_report(summary: Mapping[str, Any], output_dir: str | Path) -> dict[str, str]:
    """Write JSON, Markdown and standalone HTML into one new directory.

    Existing directories are rejected so a report can never silently overwrite
    an earlier reviewed artifact. On a write error only files created here are
    removed.
    """
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=False)
    created: list[Path] = []
    files = {
        "jsonFile": target / "summary.json",
        "markdownFile": target / "report.md",
        "htmlFile": target / "report.html",
    }
    try:
        contents = {
            files["jsonFile"]: _safe_json(summary),
            files["markdownFile"]: _markdown(summary),
            files["htmlFile"]: _html_report(summary),
        }
        for path, content in contents.items():
            # Track before writing so a partial file from a failed write is
            # also removed by the best-effort cleanup below.
            created.append(path)
            path.write_text(content, encoding="utf-8", newline="\n")
    except Exception:
        for path in created:
            try:
                path.unlink()
            except OSError:
                pass
        try:
            target.rmdir()
        except OSError:
            pass
        raise
    return {key: str(path) for key, path in files.items()}


__all__ = ["content_coverage_rows", "resolve_message_reference", "search_messages", "summarize", "write_report"]
