"""Deep research: criteria matching, alumni hunting, contact floor."""
import time

from deep_research import (
    CRITERIA_HIT_TARGET,
    HARD_STOP_SEC,
    MIN_CONTACTS,
    UNTIL_MAX_ROUNDS,
    DeepResearchService,
    alumni_mode,
    estimate_employee_count,
    extract_people_from_snippets,
    heuristic_deep_intel,
    is_alumni_term,
    parse_criteria,
    school_aliases_for_term,
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

    def test_affinity_tag_alone_is_not_a_criteria_match(self):
        contact = {
            "name": "Alex Kim",
            "role": "Intern",
            "affinity": ["criteria:VP Engineering"],
            "evidence": "Summer intern program",
        }
        match = score_criteria_match(contact, ["VP Engineering"])
        assert match["criteria_match"] is False

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

    def test_evidence_snippet_alone_is_not_a_role_match(self):
        contact = {
            "name": "Alex Kim",
            "role": "Recruiter",
            "evidence": 'Alex Kim - Recruiter - "VP Engineering" hiring at Acme',
            "affinity": ["criteria:VP Engineering"],
        }
        match = score_criteria_match(contact, ["VP Engineering"])
        assert match["criteria_match"] is False


class TestAlumniCriteria:
    def test_northwestern_alum_is_alumni_term(self):
        assert is_alumni_term("Northwestern Alum")
        assert alumni_mode(["Northwestern Alum"]) is True
        assert alumni_mode(["VP Engineering"]) is False

    def test_school_aliases_expand_northwestern(self):
        aliases = school_aliases_for_term("Northwestern Alum")
        joined = " ".join(aliases).lower()
        assert "northwestern university" in joined
        assert "kellogg" in joined

    def test_alumni_match_uses_education_evidence(self):
        contact = {
            "name": "Benjamin Forbes",
            "role": "Associate",
            "evidence": (
                "Benjamin Forbes - Northwestern University - Evanston | LinkedIn. "
                "Associate at Goldman Sachs. New York, NY."
            ),
            "affinity": [],
        }
        match = score_criteria_match(contact, ["Northwestern Alum"])
        assert match["criteria_match"] is True
        assert match["alumni_match"] is True

    def test_alumni_does_not_require_literal_alum_word_in_role(self):
        # This was the bug: role="Northwestern University" failed because
        # scoring required both "northwestern" AND "alum" tokens.
        contact = {
            "name": "Jane Doe",
            "role": "Northwestern University",
            "evidence": "Analyst at Goldman Sachs",
            "affinity": [],
        }
        match = score_criteria_match(contact, ["Northwestern Alum"])
        assert match["criteria_match"] is True

    def test_ucla_person_is_not_northwestern_alum(self):
        contact = {
            "name": "Annika Maria Tonn",
            "role": "Goldman Sachs",
            "evidence": (
                "Experience: Goldman Sachs · Education: University of California, "
                "Los Angeles · Location: New York"
            ),
            "affinity": [],
        }
        match = score_criteria_match(contact, ["Northwestern Alum"])
        assert match["criteria_match"] is False

    def test_northwestern_mutual_is_not_northwestern_alum(self):
        contact = {
            "name": "Pat Lee",
            "role": "Advisor",
            "evidence": "Advisor at Northwestern Mutual. New York.",
            "affinity": [],
        }
        match = score_criteria_match(contact, ["Northwestern Alum"])
        assert match["criteria_match"] is False
        assert match["alumni_match"] is False

    def test_truncated_title_plus_mutual_not_alum(self):
        # Truncated LinkedIn title leaves bare "Northwestern"; Mutual is in
        # Experience; Education is a different school — must not match.
        contact = {
            "name": "Pat Lee",
            "role": "Advisor",
            "evidence": (
                "Pat Lee - Northwestern | Experience: Advisor at "
                "Northwestern Mutual. Education: University of Wisconsin"
            ),
            "affinity": [],
        }
        match = score_criteria_match(contact, ["Northwestern Alum"])
        assert match["alumni_match"] is False

    def test_education_northwestern_still_matches(self):
        contact = {
            "name": "Alex Kim",
            "role": "Analyst",
            "evidence": (
                "Alex Kim - Analyst at Goldman Sachs. "
                "Education: Northwestern University"
            ),
            "affinity": [],
        }
        match = score_criteria_match(contact, ["Northwestern Alum"])
        assert match["alumni_match"] is True

    def test_surname_johnson_is_not_cornell_alum(self):
        contact = {
            "name": "Pat Johnson",
            "role": "Associate",
            "evidence": "Pat Johnson, MBA - Associate at Goldman Sachs. New York.",
            "affinity": [],
        }
        match = score_criteria_match(contact, ["Cornell Alum"])
        assert match["alumni_match"] is False

    def test_surname_johnson_mba_no_comma_not_cornell(self):
        contact = {
            "name": "Pat Johnson",
            "role": "Associate",
            "evidence": "Pat Johnson MBA - Associate at Goldman Sachs.",
            "affinity": [],
        }
        match = score_criteria_match(contact, ["Cornell Alum"])
        assert match["alumni_match"] is False

    def test_later_johnson_graduate_school_still_matches(self):
        contact = {
            "name": "Pat Johnson",
            "role": "Associate",
            "evidence": (
                "Pat Johnson - Associate at GS. "
                "Education: Johnson Graduate School of Management"
            ),
            "affinity": [],
        }
        match = score_criteria_match(contact, ["Cornell Alum"])
        assert match["alumni_match"] is True

    def test_education_kellogg_short_form(self):
        contact = {
            "name": "Sam Lee",
            "role": "Analyst",
            "evidence": "Analyst at Goldman Sachs. Education: Kellogg",
            "affinity": [],
        }
        match = score_criteria_match(contact, ["Northwestern Alum"])
        assert match["alumni_match"] is True

    def test_kellogg_dash_school_principal_not_alum(self):
        contact = {
            "name": "Chris Kellogg",
            "role": "Principal",
            "evidence": "Chris Kellogg - School Principal at Lincoln High",
            "affinity": [],
        }
        match = score_criteria_match(contact, ["Northwestern Alum"])
        assert match["alumni_match"] is False

    def test_title_only_midcard_kellogg_body_ignored(self):
        from deep_research import person_scoped_evidence, score_criteria_match
        title = "Ken Hirsch - Partner - Goldman Sachs | LinkedIn"
        body = (
            "MBA Candidate at Kellogg School of Management · "
            "Experience: Northwestern Mutual"
        )
        evidence = person_scoped_evidence("Ken Hirsch", title, body)
        assert "kellogg" not in evidence.lower()
        match = score_criteria_match(
            {"name": "Ken Hirsch", "role": "Partner", "evidence": evidence,
             "affinity": []},
            ["Northwestern Alum"],
        )
        assert match["alumni_match"] is False

    def test_kellogg_school_is_northwestern_alum(self):
        contact = {
            "name": "Sam Lee",
            "role": "VP",
            "evidence": "MBA, Kellogg School of Management. Goldman Sachs.",
            "affinity": [],
        }
        match = score_criteria_match(contact, ["Northwestern Alum"])
        assert match["alumni_match"] is True

    def test_kellogg_mba_bound_is_northwestern_alum(self):
        contact = {
            "name": "Dylan Speirs",
            "role": "Senior Analyst",
            "evidence": "Senior Analyst at Goldman Sachs | Kellogg MBA Deferred Admit",
            "affinity": [],
        }
        match = score_criteria_match(contact, ["Northwestern Alum"])
        assert match["alumni_match"] is True

    def test_serp_neighbor_kellogg_does_not_bleed(self):
        from deep_research import person_scoped_evidence, score_criteria_match
        title = "Ken Hirsch - Partner - Goldman Sachs | LinkedIn"
        body = (
            "Allison Sheehan - MBA Candidate at Kellogg School of Management. "
            "Ken Hirsch - Partner - Goldman Sachs (Co-Chairman, Global TMT). "
            "Vidhi Bhatnagar - Vice President"
        )
        evidence = person_scoped_evidence("Ken Hirsch", title, body)
        assert "kellogg" not in evidence.lower()
        match = score_criteria_match(
            {"name": "Ken Hirsch", "role": "Partner", "evidence": evidence,
             "affinity": []},
            ["Northwestern Alum"],
        )
        assert match["alumni_match"] is False

    def test_serp_title_only_neighbor_body_does_not_bleed(self):
        from deep_research import person_scoped_evidence, score_criteria_match
        title = "Ken Hirsch - Partner - Goldman Sachs | LinkedIn"
        body = (
            "Allison Sheehan - MBA Candidate at Kellogg School of Management. "
            "Vidhi Bhatnagar - Vice President"
        )
        evidence = person_scoped_evidence("Ken Hirsch", title, body)
        assert "kellogg" not in evidence.lower()
        match = score_criteria_match(
            {"name": "Ken Hirsch", "role": "Partner", "evidence": evidence,
             "affinity": []},
            ["Northwestern Alum"],
        )
        assert match["alumni_match"] is False

    def test_serp_neighbor_glued_into_title_does_not_bleed(self):
        from deep_research import person_scoped_evidence, score_criteria_match
        title = (
            "Ken Hirsch - Partner - Goldman Sachs (Co-Chairman ... - LinkedIn. "
            "Robert Wang - MBA Candidate at Kellogg School of Management"
        )
        evidence = person_scoped_evidence("Ken Hirsch", title, "")
        assert "kellogg" not in evidence.lower()
        assert "robert wang" not in evidence.lower()
        match = score_criteria_match(
            {"name": "Ken Hirsch", "role": "Partner", "evidence": evidence,
             "affinity": []},
            ["Northwestern Alum"],
        )
        assert match["alumni_match"] is False

    def test_serp_ellipsis_neighbor_kellogg_does_not_bleed(self):
        from deep_research import person_scoped_evidence, score_criteria_match
        title = "Ken Hirsch - Partner - Goldman Sachs (Co-Chairman ... - LinkedIn"
        body = (
            "Ken Hirsch - Partner - Goldman Sachs (Co-Chairman, Global TMT "
            "...Vidhi Bhatnagar - Vice President, Management & Strategy "
            "...Robert Wang - MBA Candidate at Kellogg School of Management "
            "...Winston Xu, CFA - PWA at Goldman Sachs"
        )
        evidence = person_scoped_evidence("Ken Hirsch", title, body)
        assert "kellogg" not in evidence.lower()
        assert "robert wang" not in evidence.lower()
        match = score_criteria_match(
            {"name": "Ken Hirsch", "role": "Partner", "evidence": evidence,
             "affinity": []},
            ["Northwestern Alum"],
        )
        assert match["alumni_match"] is False

    def test_mixed_criteria_alumni_floor_ignores_role_only(self, monkeypatch):
        scored = []
        for i in range(5):
            scored.append({
                "name": f"Vice Person{i} Smith",
                "role": "VP Engineering",
                "email": f"vp{i}@gs.com",
                "linkedin_url": f"https://www.linkedin.com/in/vp-{i}-smith",
                "email_kind": "personal",
                "email_verified": True,
                "email_person_match": True,
                "email_mx_ok": True,
                "on_domain": True,
                "linkedin_verified": True,
                "person_verified": True,
                "name_from_email": False,
                "criteria_match": True,
                "alumni_match": False,
                "match_score": 0.5,
                "matched_terms": ["VP Engineering"],
                "affinity": [],
            })
        for c in scored:
            parts = c["name"].split()
            c["name"] = f"{parts[0]} {parts[-1]}"
        import deep_research as dr
        monkeypatch.setattr(
            dr, "select_outreach_contacts",
            lambda contacts, emails, domain, limit=5, **_k: list(contacts)[:limit],
        )
        svc = DeepResearchService(db=None, enrichment=None)
        selected = svc._select_persistable(
            scored, [c["email"] for c in scored], "gs.com",
            min_contacts=5,
            criteria_terms=["VP Engineering", "Northwestern Alum"],
            alumni_only_floor=True,
        )
        # Role-only hits are kept as "other" contacts, never as alumni.
        assert selected
        assert all(not c.get("alumni_match") for c in selected)


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
            results, "Acme AI")
        assert len(people) == 1
        assert people[0]["name"].lower().startswith("jane")
        assert "linkedin.com/in/jane-doe" in people[0]["linkedin_url"]

    def test_extract_people_rejects_wrong_company(self):
        results = [{
            "title": "Jane Doe - VP Engineering - OtherCo | LinkedIn",
            "body": "Jane Doe works at OtherCo.",
            "href": "https://www.linkedin.com/in/jane-doe-999",
        }]
        assert extract_people_from_snippets(results, "Acme AI") == []

    def test_extract_alumni_at_company(self):
        results = [{
            "title": "Benjamin Forbes - Northwestern University | LinkedIn",
            "body": "Associate at Goldman Sachs. New York, NY.",
            "href": "https://www.linkedin.com/in/benjamin-forbes-8b8379203",
        }]
        people = extract_people_from_snippets(
            results, "Goldman Sachs")
        assert len(people) == 1
        assert "northwestern" in (people[0].get("evidence") or "").lower()


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

    def test_alumni_only_floor_does_not_pad_with_ceo(self, monkeypatch):
        scored = []
        for i in range(3):
            scored.append({
                "name": f"Alum Person{i} Smith",
                "role": "Associate",
                "email": f"alum{i}@gs.com",
                "linkedin_url": f"https://www.linkedin.com/in/alum-{i}-smith",
                "email_kind": "personal",
                "email_verified": True,
                "email_person_match": True,
                "email_mx_ok": True,
                "on_domain": True,
                "linkedin_verified": True,
                "person_verified": True,
                "name_from_email": False,
                "criteria_match": True,
                "alumni_match": True,
                "match_score": 1.0,
                "matched_terms": ["Northwestern Alum"],
                "evidence": "Northwestern University · Goldman Sachs",
                "affinity": [],
            })
        scored.append({
            "name": "David Solomon",
            "role": "CEO",
            "email": "ceo@gs.com",
            "linkedin_url": "https://www.linkedin.com/in/david-m-solomon",
            "email_kind": "personal",
            "email_verified": True,
            "email_person_match": True,
            "email_mx_ok": True,
            "on_domain": True,
            "linkedin_verified": True,
            "person_verified": True,
            "name_from_email": False,
            "criteria_match": False,
            "alumni_match": False,
            "match_score": 0.0,
            "matched_terms": [],
            "affinity": [],
        })
        import deep_research as dr
        monkeypatch.setattr(
            dr, "select_outreach_contacts",
            lambda contacts, emails, domain, limit=5, **_k: list(contacts)[:limit],
        )
        # Use realistic two-token names for channel checks
        for c in scored:
            parts = c["name"].split()
            if len(parts) >= 3:
                c["name"] = f"{parts[0]} {parts[-1]}"

        svc = DeepResearchService(db=None, enrichment=None)
        selected = svc._select_persistable(
            scored, [c["email"] for c in scored], "gs.com",
            min_contacts=5,
            criteria_terms=["Northwestern Alum"],
            alumni_only_floor=True,
        )
        alumni = [c for c in selected if c.get("alumni_match")]
        others = [c for c in selected if not c.get("alumni_match")]
        assert len(alumni) == 3
        # Non-alumni company people are kept, but separated from matches.
        assert any(c.get("name") == "David Solomon" for c in others)

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


class TestUntilCriteriaAndHardStop:
    def test_hard_stop_is_thirty_minutes(self):
        assert HARD_STOP_SEC == 30 * 60

    def test_timed_out_on_deadline(self):
        svc = DeepResearchService(db=None, enrichment=None)
        assert svc._timed_out(time.monotonic() - 1) is True
        assert svc._timed_out(time.monotonic() + 60) is False

    def test_select_respects_raised_matched_cap(self, monkeypatch):
        scored = []
        for i in range(25):
            scored.append({
                "name": f"Person{i} Smith",
                "role": "VP Engineering",
                "email": f"p{i}@acme.com",
                "linkedin_url": f"https://www.linkedin.com/in/p{i}-smith",
                "email_kind": "personal",
                "email_verified": True,
                "email_person_match": True,
                "email_mx_ok": True,
                "on_domain": True,
                "linkedin_verified": True,
                "person_verified": True,
                "name_from_email": False,
                "criteria_match": True,
                "match_score": 1.0,
                "matched_terms": ["VP Engineering"],
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
            min_contacts=25,
            criteria_terms=["VP Engineering"],
            max_matched=25,
        )
        assert sum(1 for c in selected if c.get("criteria_match")) == 25

    def test_until_floor_stops_when_should_stop_fires(self, monkeypatch):
        monkeypatch.setattr(
            "ddg_search.ddg_text_search",
            lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("should not search")),
        )
        svc = DeepResearchService(db=None, enrichment=None)
        out = svc._ensure_contact_floor(
            [],
            company_name="Acme",
            domain="acme.com",
            criteria_terms=["VP Engineering"],
            min_contacts=5,
            require_floor=True,
            target_criteria_matches=5,
            should_stop=lambda: True,
        )
        assert out == []
        assert UNTIL_MAX_ROUNDS >= 2

    def test_linkedin_fill_stop_keeps_unvisited_contacts(self, monkeypatch):
        """Hard-stop mid LinkedIn backfill must not drop or duplicate people."""
        import deep_research as dr

        people = [
            {
                "name": "Jane Doe",
                "role": "VP Engineering",
                "email": "jane.doe@acme.com",
                "linkedin_url": "",
                "email_kind": "personal",
                "email_verified": True,
                "email_person_match": True,
                "email_mx_ok": True,
                "on_domain": True,
                "name_from_email": False,
                "affinity": ["criteria:VP Engineering"],
            },
            {
                "name": "Alex Kim",
                "role": "VP Engineering",
                "email": "alex.kim@acme.com",
                "linkedin_url": "",
                "email_kind": "personal",
                "email_verified": True,
                "email_person_match": True,
                "email_mx_ok": True,
                "on_domain": True,
                "name_from_email": False,
                "affinity": ["criteria:VP Engineering"],
            },
        ]
        calls = {"n": 0}

        def fake_li(name, _company):
            calls["n"] += 1
            slug = name.lower().replace(" ", "-")
            return f"https://www.linkedin.com/in/{slug}"

        monkeypatch.setattr(dr, "find_linkedin_via_search", fake_li)
        monkeypatch.setattr(
            dr, "annotate_contact",
            lambda c, **_k: {**c, "person_verified": True},
        )
        monkeypatch.setattr(
            "ddg_search.ddg_text_search", lambda *_a, **_k: [],
        )

        def stop_after_first():
            return calls["n"] >= 1

        svc = DeepResearchService(db=None, enrichment=None)
        out = svc._ensure_contact_floor(
            people,
            company_name="Acme",
            domain="acme.com",
            criteria_terms=["VP Engineering"],
            min_contacts=5,
            require_floor=True,
            target_criteria_matches=5,
            should_stop=stop_after_first,
        )
        names = [(c.get("name") or "") for c in out]
        assert "Jane Doe" in names
        assert "Alex Kim" in names
        assert len(names) == len(set(names))


class TestDeepEnrichMode:
    def test_enrich_mode_deep_sets_flag(self, monkeypatch):
        from enrichment import EnrichmentService

        svc = EnrichmentService()
        monkeypatch.setattr(svc, "find_website", lambda _n: None)
        result = svc.enrich("Missing Co", mode="deep")
        assert result["enrich_mode"] == "deep"
        assert result["ok"] is False
