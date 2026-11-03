"""Compose the two complete, documented Windows packages into one handoff ZIP."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

from local_chat_distribution import copy_public_guide, complete_metadata, GUIDE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--desktop-package', type=Path, required=True)
    parser.add_argument('--skill-package', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    archive = output.parent / (output.name + '.zip')
    if output.exists() or archive.exists():
        raise SystemExit('Choose a new delivery directory.')
    sources = [args.desktop_package.resolve(), *(path.resolve() for path in args.skill_package)]
    packages = []
    for directory in sources:
        metadata = json.loads((directory / 'package.json').read_text(encoding='utf-8'))
        if metadata.get('distribution') != 'portable-shareable' or metadata.get('containsKeys') is not False or metadata.get('containsChatData') is not False:
            raise SystemExit('Use the verified shareable packages, not a working-data folder.')
        source_zip = directory.parent / (directory.name + '.zip')
        with zipfile.ZipFile(source_zip) as bundle:
            if bundle.testzip() is not None:
                raise SystemExit('Source archive failed its integrity check.')
        packages.append(source_zip)
    output.mkdir(parents=True)
    for source in packages:
        shutil.copyfile(source, output / source.name)
    copy_public_guide(project, output, 'suite')
    readme = '# 本地微信聊天解析：Windows完整交付包\n\n'
    readme += '先看 [操作指南](操作指南.md)，按使用方式选择下面一个独立ZIP。版本、实测范围和限制见 [版本与验证说明](版本与验证说明.md)。\n\n'
    readme += '请选择以下一种方式使用，无需同时安装两个版本，也无需安装Python。\n\n'
    readme += '**两个Skill分开导入**：个微使用“个微-本地聊天解析”，企微使用“企微-本地聊天解析”。各自默认客户端已绑定，配置和密钥隔离。桌面包仍包含Start.cmd与Start-WeCom.cmd。\n\n'
    readme += f'## 直接双击使用\n\n解压 [{packages[0].name}]({packages[0].name})，进入其中的文件夹。个微双击 `Start.cmd`，企微双击 `Start-WeCom.cmd`；两端账号配置相互独立。\n\n'
    readme += '## 在WorkBuddy中使用\n\n在技能页面“添加技能 → 上传技能”导入所需Skill ZIP并启用，也可同时启用两个：\n\n'
    for source_zip, source_dir in zip(packages[1:], sources[1:]):
        client = json.loads((source_dir / 'runtime.json').read_text(encoding='utf8')).get('client', 'wechat')
        label, phrase = ('企微-本地聊天解析', '打开企微界面让我选择群聊，人工确认前不要导出') if client == 'wecom' else ('个微-本地聊天解析', '打开个微界面让我选择群聊，人工确认前不要导出')
        readme += f'- [{label} ZIP]({source_zip.name})：新任务说“{phrase}”。\n'
    readme += '\n窗口先展示本地群ID与名称，支持多选、全选/取消和反选。也可明确提供实际群名，只解析指定群。其他宿主需支持本地Skill执行和文件访问。\n\n'
    readme += '**导入的是里面的Skill ZIP，不能把本总包作为Skill导入。** Skill ZIP本身包含SKILL.md、EXE、启动脚本及全部许可文件，不要只取SKILL.md。\n\n'
    readme += '每群解析完成即可阅读，顶部提示已完成数量和最近完成群；蓝色为已解析待复核，绿色为已复核。队列结束或停止后可逐群或批量人工复核，再点“一键导出所选群”，生成JSONL、JSON、Markdown、复核记录及缺口说明。查看文件与打开所在目录使用窗口中的对应按钮。转移结果时保留整个导出目录及assets；更新或换电脑的具体操作见操作指南。\n\n'
    readme += '## 演示与首次配置\n\n分发包不带账号配置、密钥或聊天缓存。当前电脑是否首次配置取决于本机用户目录：个微 `%LOCALAPPDATA%\\LocalWechatArchive`，企微 `%LOCALAPPDATA%\\LocalWeComArchive`；重新解压会复用已有设置。演示首次配置时应使用已清空缓存的本机环境，或显式指定独立DataDir。保留原始聊天和已导出文件。\n\n'
    readme += '## 第一次或换电脑\n\n先手动登录本人微信，在弹窗确认发现的账号并点“一键自动配置”，无需寻找密钥JSON；多个账号先选一个。校验加载成功后Skill原解析请求会继续并记住设置。已有离线副本和密钥也可切到手动页选择；未登录、布局不支持或缺少匹配密钥时会明确失败。需要本人有权读取的微信4.x副本；保留相关WAL、附件索引和媒体。\n\n'
    readme += '目标为Windows 10/11 x64；已完成Windows 10本机隔离验证，另一台物理机和Windows 11尚未实测。Office、PDF及视频附件需已有默认查看器；程序内图片和转发展示不依赖Office。企业执行策略限制由IT按组织规则处理。\n\n'
    readme += f'详细功能、环境要求和限制见 [{GUIDE}]({GUIDE})。\n\n'
    readme += '## 包内与校验\n\n各独立包及本总包均提供SHA256SUMS.txt和package.json。包内不含真实聊天、数据库、媒体、密钥、本机账号设置或真实会话分享链接。收到包后先完整解压再运行。\n'
    (output / 'README.md').write_text(readme, encoding='utf-8')
    complete_metadata(output, {'platform': 'Windows 10/11 x64', 'interface': 'delivery-suite',
                               'includes': [path.name for path in packages]})
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_STORED) as bundle:
        for path in sorted(output.iterdir()):
            if path.is_file():
                bundle.write(path, output.name + '/' + path.name)
    print(json.dumps({'directory': str(output), 'archive': str(archive), 'bytes': archive.stat().st_size,
                      'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
