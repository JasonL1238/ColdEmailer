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


class TestDiscoveryUsesTheSharedStatusMapping:
    """Discovery kept its own copy of the mapping, and that copy had no
    identity_verified case. A company found on an unrelated website was filed
    as "Research failed" — which reads as transient, so the user re-runs
    research and gets the same wrong site — instead of "Wrong site found".
    """

    def _status(self, enriched):
        """The exact function the discovery loop calls."""
        from discovery import discovery_scrape_status
        return discovery_scrape_status(enriched)

    def test_wrong_site_is_not_reported_as_a_research_failure(self):
        enriched = {"url": "https://epson.com.pe", "domain": "epson.com.pe",
                    "identity_verified": False, "ok": False, "emails": []}
        assert self._status(enriched) == "wrong_site"

    def test_a_read_page_with_no_addresses_is_not_a_failure(self):
        enriched = {"url": "https://honeycomb.io", "identity_verified": True,
                    "ok": True, "emails": []}
        assert self._status(enriched) == "no_emails_found"

    def test_a_genuine_failure_is_still_a_failure(self):
        enriched = {"url": "https://acme.com", "identity_verified": True,
                    "ok": False, "emails": []}
        assert self._status(enriched) == "scrape_failed"

    def test_a_good_scrape_with_addresses_is_scraped(self):
        enriched = {"url": "https://acme.com", "identity_verified": True,
                    "ok": True, "emails": ["hi@acme.com"]}
        assert self._status(enriched) == "scraped"

    def test_the_discovery_loop_uses_that_function(self):
        """Guard against a second copy of the mapping reappearing inline."""
        import pathlib
        src = pathlib.Path(__file__).resolve().parents[3] / "backend" / "discovery.py"
        body = src.read_text()
        assert "status = discovery_scrape_status(enriched)" in body
        assert 'status = "no_website"' not in body


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

    def test_a_summary_naming_the_company_is_not_identity_proof(self, db):
        """The summary is LLM output written from a prompt that opens "Company
        name: {name}", so the model puts the name in its first sentence almost
        every time. Trusting it let through exactly the fabricated profiles this
        repair exists to quarantine — Cogwear's read "Cogwear is a music
        streaming service" while pointing at open.spotify.com. Re-research
        restores anything wrongly caught; a fabricated profile in a real cold
        email cannot be taken back.
        """
        c = db.create_company("Cogwear", domain="open.spotify.com",
                              url="https://open.spotify.com/",
                              summary="Cogwear is a music streaming service and "
                                      "platform that provides trending songs.",
                              industry="Entertainment/Music",
                              product="Music Streaming Service",
                              scrape_status="scraped")
        assert repair_mismatched_company_sites(db) == 1
        row = db.get_company(c["id"])
        assert row["scrape_status"] == "wrong_site"
        assert row["summary"] is None and row["product"] is None
        assert row["domain"] is None and row["url"] is None

    def test_clears_junk_domains_on_rows_that_never_finished_scraping(self, db):
        """A scrape that produced no summary leaves the row at 'pending' while
        still holding the junk domain the search picked (Figure AI ->
        ezeedomains.com) — and that domain then becomes the authority for
        "verify this address" warnings."""
        c = db.create_company("Figure AI", domain="ezeedomains.com",
                              url="https://ezeedomains.com/", scrape_status="pending")
        assert repair_mismatched_company_sites(db) == 1
        row = db.get_company(c["id"])
        assert row["domain"] is None and row["url"] is None

    def test_keeps_a_legacy_subdomain_that_belongs_to_the_company(self, db):
        """Older rows stored a full netloc, so the comparison has to run on the
        registered domain or tower.betterview.com reads as a wrong site."""
        c = db.create_company("BetterView", domain="tower.betterview.com",
                              url="https://tower.betterview.com/",
                              summary="BetterView maps property risk.",
                              scrape_status="scraped")
        assert repair_mismatched_company_sites(db) == 0
        assert db.get_company(c["id"])["domain"] == "tower.betterview.com"

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

    def test_an_unverified_company_domain_never_becomes_the_yardstick(self, db):
        """The warning cites the company's domain as authority. When that domain
        is some other company's, the advice inverts: it tells the user to
        distrust brett.adcock@figure.ai on the authority of ezeedomains.com."""
        company = db.create_company("Figure AI", domain="ezeedomains.com",
                                    scrape_status="pending")
        contact = db.create_contact(company_id=company["id"],
                                    email="brett.adcock@figure.ai")
        assert repair_offdomain_contact_warnings(db) == 0
        assert not db.get_contact(contact["id"])["notes"]

    def test_a_contact_who_already_replied_is_not_asked_to_verify(self, db):
        """"Verify this address really reaches them" is noise for someone who
        has already written back — they are proof the address works."""
        company = db.create_company("Fixie.ai", domain="fixie.ai")
        contact = db.create_contact(company_id=company["id"],
                                    email="hello@ultravox.ai", status="replied")
        assert repair_offdomain_contact_warnings(db) == 0
        assert not db.get_contact(contact["id"])["notes"]

    def test_a_contact_already_emailed_is_not_asked_to_verify(self, db):
        company = db.create_company("Fixie.ai", domain="fixie.ai")
        contact = db.create_contact(company_id=company["id"], email="hello@ultravox.ai")
        db.create_email(contact_id=contact["id"], status="sent")
        assert repair_offdomain_contact_warnings(db) == 0
        assert not db.get_contact(contact["id"])["notes"]

    def test_a_legacy_netloc_domain_does_not_flag_the_right_address(self, db):
        """registered_domain on one side only meant tower.betterview.com could
        never equal betterview.com, so the company's own address got flagged."""
        company = db.create_company("BetterView", domain="tower.betterview.com",
                                    scrape_status="scraped")
        contact = db.create_contact(company_id=company["id"],
                                    email="dtobias@betterview.com")
        assert repair_offdomain_contact_warnings(db) == 0
        assert not db.get_contact(contact["id"])["notes"]

    def test_clears_a_note_it_wrote_that_cites_a_junk_domain(self, db):
        """The inverted notes were already backfilled onto real contacts, so the
        repair has to take them back, not just stop adding new ones."""
        company = db.create_company("Cogwear", domain="open.spotify.com",
                                    summary="Cogwear is a music streaming service.",
                                    scrape_status="scraped")
        contact = db.create_contact(
            company_id=company["id"], email="david@cogweartech.com",
            status="replied",
            notes="Not on open.spotify.com. Verify this address really reaches "
                  "Cogwear before sending.")
        repair_mismatched_company_sites(db)
        repair_offdomain_contact_warnings(db)
        assert not db.get_contact(contact["id"])["notes"]

    def test_clearing_never_touches_a_note_someone_else_wrote(self, db):
        company = db.create_company("Cogwear", domain="open.spotify.com",
                                    scrape_status="pending")
        typed = db.create_contact(company_id=company["id"], email="david@cogweartech.com",
                                  notes="Met at a conference")
        discovered = db.create_contact(
            company_id=company["id"], email="press@cogweartech.com",
            notes="Found on the site but not on cogwear.com. Verify this reaches "
                  "the right company before sending.")
        repair_mismatched_company_sites(db)
        repair_offdomain_contact_warnings(db)
        assert db.get_contact(typed["id"])["notes"] == "Met at a conference"
        assert "Found on the site" in db.get_contact(discovered["id"])["notes"]

    def test_leaves_contacts_and_email_history_intact(self, db):
        c = db.create_company("Cogwear", domain="spotify.com",
                              summary="Spotify is a music streaming service.",
                              scrape_status="scraped")
        ct = db.create_contact(company_id=c["id"], email="a@cogwear.com")
        e = db.create_email(contact_id=ct["id"], status="sent", subject="Hi")

        repair_mismatched_company_sites(db)
        assert db.get_contact(ct["id"]) is not None
        assert db.get_email(e["id"])["status"] == "sent"
