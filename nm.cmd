@echo off
rem nm launcher: run this repo's source with the project venv.
rem Same as: set PYTHONPATH=src & python -m nm
rem Usage: nm.cmd doctor | nm.cmd sync | nm.cmd list | nm.cmd download --only-new | nm.cmd approve | nm.cmd jobs
setlocal
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -m nm %*
) else (
    echo [nm] missing .venv - run: runtime\python311\tools\python.exe -m venv .venv
    exit /b 1
)
