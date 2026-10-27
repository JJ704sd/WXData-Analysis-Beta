"""Interactive account -> group ID -> parse -> human review -> export workflow."""
import json

from offline_chat import ArchiveError
from offline_desktop import DesktopSession
from offline_profiles import default_directory
from offline_review import REVIEW_CHECKS


def review_workflow(*, client=None, directory=None, config=None, ask=None, show=None):
    ask, show = ask or input, show or print
    if client is None:
        client = {'1': 'wechat', '2': 'wecom'}.get(ask('选择客户端：1 个微 / 2 企微：').strip())
    if client not in ('wechat', 'wecom'):
        raise ArchiveError('请选择个微或企微。')
    session = DesktopSession(directory or default_directory(client), client=client)
    if config:
        info = session.open_source(config)
    else:
        saved = session.settings.get('source')
        use_saved = False
        if saved:
            show('已有账号数据目录：' + saved.get('sourceRoot', ''))
            use_saved = ask('使用此账号？[y/N]：').strip().lower() == 'y'
        if use_saved:
            info = session.bootstrap()
        else:
            accounts = session.scan()['accounts']
            for index, account in enumerate(accounts, 1):
                show(f"{index}. {account.get('directoryLabel', '')}\t{account['sourceRoot']}")
            selected = ask('选择账号序号；或输入已解密副本 / 账号数据目录：').strip().strip('"')
            if selected.isdigit() and 1 <= int(selected) <= len(accounts):
                selected = accounts[int(selected) - 1]['sourceRoot']
            key = ask('已有数据库密钥 JSON 路径（明文副本可留空）：').strip().strip('"')
            config = {'sourceRoot': selected, 'keyFile': key, 'includeMedia': True}
            if client == 'wechat':
                config['imageKeyFile'] = ask('已有图片密钥 JSON 路径（可留空）：').strip().strip('"')
            info = session.open_source(config)
    if info['status'] != 'ready':
        raise ArchiveError(info['message'])
    show('当前客户端：' + client + '\n当前账号数据目录：' + session.archive.media_config.get('sourceRoot', ''))
    show('本地群聊 ID\t名称\t本地消息状态')
    for group in info['groups']:
        show(f"{group['id']}\t{group['name']}\t{'有记录' if group['hasLocalMessages'] else '暂无记录'}")
    show(f"共 {len(info['groups'])} 个本地群聊；不代表服务器端全部群聊。")
    chat = ask('输入所选群聊的完整 ID：').strip()
    if chat not in {group['id'] for group in info['groups']}:
        raise ArchiveError('群 ID 不属于当前账号的群列表。')
    start = ask('开始日期 YYYY-MM-DD（留空不限）：').strip() or None
    end = ask('结束日期 YYYY-MM-DD（留空不限）：').strip() or None
    result = session.prepare_review(chat, start, end, copy_media=True)
    show(json.dumps(result['review'], ensure_ascii=False, indent=2))
    show('解析预览目录：' + result['view']['viewRoot'])
    show('请查看 chat.html、messages.jsonl / messages.json / messages.md、members.json 与 manifest.json，核对内容及缺口。')
    checks = {key: ask(label + '？[y/N]：').strip().lower() == 'y' for key, label in REVIEW_CHECKS.items()}
    if not all(checks.values()):
        show('复核未通过，保留预览，未发布正式导出。')
        return {'state': 'awaiting_review', 'reviewId': result['review']['reviewId']}
    note = ask('复核备注（可留空）：')
    if ask('确认无误后输入 EXPORT 导出三种格式；其他输入取消：').strip() != 'EXPORT':
        show('已取消，未发布正式导出。')
        return {'state': 'awaiting_review', 'reviewId': result['review']['reviewId']}
    exported = session.confirm_export(result['review']['reviewId'], checks, note, chat=chat)
    show('已确认导出：' + exported['output'])
    return {'state': 'completed', **exported}
