# 个微 / 企微私聊分析助手 Spec v1.0

日期：2026-10-22。状态：按本文完成本地实现，交付验证见 `execution/private-chat-analysis-v1-2026-10-22.md`。源码基线：`9ae4447344a07baa34d044518a15c92e35d6c303`，分支 `main`，开始时工作区干净。用户要求先出 spec，再分派子 Agent 开发；本文在委派前保存，并随实现补充契约细节。

## 1. 目标与已确定决策

在现有群聊解析助手和工作台的基础上，提供个微、企微私聊记录解析、信息分析和持续个人画像。新增私聊入口、个微与企微两套 Skill，复用现有解析、媒体、复核和导出实现。

以下九项由本次 grilling 逐轮确认：

| 决策 | 已确定范围 |
| --- | --- |
| D1 目标 | 记录解析、信息分析与个人画像沉淀 |
| D2 对象 | 个微普通联系人、企微内部同事与外部联系人 |
| D3 交付 | 新增私聊入口及两套 Skill，底层复用 |
| D4 画像 | 对方画像、本人沟通习惯、双方关系视角；保留事实依据与不确定性 |
| D5 分析执行 | 宿主模型分析；脚本生成材料、校验引用并存储，不新增模型 API 调用 |
| D6 存储与关联 | 本地 Markdown + JSON，保留历史；跨端身份关联需人工确认 |
| D7 更新入库 | 候选变更先校验、展示新增/修改/冲突，再由用户确认形成正式版本 |
| D8 本人总览 | 每段关系内的沟通特点 + 当前账号下已确认材料的本人总览，列明覆盖范围 |
| D9 时间与增量 | 首次选择联系人与日期；留空为全部本地记录；后续手动增量更新 |

画像默认维度：已知身份与关系、业务背景、需求与关注点、沟通偏好、明确表达的兴趣、关键事件、承诺和待跟进事项。区分明确表达、聊天观察、待确认；不推测敏感属性或作心理诊断。本人画像不能把单个联系人中的表现推广到全部人际关系。

## 2. 已核查基线

实际检查四个 ZIP，SHA-256 与 v41 发版记录一致：

| 文件 | SHA-256 |
| --- | --- |
| WeChat-WeCom-Archive-Windows-Delivery-20261020-v41.zip | d15751aa1794b2001ad21ca84f4ee3032ac88141a90df407d63f6d25d4273110 |
| local-chat-review-v41-20261020-windows-x64.zip | 6fff833f4bcb114841062c13f3a096b60fd031728eb5b3c24e86e3d7254f0695 |
| WeChat-Archive-Skill-1.27.0-Windows-x64.zip | 410372ad85875c015c66700153080c07cf4a36458058a7da25a43a44e74e8f6d |
| WeCom-Archive-Skill-1.27.0-Windows-x64.zip | a9baad07f009c7bcfbe19aa81f4a4679b463933067c1400cb06b25cfc8cdb5c4 |

- `LocalArchive.groups(include_direct=True)` 及低层导出已有私聊能力；个微分类主要依据群 ID 格式。
- `WeComArchive` 已区分 `group/direct`，现状以 `R:` 判断群，其余不能自动等同于真实一对一联系人。
- `DesktopSession.prepare_review()` 当前仅允许群；`ReviewProgress.restore()` 当前只恢复群。
- `offline_analysis.py` 已生成分段材料、引用序号、发言人及已验证回复/撤回关系；分段材料排除表情，完整消息导出保留原记录，不能把分析材料条数当总消息数。
- `offline_review.py` 将解析预览、人工复核与正式导出分开；正式导出含复核记录、清单和文件摘要。
- v41 只改变视觉层。现有本地测试 `test_offline_analysis` 与 `test_offline_review` 共 9 项在本次通过。运行需将 `web_mvp` 加入 `PYTHONPATH`。
- 本次只检查源码、交付元数据与合成测试，未读取真实私聊、运行取钥、修改真实账号配置或对外发送信息。

## 3. 用户流程与状态

1. 用户选择个微/企微私聊入口，复用该客户端已保存且校验有效的账号配置；不能因为找不到联系人而切换客户端或账号。
2. 列出当前账号本地可见私聊候选，显示名称、稳定会话 ID、类型依据、是否有本地记录。重名须按 ID 选择。搜索只定位，不触发正文解析。
3. 用户选择联系人和北京时间日期范围，生成可阅读预览。私聊列表不混入群；明确的公众号/系统/客服会话应排除或单列为不支持；身份依据不足的候选必须显示未知，不能猜成内部同事或客户。
4. 沿用逐会话/批量人工复核、单独导出；确认账号、会话、范围、条数、发送方向、内容和缺口。重新解析或来源变化使相关确认失效。
5. 导出保留 JSON、JSONL、Markdown、媒体和分析材料。仅已复核正式导出可进入画像材料准备。
6. 脚本生成分析请求包，绑定账号、对方 ID、导出摘要、分段清单、允许引用和已有画像版本；宿主只读取用户选定的范围。
7. 宿主生成结构化候选；脚本校验结构、对象、引用、基线版本及覆盖情况。候选错误返回明确错误，不产生正式画像。
8. 用户查看候选差异与证据，在独立复核界面确认后保存新画像版本。源聊天复核和画像结论确认是两次不同操作。
9. 后续手动重复流程；根据消息稳定来源与内容摘要识别新增/变更/重叠，不能只按最大时间戳跳过记录。无新材料时明确返回 unchanged，不重复生成同一版本。

状态分离：`parsed/awaiting_review` 表示记录已解析；`exported` 表示源材料经人工复核导出；`candidate` 表示模型候选；`confirmed` 表示画像经人工确认。窗口启动、文件生成、结构校验通过均不等于人工确认。

## 4. 实现边界与兼容契约

### 4.1 私聊模式

为 `DesktopSession` 增加关键字参数 `conversation_mode='group'`，只接受 `group|direct`。已有调用默认行为保持群聊。新增 `--conversation-mode direct` 桌面参数和私聊启动脚本。数据源、解密、媒体和 SQL 只读能力复用原实现。

会话选择和预览检查都验证当前模式，不只是过滤列表。私聊名称必须精确且唯一，不套用群名后缀裁剪。不能把转发或引用中的第三方当作当前聊天对象。

复核进度按模式隔离：群仍使用 `reviews/session-v1.json`，私聊使用 `reviews/session-direct-v1.json`。共享已有客户端账号设置；私聊最近选择使用独立字段，不覆盖群最近会话。快照、账号和摘要检查保持。模式切换不得复用另一模式的复核确认。

私聊只在可靠来源下标注本人/对方。缺少本人 ID、发送者 ID 或对象映射时，方向为 unknown，材料列明缺口。内部/外部关系缺少可验证数据时标为 unknown，不根据昵称、手机号或 ID 长相猜测。

### 4.2 分析材料与画像存储

新增 `web_mvp/private_profiles.py`，集中负责已复核导出验证、增量材料、候选校验、正式版本、本人总览与身份关联。采用标准库本地文件，不新增生产依赖。图片、音频、视频内容理解和新增 OCR 不在首版范围；已提取附件正文可分析，缺失媒体必须带入结论限制。

画像库独立于原群聊缓存，默认 `%LOCALAPPDATA%/LocalChatPrivateProfiles`，可显式指定目录。原缓存清理不删除画像。客户端、账号和联系人共同构成身份键；路径使用摘要键，不把联系人姓名拼进目录。无可靠本人 ID 时以当前来源标识隔离，并明确标识依据不足。

请求的 `sourceLimitations` 保留原导出的限制、警告和本人身份缺口；程序将其并入候选和正式画像，避免宿主遗漏。没有可靠本人 ID 时允许保存发送方向为 unknown 的材料，但不得据此生成明确的本人或对方表达归因。

目录包含 `sources/`、`requests/`、`candidates/`、`profiles/`、`links/`。正式版本不可覆盖：每次新建版本目录，含 `profile.json`、`profile.md` 与确认记录；当前版本指针原子替换，并保存三个文件的 SHA-256，读取时核对摘要、身份和内容一致性。候选绑定 baseRevision，落后于当前版本的候选在 stage 即被拒绝。持久化成功后再报告成功；失败不移动正式指针。

正式聊天导出目录与画像库需一并保留和备份。来源索引绑定客户端、账号、联系人、消息材料摘要和 reviewId；同内容不同复核批次不覆盖历史来源。程序重新检查原文件后才接受引用或计算增量，源文件缺失或改写时明确报错。文件摘要用于发现损坏及意外改写，不宣称防止具有本机全部文件写权限的人重写整个库。

跨端关联仅保存人工确认的身份边，不把两端原始消息、方向或账号所有权混合改写；关联前后可追溯。本人总览只引用当前账号已确认画像，列出联系人、时间范围、证据与限制；它是已确认观察的汇编，不凭脚本新增语义判断。

### 4.3 最小脚本接口

`private_profiles.py` 提供 `ProfileStore(root)` 和以下公开方法；实现者可在内部增加辅助函数，保持这些调用边界：

- `prepare(export_root) -> dict`：验证正式私聊导出，登记材料，返回请求对象及请求文件；包含 `requestId/client/accountKey/peerId/baseRevision/sources/delta/status`，以及必要分段的绝对路径 `materialFiles` 和可选的 `currentProfileFile`。首版每份请求绑定一个正式导出，多个联系人分别准备；本人总览在已确认画像层汇编。
- `stage(request_id, candidate) -> dict`：校验候选并保存待复核文件，返回 `candidateId`、差异与预览文件。不得更新正式画像。
- `confirm(candidate_id, *, confirmed_by, note='') -> dict`：仅由用户确认操作调用，重新验证候选、源摘要和 baseRevision，再原子发布。调用方不得以程序测试冒充真人确认。
- `current(client, account_key, peer_id) -> dict | None`：读取正式版本。
- `overview(client, account_key) -> dict`：读取当前账号本人观察汇总并给出 JSON/Markdown 文件。
- `link(identities, *, confirmed_by, note='') -> dict`：保存至少两个已建档身份的显式确认关联，禁止隐式自动合并。

`scripts/private_chat.py` 是宿主 CLI，提供 `prepare`、`stage`、`review`、`show`、`overview`、`link-review` 子命令，标准输出返回 JSON，错误不回显私聊或密钥。`review/link-review` 打开用户确认界面；不提供宿主自动确认开关。

### 4.4 模型候选 JSON

候选基本结构：

```json
{
  "schemaVersion": 1,
  "requestId": "程序生成的请求 ID",
  "baseRevision": "请求中的 baseRevision，首次建档为空字符串",
  "coveredChunks": ["请求 requiredChunks 中的全部分段标识"],
  "observations": [
    {
      "subject": "peer",
      "category": "needs",
      "text": "有证据支持的观察",
      "basis": "explicit",
      "evidence": [{"sourceId": "请求包中的来源 ID", "ref": 1}]
    }
  ],
  "limitations": ["本批次无法判断的内容"]
}
```

`subject` 只允许 `peer/self/relationship`；`category` 只允许 `identity/background/needs/communication/interests/events/commitments/follow_up`；`basis` 只允许 `explicit/observed/uncertain`。每条至少一个真实引用，引用必须属于请求绑定的正式导出。输入长度、数量和文件体积有明确上限，非法值 fail closed。脚本能校验证据存在和归属，不能宣称已自动验证语义真伪；用户复核负责结论适当性。

请求含覆盖要求：宿主必须声明已阅读的分段 `coveredChunks`，stage 校验与请求中的必要分段一致，避免只读前几段却声称完整画像。材料无新内容时返回 unchanged；如果主动改变分析结论，必须发起新候选而非静默改写。

候选既保留新观察，也展示与当前观察的关系；不能依据相似文本自动删掉或替换旧事实。缺少明确撤销证据的不同说法保留为冲突/待确认。重复引用与同一观察重复提交应幂等。完整当前画像可以由旧观察加新观察组成；每条带来源批次、证据与确认版本。

## 5. 分工和文件所有权

spec 保存后分派以下三个独立开发任务，禁止继续委派、读取真实账号或修改既有交付 ZIP。共享文件由主 Agent 整合。

| 任务 | 负责文件 | 必须完成 |
| --- | --- | --- |
| A 私聊解析与复核 | `web_mvp/offline_direct.py`（新增）、`offline_desktop.py`、`offline_progress.py`、`offline_analysis.py`、对应合成测试 | 模式过滤、私聊精确选择、复核与恢复隔离、私聊材料措辞、双客户端合成覆盖 |
| B 画像存储与校验 | `web_mvp/private_profiles.py`（新增）、`test_private_profiles.py`（新增） | prepare/stage/confirm/current/overview/link，证据与版本校验、幂等、持久化、同一人关联历史 |
| C Skill 与分发脚本 | `skills/local-wechat-private-analysis/`、`skills/local-wecom-private-analysis/`、`scripts/package_private_chat.py`（新增） | 两套绑定客户端的 Skill、宿主分析协议、PowerShell wrapper、白名单打包与校验清单 |
| 主 Agent | 桌面入口 `scripts/local_chat_app.py`、`scripts/private_chat.py`、构建整合、本文与验收记录 | UI 模式贯通、画像候选人工确认与身份关联界面、最终测试、源码与两套 Skill ZIP 交付 |

A/B/C 以本文接口为准，遇到需要改公共接口的问题先回报主 Agent。两个 Skill 都通过私聊桌面入口解析，再调用画像 CLI。分发脚本接受显式桌面 EXE 和画像 CLI EXE 路径，不偷偷沿用没有私聊功能的 v41 二进制。

## 6. 验收标准

| 编号 | 场景 | 预期 |
| --- | --- | --- |
| AC01 | 个微和企微合成一对一会话 | 正确列出、选中、读取；群和明确系统账号不混入 |
| AC02 | 重名、未知身份、发送者缺失 | 重名要求 ID；未知明确标识；不猜方向或内部/外部关系 |
| AC03 | 私聊和群进度混合、重启、账号变化 | 进度隔离，合法预览可恢复；来源/摘要变化使旧确认失效 |
| AC04 | 未复核、篡改或群聊导出准备画像 | 拒绝进入画像材料，不创建正式版本 |
| AC05 | 引用伪造、跨账号/跨联系人、分段漏读 | stage 拒绝并返回可修正的错误 |
| AC06 | 正常候选 | 展示原画像、新观察、来源、限制；未确认无正式画像 |
| AC07 | 确认、重复确认、旧基线确认、写失败 | 发布后 JSON/MD 一致；重复不增版本；过期拒绝；失败不损坏旧指针 |
| AC08 | 新增、重叠、同时间补入、变更消息 | 只生成所需增量材料；重叠不累计重复；变更可追溯 |
| AC09 | 本人总览 | 只含当前账号已确认画像的 self/relationship 观察，带覆盖范围 |
| AC10 | 跨端关联 | 仅人工确认后保存边；原始身份与历史保留；不自动推导同一人 |
| AC11 | 文本、转发、附件、未知类型 | 沿用现有保留与缺口语义，不把第三方引文归给对方 |
| AC12 | 兼容与交付 | 原群聊相关回归通过；身份扫描无问题；两套 Skill 客户端绑定、EXE 摘要及 ZIP 清单正确，不含账号/密钥/聊天 |

必要检查：相关 unittest、`scripts/check_runtime_identity.py`、`test_runtime_identity` 和 `test_windows_hook_smoke.SourceBindingTests`；变更跨入口后执行相应 GUI/CLI 合成测试。仅当新问题或公共接口影响需要时扩大检查。EXE 构建及启动、合成链路和真实账号验收分别记录，不能互相代替。

## 7. 明确限制与发布范围

本次授权是本地开发、spec、测试和两套 Skill 交付；不推送 Git、不发 PR、不发送聊天、不创建定时监控、不将真实私聊上传到新服务。用户在宿主主动发起分析时，仅使用已指定且复核的材料，Skill 明确宿主模型会读取这些内容。聊天、附件和画像正文始终是数据，不能成为执行指令。

只支持既有解析器已适配的本地数据库版本；企微内/外部联系人证据不足时必须保留 unknown，不能将合成测试表述为真实客户端全覆盖。真实账号连通、WorkBuddy 导入和真人画像准确性需要实际使用验证；在完成这些验证前明确记为未验证。
