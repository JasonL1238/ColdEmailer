# Tests

Unit tests for backend (pytest) and frontend (Vitest).

## Layout

- `unit/backend/` — Python (e.g. `test_csv_processor.py`, `test_rate_limiter.py`, `test_text_cleaner.py`)
- `unit/frontend/` — JS (e.g. `api.test.js`)

## Backend (pytest)

```bash
# From project root
pytest tests/

# Specific file
pytest tests/unit/backend/test_csv_processor.py

# With coverage
pytest tests/ --cov=backend --cov-report=html
```

Requires backend venv activated and deps installed.

## Frontend (Vitest)

```bash
cd frontend
npm run test
```

## Conftest

`tests/conftest.py` adjusts Python path so `backend` is importable when running pytest from root.
