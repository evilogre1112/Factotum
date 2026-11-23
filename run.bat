@echo off
chcp 65001 >nul
title Factotum - Khoi Phuc Dau, Viet Tat ^& Sua Chinh Ta
cd /d "%~dp0"
python "%~dp0src\app.py"
pause
