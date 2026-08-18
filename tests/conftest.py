"""Pytest configuration and shared fixtures."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

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
# Importing `main` starts the send scheduler. It no-ops while the sending
# window is off (the shipped default), but a background thread whose job is
# to hand email to Gmail does not belong in a test process at all. Tests call
# main.scheduled_send_sweep() directly, which is the honest way to test it.
os.environ['COLD_DISABLE_SCHEDULER'] = '1'
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
# GITHUB_TOKEN / SEC_CONTACT_EMAIL / MAILBOX_VERIFY belong here for the same
# reason: with a developer's real .env loaded, a missed stub would send the
# suite at live GitHub, at sec.gov, or at a stranger's mail server.
for _key in ('GOOGLE_AI_API_KEY', 'OPENAI_API_KEY', 'OPENROUTER_API_KEY',
             'EMAIL_LLM_PROVIDER', 'EMAIL_LLM_MODEL', 'HUNTER_API_KEY',
             'OLLAMA_BASE_URL', 'GITHUB_TOKEN', 'SEC_CONTACT_EMAIL',
             'MAILBOX_VERIFY', 'SMTP_PROBE_HELO', 'SMTP_PROBE_FROM'):
    os.environ[_key] = ''

# ADDRESS_CORROBORATION cannot join the loop above: it defaults ON, so blanking
# it would *enable* corroboration and send every test that produces a pattern
# guess at live GitHub, Gravatar and lore. It needs the explicit off value, and
# tests that exercise it turn it on with an injected http_get.
os.environ['ADDRESS_CORROBORATION'] = '0'


# ---------------------------------------------------------------------------
# Shared fixtures.
#
# `db` was copy-pasted verbatim into fourteen backend test modules, `client`
# into three more, and `wired_db` into two; there is now one definition of each
# to keep honest. Backend imports stay *inside* the fixture bodies on purpose:
# everything above exists to fix the environment before any backend module is
# imported, and a top-level `import main` here would run at collection time and
# quietly undo that ordering.


@pytest.fixture
def db():
    """A throwaway database on its own temp directory, deleted per test."""
    from db import Database

    with tempfile.TemporaryDirectory() as tmp:
        yield Database(os.path.join(tmp, "test.db"))


@pytest.fixture
def wired_db(monkeypatch):
    """`db`, plus rebinding `main.db` so the HTTP handlers read it too."""
    import main
    from db import Database

    with tempfile.TemporaryDirectory() as tmp:
        fresh = Database(os.path.join(tmp, "test.db"))
        monkeypatch.setattr(main, "db", fresh)
        yield fresh


@pytest.fixture
def client(monkeypatch):
    """A TestClient over a throwaway database, yielded alongside it."""
    import main
    from db import Database
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as tmp:
        database = Database(os.path.join(tmp, "t.db"))
        monkeypatch.setattr(main, "db", database)
        yield TestClient(main.app), database
