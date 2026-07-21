@echo off
REM Démarre LudoteX avec une console visible (utile pour le débogage : messages
REM de lancer.py, erreurs d'uvicorn/cloudflared, etc.).
REM
REM Utiliser lancer.vbs à la place pour un démarrage silencieux, sans console.

cd /d "%~dp0"
REM Les arguments éventuels sont transmis (ex. lancer.bat --formation).
.venv\Scripts\python.exe lancer.py %*
pause
