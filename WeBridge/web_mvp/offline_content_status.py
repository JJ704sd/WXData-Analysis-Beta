"""Pure, caller-visible status and content-gap rules."""


_FORWARD_REASONS = {
    'item_limit': '转发记录超过条目上限，未完整读取',
    'depth_limit': '转发记录嵌套层数超过上限，未完整读取',
    'invalid_xml': '转发记录 XML 无法解析，未完整读取',
    'partially_supported': '转发记录仅部分字段适配，未完整读取',
}


def document_action(status, extension):
    if status in ('encrypted', 'password_rejected'):
        return '在补齐设置按文件SHA256提供已知合法密码后重新解析，或提供解密副本；未知密码不作破解。'
    if status in ('ocr_extracted', 'ocr_unavailable'):
        return 'OCR结果须人工核对数字、单位和表格；本机不可用时请安装对应Windows OCR语言后重新解析。'
    if status == 'no_text':
        return ('检查原件是否为扫描件；需要时进行 OCR，并人工核对数字、单位和表格。'
                if extension == '.pdf' else '检查原件、图片及图表；未提取到文字不代表没有业务内容。')
    if status in ('partial', 'too_large', 'timeout', 'budget_exceeded'):
        return '核对已提取范围与原件；按页或拆分文件补充未提取部分，不能将当前正文当作全文。'
    return '用原始文件核对格式与内容；保留原件，当前正文不可作为已完整读取的依据。'


def forward_notice(record):
    """Return a concrete notice for a partial forwarded-record parse."""
    record = record or {}
    status = record.get('status') or ''
    if not status or status == 'ok':
        return ''
    items = record.get('items') or []
    details = '当前保留 ' + str(len(items)) + ' 条'
    reason = _FORWARD_REASONS.get(status)
    if reason is not None:
        if 'count' in record and record['count'] is not None:
            details += '，源记录共 ' + str(record['count']) + ' 条'
    else:
        reason = '转发记录未完整读取，原状态：' + str(status)
    return reason + '；' + details + '；请在原客户端核对完整记录。'


def content_issues(node):
    """Return ``(codes, reasons, actions)`` for one traversed message node.

    The rule set only reports explicit, observable gaps.  It has no file,
    queue, or output side effects so all reading paths can share the same
    classification.
    """
    codes, reasons, actions = [], [], []
    asset = node.get('asset') or {}
    if node.get('kind') in ('image', 'file', 'video', 'voice'):
        if not asset or asset.get('status') != 'ready' or asset.get('previewOnly'):
            code = 'media_not_checked' if not asset else 'original_unavailable'
            codes.append(code)
            reasons.append(asset.get('reason') or '未核查原件缓存。')
            actions.append('在原客户端按消息时间打开原图或下载附件；也可另存到补充原件目录，再重新解析，按原件MD5校验补齐。')

    document = node.get('attachmentContent') or {}
    status = document.get('extractionStatus')
    if (node.get('kind') == 'file' or document) and not status:
        codes.append('document_not_checked')
        reasons.append('附件已有文件或正文记录，但未记录正文提取状态，完整性尚未核验。')
        actions.append('按原件重新解析并确认正文提取状态；状态明确前不要将当前正文视为完整。')
    elif status and status != 'extracted' and document.get('fileStatus') in (None, '', 'ready', 'preview_only'):
        codes.append('document_' + status)
        reasons.extend(document.get('notices') or [document.get('extractionStatusText', status)])
        actions.append(document_action(status, document.get('format')))

    record = node.get('record') or {}
    if record.get('status') and record.get('status') != 'ok':
        codes.append('forward_partial')
        reasons.append(forward_notice(record))
        actions.append('按消息 ID 在原客户端打开并核对转发记录；当前已保留正文，不能视为转发全文。')

    parse_status = node.get('parseStatus', 'ok')
    if parse_status not in ('ok', 'plain_system', 'partial_quote'):
        codes.append('message_partial')
        reasons.append(parse_status)
        actions.append('按消息 ID 与原客户端核对；未适配字段保留在 messages.jsonl，不能当成空消息。')

    decode_status = node.get('decodeStatus')
    decode_errors = [node.get(key) for key in ('decodeError', 'decode_error')
                     if node.get(key) not in (None, '', False, [], {})]
    if decode_status not in (None, '', 'ok') or decode_errors:
        codes.append('message_decode_partial')
        if decode_status not in (None, '', 'ok'):
            reasons.append('解码状态：' + str(decode_status))
        reasons.extend(str(error) for error in decode_errors)
        actions.append('按消息 ID 与原客户端核对；解码未完成的消息不能当作完整正文，保留原始字段后重新解析。')

    quote = node.get('quote')
    if quote:
        linked = quote.get('sourceLinkStatus') == 'matched_local_source'
        if not linked:
            codes.append('quote_unlinked')
            reasons.append(quote.get('sourceLinkStatus') or 'source_id_unavailable')
            actions.append('可阅读已保留的引用正文；来源未关联不能解释为无人回复，需核对同群原消息和历史范围。')
    return codes, reasons, actions
