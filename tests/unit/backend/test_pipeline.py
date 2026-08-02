"""A board is a claim about where everyone stands.

The claim this module has to keep is that a stage is *derived*, never read off
`contacts.status`. That column is written from four places and reset from
none, so the tests below are mostly about the cases where it lies: a contact
marked `drafted` whose draft was deleted, one marked `sent` whose address
bounced, one marked `replied` on a flag the current checker never verified.

Getting those wrong is not a cosmetic bug. A contact filed under a stage that
means "handled" is a person who never gets written to again.
"""
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

import main
import pipeline
from db import Database, now_iso


def row(**over):
    """A contact row in the shape db.pipeline_rows() returns."""
    base = {
        "id": "c1", "name": "Dana", "email": "dana@x.com", "role": "Eng lead",
        "status": "new", "company_id": "co1", "company_name": "Acme",
        "bounced_at": None, "created_at": now_iso(),
        "pending_draft_count": 0, "delivered_count": 0, "bounced_email_count": 0,
        "verified_reply_count": 0, "unverified_reply_count": 0,
        "follow_up_count": 0, "last_sent_at": None,
        "queued_count": 0, "unconfirmed_count": 0,
    }
    base.update(over)
    return base


class TestStageDerivation:
    def test_a_new_contact_with_an_address_is_ready(self):
        assert pipeline.stage_of(row()) == "ready"

    def test_no_address_is_a_stall_not_a_stage_to_skip(self):
        assert pipeline.stage_of(row(email="")) == "no_address"
        assert pipeline.stage_of(row(email=None)) == "no_address"
        assert pipeline.stage_of(row(email="   ")) == "no_address"

    def test_an_unsent_draft_is_drafted(self):
        assert pipeline.stage_of(row(pending_draft_count=1)) == "drafted"

    def test_a_deleted_draft_returns_the_contact_to_ready(self):
        """The bug this module exists to avoid. Deleting a draft leaves
        contacts.status='drafted' forever — grouping on that column parks
        someone under "Drafted" with no draft, and they are never written to
        again precisely because the board says they are handled."""
        assert pipeline.stage_of(
            row(status="drafted", pending_draft_count=0)) == "ready"

    def test_delivered_mail_means_awaiting_whatever_the_column_says(self):
        assert pipeline.stage_of(row(status="new", delivered_count=1)) == "awaiting"

    def test_a_verified_reply_is_replied(self):
        assert pipeline.stage_of(
            row(delivered_count=1, verified_reply_count=1)) == "replied"

    def test_an_unverified_flag_alone_is_not_a_reply(self):
        """The legacy checker counted bounces, auto-replies and our own
        messages. Filing those under Replied would retire a live prospect on
        evidence the app itself refuses to state anywhere else."""
        assert pipeline.stage_of(
            row(status="replied", delivered_count=1,
                unverified_reply_count=1)) == "awaiting"

    def test_a_bounced_address_leaves_the_waiting_column(self):
        """Otherwise a dead address ages quietly beside live prospects, and the
        only visible difference is that it never answers."""
        assert pipeline.stage_of(
            row(delivered_count=1, bounced_at=now_iso())) == "bounced"

    def test_a_bounce_recorded_on_the_email_counts_too(self):
        assert pipeline.stage_of(
            row(delivered_count=1, bounced_email_count=1)) == "bounced"

    def test_a_reply_outranks_a_bounce(self):
        """People do answer from a second address after the first fails — the
        same precedence the reply checker already uses."""
        assert pipeline.stage_of(
            row(delivered_count=2, bounced_email_count=1,
                verified_reply_count=1)) == "replied"

    def test_archived_outranks_everything(self):
        assert pipeline.stage_of(
            row(status="archived", delivered_count=1,
                verified_reply_count=1)) == "archived"
        assert pipeline.stage_of(row(status="ARCHIVED")) == "archived"

    def test_a_draft_with_nowhere_to_send_it_reads_as_blocked(self):
        """"Drafted" would say the work is done and waiting on a click. It is
        not: there is no address, and no click will produce one."""
        assert pipeline.stage_of(
            row(email="", pending_draft_count=1)) == "no_address"

    def test_a_missing_address_outranks_a_past_send(self):
        """An address blanked after a send cannot be chased at all. Filing
        them under "Awaiting reply" says the ball is in their court, and the
        user never discovers they have no way to reach them."""
        assert pipeline.stage_of(row(email="", delivered_count=1)) == "no_address"

    def test_an_unsent_draft_outranks_a_past_delivery(self):
        """Every follow-up this app drafts is written to somebody already
        delivered to. Checking delivery first put the whole follow-up backlog
        under "Waiting on them" and reported the work as zero."""
        assert pipeline.stage_of(
            row(delivered_count=1, pending_draft_count=1)) == "drafted"

    def test_a_queued_send_is_not_ready_to_write(self):
        """It is written and armed. Offering "Ready to write" invites a second
        email to somebody the scheduler is about to mail."""
        assert pipeline.stage_of(row(queued_count=1)) == "drafted"

    def test_an_unconfirmed_send_is_never_offered_as_work(self):
        """Gmail may have queued it, so the recipient may already have it.
        Anything but "already handled" here risks a duplicate."""
        assert pipeline.stage_of(row(unconfirmed_count=1)) == "awaiting"

    def test_non_numeric_counts_do_not_crash_the_board(self):
        assert pipeline.stage_of(row(delivered_count=None)) == "ready"
        assert pipeline.stage_of(row(delivered_count="")) == "ready"


class TestCard:
    def test_marks_an_unverified_reply_without_promoting_it(self):
        entry = pipeline.card(row(delivered_count=1, unverified_reply_count=1))
        assert entry["stage"] == "awaiting"
        assert entry["reply_unverified"] is True

    def test_a_verified_reply_is_not_flagged_unverified(self):
        entry = pipeline.card(row(delivered_count=1, verified_reply_count=1,
                                  unverified_reply_count=1))
        assert entry["stage"] == "replied"
        assert entry["reply_unverified"] is False

    def test_says_when_a_blocked_contact_has_writing_attached(self):
        entry = pipeline.card(row(email="", pending_draft_count=1))
        assert entry["blocked_draft"] is True

    def test_flags_an_unsendable_draft_in_every_stage_it_can_hide_in(self):
        """Gating this on `no_address` meant a card under Bounced or Archived
        with finished writing attached showed nothing at all — the cases where
        an unsendable draft is most thoroughly buried."""
        for extra in ({"bounced_at": now_iso()}, {"status": "archived"},
                      {"verified_reply_count": 1}):
            entry = pipeline.card(row(email="", pending_draft_count=1, **extra))
            assert entry["blocked_draft"] is True, extra

    def test_a_sendable_draft_is_not_flagged_blocked(self):
        assert pipeline.card(row(pending_draft_count=1))["blocked_draft"] is False

    def test_carries_the_queued_and_unconfirmed_counts(self):
        entry = pipeline.card(row(queued_count=2, unconfirmed_count=1))
        assert entry["queued"] == 2 and entry["unconfirmed"] == 1

    def test_falls_back_to_the_address_when_there_is_no_name(self):
        assert pipeline.card(row(name=""))["name"] == "dana@x.com"
        assert pipeline.card(row(name="", email=""))["name"] == "Unnamed contact"

    def test_carries_no_email_body_or_subject(self):
        """A board is a list of people, not of correspondence. Nothing here
        should be shipping message text to the browser."""
        entry = pipeline.card(row())
        assert not any(k in entry for k in ("body", "subject", "text"))


class TestBuild:
    def test_files_every_contact_under_exactly_one_stage(self):
        rows = [row(id="a"), row(id="b", pending_draft_count=1),
                row(id="c", delivered_count=1),
                row(id="d", delivered_count=1, verified_reply_count=1)]
        out = pipeline.build(rows)
        found = {c["id"]: s["key"] for s in out["stages"] for c in s["cards"]}
        assert found == {"a": "ready", "b": "drafted", "c": "awaiting",
                         "d": "replied"}
        assert sum(s["total"] for s in out["stages"]) == 4

    def test_reports_every_stage_even_when_empty(self):
        """An empty column is information — "nothing is waiting on you" — and
        a board that hides it makes the user count the ones that are there."""
        out = pipeline.build([])
        assert [s["key"] for s in out["stages"]] == pipeline.STAGE_KEYS
        assert all(s["total"] == 0 for s in out["stages"])

    def test_bounds_each_column_separately(self):
        """A thousand contacts in `ready` must not stop four cards in
        `drafted` from rendering."""
        rows = ([row(id=f"r{i}") for i in range(300)]
                + [row(id=f"d{i}", pending_draft_count=1) for i in range(4)])
        out = pipeline.build(rows, limit=100)
        ready = next(s for s in out["stages"] if s["key"] == "ready")
        drafted = next(s for s in out["stages"] if s["key"] == "drafted")
        assert len(ready["cards"]) == 100 and ready["hidden"] == 200
        assert ready["total"] == 300
        assert len(drafted["cards"]) == 4 and drafted["hidden"] == 0

    def test_counts_the_two_numbers_the_board_exists_for(self):
        """Every stage the tallies exclude is present, so widening either one
        fails rather than merely going unnoticed."""
        rows = [
            row(id="a"),                                    # ready
            row(id="b", pending_draft_count=1),             # drafted
            row(id="c", delivered_count=1),                 # awaiting
            row(id="d", delivered_count=1),                 # awaiting
            row(id="e", email=""),                          # no_address
            row(id="f", delivered_count=1, verified_reply_count=1),   # replied
            row(id="g", status="archived"),                 # archived
            row(id="h", delivered_count=1, bounced_at=now_iso()),     # bounced
        ]
        out = pipeline.build(rows)
        assert out["waiting_on_you"] == 2       # a and b only
        assert out["waiting_on_them"] == 2      # c and d only

    def test_a_bounced_contact_is_waiting_on_nobody(self):
        out = pipeline.build([row(delivered_count=1, bounced_at=now_iso())])
        assert out["waiting_on_them"] == 0
        assert out["waiting_on_you"] == 0

    def test_a_drafted_follow_up_is_work_waiting_on_you(self):
        """The number this board leads with. Before the fix a drafted follow-up
        sat in `awaiting`, so drafting twenty of them moved it by zero."""
        out = pipeline.build([row(delivered_count=1, pending_draft_count=1)])
        assert out["waiting_on_you"] == 1
        assert out["waiting_on_them"] == 0

    def test_a_queued_send_is_waiting_on_nobody(self):
        """The scheduler will send it unattended; the recipient does not have
        it yet. Counting it either way overstates one side."""
        out = pipeline.build([row(queued_count=1)])
        assert out["waiting_on_you"] == 0
        assert out["waiting_on_them"] == 0
        assert out["queued"] == 1

    def test_a_queued_send_beside_a_real_draft_still_counts_once(self):
        out = pipeline.build([row(queued_count=1, pending_draft_count=1)])
        assert out["waiting_on_you"] == 1

    def test_counts_how_far_the_stored_column_has_drifted(self):
        rows = [
            row(id="a", status="drafted", pending_draft_count=0),   # drift
            row(id="b", status="sent", delivered_count=1),          # agrees
            row(id="c", status="replied", delivered_count=1,
                unverified_reply_count=1),                          # drift
            row(id="d", status="new"),                              # agrees
        ]
        assert pipeline.build(rows)["status_drift"] == 2

    def test_an_unknown_stored_status_is_not_counted_as_drift(self):
        """Only a value this module can translate can be said to disagree."""
        assert pipeline.build([row(status="something_else")])["status_drift"] == 0

    def test_a_stage_the_column_cannot_express_is_not_drift(self):
        """contacts.status has no word for "no address" or "bounced", so every
        such contact registered as a disagreement — turning the drift banner,
        which claims the stored column is wrong, into a count of how many
        people had no email address."""
        rows = [
            row(id="a", status="new", email=""),                    # correct
            row(id="b", status="sent", delivered_count=1,
                bounced_at=now_iso()),                              # correct
            row(id="c", status="drafted", email="",
                pending_draft_count=1),                             # correct
        ]
        assert pipeline.build(rows)["status_drift"] == 0

    def test_a_stage_the_column_can_express_still_registers(self):
        """The compatibility list must not swallow real disagreement."""
        assert pipeline.build(
            [row(status="replied", email="")])["status_drift"] == 1

    def test_puts_the_most_recent_activity_first(self):
        rows = [row(id="old", delivered_count=1, last_sent_at="2020-01-01T00:00:00",
                    name="Zed"),
                row(id="new", delivered_count=1, last_sent_at="2026-01-01T00:00:00",
                    name="Amy")]
        out = pipeline.build(rows)
        awaiting = next(s for s in out["stages"] if s["key"] == "awaiting")
        assert [c["id"] for c in awaiting["cards"]] == ["new", "old"]

    def test_never_contacted_cards_sort_last_and_alphabetically(self):
        """last_sent_at is None before the first send and cannot be compared
        against a timestamp at all — ordering them by accident would bury the
        contact written to yesterday under three hundred untouched ones."""
        rows = [row(id="zed", name="Zed"), row(id="amy", name="Amy"),
                row(id="sent", name="Bo", last_sent_at="2026-01-01T00:00:00")]
        out = pipeline.build(rows)
        # all three are `ready` here — none has a delivered email
        ready = next(s for s in out["stages"] if s["key"] == "ready")
        assert [c["id"] for c in ready["cards"]] == ["sent", "amy", "zed"]


@pytest.fixture
def client(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        database = Database(os.path.join(tmp, "t.db"))
        monkeypatch.setattr(main, "db", database)
        yield TestClient(main.app), database


class TestPipelineEndpoint:
    def test_derives_a_stage_from_the_emails_not_the_column(self, client):
        """End to end against real SQL: the contact says `drafted`, the draft
        is gone, and the board has to say Ready."""
        api, database = client
        company = database.create_company(name="ZZTEST Co", url="https://zz.example")
        contact = database.create_contact(company_id=company["id"], name="ZZ Dana",
                                          email="zzdana@zz.example")
        database.update_contact(contact["id"], {"status": "drafted"})

        body = api.get("/api/pipeline").json()
        stage = {c["id"]: s["key"] for s in body["stages"] for c in s["cards"]}
        assert stage[contact["id"]] == "ready"
        assert body["status_drift"] == 1

    def test_a_delivered_email_moves_the_contact_to_awaiting(self, client):
        api, database = client
        company = database.create_company(name="ZZTEST Co", url="https://zz.example")
        contact = database.create_contact(company_id=company["id"], name="ZZ Dana",
                                          email="zzdana@zz.example")
        database.create_email(company_id=company["id"], contact_id=contact["id"],
                              subject="s", body="b", status="sent",
                              sent_at=now_iso(), gmail_message_id="gm1")
        body = api.get("/api/pipeline").json()
        stage = {c["id"]: s["key"] for s in body["stages"] for c in s["cards"]}
        assert stage[contact["id"]] == "awaiting"
        assert body["waiting_on_them"] == 1

    def test_a_legacy_row_labelled_approved_still_counts_as_delivered(self, client):
        """Rows carrying a Gmail id while labelled approved are treated as
        delivered everywhere else in the app; a board that called them drafts
        would offer to write to someone who already has the email."""
        api, database = client
        company = database.create_company(name="ZZTEST Co", url="https://zz.example")
        contact = database.create_contact(company_id=company["id"], name="ZZ L",
                                          email="zzl@zz.example")
        database.create_email(company_id=company["id"], contact_id=contact["id"],
                              subject="s", body="b", status="approved",
                              gmail_message_id="gm2")
        body = api.get("/api/pipeline").json()
        stage = {c["id"]: s["key"] for s in body["stages"] for c in s["cards"]}
        assert stage[contact["id"]] == "awaiting"

    def test_a_trashed_draft_does_not_count_as_pending(self, client):
        api, database = client
        company = database.create_company(name="ZZTEST Co", url="https://zz.example")
        contact = database.create_contact(company_id=company["id"], name="ZZ T",
                                          email="zzt@zz.example")
        database.create_email(company_id=company["id"], contact_id=contact["id"],
                              subject="s", body="b", status="trashed")
        body = api.get("/api/pipeline").json()
        stage = {c["id"]: s["key"] for s in body["stages"] for c in s["cards"]}
        assert stage[contact["id"]] == "ready"

    def test_reading_the_board_repairs_nothing(self, client):
        """It can prove the column wrong and still must not rewrite it —
        opening a page is not a mutation."""
        api, database = client
        company = database.create_company(name="ZZTEST Co", url="https://zz.example")
        contact = database.create_contact(company_id=company["id"], name="ZZ D",
                                          email="zzd@zz.example")
        database.update_contact(contact["id"], {"status": "drafted"})
        api.get("/api/pipeline")
        assert database.get_contact(contact["id"])["status"] == "drafted"

    def test_an_empty_database_is_an_empty_board_not_an_error(self, client):
        api, _ = client
        body = api.get("/api/pipeline").json()
        assert body["total"] == 0
        assert len(body["stages"]) == len(pipeline.STAGE_KEYS)

    def test_the_limit_is_bounded_by_the_route(self, client):
        api, _ = client
        assert api.get("/api/pipeline", params={"limit": 5000}).status_code == 422
        assert api.get("/api/pipeline", params={"limit": 0}).status_code == 422

    def test_the_limit_actually_reaches_the_board(self, client):
        """Asserting only that bad values are rejected left the parameter
        inert: dropping it from the call passed the whole suite while every
        card in the book was serialised to the browser on each page load."""
        api, database = client
        company = database.create_company(name="ZZTEST Co", url="https://zz.example")
        for i in range(30):
            database.create_contact(company_id=company["id"], name=f"ZZ {i:02d}",
                                    email=f"zz{i}@zz.example")
        body = api.get("/api/pipeline", params={"limit": 10}).json()
        ready = next(s for s in body["stages"] if s["key"] == "ready")
        assert ready["total"] == 30
        assert len(ready["cards"]) == 10
        assert ready["hidden"] == 20

    def test_a_contact_with_no_company_still_appears(self, client):
        """contacts.company_id is nullable, and turning the LEFT JOIN into an
        inner one would drop those rows from a board whose whole claim is that
        it shows everyone."""
        api, database = client
        contact = database.create_contact(company_id=None, name="ZZ Orphan",
                                          email="zzorphan@zz.example")
        body = api.get("/api/pipeline").json()
        cards = {c["id"]: c for s in body["stages"] for c in s["cards"]}
        assert contact["id"] in cards
        assert cards[contact["id"]]["company_name"] == ""
        assert body["total"] == 1

    def test_separates_a_verified_reply_from_an_unverified_flag(self, client):
        """Through real SQL, because the two subqueries differ by one clause
        and swapping them is invisible to any test that builds rows by hand."""
        api, database = client
        company = database.create_company(name="ZZTEST Co", url="https://zz.example")

        def _contact(name, updates):
            contact = database.create_contact(company_id=company["id"], name=name,
                                              email=f"{name.lower()}@zz.example")
            email = database.create_email(
                company_id=company["id"], contact_id=contact["id"], subject="s",
                body="b", status="sent", sent_at=now_iso(), gmail_message_id=name)
            database.update_email(email["id"], updates)
            return contact["id"]

        legacy = _contact("zzlegacy", {"has_response": True, "response_at": now_iso()})
        real = _contact("zzreal", {"has_response": True, "response_at": now_iso(),
                                   "response_verified_at": now_iso()})

        body = api.get("/api/pipeline").json()
        stage = {c["id"]: s["key"] for s in body["stages"] for c in s["cards"]}
        cards = {c["id"]: c for s in body["stages"] for c in s["cards"]}
        assert stage[legacy] == "awaiting"
        assert cards[legacy]["reply_unverified"] is True
        assert stage[real] == "replied"
        assert cards[real]["reply_unverified"] is False

    def test_counts_only_follow_ups_that_actually_went_out(self, client):
        api, database = client
        company = database.create_company(name="ZZTEST Co", url="https://zz.example")
        contact = database.create_contact(company_id=company["id"], name="ZZ F",
                                          email="zzf@zz.example")
        first = database.create_email(
            company_id=company["id"], contact_id=contact["id"], subject="s",
            body="b", status="sent", sent_at=now_iso(), gmail_message_id="gm1")
        database.create_email(
            company_id=company["id"], contact_id=contact["id"], subject="s2",
            body="b2", status="sent", sent_at=now_iso(), gmail_message_id="gm2",
            is_follow_up=True, original_email_id=first["id"])
        # a second follow-up, drafted but never sent — not a chase yet
        database.create_email(
            company_id=company["id"], contact_id=contact["id"], subject="s3",
            body="b3", status="draft", is_follow_up=True,
            original_email_id=first["id"])

        cards = {c["id"]: c for s in api.get("/api/pipeline").json()["stages"]
                 for c in s["cards"]}
        assert cards[contact["id"]]["follow_ups_sent"] == 1
        assert cards[contact["id"]]["drafts"] == 1
        assert cards[contact["id"]]["stage"] == "drafted"

    def test_last_sent_at_is_the_most_recent_send(self, client):
        """MIN instead of MAX would sort a contact chased three times by their
        first contact, and NULL would drop every card to the bottom."""
        api, database = client
        company = database.create_company(name="ZZTEST Co", url="https://zz.example")
        contact = database.create_contact(company_id=company["id"], name="ZZ M",
                                          email="zzm@zz.example")
        for stamp, mid in (("2026-01-01T09:00:00", "gm1"),
                           ("2026-06-01T09:00:00", "gm2")):
            database.create_email(company_id=company["id"], contact_id=contact["id"],
                                  subject="s", body="b", status="sent",
                                  sent_at=stamp, gmail_message_id=mid)
        cards = {c["id"]: c for s in api.get("/api/pipeline").json()["stages"]
                 for c in s["cards"]}
        assert cards[contact["id"]]["last_sent_at"] == "2026-06-01T09:00:00"

    def test_a_legacy_sent_row_with_no_date_still_counts_as_delivered(self, client):
        """migrate_legacy_data imports `"status": "sent"` with no timestamp and
        no Gmail id, and repair_delivered_email_status only backfills rows that
        already have an id — so these survive every startup. Every other
        surface treats them as delivered; a board that did not was the one
        place telling the user to write a first email to somebody who already
        received one."""
        api, database = client
        company = database.create_company(name="ZZTEST Co", url="https://zz.example")
        contact = database.create_contact(company_id=company["id"], name="ZZ Legacy",
                                          email="zzlegacy2@zz.example")
        first = database.create_email(
            company_id=company["id"], contact_id=contact["id"], subject="s",
            body="b", status="sent", sent_at=None, gmail_message_id=None)
        database.create_email(
            company_id=company["id"], contact_id=contact["id"], subject="s2",
            body="b2", status="sent", sent_at=None, gmail_message_id=None,
            is_follow_up=True, original_email_id=first["id"])

        body = api.get("/api/pipeline").json()
        cards = {c["id"]: c for s in body["stages"] for c in s["cards"]}
        entry = cards[contact["id"]]
        assert entry["stage"] == "awaiting"
        assert entry["sent"] == 2
        assert entry["follow_ups_sent"] == 1
        assert body["waiting_on_you"] == 0

    def test_a_bounce_at_an_old_address_does_not_condemn_the_new_one(self, client):
        """A bounce is a fact about an address, not about a person — the rule
        the send path and the follow-up query already follow. Without the same
        scoping, giving somebody a working address left them in Bounced for
        good."""
        api, database = client
        company = database.create_company(name="ZZTEST Co", url="https://zz.example")
        contact = database.create_contact(company_id=company["id"], name="ZZ B",
                                          email="zzold@zz.example")
        email = database.create_email(
            company_id=company["id"], contact_id=contact["id"], subject="s",
            body="b", status="sent", sent_at=now_iso(), gmail_message_id="gm1",
            recipient_email="zzold@zz.example")
        database.update_email(email["id"], {"bounced_at": now_iso()})

        stage = {c["id"]: s["key"] for s in api.get("/api/pipeline").json()["stages"]
                 for c in s["cards"]}
        assert stage[contact["id"]] == "bounced"

        # a working address for the same person — the old bounce is not theirs
        database.update_contact(contact["id"], {"email": "zznew@zz.example"})
        stage = {c["id"]: s["key"] for s in api.get("/api/pipeline").json()["stages"]
                 for c in s["cards"]}
        assert stage[contact["id"]] == "awaiting"

    def test_a_queued_send_is_not_offered_as_work(self, client):
        """The scheduler sends it unattended. "Ready to write" here invites a
        second email to somebody who is about to receive the first."""
        api, database = client
        company = database.create_company(name="ZZTEST Co", url="https://zz.example")
        contact = database.create_contact(company_id=company["id"], name="ZZ Q",
                                          email="zzq@zz.example")
        database.create_email(
            company_id=company["id"], contact_id=contact["id"], subject="s",
            body="b", status="approved", scheduled_at="2099-01-01T09:00:00",
            scheduled_by_job="job1")

        body = api.get("/api/pipeline").json()
        cards = {c["id"]: c for s in body["stages"] for c in s["cards"]}
        assert cards[contact["id"]]["stage"] == "drafted"
        assert cards[contact["id"]]["queued"] == 1
        assert cards[contact["id"]]["drafts"] == 0
        assert body["waiting_on_you"] == 0
        assert body["queued"] == 1

    def test_an_in_flight_send_is_never_offered_as_work(self, client):
        """send_attempted_at with no verdict means Gmail may already have
        delivered it. Anything that reads as "still to write" risks a
        duplicate to a real person."""
        api, database = client
        company = database.create_company(name="ZZTEST Co", url="https://zz.example")
        contact = database.create_contact(company_id=company["id"], name="ZZ U",
                                          email="zzu@zz.example")
        email = database.create_email(
            company_id=company["id"], contact_id=contact["id"], subject="s",
            body="b", status="approved")
        database.update_email(email["id"], {"send_attempted_at": now_iso()})

        body = api.get("/api/pipeline").json()
        cards = {c["id"]: c for s in body["stages"] for c in s["cards"]}
        assert cards[contact["id"]]["stage"] == "awaiting"
        assert cards[contact["id"]]["unconfirmed"] == 1
        assert body["waiting_on_you"] == 0
