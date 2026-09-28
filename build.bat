@echo off
setlocal

REM ============================================================
REM  AirCard for Windows - build single-file portable exe
REM  Output: dist\AirCard.exe   (onefile / windowed / no install)
REM
REM  IMPORTANT - keep this file ASCII-only with CRLF line endings:
REM  1) Do NOT add "chcp 65001". Switching code page while a batch
REM     file is running corrupts cmd.exe's file-position tracking,
REM     so multi-byte characters get split into garbage commands.
REM  2) Never put a literal "(" or ")" inside a parenthesized block
REM     (e.g. "echo size (%%~zF bytes)" inside "if exist (...)"),
REM     it closes the block early. Escape as ^( or restructure.
REM ============================================================

set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo ============================================================
echo  AirCard build script
echo ============================================================
echo Python: %PY%
echo Folder: %CD%
echo.

echo [1/5] Cleaning old output...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [2/5] Syntax check...
"%PY%" -m compileall -q aircard AirCard.py
if errorlevel 1 (
    echo.
    echo FAILED: syntax check. Build aborted.
    if not defined AIRCARD_NO_PAUSE pause
    exit /b 1
)

echo [3/5] Import / behaviour regression check...
"%PY%" tests\smoke_imports.py
if errorlevel 1 (
    echo.
    echo FAILED: regression check. Build aborted.
    if not defined AIRCARD_NO_PAUSE pause
    exit /b 1
)

echo [4/5] PyInstaller packaging - about 1-3 min, please wait...
"%PY%" -m PyInstaller --noconfirm --clean AirCard.spec
if errorlevel 1 (
    echo.
    echo FAILED: PyInstaller packaging.
    if not defined AIRCARD_NO_PAUSE pause
    exit /b 1
)

echo [5/5] Done.
if not exist dist\AirCard.exe goto :nofile

echo Output: %CD%\dist\AirCard.exe
for %%F in (dist\AirCard.exe) do echo Size:   %%~zF bytes
echo.
echo Build succeeded. Press any key to close.
if not defined AIRCARD_NO_PAUSE pause
endlocal
exit /b 0

:nofile
echo.
echo ERROR: dist\AirCard.exe was not created.
if not defined AIRCARD_NO_PAUSE pause
endlocal
exit /b 1
