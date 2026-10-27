# WXData-Analysis-Beta

Windows 本地个微／企微聊天分析助手，提供群聊和私聊四类入口。读取用户选择的数据库副本，解析消息和附件，生成带出处的阅读材料，人工复核后导出 JSON、JSONL 和 Markdown。私聊还支持本地统计、宿主分析材料、画像候选复核和版本管理。

当前源码版本：**群聊 v71／Skill 1.51.5，私聊 1.20.3**。四类工具共用最新解析代码；版本由 [chat_release.py](WeBridge/scripts/chat_release.py) 统一维护。具体基线和修复见 [开发更新说明](docs/development-update.md)。本仓库提供源码，运行包需自行构建。

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
| `WeBridge/web_mvp/offline_desktop.py` | 数据源、解析、复核、恢复与批量导出 |
| `WeBridge/web_mvp/wecom_*.py`、`database_adapter.py` | 双客户端快照及消息读取 |
| `WeBridge/web_mvp/offline_analysis.py`、`offline_documents.py` | 可追溯语料、附件正文及缺口 |
| `WeBridge/web_mvp/private_profiles.py`、`private_insights.py` | 私聊画像版本、证据和本地统计 |
| `WeBridge/skills/`、`WeBridge/scripts/package_four_chat.py` | 宿主协议及统一分发 |
| `poc/wechat_agent_poc/` | 当前个微读取流程依赖的少量共用模块 |

仓库仅包含相关源码、合成测试、说明和验证记录，保留原来的 `WeBridge/` 与 `poc/` 相对路径。来源与组件归属见 [来源说明](THIRD_PARTY_NOTICES.md)。客户端仅支持现有适配的数据布局；真实客户端连通、Windows 11 和宿主导入仍需实际环境验证。
