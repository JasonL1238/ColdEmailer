"""
Resume version management: upload PDFs, extract text, pick a default.
Files live under backend/data/resumes/; text + metadata live in SQLite.
"""
import os
import re
from typing import Dict, List, Optional

from db import Database

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
# Overridable for the same reason COLD_DB_PATH is. Without it the test suite
# was not the inert thing it is documented to be: every run against a fresh
# temp database re-ran the legacy resume import (its guard is a *setting*, and
# a temp DB has none) and wrote fresh copies of the user's real PDFs into their
# real backend/data/resumes.
RESUME_DIR = os.getenv("COLD_RESUME_DIR") or os.path.join(
    _BACKEND_DIR, "data", "resumes")

MAX_RESUME_BYTES = 10 * 1024 * 1024  # 10 MB


def _safe_filename(name: str) -> str:
    base = os.path.basename(name or "resume.pdf")
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    return base or "resume.pdf"


def extract_pdf_text(path: str) -> str:
    """Extract text from a PDF. Returns '' on failure — caller decides how to handle."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        parts = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
        text = "\n".join(parts).strip()
        # Collapse excessive whitespace from PDF extraction
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text
    except Exception as e:
        print(f"[resume] PDF text extraction failed for {path}: {e}")
        return ""


class ResumeService:
    def __init__(self, db: Database):
        self.db = db
        os.makedirs(RESUME_DIR, exist_ok=True)

    def save_upload(self, file_bytes: bytes, filename: str,
                    label: Optional[str] = None) -> Dict:
        if not file_bytes:
            raise ValueError("Empty file")
        if len(file_bytes) > MAX_RESUME_BYTES:
            raise ValueError("Resume file too large (max 10 MB)")
        if not file_bytes[:5].startswith(b"%PDF"):
            raise ValueError("Only PDF resumes are supported")
        # Magic bytes alone are trivially spoofed; make sure it really parses,
        # or we would happily attach a corrupt file to a real cold email.
        try:
            import io as _io
            from pypdf import PdfReader
            if len(PdfReader(_io.BytesIO(file_bytes)).pages) == 0:
                raise ValueError("empty")
        except Exception:
            raise ValueError("That file isn't a readable PDF. Try re-exporting it.")

        safe = _safe_filename(filename)
        # Avoid collisions: prefix with short uuid if the name already exists
        dest = os.path.join(RESUME_DIR, safe)
        if os.path.exists(dest):
            stem, ext = os.path.splitext(safe)
            from db import new_id
            dest = os.path.join(RESUME_DIR, f"{stem}-{new_id()[:8]}{ext}")

        with open(dest, "wb") as f:
            f.write(file_bytes)

        text = extract_pdf_text(dest)
        label = (label or "").strip() or os.path.splitext(safe)[0]
        is_default = len(self.db.list_resumes()) == 0
        row = self.db.create_resume(
            label=label,
            filename=os.path.basename(dest),
            path=dest,
            text_content=text,
            is_default=is_default,
        )
        self.db.log_event("resume", row["id"], "uploaded", label)
        return row

    @staticmethod
    def public(row: Optional[Dict]) -> Optional[Dict]:
        """API-safe view: no absolute filesystem path, no full resume text."""
        if not row:
            return None
        out = dict(row)
        text = out.pop("text_content", None) or ""
        out.pop("path", None)
        out["has_text"] = bool(text.strip())
        out["text_preview"] = text[:600]
        return out

    def list(self) -> List[Dict]:
        return [self.public(r) for r in self.db.list_resumes()]

    def get(self, resume_id: str) -> Optional[Dict]:
        return self.db.get_resume(resume_id)

    def get_text(self, resume_id: Optional[str]) -> str:
        """Resume text for prompt context: requested id, else default resume."""
        row = self.db.get_resume(resume_id) if resume_id else None
        if not row:
            row = self.db.get_default_resume()
        return (row or {}).get("text_content") or ""

    def resolve_attachment_path(self, resume_id: Optional[str]) -> Optional[str]:
        """Absolute path of the resume PDF to attach, or None."""
        row = self.db.get_resume(resume_id) if resume_id else None
        if not row:
            return None
        path = row.get("path")
        return path if path and os.path.isfile(path) else None

    def update(self, resume_id: str, label: Optional[str] = None,
               is_default: Optional[bool] = None) -> Optional[Dict]:
        row = self.db.get_resume(resume_id)
        if not row:
            return None
        updates = {}
        if label is not None and label.strip():
            updates["label"] = label.strip()
        if is_default:
            updates["is_default"] = True
        if updates:
            self.db.update_resume(resume_id, updates)
        return self.db.get_resume(resume_id)

    def delete(self, resume_id: str) -> bool:
        row = self.db.delete_resume(resume_id)
        if not row:
            return False
        path = row.get("path")
        # Only remove files we manage (inside RESUME_DIR)
        if path and os.path.isfile(path):
            try:
                if os.path.realpath(path).startswith(os.path.realpath(RESUME_DIR)):
                    os.remove(path)
            except OSError:
                pass
        # Keep exactly one default
        if row.get("is_default"):
            remaining = self.db.list_resumes()
            if remaining:
                self.db.update_resume(remaining[0]["id"], {"is_default": True})
        self.db.log_event("resume", resume_id, "deleted", row.get("label") or "")
        return True

    def migrate_legacy_resumes(self, project_root: str):
        """Import resume*.pdf files from the project root once."""
        if self.db.get_setting("resumes_migrated"):
            return
        imported = 0
        try:
            candidates = sorted(
                f for f in os.listdir(project_root)
                if f.lower().endswith(".pdf") and "resume" in f.lower()
            )
        except OSError:
            candidates = []
        for fname in candidates:
            src = os.path.join(project_root, fname)
            try:
                with open(src, "rb") as f:
                    data = f.read()
                self.save_upload(data, fname, label=os.path.splitext(fname)[0])
                imported += 1
            except Exception as e:
                print(f"[resume] legacy import failed for {fname}: {e}")
        self.db.set_setting("resumes_migrated", {"imported": imported})
