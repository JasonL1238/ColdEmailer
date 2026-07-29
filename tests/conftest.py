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
# Keep Gmail/OAuth paths pointed at nonexistent files so nothing can
# accidentally authenticate or send during a test run.
os.environ.setdefault('CREDENTIALS_JSON_PATH', os.path.join(_tmp_data, "no-credentials.json"))
os.environ.setdefault('TOKEN_JSON_PATH', os.path.join(_tmp_data, "no-token.json"))
