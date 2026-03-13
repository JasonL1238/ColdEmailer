# AI Cold Emailer

AI-powered cold outreach pipeline: CSV contacts → automated company research → LLM-generated personalized emails → review UI → batch send via Gmail API.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Frontend (React + Vite)  :5173                                 │
│  ┌──────────────┐  ┌──────────────┐                             │
│  │  CSVManager   │  │ EmailReview  │                             │
│  │  (contacts)   │  │ (review/send)│                             │
│  └──────┬───────┘  └──────┬───────┘                             │
│         └────────┬────────┘                                     │
│                  │ axios                                        │
├──────────────────┼──────────────────────────────────────────────┤
│  Backend (FastAPI)  :8000                                       │
│                  │                                              │
│  ┌───────────────▼───────────────┐                              │
│  │         main.py (API)         │                              │
│  └──┬───────┬───────┬──────┬────┘                               │
│     │       │       │      │                                    │
│  ┌──▼──┐ ┌──▼───┐ ┌─▼──┐ ┌▼──────────────┐                     │
│  │ CSV │ │Email │ │LLM │ │  Company       │                     │
│  │Proc.│ │Sender│ │Gen.│ │  Enrichment    │                     │
│  └──┬──┘ └──┬───┘ └─┬──┘ └┬──────────────┘                     │
│     │       │       │      │                                    │
│  contacts  Gmail   Cloud  Web Scraper                           │
│  .csv      API     LLM   (requests +                            │
│            OAuth2  API    trafilatura +                          │
│                           DuckDuckGo)                           │
└─────────────────────────────────────────────────────────────────┘
```

## Features

- **CSV contacts** — Upload, edit, add, delete; sections: Emailed, Generated (not sent), No emails yet
- **Company research** — DuckDuckGo search → web scraping → LLM metadata extraction (summary, product, industry, news)
- **AI email generation** — Cloud LLM (Gemini/OpenRouter/OpenAI) writes personalized cold emails using company research + your resume/skills. Template fallback if no LLM key is set.
- **Review UI** — Accept/trash before sending; inline edit subject & body; attach resume (2028 or 2029)
- **Batch send** — Gmail API with rate limits, response tracking, and follow-up reminders
- **Follow-ups** — Auto-detect no-response contacts; generate and send follow-up emails

## Quick start

```bash
./start.sh
```

Then open **http://localhost:5173**.
Backend: http://localhost:8000 · API docs: http://localhost:8000/docs

## Prerequisites

- **Python 3.12** (3.13 has compatibility issues; use `python3.12 -m venv venv` if needed)
- **Node.js 18+**
- **LLM API key** — At least one of: Google AI (Gemini), OpenRouter, or OpenAI. See `.env.example`.
- **Ollama** (optional) — [ollama.ai](https://ollama.ai) for local follow-up generation: `ollama pull llama3.2`

## Setup

### 1. Environment

Copy `.env.example` to `.env` and fill in your API key(s):

```bash
cp .env.example .env
```

Key settings:
- `GOOGLE_AI_API_KEY` — Gemini (recommended, free tier available). Provider auto-detected by key.
- `OPENROUTER_API_KEY` / `OPENAI_API_KEY` — alternatives
- `OLLAMA_BASE_URL`, `OLLAMA_MODEL` — local Ollama for follow-ups
- Rate limits: `MAX_EMAILS_PER_DAY`, `MAX_EMAIL_GENERATIONS_PER_MINUTE`, etc.
- `RESUME_PATH` — resume PDF for attachments (optional; app also checks `resume28.pdf` / `resume29.pdf` in project root)

### 2. Gmail API (for sending)

1. [Google Cloud Console](https://console.cloud.google.com/) → create project → enable **Gmail API**
2. **APIs & Services** → **Credentials** → **Create credentials** → **OAuth client ID**
3. Configure OAuth consent screen if prompted (External, app name, add scope `https://www.googleapis.com/auth/gmail.send`, add your email as test user)
4. Application type: **Desktop app** → Create → **Download JSON**
5. Save as `credentials.json` in **project root**
6. First send will open the browser for sign-in; `token.json` is created automatically

**Troubleshooting:** "Credentials not found" → ensure `credentials.json` is in project root. "Access denied" → add your Gmail as test user in OAuth consent screen. To switch account → delete `token.json` and send again.

### 3. Resume (optional)

Put a PDF in project root (e.g. `resume.pdf`, `resume28.pdf`, `resume29.pdf`) or set `RESUME_PATH` in `.env`. When sending, you can attach **2028 resume** (default) or **2029 resume** from the dropdown.

## Running

**One command:** `./start.sh` (starts backend on 8000, frontend on 5173).

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
2. **Review Emails** — "Generate Emails" for contacts with no email; review and accept/trash
3. **Settings** (⚙️) — Name, email, background (used in generation)
4. **Send** — "Send Accepted Emails"; attach 2028 or 2029 resume (default 2028)
5. **Emailed** tab — Sent dates, response status, follow-up reminders (e.g. after 1 week)

## How email generation works

1. **Company enrichment** — DuckDuckGo search finds the company website; `trafilatura` + `BeautifulSoup` scrape and clean the page; an LLM extracts structured metadata (summary, product, industry, recent news).
2. **Email drafting** — The cloud LLM (Gemini by default) receives your resume/skills + company metadata and writes a personalized cold email. Falls back to a fixed template if no LLM key is configured.
3. **Gemini model fallback** — On quota exhaustion (429), automatically cycles through: `gemini-2.5-flash` → `gemini-3-flash` → `gemini-3.1-flash-lite` → lighter Gemma models.

## Rate limits

Configured in `.env`; restart backend after changes.

| Limit | Default | Env variable |
|-------|---------|--------------|
| Emails per day (send) | 50 | `MAX_EMAILS_PER_DAY` (Gmail free tier up to 500/day) |
| Generations per day | 500 | `MAX_EMAIL_GENERATIONS_PER_DAY` |
| Generations per minute | 10 | `MAX_EMAIL_GENERATIONS_PER_MINUTE` |
| Company researches per minute | 5 | `MAX_COMPANY_RESEARCH_PER_MINUTE` |
| Delay between sends | 3 s | `EMAIL_SEND_DELAY_SECONDS` |

## CSV format

- **Required:** `name`, `company`, `email`
- **Optional:** `id`, `status` (e.g. pending, trashed, sent)

**Personalization:** The email body uses your `skills.md` file (project root) for the "about you" sentence. Add a `## Email one-liner` section with one sentence, or the app uses the first sentence of your first `## Experience` block.

## Security

- Do **not** commit `.env`, `credentials.json`, or `token.json` (they are in `.gitignore`)
- Use `.env.example` as a template (no real secrets)
- CORS origins: set `CORS_ORIGINS` in `.env` for production

## Project layout

- **backend/** — FastAPI, CSV/contact handling, LLM client, Gmail send, rate limiting
- **frontend/** — React + Vite UI
- **stress/** — Load/concurrency tests
- **tests/** — Unit tests

## Tech stack

| Layer | Tech |
|-------|------|
| Frontend | React 18, Vite, react-hot-toast |
| Backend | Python 3.12, FastAPI, Uvicorn |
| LLM | Google Gemini (primary), OpenRouter, OpenAI, Ollama (local fallback) |
| Email | Gmail API (OAuth2) |
| Scraping | requests, trafilatura, BeautifulSoup4, duckduckgo-search |
| Data | CSV (contacts), JSON (emails, company cache) |

## License

MIT
