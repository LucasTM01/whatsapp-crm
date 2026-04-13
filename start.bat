@echo off
title WhatsApp CRM
cd /d "%~dp0"
set UV_LINK_MODE=copy
echo Iniciando WAHA...
docker compose up -d
uv run streamlit run app.py
echo Desligando WAHA...
docker compose stop waha
pause
