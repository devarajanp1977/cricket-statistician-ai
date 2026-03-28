# Cricket Statistician AI

AI-powered cricket statistics analysis agent with synthetic data generation, match analysis, and player performance tracking.

## Project Structure

```
app/
  cricket/
    agent.py           # Core AI agent logic
    models.py          # Data models
    router.py          # API endpoints
    synthetic_data.py  # Synthetic cricket data generation
  main.py              # FastAPI application entry point
frontend/
  cricket.html         # Web UI
tests/
  test_cricket.py      # Test suite
```

## Getting Started

### Install dependencies

```bash
pip install -r requirements.txt
```

### Run the application

```bash
uvicorn app.main:app --reload
```

### Run tests

```bash
pytest tests/
```

## API Endpoints

See the auto-generated docs at `/docs` when the server is running.
