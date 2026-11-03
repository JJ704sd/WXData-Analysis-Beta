@echo off
setlocal
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" scripts\local_chat_bootstrap.py --client wecom --conversation-mode group
