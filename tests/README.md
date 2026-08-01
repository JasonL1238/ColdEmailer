# Tests

## Backend (pytest)

```bash
cd tests && ../backend/venv/bin/python -m pytest
```

747 tests. `conftest.py` points `COLD_DB_PATH` at a throwaway database and stubs
the Gmail credential paths, so the suite never touches real data or sends mail.

| File | Covers |
|---|---|
| `test_db.py` | SQLite layer: CRUD, cascades, follow-up candidate selection, job lifecycle, settings |
| `test_email_composer.py` | LLM output parsing, template fallbacks per email type, prompt construction |
| `test_enrichment.py` | Email extraction and ranking, domain parsing, heuristic metadata |
| `test_security.py` | SSRF guards, recipient validation, reply-check failure handling |
| `test_rate_limiter.py` | Send/generation/research caps |
| `test_text_cleaner.py` | Scraped-text normalization — including that one nav word cannot discard a whole page |
| `test_prompt_safety.py` | Scraped research is untrusted: it cannot hijack the prompt or put claims in an email |
| `test_send_integrity.py` | No duplicate first contacts, follow-ups thread onto the original, no header injection |
| `test_batch_integrity.py` | Batch jobs report every skipped recipient instead of stopping silently at the first cap hit |
| `test_reply_verification.py` | Verified replies vs. flags inherited from the old checker that counted bounces |
| `test_company_identity.py` | A search hit only becomes the company's site if it demonstrably belongs to them |
| `test_junk_sites.py` | Parked pages, aggregators and content farms are rejected as company sites |
| `test_outreach_selection.py` | Prefers the company's own domain; flags addresses that belong to someone else |
| `test_scraping_rigor.py` | Nonstandard navigation, obfuscated emails, grounded senior/UPenn ranking, domain safety, crawl coverage |
| `test_pdf_viewing.py` | Inline PDF preview versus explicit attachment download |
| `test_discovery_note.py` | The model's guess at why a company matched is never quoted as research |
| `test_data_safety.py` | Data-loss and quota-integrity guards (CSV handling, destructive deletes) |

## Frontend (Vitest)

```bash
cd frontend && npm test
```

65 tests over the API client's request shapes and the screens where a wrong
render would cause a real mistake: the compose modal, the company drawer, and
the email list's delivered / attachment-claim / reply-verification / send-safety
states.
