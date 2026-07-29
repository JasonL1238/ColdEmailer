"""A search result is only the company's website if it actually belongs to
them. Accepting the first hit fabricated whole company profiles — the user's
real data had "Sabanto" filed as an Epson printer-ink page and "Claim" as
merriam-webster.com, and those descriptions flow verbatim into cold emails.
"""
import os
import tempfile

import pytest

from db import (Database, repair_mismatched_company_sites,
                repair_offdomain_contact_warnings)
from enrichment import (domain_matches_name, page_mentions_company,
                        scrape_status_for)


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        yield Database(os.path.join(tmp, "test.db"))


class TestDomainMatchesName:
    @pytest.mark.parametrize("name,domain", [
        ("Vellum AI", "vellum.ai"),
        ("Openlayer", "openlayer.com"),
        ("Fixie.ai", "fixie.ai"),
        ("Runway ML", "runwayml.com"),
        ("Dispatch Bio", "dispatchbio.com"),
        ("Acme Inc.", "acme.com"),
        ("Fort Robotics", "fortrobotics.com"),
    ])
    def test_accepts_matching_domains(self, name, domain):
        assert domain_matches_name(name, domain) is True

    @pytest.mark.parametrize("name,domain", [
        ("Sabanto", "epson.com.pe"),
        ("Claim", "merriam-webster.com"),
        ("Treeswift", "trip.com"),
        ("Cogwear", "spotify.com"),
        ("Fort Robotics", "dailythemedcrosswordanswers.com"),
    ])
    def test_rejects_unrelated_domains(self, name, domain):
        assert domain_matches_name(name, domain) is False

    def test_handles_missing_input(self):
        assert domain_matches_name("Acme", None) is False
        assert domain_matches_name("", "acme.com") is False

    def test_ignores_corporate_suffixes(self):
        """'Acme Technologies Inc' should still match acme.com."""
        assert domain_matches_name("Acme Technologies Inc", "acme.com") is True

    @pytest.mark.parametrize("name,domain", [
        ("Runaway ML", "runwayml.com"),   # real typo in the user's data
        ("Openlayr", "openlayer.com"),
    ])
    def test_tolerates_small_spelling_drift(self, name, domain):
        """A typo in a saved company name must not read as 'wrong site' and
        throw away correct research."""
        assert domain_matches_name(name, domain) is True

    def test_fuzzy_tolerance_does_not_swallow_real_mismatches(self):
        assert domain_matches_name("Sabanto", "epson.com.pe") is False
        assert domain_matches_name("Cogwear", "spotify.com") is False


class TestPageMentionsCompany:
    def test_accepts_a_page_that_names_the_company(self):
        text = "Welcome to Openlayer. We help teams test and monitor AI systems."
        assert page_mentions_company("Openlayer", text) is True

    def test_accepts_spacing_variants(self):
        assert page_mentions_company("Runway ML", "RunwayML builds creative tools") is True

    def test_rejects_a_page_about_someone_else(self):
        text = "Tintas Originales. Defiende tus impresoras contra las tintas falsificadas."
        assert page_mentions_company("Sabanto", text) is False

    def test_rejects_empty_text(self):
        assert page_mentions_company("Acme", "") is False
        assert page_mentions_company("Acme", None) is False


class TestScrapeStatusFor:
    def test_no_url_is_no_website(self):
        assert scrape_status_for({"url": None}) == "no_website"

    def test_unverified_identity_is_wrong_site(self):
        assert scrape_status_for(
            {"url": "https://epson.com.pe", "identity_verified": False,
             "ok": False}) == "wrong_site"

    def test_verified_with_summary_is_scraped(self):
        assert scrape_status_for(
            {"url": "https://acme.com", "identity_verified": True,
             "ok": True}) == "scraped"

    def test_verified_without_usable_content_is_failure(self):
        assert scrape_status_for(
            {"url": "https://acme.com", "identity_verified": True,
             "ok": False}) == "scrape_failed"

    def test_absent_flag_defaults_to_verified(self):
        """Older rows predate the flag; don't retroactively call them wrong."""
        assert scrape_status_for({"url": "https://acme.com", "ok": True}) == "scraped"


class TestRepairMismatchedCompanySites:
    """Rows scraped before identity checking existed still read as
    authoritative; their invented summaries must not reach an email."""

    def test_clears_a_profile_scraped_from_an_unrelated_site(self, db):
        c = db.create_company("Cogwear", domain="spotify.com",
                              summary="Spotify is a music streaming service.",
                              industry="Entertainment/Music",
                              scrape_status="scraped")
        assert repair_mismatched_company_sites(db) == 1
        row = db.get_company(c["id"])
        assert row["scrape_status"] == "wrong_site"
        assert row["summary"] is None and row["industry"] is None

    def test_keeps_a_correctly_matched_company(self, db):
        c = db.create_company("Openlayer", domain="openlayer.com",
                              summary="Openlayer helps teams test AI systems.",
                              scrape_status="scraped")
        assert repair_mismatched_company_sites(db) == 0
        assert db.get_company(c["id"])["summary"] is not None

    def test_keeps_a_company_whose_summary_names_it(self, db):
        """Rebrands and acquisitions legitimately sit on another domain."""
        c = db.create_company("Fixie.ai", domain="ultravox.ai",
                              summary="Fixie.ai builds voice agents.",
                              scrape_status="scraped")
        assert repair_mismatched_company_sites(db) == 0
        assert db.get_company(c["id"])["summary"] is not None

    def test_is_idempotent(self, db):
        db.create_company("Cogwear", domain="spotify.com",
                          summary="Spotify is a music streaming service.",
                          scrape_status="scraped")
        assert repair_mismatched_company_sites(db) == 1
        assert repair_mismatched_company_sites(db) == 0

    def test_offdomain_contacts_get_a_warning_note(self, db):
        """Discovery used to harvest any address on a page, so contacts exist
        that belong to another company. Warn before the user sends."""
        company = db.create_company("Fixie.ai", domain="fixie.ai")
        stranger = db.create_contact(company_id=company["id"], email="hello@ultravox.ai")
        own = db.create_contact(company_id=company["id"], email="team@fixie.ai")

        assert repair_offdomain_contact_warnings(db) == 1
        assert "fixie.ai" in db.get_contact(stranger["id"])["notes"]
        assert not db.get_contact(own["id"])["notes"]

    def test_offdomain_warning_is_idempotent_and_preserves_notes(self, db):
        company = db.create_company("Fixie.ai", domain="fixie.ai")
        kept = db.create_contact(company_id=company["id"], email="hello@ultravox.ai",
                                 notes="Met at a conference")
        assert repair_offdomain_contact_warnings(db) == 0
        assert db.get_contact(kept["id"])["notes"] == "Met at a conference"

    def test_subdomain_addresses_count_as_on_domain(self, db):
        company = db.create_company("Acme", domain="acme.com")
        db.create_contact(company_id=company["id"], email="jane@mail.acme.com")
        assert repair_offdomain_contact_warnings(db) == 0

    def test_wrong_site_companies_never_produce_offdomain_warnings(self, db):
        """The warning would be inverted: for a wrong_site row it is the saved
        domain that is wrong, so steven@treeswift.com would be flagged as "not
        on hk.trip.com" — telling the user to distrust the correct address."""
        company = db.create_company("Treeswift", domain="hk.trip.com",
                                    summary="Trip.com provides travel services.",
                                    scrape_status="scraped")
        contact = db.create_contact(company_id=company["id"],
                                    email="steven@treeswift.com")
        repair_mismatched_company_sites(db)
        assert repair_offdomain_contact_warnings(db) == 0
        assert not db.get_contact(contact["id"])["notes"]

    def test_wrong_site_repair_clears_the_bogus_url_and_domain(self, db):
        """A wrong company's URL is not a usable reference for anything."""
        company = db.create_company("Sabanto", domain="epson.com.pe",
                                    url="https://epson.com.pe/",
                                    summary="Tintas Originales para impresoras.",
                                    scrape_status="scraped")
        repair_mismatched_company_sites(db)
        row = db.get_company(company["id"])
        assert row["domain"] is None and row["url"] is None

    def test_leaves_contacts_and_email_history_intact(self, db):
        c = db.create_company("Cogwear", domain="spotify.com",
                              summary="Spotify is a music streaming service.",
                              scrape_status="scraped")
        ct = db.create_contact(company_id=c["id"], email="a@cogwear.com")
        e = db.create_email(contact_id=ct["id"], status="sent", subject="Hi")

        repair_mismatched_company_sites(db)
        assert db.get_contact(ct["id"]) is not None
        assert db.get_email(e["id"])["status"] == "sent"
