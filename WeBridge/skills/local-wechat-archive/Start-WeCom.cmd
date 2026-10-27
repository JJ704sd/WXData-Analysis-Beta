@echo off
setlocal
powershell.exe -NoLogo -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0scripts\run.ps1" -Client WeCom -Mode Desktop
