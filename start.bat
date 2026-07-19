@echo off
echo Starting AdventureWorks Chat Analytics...
echo.
echo Make sure Ollama is running: ollama serve
echo.
cd /d "%~dp0"
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
