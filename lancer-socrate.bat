@echo off
title Portail Socrate
cd /d "C:\Users\francois-regis.auer\Documents\portail-socrate"
set PYTHONIOENCODING=utf-8

echo  Arret des anciens processus Python...
taskkill /F /IM python3.13.exe >nul 2>&1
taskkill /F /IM python.exe >nul 2>&1
timeout /t 2 /nobreak >nul

echo.
echo  =====================================
echo   Portail Socrate - Lancement
echo  =====================================
echo.
echo  Adresse : http://127.0.0.1:8000
echo  Ctrl+C pour arreter
echo.
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
pause
