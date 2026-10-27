# 来源与组件归属

本仓库从用户提供的 WeBridge 开发源码和四类交付资料整理，保留 `WeBridge/` 与必要的 `poc/wechat_agent_poc/` 相对路径。导入前的文件摘要与版本包摘要分别见 [源码快照](docs/imported-source.json) 和 [交付基线](docs/source-baseline.json)。原开发仓库为 `JJ704sd/ZJ-AI-WXBot`，当前代码中保留已有作者与组件说明；本次没有新增覆盖全部上游代码的统一许可证。

程序使用 Python、Tcl/Tk、cryptography、zstandard、Pillow、pypdf、xlrd、olefile 和 msoffcrypto-tool，构建使用 PyInstaller。运行依赖和构建依赖分别列在 `WeBridge/requirements.txt` 与 `WeBridge/scripts/requirements-offline-build.txt`。便携包生成时收集当前 Python、Tcl/Tk 及这些依赖的许可文本，保留到包内 `licenses/`；仓库保留原有 `Tcl-8.6.15-license.terms`。

用户提供的 `wecom-chat-extractor-win.zip` 仅作 Windows 数据布局的参考，本仓库没有复制其代码或重新分发该 ZIP，因此不将其许可证作为本项目的统一授权。
