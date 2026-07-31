"""Consolidating repeated deep dives on one company into a single view."""
import pytest

from research_digest import (
    MAX_RUNS_KEPT,
    append_run,
    consolidate,
    contact_is_criteria_match,
    group_contacts,
    has_deep_research,
    matched_terms_from_notes,
    merge_intel,
    runs_of,
)


def intel(**kw):
    base = {
        "key_changes": [], "improvements": [], "policy_highlights": [],
        "differentiators": [], "talking_points": [], "news_snippets": [],
    }
    base.update(kw)
    return base


class TestAppendRun:
    def test_second_dive_does_not_erase_the_first(self):
        first = append_run(
            None, intel(key_changes=["Raised a Series B"]),
            criteria="Penn alumni", job_id="j1", researched_at="2026-01-01T00:00:00")
        second = append_run(
            first, intel(key_changes=["Opened a Berlin office"]),
            criteria="ML engineers", job_id="j2", researched_at="2026-02-01T00:00:00")

        runs = second["runs"]
        assert len(runs) == 2
        assert [r["criteria"] for r in runs] == ["Penn alumni", "ML engineers"]
        assert runs[0]["key_changes"] == ["Raised a Series B"]
        assert runs[1]["key_changes"] == ["Opened a Berlin office"]

    def test_flat_keys_still_describe_the_latest_run(self):
        """Existing readers of deep_intel keep working unchanged."""
        first = append_run(None, intel(key_changes=["old"]), criteria="a", job_id="j1")
        second = append_run(first, intel(key_changes=["new"]), criteria="b", job_id="j2")
        assert second["key_changes"] == ["new"]

    def test_rewriting_the_same_job_replaces_rather_than_duplicates(self):
        first = append_run(None, intel(key_changes=["draft"]), criteria="a", job_id="j1")
        again = append_run(first, intel(key_changes=["final"]), criteria="a", job_id="j1")
        assert len(again["runs"]) == 1
        assert again["runs"][0]["key_changes"] == ["final"]

    def test_a_legacy_row_becomes_the_first_run(self):
        """Companies researched before runs[] existed keep their findings."""
        legacy = {
            "key_changes": ["Shipped v2"],
            "contact_criteria": "Penn alumni",
            "researched_at": "2025-12-01T00:00:00",
        }
        merged = append_run(
            legacy, intel(key_changes=["Hired a CFO"]),
            criteria="CFO", job_id="j2")
        assert len(merged["runs"]) == 2
        assert merged["runs"][0]["criteria"] == "Penn alumni"
        assert merged["runs"][0]["key_changes"] == ["Shipped v2"]

    def test_history_is_capped(self):
        blob = None
        for i in range(MAX_RUNS_KEPT + 5):
            blob = append_run(blob, intel(key_changes=[f"c{i}"]),
                              criteria=f"crit-{i}", job_id=f"j{i}")
        runs = blob["runs"]
        assert len(runs) == MAX_RUNS_KEPT
        # Oldest dropped, newest kept.
        assert runs[-1]["criteria"] == f"crit-{MAX_RUNS_KEPT + 4}"
        assert all(r["criteria"] != "crit-0" for r in runs)

    def test_blank_criteria_is_recorded_as_none_not_empty_string(self):
        blob = append_run(None, intel(), criteria="   ", job_id="j1")
        assert blob["runs"][0]["criteria"] is None


class TestRunsOf:
    def test_reads_the_runs_list(self):
        blob = append_run(None, intel(key_changes=["x"]), criteria="a", job_id="j1")
        assert len(runs_of(blob)) == 1

    def test_falls_back_to_a_legacy_flat_blob(self):
        assert len(runs_of({"key_changes": ["x"], "contact_criteria": "a"})) == 1

    @pytest.mark.parametrize("blob", [None, {}, "", [], {"error": "Wrong site"}])
    def test_nothing_researched_is_no_runs(self, blob):
        assert runs_of(blob) == []
        assert has_deep_research({"deep_intel": blob}) is False


class TestMergeIntel:
    def test_repeated_findings_collapse_to_one(self):
        runs = [
            {"criteria": "Penn", "key_changes": ["Raised a Series B"]},
            {"criteria": "ML", "key_changes": ["Raised  a series b!", "New CTO"]},
        ]
        merged = merge_intel(runs)["key_changes"]
        assert [m["text"] for m in merged] == ["Raised a Series B", "New CTO"]
        # The shared finding is tagged with both dives that surfaced it.
        assert merged[0]["criteria"] == ["Penn", "ML"]
        assert merged[1]["criteria"] == ["ML"]

    def test_first_wording_wins(self):
        runs = [
            {"criteria": "a", "talking_points": ["Ships weekly"]},
            {"criteria": "b", "talking_points": ["ships weekly"]},
        ]
        assert merge_intel(runs)["talking_points"][0]["text"] == "Ships weekly"

    def test_every_category_is_present_even_when_empty(self):
        merged = merge_intel([])
        assert set(merged) == {
            "key_changes", "improvements", "policy_highlights",
            "differentiators", "talking_points",
        }
        assert all(v == [] for v in merged.values())


class TestCriteriaNotes:
    @pytest.mark.parametrize("notes,expected", [
        ("Criteria match: Penn, Wharton; CEO", ["Penn", "Wharton"]),
        ("Criteria match: Northwestern Alum", ["Northwestern Alum"]),
        ("Criteria match", []),
        ("CEO at Acme", []),
        (None, []),
    ])
    def test_terms_are_parsed_from_notes(self, notes, expected):
        assert matched_terms_from_notes(notes) == expected

    def test_bare_match_still_counts_as_matched(self):
        assert contact_is_criteria_match("Criteria match") is True
        assert contact_is_criteria_match("CEO at Acme") is False


class TestGroupContacts:
    def test_buckets_by_term_and_keeps_the_rest(self):
        grouped = group_contacts([
            {"id": "1", "name": "A", "email": "a@x.com",
             "notes": "Criteria match: Penn"},
            {"id": "2", "name": "B", "linkedin_url": "https://linkedin.com/in/b",
             "notes": "Criteria match: Penn, Wharton"},
            {"id": "3", "name": "C", "email": "c@x.com", "notes": "CEO"},
        ])
        assert grouped["total"] == 3
        assert grouped["matched"] == 2
        assert grouped["with_email"] == 2
        assert grouped["with_linkedin"] == 1
        assert [g["term"] for g in grouped["groups"]] == ["Penn", "Wharton"]
        assert [c["id"] for c in grouped["groups"][0]["contacts"]] == ["1", "2"]
        assert [c["id"] for c in grouped["unmatched"]] == ["3"]

    def test_one_person_matched_twice_appears_under_both_terms(self):
        grouped = group_contacts([
            {"id": "1", "name": "A", "notes": "Criteria match: Penn, Wharton"},
        ])
        assert len(grouped["groups"]) == 2
        assert grouped["matched"] == 1, "should not double-count the person"

    def test_casing_does_not_split_a_bucket(self):
        grouped = group_contacts([
            {"id": "1", "notes": "Criteria match: Northwestern Alum"},
            {"id": "2", "notes": "Criteria match: northwestern alum"},
        ])
        assert len(grouped["groups"]) == 1
        assert grouped["groups"][0]["term"] == "Northwestern Alum"
        assert len(grouped["groups"][0]["contacts"]) == 2

    def test_similar_but_distinct_criteria_stay_apart(self):
        """Penn and Penn State are different schools — never merge them."""
        grouped = group_contacts([
            {"id": "1", "notes": "Criteria match: Penn"},
            {"id": "2", "notes": "Criteria match: Penn State"},
        ])
        assert [g["term"] for g in grouped["groups"]] == ["Penn", "Penn State"]

    def test_seniority_orders_each_bucket(self):
        grouped = group_contacts([
            {"id": "junior", "name": "Z", "seniority_rank": 12,
             "notes": "Criteria match: Penn"},
            {"id": "ceo", "name": "A", "seniority_rank": 1,
             "notes": "Criteria match: Penn"},
        ])
        assert [c["id"] for c in grouped["groups"][0]["contacts"]] == ["ceo", "junior"]


class TestConsolidate:
    def test_two_dives_on_one_company_become_one_section(self):
        blob = append_run(
            None, intel(key_changes=["Series B"], talking_points=["Ships weekly"]),
            criteria="Penn alumni", job_id="j1", researched_at="2026-01-01T00:00:00")
        blob = append_run(
            blob, intel(key_changes=["Series B", "Berlin office"]),
            criteria="ML engineers", job_id="j2", researched_at="2026-03-01T00:00:00")

        view = consolidate(
            {"id": "c1", "name": "Acme", "domain": "acme.com", "deep_intel": blob},
            [{"id": "1", "name": "Jane", "email": "j@acme.com",
              "notes": "Criteria match: Penn alumni"}],
        )

        assert view["company"]["name"] == "Acme"
        assert view["run_count"] == 2
        assert view["criteria"] == ["Penn alumni", "ML engineers"]
        assert view["first_researched_at"] == "2026-01-01T00:00:00"
        assert view["last_researched_at"] == "2026-03-01T00:00:00"
        # "Series B" came from both dives and is listed once.
        assert [b["text"] for b in view["intel"]["key_changes"]] == [
            "Series B", "Berlin office"]
        assert view["contacts"]["total"] == 1
        assert view["runs"][0]["bullet_count"] == 2

    def test_a_company_never_deep_dived_reports_nothing(self):
        view = consolidate({"id": "c1", "name": "Acme", "deep_intel": None}, [])
        assert view["run_count"] == 0
        assert view["criteria"] == []
        assert view["contacts"]["total"] == 0
        assert has_deep_research({"deep_intel": None}) is False

    def test_news_is_deduped_across_runs(self):
        blob = append_run(None, intel(news_snippets=["Acme raised $20M"]),
                          criteria="a", job_id="j1")
        blob = append_run(blob, intel(news_snippets=["Acme raised $20M", "Acme hired"]),
                          criteria="b", job_id="j2")
        view = consolidate({"id": "c", "name": "Acme", "deep_intel": blob}, [])
        assert view["news_snippets"] == ["Acme raised $20M", "Acme hired"]
