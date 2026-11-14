@echo off
title India Stock Intelligence Daily - Instant Mobile Phone Link
cd /d "D:\chatgpt\India Stock Intelligence Daily"
set PYTHONPATH=D:\chatgpt\India Stock Intelligence Daily\src

echo ============================================================================
echo   INDIA STOCK INTELLIGENCE DAILY - INSTANT MOBILE PHONE LINK
echo ============================================================================
echo Starting dashboard server on port 8501...
start /b "" ".venv\Scripts\streamlit.exe" run app.py --server.headless true --server.port 8501 --server.address 0.0.0.0

timeout /t 3 /nobreak >nul
echo.
echo Creating your secure public mobile link (works on 4G/5G anywhere)...
echo Copy or scan the https://....lhr.life link shown below on your mobile phone!
echo ============================================================================
ssh -o StrictHostKeyChecking=no -R 80:localhost:8501 nokey@localhost.run
pause
