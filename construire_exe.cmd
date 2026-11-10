@echo off
chcp 65001 >nul
title Construire Editeur.exe
rem Fabrique dist\Editeur\Editeur.exe (PyInstaller, un dossier : demarrage rapide).
rem A relancer apres chaque modification du code pour que l'exe en profite.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed --name Editeur ^
  --add-data "%~dp0ui;ui" --add-data "%~dp0outils;outils" --add-data "%~dp0moteur;moteur" ^
  --collect-submodules uvicorn --collect-all webview ^
  --distpath dist --workpath build --specpath build ^
  app_desktop.py
if errorlevel 1 (
  echo.
  echo La construction a echoue.
  pause
  exit /b 1
)
echo.
echo Pret : %~dp0dist\Editeur\Editeur.exe
