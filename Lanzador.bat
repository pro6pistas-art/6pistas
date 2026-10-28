@echo off
cd /d C:\separa
echo Iniciando 6pistas.com...
start http://6pistas.com:8000/app
python -m uvicorn main:app --host 0.0.0.0 --port 8000
pause
