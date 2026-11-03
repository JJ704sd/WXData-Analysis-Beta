# WXData-Analysis-Beta

Windows 本地个微／企微聊天分析助手，提供群聊和私聊四类采集入口，以及两个消息日报脚本。读取用户选择的数据库副本，解析消息和附件，生成带出处的阅读材料，人工复核后导出 JSON、JSONL 和 Markdown。日报脚本将同一账号的已复核导出整理成每日概览、待跟进线索和可交互的 HTML；私聊还支持画像候选复核和版本管理。

本源码副本在 v80 基线上新增**供应商画像材料**，版本为 **群聊 v81／Skill 1.51.15，私聊 1.20.13**；版本由 [chat_release.py](WeBridge/scripts/chat_release.py) 统一维护。先解析并人工复核导出，再通过“更多设置 → 供应商画像材料…”指定对象、时段和群成员，生成可回查的业务线索与画像草稿模板。操作和身份边界见 [供应商画像材料说明](WeBridge/docs/local-chat/供应商画像材料-v81.md)，接手事项见 [交接报告](docs/供应商画像交接-v81.md)。

本副本附带的日报仍为 1.4.0；原 `WXData-Analysis-Beta` 工作区中的日报 1.8.0 独立保留。v76 企微文字字段修复见 [v76 说明](WeBridge/docs/local-chat/企微已知文字字段完整性修复-v76.md)，更早的附件、链接及媒体修复说明继续保留。

## 下载 v81 Windows 包

在 Windows x64 上，按客户端和会话类型选择对应的包；也可在 [v81 发布页](https://github.com/JJ704sd/WXData-Analysis-Beta/releases/tag/chat-assistants-v81)查看全部附件。

上轮本地预验证包与发布包同名但摘要不同；请使用发布页文件，并按[交接报告](docs/供应商画像交接-v81.md)中的摘要核对。

| 客户端 | 群聊 | 私聊 |
| --- | --- | --- |
| 个微 | [下载个微群聊 1.51.15](https://github.com/JJ704sd/WXData-Analysis-Beta/releases/download/chat-assistants-v81/WeChat-Archive-Skill-1.51.15-Windows-x64.zip) | [下载个微私聊 1.20.13](https://github.com/JJ704sd/WXData-Analysis-Beta/releases/download/chat-assistants-v81/local-wechat-private-analysis-1.20.13-windows-x64.zip) |
| 企微 | [下载企微群聊 1.51.15](https://github.com/JJ704sd/WXData-Analysis-Beta/releases/download/chat-assistants-v81/WeCom-Archive-Skill-1.51.15-Windows-x64.zip) | [下载企微私聊 1.20.13](https://github.com/JJ704sd/WXData-Analysis-Beta/releases/download/chat-assistants-v81/local-wecom-private-analysis-1.20.13-windows-x64.zip) |

完整解压所选 ZIP，双击包内的 `Start.cmd`。四个包均内置运行环境和聊天查看器，无需另装 Python 或 pip 包。登录对应客户端后，在工具中确认账号、数据源、会话和日期，再开始解析；完成解析后可选择会话并点击 **打开聊天窗口**。查看预览后仍需人工复核，确认后才能正式导出。

在支持本机 Windows 程序执行和文件访问的宿主中，也可分别导入所需的单个 Skill ZIP；不要将多个 ZIP 一起作为一个 Skill 导入。详细步骤见 [操作指南](WeBridge/docs/local-chat/操作指南.md)和 [聊天记录查看器](WeBridge/docs/local-chat/聊天记录查看器-v79.md)。

## 从源码启动

使用 Windows x64 和带 Tcl/Tk 的 Python 3.14；本轮验证环境为 Python 3.14.6。在仓库根目录使用 PowerShell：

```powershell
uv venv --python 3.14 .venv
uv pip install --python .venv\Scripts\python.exe -r WeBridge\requirements.txt
.\.venv\Scripts\python.exe WeBridge\scripts\local_chat_bootstrap.py --client wechat --conversation-mode group
```

四类助手共用启动命令，按下表替换参数：

| 助手 | 主要用途 | `--client` | `--conversation-mode` | 版本 |
| --- | --- | --- | --- | --- |
| 个微群聊 | 解析、复核和导出个人微信群聊 | `wechat` | `group` | 1.51.15 |
| 企微群聊 | 解析、复核和导出企业微信群聊 | `wecom` | `group` | 1.51.15 |
| 个微私聊 | 解析微信联系人私聊，准备画像材料 | `wechat` | `direct` | 1.20.13 |
| 企微私聊 | 解析企业微信私聊，准备画像材料 | `wecom` | `direct` | 1.20.13 |

已允许执行本地 PowerShell 脚本时，也可运行 `./start-assistant.ps1 -Client wecom -ConversationMode direct`；它优先使用仓库内 `.venv`。添加 `-Capabilities` 只查询版本，不读取账号或创建配置。

首次使用先登录有权读取的客户端，在窗口中确认账号、会话、日期和媒体范围，再配置匹配的数据源及密钥。已有设置默认保存在 `%LOCALAPPDATA%\LocalWechatArchive` 或 `%LOCALAPPDATA%\LocalWeComArchive`；私聊画像独立保存在 `%LOCALAPPDATA%\LocalChatPrivateProfiles`。切换群聊／私聊会隔离复核进度，更新源码不清理已有设置或导出。

完成解析后核对来源、条数、正文和缺口，再由用户确认正式导出。私聊画像需要再次复核候选；脚本不会自动调用模型或确认画像。转发、引用和附件保留各自出处，缺失原件、部分提取和未知身份会明确记录。v76 对企微已知卡片、媒体文件名及嵌套来源的坏文字字段标记部分解析并保留其他可读信息，不猜测坏 URL、ID 或时间；原始 bytes 和人工复核门槛继续保留。v75、v74、v73 和 v72 的附件、链接及媒体边界修复继续有效。完整步骤见 [操作指南](WeBridge/docs/local-chat/操作指南.md)、[企微使用说明](WeBridge/docs/local-chat/企微使用说明.md)、[v76 修复说明](WeBridge/docs/local-chat/企微已知文字字段完整性修复-v76.md) 和 [私聊 Skill](WeBridge/skills/local-wecom-private-analysis/SKILL.md)。

## 查看聊天记录

四类助手完成解析后，选中会话并点击 **打开聊天窗口**，即可在浏览器中查看该会话的本地解析预览。窗口支持会话内查找、跨已接入会话搜索和结果定位；首次全局搜索需要建立本地索引，耗时与磁盘占用取决于资料量。

查看不会代替人工复核或正式导出。页面展示的消息和附件取决于解析缓存或已接入导出的实际内容；关闭解析工具、成功切换数据源或清除账号缓存后，本次预览服务停止。使用步骤与缓存说明见 [聊天记录查看器](WeBridge/docs/local-chat/聊天记录查看器-v79.md)。

v80 优化了大记录量下的阅读筛选和聊天搜索翻页，并改善索引异常时的提示与资源释放；测试条件和适用范围见 [性能与稳定性说明](WeBridge/docs/local-chat/性能与稳定性-v80.md)。

历史 v80 四类 Windows 包已用合成资料验证解析后打开聊天窗口，记录见 [包内查看器检查](docs/viewer80-compiled-validation.json)；真实账号和其他电脑尚未实测。高缩放时，较窄的群列表可能需要横向滚动才能看到复核列，这一限制在修改前也存在。

也可以独立查看已有的 WeBridge 导出目录或 ZIP。此查看器只使用 Python 3.10 及以上的标准库；在仓库根目录运行，并将示例路径替换为自己的导出：

```powershell
python WeBridge\web_mvp\chat_viewer.py `
  --source "D:\ChatExports\reviewed" `
  --data-dir "$env:LOCALAPPDATA\WeBridgeChatViewer"
```

启动后浏览器会打开本地页面，可继续接入资料；按 `Ctrl+C` 停止。设置与搜索索引保存在 `--data-dir` 指定的目录中，导出资料不会因查看而改写。

## 指定对象与时段分析

四类助手均支持“时段分析…”：先解析并人工复核导出所需联系人／群聊，再选择一个或多个对象和北京时间范围，生成统计、关键词主题、待跟进线索及可搜索的原文依据。可只选日期，也可精确到分钟或秒；结束日期含当天，结束分钟或秒包含在内。时间未知的消息单列，时段外的回复不参与本时段判断。

常用范围提供今天、昨天、近7天、近30天、本周、上周、本月、上月及自定义；自动填入日期后仍可手动修改。近7／30天含今天，本周从周一开始，本周／本月至今天，详见 [快捷选择说明](WeBridge/docs/local-chat/常用时段快捷选择-v78.md)。

报告包含 `report.html`、`report.md`、`report.json`、完整 `evidence.jsonl`、`分析任务.md` 和文件校验清单。脚本生成本地统计和带出处的材料，不自动调用模型；宿主可根据分析任务进一步整理业务信息。桌面步骤、命令行任务格式与范围说明见 [指定对象与时段分析](WeBridge/docs/local-chat/指定对象与时段分析-v77.md)，原时段分析验证见 [v77 验证记录](docs/period77-validation.json)，本轮快捷选择验证见 [v78 验证记录](docs/presets78-validation.json)。

## 生成每日消息日报

直接从 [日报 1.4.0 发布页](https://github.com/JJ704sd/WXData-Analysis-Beta/releases/tag/daily-v1.4.0) 下载对应包。Windows x64 便携包和 Skill 均内置 Python，无需安装 Python、pip 或完整仓库。

| 用途 | 个微 | 企微 |
| --- | --- | --- |
| 直接运行 | `WeChat-Daily-Report-1.4.0-Windows-x64.zip` | `WeCom-Daily-Report-1.4.0-Windows-x64.zip` |
| WorkBuddy 等宿主导入 | `WeChat-Daily-Report-Skill-1.4.0-Windows-x64.zip` | `WeCom-Daily-Report-Skill-1.4.0-Windows-x64.zip` |

便携包解压后双击 `Start.cmd` 或 `*-daily-report-ui.exe` 打开桌面窗口：添加多个已复核目录，用日历或“今天／昨天”选择日期，选择保存位置并生成。生成在后台运行，可取消；完成后可搜索、筛选待跟进线索，打开 HTML 日报或复制结果路径。界面只展示业务概览、姓名和请求，技术追踪留在排查日志中。

1.4.0 增加材料范围提醒与“待补充信息”查阅：当天为零条时提醒核对日期，仍保留回看期内的待跟进；附件、转发和时间待确认的内容缺口可搜索、按日期筛选并跳转原文。当天缺口数与回看总数分别展示，技术标识继续留在日志。历史编码、引用与占用修复见 [对应处理记录](docs/daily-history-fixes.md)。

WorkBuddy 在“添加技能 → 上传技能”中分别导入 Skill ZIP，宿主需能执行本机 Windows 程序并访问所选目录；[官方导入说明](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)。Skill 使用命令行 EXE 自动生成，无需启用 PowerShell 脚本；包内也提供桌面入口。纯网页聊天及远端 Linux 沙箱不能直接使用 Windows EXE。

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

日报按北京时间统计当天消息，默认回看含当天的 7 天寻找请求与后续回应。群内明确发给别人的请求不计入本人线索；“稍后回复”仍待跟进，完成或取消作为待核对信号单列。只统计所选导出，不估算历史未读。完整参数和格式见 [日报脚本说明](docs/daily-reports.md)，1.4.0 的源码、EXE、包和界面检查见 [日报分发验证](docs/daily-delivery-validation.json)。WorkBuddy 元数据已按官方要求适配，客户端内实际导入与真实账号材料尚未验证。

## 开发和构建

```powershell
.\.venv\Scripts\python.exe WeBridge\scripts\sync_chat_release.py --check
.\.venv\Scripts\python.exe WeBridge\scripts\build_chat_viewer.py --check
.\.venv\Scripts\python.exe WeBridge\scripts\check_runtime_identity.py
.\.venv\Scripts\python.exe WeBridge\scripts\check_assistants.py --report .runtime\validation.json
.\.venv\Scripts\python.exe WeBridge\scripts\check_assistants.py test_chat_viewer_integration test_chat_performance80
```

检查使用临时合成数据，包含原生 Tk 界面和 Windows 文件锁场景，需要可用的桌面会话。本次查看器、性能和账号绑定的定向检查见 [v80 源码验证记录](docs/viewer80-source-validation.json)。v78 的快捷范围、日历边界、手动修改和运行包检查以 [v78 验证记录](docs/presets78-validation.json) 及交付清单为准；真实账号、原故障电脑和 WorkBuddy 导入仍需实际验证。v75、v74、v73、v72 与历史记录继续保留在对应验证文件中。

安装构建依赖后，一次生成两个群聊 Skill、两个私聊 Skill 和共用桌面包：

```powershell
uv pip install --python .venv\Scripts\python.exe -r WeBridge\scripts\requirements-offline-build.txt
& .\WeBridge\scripts\build_all_chat.ps1 -Python "$PWD\.venv\Scripts\python.exe"
```

默认输出为 `WeBridge/build/v81/packages/`，四类包的版本、客户端、模式、ZIP 完整性及文件摘要校验通过后生成 `release-matrix.json`。旧 EXE 或不匹配的版本会在打包前被拒绝；已有构建目录不会被覆盖。更新版本时只改 `chat_release.py`，再运行 `sync_chat_release.py` 同步 Skill 元数据。源码文件清单可用 `python WeBridge/scripts/sync_source_manifest.py` 检查；新增文件须在更新时通过 `--add` 明确加入。详见 [维护与构建](docs/maintenance.md)。

## 源码入口

| 路径 | 用途 |
| --- | --- |
| `WeBridge/scripts/local_chat_bootstrap.py`、`local_chat_app.py` | 桌面启动和四类界面 |
| `WeBridge/web_mvp/chat_viewer.py`、`chat_viewer_src/`、`offline_chat_viewer.py` | 聊天查看器生成结果、源码及桌面接入 |
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
