@echo off
cd /d "C:\Users\admin\Documents\Codex\2026-07-14\wo\crypto-radar"
echo Starting CryptoRadar...
echo Open http://localhost:8000 in your browser
python -m uvicorn main:app --host 0.0.0.0 --port 8000
echo.
echo Server stopped.
pause
