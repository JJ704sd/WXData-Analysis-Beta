"""Program-authored completion wording and host file presentation arguments."""
import html
from pathlib import Path
import re
from urllib.parse import quote

from offline_media_summary import attachment_type_text

REPORT_FILE = '解析结果.md'


def completion_report(destination, report):
    destination = Path(destination)
    name = re.sub(r'([\\`*_\[\]#!|])', r'\\\1', html.escape(str(report['chat']['name']), quote=False))
    def link(label, relative):
        return '[' + label + '](<' + quote((destination / relative).as_posix(), safe='/:') + '>)'
    formats = list(report['messageExports'].items())
    client_note = ''
    if report.get('coverage', {}).get('client') == 'wecom':
        unsupported = sum(count for status, count in report['parseStatuses'].items() if status not in ('ok', 'plain_system'))
        partial = sum(count for status, count in report['recordStatuses'].items() if status != 'ok')
        client_note = f'客户端：企业微信。未完整适配的源消息 {unsupported} 条，未完整展开的转发结构 {partial} 个；原始字段保留于JSONL/JSON。\n\n'
    from offline_completeness import summarize
    content = summarize([report])
    coverage_table = ('| 解析内容概览 | 当前结果 |\n| --- | ---: |\n'
        + f"| 源记录 | {content['rows']:,} 条 |\n"
        + f"| 原件 / 媒体引用（不含表情） | {content['original']:,} / {content['mediaTotal']:,} |\n"
        + f"| 有文字 / 文档引用（含启用OCR的图片） | {content['withText']:,} / {content['documentTotal']:,} |\n"
        + f"| OCR文字待核对 | {content['ocrText']:,} 处 |\n"
        + f"| 待补齐或核对 | {content['gaps']:,} 处 |\n\n"
        + '媒体与正文按引用出现次数统计；有文字不等于完整全文，OCR必须核对。\n\n')
    if content['unknownMedia'] or content['unknownDocuments'] or content['unknownQuality']:
        coverage_table = '旧版结果缺少完整内容统计，请重新解析核查；不能把缺少统计解释为零缺口。\n\n'
    analysis = report.get('analysis')
    quality = report.get('contentQuality')
    quality_note = ''
    if quality:
        quotes = quality['quoteLinks']
        quality_note = ('导出完成表示当前范围的记录与已取得文件已保存，不表示图片、附件正文及消息类型全部还原。'
                        '引用来源已关联 ' + str(quotes['linked']) + ' 处，未关联 ' + str(quotes['unlinked']) +
                        ' 处；这是来源覆盖，不是回复率。\n\n' + link('查看补齐清单与处理建议', quality['readmeFile']) +
                        ' · ' + link('表格筛选', quality['csvFile']) + '\n\n')
    search = report.get('localMediaSearch', {})
    if search.get('contentSearchComplete') is False:
        quality_note += ('本地媒体检索存在不可读、处理中变化或超过单文件上限的文件；“未取得”不能视为本机没有原件。'
                         '详见 manifest.json 的 localMediaSearch.diagnostics。\n\n')
    issues = report.get('recordIssues', {})
    issue_note = ''
    if issues.get('invalidMetadataRows'):
        issue_note = ('字段异常记录 ' + str(issues['invalidMetadataRows']) +
                      ' 条；有效正文与原始字段保留，未知字段未作推断。\n\n')
    if issues.get('unknownTimeRows'):
        issue_note += ('时间未知记录 ' + str(issues['unknownTimeRows']) + ' 条，排在已知时间记录之后。' +
                       ('其中 ' + str(issues['unknownTimeIncludedOutsideDateFilter']) +
                        ' 条仍予保留，无法确认是否位于所选日期范围。' if issues.get('unknownTimeIncludedOutsideDateFilter') else '') + '\n\n')
    analysis_text = ('业务分析材料：' + link('使用说明', analysis['readmeFile']) + '，按时间分 '
                     + str(len(analysis['chunkFiles'])) + ' 段，消息以 #序号 引用，发言人 '
                     + str(analysis['speakers']) + ' 人。\n\n') if analysis else ''
    markdown = ('已完成“' + name + '”的本地记录解析。\n\n'
                f'源记录 {report["exportedRows"]} 条，业务阅读 {report["businessRows"]} 条。'
                + ('已达到条数限制，结果不完整。' if report['truncatedByLimit'] else '当前筛选范围已导出完毕。')
                + '\n\n' + client_note + issue_note + quality_note + coverage_table + report['mediaSummaryText'] + '\n\n'
                + attachment_type_text(report['mediaSummary']) + '\n\n'
                + (report['attachmentTextSummaryText'] + '\n\n' if 'attachmentTextSummaryText' in report else '')
                + '三种导出文件：' + ' · '.join(link(kind.upper(), path) for kind, path in formats) + '\n\n'
                + link('阅读视图', 'chat.html') + ' · ' + link('图片总览', report['imageGalleryFile']) + '\n\n'
                + analysis_text
                + '未取得图片当前没有可显示的原件；缩略图与本地预览的原图均未取得，分别计数；未加载尚未核查，不能当成缺失。'
                  '表情缺失不阻塞业务阅读。MD须与同目录 assets/ 一起保存；完整原始字段以JSONL/JSON为准。\n')
    return {'version': 1, 'markdownFile': str(destination / REPORT_FILE), 'replyMarkdown': markdown,
            'presentation': {'cwd': str(destination),
                             'explanation': '展示程序统计及 JSONL、JSON、MD 三种导出文件与阅读入口。',
                             'files': [str(destination / relative) for relative in
                                       [path for _, path in formats] + [REPORT_FILE, 'chat.html',
                                        report['imageGalleryFile'], report['businessReadingFile']]
                                       + ([analysis['readmeFile']] if analysis else [])
                                       + ([quality['readmeFile'], quality['csvFile']] if quality else [])]}}
