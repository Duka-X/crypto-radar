Dim shell
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = "C:\Users\admin\Documents\Codex\2026-07-14\wo\crypto-radar"
shell.Run "python -m uvicorn main:app --host 0.0.0.0 --port 8000", 0, False
Set shell = Nothing
