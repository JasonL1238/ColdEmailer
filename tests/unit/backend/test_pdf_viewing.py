"""PDF responses should preview inline while preserving explicit downloads."""
import asyncio
from unittest.mock import patch

import main


def test_resume_pdf_previews_inline():
    row = {"path": "/tmp/resume.pdf", "filename": "resume.pdf"}
    with patch.object(main.resumes, "get", return_value=row), \
            patch("main.os.path.isfile", return_value=True):
        response = asyncio.run(main.get_resume_file("r1", download=False))
    assert response.media_type == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline;")


def test_resume_pdf_explicit_download_is_an_attachment():
    row = {"path": "/tmp/resume.pdf", "filename": "resume.pdf"}
    with patch.object(main.resumes, "get", return_value=row), \
            patch("main.os.path.isfile", return_value=True):
        response = asyncio.run(main.get_resume_file("r1", download=True))
    assert response.headers["content-disposition"].startswith("attachment;")
