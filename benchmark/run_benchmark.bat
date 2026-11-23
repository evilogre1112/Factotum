@echo off
chcp 65001 >nul
title Benchmark Do Chinh Xac & Toc Do - NLP Graph
cd /d "%~dp0"
python "%~dp0run_benchmark.py"
pause
