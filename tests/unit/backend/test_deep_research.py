"""Deep research: criteria matching, contact floor, intel extraction."""
from deep_research import (
    CRITERIA_HIT_TARGET,
    MIN_CONTACTS,
    DeepResearchService,
    estimate_employee_count,
    extract_people_from_snippets,
    heuristic_deep_intel,
    parse_criteria,
    score_criteria_match,
    should_require_contact_floor,
)


class TestCriteriaParsingAndScoring:
    def test_parse_splits_roles_and_schools(self):
        terms = parse_criteria("VP Engineering, Penn alumni; Head of Talent")
        assert "VP Engineering" in terms
        assert "Penn alumni" in terms
        assert "Head of Talent" in terms

    def test_score_matches_role_phrase(self):
        contact = {
            "name": "Jane Doe",
            "role": "VP Engineering",
            "evidence": "Leads the platform org",
            "affinity": [],
        }
        match = score_criteria_match(contact, ["VP Engineering", "Penn alumni"])
        assert match["criteria_match"] is True
        assert "VP Engineering" in match["matched_terms"]
        assert match["match_score"] > 0

    def test_evidence_snippet_alone_is_not_a_criteria_match(self):
        contact = {
            "name": "Alex Kim",
            "role": "Recruiter",
            "evidence": 'Alex Kim - Recruiter - "VP Engineering" hiring at Acme',
            "affinity": ["criteria:VP Engineering"],
        }
        match = score_criteria_match(contact, ["VP Engineering"])
        assert match["criteria_match"] is False

    def test_affinity_tag_alone_is_not_a_criteria_match(self):
        contact = {
            "name": "Alex Kim",
            "role": "Intern",
            "affinity": ["criteria:VP Engineering"],
            "evidence": "Summer intern program",
        }
        match = score_criteria_match(contact, ["VP Engineering"])
        assert match["criteria_match"] is False
        assert match["found_via_criteria_search"] is True

    def test_vp_engineering_does_not_match_bare_engineer(self):
        contact = {
            "name": "Sam Patel",
            "role": "Software Engineer",
            "evidence": "Builds the payments API",
            "affinity": [],
        }
        match = score_criteria_match(contact, ["VP Engineering"])
        assert match["criteria_match"] is False

    def test_short_term_does_not_substring_match(self):
        contact = {
            "name": "Pat Lee",
            "role": "Frontend Engineer",
            "evidence": "Owns email templates and html emails",
            "affinity": [],
        }
        assert score_criteria_match(contact, ["AI"])["criteria_match"] is False
        assert score_criteria_match(contact, ["HR"])["criteria_match"] is False


class TestEmployeeEstimateAndFloor:
    def test_estimate_reads_team_size_copy(self):
        n = estimate_employee_count(
            ["We are a team of 42 employees building infra."])
        assert n == 42

    def test_missing_estimate_still_requires_floor(self):
        assert should_require_contact_floor(
            None, min_contacts=5, named_people=2) is True

    def test_explicit_tiny_company_skips_floor(self):
        assert should_require_contact_floor(
            3, min_contacts=5, named_people=2) is False

    def test_extract_people_requires_company_evidence(self):
        results = [{
            "title": "Jane Doe - VP Engineering - Acme AI | LinkedIn",
            "body": "Jane Doe works at Acme AI as VP Engineering.",
            "href": "https://www.linkedin.com/in/jane-doe-123",
        }]
        people = extract_people_from_snippets(
            results, "Acme AI", role_hint="VP Engineering")
        assert len(people) == 1
        assert people[0]["name"].lower().startswith("jane")
        assert "linkedin.com/in/jane-doe" in people[0]["linkedin_url"]
        assert people[0]["role"] == "VP Engineering"
        assert people[0].get("search_hint") == "VP Engineering"

    def test_search_hint_is_not_used_as_role(self):
        results = [{
            "title": "Jane Doe - Acme AI | LinkedIn",
            "body": "Jane Doe currently works at Acme AI.",
            "href": "https://www.linkedin.com/in/jane-doe-123",
        }]
        people = extract_people_from_snippets(
            results, "Acme AI", role_hint="VP Engineering")
        assert len(people) == 1
        assert people[0].get("role") in (None, "", "Acme AI")
        # Even if role accidentally became company name, criteria must not
        # match solely because of the hunt term.
        match = score_criteria_match(people[0], ["VP Engineering"])
        assert match["criteria_match"] is False

    def test_extract_people_rejects_wrong_company(self):
        results = [{
            "title": "Jane Doe - VP Engineering - OtherCo | LinkedIn",
            "body": "Jane Doe works at OtherCo.",
            "href": "https://www.linkedin.com/in/jane-doe-999",
        }]
        assert extract_people_from_snippets(results, "Acme AI") == []

    def test_extract_people_rejects_ex_employee_noise(self):
        results = [{
            "title": "Jane Doe - Advisor | LinkedIn",
            "body": "Former VP at Acme AI, now advising startups.",
            "href": "https://www.linkedin.com/in/jane-doe-ex",
        }]
        assert extract_people_from_snippets(results, "Acme AI") == []


class TestHeuristicIntel:
    def test_pulls_change_and_policy_sentences(self):
        site = (
            "Acme launched a new payments API in March. "
            "Acme remote-first culture policy supports flexible work. "
            "Acme improved checkout latency by 40 percent this quarter."
        )
        intel = heuristic_deep_intel(
            site, ["Acme announces Series B funding round"],
            company_name="Acme",
        )
        assert intel["key_changes"] or intel["recent_news"]
        assert intel["policy_highlights"] or intel["improvements"]


class TestContactFloorSelection:
    def test_select_prefers_criteria_then_fills_floor(self, monkeypatch):
        names = [
            ("Jane Doe", "jane.doe@acme.com", "jane-doe", "VP Engineering"),
            ("Alex Kim", "alex.kim@acme.com", "alex-kim", "VP Engineering"),
            ("Sam Patel", "sam.patel@acme.com", "sam-patel", "VP Engineering"),
            ("Riley Chen", "riley.chen@acme.com", "riley-chen", "VP Engineering"),
            ("Morgan Lee", "morgan.lee@acme.com", "morgan-lee", "Analyst"),
            ("Casey Brooks", "casey.brooks@acme.com", "casey-brooks", "Analyst"),
        ]
        scored = []
        for i, (name, email, slug, role) in enumerate(names):
            scored.append({
                "name": name,
                "role": role,
                "email": email,
                "linkedin_url": f"https://www.linkedin.com/in/{slug}",
                "email_kind": "personal",
                "email_verified": True,
                "email_person_match": True,
                "email_mx_ok": True,
                "on_domain": True,
                "linkedin_verified": True,
                "person_verified": True,
                "name_from_email": False,
                "seniority_rank": 5 if i < 4 else 18,
                "criteria_match": i < 4,
                "match_score": 1.0 if i < 4 else 0.0,
                "matched_terms": ["VP Engineering"] if i < 4 else [],
                "affinity": [],
            })

        import deep_research as dr
        monkeypatch.setattr(
            dr, "select_outreach_contacts",
            lambda contacts, emails, domain, limit=5, **_k: list(contacts)[:limit],
        )

        svc = DeepResearchService(db=None, enrichment=None)
        selected = svc._select_persistable(
            scored, [c["email"] for c in scored], "acme.com",
            min_contacts=MIN_CONTACTS,
            criteria_terms=["VP Engineering"],
        )
        assert len(selected) >= MIN_CONTACTS
        assert sum(1 for c in selected if c.get("criteria_match")) >= CRITERIA_HIT_TARGET

    def test_channel_rejects_mx_failed_email(self):
        svc = DeepResearchService(db=None, enrichment=None)
        email_ok, li_ok = svc._channel_ok({
            "name": "Jane Doe",
            "email": "jane.doe@acme.com",
            "email_kind": "personal",
            "email_person_match": True,
            "email_mx_ok": False,
            "on_domain": True,
            "linkedin_url": "",
        })
        assert email_ok is False
        assert li_ok is False

    def test_channel_requires_email_or_linkedin(self):
        svc = DeepResearchService(db=None, enrichment=None)
        email_ok, li_ok = svc._channel_ok({
            "name": "Jane Doe",
            "email": "jane.doe@acme.com",
            "email_kind": "personal",
            "email_person_match": True,
            "email_mx_ok": True,
            "on_domain": True,
            "linkedin_url": "",
        })
        assert email_ok and not li_ok
        email_ok, li_ok = svc._channel_ok({
            "name": "Jane Doe",
            "email": "",
            "linkedin_url": "https://www.linkedin.com/in/jane-doe",
            "linkedin_verified": True,
        })
        assert li_ok and not email_ok

    def test_merge_promotes_name_key_when_linkedin_arrives(self):
        svc = DeepResearchService(db=None, enrichment=None)
        merged = svc._merge_contacts([
            {"name": "Jane Doe", "role": "CEO", "email": "", "linkedin_url": ""},
            {
                "name": "Jane Doe",
                "role": "CEO",
                "email": "",
                "linkedin_url": "https://www.linkedin.com/in/jane-doe",
            },
        ])
        assert len(merged) == 1
        only = next(iter(merged.values()))
        assert "linkedin.com/in/jane-doe" in (only.get("linkedin_url") or "")


class TestDeepEnrichMode:
    def test_enrich_mode_deep_sets_flag(self, monkeypatch):
        from enrichment import EnrichmentService

        svc = EnrichmentService()
        monkeypatch.setattr(svc, "find_website", lambda _n: None)
        result = svc.enrich("Missing Co", mode="deep")
        assert result["enrich_mode"] == "deep"
        assert result["ok"] is False
