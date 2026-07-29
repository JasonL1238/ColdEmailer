# Tests

## Backend (pytest)

```bash
cd tests && ../backend/venv/bin/python -m pytest
```

| File | Covers |
|---|---|
| `test_db.py` | SQLite layer: CRUD, cascades, follow-up candidate selection, job lifecycle, settings |
| `test_email_composer.py` | LLM output parsing, template fallbacks per email type, prompt construction |
| `test_enrichment.py` | Email extraction and ranking, domain parsing, heuristic metadata |
| `test_security.py` | SSRF guards, recipient validation, reply-check failure handling |
| `test_rate_limiter.py` | Send/generation/research caps |
| `test_text_cleaner.py` | Scraped-text normalization |

## Frontend (Vitest)

```bash
cd frontend && npm test
```

Covers the API client's request shapes and error normalization.
