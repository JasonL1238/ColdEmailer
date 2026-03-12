# AI Cold Emailer

Web-based cold emailer: CSV contacts → company research → AI-generated emails (Ollama) → review → batch send via Gmail API.

## Features

- **CSV contacts** — Upload, edit, add, delete; sections: Emailed, Generated (not sent), No emails yet
- **Company research** — Scraping + AI extraction for personalization
- **AI generation** — Fixed template: same skeleton every time; only company name, company details (from research), and one experience sentence (from `skills.md` — see “Email one-liner” section or first Experience sentence). Follow-ups still use Ollama.
- **Review** — Accept/trash before sending; attach resume (default: 2028 resume; option: 2029 resume)
- **Batch send** — Gmail API with rate limits; response tracking and follow-up reminders

## Quick start

```bash
./start.sh
```

Then open **http://localhost:5173**.  
Backend: http://localhost:8000 · API docs: http://localhost:8000/docs

## Prerequisites

- **Python 3.12** (3.13 has compatibility issues; use `python3.12 -m venv venv` if needed)
- **Node.js 18+**
- **Ollama** — [ollama.ai](https://ollama.ai), then: `ollama pull llama3.2`

## Setup

### 1. Environment

Copy `.env.example` to `.env` and adjust if needed. Defaults:

- `OLLAMA_BASE_URL`, `OLLAMA_MODEL` — Ollama
- `MAX_EMAILS_PER_DAY=50`, `MAX_EMAIL_GENERATIONS_PER_MINUTE=10` — rate limits (see [Rate limits](#rate-limits))
- `RESUME_PATH` — resume PDF for attachments (optional; app also checks `resume28.pdf` / `resume29.pdf` in project root)

Paths (CSV, cache, email storage) are resolved from project root; see `backend/README.md` for details.

### 2. Gmail API (for sending)

1. [Google Cloud Console](https://console.cloud.google.com/) → create project → enable **Gmail API**
2. **APIs & Services** → **Credentials** → **Create credentials** → **OAuth client ID**
3. Configure OAuth consent screen if prompted (External, app name, add scope `https://www.googleapis.com/auth/gmail.send`, add your email as test user)
4. Application type: **Desktop app** → Create → **Download JSON**
5. Save as `credentials.json` in **project root**
6. First send will open the browser for sign-in; `token.json` is created automatically

**Troubleshooting:** “Credentials not found” → ensure `credentials.json` is in project root. “Access denied” → add your Gmail as test user in OAuth consent screen. To switch account → delete `token.json` and send again.

### 3. Resume (optional)

Put a PDF in project root (e.g. `resume.pdf`, `resume28.pdf`, `resume29.pdf`) or set `RESUME_PATH` in `.env`. When sending, you can attach **2028 resume** (default) or **2029 resume** from the dropdown.

## Running

**One command:** `./start.sh` (starts Ollama if needed, backend on 8000, frontend on 5173).

**Manual (two terminals):**

```bash
# Terminal 1
cd backend && source venv/bin/activate && uvicorn main:app --reload --port 8000

# Terminal 2
cd frontend && npm run dev
```

Open http://localhost:5173.

## Usage

1. **Contacts** — Upload CSV (columns: `name`, `company`, `email`) or add manually
2. **Review Emails** — “Generate Emails” for contacts with no email; review and accept/trash
3. **Settings** (⚙️) — Name, email, background (used in generation)
4. **Send** — “Send Accepted Emails”; attach 2028 or 2029 resume (default 2028)
5. **Emailed** tab — Sent dates, response status, follow-up reminders (e.g. after 1 week)

## Rate limits

Configured in `.env`; restart backend after changes.

| Limit | Default | Env variable |
|-------|---------|--------------|
| Emails per day (send) | 50 | `MAX_EMAILS_PER_DAY` (Gmail free tier up to 500/day) |
| Generations per day | 500 | `MAX_EMAIL_GENERATIONS_PER_DAY` (practice without burning send quota) |
| Generations per minute | 10 | `MAX_EMAIL_GENERATIONS_PER_MINUTE` (why only ~10 generate at a time) |
| Company researches per minute | 5 | `MAX_COMPANY_RESEARCH_PER_MINUTE` |
| Delay between sends | 3 s | `EMAIL_SEND_DELAY_SECONDS` |

Gmail API is **free** for normal use (e.g. &lt;500 emails/day).

## CSV format

- **Required:** `name`, `company`, `email`
- **Optional:** `id`, `status` (e.g. pending, trashed, sent)

**Email template:** Cold email body uses a fixed skeleton. The only variable “about you” sentence comes from project-root `skills.md`: add a `## Email one-liner` section with one sentence, or the app uses the first sentence of your first `## Experience` block. See `backend/README.md` for details.

## Security

- Do **not** commit `.env`, `credentials.json`, or `token.json` (they are in `.gitignore`)
- Use `.env.example` as a template (no real secrets)
- CORS origins: set `CORS_ORIGINS` in `.env` for production

## Project layout

- **backend/** — FastAPI, CSV/contact handling, Ollama, Gmail send, rate limiting (see `backend/README.md`)
- **frontend/** — React + Vite (see `frontend/README.md`)
- **stress/** — Load/concurrency tests (see `stress/README.md`)
- **tests/** — Unit tests (see `tests/README.md`)

## License

MIT
