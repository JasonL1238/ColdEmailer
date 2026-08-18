"""Person finder: disambiguation scoring, staged results, email labeling."""
import json

import pytest

import person_finder as pf
from _person_fixtures import FakeEnrichment, _staged_job
from person_finder import (
    PersonFinderService,
    build_criteria,
    classify_email_domain,
    extract_person_candidates,
    merge_person_candidates,
    parse_year_ranges,
    score_candidate,
    years_overlap,
    _past_employer_mentioned,
)


@pytest.fixture
def mx_true(monkeypatch):
    """Answer every MX probe True without touching DNS.

    domain_has_mx was imported by name into three modules; patch each
    namespace or one path keeps hitting the real resolver.
    """
    fake = lambda domain, timeout=2.0: True
    monkeypatch.setattr("contact_verify.domain_has_mx", fake)
    monkeypatch.setattr("contact_enrich.domain_has_mx", fake)
    monkeypatch.setattr("person_finder.domain_has_mx", fake)


class TestYearParsing:
    def test_range_forms(self):
        assert parse_year_ranges("2018–2022") == [(2018, 2022)]
        assert parse_year_ranges("2018-22") == [(2018, 2022)]
        assert parse_year_ranges("2018 to 2022") == [(2018, 2022)]

    def test_bare_year_is_open_ended(self):
        assert parse_year_ranges("class of 2019") == [(2019, None)]

    def test_noise_is_not_a_year(self):
        assert parse_year_ranges("call 867-5309, id 123456") == []
        assert parse_year_ranges("") == []
        assert parse_year_ranges(None) == []

    def test_overlap_is_tri_state(self):
        assert years_overlap([], [(2018, 2022)]) is None
        assert years_overlap([(2018, 2022)], []) is None
        assert years_overlap([(2018, 2022)], [(2022, None)]) is True
        assert years_overlap([(2018, 2022)], [(2010, 2014)]) is False


class TestPastEmployer:
    def test_ex_prefix_counts_for_short_names(self):
        # "figma" is under the 6-char single-token bar in _company_mentioned;
        # the ex- shape is what has to catch it.
        assert _past_employer_mentioned(
            "Figma", "", "Jane Doe, ex-Figma, now building something new")

    def test_former_phrase_counts(self):
        assert _past_employer_mentioned(
            "Figma", "", "formerly a designer at Figma")

    def test_plain_mention_counts(self):
        assert _past_employer_mentioned(
            "Stripe", "", "Jane was at Stripe from 2016 to 2019")

    def test_unrelated_company_does_not(self):
        assert not _past_employer_mentioned(
            "Stripe", "", "Jane works at Google on payments")


class TestClassifyEmailDomain:
    def test_company_domain(self):
        assert classify_email_domain("jane@acme.com", "acme.com") == "company"

    def test_company_subdomain_is_company(self):
        assert classify_email_domain(
            "jane@mail.acme.com", "acme.com") == "company"

    def test_freemail_is_personal(self):
        assert classify_email_domain("jane@gmail.com", "acme.com") == "personal"
        assert classify_email_domain("jane@gmail.com", None) == "personal"

    def test_everything_else_is_other(self):
        assert classify_email_domain("jane@mit.edu", "acme.com") == "other"
        assert classify_email_domain("jane@other-co.com", "acme.com") == "other"


LINKEDIN_RESULT = {
    "title": "Jane Doe - VP Engineering - Acme | LinkedIn",
    "href": "https://www.linkedin.com/in/jane-doe",
    "body": ("Jane Doe. VP Engineering at Acme. "
             "Education: Northwestern University. 2018 - 2022"),
}


class TestExtractPersonCandidates:
    def test_matching_profile_becomes_candidate(self):
        out = extract_person_candidates([LINKEDIN_RESULT], "Jane Doe")
        assert len(out) == 1
        assert out[0]["linkedin_url"] == "https://www.linkedin.com/in/jane-doe"
        assert out[0]["role"] == "VP Engineering"

    def test_wrong_name_is_rejected(self):
        result = {
            "title": "John Smith - CFO - Acme | LinkedIn",
            "href": "https://www.linkedin.com/in/john-smith",
            "body": "John Smith. CFO at Acme.",
        }
        assert extract_person_candidates([result], "Jane Doe") == []

    def test_neighbor_card_education_does_not_bleed(self):
        result = {
            "title": "Jane Doe - Engineer - Acme | LinkedIn",
            "href": "https://www.linkedin.com/in/jane-doe",
            "body": ("Jane Doe - Engineer at Acme. "
                     "Ken Hirsch - Partner. Education: Kellogg School"),
        }
        out = extract_person_candidates([result], "Jane Doe")
        assert len(out) == 1
        snippets = " ".join(e["snippet"] for e in out[0]["evidence"])
        assert "Kellogg" not in snippets

    def test_non_linkedin_result_with_matching_head(self):
        result = {
            "title": "Jane Doe — Acme engineering blog",
            "href": "https://acme.com/blog/jane",
            "body": "A post by Jane Doe about payments infrastructure",
        }
        out = extract_person_candidates([result], "Jane Doe")
        assert len(out) == 1
        assert out[0]["linkedin_url"] == ""
        assert out[0]["source_url"] == "https://acme.com/blog/jane"


class TestMergeCandidates:
    def test_same_slug_merges_evidence(self):
        a = extract_person_candidates([LINKEDIN_RESULT], "Jane Doe")[0]
        b = dict(a, evidence=[{"source_url": "https://x.test", "snippet": "s"}])
        merged = merge_person_candidates([a, b])
        assert len(merged) == 1
        assert len(merged[0]["evidence"]) == 2

    def test_distinct_slugs_stay_distinct(self):
        a = extract_person_candidates([LINKEDIN_RESULT], "Jane Doe")[0]
        b = dict(a, linkedin_url="https://www.linkedin.com/in/jane-doe-2")
        assert len(merge_person_candidates([a, b])) == 2

    def test_lone_profile_absorbs_no_profile_evidence(self):
        profile = extract_person_candidates([LINKEDIN_RESULT], "Jane Doe")[0]
        loose = {
            "name": "Jane Doe", "role": None, "linkedin_url": "",
            "source_url": "https://acme.com/blog/jane",
            "evidence": [{"source_url": "https://acme.com/blog/jane",
                          "snippet": "by Jane Doe"}],
        }
        merged = merge_person_candidates([profile, loose])
        assert len(merged) == 1
        assert any(e["source_url"] == "https://acme.com/blog/jane"
                   for e in merged[0]["evidence"])

    def test_two_profiles_keep_no_profile_pool_separate(self):
        a = extract_person_candidates([LINKEDIN_RESULT], "Jane Doe")[0]
        b = dict(a, linkedin_url="https://www.linkedin.com/in/jane-doe-2")
        loose = {
            "name": "Jane Doe", "role": None, "linkedin_url": "",
            "source_url": "https://acme.com/blog/jane",
            "evidence": [{"source_url": "https://acme.com/blog/jane",
                          "snippet": "by Jane Doe"}],
        }
        # Attributing loose evidence to one of two same-named profiles would
        # be a guess — it must stay its own candidate.
        assert len(merge_person_candidates([a, b, loose])) == 3


class TestScoring:
    def _criteria(self, **over):
        query = {
            "company_name": "Acme", "school": "Northwestern",
            "school_dates": "2018-2022", "past_companies": "Stripe",
            "role_hint": "", "location": "", "company_domain": "acme.com",
        }
        query.update(over)
        return build_criteria(query)

    def _candidate(self, snippet):
        return {
            "name": "Jane Doe", "role": "VP Engineering",
            "linkedin_url": "https://www.linkedin.com/in/jane-doe",
            "source_url": "https://www.linkedin.com/in/jane-doe",
            "evidence": [{"source_url": "https://www.linkedin.com/in/jane-doe",
                          "snippet": snippet}],
        }

    def test_school_evidence_picks_the_right_same_named_person(self):
        criteria = self._criteria()
        right = self._candidate(
            "VP Engineering at Acme. Education: Northwestern University")
        stranger = self._candidate("VP Engineering at SomeOtherCo")
        assert (score_candidate(right, criteria)["score"]
                > score_candidate(stranger, criteria)["score"])

    def test_strong_needs_two_signal_families(self):
        criteria = self._criteria()
        both = score_candidate(self._candidate(
            "Engineer at Acme. Education: Northwestern University"), criteria)
        assert both["confidence"] == "strong"
        company_only = score_candidate(
            self._candidate("Engineer at Acme"), criteria)
        assert company_only["confidence"] == "possible"
        nothing = score_candidate(
            self._candidate("A person on the internet"), criteria)
        assert nothing["confidence"] == "weak"

    def test_every_signal_names_its_evidence(self):
        verdict = score_candidate(self._candidate(
            "Engineer at Acme. Education: Northwestern University. "
            "Previously at Stripe."), self._criteria())
        assert verdict["matched_signals"]
        for signal in verdict["matched_signals"]:
            assert signal["evidence"]
            assert signal["source_url"]

    def test_disjoint_years_conflict(self):
        verdict = score_candidate(
            self._candidate("Northwestern University, Class of 2010"),
            self._criteria())
        assert any(s["signal"] == "years"
                   for s in verdict["conflicting_signals"])

    def test_year_overlap_scores(self):
        verdict = score_candidate(
            self._candidate("Northwestern University, 2018 - 2022"),
            self._criteria())
        assert any(s["signal"] == "years" for s in verdict["matched_signals"])


def serp_router(mapping):
    """SERP fake keyed on substrings — first matching key wins."""
    def fake(query, max_results=8):
        for key, results in mapping.items():
            if key in query:
                return results
        return []
    return fake


class TestServiceRun:
    def _run_service(self, db, monkeypatch, *, pages=None, serp=None,
                     query=None):
        svc = PersonFinderService(
            db, FakeEnrichment(website="https://acme.com", pages=pages))
        monkeypatch.setattr(
            pf, "ddg_text_search", serp_router(serp or {}))
        q = {
            "name": "Jane Doe", "company_name": "Acme",
            "school": "Northwestern", "school_dates": "2018-2022",
            "past_companies": "", "role_hint": "", "location": "",
            "notes": "", "company_url": "",
        }
        q.update(query or {})
        job = db.create_job("person_finder", q)
        svc._run(job["id"], q)
        return db.get_job(job["id"])

    def test_keyless_run_stages_labeled_candidates(
            self, db, monkeypatch, mx_true):
        pages = {"https://acme.com/team":
                 "Jane Doe leads engineering. doe.jane@acme.com"}
        serp = {
            "site:linkedin.com/in": [LINKEDIN_RESULT],
            "site:acme.com": [{
                "title": "Team - Acme", "href": "https://acme.com/team",
                "body": "Jane Doe",
            }],
        }
        job = self._run_service(db, monkeypatch, pages=pages, serp=serp)
        assert job["status"] == "done"
        result = json.loads(job["result"])
        assert result["keyless"] is True
        assert result["company"]["domain"] == "acme.com"
        top = result["candidates"][0]
        assert top["confidence"] == "strong"
        assert top["llm_summary"] is None
        assert top["approved_contact_id"] is None
        by_origin = {e["origin"]: e for e in top["emails"]}
        found = by_origin["found"]
        assert found["email"] == "doe.jane@acme.com"
        assert found["email_verified"] is True
        assert found["domain_kind"] == "company"
        assert found["source_url"] == "https://acme.com/team"
        guessed = by_origin["guessed"]
        assert guessed["email"] == "jane.doe@acme.com"
        assert guessed["email_verified"] is False

    def test_guessed_never_carries_verified(self, db, mx_true):
        svc = PersonFinderService(db, FakeEnrichment())
        emails = svc._discover_emails(
            {"found_emails": []}, "Jane Doe", "acme.com")
        assert [e["origin"] for e in emails] == ["guessed"]
        assert emails[0]["email_verified"] is False

    def test_handle_style_personal_email_stays_unverified(
            self, db, mx_true):
        svc = PersonFinderService(db, FakeEnrichment())
        emails = svc._discover_emails(
            {"found_emails": [{"email": "coolhacker99@gmail.com",
                               "source_url": "https://github.com/ch99"}]},
            "Jane Doe", "acme.com")
        entry = next(e for e in emails if e["origin"] == "found")
        assert entry["domain_kind"] == "personal"
        assert entry["email_person_match"] is False
        assert entry["email_verified"] is False

    def test_name_matching_freemail_verifies(self, db, mx_true):
        # The behavior the approve path relies on: a name-matching personal
        # address is a first-class verified channel, no override needed.
        svc = PersonFinderService(db, FakeEnrichment())
        emails = svc._discover_emails(
            {"found_emails": [{"email": "jane.doe@gmail.com",
                               "source_url": "https://janedoe.dev"}]},
            "Jane Doe", "acme.com")
        entry = next(e for e in emails if e["origin"] == "found")
        assert entry["domain_kind"] == "personal"
        assert entry["email_verified"] is True

    def test_start_requires_first_and_last_name(self, db):
        svc = PersonFinderService(db, FakeEnrichment())
        with pytest.raises(ValueError):
            svc.start(name="Cher")

    def test_single_flight_slot(self, db):
        svc = PersonFinderService(db, FakeEnrichment())
        svc._running_job = "elsewhere"
        with pytest.raises(RuntimeError):
            svc.start(name="Jane Doe")

    def test_single_flight_db_row(self, db):
        db.create_job("person_finder", {"name": "Jane Doe"})
        svc = PersonFinderService(db, FakeEnrichment())
        with pytest.raises(RuntimeError):
            svc.start(name="Jane Doe")

    def test_cancelled_job_does_no_work(self, db, monkeypatch):
        def boom(*_a, **_k):
            raise AssertionError("cancelled run must not search")
        monkeypatch.setattr(pf, "ddg_text_search", boom)
        svc = PersonFinderService(db, FakeEnrichment())
        q = {"name": "Jane Doe", "company_name": "Acme"}
        job = db.create_job("person_finder", q)
        db.finish_job(job["id"], status="cancelled")
        svc._run(job["id"], q)
        assert db.get_job(job["id"])["status"] == "cancelled"
        assert db.list_contacts() == []

    def test_ssrf_blocked_url_is_never_fetched(self, db, monkeypatch, mx_true):
        enrichment = FakeEnrichment(
            website="https://acme.com",
            pages={"https://acme.com/team": "Jane Doe doe.jane@acme.com"})
        svc = PersonFinderService(db, enrichment)
        monkeypatch.setattr(
            pf, "is_safe_public_url",
            lambda url: url.startswith("https://acme.com"))
        monkeypatch.setattr(pf, "ddg_text_search", serp_router({
            "site:linkedin.com/in": [LINKEDIN_RESULT],
            "site:acme.com": [
                {"title": "t", "href": "http://127.0.0.1/steal",
                 "body": "Jane Doe"},
                {"title": "Team", "href": "https://acme.com/team",
                 "body": "Jane Doe"},
            ],
        }))
        q = {"name": "Jane Doe", "company_name": "Acme"}
        job = db.create_job("person_finder", q)
        svc._run(job["id"], q)
        assert enrichment.scraper.fetched == ["https://acme.com/team"]

    def test_llm_summary_attached_when_provider_present(
            self, db, monkeypatch, mx_true):
        monkeypatch.setattr(pf, "get_cloud_llm_provider", lambda: "openrouter")
        monkeypatch.setattr(
            pf, "complete_json",
            lambda *_a, **_k: {"c1": "Evidence fits the Acme Jane Doe."})
        serp = {"site:linkedin.com/in": [LINKEDIN_RESULT]}
        job = self._run_service(db, monkeypatch, serp=serp)
        result = json.loads(job["result"])
        assert result["keyless"] is False
        assert result["candidates"][0]["llm_summary"] == (
            "Evidence fits the Acme Jane Doe.")

class TestCorroborationRanking:
    """Measured on 30 real saved contacts: ranking by cross-query agreement
    moved top-1 identity accuracy 93.3% -> 96.7% and halved wrong-person-first.
    The failure it fixes: a Goldman MD losing to a same-named construction
    crew foreman because neither snippet contained the literal company token,
    so the tie broke arbitrarily."""

    def _cand(self, slug, found_by, score=0.0):
        return {"name": "Jane Doe",
                "linkedin_url": f"https://www.linkedin.com/in/{slug}",
                "found_by": found_by, "score": score}

    def test_more_queries_agreeing_ranks_higher(self):
        alone = self._cand("jane-doe-stranger", ["q1"])
        agreed = self._cand("jane-doe", ["q1", "q2", "q3"])
        assert pf.rank_score(agreed) > pf.rank_score(alone)

    def test_real_evidence_still_outranks_mere_agreement(self):
        # Corroboration must not overpower actual evidence about the person.
        evidenced = self._cand("jane-doe", ["q1"], score=5.0)
        agreed = self._cand("other-jane", ["q1", "q2"], score=0.0)
        assert pf.rank_score(evidenced) > pf.rank_score(agreed)

    def test_merge_unions_the_queries_that_found_a_profile(self):
        a = pf.extract_person_candidates([LINKEDIN_RESULT], "Jane Doe",
                                         source="q1")
        b = pf.extract_person_candidates([LINKEDIN_RESULT], "Jane Doe",
                                         source="q2")
        merged = pf.merge_person_candidates(a + b)
        assert merged[0]["found_by"] == ["q1", "q2"]

    def test_corroboration_does_not_inflate_confidence(self):
        # Retrieval agreement says the search was consistent, not that the
        # evidence about this person is stronger. A card must never read
        # "Strong match" on agreement alone.
        cand = {"name": "Jane Doe", "role": None, "source_url": "",
                "evidence": [], "found_by": ["q1", "q2", "q3", "q4"]}
        verdict = pf.score_candidate(cand, pf.build_criteria(
            {"company_name": "Acme"}))
        assert verdict["confidence"] == "weak"


@pytest.fixture
def fake_mx(monkeypatch):
    """Real MX records, frozen. Tests must never depend on live DNS.

    goldmansachs.com and gs.com genuinely answer the same tenant-specific
    pphosted host; cranium.ai/.eu are both on Outlook with different tenant
    prefixes; arize.com is Google while the campsite is OVH.
    """
    table = {
        "goldmansachs.com": {"mx0a-0014b501.pphosted.com"},
        "gs.com": {"mx0a-0014b501.pphosted.com"},
        "arize.com": {"alt1.aspmx.l.google.com", "aspmx.l.google.com"},
        "camping-arize.com": {"mx0.mail.ovh.net"},
        "cranium.ai": {"cranium-ai.mail.protection.outlook.com"},
        "cranium.eu": {"cranium-eu.mail.protection.outlook.com"},
        "shared-a.com": {"aspmx.l.google.com"},
        "shared-b.com": {"aspmx.l.google.com"},
    }
    monkeypatch.setattr(pf, "_mx_hosts", lambda d: table.get(d, set()))


class TestMailTenancy:
    def test_identical_tenant_specific_host_proves_one_estate(self, fake_mx):
        assert pf.shares_mail_tenancy("goldmansachs.com", "gs.com") is True

    def test_a_shared_commodity_provider_proves_nothing(self, fake_mx):
        # Millions of unrelated orgs answer aspmx.l.google.com.
        assert pf.shares_mail_tenancy("shared-a.com", "shared-b.com") is False

    def test_same_provider_different_tenant_is_not_shared(self, fake_mx):
        assert pf.shares_mail_tenancy("cranium.ai", "cranium.eu") is False

    def test_unrelated_providers_are_not_shared(self, fake_mx):
        assert pf.shares_mail_tenancy(
            "arize.com", "camping-arize.com") is False


class TestEmailDomainInference:
    """goldmansachs.com is the website; gs.com is where the mail lives. Every
    guessed address for the 26 Goldman contacts used the wrong domain."""

    def test_company_linked_evidence_plus_shared_tenancy_overrides(
            self, mx_true, fake_mx):
        counts = {"gs.com": 16, "goldmansachs.com": 8}
        got, why = pf.infer_email_domain(
            "Goldman Sachs", "goldmansachs.com", counts)
        assert got == "gs.com"
        assert "mail tenancy" in why

    def test_unrelated_lookalike_never_overrides(self, mx_true, fake_mx):
        # Arize AI's most-harvested domain is a French campsite.
        counts = {"camping-arize.com": 9, "arize.com": 0}
        got, _ = pf.infer_email_domain("Arize AI", "arize.com", counts)
        assert got == "arize.com"

    def test_shared_token_alone_is_not_enough(self, mx_true, fake_mx):
        # cranium.eu / cranium.me are different companies that share a word.
        counts = {"cranium.eu": 9, "cranium.me": 3, "cranium.ai": 1}
        got, _ = pf.infer_email_domain("Cranium", "cranium.ai", counts)
        assert got == "cranium.ai"

    def test_frequency_without_tenancy_proof_is_refused(self, mx_true,
                                                        monkeypatch):
        # The gate adversarial review demanded: text popularity alone must
        # never authorize building addresses on another domain.
        monkeypatch.setattr(pf, "_mx_hosts", lambda d: set())
        counts = {"gs.com": 99, "goldmansachs.com": 1}
        got, _ = pf.infer_email_domain(
            "Goldman Sachs", "goldmansachs.com", counts)
        assert got == "goldmansachs.com"

    def test_a_couple_of_sightings_never_overrides(self, mx_true, fake_mx):
        counts = {"gs.com": 2, "goldmansachs.com": 0}
        got, _ = pf.infer_email_domain(
            "Goldman Sachs", "goldmansachs.com", counts)
        assert got == "goldmansachs.com"

    def test_domain_without_mx_is_refused(self, monkeypatch, fake_mx):
        monkeypatch.setattr(pf, "domain_has_mx",
                            lambda d, timeout=2.0: d != "gs.com")
        counts = {"gs.com": 16, "goldmansachs.com": 2}
        got, _ = pf.infer_email_domain(
            "Goldman Sachs", "goldmansachs.com", counts)
        assert got == "goldmansachs.com"

    def test_aggregator_domains_are_never_harvested(self):
        results = [{"title": "Find emails",
                    "body": "contact a@rocketreach.co b@zoominfo.com "
                            "real.person@acme.com"}]
        counts = pf.harvest_email_domains(results)
        assert counts == {"acme.com": 1}


class TestFoundPathAndHarvestStaySeparate:
    """The two mechanisms are mutually destructive and must not share input.

    Measured: filtering the mail-domain harvest with the placeholder rule
    flips Goldman from gs.com 16 / goldmansachs.com 8 to goldmansachs.com 4 /
    gs.com 1, i.e. it destroys the one domain inference that works.
    """

    def test_templates_still_feed_the_domain_harvest(self, mx_true, fake_mx):
        results = [{"title": "Goldman Sachs email format",
                    "body": "john.smith@gs.com jane.doe@gs.com "
                            "janedoe@gs.com contact@goldmansachs.com"}]
        counts = pf.harvest_email_domains(results)
        assert counts.get("gs.com", 0) >= 3
        got, _ = pf.infer_email_domain(
            "Goldman Sachs", "goldmansachs.com",
            {"gs.com": 16, "goldmansachs.com": 8})
        assert got == "gs.com"

    def test_the_same_template_is_refused_on_the_found_path(self):
        assert pf.PersonFinderService._accept_found_address(
            "john.smith@gs.com", name="John Smith", attributed=False) is False

    def test_attributed_addresses_skip_the_name_check(self):
        # torvalds@linux-foundation.org is a correct answer a name rule kills.
        assert pf.PersonFinderService._accept_found_address(
            "torvalds@linux-foundation.org", name="Linus Torvalds",
            attributed=True) is True

    def test_unattributed_addresses_still_require_the_name(self):
        assert pf.PersonFinderService._accept_found_address(
            "someone.else@acme.com", name="Jane Doe",
            attributed=False) is False

    def test_generic_inboxes_never_pass(self):
        assert pf.PersonFinderService._accept_found_address(
            "careers@acme.com", name="Jane Doe", attributed=True) is False


class TestProvenanceThreading:
    def test_source_kind_and_attribution_survive(self, db, mx_true):
        svc = PersonFinderService(db, FakeEnrichment())
        emails = svc._discover_emails(
            {"found_emails": [{"email": "j.doe@acme.com",
                               "source_url": "https://github.com/x/y/commit/a",
                               "source_kind": "github_commit",
                               "attribution": "Commit abc in x/y"}]},
            "Jane Doe", None)
        entry = next(e for e in emails if e["origin"] == "found")
        assert entry["source_kind"] == "github_commit"
        assert entry["attribution"] == "Commit abc in x/y"

    def test_a_commit_address_outranks_serp_noise(self, db, mx_true):
        noise = [{"email": f"jane.doe{i}@acme.com", "source_url": "u",
                  "source_kind": "serp"} for i in range(6)]
        commit = {"email": "j.doe@realco.com", "source_url": "c",
                  "source_kind": "github_commit"}
        svc = PersonFinderService(db, FakeEnrichment())
        emails = svc._discover_emails(
            {"found_emails": noise + [commit]}, "Jane Doe", None)
        # Truncation at MAX_EMAILS_PER_CANDIDATE must not evict the best source.
        assert "j.doe@realco.com" in [e["email"] for e in emails]


class TestFoundSourcesStage:
    def _svc(self, db, http_get=None):
        return PersonFinderService(db, FakeEnrichment(), http_get=http_get)

    def test_a_broken_adapter_does_not_fail_the_run(self, db, monkeypatch,
                                                    mx_true):
        def boom(*_a, **_k):
            raise RuntimeError("github exploded")
        monkeypatch.setitem(pf.found_email.SOURCE_FUNCS, "github", boom)
        monkeypatch.setattr(pf.found_email, "route_sources",
                            lambda **_k: ["github"])
        svc = self._svc(db)
        cands = [{"id": "c1", "role": "Engineer", "channels": [],
                  "evidence": [], "found_emails": []}]
        stats = svc._run_found_sources(
            cands, name="Jane Doe", company_name="Acme",
            website_domain="acme.com", notes="",
            budget=pf.found_email.GetBudget(), should_stop=lambda: False)
        assert cands[0]["found_emails"] == []
        assert "routed" in stats

    def test_only_the_top_candidates_spend_budget(self, db, monkeypatch,
                                                  mx_true):
        seen = []
        monkeypatch.setattr(pf.found_email, "route_sources",
                            lambda **k: (seen.append(k), ["github"])[1])
        monkeypatch.setitem(pf.found_email.SOURCE_FUNCS, "github",
                            lambda *_a, **_k: [])
        cands = [{"id": f"c{i}", "role": "Engineer", "channels": [],
                  "evidence": [], "found_emails": []} for i in range(5)]
        self._svc(db)._run_found_sources(
            cands, name="Jane Doe", company_name="Acme",
            website_domain="acme.com", notes="",
            budget=pf.found_email.GetBudget(), should_stop=lambda: False)
        assert len(seen) == pf.FOUND_SOURCES_TOP_N


class TestApproval:
    @pytest.fixture
    def wired(self, db, monkeypatch, mx_true):
        import main
        monkeypatch.setattr(main, "db", db)
        monkeypatch.setattr(
            main, "person_finder", PersonFinderService(db, FakeEnrichment()))
        return main

    def _approve(self, main_mod, job_id, **kwargs):
        import asyncio
        from models import PersonApproveRequest
        payload = PersonApproveRequest(candidate_id="c1", **kwargs)
        return asyncio.run(
            main_mod.approve_person_candidate(job_id, payload))

    def test_happy_path_creates_company_and_contact(self, db, wired):
        job_id = _staged_job(db)
        out = self._approve(wired, job_id, email="jane.doe@acme.com")
        contact = out["contact"]
        assert out["already_existed"] is False
        assert contact["email"] == "jane.doe@acme.com"
        assert contact["source"] == "person_finder"
        assert contact["email_verified"] == 1
        company = db.get_company(contact["company_id"])
        assert company["name"] == "Acme"
        notes = contact["notes"] or ""
        assert "Possible email pattern jdoe@acme.com" in notes
        assert "Found via person search" in notes
        assert "Github: https://github.com/janedoe" in notes

    def test_approval_marks_the_staged_candidate(self, db, wired):
        job_id = _staged_job(db)
        out = self._approve(wired, job_id, email="jane.doe@acme.com")
        stored = json.loads(db.get_job(job_id)["result"])
        assert (stored["candidates"][0]["approved_contact_id"]
                == out["contact"]["id"])

    def test_reapprove_is_idempotent(self, db, wired):
        job_id = _staged_job(db)
        first = self._approve(wired, job_id, email="jane.doe@acme.com")
        second = self._approve(wired, job_id, email="jane.doe@acme.com")
        assert second["already_existed"] is True
        assert second["contact"]["id"] == first["contact"]["id"]

    def test_guessed_email_is_refused_as_sendable(self, db, wired):
        import main
        job_id = _staged_job(db)
        with pytest.raises(main.HTTPException) as caught:
            self._approve(wired, job_id, email="jdoe@acme.com")
        assert caught.value.status_code == 400
        assert "guess" in str(caught.value.detail).lower()

    def test_unknown_address_is_refused(self, db, wired):
        import main
        job_id = _staged_job(db)
        with pytest.raises(main.HTTPException) as caught:
            self._approve(wired, job_id, email="stranger@acme.com")
        assert caught.value.status_code == 400

    def test_generic_inbox_is_refused(self, db, wired):
        import main
        job_id = _staged_job(db, emails=[{
            "email": "careers@acme.com", "origin": "found",
            "domain_kind": "company", "source_url": "https://acme.com",
            "email_kind": "generic", "email_person_match": False,
            "email_mx_ok": True, "email_verified": False,
        }])
        with pytest.raises(main.HTTPException) as caught:
            self._approve(wired, job_id, email="careers@acme.com")
        assert caught.value.status_code == 400

    def test_suppressed_address_is_refused(self, db, wired):
        import main
        db.add_suppression("jane.doe@acme.com", "address")
        job_id = _staged_job(db)
        with pytest.raises(main.HTTPException) as caught:
            self._approve(wired, job_id, email="jane.doe@acme.com")
        assert caught.value.status_code == 400

    def test_handle_email_needs_explicit_confirmation(self, db, wired):
        import main
        job_id = _staged_job(db, emails=[{
            "email": "coolhacker99@gmail.com", "origin": "found",
            "domain_kind": "personal",
            "source_url": "https://github.com/janedoe",
            "email_kind": "named_unmatched", "email_person_match": False,
            "email_mx_ok": True, "email_verified": False,
        }])
        with pytest.raises(main.HTTPException) as caught:
            self._approve(wired, job_id, email="coolhacker99@gmail.com")
        assert caught.value.status_code == 400
        assert "confirm" in str(caught.value.detail).lower()

    def test_confirmed_handle_email_saves_honestly(self, db, wired):
        job_id = _staged_job(db, emails=[{
            "email": "coolhacker99@gmail.com", "origin": "found",
            "domain_kind": "personal",
            "source_url": "https://github.com/janedoe",
            "email_kind": "named_unmatched", "email_person_match": False,
            "email_mx_ok": True, "email_verified": False,
        }])
        out = self._approve(wired, job_id, email="coolhacker99@gmail.com",
                            confirm_email_ownership=True)
        contact = out["contact"]
        assert contact["email"] == "coolhacker99@gmail.com"
        assert contact["email_kind"] == "named_unmatched"
        assert contact["email_verified"] == 0
        assert "You confirmed this address" in (contact["notes"] or "")
        events = db.query(
            "SELECT * FROM events WHERE entity_id=? AND event=?",
            (contact["id"], "email_user_confirmed"))
        assert events

    def test_name_matching_freemail_needs_no_confirmation(self, db, wired):
        # The traced ingest behavior pinned end to end: jane.doe@gmail.com is
        # a first-class verified channel, no override involved.
        job_id = _staged_job(db, emails=[{
            "email": "jane.doe@gmail.com", "origin": "found",
            "domain_kind": "personal", "source_url": "https://janedoe.dev",
            "email_kind": "personal", "email_person_match": True,
            "email_mx_ok": True, "email_verified": True,
        }])
        out = self._approve(wired, job_id, email="jane.doe@gmail.com")
        contact = out["contact"]
        assert contact["email"] == "jane.doe@gmail.com"
        assert contact["email_verified"] == 1

    def test_linkedin_only_approval(self, db, wired):
        job_id = _staged_job(db, emails=[])
        out = self._approve(wired, job_id)
        contact = out["contact"]
        assert contact["email"] == ""
        assert contact["linkedin_url"] == "https://www.linkedin.com/in/jane-doe"
        assert contact["linkedin_verified"] == 1

    def test_nothing_usable_is_refused(self, db, wired):
        import main
        job_id = _staged_job(db, emails=[])
        with pytest.raises(main.HTTPException) as caught:
            self._approve(wired, job_id, include_linkedin=False)
        assert caught.value.status_code == 400

    def test_owned_elsewhere_is_a_conflict(self, db, wired):
        import main
        other = db.create_company("OtherCo")
        db.create_contact(company_id=other["id"], name="Jane Doe",
                          email="jane.doe@acme.com")
        job_id = _staged_job(db)
        with pytest.raises(main.HTTPException) as caught:
            self._approve(wired, job_id, email="jane.doe@acme.com")
        assert caught.value.status_code == 409
        assert "OtherCo" in str(caught.value.detail)

    def test_same_company_duplicate_returns_existing(self, db, wired):
        company = db.create_company("Acme", domain="acme.com")
        existing = db.create_contact(
            company_id=company["id"], name="Jane Doe",
            email="jane.doe@acme.com")
        job_id = _staged_job(db)
        out = self._approve(wired, job_id, email="jane.doe@acme.com")
        assert out["already_existed"] is True
        assert out["contact"]["id"] == existing["id"]

    def test_running_job_cannot_be_approved(self, db, wired):
        import main
        job = db.create_job("person_finder", {"name": "Jane Doe"})
        with pytest.raises(main.HTTPException) as caught:
            self._approve(wired, job["id"])
        assert caught.value.status_code == 409

    def test_route_obeys_the_boundary_verdict(self, db, wired, monkeypatch):
        # Mutation guard: if the route ever bypasses verified_channels and
        # writes the raw candidate email, this fails.
        import main
        monkeypatch.setattr(main, "verified_channels", lambda _c: ("", ""))
        job_id = _staged_job(db, linkedin="")
        with pytest.raises(main.HTTPException) as caught:
            self._approve(wired, job_id, email="jane.doe@acme.com")
        assert caught.value.status_code == 400

    def test_search_budget_is_bounded(self, db, monkeypatch, mx_true):
        calls = {"n": 0}

        def counting(query, max_results=8):
            calls["n"] += 1
            return []
        monkeypatch.setattr(pf, "ddg_text_search", counting)
        svc = PersonFinderService(db, FakeEnrichment(website="https://acme.com"))
        q = {"name": "Jane Doe", "company_name": "Acme",
             "school": "Northwestern", "school_dates": "2018-2022",
             "past_companies": "Stripe, Google, Airbnb",
             "role_hint": "", "location": "", "notes": "", "company_url": ""}
        job = db.create_job("person_finder", q)
        svc._run(job["id"], q)
        assert 0 < calls["n"] <= pf.MAX_PERSON_SEARCHES


class TestSmtpConfirmedGuessApproval:
    """A guess may become savable, but only on two independent facts: a mail
    server confirmed the mailbox, and a human took responsibility for identity.
    Neither alone is enough, and the row is never marked verified."""

    @pytest.fixture
    def wired(self, db, monkeypatch, mx_true):
        import main
        monkeypatch.setattr(main, "db", db)
        monkeypatch.setattr(
            main, "person_finder", PersonFinderService(db, FakeEnrichment()))
        return main

    def _guess(self, smtp_status):
        return [{"email": "jane.doe@acme.com", "origin": "guessed",
                 "domain_kind": "company", "source_url": None,
                 "email_kind": "pattern_guess", "email_person_match": True,
                 "email_mx_ok": True, "email_verified": False,
                 "smtp_status": smtp_status}]

    def _approve(self, main_mod, job_id, **kw):
        import asyncio
        from models import PersonApproveRequest
        return asyncio.run(main_mod.approve_person_candidate(
            job_id, PersonApproveRequest(candidate_id="c1", **kw)))

    def test_an_unverified_guess_is_still_refused(self, db, wired):
        import main
        job_id = _staged_job(db, emails=self._guess(None))
        with pytest.raises(main.HTTPException) as caught:
            self._approve(wired, job_id, email="jane.doe@acme.com",
                          confirm_email_ownership=True)
        assert caught.value.status_code == 400

    def test_an_inconclusive_check_is_still_refused(self, db, wired):
        import main
        job_id = _staged_job(db, emails=self._guess("accept_all"))
        with pytest.raises(main.HTTPException) as caught:
            self._approve(wired, job_id, email="jane.doe@acme.com",
                          confirm_email_ownership=True)
        assert caught.value.status_code == 400

    def test_confirmed_mailbox_without_ownership_is_refused(self, db, wired):
        import main
        job_id = _staged_job(db, emails=self._guess("deliverable"))
        with pytest.raises(main.HTTPException) as caught:
            self._approve(wired, job_id, email="jane.doe@acme.com")
        assert caught.value.status_code == 400
        assert "confirm_email_ownership" in str(caught.value.detail)

    def test_confirmed_plus_ownership_saves_but_never_verified(self, db, wired):
        job_id = _staged_job(db, emails=self._guess("deliverable"))
        out = self._approve(wired, job_id, email="jane.doe@acme.com",
                            confirm_email_ownership=True)
        contact = out["contact"]
        assert contact["email"] == "jane.doe@acme.com"
        # The load-bearing assertion: an inference never becomes a verification.
        assert contact["email_verified"] == 0
        assert contact["person_verified"] == 0
        assert "does not prove it belongs to" in (contact["notes"] or "")
        events = {r["event"] for r in db.query(
            "SELECT event FROM events WHERE entity_id=?", (contact["id"],))}
        assert {"email_user_confirmed", "mailbox_confirmed"} <= events


class TestCorroboratedGuessApproval:
    """The second way a guess earns its way out of "note only".

    Outbound port 25 is blocked on most home networks, so the mailbox probe
    usually cannot run at all. A public corpus showing the address in use under
    this person's name is the alternative — and it is the stronger evidence of
    the two, because a mail server can only say a mailbox exists while a signed
    commit says whose it is. It still proves nothing about ownership *today*,
    so it lands on the same human assertion and the same email_verified=0.
    """

    @pytest.fixture
    def wired(self, db, monkeypatch, mx_true):
        import main
        monkeypatch.setattr(main, "db", db)
        monkeypatch.setattr(
            main, "person_finder", PersonFinderService(db, FakeEnrichment()))
        return main

    def _guess(self, corroboration):
        return [{"email": "jane.doe@acme.com", "origin": "guessed",
                 "domain_kind": "company", "source_url": None,
                 "email_kind": "pattern_guess", "email_person_match": True,
                 "email_mx_ok": True, "email_verified": False,
                 "smtp_status": None, "corroboration": corroboration}]

    def _approve(self, main_mod, job_id, **kw):
        import asyncio
        from models import PersonApproveRequest
        return asyncio.run(main_mod.approve_person_candidate(
            job_id, PersonApproveRequest(candidate_id="c1", **kw)))

    def test_corroborated_under_this_name_saves_but_never_verified(
            self, db, wired):
        job_id = _staged_job(db, emails=self._guess({
            "found": True, "sources": ["github_commit"],
            "names": ["Jane Doe"], "name_match": True,
            "evidence_url": "https://github.com/o/r/commit/abc"}))
        out = self._approve(wired, job_id, email="jane.doe@acme.com",
                            confirm_email_ownership=True)
        contact = out["contact"]
        assert contact["email"] == "jane.doe@acme.com"
        assert contact["email_verified"] == 0
        assert contact["person_verified"] == 0
        # The note must describe the evidence that actually existed, not the
        # mailbox probe that never ran.
        assert "github_commit" in (contact["notes"] or "")
        events = {r["event"] for r in db.query(
            "SELECT event FROM events WHERE entity_id=?", (contact["id"],))}
        assert {"email_user_confirmed", "address_corroborated"} <= events
        assert "mailbox_confirmed" not in events

    def test_anonymous_corroboration_is_refused(self, db, wired):
        """A hit with no name attached shows the address is real but not whose.

        lore.kernel.org is the measured case: only 8 of 199 matching entries
        carried the address in a From header.
        """
        import main
        job_id = _staged_job(db, emails=self._guess({
            "found": True, "sources": ["mailing_list"], "names": [],
            "name_match": None}))
        with pytest.raises(main.HTTPException) as caught:
            self._approve(wired, job_id, email="jane.doe@acme.com",
                          confirm_email_ownership=True)
        assert caught.value.status_code == 400

    def test_a_namesakes_real_address_is_refused(self, db, wired):
        """The address is genuinely real and belongs to someone else."""
        import main
        job_id = _staged_job(db, emails=self._guess({
            "found": True, "sources": ["github_commit"],
            "names": ["Bob Stone"], "name_match": False}))
        with pytest.raises(main.HTTPException) as caught:
            self._approve(wired, job_id, email="jane.doe@acme.com",
                          confirm_email_ownership=True)
        assert caught.value.status_code == 400

    def test_corroborated_without_ownership_is_refused(self, db, wired):
        import main
        job_id = _staged_job(db, emails=self._guess({
            "found": True, "sources": ["github_commit"],
            "names": ["Jane Doe"], "name_match": True}))
        with pytest.raises(main.HTTPException) as caught:
            self._approve(wired, job_id, email="jane.doe@acme.com")
        assert caught.value.status_code == 400
        assert "confirm_email_ownership" in str(caught.value.detail)
        assert "public source" in str(caught.value.detail)
