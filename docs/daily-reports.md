# 个微与企微本地消息日报

`wechat_daily_report.py` 和 `wecom_daily_report.py` 将四类助手的正式导出整理成每日概览、待跟进线索及本地 HTML 日报。两个入口固定各自客户端，共用整理内核，当前版本 1.1.0。

## 输入和启动

直接使用 [发布页](https://github.com/JJ704sd/WXData-Analysis-Beta/releases/tag/daily-v1.1.0) 的 Windows x64 便携包或 Skill，不需要安装 Python、pip 或完整仓库。解压便携包后双击 `Start.cmd`，也可直接执行包内 EXE。单文件源码位于仓库 `standalone/`，需要 Python 3.12+，仅使用标准库；已验证 Python 3.14.6。开发入口仍保留在 `WeBridge/scripts/`。

先在原助手核对账号、会话、日期、正文和缺口，并确认正式导出；日报接受含 `manifest.json`、`review.json`、`messages.jsonl` 的正式目录，也接受原助手生成的 `batch.json` 批次。普通 JSONL、预览目录及原始数据库不能直接作为日报输入。

```powershell
& .\wechat-daily-report.exe `
  --export-root "D:\ChatExports\wechat-reviewed-batch" `
  --date 2026-10-27 --lookback-days 7 `
  --output "D:\DailyReports\wechat-2026-10-27"
```

企微使用对应包的 `wecom-daily-report.exe`，并选企微导出。单文件源码改用 `python wechat_daily_report.py` 或 `python wecom_daily_report.py`，其余参数相同。可重复传入 `--export-root`，合并同一账号的群聊和私聊。一次运行拒绝混入其他账号或客户端；不按目录修改时间自动选择账号。

| 参数 | 含义 |
| --- | --- |
| `--export-root PATH` | 正式导出或批次目录；可重复 |
| `--date YYYY-MM-DD` | 必填，日报日期，使用北京时间 |
| `--output PATH` | 必填，新的输出目录，不能位于输入或原始数据目录内 |
| `--lookback-days N` | 1～90 天，含日报当天，默认 7 |
| `--my-name NAME` | 用户提供的群内本人称呼；可重复，只作为点名线索 |
| `--capabilities` | 只输出能力、客户端与版本，不读取导出或建立配置 |
| `--interactive` | 本机控制台逐项输入目录及日期；双击 `Start.cmd` 使用此模式 |
| `--version` | 只输出版本 |

成功退出码为 0，标准输出为包含路径和计数的 JSON；材料校验或读写失败返回 2。脚本拒绝覆盖已有日报。需要重新生成时选择新的目录。

## 每日概览和线索规则

每日概览只统计北京时间指定日期 00:00 至次日 00:00 之前的消息，包括条数、活跃会话、本人发出、消息类型、24 小时分布和内容缺口。回看窗口用于寻找此前请求及后续回应，不会把前几天的消息计入当天条数。无时区的源时间按北京时间解释；时间缺失或无效的消息单独记录，保留证据，不参与当天计数和请求匹配。历史时点未读状态不可从这些导出恢复，报告不提供估算未读数。

待跟进使用可审查的字面规则，覆盖“请确认”“请尽快处理”“麻烦”“能否”及问句等表达；主题与风险也使用源码中明确列出的词表。它们是候选线索，不能覆盖所有自然语言请求。主题按词表归类，不推断跨会话具有同一个业务事件。

私聊对方请求可归为本人相关；群聊优先检查结构化 `@`，明确提到其他人的请求不作为本人待办。缺少结构化目标时，仅用户提供的独立称呼可形成点名线索，其余保留“对象待确认”。本人身份未知时不推断归属，本人发出数显示“未确认”。

| 状态 | 报告如何处理 |
| --- | --- |
| 尚未找到对应回复 | 本人相关待跟进保留 |
| 本人暂缓处理 | “稍后回复”等保留待跟进 |
| 已有后续回复 | 保留线索，结果仍需核对 |
| 本人表示已处理 | 作为完成信号单列，仍可查看原请求及回应 |
| 对方表示取消或暂缓 | 作为取消信号单列，仍需核对 |
| 对象或本人身份待确认 | 单列，不加入本人相关待跟进计数 |

每个独立请求各保留一条。关联回应优先使用明确引用的消息 ID；只有私聊在当前回看窗口内仅存在一条先前请求、且回应没有指向其他消息的引用时，才使用时间先后作线索。同一会话存在多条请求时，不用一句后续回复清除全部请求。完成或取消信号仍不是业务验收结论，所有线索均标注需要人工复核。

请求、主题和风险识别只读取根文本及引用消息自身正文；被引用原文、转发记录和附件作者的内容保持原归属。原文、嵌套结构、解析缺口和来源在证据中保留，HTML 卡片的短预览不会截断证据文件。

## 校验与输出

读取前检查原助手的确认状态、显式复核清单、客户端、会话、账号来源以及文件字节数和 SHA-256。读取消息时再次核对条数和摘要，并检查元数据未在读取期间变化。重叠导出按账号、会话与稳定消息 ID 去重，并保留所有来源行号；同一 ID 内容冲突直接报错，不选择其中一个版本。

批次失败、在其他目录已导出或清单未完成等情况会出现在范围说明中。报告只覆盖本次明确提供的导出，不能据此推断账号内全部会话或未保存消息。账号归并使用已复核的本人 ID，缺少本人 ID 时使用来源指纹；无法确认相同账号的材料不会强行合并。

| 输出 | 内容 |
| --- | --- |
| `report.html` | 独立 HTML，业务概览、待跟进、处理进展、信息待补充及可展开原文 |
| `report.json` | 版本、账号摘要标识、日期、回看范围、概览、线索、主题风险及来源 |
| `evidence.jsonl` | 回看窗口内消息及时间未知消息的正文、嵌套内容、来源行号、复核 ID 和缺口 |
| `diagnostics.jsonl` | 消息与来源 ID、复核编号、来源目录、摘要、内部状态、请求与回应关联依据 |
| `manifest.json` | 日报客户端、日期、版本及另外四个文件的字节数、SHA-256 |

输出 JSON 的 `schemaVersion` 为 1，诊断日志为兼容性新增文件。主界面展示会话、发送人、时间、正文和业务影响，不展示消息 ID、来源指纹、复核编号、目录路径或原始 JSON 结构；排查入口默认折叠。名称缺失时显示“待确认”，不用 ID 代替业务名称。引用、转发及附件中已有的正文按原归属展示，完整结构与追踪信息留在数据文件及日志。

HTML 仅展示所引用的证据，完整回看证据在 JSONL。HTML 无外部资源，搜索、筛选和展开依据均在浏览器本地执行；消息内容进行 HTML 转义。输出以临时目录生成后整体发布，沿用已有 Windows 文件占用有限重试逻辑。数据文件和日志包含原文、技术标识及来源路径，分享时应选择实际需要的文件。

## Skill 导入

两个 Skill 分别为 `wechat-daily-report`、`wecom-daily-report`，ZIP 根目录包含 `SKILL.md`，执行程序在 `bin/`，标准库单文件脚本在 `scripts/`。在 WorkBuddy 点击“添加技能 → 上传技能”，分别导入对应 Skill ZIP；导入整套便携包或只粘贴 `SKILL.md` 无法替代完整 Skill。元数据按 [WorkBuddy 官方结构](https://open.workbuddy.cn/docs/skill) 提供中文、英文名称及介绍、版本和作者；[官方导入步骤](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)。

Skill 直接调用 EXE，不要求启用 PowerShell 脚本或修改系统策略。宿主需支持访问用户指定的本地目录、运行 Windows x64 程序及展示生成文件。普通网页对话、移动端或远端 Linux 沙箱不具备同等本机执行条件，不能仅因能读取 `SKILL.md` 就认定已可运行。其他支持标准 Skill 目录的宿主可由打包器选 `--skill-format standard` 生成嵌套元数据版本；不同宿主的实际导入状态分别验证。

脚本本身不联网。Skill 默认向宿主返回生成路径和概览计数；如要求宿主解释具体原文，读取的部分可能进入宿主对话。当前未在 WorkBuddy 或其他国内宿主的客户端中实际导入。

## 实现和验证

两个开发入口调用 `scripts/daily_report.py`，由 `web_mvp/offline_daily.py` 校验与整理，再由 `offline_daily_html.py` 渲染。`build_daily_standalone.py` 从这些维护模块及已有纯函数生成可独立复制的标准库脚本；分发版不导入桌面、解密、文档读取模块。生成内容保留来源摘要，不单独手改两个副本。

EXE 由 PyInstaller 6.22.3 构建，内含 Python。不连接运行中的客户端，不扫描密钥，不调用模型接口；不修改已有四类助手的数据格式、画像或发布版本。

构建机使用仓库 Python 3.14 环境并安装 `pyinstaller==6.22.3`，从仓库根目录执行：

```powershell
& .\WeBridge\scripts\build_daily_reports.ps1 -Python "$PWD\.venv\Scripts\python.exe"
```

输出为 `WeBridge/build/daily-1.1.0/` 下的 `standalone/`、`bin/`、`packages/`。构建目录必须为新目录；也可使用 `-OutputDirectory` 指定 `WeBridge/build/` 下的新位置。四个 ZIP 分别是两个便携包和两个 Skill，均包含许可文本、文件清单及摘要；`release-matrix.json` 记录包摘要和二进制实际能力查询结果。

从仓库根目录执行定向检查：

```powershell
.\.venv\Scripts\python.exe WeBridge\scripts\check_assistants.py `
  --report .runtime\daily-checks.json `
  test_daily_reports test_daily_distribution.StandaloneDistributionTests `
  test_runtime_identity test_windows_hook_smoke.SourceBindingTests
.\.venv\Scripts\python.exe WeBridge\scripts\check_runtime_identity.py
```

已构建 ZIP 的隔离验证另运行：

```powershell
$env:DAILY_DELIVERY_ROOT = "$PWD\WeBridge\build\daily-1.1.0\packages"
.\.venv\Scripts\python.exe WeBridge\scripts\check_assistants.py `
  --report .runtime\daily-native-checks.json test_daily_distribution.WindowsDeliveryTests
```

1.1.0 当前检查见 [分发验证记录](daily-delivery-validation.json)，1.0.0 源码基线记录保留在 [原验证文件](daily-reports-validation.json)。检查覆盖四类助手实际导出流程生成的合成材料、客户端绑定、隔离单文件运行、跨日与多请求、归属边界、业务界面与诊断日志、原生 EXE、中文及带空格路径、无 Python PATH、四个 ZIP 的 CRC/摘要及许可文本。

这些结果覆盖合成数据及本机原生 EXE 运行；尚未验证真实账号材料、原故障电脑、Windows 11 或 WorkBuddy 客户端内导入。日报独立构建，不改变原四类采集 Skill 的发布矩阵。
