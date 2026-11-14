@echo off
rem Запуск: run.bat [-c config.ini] [--load-order] [--no-image]
rem Тесты:  run.bat test
cd /d "%~dp0"
set PYTHONUTF8=1
if "%1"=="test" (
    python -m unittest discover -s tests -v
) else (
    python src\main.py %*
)
