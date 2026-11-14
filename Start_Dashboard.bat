@echo off
title India Stock Intelligence Daily - Automatic Dashboard
cd /d "D:\chatgpt\India Stock Intelligence Daily"
set PYTHONPATH=D:\chatgpt\India Stock Intelligence Daily\src
echo Starting India Stock Intelligence Daily Dashboard...
echo Your browser will open automatically in a moment.
".venv\Scripts\streamlit.exe" run app.py
pause
