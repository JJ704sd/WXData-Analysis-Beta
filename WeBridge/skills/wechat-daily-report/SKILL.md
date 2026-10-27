---
name: wechat-daily-report
description: 将个人微信已复核的本地群聊和私聊导出整理成每日概览、待跟进线索及可搜索的 HTML 日报；用户要求微信日报或整理当天消息时使用。
metadata:
  version: "1.2.0"
  display_name: "个微本地消息日报"
  display_name_en: "WeChat Local Daily Report"
  description_zh: "合并个微已复核的群聊与私聊导出，生成每日概览、待跟进线索和 HTML 日报。"
  description_en: "Create a local daily overview, follow-up clues and HTML report from reviewed WeChat exports."
  author: "WXData-Analysis-Beta"
---

# 个微本地消息日报

固定处理个人微信 `wechat`。使用包内 Windows x64 EXE，无需安装 Python、pip 或仓库；仅接受原助手已经人工确认的正式导出目录或批次目录，可合并同一账号的私聊、群聊。未提供导出目录或日期时询问缺失项；“今天”按北京时间转换。不要选择其他账号、伪造复核文件或改写原导出。

先定位当前 Skill 的实际目录，能力查询不读取消息：

```powershell
& "技能目录\bin\wechat-daily-report.exe" --capabilities
```

生成日报，输出使用当前任务允许写入的新目录：

```powershell
& "技能目录\bin\wechat-daily-report.exe" `
  --export-root "已复核群聊目录" --export-root "已复核私聊目录" `
  --date "2026-10-27" --output "任务目录\reports\wechat-2026-10-27" --lookback-days 7
```

将命令中的路径、日期替换为本次实际值；用参数数组调用，不拼接可执行命令文本。调用权限由宿主控制。直接运行 EXE，不要求启用 PowerShell 脚本或修改系统策略；用户要求本机交互时可打开 `bin/wechat-daily-report-ui.exe` 或根目录 `Start.cmd`；自动生成继续用带参数的命令行入口，不弹出窗口。参数及返回值见包内 `references/usage.md`。

能力查询应返回 `client=wechat`、`version=1.2.0`、`standalone=true`，以及 `requiresRepository=false`、`requiresPythonInstallation=false`。读取生成命令的退出码和 JSON；只有退出码 0 且 `ok=true` 才报告完成。返回 HTML 路径、当天消息数、活跃会话数、本人相关待跟进数及影响范围的限制，不自动把全部原文读入对话或上传其他服务。用户要求解释具体线索时再按其范围读取证据。

日报按字面规则找请求和风险；暂缓仍待跟进，回复不等于办结，完成和取消仅作为待核对信号。群内发给别人的请求不算本人待办；对象或身份未知时保留未知。引用、转发、附件内容保持原归属，不据此给当前发送者添加请求。不提供历史未读数，不假定选定导出覆盖账号全部消息。

若文件摘要变化、混入其他账号或客户端、目录已存在或原复核未完成，保留实际失败并修正所选材料或新建输出位置，不绕过校验。程序本身不连接客户端或模型；宿主读取原文时，该部分内容可能进入宿主对话。
