# Reach — AI cold outreach studio

Find companies with an AI search, scrape their sites for contact emails and talking points, generate a tailored cold email for each one, send it from your Gmail, and track every reply — in one app.

```
Discover ──▶ Database ──▶ Emails ──▶ Send ──▶ Track
 search       companies    AI drafts   Gmail    replies
 + scrape     + contacts   by type     + resume + follow-ups
```

## Quick start

```bash
./start.sh
```

Opens the app at **http://localhost:5173** (API docs at http://localhost:8000/docs). First run creates the Python venv and installs dependencies automatically.

## What it does

**Discover** — Type what you're looking for in plain English ("seed-stage fintech startups in New York"). Reach asks the LLM for real matching companies, cross-checks with a web search, finds each company's website, follows its real first-party navigation (leadership, team, about, contact, careers, press, news, and blog pages), and extracts a structured company profile plus public contact addresses. It keeps evidence-backed CEO/COO/CTO, hiring, recruiting, and engineering leads when a company page publishes either an email or a LinkedIn member link. It records every source page and crawl count so you can audit the research. Runs in the background with live progress.

**Database** — Every company and contact in one place. Drill into a company to see its scraped research, its contacts, and the full email history with that company. Warm matches are ranked from your configured school and past employers/communities. For a contact with a verified LinkedIn URL, Reach can draft and copy a message, then open the profile for you to review and send manually. Import a Reach CSV (`name, company` plus `email` and/or `linkedin_url`) or your LinkedIn Connections CSV export; imported LinkedIn rows are marked as direct connections. Re-run research on any company at any time.

**Resumes** — Upload multiple PDF versions ("ML research", "Full-stack", "2029 general"). Reach extracts the text so the AI can weave your real projects into emails, and attaches the PDF you pick when sending. One is marked default. PDFs preview inline in the app, with separate open-in-tab and download actions.

**Emails** — Pick contacts, choose an email type, and Reach writes each one individually using that company's scraped research plus your selected resume and profile:

| Type | What it writes |
|---|---|
| **Application** | Internship/job inquiry, leaning hard on your resume to argue fit for that specific company |
| **Coffee chat** | Warm networking ask for 15 minutes; explicitly not asking for a job |
| **Sales / Pitch** | Product pitch framed around the recipient's problem, not your biography |
| **Custom** | Follows your own instructions, still grounded in the scraped research |

Add free-text instructions to any type ("mention I saw their Show HN post"). Review drafts side by side, edit subject and body inline, rewrite with AI, approve, then send in a batch.

**Tracking** — The dashboard shows the funnel (companies → contacts → drafts → sent → replied), a 30-day sent/reply chart, per-type reply rates, and a live activity feed. "Check replies" scans your Gmail threads for genuine responses, ignoring bounces, out-of-office auto-replies, and your own follow-ups in the same thread. Contacts who go quiet surface for follow-up on the cadence you set in Settings — up to four nudges, each measured from the last message that person actually received. Draft one at a time or the whole due list at once; nothing sends itself. A reply, a bounce, or trashing the draft ends the sequence, and follow-ups are threaded onto the original conversation.

## Guardrails

Cold outreach is easy to get wrong in ways that are embarrassing rather than merely broken, so a few things are enforced rather than left to care:

- **Nobody gets the same first email twice.** Anyone already emailed is skipped during generation and pointed at the follow-up flow. You can override it with a checkbox when you mean to.
- **Scraped text can't hijack your emails.** Company research is untrusted input: it is fenced as data in the prompt, and any page that tries to inject instructions has its text dropped rather than quoted.
- **The website has to actually be the company's.** A search result is only accepted if the domain matches the name or the page names the company; otherwise the row is marked *Wrong site found* instead of inventing a profile. Existing bad rows are cleaned up on first startup.
- **Emails don't claim attachments they don't have.** The "resume is attached" line only appears when a real PDF will be attached, and sales emails never attach one.
- **Affinity never overrides address safety.** Public same-school matches (including UPenn/Wharton when that is your configured school) and senior leaders rank first, but only addresses actually found in the scraped evidence are accepted; a personal/off-domain address never displaces a company-domain address.
- **Sent mail is not deletable.** It is the record of what a real person received, and it is what prevents double-contacting.
- **Addresses are validated twice** — on the way in and again at send time — so a comma or newline can never add a recipient or a `Bcc:` header.
- **Archive, don't delete.** Archiving keeps the history and stops future outreach.

## Setup

### 1. AI provider (required for discovery and AI writing)

Copy `.env.example` to `.env` and add one key:

```bash
cp .env.example .env
```

- `GOOGLE_AI_API_KEY` — Gemini, has a free tier, recommended
- `OPENAI_API_KEY` or `OPENROUTER_API_KEY` — alternatives

### Running without any AI key

The app works keyless, with a narrower feature set. Nothing silently degrades — the UI shows which mode you are in:

| | With a key | No key |
|---|---|---|
| Find companies | LLM suggests them, web search cross-checks | Web search only — noisier, returns some VCs and directories alongside real companies |
| Company research | Structured profile (product, industry, hook, recent news) | Summary only, taken from the first lines of the scraped page |
| Contact email scraping | ✅ | ✅ identical |
| Application / coffee chat / sales emails | Written per company | Filled-in template, still personalized with the scraped summary and your profile |
| Custom emails | ✅ | ✗ refused rather than ignoring your instructions |
| Send, attachments, reply tracking, follow-ups | ✅ | ✅ identical |

Keyless discovery depends on the `ddgs` package. If searches return nothing, check it is installed (`pip install -r requirements.txt`) — the older `duckduckgo-search` package is deprecated and silently returns zero results.

### 2. Gmail (required for sending)

1. [Google Cloud Console](https://console.cloud.google.com/) → new project → enable **Gmail API**
2. **Credentials** → **Create credentials** → **OAuth client ID** → **Desktop app**
3. Download the JSON, save it as `credentials.json` in the project root

The first time you hit Send, a Google sign-in window opens on the machine running the backend. The resulting `token.json` is gitignored, and you can disconnect from Settings.

### 3. Your profile

Open **Settings** and fill in your name, email, school, past employers/communities, website, and a concrete background one-liner. School and employer entries are used to flag warm matches in public company biographies. The AI writes every email as you, so specifics here ("built computer-vision pipelines with OpenCV and YOLO") produce far better emails than generalities.

## Architecture

The React/Vite frontend calls a FastAPI backend backed by one local SQLite
database. Long operations run as background jobs that the UI polls for progress.
See [`docs/architecture.md`](docs/architecture.md) for component and dependency
boundaries, and [`docs/map.md`](docs/map.md) to locate changes. Working on this
repo with a coding agent? [`AGENTS.md`](AGENTS.md) is the entry point.

Data from the previous CSV/JSON version (`contacts.csv`, `generated_emails.json`, `company_cache.json`, root-level `resume*.pdf`) is imported automatically on first startup.

## Configuration

All optional, set in `.env`:

| Variable | Default | Purpose |
|---|---|---|
| `MAX_EMAILS_PER_DAY` | 50 | Send cap |
| `MAX_EMAIL_GENERATIONS_PER_DAY` | 500 | Generation cap |
| `MAX_EMAIL_GENERATIONS_PER_MINUTE` | 10 | Burst cap |
| `MAX_COMPANY_RESEARCH_PER_MINUTE` | 20 (`.env.example` ships 5) | Scrape rate |
| `EMAIL_SEND_DELAY_SECONDS` | 3 | Pause between sends |
| `EMAIL_LLM_MODEL` | provider default | Override the model |
| `CORS_ORIGINS` | localhost:5173,3000 | Allowed frontend origins |
| `COLD_DB_PATH` | `backend/data/coldemailer.db` | Database location |

## Tests

```bash
make test-backend
```

```bash
make test-frontend
```

The backend suite runs against a throwaway database (`tests/conftest.py` sets `COLD_DB_PATH`), so it never touches your real data or Gmail credentials.
See [`docs/testing.md`](docs/testing.md) for targeted checks, linting, scoped type
checking, builds, and full validation.

## Notes on responsible use

Reach does **not** consult `robots.txt`. It fetches only public first-party pages a browser would load, rate-limits per domain, backs off when a server returns `Retry-After`, and caps how many pages it takes from any one site — but if you need robots compliance for your use case, it is not there today and you would have to add it.

Cold email is regulated in most jurisdictions (CAN-SPAM, GDPR, CASL) — send to people plausibly interested in hearing from you, keep volumes sane, and honor opt-outs.

To run the deterministic scraping benchmark:

```bash
backend/venv/bin/python scripts/evaluate_scraping.py
```

For a read-only live diagnostic (results vary with site changes and bot controls):

```bash
backend/venv/bin/python scripts/evaluate_scraping.py \
  --live 'Openlayer=https://www.openlayer.com'
```
