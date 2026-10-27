# WeBridge 项目指令

本文件仅补充 WeBridge 的账号绑定与源码验证规则。全局开发习惯沿用上级指令。

账号、群 ID 和账号目录必须从当前已选择的数据源或已确认请求读取；不得用真实账号、测试账号或脱敏占位账号作为运行代码的固定准入条件。客户端版本、模块摘要和 RVA 仍遵循原有固定适配契约。

测试和演示数据按 `scripts/check_runtime_identity.py` 定义的目录边界隔离。公开发布时，敏感运行值应移入未提交的本地配置；不得通过把可执行校验条件替换成假账号来脱敏。示例和历史证据的脱敏不能改变运行逻辑。

修改账号绑定、数据源、启动流程或对源码做脱敏后，执行：

```powershell
python scripts/check_runtime_identity.py
python -m unittest web_mvp.test_runtime_identity web_mvp.test_windows_hook_smoke.SourceBindingTests
```

完整 `test_*.py` 回归已包含上述源码检查。检查报告只显示文件、行号和问题类型，不输出命中的账号值。检查与合成测试不能代替当前账号的连接实测或收件端送达证据。
