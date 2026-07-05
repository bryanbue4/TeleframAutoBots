@echo off
REM ===== Find your channel/group ID (Windows) =====
REM Double-click after adding the customer bot to your channel and posting a message there.

cd /d "%~dp0"

python get_id.py

echo.
pause
