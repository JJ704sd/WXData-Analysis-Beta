# WXData-Analysis-Beta

Windows 本地个微／企微聊天分析助手，提供群聊和私聊四类采集入口，以及两个消息日报脚本。读取用户选择的数据库副本，解析消息和附件，生成带出处的阅读材料，人工复核后导出 JSON、JSONL 和 Markdown。日报脚本将同一账号的已复核导出整理成每日概览、待跟进线索和可交互的 HTML；私聊还支持画像候选复核和版本管理。

当前版本：**群聊 v71／Skill 1.51.5，私聊 1.20.3，日报 1.1.0**。版本由 [chat_release.py](WeBridge/scripts/chat_release.py) 统一维护。原四类采集助手的基线和修复见 [开发更新说明](docs/development-update.md)；日报提供单文件源码、Windows 便携 EXE 和两个 Skill，见下方 [日报使用入口](#生成每日消息日报)。

## 从源码启动

使用 Windows x64 和带 Tcl/Tk 的 Python 3.14；本轮验证环境为 Python 3.14.6。在仓库根目录使用 PowerShell：

```powershell
uv venv --python 3.14 .venv
uv pip install --python .venv\Scripts\python.exe -r WeBridge\requirements.txt
.\.venv\Scripts\python.exe WeBridge\scripts\local_chat_bootstrap.py --client wechat --conversation-mode group
```

用以下参数选择另外的入口：

| 助手 | `--client` | `--conversation-mode` | 版本 |
| --- | --- | --- | --- |
| 个微群聊 | `wechat` | `group` | 1.51.5 |
| 企微群聊 | `wecom` | `group` | 1.51.5 |
| 个微私聊 | `wechat` | `direct` | 1.20.3 |
| 企微私聊 | `wecom` | `direct` | 1.20.3 |

已允许执行本地 PowerShell 脚本时，也可运行 `./start-assistant.ps1 -Client wecom -ConversationMode direct`；它优先使用仓库内 `.venv`。添加 `-Capabilities` 只查询版本，不读取账号或创建配置。

首次使用先登录有权读取的客户端，在窗口中确认账号、会话、日期和媒体范围，再配置匹配的数据源及密钥。已有设置默认保存在 `%LOCALAPPDATA%\LocalWechatArchive` 或 `%LOCALAPPDATA%\LocalWeComArchive`；私聊画像独立保存在 `%LOCALAPPDATA%\LocalChatPrivateProfiles`。切换群聊／私聊会隔离复核进度，更新源码不清理已有设置或导出。

完成解析后核对来源、条数、正文和缺口，再由用户确认正式导出。私聊画像需要再次复核候选；脚本不会自动调用模型或确认画像。转发、引用和附件保留各自出处，缺失原件、部分提取和未知身份会明确记录。完整步骤见 [操作指南](WeBridge/docs/local-chat/操作指南.md)、[企微使用说明](WeBridge/docs/local-chat/企微使用说明.md) 和 [私聊 Skill](WeBridge/skills/local-wecom-private-analysis/SKILL.md)。

## 生成每日消息日报

直接从 [日报 1.1.0 发布页](https://github.com/JJ704sd/WXData-Analysis-Beta/releases/tag/daily-v1.1.0) 下载对应包。Windows x64 便携包和 Skill 均内置 Python，无需安装 Python、pip 或完整仓库。

| 用途 | 个微 | 企微 |
| --- | --- | --- |
| 直接运行 | `WeChat-Daily-Report-1.1.0-Windows-x64.zip` | `WeCom-Daily-Report-1.1.0-Windows-x64.zip` |
| WorkBuddy 等宿主导入 | `WeChat-Daily-Report-Skill-1.1.0-Windows-x64.zip` | `WeCom-Daily-Report-Skill-1.1.0-Windows-x64.zip` |

便携包解压后双击 `Start.cmd`，输入已复核的导出目录、日期和新的输出目录。WorkBuddy 在“添加技能 → 上传技能”中分别导入 Skill ZIP，宿主需能执行本机 Windows 程序并访问所选目录；[官方导入说明](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)。Skill 直接调用 EXE，无需启用 PowerShell 脚本。纯网页聊天及远端 Linux 沙箱不能直接使用 Windows EXE。

也可单独下载 [个微单文件脚本](standalone/wechat_daily_report.py) 或 [企微单文件脚本](standalone/wecom_daily_report.py)：只需 Python 3.12+，无需 pip 和仓库共用模块；已验证环境为 Python 3.14.6。

命令行使用示例，在各自便携包的解压目录运行。将输入路径替换为原助手已经人工确认的正式导出目录，或含 `batch.json` 的批次；可重复传入 `--export-root` 合并同一账号的群聊与私聊。

```powershell
& .\wechat-daily-report.exe `
  --export-root "D:\ChatExports\wechat-group-reviewed" `
  --export-root "D:\ChatExports\wechat-direct-reviewed" `
  --date 2026-10-27 --output "D:\DailyReports\wechat-2026-10-27"

& .\wecom-daily-report.exe `
  --export-root "D:\ChatExports\wecom-reviewed-batch" `
  --date 2026-10-27 --output "D:\DailyReports\wecom-2026-10-27"
```

成功后直接打开 `report.html`：主界面突出业务概览、待跟进事项、处理进展和需补充信息，支持搜索、筛选及展开原文。消息 ID、来源路径、复核编号、摘要和关联规则保存在 `diagnostics.jsonl`；同目录还有 `report.json`、`evidence.jsonl` 和 `manifest.json`。输出必须使用新目录。

日报按北京时间统计当天消息，默认回看含当天的 7 天寻找请求与后续回应。群内明确发给别人的请求不计入本人线索；“稍后回复”仍待跟进，完成或取消作为待核对信号单列。只统计所选导出，不估算历史未读。完整参数和格式见 [日报脚本说明](docs/daily-reports.md)，1.1.0 的源码、EXE、包和界面检查见 [日报分发验证](docs/daily-delivery-validation.json)。WorkBuddy 元数据已按官方要求适配，客户端内实际导入与真实账号材料尚未验证。

## 开发和构建

```powershell
.\.venv\Scripts\python.exe WeBridge\scripts\sync_chat_release.py --check
.\.venv\Scripts\python.exe WeBridge\scripts\check_runtime_identity.py
.\.venv\Scripts\python.exe WeBridge\scripts\check_assistants.py --report .runtime\validation.json
```

检查使用临时合成数据，包含原生 Tk 界面和 Windows 文件锁场景，需要可用的桌面会话。当前验证结果及范围见 [验证记录](docs/validation.json)；真实账号、原故障电脑恢复和新编译运行包的验证分别记录，不能由源码测试推定。

安装构建依赖后，一次生成两个群聊 Skill、两个私聊 Skill 和共用桌面包：

```powershell
uv pip install --python .venv\Scripts\python.exe -r WeBridge\scripts\requirements-offline-build.txt
& .\WeBridge\scripts\build_all_chat.ps1 -Python "$PWD\.venv\Scripts\python.exe"
```

输出位于 `WeBridge/build/v71/packages/`，四类包的版本、客户端、模式、ZIP 完整性及文件摘要校验通过后生成 `release-matrix.json`。旧 EXE 或不匹配的版本会在打包前被拒绝；已有构建目录不会被覆盖。更新版本时只改 `chat_release.py`，再运行 `sync_chat_release.py` 同步 Skill 元数据。详见 [维护与构建](docs/maintenance.md)。

## 源码入口

| 路径 | 用途 |
| --- | --- |
| `WeBridge/scripts/local_chat_bootstrap.py`、`local_chat_app.py` | 桌面启动和四类界面 |
| `WeBridge/scripts/wechat_daily_report.py`、`wecom_daily_report.py` | 仓库开发入口，使用共用模块 |
| `WeBridge/web_mvp/offline_daily.py`、`offline_daily_html.py` | 已复核导出校验、每日整理与 HTML |
| `standalone/` | 可独立复制的标准库单文件日报脚本，由维护源码自动生成 |
| `WeBridge/scripts/build_daily_reports.ps1`、`package_daily_reports.py` | 日报 EXE、便携包与 WorkBuddy Skill 构建 |
| `WeBridge/web_mvp/offline_desktop.py` | 数据源、解析、复核、恢复与批量导出 |
| `WeBridge/web_mvp/wecom_*.py`、`database_adapter.py` | 双客户端快照及消息读取 |
| `WeBridge/web_mvp/offline_analysis.py`、`offline_documents.py` | 可追溯语料、附件正文及缺口 |
| `WeBridge/web_mvp/private_profiles.py`、`private_insights.py` | 私聊画像版本、证据和本地统计 |
| `WeBridge/skills/`、`WeBridge/scripts/package_four_chat.py` | 宿主协议及统一分发 |
| `poc/wechat_agent_poc/` | 当前个微读取流程依赖的少量共用模块 |

仓库仅包含相关源码、合成测试、说明和验证记录，保留原来的 `WeBridge/` 与 `poc/` 相对路径。来源与组件归属见 [来源说明](THIRD_PARTY_NOTICES.md)。客户端仅支持现有适配的数据布局；真实客户端连通、Windows 11 和宿主导入仍需实际环境验证。
