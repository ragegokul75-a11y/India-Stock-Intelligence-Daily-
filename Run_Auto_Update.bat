@echo off
title India Stock Intelligence Daily - Full Automatic Market Update
cd /d "D:\chatgpt\India Stock Intelligence Daily"
set PYTHONPATH=D:\chatgpt\India Stock Intelligence Daily\src
echo Running 100%% Automatic NSE Market Fetch + Bull/Bear/Judge Debate...
".venv\Scripts\python.exe" -m india_stock_intelligence.cli auto-run --days 3 --top-debate 15
echo.
echo Done! All reports and dashboard briefings are updated.
pause
