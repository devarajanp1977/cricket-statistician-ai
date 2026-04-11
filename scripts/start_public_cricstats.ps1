$env:APP_BASE_PATH = "/cricstats"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080