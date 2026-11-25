@echo off
rem Запуск игровых сервисов (Windows). Каждый — в отдельном окне, переживают
rem закрытие этого окна. Второй экземпляр бота не поднимется (advisory lock).
cd /d "%~dp0\.."
set PYTHON=.venv\Scripts\python.exe
if not exist "%PYTHON%" set PYTHON=python

start "mmo-bot" "%PYTHON%" -m adapters.vk.bot
timeout /t 2 >nul
start "mmo-worker" "%PYTHON%" -m worker.main
timeout /t 2 >nul
start "mmo-api" "%PYTHON%" -m uvicorn adapters.http.app_factory:create_app_from_settings --factory --port 8000
echo Бот, worker и API запущены. Проверка: http://localhost:8000/healthz
