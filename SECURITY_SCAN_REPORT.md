# Security Scan Report — API Keys / Secrets & Leak Prevention

**Scan date:** 2025-02-17  
**Scope:** Working tree + git history (all branches/commits)  
**Constraints:** No full secrets printed; redaction format `first4....last4` where applicable.

---

## 1) Summary

| Severity | Count |
|----------|--------|
| **Critical** | 0 |
| **High** | 1 |
| **Medium** | 1 |
| **Low** | 2 |

**Overall:** No API keys, OAuth secrets, or PEM blocks were found in the repo. `.env` and `credentials.json`/`token.json` are correctly ignored and were **not** found in git history. One hardcoded telemetry endpoint URL (with UUID) and absolute paths exposing your username are the main issues.

---

## 2) Findings Table

| Severity | File(s) | Line(s) | Pattern description | Redacted snippet | Why it's risky |
|----------|---------|---------|----------------------|------------------|------------------|
| **High** | `frontend/src/App.jsx`, `frontend/src/main.jsx`, `frontend/src/api.js`, `frontend/src/CSVManager.jsx` | Multiple (see below) | Hardcoded telemetry/ingest URL with UUID | `http://127.0.0.1:7243/ingest/2a1d....92a4` | UUID acts as an endpoint/agent identifier. Present in **client-side bundle** and in **git history**. If 7243 is ever exposed or the UUID is reused elsewhere, it could be abused to send fake events or infer internal tooling. |
| **Medium** | `backend/main.py`, `backend/email_sender.py`, `backend/csv_processor.py`, `GMAIL_SETUP.md`, `QUICK_START.md`, `FULL_SETUP.md`, `RESUME_SETUP.md`, others | Various | Absolute path containing username | `/Users/jasonli..../ColdEmailer/` | Exposes local username and machine path; not portable; unnecessary for docs (use `project root` or `$PROJECT_ROOT`). |
| **Low** | `backend/main.py` | 68 | CORS origins fixed to localhost | `allow_origins=["http://localhost:5173", "http://localhost:3000"]` | Not a secret leak; deployment will break until origins are configurable (e.g. env). |
| **Low** | Repo root | — | No `.env.example` | — | New contributors may copy `.env` or guess keys; an example (with placeholders, no real values) reduces temptation to commit real `.env`. |

### High – Telemetry URL (exact locations)

- **frontend/src/App.jsx:** 2 occurrences (lines ~8, ~13)
- **frontend/src/main.jsx:** 1 occurrence (line ~8)
- **frontend/src/api.js:** 7 occurrences (lines ~16, ~22, ~32, ~38, ~45, ~52, ~56)
- **frontend/src/CSVManager.jsx:** 25 occurrences (throughout, in `#region agent log` blocks)

**Git history:** Same URL (and UUID) appears in commits including `b168f8de`, `6f4f95e5`, `96d50947`, `8894a415` in those files. So it is in history, not only the working tree.

### Medium – Absolute paths (sample)

- **backend/main.py:** `.cursor/debug.log` path (e.g. lines 29, 39, 111, 151, 169, 188, 210, 218, 666)
- **backend/email_sender.py:** same debug.log path (many lines)
- **backend/csv_processor.py:** same
- **Docs:** `QUICK_START.md`, `GMAIL_SETUP.md`, `FULL_SETUP.md`, `RESUME_SETUP.md`, `QUICK_GMAIL_SETUP.md`, `EMAIL_LIMITS.md`, `READY_TO_RUN.md`, `STATUS.md`, `HOW_TO_RUN.md`, `QUICKSTART.md` — use `cd /Users/jasonli/Documents/GitHub/ColdEmailer` or similar

---

## 3) Remediation Plan

### A) Remove / fix secrets and risky patterns

1. **Telemetry URL (High)**  
   - **Option A (recommended):** Remove all `#region agent log` / telemetry `fetch` calls from frontend (App.jsx, main.jsx, api.js, CSVManager.jsx). If you need telemetry later, use a build-time or runtime env var (e.g. `VITE_TELEMETRY_URL`) that is empty in production.  
   - **Option B:** Keep behavior but move URL to env, e.g. `VITE_INGEST_URL`, and only set it in dev; in production build leave it unset so no request is sent.  
   - Redact in any docs or runbooks: do not paste the full URL; use `http://127.0.0.1:7243/ingest/<REDACTED>`.

2. **Absolute paths (Medium)**  
   - **Backend:** Replace `/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log` with a path derived from `os.getenv('PROJECT_ROOT', '.')` or `pathlib.Path(__file__).resolve().parents[2]` and then `.cursor/debug.log`, or disable file logging when `PROJECT_ROOT` is unset.  
   - **Docs:** Use “project root”, “repo root”, or `$PROJECT_ROOT` (and show `export PROJECT_ROOT=/path/to/ColdEmailer`) instead of `/Users/jasonli/...`.

3. **CORS (Low)**  
   - Read allowed origins from env, e.g. `os.getenv('CORS_ORIGINS', 'http://localhost:5173,http://localhost:3000').split(',')`, and use that in `allow_origins`.

4. **.env.example (Low)**  
   - Add `.env.example` at repo root (and optionally `backend/.env.example`) with keys only and placeholder values (e.g. `OLLAMA_BASE_URL=http://localhost:11434`). Document in README that users should copy to `.env` and fill in.

### B) Rotate / invalidate if any secret was ever exposed

- **Current scan:** No API keys, OAuth client secrets, or tokens were found in the repo or in the scanned git history.  
- **Telemetry UUID:** If the ingest endpoint is sensitive, treat the UUID as a secret: rotate or disable that endpoint and stop using it in the frontend (or move to env and never commit the value).

### C) Remove from git history (only if you confirm a real secret was committed)

- **Do not** run history rewrite for “cleaning” unless you have a concrete secret (e.g. a key) that was committed. Rewriting history force-pushes and disrupts everyone who has cloned the repo.  
- **If** you later find a real secret in history:  
  1. Rotate/revoke the secret everywhere.  
  2. Use `git filter-repo` (preferred) or BFG Repo-Cleaner to remove the secret from all commits.  
  3. Force-push and have all collaborators re-clone or rebase.  
  4. Prefer `git filter-repo --path .env --invert-paths` (or similar) over manual edits.  

**Warnings:**  
- `git filter-branch` is not recommended; use `git filter-repo` or BFG.  
- After a rewrite, any clone or fork that still has the old history can expose the secret; rotation is mandatory.

---

## 4) Prevention

### A) .gitignore — add/verify

Ensure these are present (you already have `.env`, `.env.local`, `credentials.json`, `token.json`). Suggested additions:

```gitignore
# Secrets and env
.env
.env.*
!.env.example
*.key
*.pem
*.p12
*.pfx
*.jks
credentials*.json
token*.json
*.log
.cursor/
```

- `backend/data/*.csv` and `backend/data/*.json` are already ignored; keep them.  
- If you use a secret manager or local override, add those paths too (e.g. `secrets/`).

### B) Pre-commit hook (local)

Run from repo root:

```bash
mkdir -p .git/hooks
cat << 'EOF' > .git/hooks/pre-commit
#!/bin/sh
# Block commits that look like secrets (baseline check)
if git diff --cached --name-only | grep -qE '\.env$|^\.env\.|credentials\.json|token\.json|\.pem$|\.key$'; then
  echo "ERROR: Attempt to commit a secret or env file. Aborting."
  exit 1
fi
if git diff --cached | grep -qE 'sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{36}|AKIA[0-9A-Z]{16}|-----BEGIN (RSA|EC|OPENSSH) PRIVATE KEY-----'; then
  echo "ERROR: Possible secret or private key in staged diff. Aborting."
  exit 1
fi
exit 0
EOF
chmod +x .git/hooks/pre-commit
```

Optional: install [pre-commit](https://pre-commit.com/) and add a `detect-secrets` or `gitleaks` hook for stronger checks.

### C) CI check (GitHub Actions example)

Create `.github/workflows/secret-scan.yml`:

```yaml
name: Secret scan
on: [push, pull_request]
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Gitleaks
        uses: gitleaks/gitleaks-action@v2
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          GITLEAKS_LICENSE: ${{ secrets.GITLEAKS_LICENSE }}
```

If you don’t use Gitleaks, you can run a simple grep step instead (e.g. fail on `sk-`, `ghp_`, `AKIA`, `-----BEGIN ... PRIVATE KEY-----` in tracked files).

### D) Cursor / editor workflow

- Never paste API keys or tokens into chat or into files that are committed.  
- Use env vars or a local `.env` (in `.gitignore`) and reference them by name in prompts (e.g. “use the key from OPENAI_API_KEY”).  
- Before committing, run a quick scan:  
  `rg -i 'sk-|ghp_|AKIA|Bearer [a-zA-Z0-9_-]{20,}' --type-add 'code:*.{js,jsx,ts,tsx,py,json,yaml,yml}' -t code .`  
  and ensure no real secrets appear.

---

## 5) Commands you can run from repo root

**Rescan for common key patterns (no full secrets printed):**

```bash
rg -i 'sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{36}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}' --type-add 'code:*.{js,jsx,ts,tsx,py,json,yaml,yml}' -t code . 2>/dev/null || true
rg '-----BEGIN (RSA|EC|OPENSSH) PRIVATE KEY-----' . 2>/dev/null || true
git log -p --all -S 'sk-' -- '*.py' '*.js' '*.jsx' '*.ts' '*.tsx' 2>/dev/null | head -50
```

**Check that sensitive files are ignored:**

```bash
git check-ignore -v .env backend/.env credentials.json token.json 2>/dev/null
```

**Optional – install and run Gitleaks (one-off scan):**

```bash
# macOS
brew install gitleaks
gitleaks detect --source . -v --no-git
```

---

*End of report. Treat any redacted snippet as sensitive; do not re-paste the full value in chat or in docs.*
