@echo off
echo Starting React frontend on http://localhost:5173 ...
cd /d "%~dp0frontend"
call npm install
npm run dev
