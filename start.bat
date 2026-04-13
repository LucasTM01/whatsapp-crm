@echo off
title WhatsApp CRM
cd /d "%~dp0"
set UV_LINK_MODE=copy

where uv >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo uv nao encontrado. Instalando...
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)

echo Iniciando WAHA...
docker compose up -d
uv run streamlit run app.py
echo Desligando WAHA...
docker compose stop waha
pause
