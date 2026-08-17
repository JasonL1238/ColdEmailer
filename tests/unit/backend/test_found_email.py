"""Self-published address sources: attribution, filters, and budget.

Each test here pins a measured fact. The numbers come from replaying cached
captures against real GitHub/arXiv/EDGAR responses; where a rule looks wrong
(a GitHub address that does not contain the person's name is ACCEPTED) the
measurement is in the docstring.
"""
import json

import pytest

import found_email as fe


class _Resp:
    def __init__(self, status_code=200, text="", headers=None, json_data=None):
        self.status_code = status_code
        self.text = json.dumps(json_data) if json_data is not None else text
        self.headers = headers or {}


def http_router(mapping, *, record=None):
    """URL-substring keyed fake getter; first matching key wins."""
    def fake(url, headers=None, timeout=None, allow_redirects=False):
        if record is not None:
            record.append(url)
        for key, resp in mapping.items():
            if key in url:
                return resp
        return _Resp(404, "")
    return fake


EVENTS = [{"type": "PushEvent", "repo": {"name": "janedoe/proj"},
           "payload": {"head": "abc1234",
                       # Stale field: if anyone starts reading payload.commits
                       # again this wrong address would surface.
                       "commits": [{"author": {"email": "wrong@stale.com"}}]}}]


def _github_map(patch_text=None, commit_json=None, profile=None, events=EVENTS):
    return {
        "api.github.com/users/janedoe/events/public": _Resp(json_data=events),
        "api.github.com/users/janedoe": _Resp(json_data=profile or {
            "login": "janedoe", "type": "User", "name": "Jane Doe"}),
        "api.github.com/repos/janedoe/proj/commits/abc1234": _Resp(
            json_data=commit_json) if commit_json else _Resp(404, ""),
        "github.com/janedoe/proj/commit/abc1234.patch": _Resp(
            text=patch_text) if patch_text else _Resp(404, ""),
    }


@pytest.fixture
def allow_urls(monkeypatch):
    """Let fake hostnames through the SSRF guard.

    is_safe_public_url resolves the host, so invented domains like big.test
    fail for the wrong reason and would hide the behavior under test.
    """
    monkeypatch.setattr(fe, "is_safe_public_url",
                        lambda u: "127.0.0.1" not in u and "localhost" not in u)


class TestGuardedGet:
    def test_a_private_redirect_hop_is_refused(self, allow_urls):
        getter = http_router({"start": _Resp(
            302, headers={"Location": "http://127.0.0.1/steal"})})
        assert fe.guarded_get_text("https://start.test/x",
                                   http_get=getter) is None

    def test_body_is_capped(self, allow_urls):
        getter = http_router({"big": _Resp(text="x" * 100000)})
        got = fe.guarded_get_text("https://big.test", http_get=getter,
                                  max_bytes=1000)
        assert len(got.text) == 1000

    def test_transport_failure_is_not_an_error(self, allow_urls):
        def boom(*_a, **_k):
            raise OSError("network down")
        assert fe.guarded_get_text("https://x.test", http_get=boom) is None


class TestPlaceholderFilter:
    """24 of 34 (71%) email-shaped strings in cached SERPs were templates."""

    @pytest.mark.parametrize("addr", [
        "john.smith@gs.com", "janedoe@gs.com", "jane@gs.com",
        "jane.doe@gs.com", "first.last@acme.com", "firstname.lastname@acme.com",
        "flast@acme.com", "last@exyntechnologies.com", "yourname@acme.com",
        "first_last@acme.com",
    ])
    def test_measured_templates_are_rejected(self, addr):
        assert fe.is_placeholder_address(addr) is True

    @pytest.mark.parametrize("addr", [
        "torvalds@linux-foundation.org", "swillison@gmail.com",
        "armin.ronacher@active-4.com", "ken.hirsch@gs.com",
    ])
    def test_real_addresses_survive(self, addr):
        assert fe.is_placeholder_address(addr) is False

    def test_a_real_john_smith_is_a_known_cost(self):
        """Documenting the false negative we accept deliberately.

        A genuine John Smith loses his address to this filter. The trade is
        taken because 71% of email-shaped SERP strings were templates, and a
        template saved as a real address is worse: it looks scraped-from-a-page
        and can be approved for sending."""
        assert fe.is_placeholder_address("john.smith@realco.com") is True


class TestGithubLoginFromUrl:
    def test_profile_url(self):
        assert fe.github_login_from_url(
            "https://github.com/janedoe") == ("janedoe", False)

    def test_repo_owner_is_weak(self):
        assert fe.github_login_from_url(
            "https://github.com/facebook/react") == ("facebook", True)

    def test_reserved_paths_are_not_logins(self):
        for url in ("https://github.com/features", "https://github.com/orgs/x",
                    "https://github.com/search?q=a"):
            assert fe.github_login_from_url(url) is None

    def test_non_github_host(self):
        assert fe.github_login_from_url("https://gitlab.com/janedoe") is None


class TestGithubEmails:
    def test_patch_route_yields_the_address(self):
        getter = http_router(_github_map(
            patch_text="From: Jane Doe <j.doe@realco.com>\n"))
        out = fe.github_emails_for("Jane Doe",
                                   urls=["https://github.com/janedoe"],
                                   http_get=getter, token=None)
        assert [a["email"] for a in out] == ["j.doe@realco.com"]
        assert out[0]["source_kind"] == "github_commit"
        assert out[0]["attributed"] is True

    def test_patch_is_preferred_and_the_commits_api_is_not_called(self):
        seen = []
        getter = http_router(_github_map(
            patch_text="From: Jane Doe <j.doe@realco.com>\n"), record=seen)
        budget = fe.GetBudget()
        fe.github_emails_for("Jane Doe", urls=["https://github.com/janedoe"],
                             http_get=getter, budget=budget, token=None)
        assert not any("/repos/" in u for u in seen)
        # profile verification + events feed. The .patch itself costs no API
        # quota, which is the whole reason it is preferred.
        assert budget.counts["github_api"] == 2
        assert budget.counts["plain"] == 1

    def test_payload_commits_is_never_read(self):
        """Measured dead: 0 of 350 PushEvents still carry payload.commits."""
        getter = http_router(_github_map(
            patch_text="From: Jane Doe <j.doe@realco.com>\n"))
        out = fe.github_emails_for("Jane Doe",
                                   urls=["https://github.com/janedoe"],
                                   http_get=getter, token=None)
        assert "wrong@stale.com" not in [a["email"] for a in out]

    def test_merge_commit_authored_by_another_person_is_rejected(self):
        """The head of your push can be someone else's commit."""
        getter = http_router(_github_map(
            patch_text="From: Bob Other <bob@other.com>\n"))
        assert fe.github_emails_for("Jane Doe",
                                    urls=["https://github.com/janedoe"],
                                    http_get=getter, token=None) == []

    def test_disagreeing_patch_headers_fall_back_to_the_api(self):
        getter = http_router(_github_map(
            patch_text=("From: Bob Other <bob@other.com>\n"
                        "From: Jane Doe <j.doe@realco.com>\n"),
            commit_json={"commit": {"author": {"email": "j.doe@realco.com",
                                               "name": "Jane Doe"}},
                         "author": {"login": "janedoe"}}))
        out = fe.github_emails_for("Jane Doe",
                                   urls=["https://github.com/janedoe"],
                                   http_get=getter, token=None)
        assert [a["email"] for a in out] == ["j.doe@realco.com"]

    def test_api_author_login_must_match_the_resolved_login(self):
        getter = http_router(_github_map(
            commit_json={"commit": {"author": {"email": "someone@else.com",
                                               "name": "Someone Else"}},
                         "author": {"login": "someoneelse"}}))
        assert fe.github_emails_for("Jane Doe",
                                    urls=["https://github.com/janedoe"],
                                    http_get=getter, token=None) == []

    def test_noreply_is_never_emitted(self):
        """32.3% of 186 ordinary committers use a noreply address."""
        getter = http_router(_github_map(
            patch_text="From: Jane Doe <1+janedoe@users.noreply.github.com>\n"))
        assert fe.github_emails_for("Jane Doe",
                                    urls=["https://github.com/janedoe"],
                                    http_get=getter, token=None) == []

    def test_bot_commits_are_rejected(self):
        getter = http_router(_github_map(
            patch_text="From: dependabot[bot] <support@dependabot.com>\n"))
        assert fe.github_emails_for("Jane Doe",
                                    urls=["https://github.com/janedoe"],
                                    http_get=getter, token=None) == []

    def test_an_address_not_containing_the_name_is_accepted(self):
        """The rule that inverts normal intuition.

        contact_verify.email_matches_person would reject 4 of the 8 correct
        answers measured (torvalds@linux-foundation.org, me@kennethreitz.org,
        antirez@gmail.com, tiangolo@gmail.com). Attribution replaces the
        name check for commit-sourced addresses."""
        from contact_verify import email_matches_person
        assert email_matches_person(
            "torvalds@linux-foundation.org", "Linus Torvalds") is False
        events = [{"type": "PushEvent", "repo": {"name": "torvalds/linux"},
                   "payload": {"head": "deadbee"}}]
        getter = http_router({
            "users/torvalds/events/public": _Resp(json_data=events),
            "api.github.com/users/torvalds": _Resp(json_data={
                "login": "torvalds", "type": "User", "name": "Linus Torvalds"}),
            "torvalds/linux/commit/deadbee.patch": _Resp(
                text="From: Linus Torvalds <torvalds@linux-foundation.org>\n"),
        })
        out = fe.github_emails_for("Linus Torvalds",
                                   urls=["https://github.com/torvalds"],
                                   http_get=getter, token=None)
        assert [a["email"] for a in out] == ["torvalds@linux-foundation.org"]

    def test_a_url_resolved_login_costs_no_search_call(self):
        budget = fe.GetBudget()
        getter = http_router(_github_map(
            patch_text="From: Jane Doe <j.doe@realco.com>\n"))
        fe.github_emails_for("Jane Doe", urls=["https://github.com/janedoe"],
                             http_get=getter, budget=budget, token=None)
        assert budget.counts["github_search"] == 0

    def test_org_owner_url_is_verified_before_use(self):
        # github.com/facebook/react must not make "facebook" Jane's login.
        getter = http_router({
            "api.github.com/users/facebook": _Resp(json_data={
                "login": "facebook", "type": "Organization"}),
            "api.github.com/search/users": _Resp(json_data={"items": []}),
        })
        assert fe.github_resolve_login(
            "Jane Doe", urls=["https://github.com/facebook/react"],
            http_get=getter) is None

    def test_quota_exhaustion_disables_github_for_the_run(self):
        budget = fe.GetBudget()
        getter = http_router({"api.github.com": _Resp(
            403, headers={"X-RateLimit-Remaining": "0"})})
        fe.github_emails_for("Jane Doe", urls=["https://github.com/janedoe"],
                             http_get=getter, budget=budget, token=None)
        assert budget.is_rate_limited("github") is True

    def test_token_is_sent_when_configured(self):
        captured = {}

        def getter(url, headers=None, **_k):
            captured.update(headers or {})
            return _Resp(404, "")
        fe.github_emails_for("Jane Doe", urls=["https://github.com/janedoe"],
                             http_get=getter, token="ghp_secret")
        assert captured.get("Authorization") == "Bearer ghp_secret"


ARXIV_FEED = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>http://arxiv.org/abs/2401.00001v1</id>
<published>2024-01-02T00:00:00Z</published>
<author><name>Jane Doe</name></author></entry>
<entry><id>http://arxiv.org/abs/2001.00002v1</id>
<published>2020-01-02T00:00:00Z</published>
<author><name>Jane Doe</name></author></entry></feed>"""


class TestArxiv:
    def test_query_sorts_by_submitted_date_descending(self):
        seen = []
        fe.arxiv_recent_entries("Jane Doe", http_get=http_router(
            {"export.arxiv.org": _Resp(text=ARXIV_FEED)}, record=seen))
        assert "sortBy=submittedDate" in seen[0]
        assert "sortOrder=descending" in seen[0]

    def test_papers_before_the_html_epoch_are_dropped(self):
        """arXiv renders HTML only from ~Dec 2023; older ids 404."""
        entries = fe.arxiv_recent_entries("Jane Doe", http_get=http_router(
            {"export.arxiv.org": _Resp(text=ARXIV_FEED)}))
        assert [e["arxiv_id"] for e in entries] == ["2401.00001v1"]

    def test_author_block_address_is_returned(self):
        getter = http_router({
            "export.arxiv.org": _Resp(text=ARXIV_FEED),
            "arxiv.org/html/2401.00001v1": _Resp(
                text="<p>Jane Doe, jane.doe@uni.edu</p>"),
        })
        out = fe.arxiv_emails_for("Jane Doe", http_get=getter)
        assert [a["email"] for a in out] == ["jane.doe@uni.edu"]
        assert out[0]["source_kind"] == "arxiv"

    def test_a_coauthors_distant_address_is_not_credited(self):
        getter = http_router({
            "export.arxiv.org": _Resp(text=ARXIV_FEED),
            "arxiv.org/html/2401.00001v1": _Resp(
                text="<p>Jane Doe</p>" + ("filler " * 900)
                     + "<p>Rob Roy, rob.roy@other.edu</p>"),
        })
        assert fe.arxiv_emails_for("Jane Doe", http_get=getter) == []


class TestEdgar:
    def test_no_contact_means_no_request_at_all(self):
        def boom(*_a, **_k):
            raise AssertionError("EDGAR must not be called without a contact")
        assert fe.edgar_emails_for("Jane Doe", contact_email=None,
                                   http_get=boom) == []

    def test_a_declared_contact_user_agent_is_sent(self):
        captured = {}

        def getter(url, headers=None, **_k):
            captured.update(headers or {})
            return _Resp(json_data={"hits": {"hits": []}})
        fe.edgar_emails_for("Jane Doe", contact_email="me@example.com",
                            http_get=getter)
        assert "@" in captured.get("User-Agent", "")


class TestRouter:
    def test_a_github_url_routes_to_github(self):
        assert "github" in fe.route_sources(
            urls=["https://github.com/janedoe"], role="Analyst")

    def test_a_managing_director_gets_no_github(self):
        assert fe.route_sources(role="Managing Director",
                                company_name="Goldman Sachs") == []

    def test_finance_routes_to_edgar_only_when_enabled(self):
        args = dict(role="Managing Director", company_name="Goldman Sachs")
        assert fe.route_sources(edgar_enabled=True, **args) == ["edgar"]
        assert fe.route_sources(edgar_enabled=False, **args) == []

    def test_academic_signals_route_to_arxiv(self):
        assert "arxiv" in fe.route_sources(role="Professor of Robotics")
        assert "arxiv" in fe.route_sources(
            role="", urls=["https://cs.stanford.edu/~jane"])

    def test_at_most_two_sources(self):
        chosen = fe.route_sources(
            role="Professor and software engineer",
            urls=["https://github.com/janedoe"],
            company_name="Goldman Sachs", edgar_enabled=True)
        assert len(chosen) <= fe.MAX_SOURCES_PER_CANDIDATE
