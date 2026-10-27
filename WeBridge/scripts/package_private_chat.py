"""Build allowlisted WeChat/WeCom private-analysis Skill ZIPs.

The caller must provide the exact desktop and profile CLI executables.  This
script never searches build directories and never reads account or chat data.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

from chat_release import PRIVATE_VERSION


CLIENTS = (
    ("wechat", "local-wechat-private-analysis", "个微", "LocalWechatArchive"),
    ("wecom", "local-wecom-private-analysis", "企微", "LocalWeComArchive"),
)
MAX_LICENSE_FILES = 256
MAX_LICENSE_FILE_BYTES = 4 * 1024 * 1024
MAX_LICENSE_TOTAL_BYTES = 32 * 1024 * 1024
LICENSE_NAME_RE = re.compile(
    r"^(?:[a-z0-9]+[._-])*?(?:license|licence|copying|notice|authors)(?:[._-][a-z0-9]+)*$|^license\.terms$",
    re.I,
)
def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def probe_capabilities(path: Path, role: str, expected_version: str) -> dict[str, object]:
    result_path: Path | None = None
    try:
        if role == "desktop":
            with tempfile.TemporaryDirectory(prefix="private-capabilities-") as temporary:
                result_path = Path(temporary) / "result.json"
                result = subprocess.run([str(path), "--capabilities", "--conversation-mode", "direct",
                                         "--result-json", str(result_path)],
                                        capture_output=True, text=True, encoding="utf-8",
                                        timeout=30, check=False)
                if result.returncode != 0:
                    raise SystemExit(f"{role} capability probe failed")
                if not result_path.is_file():
                    raise SystemExit(f"{role} capability probe returned no result file")
                raw = result_path.read_text(encoding="utf-8")
        else:
            result = subprocess.run([str(path), "--capabilities"], capture_output=True,
                                    text=True, encoding="utf-8", timeout=30, check=False)
            if result.returncode != 0:
                raise SystemExit(f"{role} capability probe failed")
            raw = result.stdout
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise SystemExit(f"unable to probe {role} capabilities") from exc
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"{role} capability probe did not return JSON") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{role} capability probe returned an invalid object")
    required = {"schemaVersion", "privateAnalysis", "conversationMode", "role", "version"}
    if not required.issubset(value):
        raise SystemExit(f"{role} capability probe is missing required fields")
    if (value.get("schemaVersion") != 1 or value.get("privateAnalysis") is not True
            or value.get("conversationMode") != "direct" or value.get("role") != role
            or value.get("version") != expected_version):
        raise SystemExit(f"{role} is not the requested private direct build")
    return value


def validate_executable(path: Path, role: str, expected_version: str) -> tuple[Path, dict[str, object]]:
    path = path.resolve()
    if path.suffix.lower() != ".exe" or not path.is_file():
        raise SystemExit(f"{role} must be an existing explicit .exe path")
    return path, probe_capabilities(path, role, expected_version)


def copy_allowlisted(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def _is_reparse(path: Path) -> bool:
    """Reject symlinks and Windows junctions without following them."""
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    try:
        attributes = path.stat(follow_symlinks=False).st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _absolute_without_resolving(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _assert_no_reparse_in_parents(path: Path) -> Path:
    absolute = _absolute_without_resolving(path)
    for parent in (absolute, *absolute.parents):
        if _is_reparse(parent):
            raise SystemExit(f"license source contains a symlink or junction: {parent}")
    return absolute


def _validate_license_file(path: Path, *, count: int, total_bytes: int) -> tuple[Path, int, int]:
    absolute = _assert_no_reparse_in_parents(path)
    if not absolute.is_file() or _is_reparse(absolute):
        raise SystemExit(f"invalid license file: {absolute}")
    if not LICENSE_NAME_RE.fullmatch(absolute.name):
        raise SystemExit(f"non-license file in license source: {absolute.name}")
    size = absolute.stat(follow_symlinks=False).st_size
    if size > MAX_LICENSE_FILE_BYTES:
        raise SystemExit(f"license file is too large: {absolute.name}")
    count += 1
    total_bytes += size
    if count > MAX_LICENSE_FILES:
        raise SystemExit("too many license files")
    if total_bytes > MAX_LICENSE_TOTAL_BYTES:
        raise SystemExit("license files exceed the total size limit")
    return absolute, count, total_bytes


def _explicit_license_tree(root: Path) -> list[tuple[Path, Path]]:
    absolute_root = _assert_no_reparse_in_parents(root)
    if not absolute_root.is_dir() or _is_reparse(absolute_root):
        raise SystemExit("--licenses-dir must name a real directory")
    selected: list[tuple[Path, Path]] = []
    count = total_bytes = 0
    pending = [absolute_root]
    while pending:
        current = pending.pop()
        try:
            entries = sorted(os.scandir(current), key=lambda item: item.name.lower())
        except OSError as exc:
            raise SystemExit("cannot read the explicit license directory") from exc
        for entry in entries:
            child = Path(entry.path)
            if _is_reparse(child):
                raise SystemExit(f"license source contains a symlink or junction: {child}")
            if entry.is_dir(follow_symlinks=False):
                pending.append(child)
                continue
            if not entry.is_file(follow_symlinks=False):
                raise SystemExit(f"unsupported entry in license source: {entry.name}")
            child, count, total_bytes = _validate_license_file(child, count=count, total_bytes=total_bytes)
            selected.append((child, Path("licenses") / child.relative_to(absolute_root)))
    return selected


def file_rows(root: Path) -> list[dict[str, object]]:
    rows = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name in {"package.json", "SHA256SUMS.txt"}:
            continue
        rows.append({"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size,
                     "sha256": sha256(path)})
    return rows


def license_files(project: Path, explicit_dir: Path | None) -> list[tuple[Path, Path]]:
    """Return a deterministic, allowlisted set of redistribution licenses."""
    selected: list[tuple[Path, Path]] = []
    if explicit_dir is not None:
        selected.extend(_explicit_license_tree(explicit_dir))
    else:
        local = project / "scripts/licenses"
        if local.is_dir():
            selected.extend(_explicit_license_tree(local))
        base = Path(sys.base_prefix)
        for source, target in ((base / "LICENSE.txt", Path("licenses/Python-LICENSE.txt")),
                               (base / "tcl/tk8.6/license.terms", Path("licenses/Tk-8.6-license.terms"))):
            if source.is_file():
                checked, _, _ = _validate_license_file(source, count=0, total_bytes=0)
                selected.append((checked, target))
        dependency_root = project / ".runtime/offline-build-deps"
        names = {"altgraph", "cffi", "cryptography", "msoffcrypto-tool", "olefile", "pefile", "pypdf",
                 "pycparser", "pyinstaller", "pillow", "xlrd", "zstandard"}
        for distribution in importlib.metadata.distributions(path=[str(dependency_root), *sys.path]):
            name = (distribution.metadata.get("Name") or "").lower()
            if name not in names:
                continue
            for item in distribution.files or ():
                listed = str(item).replace("\\", "/")
                modern = "/licenses/" in listed
                legacy = Path(listed).name.upper() in {"LICENSE", "LICENSE.TXT", "COPYING"}
                if not modern and not legacy:
                    continue
                source = Path(distribution.locate_file(item))
                if source.is_file():
                    checked, _, _ = _validate_license_file(source, count=0, total_bytes=0)
                    suffix = Path(listed.split("licenses/", 1)[1]) if modern else Path(listed).name
                    selected.append((checked, Path("licenses") / name / suffix))
    if not selected:
        raise SystemExit("no redistribution licenses were found")
    if len(selected) > MAX_LICENSE_FILES:
        raise SystemExit("too many license files")
    total_bytes = 0
    for source, _ in selected:
        if _is_reparse(source):
            raise SystemExit(f"license source contains a symlink or junction: {source}")
        size = source.stat(follow_symlinks=False).st_size
        if size > MAX_LICENSE_FILE_BYTES:
            raise SystemExit(f"license file is too large: {source.name}")
        total_bytes += size
    if total_bytes > MAX_LICENSE_TOTAL_BYTES:
        raise SystemExit("license files exceed the total size limit")
    # Do not let a directory with duplicate relative names silently replace a
    # license; explicit input must be unambiguous.
    seen: dict[Path, Path] = {}
    unique: list[tuple[Path, Path]] = []
    for source, target in selected:
        previous = seen.get(target)
        if previous is not None:
            source_hash = sha256(source)
            if sha256(previous) == source_hash:
                continue
            target = target.with_name(target.stem + "-" + source_hash[:8] + target.suffix)
            while target in seen:
                target = target.with_name(target.stem + "-copy" + target.suffix)
        seen[target] = source
        unique.append((source, target))
    return unique


def write_metadata(root: Path, *, client: str, name: str, version: str,
                   desktop: Path, profile: Path) -> None:
    metadata = {
        "distribution": "portable-shareable",
        "interface": "skill",
        "version": version,
        "skillName": name,
        "client": client,
        "conversationMode": "direct",
        "privateAnalysis": True,
        "containsChatData": False,
        "containsKeys": False,
        "containsLocalSettings": False,
        "desktopExeSha256": sha256(desktop),
        "profileCliSha256": sha256(profile),
        "files": file_rows(root),
    }
    (root / "package.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            lines.append(f"{sha256(path)}  {path.relative_to(root).as_posix()}")
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def package_one(project: Path, output_root: Path, *, client: str, skill_name: str,
                label: str, profile_dir: str, version: str, desktop: Path,
                profile: Path, licenses: list[tuple[Path, Path]]) -> dict[str, object]:
    source = project / "skills" / skill_name
    if not (source / "SKILL.md").is_file() or not (source / "scripts" / "run.ps1").is_file():
        raise SystemExit(f"source Skill is incomplete: {skill_name}")
    directory = output_root / f"{skill_name}-{version}-windows-x64"
    archive = Path(str(directory) + ".zip")
    if directory.exists() or archive.exists():
        raise SystemExit(f"output already exists: {directory}")
    directory.mkdir(parents=True)
    for relative in ("SKILL.md", "Start.cmd", "scripts/run.ps1", "agents/openai.yaml"):
        copy_allowlisted(source / relative, directory / relative)
    copy_allowlisted(desktop, directory / "bin" / "local-chat-desktop.exe")
    copy_allowlisted(profile, directory / "bin" / "private-chat-profile.exe")
    for source, relative in licenses:
        copy_allowlisted(source, directory / relative)
    runtime = {
        "version": version,
        "platform": "Windows 10/11 x64",
        "client": client,
        "skillName": skill_name,
        "conversationMode": "direct",
        "privateAnalysis": True,
        "exeRelativePath": "bin/local-chat-desktop.exe",
        "exeSha256": sha256(desktop),
        "desktopExeRelativePath": "bin/local-chat-desktop.exe",
        "desktopExeSha256": sha256(desktop),
        "profileCliRelativePath": "bin/private-chat-profile.exe",
        "profileCliSha256": sha256(profile),
        "profileStoreDefault": "%LOCALAPPDATA%/LocalChatPrivateProfiles",
        "desktopDataDirectory": "%LOCALAPPDATA%/" + profile_dir,
        "containsKeys": False,
        "containsChatData": False,
        "humanReviewRequiredBeforeProfile": True,
        "profileConfirmationRequired": True,
    }
    (directory / "runtime.json").write_text(json.dumps(runtime, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if client == 'wecom':
        from local_chat_distribution import write_wecom_diagnostic_launcher
        write_wecom_diagnostic_launcher(directory, 'bin/local-chat-desktop.exe')
    (directory / "README.md").write_text(
        f"# {label}私聊分析 Skill\n\n"
        "## 使用\n\n"
        "导入本 ZIP 后使用 `Start.cmd` 或 Skill wrapper 打开私聊桌面入口。选择联系人和日期范围，"
        "完成源聊天人工复核并正式导出后，才可运行 `ProfilePrepare`。宿主模型读取请求包指定材料，"
        "在材料窗口点击“复制分析任务”，交给宿主生成带 `baseRevision`、`coveredChunks` 和证据引用的候选 JSON；"
        "回到该窗口点击“导入候选并复核”，由用户确认后才写入正式画像。CLI 的 `ProfileStage` / `ProfileReview` 路径继续可用。"
        "正式聊天导出目录与画像库需要一并保留和备份；源文件移动、删除或改写后不能继续复核或增量分析。\n\n"
        "## 1.20 内容完整性与复核提示\n\n"
        "私聊统计覆盖根消息、转发与引用内部的附件提取缺口；区分有文字、无正文和状态未记录，并按实际证据显示附件或 OCR 复核提示。可在内容完整性页查看，并保存为 JSON、Markdown 和 HTML 报告。\n\n"
        "## 1.19 旧版表格批注与隐藏幻灯片\n\n"
        "补齐旧版 Excel 的链接、文本标记、批注正文与作者；PPTX 隐藏页及其备注、图表、链接、图片来源统一标明隐藏状态。新增内容沿用人工复核流程。\n\n"
        "## 1.18 分析材料增量与缺口\n\n"
        "修复分析语料补齐后的增量漏检；转发截断、附件 OCR 复核要求和提取缺口随阅读材料保留。旧画像来源与候选保持兼容，新增分析内容仍需人工复核。\n\n"
        "## 1.17 兼容分支与批注\n\n"
        "Word 兼容内容统一选择一个分支，避免文字、批注和图片引用重复。PDF 文本批注单独保留来源、作者日期和回复关系。"
        "文件中的批注状态不代表业务结论已确认；旧材料需重新解析和复核。\n\n"
        "## 1.16 PDF 与演示链接\n\n"
        "PDF 与 PPTX 保留文件实际引用的链接目标、页码或幻灯片及相关位置，支持按地址检索。"
        "链接仅作为材料，不打开目标或执行动作；旧材料需重新解析和复核。\n\n"
        "## 1.15 隐藏状态与演示表格\n\n"
        "Excel 隐藏工作表、行和列保留原值并加文件状态标记；PPTX 表格保留行列、空值和合并声明。"
        "不推测隐藏原因，不复制合并锚点值；历史结果需要重新解析和复核。\n\n"
        "## 1.14 附注与共享公式\n\n"
        "Word 脚注、尾注与批注按引用 ID 关联；XLSX 补齐经典批注和共享公式锚点原式。"
        "批注作者是文件记录，公式值是文件缓存，不代表聊天身份或重新计算结果。\n\n"
        "## 1.13 表单与合并范围\n\n"
        "补齐 PDF AcroForm 已存字段值与 Excel 合并范围，保留字段页码和合并锚点来源。"
        "默认值不当作填写内容，合并范围不逐格扩展；要补齐历史结果，需重新解析和复核。\n\n"
        "## 1.12 旧版表格与转发信息\n\n"
        "旧版 XLS 补齐日期、百分比、编号、布尔和错误值的可读含义；个微转发链接标题及转发说明进入阅读、分析与搜索。"
        "复杂数字格式仍保留原值和格式说明，不执行公式或打开链接。\n\n"
        "## 1.11 附件链接与表格关联\n\n"
        "识别带 BOM 的 UTF-16/UTF-32 文本，保留 Word/Excel 实际引用的超链接目标，补齐 Word 纵向合并字段并标明继承来源。撤回通知只显示可读提示，关联消息 ID 仍保留。链接仅作为材料，不访问外部地址。\n\n"
        "## 1.10 附件信息与检索速度\n\n"
        "按工作簿格式解读日期、百分比与编号，补齐幻灯片图表缓存；长附件搜索只为当前页生成预览。"
        "引用回复中的本人原话可作为对应主体依据，被引第三方原文仍分开处理。\n\n"
        "## 1.9 信息解析与检索\n\n"
        "补齐Word内容控件、引用附件正文和卡片字段；检索覆盖已提取附件与转发引用，并标明命中来源。"
        "长附件全文补充按本次分析范围提供；引用内容不作为对方或本人直接陈述。\n\n"
        "## 1.8.1 异常中断恢复\n\n"
        "异常中断时释放消息查询游标和数据库文件占用，保留已有复核结果，允许单独重试失败会话。\n\n"
        "## 1.8 内容质量与速度\n\n"
        "同步企微引用索引、附件正文异常字符、原件补齐与诊断日志更新。重复解析同一内容时，"
        "在当前数据源会话内复用已校验正文；每次仍核对原件摘要，消息来源和人工复核保持独立。"
        "失败、超时或未完成 OCR 不跨次复用；重新加载数据源后缓存重新建立。\n\n"
        "## 1.7 界面与导出恢复\n\n"
        "私聊入口按解析记录、复核导出、分析画像三阶段组织；统计页突出聊天足迹，画像库和技术 ID 按需展开，"
        "不把技术标识堆在主要阅读区域。重新解析失败时保留旧复核；已导出会话重启后先校验再恢复。"
        "清单失败时保留正文，清单可单独修复；重试按会话进行，不承诺单文件字节续传。"
        "升级时请保留原工作目录缓存、正式导出和画像库；恢复不会覆盖会话正文。Windows 旧锁检查只查询进程状态，不发送控制台信号。\n\n"
        "## 1.6 宿主分析衔接\n\n"
        "材料窗口可复制完整分析任务，桌面可直接导入宿主生成的候选 JSON 并打开复核。导入会核对当前客户端、账号和联系人；"
        "从材料窗口导入还会核对该批请求。切换会话后请返回对应材料会话操作；原画像库中的待复核候选仍可通过更多设置打开。\n\n"
        "## 1.5 持续使用优化\n\n"
        "搜索与旧消息回顾统一使用已校验的分析引用，非分析消息仅按消息 ID 定位原文。"
        "回顾准备失败会显示原因并支持重试；取消保存或主窗口忙碌时不再误报已提交。\n\n"
        "本人总览串行发布 JSON 和 Markdown，避免并发调用写入不同内容。\n\n"
        "## 1.4 可靠性优化\n\n"
        "升级后可继续读取旧版请求、候选和已确认画像；重复确认旧批次时，后续增量采用最后确认的内容。"
        "严格检查消息 ID 和引用序号，排除类型冲突或系统会话，避免把错误记录纳入个人画像。\n\n"
        "## 1.3 功能补全\n\n"
        "桌面“更多设置”新增“查看已确认画像”和“本人沟通总览”，从当前正式导出识别账号，支持观察筛选与原文证据查阅。"
        "增量材料窗口显示并复制当前画像基线路径；无变化材料无需再生成候选。本人总览按账号隔离，统计覆盖包含表情和媒体消息。\n\n"
        "## 1.1 聊天时光\n\n"
        "桌面 UI 在已复核导出范围内提供日历热度、带出处的随机旧消息回顾，以及“方案”“下周”“发你”“确认”四个快捷字面查询。"
        "这些只是线索，不等于承诺，也不评分关系。独立 JSON、Markdown、HTML 报告默认不含消息原文；需要原文时回到正式导出和消息出处。"
        "ProfileInsights CLI 只提供统计、字面关键词、分页和报告参数，不承诺随机或日历接口；指定输出目录时必须是位于 formal export 和 profile store 之外的新目录。\n\n"
        "## 1.2 界面更新\n\n"
        "私聊主页、聊天足迹与画像复核统一为浅绿主题、清楚的分区和较大的字体。概览突出四项指标与趋势图，"
        "日历与原文回顾单独成页；观察和原消息可并排核对。解析、统计和画像确认规则保持兼容。\n\n"
        "## 限制\n\n"
        "仅支持当前绑定客户端的私聊；群聊、公众号和系统通知不纳入。窗口启动不等于解析完成，"
        "候选校验通过不等于画像已确认。脚本不调用模型 API，不自动合并个微与企微身份，"
        "不推测敏感属性或进行心理诊断。包内不含账号配置、密钥、聊天或媒体数据。\n\n"
        "许可文件见 `licenses/`，文件校验见 `SHA256SUMS.txt`。\n", encoding="utf-8")
    write_metadata(directory, client=client, name=skill_name, version=version,
                   desktop=desktop, profile=profile)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(directory).as_posix())
    return {"client": client, "skillName": skill_name, "directory": str(directory),
            "archive": str(archive), "bytes": archive.stat().st_size,
            "sha256": sha256(archive), "desktopExeSha256": sha256(desktop),
            "profileCliSha256": sha256(profile), "conversationMode": "direct",
            "privateAnalysis": True, "label": label}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--desktop-exe", required=True, type=Path,
                        help="explicit newly-built private-analysis desktop EXE")
    parser.add_argument("--profile-cli-exe", required=True, type=Path,
                        help="explicit newly-built private profile CLI EXE")
    parser.add_argument("--output-root", "--output-dir", dest="output_root", required=True, type=Path,
                        help="new output directory for the two Skill packages")
    parser.add_argument("--licenses-dir", type=Path,
                        help="optional explicit allowlisted license directory")
    parser.add_argument("--version", default=PRIVATE_VERSION, help="package version")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", args.version):
        raise SystemExit("--version must be a semantic version")
    if args.version != PRIVATE_VERSION:
        raise SystemExit("--version must match chat_release.py and the source Skill metadata")
    desktop, desktop_capabilities = validate_executable(args.desktop_exe, "desktop", args.version)
    profile, profile_capabilities = validate_executable(args.profile_cli_exe, "profile", args.version)
    if desktop == profile or sha256(desktop) == sha256(profile):
        raise SystemExit("desktop and profile CLI must be distinct executables")
    project = Path(__file__).resolve().parents[1]
    licenses = license_files(project, args.licenses_dir)
    output_root = args.output_root.resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise SystemExit("output root must be new or empty")
    output_root.mkdir(parents=True, exist_ok=True)
    results = [package_one(project, output_root, client=client, skill_name=skill,
                           label=label, profile_dir=profile_dir, version=args.version,
                           desktop=desktop, profile=profile, licenses=licenses)
               for client, skill, label, profile_dir in CLIENTS]
    print(json.dumps({"packages": results, "desktopExe": str(desktop),
                      "profileCliExe": str(profile), "containsChatData": False,
                      "containsKeys": False, "desktopCapabilities": desktop_capabilities,
                      "profileCapabilities": profile_capabilities}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
