"""The model's guess at why a company matched a search is not research.

Discovery used to drop that one-liner into `companies.summary` whenever
scraping failed — the same field the composer quotes as established fact. An
email would then tell a real person what their own company does on the
strength of nothing but a search result.
"""

import pytest

from db import repair_speculative_company_summaries
from email_composer import EmailComposer
from resume_service import ResumeService


class TestDiscoveryNoteIsSeparateFromResearch:
    def test_the_column_exists_on_a_fresh_database(self, db):
        cols = {r["name"] for r in db.query("PRAGMA table_info(companies)")}
        assert "discovery_note" in cols

    def test_a_note_can_be_stored_without_touching_summary(self, db):
        c = db.create_company("Lightrun", discovery_note="Matches: debugging tools")
        row = db.get_company(c["id"])
        assert row["discovery_note"] == "Matches: debugging tools"
        assert row["summary"] is None

    def test_the_composer_never_quotes_the_note(self, db):
        db.update_profile({"full_name": "Ada Lovelace", "school": "Cambridge"})
        company = {"name": "Lightrun", "summary": None,
                   "discovery_note": "GUESSED DESCRIPTION FROM SEARCH"}
        comp = EmailComposer(db, ResumeService(db))

        prompt = comp._build_prompt({"name": "Jane", "company_name": "Lightrun"},
                                    company, "application", db.get_profile(), "", None)
        assert "GUESSED DESCRIPTION FROM SEARCH" not in prompt

        body = comp.compose({"name": "Jane", "company_name": "Lightrun"}, company,
                            use_template_only=True)["body"]
        assert "GUESSED DESCRIPTION FROM SEARCH" not in body


class TestRepairSpeculativeSummaries:
    def test_moves_a_summary_that_no_scrape_backs_up(self, db):
        c = db.create_company("Lightrun", scrape_status="scrape_failed",
                              summary="Provides real-time production debugging.")
        assert repair_speculative_company_summaries(db) == 1
        row = db.get_company(c["id"])
        assert row["summary"] is None
        assert row["discovery_note"] == "Provides real-time production debugging."

    @pytest.mark.parametrize("status", ["scraped", "no_emails_found"])
    def test_keeps_a_summary_a_successful_scrape_produced(self, db, status):
        """no_emails_found still means the page was read — only the addresses
        were missing, so that description is real evidence."""
        c = db.create_company("Honeycomb", scrape_status=status,
                              summary="Honeycomb provides an observability platform.")
        assert repair_speculative_company_summaries(db) == 0
        assert db.get_company(c["id"])["summary"] is not None

    @pytest.mark.parametrize("status", ["no_website", "wrong_site"])
    def test_also_covers_the_other_no_evidence_states(self, db, status):
        c = db.create_company("Ghost Co", scrape_status=status, summary="Some guess.")
        assert repair_speculative_company_summaries(db) == 1
        assert db.get_company(c["id"])["summary"] is None

    def test_is_idempotent(self, db):
        db.create_company("Lightrun", scrape_status="scrape_failed", summary="A guess.")
        assert repair_speculative_company_summaries(db) == 1
        assert repair_speculative_company_summaries(db) == 0

    def test_does_not_clobber_an_existing_note(self, db):
        c = db.create_company("Lightrun", scrape_status="scrape_failed",
                              summary="A guess.", discovery_note="original note")
        assert repair_speculative_company_summaries(db) == 0
        assert db.get_company(c["id"])["discovery_note"] == "original note"
