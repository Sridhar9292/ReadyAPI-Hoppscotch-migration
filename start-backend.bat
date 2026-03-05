@echo off
echo Installing backend dependencies...
pip install fastapi "uvicorn[standard]" python-multipart python-dotenv

echo.
echo Starting FastAPI backend on http://localhost:8000 ...
cd /d "%~dp0backend"
uvicorn main:app --reload --port 8000
