@echo off
cd /d %~dp0
where python >nul 2>nul
if not errorlevel 1 (
    python backend\server.py 8000
    goto done
)
for /d %%P in (".runtime\python\cpython-*") do (
    if exist "%%~P\python.exe" (
        "%%~P\python.exe" backend\server.py 8000
        goto done
    )
)
echo Python 3.10+ is required. Install Python and run this file again.
:done
pause
