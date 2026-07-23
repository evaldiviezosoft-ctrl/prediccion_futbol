@echo off
cd /d %~dp0
if not exist .venv (
  py -3.12 -c "import sys" >nul 2>&1
  if errorlevel 1 (
    py -m venv .venv
  ) else (
    py -3.12 -m venv .venv
  )
)
call .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if not exist .env copy .env.example .env
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
