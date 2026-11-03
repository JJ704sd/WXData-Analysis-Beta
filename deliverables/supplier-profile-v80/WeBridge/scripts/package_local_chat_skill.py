"""Package only the portable runtime, skill instructions and redistribution licenses."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

from local_chat_distribution import copy_public_guide, write_quick_start, complete_metadata, verified_desktop_inputs, write_wecom_diagnostic_launcher, verify_group_desktop
from chat_release import GROUP_VERSION


def scoped_skill(skill, client):
    """Keep one maintained parser contract, publish two distinct host skills."""
    skill = skill.replace('(../../docs/local-chat/', '(').replace('(../../个微聊天记录解析工具说明.md)', '(个微聊天记录解析工具说明.md)')
    if not client:
        return skill
    label, name = ('企微', 'wecom-local-archive') if client == 'wecom' else ('个微', 'wechat-local-archive')
    lines = skill.splitlines()
    for index, line in enumerate(lines):
        if line == 'name: local-wechat-archive':
            lines[index] = 'name: ' + name
        elif line.startswith('description:'):
            trigger = '解析企微某群、解析企业微信某群' if client == 'wecom' else '解析个微某群、解析微信某群或解析某群'
            lines[index] = f'description: {label}本地聊天解析；用户要求{trigger}或打开解析界面、多选群批量解析时使用；逐群人工复核确认后导出JSONL/JSON/MD；免安装Python，复用现有账号配置。'
        elif line.startswith('  description_zh:'):
            lines[index] = f'  description_zh: "{label}-本地聊天解析，多选群批量解析、逐群复核后导出。"'
        elif line.startswith('  description_en:'):
            lines[index] = f'  description_en: "Open {"WeCom" if client == "wecom" else "WeChat"}, select multiple groups and review each before export."'
    text = '\n'.join(lines) + '\n'
    text = text.replace('# 本地微信聊天解析', '# ' + label + '-本地聊天解析', 1)
    start = text.index('用户明确说“企微/企业微信”时，')
    end = text.index('\n\n', start)
    own = '企业微信' if client == 'wecom' else '个人微信'
    other = 'wechat-local-archive（个微）' if client == 'wecom' else 'wecom-local-archive（企微）'
    text = text[:start] + f'本 Skill 固定使用{own}；脚本和直接双击EXE均从 runtime.json 校验并取得客户端，用户无需传 -Client。请求其他客户端时改用 {other}，不得混用配置。Start.cmd 直接启动本客户端；保留完整包，不能只复制bin内EXE。' + text[end:]
    if client == 'wecom':
        text = text.replace('%LOCALAPPDATA%\\LocalWechatArchive', '%LOCALAPPDATA%\\LocalWeComArchive')
        text = text.replace('个微4.x或企微5.x', '企业微信5.x')
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--desktop-package', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--shareable', action='store_true', help='Include public documentation and package checksums')
    parser.add_argument('--client', choices=('wechat', 'wecom'), help='Publish a separately named client-bound skill')
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    source = project / 'skills/local-wechat-archive'
    desktop = args.desktop_package.resolve()
    output = args.output.resolve()
    archive = output.parent / (output.name + '.zip')
    if output.exists() or archive.exists():
        raise SystemExit('Choose a new package path.')
    binary = desktop / '本地聊天解析.exe'
    if not binary.is_file():
        binary = desktop / 'local-chat-desktop.exe'
    if not binary.is_file() or not (desktop / 'licenses').is_dir():
        raise SystemExit('A complete desktop distribution is required.')
    licenses = verified_desktop_inputs(desktop, binary)
    # Metadata is promoted for WorkBuddy, whose import schema requires these fields.
    skill = scoped_skill((source / 'SKILL.md').read_text(encoding='utf-8'), args.client)
    header, body = skill.split('\n---\n', 1)
    lines = header.splitlines()[1:]
    top, vendor = [], []
    for line in lines:
        if line == 'metadata:':
            continue
        if line.startswith('  '):
            vendor.append(line[2:])
        else:
            top.append(line)
    version = json.loads(next(line.split(':', 1)[1].strip() for line in vendor if line.startswith('version:')))
    if version != GROUP_VERSION:
        raise ValueError('Group Skill metadata differs from chat_release.py.')
    capabilities = verify_group_desktop(binary, version)
    output.mkdir(parents=True)
    (output / 'SKILL.md').write_text('---\n' + '\n'.join(top + vendor) + '\n---\n' + body, encoding='utf-8')
    for relative in ('scripts/run.ps1', 'Start.cmd', 'Start-WeCom.cmd', 'references/input-and-errors.md', 'references/attachment-content.md', 'agents/openai.yaml'):
        if args.client == 'wechat' and relative == 'Start-WeCom.cmd':
            continue
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, target)
        if target.suffix == '.md':
            target.write_text(target.read_text(encoding='utf-8').replace('(../../../docs/local-chat/', '(../'), encoding='utf-8')
    if args.client:
        label = '企微' if args.client == 'wecom' else '个微'
        skill_name = args.client + '-local-archive'
        (output / 'agents/openai.yaml').write_text('interface:\n' +
            f'  display_name: "{label}-本地聊天解析"\n' +
            f'  short_description: "打开{label}界面，多选群批量解析，逐群人工复核后导出JSONL/JSON/MD"\n' +
            f'  default_prompt: "使用 ${skill_name} 打开{label}聊天解析界面，让我多选群、逐群保存人工复核，再一键导出所选群。"\n', encoding='utf8')
        if args.client == 'wecom':
            (output / 'references/input-and-errors.md').write_text(
                '# 企微输入与故障\n\n'
                '本 Skill 固定读取企业微信，自动复用 `%LOCALAPPDATA%\\LocalWeComArchive`。\n\n'
                '桌面与Skill共用此目录，更新或移动程序不重新初始化；已有配置优先，附近有效旧配置自动迁移。'
                '多个账号或远处旧目录在手动页“导入旧版配置”选择。见[配置迁移说明](../配置迁移说明.md)。\n\n'
                '数据选择单个企微账号目录或 Data，保留 message.db、session.db、user.db、WAL 和账号 Cache。'
                '首次登录本人企微，确认账号后点“一键自动配置”；未登录或布局不支持时保留错误，不切换账号。'
                '企微密钥使用 wecomKeys 格式，不能使用个微的数据库或图片密钥。媒体按本机缓存摘要匹配。\n\n'
                '直接传 -ChatName，搜索只查群索引，重名需确认会话ID。awaiting_review 表示解析预览完成、等待人工；completed 且 humanReview.status=confirmed 才表示已确认导出；'
                'needs_source/needs_chat 等待窗口操作，waiting 读取回执，failed 报告实际错误。'
                '人工确认前不直接Export，不代勾检查项、不自动点击确认；后续只读取本次resultFile。'
                '群列表及当前群顶部显示未复核/已复核；可筛选所选群的复核状态。人工可用“全选复核项”“清空勾选”修改当前群四项检查，仍须点击确认才保存，宿主不得代点。'
                '多选群或没有群名时用 -Mode Desktop 打开界面人工勾选；逐群保存复核后，由用户一键导出所选群，保存复核仍是awaiting_review。一个群的completed不代表其他群已导出。固定统计和正式文件入口以程序 reporting 为准。\n\n'
                '只有用户明确请求本机取钥时使用 -Mode RecoverKeys -Source "本人企微账号目录"；'
                '不扫描其他账号或私有密钥目录，不把密钥正文放入对话。\n\n'
                '目录、密钥格式、缓存和未知消息边界详见 [企微使用说明](../企微使用说明.md)。\n', encoding='utf8')
    (output / 'bin').mkdir()
    binary_relative = 'bin/local-chat-desktop-' + version + '.exe'
    shutil.copyfile(binary, output / binary_relative)
    for relative, path in licenses:
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    runtime = {'version': version, 'platform': 'Windows 10/11 x64',
               'release': capabilities['release'], 'conversationMode': 'group', 'privateAnalysis': False,
               'exeRelativePath': binary_relative,
               'exeSha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
               'requiresPythonInstallation': False, 'containsKeys': False, 'containsChatData': False,
               'humanReviewRequiredBeforeExport': True, 'awaitingReviewState': 'awaiting_review',
               'directExportEnabled': False, 'groupSelection': 'multiple', 'reviewScope': 'per_group',
               'clientBinding': 'manifest' if args.client else 'selectable',
               'exportMode': 'reviewed_selection_batch'}
    if args.client:
        runtime.update(client=args.client, skillName=args.client + '-local-archive')
        profile = 'LocalWeComArchive' if args.client == 'wecom' else 'LocalWechatArchive'
        launcher = ('@echo off\r\nsetlocal\r\nstart "" "%~dp0'+binary_relative.replace('/','\\')+'" --client '+args.client+
                    ' --data-dir "%LOCALAPPDATA%\\'+profile+'"\r\n')
        (output / 'Start.cmd').write_bytes(launcher.encode('ascii'))
        if args.client == 'wecom':
            (output / 'Start-WeCom.cmd').write_bytes(launcher.encode('ascii'))
            write_wecom_diagnostic_launcher(output, binary_relative)
    (output / 'runtime.json').write_text(json.dumps(runtime, indent=2), encoding='utf-8')
    copy_public_guide(project, output, 'skill')
    write_quick_start(output, 'skill')
    if args.client:
        readme = (output / 'README.md').read_text(encoding='utf8')
        start = readme.index('**解析企业微信**')
        end = readme.index('\n\n', start)
        readme = readme[:start] + '本包的 Start.cmd 固定打开' + label + '。解析另一个客户端时导入对应的独立Skill包；两个Skill可同时启用，配置分别保存。' + readme[end:]
        readme = '# ' + label + '-本地聊天解析 Skill\n\n' + f'技能标识：`{skill_name}`；默认客户端固定为{label}，与另一个Skill使用独立配置。\n\n' + readme
        if args.client == 'wecom':
            readme = readme.replace('说“解析示例业务群”', '说“解析企微示例业务群”')
        (output / 'README.md').write_text(readme, encoding='utf8')
    complete_metadata(output, {'platform': 'Windows 10/11 x64', 'interface': 'skill', 'version': version,
                               **({'client': args.client, 'skillName': skill_name} if args.client else {})})
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(output.rglob('*')):
            if path.is_file():
                bundle.write(path, path.relative_to(output).as_posix())
    print(json.dumps({'directory': str(output), 'archive': str(archive), 'bytes': archive.stat().st_size,
                      'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), **runtime}))


if __name__ == '__main__':
    main()
