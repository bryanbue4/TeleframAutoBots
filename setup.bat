@echo off
REM ===== TeleframAutoBots one-time setup (Windows) =====
REM Double-click this file to set everything up.

cd /d "%~dp0"
echo.
echo ============================================
echo   TeleframAutoBots - Setup
echo ============================================
echo.

REM --- Check Python is installed ---
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not on PATH.
    echo Install it from https://python.org/downloads
    echo IMPORTANT: tick "Add Python to PATH" during install, then run this again.
    echo.
    pause
    exit /b 1
)
echo [OK] Python found:
python --version
echo.

REM --- Create virtual environment (only if missing) ---
if not exist "venv\" (
    echo Creating virtual environment...
    python -m venv venv
) else (
    echo [OK] Virtual environment already exists.
)
echo.

REM --- Install dependencies ---
echo Installing dependencies (this can take a minute)...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Dependency install failed. Scroll up to see why.
    pause
    exit /b 1
)
echo.

REM --- Create .env from the template if it does not exist yet ---
if not exist ".env" (
    copy .env.example .env >nul
    echo [OK] Created .env from template.
    echo Opening .env in Notepad - fill in your values, then SAVE and close.
    echo.
    notepad .env
) else (
    echo [OK] .env already exists - leaving it as is.
)

echo.
echo ============================================
echo   Setup complete!
echo   Next: double-click run.bat to start the bot.
echo ============================================
echo.
pause
