@echo off
rem Остановка игровых сервисов (bot / worker / uvicorn из .venv проекта).
echo Останавливаю сервисы...
taskkill /F /FI "WINDOWTITLE eq mmo-bot" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq mmo-worker" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq mmo-api" >nul 2>&1
taskkill /F /IM python.exe >nul 2>&1
echo Готово.
