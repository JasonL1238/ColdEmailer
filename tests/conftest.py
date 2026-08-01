"""Pytest configuration and shared fixtures."""
import os
import sys
import tempfile
from pathlib import Path

project_root = Path(__file__).parent.parent
backend_path = project_root / "backend"
sys.path.insert(0, str(backend_path))
sys.path.insert(0, str(project_root))

os.environ['PYTHONPATH'] = f"{backend_path}:{project_root}:{os.environ.get('PYTHONPATH', '')}"

# Point every test at a throwaway database BEFORE anything imports backend
# modules. `main` opens the database at import time (module-level service
# wiring), so without this the suite would run migrations against — and hold a
# lock on — the user's real coldemailer.db.
_tmp_data = tempfile.mkdtemp(prefix="coldemailer-tests-")
os.environ['COLD_DB_PATH'] = os.path.join(_tmp_data, "test.db")
# Resumes live on disk, not in the database, and resume_service used to derive
# its directory from __file__ alone. The legacy-import guard is a *setting*, so
# a fresh temp DB has none — every suite run re-imported the user's real PDFs
# and wrote new content-hashed copies into their real backend/data/resumes.
os.environ['COLD_RESUME_DIR'] = os.path.join(_tmp_data, "resumes")
# Keep Gmail/OAuth paths pointed at nonexistent files so nothing can
# accidentally authenticate or send during a test run.
os.environ.setdefault('CREDENTIALS_JSON_PATH', os.path.join(_tmp_data, "no-credentials.json"))
os.environ.setdefault('TOKEN_JSON_PATH', os.path.join(_tmp_data, "no-token.json"))

# Neutralise every AI/enrichment provider key. Tests stub the LLM seam, but a
# stub that misses — one refactor routed around the patch point — turns the
# suite into a live client spending the user's paid quota, which is exactly
# what happened once. With no key configured, get_cloud_llm_provider() returns
# None and every AI path takes its offline branch, so a missed stub fails
# loudly and cheaply instead of going to the network. Tests that need a
# provider monkeypatch it in.
#
# Set to empty rather than delete, deliberately: importing `main` runs
# load_dotenv() at module level, and load_dotenv does not overwrite a key that
# is already present — but it will happily repopulate one that was deleted,
# handing the suite the real keys back.
for _key in ('GOOGLE_AI_API_KEY', 'OPENAI_API_KEY', 'OPENROUTER_API_KEY',
             'EMAIL_LLM_PROVIDER', 'EMAIL_LLM_MODEL', 'HUNTER_API_KEY',
             'OLLAMA_BASE_URL'):
    os.environ[_key] = ''
