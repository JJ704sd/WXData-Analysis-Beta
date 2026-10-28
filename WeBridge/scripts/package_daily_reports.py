"""Package verified standalone daily-report executables and portable host Skills."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

from build_daily_standalone import PROJECT, standalone_source
from chat_release import DAILY_REPORT_VERSION


WORKBUDDY_FIELDS = {'name', 'display_name', 'display_name_en', 'description',
                    'description_zh', 'description_en', 'version', 'author'}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def workbuddy_skill(source):
    header, body = source.split('\n---\n', 1)
    fields = [line[2:] if line.startswith('  ') else line
              for line in header.splitlines()[1:] if line != 'metadata:']
    keys = {line.split(':', 1)[0] for line in fields if ':' in line}
    if not WORKBUDDY_FIELDS <= keys:
        raise ValueError('The WorkBuddy Skill metadata is incomplete')
    return '---\n' + '\n'.join(fields) + '\n---\n' + body


def verify_binary(path, client):
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() not in {'PYTHONPATH', 'PYTHONHOME'}}
    result = subprocess.run([str(Path(path).resolve()), '--capabilities'], capture_output=True,
                            encoding='utf-8', timeout=30, env=environment,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        capabilities = json.loads(result.stdout)
    except ValueError as error:
        raise ValueError('The daily-report executable did not return capabilities') from error
    expected = {'client': client, 'version': DAILY_REPORT_VERSION, 'role': 'daily_report',
                'standalone': True, 'requiresRepository': False, 'requiresPythonInstallation': False,
                'readsLiveClient': False, 'callsModelAPI': False}
    if result.returncode or any(capabilities.get(key) != value for key, value in expected.items()):
        raise ValueError('The executable version, client or portability does not match')
    return capabilities


def verify_desktop(path, client):
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() not in {'PYTHONPATH', 'PYTHONHOME', 'TCL_LIBRARY', 'TK_LIBRARY'}}
    with tempfile.TemporaryDirectory(prefix='daily-desktop-components-') as temporary:
        receipt = Path(temporary) / 'desktop.json'
        process = subprocess.run([str(Path(path).resolve()), '--check-desktop', str(receipt)],
                                 capture_output=True, timeout=30, env=environment,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if process.returncode or not receipt.is_file():
            raise ValueError('The packaged desktop could not initialize its Tk components')
        result = json.loads(receipt.read_text(encoding='utf-8'))
    expected = {'ok': True, 'client': client, 'version': DAILY_REPORT_VERSION,
                'desktopUI': True, 'datePickerInitialized': True, 'requiresPythonInstallation': False}
    if any(result.get(key) != value for key, value in expected.items()):
        raise ValueError('The desktop version or runtime does not match')
    return result


def usage(client):
    label = '个微' if client == 'wechat' else '企微'
    binary = client + '-daily-report.exe'
    return ('# ' + label + '本地消息日报 ' + DAILY_REPORT_VERSION + '\n\n'
            'Windows x64 便携版内置 Python，用户无需安装 Python、pip 或下载仓库。解压到本机可写目录；'
            '直接双击 Start.cmd 或 ' + client + '-daily-report-ui.exe 打开桌面窗口，添加已复核的导出目录，'
            '用日历、今天或昨天选择日期，选择保存位置后点击“生成日报”。'
            '生成在后台运行，可取消，整理线索时显示已处理条数；每次自动新建结果目录。'
            '完成后可搜索、筛选线索，选中后点“查看这条原文”直接核对依据，也可打开 HTML 或复制结果路径。'
            '不要在压缩包预览窗口中直接启动。\n\n'
            '线索支持私聊／群聊与进展组合筛选，可搜索完整请求及已关联回应；'
            '选中后“复制这条线索”，包含完整请求、回应和待核对提示。'
            '“打开跟进清单”或 HTML 中“下载待跟进清单”可取得 followups.txt，包含全部候选，'
            '按本人相关、对象待确认、处理或取消待核对分组，不受桌面 500 条预览限制。\n\n'
            '所选材料未找到当日消息时，会提醒核对日期和导出范围；不能据此认定当天没有消息。'
            '有内容缺口时点击“查看待补充信息”，按当天、之前日期或时间待确认筛选，'
            '搜索附件、引用、转发中已读取的正文，再跳转原文核对。当天缺口数与回看材料总数分别显示。\n\n'
            'Skill 包含根目录 SKILL.md、bin/' + binary + ' 和 scripts 中的单文件源码。'
            '在 WorkBuddy 的“添加技能 → 上传技能”中导入对应 Skill ZIP；其他宿主需支持 Skill 目录、'
            'Windows 本机程序执行和访问用户选择的目录。纯网页聊天或远端 Linux 沙箱不能直接运行 Windows EXE。'
            'Skill 直接运行 EXE，不要求启用 PowerShell 脚本或修改系统策略。'
            '包结构与本机运行可独立检查，宿主实际导入与调用需分别验证。\n\n'
            '命令行示例（普通便携包的 EXE 在根目录，Skill 的 EXE 在 bin）：\n\n'
            '```powershell\n& ".\\' + binary + '" --export-root "D:\\ChatExports\\reviewed-batch" `\n'
            '  --date 2026-10-27 --lookback-days 7 --output "D:\\DailyReports\\' + client + '-2026-10-27"\n```\n\n'
            '可重复传入 --export-root 合并同一账号的群聊、私聊；--my-name 可重复设置用户提供的群内称呼。'
            '--capabilities 仅查询客户端、版本及能力，--interactive 打开控制台向导，--desktop 打开桌面。'
            '单文件 ' + client + '_daily_report.py 使用同样参数，需要 Python 3.12+，桌面模式需该 Python 自带 Tcl/Tk；无需 pip。\n\n'
            '输入必须是原四类助手已人工确认的正式导出，含 manifest.json、review.json、messages.jsonl，'
            '或含 batch.json 的批次。不直接读取数据库、不取密钥，不伪造确认状态。'
            '拒绝混账号、混客户端、摘要变化、越界链接及覆盖已有日报。\n\n'
            '默认按北京时间统计当天消息，并回看含当天的 7 天（可选 1～90 天）。'
            '请求、主题和风险按字面规则识别；暂缓继续待跟进，回复、完成或取消均需核对；'
            '群内他人请求不作为本人待办，身份或目标不足时保留未知。'
            '引用、转发和附件保留原归属与缺口，不估算历史未读。\n\n'
            '引用关联支持数字与字符串编号，以已关联的本地来源为准；同一秒的回复只有明确引用时才关联。'
            '异常的群内点名目标保留待确认，不按字符串包含关系推断本人待办。\n\n'
            '成功退出码 0，JSON 含 ok=true 及 HTML/JSON/证据路径；失败退出码 2。'
            '持续文件占用也返回明确失败；程序已做有限重试，释放占用或更换新的输出位置后再运行，不清空材料或复核记录。'
            '打开 report.html 查看搜索、筛选及原文依据；主界面只展示业务重点，技术 ID、来源摘要及关联记录保存在 diagnostics.jsonl。'
            'report.json、evidence.jsonl、manifest.json 位于同目录。'
            '桌面记住上次选择的材料、保存位置和回看设置，日期默认今天，不自动生成；'
            '界面设置及失败日志位于 %LOCALAPPDATA%/LocalChatDailyReports/' + client + '/，原助手配置不变。'
            '报告仅覆盖所选材料，包含消息原文和来源路径。程序不联网；如宿主读取原文，该内容可能进入宿主对话。\n\n'
            'WorkBuddy 格式来源：https://open.workbuddy.cn/docs/skill\n'
            '导入步骤来源：https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market\n')


def _copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def _finish(directory, *, client, kind, format_name):
    rows = [{'path': path.relative_to(directory).as_posix(), 'bytes': path.stat().st_size, 'sha256': sha256(path)}
            for path in sorted(directory.rglob('*')) if path.is_file()]
    metadata = {'schemaVersion': 1, 'version': DAILY_REPORT_VERSION, 'client': client,
                'role': 'daily_report', 'delivery': kind, 'skillFormat': format_name,
                'requiresPythonInstallation': False, 'requiresRepository': False,
                'containsChatData': False, 'containsKeys': False, 'containsLocalSettings': False, 'files': rows}
    (directory / 'package.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (directory / 'SHA256SUMS.txt').write_text(''.join(sha256(path) + '  ' + path.relative_to(directory).as_posix() + '\n'
         for path in sorted(directory.rglob('*')) if path.is_file()), encoding='utf-8')
    archive = Path(str(directory) + '.zip')
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(directory.rglob('*')):
            if path.is_file():
                bundle.write(path, path.relative_to(directory).as_posix())
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None:
            raise ValueError('The generated archive failed its CRC check')
        for row in rows:
            if hashlib.sha256(bundle.read(row['path'])).hexdigest() != row['sha256']:
                raise ValueError('The archived file differs from the verified package')
    return {'client': client, 'delivery': kind, 'directory': directory.name, 'archive': archive.name,
            'version': DAILY_REPORT_VERSION, 'bytes': archive.stat().st_size, 'sha256': sha256(archive)}


def package_all(binary_dir, standalone_dir, output_root, *, python_license, pyinstaller_license, tk_license,
                skill_format='workbuddy', project=PROJECT):
    if skill_format not in {'workbuddy', 'standard'}:
        raise ValueError('Unknown Skill format')
    binary_dir, standalone_dir, output_root = map(lambda path: Path(path).resolve(), (binary_dir, standalone_dir, output_root))
    if output_root.exists():
        raise ValueError('Choose a new package output directory')
    for path in (Path(python_license), Path(pyinstaller_license), Path(tk_license)):
        if not path.is_file() or path.stat().st_size < 100:
            raise ValueError('A complete Python and PyInstaller license is required')
    capabilities, desktops = {}, {}
    for client in ('wechat', 'wecom'):
        script = standalone_dir / (client + '_daily_report.py')
        if script.read_text(encoding='utf-8') != standalone_source(client, project):
            raise ValueError('The standalone script is stale; regenerate it before packaging')
        capabilities[client] = verify_binary(binary_dir / (client + '-daily-report.exe'), client)
        desktops[client] = verify_desktop(binary_dir / (client + '-daily-report-ui.exe'), client)
    output_root.mkdir(parents=True)
    products = []
    for client, title in (('wechat', 'WeChat'), ('wecom', 'WeCom')):
        for kind in ('portable', 'skill'):
            name = title + '-Daily-Report-' + ('Skill-' if kind == 'skill' else '') + DAILY_REPORT_VERSION + '-Windows-x64'
            if kind == 'skill' and skill_format == 'standard':
                name += '-Standard'
            directory = output_root / name
            directory.mkdir()
            binary_name = client + '-daily-report.exe'
            relative = ('bin/' if kind == 'skill' else '') + binary_name
            _copy(binary_dir / binary_name, directory / relative)
            ui_relative = ('bin/' if kind == 'skill' else '') + client + '-daily-report-ui.exe'
            _copy(binary_dir / (client + '-daily-report-ui.exe'), directory / ui_relative)
            _copy(standalone_dir / (client + '_daily_report.py'), directory / ('scripts/' if kind == 'skill' else '') / (client + '_daily_report.py'))
            runtime = {'schemaVersion': 1, 'role': 'daily_report', 'version': DAILY_REPORT_VERSION,
                       'client': client, 'skillName': client + '-daily-report', 'platform': 'Windows x64',
                       'exeRelativePath': relative, 'exeSha256': sha256(directory / relative),
                       'desktopRelativePath': ui_relative, 'desktopSha256': sha256(directory / ui_relative),
                       'requiresPythonInstallation': False, 'requiresRepository': False,
                       'containsKeys': False, 'containsChatData': False, 'containsLocalSettings': False}
            (directory / 'runtime.json').write_text(json.dumps(runtime, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            launcher = '@echo off\r\nstart "" "%~dp0' + ui_relative.replace('/', '\\') + '" --desktop\r\n'
            (directory / 'Start.cmd').write_bytes(launcher.encode('ascii'))
            if kind == 'skill':
                template = project / 'skills' / (client + '-daily-report')
                body = (template / 'SKILL.md').read_text(encoding='utf-8')
                if ('version: "' + DAILY_REPORT_VERSION + '"') not in body:
                    raise ValueError('The daily Skill version is stale')
                (directory / 'SKILL.md').write_text(workbuddy_skill(body) if skill_format == 'workbuddy' else body, encoding='utf-8')
                _copy(template / 'agents/openai.yaml', directory / 'agents/openai.yaml')
                (directory / 'references').mkdir()
                (directory / 'references/usage.md').write_text(usage(client), encoding='utf-8')
            else:
                (directory / 'README.md').write_text(usage(client), encoding='utf-8')
            _copy(Path(python_license), directory / 'licenses/Python-LICENSE.txt')
            _copy(Path(pyinstaller_license), directory / 'licenses/PyInstaller-COPYING.txt')
            _copy(project / 'scripts/licenses/Tcl-8.6.15-license.terms', directory / 'licenses/Tcl-license.terms')
            _copy(Path(tk_license), directory / 'licenses/Tk-license.terms')
            notice = (project.parent / 'THIRD_PARTY_NOTICES.md').read_text(encoding='utf-8')
            notice = notice.replace('(docs/', '(https://github.com/JJ704sd/WXData-Analysis-Beta/blob/main/docs/')
            (directory / 'licenses/SOURCE-NOTICE.md').write_text(notice, encoding='utf-8')
            products.append(_finish(directory, client=client, kind=kind, format_name=skill_format if kind == 'skill' else None))
    matrix = {'schemaVersion': 1, 'role': 'daily_report_delivery', 'version': DAILY_REPORT_VERSION,
              'products': products, 'capabilities': capabilities, 'desktopComponents': desktops,
              'realAccountValidation': False, 'hostSkillImportValidation': False}
    (output_root / 'release-matrix.json').write_text(json.dumps(matrix, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return matrix


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary-dir', required=True, type=Path)
    parser.add_argument('--standalone-dir', required=True, type=Path)
    parser.add_argument('--output-root', required=True, type=Path)
    parser.add_argument('--python-license', required=True, type=Path)
    parser.add_argument('--pyinstaller-license', required=True, type=Path)
    parser.add_argument('--tk-license', required=True, type=Path)
    parser.add_argument('--skill-format', choices=('workbuddy', 'standard'), default='workbuddy')
    args = parser.parse_args(argv)
    print(json.dumps(package_all(args.binary_dir, args.standalone_dir, args.output_root,
          python_license=args.python_license, pyinstaller_license=args.pyinstaller_license, tk_license=args.tk_license,
          skill_format=args.skill_format), ensure_ascii=False))


if __name__ == '__main__':
    main()
