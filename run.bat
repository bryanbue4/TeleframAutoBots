@echo off
REM ===== Start TeleframAutoBots (Windows) =====
REM Double-click this file to run the bot.

cd /d "%~dp0"

if not exist "venv\" (
    echo [ERROR] No virtual environment found.
    echo Run setup.bat first.
    echo.
    pause
    exit /b 1
)

if not exist ".env" (
    echo [ERROR] No .env file found.
    echo Run setup.bat first and fill in your values.
    echo.
    pause
    exit /b 1
)

echo Starting TeleframAutoBots...
echo (Keep this window open - closing it stops the bot.)
echo On first run you will be asked for your phone number and a Telegram login code.
echo.

call venv\Scripts\activate.bat
python -m app.main

echo.
echo Bot stopped. Press any key to close.
pause >nul
