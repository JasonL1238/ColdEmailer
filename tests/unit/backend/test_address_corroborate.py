"""Corpus-presence corroboration: precision, and the traps that fake it.

Every test here pins a measured fact from a run over 40 real addresses and 12
invented addresses built on the same real domains. The invented ones are the
point: they are what separates an oracle from something that merely returns
200. Four sources in this family look like oracles and are not, and each has a
test below asserting we do not believe them.
"""
import json

import pytest

import address_corroborate as ac


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


@pytest.fixture(autouse=True)
def _enable(monkeypatch):
    monkeypatch.setenv("ADDRESS_CORROBORATION", "1")
    ac.clear_corroboration_cache()
    yield
    ac.clear_corroboration_cache()


def _commit_payload(email, name="Ada Lovelace", total=7):
    return {"total_count": total, "items": [
        {"html_url": "https://github.com/o/r/commit/abc",
         "commit": {"author": {"name": name, "email": email},
                    "committer": {"name": name, "email": email}}}]}


# --------------------------------------------------------------------------
# GitHub commit search — the highest-coverage source
# --------------------------------------------------------------------------

class TestGithubCommit:
    def test_exact_author_email_is_a_hit_and_carries_the_name(self):
        get = http_router({"search/commits":
                           _Resp(json_data=_commit_payload("ada@acme.com"))})
        hit = ac.github_commit_hit("ada@acme.com", http_get=get)
        assert hit is not None
        assert hit.source == "github_commit"
        assert hit.names == ("Ada Lovelace",)

    def test_total_count_without_a_matching_item_is_not_a_hit(self):
        """`total_count` is an estimate, not a count.

        Measured: one real address reports total_count=1,206,871 while another
        reports 2. Trusting the number instead of the items is how a search
        that merely ranked something highly becomes a false confirmation.
        """
        payload = {"total_count": 4321, "items": [
            {"html_url": "https://github.com/o/r/commit/abc",
             "commit": {"author": {"name": "Someone Else",
                                   "email": "other@acme.com"}}}]}
        get = http_router({"search/commits": _Resp(json_data=payload)})
        assert ac.github_commit_hit("ada@acme.com", http_get=get) is None

    def test_an_original_repo_is_cited_over_a_fork(self):
        """Search returns the same commit from every copy of the repo.

        A cosmetic tiebreak on the citation only — the address match itself is
        confirmed per item and is unaffected. It is also only a partial fix:
        measured live, storchaka@gmail.com's top hits are all
        `swe-train/numpy__numpy`, which GitHub reports as fork=False because it
        was re-committed rather than forked, so no field can catch it.
        """
        payload = {"total_count": 2, "items": [
            {"html_url": "https://github.com/mirror/x/commit/1",
             "repository": {"fork": True},
             "commit": {"author": {"name": "Ada", "email": "ada@acme.com"}}},
            {"html_url": "https://github.com/ada/real/commit/2",
             "repository": {"fork": False},
             "commit": {"author": {"name": "Ada", "email": "ada@acme.com"}}},
        ]}
        get = http_router({"search/commits": _Resp(json_data=payload)})
        hit = ac.github_commit_hit("ada@acme.com", http_get=get)
        assert hit.url == "https://github.com/ada/real/commit/2"

    def test_a_fork_is_still_cited_when_it_is_all_there_is(self):
        payload = {"total_count": 1, "items": [
            {"html_url": "https://github.com/mirror/x/commit/1",
             "repository": {"fork": True},
             "commit": {"author": {"name": "Ada", "email": "ada@acme.com"}}}]}
        get = http_router({"search/commits": _Resp(json_data=payload)})
        hit = ac.github_commit_hit("ada@acme.com", http_get=get)
        assert hit.url == "https://github.com/mirror/x/commit/1"

    def test_invented_address_returns_no_hit(self):
        get = http_router({"search/commits":
                           _Resp(json_data={"total_count": 0, "items": []})})
        assert ac.github_commit_hit(
            "quinlan.vashtimore@acme.com", http_get=get) is None


# --------------------------------------------------------------------------
# GitHub profile — independent of the commit index
# --------------------------------------------------------------------------

class TestGithubProfile:
    def test_hit_requires_the_profile_email_to_match_exactly(self):
        get = http_router({
            "search/users": _Resp(json_data={
                "total_count": 1, "items": [{"login": "ada"}]}),
            "/users/ada": _Resp(json_data={
                "login": "ada", "name": "Ada Lovelace",
                "email": "ada@acme.com",
                "html_url": "https://github.com/ada"}),
        })
        hit = ac.github_profile_hit("ada@acme.com", http_get=get)
        assert hit is not None and hit.names == ("Ada Lovelace",)

    def test_search_ranking_alone_is_not_a_hit(self):
        """A returned login whose profile carries a different address is a
        ranking artifact, not evidence about the queried address."""
        get = http_router({
            "search/users": _Resp(json_data={
                "total_count": 1, "items": [{"login": "someone"}]}),
            "/users/someone": _Resp(json_data={
                "login": "someone", "name": "Someone Else",
                "email": "different@acme.com"}),
        })
        assert ac.github_profile_hit("ada@acme.com", http_get=get) is None


# --------------------------------------------------------------------------
# Gravatar — free, no auth, and easy to call wrongly
# --------------------------------------------------------------------------

class TestGravatar:
    def test_uses_sha256_not_md5(self):
        """The v3 profile API is sha256-only and silently 404s an md5 hash.

        Measured: addresses with real profiles return 404 when asked by md5.
        Getting this wrong produces a believable "no coverage" result rather
        than an error, so the hash is asserted directly.
        """
        import hashlib
        seen = []
        get = http_router({"gravatar": _Resp(404)}, record=seen)
        ac.gravatar_hit("ada@acme.com", http_get=get)
        want = hashlib.sha256(b"ada@acme.com").hexdigest()
        assert want in seen[0]
        assert hashlib.md5(b"ada@acme.com").hexdigest() not in seen[0]

    def test_profile_hit_returns_display_name(self):
        get = http_router({"gravatar": _Resp(json_data={
            "display_name": "Ada Lovelace",
            "profile_url": "https://gravatar.com/ada"})})
        hit = ac.gravatar_hit("ada@acme.com", http_get=get)
        assert hit.source == "gravatar" and hit.names == ("Ada Lovelace",)

    def test_404_is_a_miss(self):
        get = http_router({"gravatar": _Resp(404)})
        assert ac.gravatar_hit("ada@acme.com", http_get=get) is None


# --------------------------------------------------------------------------
# PGP keyservers
# --------------------------------------------------------------------------

class TestPgp:
    def test_openpgp_key_block_is_the_strong_hit(self):
        get = http_router({"keys.openpgp.org": _Resp(
            text="-----BEGIN PGP PUBLIC KEY BLOCK-----\nmDMEY...")})
        hit = ac.pgp_hit("ada@acme.com", http_get=get)
        assert hit.source == "pgp_verified"

    def test_ubuntu_index_extracts_the_uid_name(self):
        body = ("info:1:1\npub:ABC:1:2048:1300000000::\n"
                "uid:Ada%20Lovelace%20%3Cada%40acme.com%3E:1300000000::\n")
        get = http_router({"keys.openpgp.org": _Resp(404),
                           "keyserver.ubuntu.com": _Resp(text=body)})
        hit = ac.pgp_hit("ada@acme.com", http_get=get)
        assert hit.source == "pgp_keyserver"
        assert hit.names == ("Ada Lovelace",)

    def test_uid_for_a_different_address_on_the_same_key_is_not_a_hit(self):
        """A key can carry several addresses; only the queried one counts."""
        body = ("info:1:1\npub:ABC:1:2048:1300000000::\n"
                "uid:Ada%20Lovelace%20%3Cother%40acme.com%3E:1300000000::\n")
        get = http_router({"keys.openpgp.org": _Resp(404),
                           "keyserver.ubuntu.com": _Resp(text=body)})
        assert ac.pgp_hit("ada@acme.com", http_get=get) is None


# --------------------------------------------------------------------------
# Mailing list — the trap that motivated the whole entry-scoping rule
# --------------------------------------------------------------------------

class TestMailingList:
    def test_query_echoed_in_title_is_not_a_hit(self):
        """lore's Atom feed echoes the query inside <title>.

        Measured: a naive "address appears in the response body" test is
        trivially true for EVERY query, including invented addresses, because
        the server hands your own query back. Only <entry> content counts.
        """
        feed = ('<?xml version="1.0"?><feed><title>lore.kernel.org search '
                'for "quinlan.vashtimore@acme.com"</title></feed>')
        get = http_router({"lore.kernel.org": _Resp(text=feed)})
        assert ac.mailing_list_hit(
            "quinlan.vashtimore@acme.com", http_get=get) is None

    def test_address_inside_an_entry_is_a_hit(self):
        feed = ('<feed><title>search for "ada@acme.com"</title>'
                '<entry><author><name>Ada</name></author>'
                '<content>From: ada@acme.com</content></entry></feed>')
        get = http_router({"lore.kernel.org": _Resp(text=feed)})
        hit = ac.mailing_list_hit("ada@acme.com", http_get=get)
        assert hit is not None and hit.source == "mailing_list"

    def test_entries_about_someone_else_are_not_a_hit(self):
        feed = ('<feed><title>search for "ada@acme.com"</title>'
                '<entry><content>From: bob@acme.com</content></entry></feed>')
        get = http_router({"lore.kernel.org": _Resp(text=feed)})
        assert ac.mailing_list_hit("ada@acme.com", http_get=get) is None


# --------------------------------------------------------------------------
# The composed result
# --------------------------------------------------------------------------

class TestCorroborateAddress:
    def test_invented_address_is_not_corroborated_by_anything(self):
        """The number that generalises. Measured 0 false positives across
        every source on 12 invented addresses over real domains."""
        get = http_router({
            "search/commits": _Resp(json_data={"total_count": 0, "items": []}),
            "search/users": _Resp(json_data={"total_count": 0, "items": []}),
            "gravatar": _Resp(404),
            "keys.openpgp.org": _Resp(404),
            "keyserver.ubuntu.com": _Resp(404),
            "lore.kernel.org": _Resp(404),
        })
        out = ac.corroborate_address("quinlan.vashtimore@acme.com",
                                     name="Quinlan Vashtimore", http_get=get)
        assert out.found is False
        assert out.sources == ()
        assert out.name_match is None
        assert out.reason == "not_found"

    def test_sources_union_and_name_match(self):
        get = http_router({
            "search/commits": _Resp(json_data=_commit_payload("ada@acme.com")),
            "search/users": _Resp(json_data={"total_count": 0, "items": []}),
            "gravatar": _Resp(json_data={"display_name": "Ada Lovelace"}),
            "keys.openpgp.org": _Resp(404),
            "keyserver.ubuntu.com": _Resp(404),
            "lore.kernel.org": _Resp(404),
        })
        out = ac.corroborate_address("ada@acme.com", name="Ada Lovelace",
                                     http_get=get)
        assert set(out.sources) == {"github_commit", "gravatar"}
        assert out.name_match is True
        assert out.as_dict()["found"] is True

    def test_a_real_address_owned_by_someone_else_does_not_match(self):
        """The namesake case, which is the whole reason name_match exists.

        The address is genuinely real — a commit proves it — but it belongs to
        a different human, so it must not read as confirmation of our target.
        """
        get = http_router({
            "search/commits": _Resp(
                json_data=_commit_payload("ada@acme.com", name="Bob Stone")),
            "search/users": _Resp(json_data={"total_count": 0, "items": []}),
            "gravatar": _Resp(404), "keys.openpgp.org": _Resp(404),
            "keyserver.ubuntu.com": _Resp(404), "lore.kernel.org": _Resp(404),
        })
        out = ac.corroborate_address("ada@acme.com", name="Ada Lovelace",
                                     http_get=get)
        assert out.found is True
        assert out.name_match is False

    def test_anonymous_hit_leaves_name_match_unknown_not_false(self):
        """lore returns no name, so identity is unknown — which is not the
        same as wrong, and must not be collapsed into it."""
        get = http_router({
            "search/commits": _Resp(json_data={"total_count": 0, "items": []}),
            "search/users": _Resp(json_data={"total_count": 0, "items": []}),
            "gravatar": _Resp(404), "keys.openpgp.org": _Resp(404),
            "keyserver.ubuntu.com": _Resp(404),
            "lore.kernel.org": _Resp(
                text='<feed><entry><content>ada@acme.com</content>'
                     '</entry></feed>'),
        })
        out = ac.corroborate_address("ada@acme.com", name="Ada Lovelace",
                                     http_get=get)
        assert out.sources == ("mailing_list",)
        assert out.name_match is None

    def test_kill_switch_makes_no_requests(self, monkeypatch):
        monkeypatch.setenv("ADDRESS_CORROBORATION", "0")
        seen = []
        get = http_router({}, record=seen)
        out = ac.corroborate_address("ada@acme.com", name="Ada", http_get=get)
        assert out.reason == "disabled"
        assert seen == []

    def test_a_broken_oracle_never_fails_the_lookup(self):
        def explode(email, **kwargs):
            raise RuntimeError("upstream is down")
        out = ac.corroborate_address(
            "ada@acme.com", name="Ada Lovelace",
            oracles=(("boom", explode),))
        assert out.found is False
        assert out.reason == "not_found"

    def test_budget_caps_the_requests(self):
        seen = []
        get = http_router({"": _Resp(404)}, record=seen)
        import found_email
        budget = found_email.GetBudget(max_gets=2, max_github_api=1,
                                       max_github_search=1)
        ac.corroborate_address("ada@acme.com", name="Ada", http_get=get,
                               budget=budget)
        assert len(seen) <= 2
