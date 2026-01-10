@echo off
echo ========================================
echo   NIFTY Strangle Trading Web UI
echo ========================================
echo.
echo Killing any existing process on port 8080...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8080 ^| findstr LISTENING') do (
    taskkill /PID %%a /F >nul 2>&1
)
echo.
echo Starting server...
call venv\Scripts\activate.bat
python run.py --ui
