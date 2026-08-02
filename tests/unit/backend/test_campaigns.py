"""A campaign is a claim that these numbers belong together.

Two ways that claim goes wrong, and the tests are weighted to both.

It can attach the wrong rows — attribution that drifts, or gets invented for
history that predates the feature. Everything before campaigns existed is
permanently unassigned, and a backfill would fabricate exactly the fact the
page is meant to report.

And it can state a rate the sample cannot support. A campaign is always small
when it is new, which makes this the most tempting place in the app to render
"1 of 3" as 33%. The floor is the same one `analytics` uses, imported rather
than re-declared.
"""
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

import campaigns
import main
from analytics import MIN_SAMPLE
from db import Database, now_iso


def row(**over):
    base = {
        "id": "c1", "name": "fintech NYC", "query": "seed fintech in NYC",
        "created_at": now_iso(), "archived_at": None, "notes": "",
        "companies": 5, "contacts": 9, "drafts": 0, "sent": 0, "replied": 0,
        "bounced": 0, "unverified": 0, "last_sent_at": None,
    }
    base.update(over)
    return base


class TestSummarize:
    def test_withholds_a_rate_below_the_sample_floor(self):
        """A new campaign is always small, so this is where "1 of 3" most
        wants to render as a confident 33%."""
        out = campaigns.summarize(row(sent=3, replied=1))
        assert out["enough_data"] is False
        assert out["rate"] is None
        assert out["sent"] == 3 and out["replied"] == 1

    def test_states_a_rate_once_the_sample_supports_one(self):
        out = campaigns.summarize(row(sent=20, replied=4))
        assert out["enough_data"] is True
        assert out["rate"] == 20.0

    def test_uses_the_same_floor_as_the_rest_of_the_app(self):
        """Imported, not re-declared: two floors would eventually differ and
        the same campaign would be rated on one page and not the other."""
        assert campaigns.summarize(row(sent=MIN_SAMPLE, replied=1))["rate"] is not None
        assert campaigns.summarize(row(sent=MIN_SAMPLE - 1, replied=1))["rate"] is None

    def test_reports_unverified_flags_without_counting_them(self):
        out = campaigns.summarize(row(sent=20, replied=2, unverified=6))
        assert out["unverified"] == 6
        assert out["replied"] == 2
        assert out["rate"] == 10.0     # not 40% — the six are not evidence

    def test_an_empty_campaign_is_zero_not_a_crash(self):
        out = campaigns.summarize(row())
        assert out["sent"] == 0 and out["rate"] is None

    def test_survives_missing_and_junk_counts(self):
        out = campaigns.summarize({"id": "x", "name": "n", "sent": None,
                                   "replied": "", "companies": "abc"})
        assert out["sent"] == 0 and out["companies"] == 0


class TestCompare:
    def test_names_nothing_without_two_qualifying_campaigns(self):
        one = [campaigns.summarize(row(id="a", sent=20, replied=4)),
               campaigns.summarize(row(id="b", sent=3, replied=3))]
        assert campaigns.compare(one)["best"] is None

    def test_names_a_winner_when_two_clear_the_floor(self):
        out = campaigns.compare([
            campaigns.summarize(row(id="a", name="A", sent=20, replied=8)),
            campaigns.summarize(row(id="b", name="B", sent=20, replied=2)),
        ])
        assert out["best"]["name"] == "A"
        assert out["worst"]["name"] == "B"
        assert out["spread"] == 30.0

    def test_leaves_archived_campaigns_out_of_the_comparison(self):
        """Archiving is the user saying "stop counting this". A verdict built
        on one would recommend a campaign they have already retired."""
        out = campaigns.compare([
            campaigns.summarize(row(id="a", sent=20, replied=8)),
            campaigns.summarize(row(id="b", sent=20, replied=2,
                                    archived_at=now_iso())),
        ])
        assert out["best"] is None


class TestBuild:
    def test_reports_unassigned_rows_rather_than_hiding_them(self):
        """Otherwise the campaign totals read as the whole database, and a
        user with 200 pre-campaign contacts sees "9 contacts"."""
        out = campaigns.build([row(sent=20, replied=4)], {
            "companies": 120, "contacts": 300, "sent": 40, "replied": 6})
        assert out["unassigned"]["contacts"] == 300
        assert out["unassigned"]["sent"] == 40
        assert out["totals"]["sent"] == 20      # campaign totals stay separate

    def test_the_unassigned_pile_obeys_the_same_floor(self):
        thin = campaigns.build([], {"sent": 4, "replied": 2})
        assert thin["unassigned"]["rate"] is None
        fat = campaigns.build([], {"sent": 40, "replied": 6})
        assert fat["unassigned"]["rate"] == 15.0

    def test_counts_active_separately_from_archived(self):
        out = campaigns.build([row(id="a"), row(id="b", archived_at=now_iso())])
        assert out["totals"]["campaigns"] == 2
        assert out["totals"]["active"] == 1

    def test_an_empty_database_is_an_empty_page(self):
        out = campaigns.build([], {})
        assert out["campaigns"] == []
        assert out["unassigned"]["sent"] == 0
        assert out["min_sample"] == MIN_SAMPLE


@pytest.fixture
def client(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        database = Database(os.path.join(tmp, "t.db"))
        monkeypatch.setattr(main, "db", database)
        yield TestClient(main.app), database


def _seed(database, campaign_id=None):
    company = database.create_company(name="ZZTEST Co", url="https://zz.example",
                                      campaign_id=campaign_id)
    contact = database.create_contact(company_id=company["id"], name="ZZ Dana",
                                      email="zzdana@zz.example")
    return company, contact


class TestAttribution:
    def test_a_contact_inherits_its_company_campaign(self, client):
        """One choke point, so a new scraping path cannot create contacts that
        belong to no campaign while their company belongs to one."""
        _, database = client
        campaign = database.create_campaign("ZZTEST run")
        _, contact = _seed(database, campaign["id"])
        assert database.get_contact(contact["id"])["campaign_id"] == campaign["id"]

    def test_an_email_inherits_from_the_contact(self, client):
        _, database = client
        campaign = database.create_campaign("ZZTEST run")
        company, contact = _seed(database, campaign["id"])
        email = database.create_email(company_id=company["id"],
                                      contact_id=contact["id"],
                                      subject="s", body="b")
        assert database.get_email(email["id"])["campaign_id"] == campaign["id"]

    def test_a_force_deleted_contact_takes_its_mail_out_of_the_campaign(self, client):
        """emails.contact_id cascades, which is the app's existing rule for
        force-deleting somebody with sent history. Campaigns do not override
        it — the point of this test is that they do not silently *diverge*
        from it either, leaving a campaign reporting mail whose record the
        rest of the app no longer has."""
        api, database = client
        campaign = database.create_campaign("ZZTEST run")
        company, contact = _seed(database, campaign["id"])
        database.create_email(company_id=company["id"], contact_id=contact["id"],
                              subject="s", body="b", status="sent",
                              sent_at=now_iso(), gmail_message_id="gm1")

        found = next(c for c in api.get("/api/campaigns").json()["campaigns"]
                     if c["id"] == campaign["id"])
        assert found["sent"] == 1

        database.delete_contact(contact["id"])
        found = next(c for c in api.get("/api/campaigns").json()["campaigns"]
                     if c["id"] == campaign["id"])
        assert found["sent"] == 0
        # and the company it belonged to is still attributed
        assert found["companies"] == 1

    def test_rows_created_outside_a_campaign_stay_unassigned(self, client):
        api, database = client
        company, contact = _seed(database)          # no campaign
        database.create_email(company_id=company["id"], contact_id=contact["id"],
                              subject="s", body="b", status="sent",
                              sent_at=now_iso(), gmail_message_id="gm1")
        body = api.get("/api/campaigns").json()
        assert body["unassigned"]["sent"] == 1
        assert body["unassigned"]["contacts"] == 1
        assert body["totals"]["sent"] == 0

    def test_nothing_backfills_history(self, client):
        """Creating a campaign must not adopt rows that predate it — that
        would invent the very fact the page reports."""
        api, database = client
        company, _ = _seed(database)
        database.create_campaign("ZZTEST later run")
        body = api.get("/api/campaigns").json()
        assert body["unassigned"]["companies"] == 1
        assert body["campaigns"][0]["companies"] == 0
        assert database.get_company(company["id"])["campaign_id"] is None


class TestDiscoveryAttribution:
    """The wire between a campaign and the rows it is supposed to contain.

    Nothing exercised it: deleting `campaign_id=campaign_id` from the
    create_company call left every test passing while the feature did nothing
    at all — the campaign row appeared and stayed permanently empty.
    """

    def _run_discovery(self, database, monkeypatch, query, candidates,
                       domain="zzacme.com", local="ada.lovelace"):
        """Drive the real _run with the network stubbed out."""
        import discovery as discovery_module
        service = main.discovery
        monkeypatch.setattr(service, "db", database)
        monkeypatch.setattr(service, "_find_candidates",
                            lambda *a, **k: list(candidates))
        # No DNS. The real selector does an MX lookup by default, which made
        # this test pass or fail on whether a made-up domain happened to
        # resolve — nothing to do with what is being tested.
        real_select = discovery_module.select_outreach_contacts
        monkeypatch.setattr(
            discovery_module, "select_outreach_contacts",
            lambda *a, **kw: real_select(*a, **{**kw, "check_mx": False}))

        class _Enrichment:
            def enrich(self, name, website, **kwargs):
                return {
                    "url": f"https://{domain}", "domain": domain,
                    "summary": "ZZTEST summary", "scraped_at": now_iso(),
                    "pages_scraped": 1, "pages_attempted": 1,
                    "research_quality": "high",
                    # The address has to look like the person for the
                    # selector to treat it as a real contact, which is the
                    # only shape that reaches create_contact.
                    "contacts": [{"name": local.replace(".", " ").title(),
                                  "email": f"{local}@{domain}",
                                  "role": "Engineer"}],
                    "emails": [f"{local}@{domain}"],
                }

        monkeypatch.setattr(service, "enrichment", _Enrichment())
        job = database.create_job("discovery", {"query": query})
        campaign = database.create_campaign(query, query=query, job_id=job["id"])
        service._run(job["id"], query, len(candidates),
                     campaign_id=campaign["id"])
        return campaign["id"]

    def test_a_discovered_company_and_its_contacts_carry_the_campaign(
            self, client, monkeypatch):
        _, database = client
        campaign_id = self._run_discovery(
            database, monkeypatch, "ZZTEST fintech NYC",
            [{"name": "ZZTEST Acme", "domain": "zzacme.com",
              "website": "https://zzacme.com", "reason": "matched"}])

        company = database.find_company_by_name("ZZTEST Acme")
        assert company is not None
        assert company["campaign_id"] == campaign_id
        contacts = database.list_contacts(company_id=company["id"])
        assert contacts and all(c["campaign_id"] == campaign_id for c in contacts)

    def test_a_second_run_credits_new_contacts_to_itself(self, client, monkeypatch):
        """The company keeps its original campaign — the first run found it.
        But people this run discovered belong to this run: inheriting the
        company's campaign credited them to one that never went looking."""
        _, database = client
        first = self._run_discovery(
            database, monkeypatch, "ZZTEST run one",
            [{"name": "ZZTEST Acme", "domain": "zzacme.com",
              "website": "https://zzacme.com"}])
        company = database.find_company_by_name("ZZTEST Acme")
        assert company["campaign_id"] == first

        # A genuinely different display name — what a search actually returns
        # for a company it knows by another brand. Discovery's early guard
        # misses (different name, no domain on the candidate yet), enrich
        # resolves the same domain, and create_company's domain dedupe hands
        # back the row run one already owns.
        second = self._run_discovery(
            database, monkeypatch, "ZZTEST run two",
            [{"name": "ZZTEST Widgets", "website": "https://zzacme.com"}],
            local="grace.hopper")

        assert database.get_company(company["id"])["campaign_id"] == first
        new_contacts = [c for c in database.list_contacts(company_id=company["id"])
                        if c["campaign_id"] == second]
        assert new_contacts, "contacts found by run two were credited elsewhere"


class TestCampaignEndpoint:
    def test_counts_verified_replies_only(self, client):
        api, database = client
        campaign = database.create_campaign("ZZTEST run")
        company, contact = _seed(database, campaign["id"])
        for i, verified in enumerate([True, False]):
            email = database.create_email(
                company_id=company["id"], contact_id=contact["id"], subject="s",
                body="b", status="sent", sent_at=now_iso(),
                gmail_message_id=f"gm{i}")
            updates = {"has_response": True, "response_at": now_iso()}
            if verified:
                updates["response_verified_at"] = now_iso()
            database.update_email(email["id"], updates)

        found = next(c for c in api.get("/api/campaigns").json()["campaigns"]
                     if c["id"] == campaign["id"])
        assert found["replied"] == 1
        assert found["unverified"] == 1

    def test_counts_the_third_form_of_delivered(self, client):
        """A legacy row imported as status='sent' carries neither a Gmail id
        nor a timestamp, and repair_delivered_email_status only backfills rows
        that already have an id — so they survive every startup. The rest of
        the app counts them; this page has to agree."""
        api, database = client
        campaign = database.create_campaign("ZZTEST run")
        company, contact = _seed(database, campaign["id"])
        database.create_email(company_id=company["id"], contact_id=contact["id"],
                              subject="s", body="b", status="sent",
                              sent_at=None, gmail_message_id=None)
        body = api.get("/api/campaigns").json()
        found = next(c for c in body["campaigns"] if c["id"] == campaign["id"])
        assert found["sent"] == 1
        assert found["drafts"] == 0     # and it is not also counted as a draft

        # the same shape outside any campaign. A distinct name, because
        # create_company returns the existing row on a name match — which
        # would have quietly put this back inside the campaign.
        loose_company = database.create_company(name="ZZTEST Loose Co",
                                                url="https://zzloose.example")
        loose_contact = database.create_contact(company_id=loose_company["id"],
                                                name="ZZ Loose",
                                                email="zzloose@zzloose.example")
        assert loose_company["campaign_id"] is None
        database.create_email(company_id=loose_company["id"],
                              contact_id=loose_contact["id"], subject="s",
                              body="b", status="sent", sent_at=None,
                              gmail_message_id=None)
        assert api.get("/api/campaigns").json()["unassigned"]["sent"] == 1

    def test_drafts_and_sent_are_disjoint(self, client):
        api, database = client
        campaign = database.create_campaign("ZZTEST run")
        company, contact = _seed(database, campaign["id"])
        database.create_email(company_id=company["id"], contact_id=contact["id"],
                              subject="draft", body="b", status="draft")
        database.create_email(company_id=company["id"], contact_id=contact["id"],
                              subject="sent", body="b", status="sent",
                              sent_at=now_iso(), gmail_message_id="gm1")
        found = next(c for c in api.get("/api/campaigns").json()["campaigns"]
                     if c["id"] == campaign["id"])
        assert found["drafts"] == 1 and found["sent"] == 1

    def test_reports_bounces_and_the_real_last_send(self, client):
        """Both are rendered on the card. `last_sent_at` falling back to the
        campaign's creation date would have a campaign that never sent
        anything claiming it sent mail."""
        api, database = client
        campaign = database.create_campaign("ZZTEST run")
        company, contact = _seed(database, campaign["id"])
        for stamp, mid in (("2026-01-01T09:00:00", "gm1"),
                           ("2026-06-01T09:00:00", "gm2")):
            database.create_email(company_id=company["id"],
                                  contact_id=contact["id"], subject="s", body="b",
                                  status="sent", sent_at=stamp,
                                  gmail_message_id=mid)
        # A fixed past date, not now_iso(): the campaign is created in the
        # same second, so "the newest send" and "the campaign's created_at"
        # were identical and the substitution this asserts against was
        # invisible.
        dead = database.create_email(company_id=company["id"],
                                     contact_id=contact["id"], subject="s",
                                     body="b", status="sent",
                                     sent_at="2026-07-01T09:00:00",
                                     gmail_message_id="gm3")
        database.update_email(dead["id"], {"bounced_at": now_iso()})

        found = next(c for c in api.get("/api/campaigns").json()["campaigns"]
                     if c["id"] == campaign["id"])
        assert found["bounced"] == 1
        # Exact, not a lower bound: falling back to the campaign's own
        # created_at is *newer* than every send here, so ">=" was satisfied by
        # the very substitution it was meant to catch.
        newest = database.query_one(
            "SELECT MAX(sent_at) AS m FROM emails WHERE campaign_id=?",
            (campaign["id"],))["m"]
        assert found["last_sent_at"] == newest
        assert found["last_sent_at"] != campaign["created_at"]

    def test_a_campaign_that_never_sent_reports_no_last_send(self, client):
        api, database = client
        campaign = database.create_campaign("ZZTEST quiet")
        found = next(c for c in api.get("/api/campaigns").json()["campaigns"]
                     if c["id"] == campaign["id"])
        assert found["last_sent_at"] is None

    def test_only_the_three_editable_fields_can_be_written(self, client):
        """Defence in depth behind the Pydantic model: update_campaign takes a
        dict, and a future caller passing a whole row would otherwise be able
        to rewrite created_at, the query, or the id itself."""
        _, database = client
        campaign = database.create_campaign("ZZTEST run", query="original query")
        database.update_campaign(campaign["id"], {
            "name": "ZZTEST renamed", "query": "rewritten",
            "created_at": "1999-01-01T00:00:00", "id": "hijacked",
        })
        after = database.get_campaign(campaign["id"])
        assert after["name"] == "ZZTEST renamed"
        assert after["query"] == "original query"
        assert after["created_at"] == campaign["created_at"]
        assert after["id"] == campaign["id"]

    def test_counts_contacts_by_their_own_campaign_not_their_company(self, client):
        """The column-versus-join distinction is invisible while the two always
        agree — and they stop agreeing the moment a second run finds people at
        a company an earlier run already owned."""
        api, database = client
        campaign = database.create_campaign("ZZTEST run")
        company = database.create_company(name="ZZTEST Older Co",
                                          url="https://zzold.example")
        assert company["campaign_id"] is None
        database.create_contact(company_id=company["id"], name="ZZ New",
                                email="zznew@zzold.example",
                                campaign_id=campaign["id"])

        found = next(c for c in api.get("/api/campaigns").json()["campaigns"]
                     if c["id"] == campaign["id"])
        assert found["contacts"] == 1
        assert found["companies"] == 0      # the company is still unattributed

    def test_a_new_campaign_starts_active(self, client):
        """Born archived would exclude every campaign from the comparison, so
        no verdict could ever be reached and every card would render dimmed."""
        api, database = client
        created = database.create_campaign("ZZTEST run")
        assert created["archived_at"] is None
        body = api.get("/api/campaigns").json()
        assert body["totals"]["active"] == 1
        assert body["campaigns"][0]["archived_at"] is None

    def test_renames_and_archives_without_deleting(self, client):
        api, database = client
        campaign = database.create_campaign("ZZTEST run")
        assert api.patch(f"/api/campaigns/{campaign['id']}",
                         json={"name": "ZZTEST renamed"}).status_code == 200
        assert database.get_campaign(campaign["id"])["name"] == "ZZTEST renamed"

        api.patch(f"/api/campaigns/{campaign['id']}", json={"archived": True})
        assert database.get_campaign(campaign["id"])["archived_at"]
        api.patch(f"/api/campaigns/{campaign['id']}", json={"archived": False})
        assert database.get_campaign(campaign["id"])["archived_at"] is None

    def test_offers_no_way_to_delete_a_campaign(self, client):
        """The rows pointing at it are real outreach history. Removing the
        label would orphan them into a pile that is supposed to mean
        "predates campaigns", not "someone tidied up"."""
        api, database = client
        campaign = database.create_campaign("ZZTEST run")
        assert api.delete(f"/api/campaigns/{campaign['id']}").status_code == 405

    def test_a_missing_campaign_is_a_404(self, client):
        api, _ = client
        assert api.patch("/api/campaigns/nope", json={"name": "x"}).status_code == 404

    def test_rejects_an_empty_name(self, client):
        api, database = client
        campaign = database.create_campaign("ZZTEST run")
        assert api.patch(f"/api/campaigns/{campaign['id']}",
                         json={"name": ""}).status_code == 422

    def test_a_discovery_run_creates_a_campaign_named_for_its_query(self, client,
                                                                   monkeypatch):
        """Anchored to something that happened. An opt-in tag would be blank on
        exactly the runs worth comparing later."""
        api, database = client
        started = {}

        def _fake_start(query, count, mode="full", campaign_id=None):
            if not campaign_id:
                campaign_id = database.create_campaign(
                    query, query=query, job_id="j1")["id"]
            started["campaign_id"] = campaign_id
            return {**database.create_job("discovery", {"query": query}),
                    "campaign_id": campaign_id}

        monkeypatch.setattr(main.discovery, "start", _fake_start)
        api.post("/api/discovery", json={"query": "ZZTEST seed fintech NYC",
                                         "count": 3})
        campaign = database.get_campaign(started["campaign_id"])
        assert campaign["name"] == "ZZTEST seed fintech NYC"
        assert campaign["query"] == "ZZTEST seed fintech NYC"

    def test_an_unknown_campaign_id_starts_a_new_one_instead_of_failing(
            self, client, monkeypatch):
        """The run is the valuable part. Rejecting it over a stale campaign
        reference would be the wrong trade — but silently writing rows against
        an id with no campaign row would be worse, so it falls back."""
        _, database = client
        monkeypatch.setattr(main.discovery, "db", database)
        # No thread, no network: the guard under test runs before either.
        monkeypatch.setattr(main.discovery, "_run_safe",
                            lambda *a, **k: None)
        monkeypatch.setattr(main.discovery, "_find_candidates",
                            lambda *a, **k: [])

        job = main.discovery.start("ZZTEST query", 1, campaign_id="does-not-exist")
        assert job["campaign_id"] != "does-not-exist"
        assert database.get_campaign(job["campaign_id"]) is not None

    def test_an_existing_campaign_id_is_reused(self, client, monkeypatch):
        _, database = client
        monkeypatch.setattr(main.discovery, "db", database)
        monkeypatch.setattr(main.discovery, "_run_safe", lambda *a, **k: None)
        existing = database.create_campaign("ZZTEST existing")

        job = main.discovery.start("ZZTEST query", 1, campaign_id=existing["id"])
        assert job["campaign_id"] == existing["id"]
        # and no second campaign was created for the same run
        assert len(database.campaign_rows()) == 1
