"""The list of people this app must never write to.

Everything else in this app is deliberately optimistic. `domain_has_mx`
returning None means "could not check" and the send proceeds, because a DNS
blip must not block real mail. This is the one check that goes the other way:
a wrongly blocked email costs an error message, and a wrongly sent one costs a
person who asked to be left alone hearing from you again.

So the tests are weighted to the send path. Ingest and generation refusals are
convenience — they stop wasted quota and confusing drafts. The gate inside
`_send_batch_job` is the guarantee, and it is the one that has to hold against
rows that predate the list, CSVs, and drafts written a week before somebody
asked to be removed.
"""
import itertools
import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import main
import suppression
from db import Database, now_iso


def entry(value, kind, **over):
    base = {"id": "s1", "value": value, "kind": kind, "reason": None,
            "source": "manual", "created_at": now_iso()}
    base.update(over)
    return base


class TestNormalize:
    def test_reads_the_forms_people_actually_type(self):
        assert suppression.normalize("dana@acme.com") == ("dana@acme.com", "address")
        assert suppression.normalize("acme.com") == ("acme.com", "domain")
        assert suppression.normalize("@acme.com") == ("acme.com", "domain")

    def test_lowercases_and_trims(self):
        assert suppression.normalize("  Dana@ACME.com  ") == ("dana@acme.com", "address")

    def test_reads_a_pasted_display_name(self):
        """"Dana Lee <dana@acme.com>" must not become a domain entry that
        blocks the whole company."""
        assert suppression.normalize('Dana Lee <dana@acme.com>') == (
            "dana@acme.com", "address")

    def test_rejects_what_is_neither(self):
        for junk in ("", "   ", "not an address", "dana@", "@", "acme",
                     "dana@@acme.com", None):
            assert suppression.normalize(junk) == (None, None), junk

    def test_absorbs_what_copying_an_address_leaves_behind(self):
        """`mailto:` is what "copy link address" gives you; a trailing period
        or comma is what copying out of a sentence or a list gives you. All
        three were accepted verbatim and could never equal a real recipient —
        so the entry sat in the list looking like protection while that person
        kept getting mail."""
        for raw in ("mailto:dana@acme.com", "dana@acme.com.", "dana@acme.com,",
                    "dana@acme.com;", "<dana@acme.com>", "(dana@acme.com)"):
            assert suppression.normalize(raw) == ("dana@acme.com", "address"), raw

    def test_refuses_a_paste_naming_more_than_one_person(self):
        """Selecting two recipients in Gmail and copying gives exactly this.
        Taking the last one accepted it with a 200, showed a single row in
        Settings, and left the first person mailable — while the hand-typed
        comma form was already rejected outright. The form people actually
        paste was the one that failed quietly."""
        for raw in ("Dana Lee <dana@acme.com>, Sam Ray <sam@acme.com>",
                    "Dana <dana@acme.com>; Sam <sam@acme.com>",
                    "Sam <sam@acme.com> and Dana <dana@acme.com>",
                    "dana@acme.com <sam@acme.com>",
                    "dana@acme.com, sam@acme.com"):
            assert suppression.normalize(raw) == (None, None), raw

    def test_still_reads_one_recipient_repeated(self):
        """The same address twice is one person, not two."""
        assert suppression.normalize(
            "Dana <dana@acme.com>, Dana Lee <dana@acme.com>") == (
                "dana@acme.com", "address")

    def test_reads_a_display_name_paste_with_a_trailing_separator(self):
        for raw in ("Dana Lee <dana@acme.com>,", "Dana Lee <dana@acme.com>;",
                    "Dana Lee <dana@acme.com>."):
            assert suppression.normalize(raw) == ("dana@acme.com", "address"), raw

    def test_reads_a_wildcard_as_the_whole_domain(self):
        for raw in ("*@acme.com", "*.acme.com", "@acme.com"):
            assert suppression.normalize(raw) == ("acme.com", "domain"), raw

    def test_refuses_an_address_the_send_path_could_never_emit(self):
        """Bounded to the same shape email_sender allows out the door. An
        entry no recipient can equal is not a harmless no-op — it is somebody
        who believes they are on the list and is not."""
        for junk in ("da na@acme.com", "dana@acme", "dana@.com", "d*na@acme.com",
                     "dana@acme..com"):
            assert suppression.normalize(junk) == (None, None), junk

    def test_holds_everything_the_send_path_can_emit(self):
        """The invariant, checked by sweeping the sender's own alphabet rather
        than by listing cases.

        Both directions of getting this wrong put mail in somebody's inbox.
        Too loose and an entry matches no possible recipient. Too strict and
        there is an address the sender delivers to that the list cannot hold —
        a first attempt rejected leading-hyphen domains as malformed, which
        made dana@-acme.com sendable and unblockable. Tidiness that creates an
        address you cannot block is backwards for this feature."""
        from email_sender import _RECIPIENT_RE
        alphabet = "ad0._%+-"
        domains = ("a.co", "-a.co", "a-.co", "a.b-c.co", "--.co",
                   "sub.acme.co.uk")
        holes = []
        for size in range(1, 4):
            for local in itertools.product(alphabet, repeat=size):
                for domain in domains:
                    address = "".join(local) + "@" + domain
                    if not _RECIPIENT_RE.fullmatch(address):
                        continue
                    if suppression.normalize(address) != (address, "address"):
                        holes.append(address)
        assert not holes, f"sendable but unblockable: {holes[:5]}"

    def test_ingest_and_send_agree_on_what_an_address_is(self):
        """The strict single-recipient pattern exists in three modules.

        `models.EMAIL_ADDRESS_RE` guards ingest, `email_sender._RECIPIENT_RE`
        guards the send path, and `suppression._SENDABLE` guards the block
        list — each deliberately re-stated rather than imported across the send
        boundary. Only two of the three were held together by a test, so the
        ingest copy could drift and start accepting an address the sender
        refuses (or worse, the reverse). Same sweep as above, both directions.
        """
        import models
        from email_sender import _RECIPIENT_RE
        alphabet = "ad0._%+-"
        domains = ("a.co", "-a.co", "a-.co", "a.b-c.co", "--.co",
                   "sub.acme.co.uk")
        disagreements = []
        for size in range(1, 4):
            for local in itertools.product(alphabet, repeat=size):
                for domain in domains:
                    address = "".join(local) + "@" + domain
                    if bool(_RECIPIENT_RE.fullmatch(address)) != bool(
                            models.EMAIL_ADDRESS_RE.fullmatch(address)):
                        disagreements.append(address)
        assert not disagreements, (
            f"ingest and send disagree on: {disagreements[:5]}")

    def test_stripping_never_widens_an_address_into_a_domain(self):
        """`.@acme.com` is absurd but sendable. Stripping punctuation
        unconditionally turned it into a block on the whole of acme.com —
        trading an unblockable address for an over-block, which is worse."""
        assert suppression.normalize(".@acme.com") == (".@acme.com", "address")


class TestMatch:
    def test_matches_an_exact_address(self):
        assert suppression.match("dana@acme.com", [entry("dana@acme.com", "address")])

    def test_does_not_match_a_different_address_at_the_same_domain(self):
        assert suppression.match(
            "bob@acme.com", [entry("dana@acme.com", "address")]) is None

    def test_a_domain_entry_covers_everyone_there(self):
        block = [entry("acme.com", "domain")]
        assert suppression.match("dana@acme.com", block)
        assert suppression.match("careers@acme.com", block)

    def test_a_domain_entry_covers_subdomains(self):
        """Blocking acme.com and then mailing careers.mail.acme.com would be
        an obvious hole."""
        block = [entry("acme.com", "domain")]
        assert suppression.match("dana@mail.acme.com", block)
        assert suppression.match("dana@careers.mail.acme.com", block)

    def test_a_domain_entry_does_not_match_a_lookalike(self):
        """`endswith` would block notacme.com and evil-acme.com. A false block
        erodes trust in the list until someone switches it off."""
        block = [entry("acme.com", "domain")]
        assert suppression.match("dana@notacme.com", block) is None
        assert suppression.match("dana@evil-acme.com", block) is None
        assert suppression.match("dana@acme.com.evil.net", block) is None

    def test_a_subdomain_entry_does_not_block_the_parent(self):
        assert suppression.match(
            "dana@acme.com", [entry("mail.acme.com", "domain")]) is None

    def test_is_case_insensitive_both_ways(self):
        assert suppression.match("Dana@ACME.com", [entry("dana@acme.com", "address")])
        assert suppression.match("dana@acme.com", [entry("DANA@ACME.COM", "address")])

    def test_an_empty_list_blocks_nothing(self):
        assert suppression.match("dana@acme.com", []) is None
        assert suppression.match("dana@acme.com", None) is None

    def test_junk_input_matches_nothing_rather_than_raising(self):
        block = [entry("acme.com", "domain")]
        # "@acme.com" is deliberately absent: it is malformed, but its domain
        # is blocked, and matching is the safe direction for this one check.
        for junk in ("", None, "not an address", "dana", "@"):
            assert suppression.match(junk, block) is None, junk

    def test_a_tagged_address_is_the_same_mailbox(self):
        """dana+jobs@acme.com delivers to dana@acme.com almost everywhere —
        the same human, the same message. Blocking one and mailing the other
        means the person who asked you to stop hears from you again."""
        block = [entry("dana@acme.com", "address")]
        assert suppression.match("dana+jobs@acme.com", block)
        assert suppression.match("dana+anything.else@acme.com", block)

    def test_suppressing_a_tagged_address_covers_the_bare_one(self):
        block = [entry("dana+jobs@acme.com", "address")]
        assert suppression.match("dana@acme.com", block)

    def test_gmail_ignores_dots_and_so_does_this(self):
        block = [entry("dana@gmail.com", "address")]
        assert suppression.match("d.a.n.a@gmail.com", block)

    def test_googlemail_is_the_same_inbox_as_gmail(self):
        """Google issued googlemail.com in Germany, the UK and Russia, and the
        two remain interchangeable — so blocking one and mailing the other
        reaches the same person at an address they never gave you."""
        block = [entry("dana@gmail.com", "address")]
        assert suppression.match("dana@googlemail.com", block)
        assert suppression.match("d.a.n.a@googlemail.com", block)
        assert suppression.match("dana+jobs@googlemail.com", block)
        # and the other way round
        assert suppression.match(
            "dana@gmail.com", [entry("dana@googlemail.com", "address")])

    def test_an_all_tag_local_part_does_not_cross_block(self):
        """Folding `+tag@acme.com` to `@acme.com` collapsed every such address
        at a domain onto one key, blocking unrelated people."""
        block = [entry("+jobs@acme.com", "address")]
        assert suppression.match("+jobs@acme.com", block)
        assert suppression.match("+other@acme.com", block) is None
        assert suppression.match("dana@acme.com", block) is None

    def test_dots_are_not_folded_anywhere_else(self):
        """j.smith@acme.com and jsmith@acme.com are different people at a
        normal domain. Folding everywhere would block the wrong person."""
        block = [entry("jsmith@acme.com", "address")]
        assert suppression.match("j.smith@acme.com", block) is None

    def test_returns_the_entry_so_the_caller_can_say_why(self):
        found = suppression.match("dana@acme.com", [entry("acme.com", "domain")])
        assert found["kind"] == "domain" and found["value"] == "acme.com"


class TestBlockedReason:
    def test_names_the_entry_that_did_it(self):
        text = suppression.blocked_reason(
            entry("acme.com", "domain", created_at="2026-03-04T10:00:00",
                  reason="asked us to stop"), "dana@acme.com")
        assert "dana@acme.com" in text
        assert "@acme.com" in text
        assert "2026-03-04" in text
        assert "asked us to stop" in text
        assert "Settings" in text

    def test_never_a_bare_blocked(self):
        """The user has to be able to find and undo it — and a domain entry
        blocking one address is the case they will not expect."""
        text = suppression.blocked_reason(entry("dana@acme.com", "address"),
                                          "dana@acme.com")
        assert text and "do-not-contact" in text


@pytest.fixture
def client(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        database = Database(os.path.join(tmp, "t.db"))
        monkeypatch.setattr(main, "db", database)
        monkeypatch.setattr(main.generation, "db", database, raising=False)
        yield TestClient(main.app), database


def _contact(database, email, name="ZZ Dana"):
    company = database.create_company(name=f"ZZTEST {email}",
                                      url=f"https://{email.split('@')[-1]}")
    return company, database.create_contact(company_id=company["id"], name=name,
                                            email=email)


class TestSuppressionRoutes:
    def test_adds_and_lists(self, client):
        api, database = client
        response = api.post("/api/suppressions",
                            json={"value": "Dana@ACME.com", "reason": "asked to stop"})
        assert response.status_code == 200
        assert response.json()["value"] == "dana@acme.com"
        assert response.json()["kind"] == "address"
        assert len(api.get("/api/suppressions").json()) == 1

    def test_adding_twice_keeps_the_first_reason_and_date(self, client):
        """The refusal message quotes the entry back, so overwriting the
        original reason with a later blank loses the only record of why
        somebody is on the list."""
        api, _ = client
        first = api.post("/api/suppressions",
                         json={"value": "dana@acme.com", "reason": "asked to stop"}).json()
        again = api.post("/api/suppressions", json={"value": "dana@acme.com"}).json()
        assert again["id"] == first["id"]
        assert again["reason"] == "asked to stop"
        assert len(api.get("/api/suppressions").json()) == 1

    def test_rejects_what_is_not_an_address_or_domain(self, client):
        api, _ = client
        assert api.post("/api/suppressions",
                        json={"value": "not an address"}).status_code == 422

    def test_says_how_many_contacts_it_already_covers(self, client):
        """Otherwise the user learns a week later, by a send being refused."""
        api, database = client
        _contact(database, "dana@acme.com")
        _contact(database, "bob@acme.com", name="ZZ Bob")
        _contact(database, "eve@other.example", name="ZZ Eve")
        body = api.post("/api/suppressions", json={"value": "acme.com"}).json()
        assert body["matched_contacts"] == 2

    def test_removes(self, client):
        api, _ = client
        added = api.post("/api/suppressions", json={"value": "dana@acme.com"}).json()
        assert api.delete(f"/api/suppressions/{added['id']}").status_code == 200
        assert api.get("/api/suppressions").json() == []

    def test_removing_something_absent_is_a_404(self, client):
        api, _ = client
        assert api.delete("/api/suppressions/nope").status_code == 404


class TestIngestGate:
    def test_refuses_a_suppressed_address_on_manual_add(self, client):
        api, database = client
        api.post("/api/suppressions", json={"value": "dana@acme.com"})
        company = database.create_company(name="ZZTEST Co", url="https://acme.com")
        response = api.post("/api/contacts", json={
            "company_id": company["id"], "name": "Dana Lovelace",
            "email": "dana@acme.com"})
        assert response.status_code == 400
        assert "do-not-contact" in response.json()["detail"]

    def test_a_domain_entry_refuses_the_whole_company(self, client):
        api, database = client
        api.post("/api/suppressions", json={"value": "acme.com"})
        company = database.create_company(name="ZZTEST Co", url="https://acme.com")
        response = api.post("/api/contacts", json={
            "company_id": company["id"], "name": "Bob Lovelace",
            "email": "bob.lovelace@acme.com"})
        assert response.status_code == 400

    def test_refuses_a_suppressed_address_in_a_csv_import(self, client):
        """Named in the module docstring as a reason the check exists — CSVs
        arrive with addresses already in them."""
        api, database = client
        api.post("/api/suppressions", json={"value": "dana@acme.com"})
        csv = ("name,company,email\n"
               "Dana Lovelace,ZZTEST Acme,dana@acme.com\n"
               "Eve Lovelace,ZZTEST Other,eve.lovelace@other.example\n")
        response = api.post("/api/contacts/import",
                            files={"file": ("c.csv", csv, "text/csv")})
        assert response.status_code == 200
        body = response.json()
        assert body["invalid"] == 1
        assert any("suppressed" in sample for sample in body.get("invalid_samples", []))
        assert body["added"] == 1

    def test_an_unsuppressed_address_still_goes_in(self, client):
        api, database = client
        api.post("/api/suppressions", json={"value": "acme.com"})
        company = database.create_company(name="ZZTEST Co", url="https://other.example")
        response = api.post("/api/contacts", json={
            "company_id": company["id"], "name": "Eve Lovelace",
            "email": "eve.lovelace@other.example"})
        assert response.status_code == 200


class TestSendGate:
    """The guarantee. Everything above is convenience."""

    def _sendable(self, database, email_addr, **over):
        company, contact = _contact(database, email_addr)
        data = dict(company_id=company["id"], contact_id=contact["id"],
                    subject="ZZTEST", body="hello", status="approved",
                    recipient_email=email_addr)
        data.update(over)
        return database.create_email(**data)

    def _run(self, database, monkeypatch, email_ids, expect_failure=False):
        """Drive the real send job with a sender that records instead of mailing.

        The lock is taken here because the route takes it — _send_batch_job
        releases it in its finally, so calling the job directly without
        acquiring it first raises rather than testing anything.
        """
        sent_to = []

        class _FakeSender:
            send_delay = 0

            def authenticate(self):
                return MagicMock()

            # A follow-up asks for this before sending. Without it the batch
            # died on an AttributeError *before* reaching the gate, so the
            # follow-up test passed on silence rather than on the refusal —
            # exempting follow-ups from the gate survived the whole suite.
            def get_thread_context(self, message_id):
                return {"message_id": "<a@b>", "thread_id": "t1"}

            def send_email(self, email, from_email, resume_path=None):
                sent_to.append(email.get("recipient_email")
                               or email.get("contact_email"))
                return {"email_id": email["id"], "success": True,
                        "gmail_message_id": "gm1", "thread_id": "t1"}

        monkeypatch.setattr(main, "email_sender", _FakeSender())
        monkeypatch.setattr(main, "_domain_accepts_mail", lambda *a, **k: True)
        job = database.create_job("send", {"email_ids": email_ids})
        main._send_lock.acquire()
        main._send_batch_job(job["id"], email_ids, None, False, "me@example.com")
        assert not main._send_lock.locked(), \
            "the send job must release the lock on every path, including failure"
        finished = database.get_job(job["id"])
        # An empty `sent_to` proves nothing if the batch crashed on the way to
        # the gate. Every case here expects the job to complete.
        if not expect_failure:
            assert finished["status"] == "done", \
                f"batch did not complete: {finished.get('error')}"
        return sent_to, finished

    def test_refuses_a_suppressed_address_at_the_last_step(self, client, monkeypatch):
        """The draft predates the suppression — the ordinary case, since a
        draft can sit for a week after somebody asks to be removed."""
        api, database = client
        email = self._sendable(database, "dana@acme.com")
        api.post("/api/suppressions",
                 json={"value": "dana@acme.com", "reason": "asked to stop"})

        sent_to, job = self._run(database, monkeypatch, [email["id"]])
        assert sent_to == []
        assert database.get_email(email["id"])["status"] != "sent"
        assert "do-not-contact" in job["result"]

    def test_a_domain_entry_stops_mail_to_everyone_there(self, client, monkeypatch):
        api, database = client
        one = self._sendable(database, "dana@acme.com")
        two = self._sendable(database, "bob@mail.acme.com")
        api.post("/api/suppressions", json={"value": "acme.com"})

        sent_to, _ = self._run(database, monkeypatch, [one["id"], two["id"]])
        assert sent_to == []

    def test_an_unsuppressed_address_in_the_same_batch_still_sends(
            self, client, monkeypatch):
        """A blocked recipient must not take the rest of the batch down with
        it — that would make the list something users route around."""
        api, database = client
        blocked = self._sendable(database, "dana@acme.com")
        fine = self._sendable(database, "eve@other.example")
        api.post("/api/suppressions", json={"value": "dana@acme.com"})

        sent_to, _ = self._run(database, monkeypatch, [blocked["id"], fine["id"]])
        assert sent_to == ["eve@other.example"]

    def test_sends_normally_when_the_list_is_empty(self, client, monkeypatch):
        _, database = client
        email = self._sendable(database, "eve@other.example")
        sent_to, _ = self._run(database, monkeypatch, [email["id"]])
        assert sent_to == ["eve@other.example"]

    def test_an_unreadable_list_stops_the_whole_batch(self, client, monkeypatch):
        """The one check in this app that fails closed. Every other gate is
        optimistic so a transient failure cannot block real mail; here a
        transient failure must block it."""
        _, database = client
        email = self._sendable(database, "eve@other.example")

        def _boom():
            raise RuntimeError("disk gone")

        monkeypatch.setattr(database, "list_suppressions", _boom)
        sent_to, job = self._run(database, monkeypatch, [email["id"]],
                                 expect_failure=True)
        assert sent_to == []
        assert job["status"] == "failed"
        assert "do-not-contact" in (job["error"] or "")

    def test_refuses_a_suppressed_follow_up(self, client, monkeypatch):
        """Follow-ups are the drafts most likely to outlive a suppression —
        they are written days after the first contact, which is exactly when
        somebody says stop. Every other test here builds a first-contact
        email, so a gate that exempted follow-ups passed the whole suite."""
        api, database = client
        company, contact = _contact(database, "dana@acme.com")
        original = database.create_email(
            company_id=company["id"], contact_id=contact["id"], subject="s",
            body="b", status="sent", sent_at=now_iso(), gmail_message_id="gm1",
            recipient_email="dana@acme.com")
        follow_up = database.create_email(
            company_id=company["id"], contact_id=contact["id"], subject="s2",
            body="b2", status="approved", recipient_email="dana@acme.com",
            is_follow_up=True, follow_up_step=1,
            original_email_id=original["id"])
        api.post("/api/suppressions", json={"value": "dana@acme.com"})

        sent_to, job = self._run(database, monkeypatch, [follow_up["id"]])
        assert sent_to == []
        # Asserted on the refusal, not on silence.
        assert "do-not-contact" in job["result"]

    def test_every_entry_on_the_list_is_consulted(self, client, monkeypatch):
        """Every other test adds exactly one entry, so any regression that
        returned a subset — a LIMIT, a stray WHERE — would go unnoticed while
        silently un-suppressing people."""
        api, database = client
        one = self._sendable(database, "dana@acme.com")
        two = self._sendable(database, "bob@other.example")
        three = self._sendable(database, "eve@third.example")
        api.post("/api/suppressions", json={"value": "dana@acme.com"})
        api.post("/api/suppressions", json={"value": "other.example"})

        sent_to, _ = self._run(database, monkeypatch,
                               [one["id"], two["id"], three["id"]])
        assert sent_to == ["eve@third.example"]

    def test_an_entry_added_mid_batch_stops_the_rest(self, client, monkeypatch):
        """A 200-email batch runs for ten minutes at the default send delay,
        and the scheduler's unattended batches are this same code. Reading the
        list once meant "I added them while it was sending" took effect on the
        next batch rather than the next message."""
        _, database = client
        first = self._sendable(database, "first@other.example")
        second = self._sendable(database, "dana@acme.com")

        sent_to = []
        real_add = database.add_suppression

        class _FakeSender:
            send_delay = 0

            def authenticate(self):
                return MagicMock()

            def send_email(self, email, from_email, resume_path=None):
                sent_to.append(email.get("recipient_email"))
                # The user adds the entry while the batch is running.
                if len(sent_to) == 1:
                    real_add("dana@acme.com", "address")
                return {"email_id": email["id"], "success": True,
                        "gmail_message_id": "gm1"}

        monkeypatch.setattr(main, "email_sender", _FakeSender())
        monkeypatch.setattr(main, "_domain_accepts_mail", lambda *a, **k: True)
        job = database.create_job("send", {"email_ids": [first["id"], second["id"]]})
        main._send_lock.acquire()
        main._send_batch_job(job["id"], [first["id"], second["id"]], None, False,
                             "me@example.com")
        assert sent_to == ["first@other.example"]

    def test_a_tagged_recipient_is_refused_by_the_bare_entry(self, client, monkeypatch):
        api, database = client
        email = self._sendable(database, "dana+jobs@acme.com")
        api.post("/api/suppressions", json={"value": "dana@acme.com"})
        sent_to, _ = self._run(database, monkeypatch, [email["id"]])
        assert sent_to == []

    def test_a_read_failure_mid_batch_stops_the_rest_and_says_why(
            self, client, monkeypatch):
        """Fails closed, but honestly. Reporting the failure as "they are on
        your list" sent the user to Settings to remove an entry that does not
        exist, and marked a transient error as permanent."""
        _, database = client
        first = self._sendable(database, "first@other.example")
        second = self._sendable(database, "second@other.example")

        calls = {"n": 0}
        real = database.list_suppressions

        def _flaky():
            calls["n"] += 1
            if calls["n"] > 2:          # pre-loop read, then the first message
                raise RuntimeError("disk gone")
            return real()

        monkeypatch.setattr(database, "list_suppressions", _flaky)
        sent_to, job = self._run(database, monkeypatch,
                                 [first["id"], second["id"]])
        assert sent_to == ["first@other.example"]
        assert "Could not read the do-not-contact list" in job["result"]
        # Named as retryable — the list may well be readable a minute later,
        # and "on your list" would have marked it permanent.
        assert '"retryable": true' in job["result"]

    def test_the_gate_reads_the_recipient_the_message_would_go_to(
            self, client, monkeypatch):
        """`recipient_email` is frozen at draft time and is what actually gets
        mailed, so checking the contact's current address instead would let a
        suppressed recipient through whenever the two differ."""
        api, database = client
        company, contact = _contact(database, "new@other.example")
        email = database.create_email(
            company_id=company["id"], contact_id=contact["id"], subject="s",
            body="b", status="approved", recipient_email="old@acme.com")
        api.post("/api/suppressions", json={"value": "old@acme.com"})

        sent_to, _ = self._run(database, monkeypatch, [email["id"]])
        assert sent_to == []


class TestUnattendedSendGate:
    """The send-window scheduler hands real mail to Gmail from a background
    thread with nobody watching. It is the one path where a missing gate would
    not even produce an error somebody sees, so it gets its own test rather
    than resting on the fact that it currently delegates to _send_batch_job."""

    def test_the_scheduler_refuses_a_suppressed_address(self, client, monkeypatch):
        api, database = client
        company, contact = _contact(database, "dana@acme.com")
        queued = database.create_email(
            company_id=company["id"], contact_id=contact["id"], subject="s",
            body="b", status="approved", recipient_email="dana@acme.com")
        api.post("/api/suppressions", json={"value": "dana@acme.com"})

        # A window that is open right now, and a row due a minute ago.
        # `end_hour` is clamped to 23 and `_in_window` is a half-open range, so
        # no stored config can cover the 23:00 hour — configuring one here made
        # this test fail for an hour every night. The window is not what this
        # test is about; the suppression gate below it is.
        database.update_send_window({"enabled": True, "days": list(range(7)),
                                     "start_hour": 0, "end_hour": 23})
        monkeypatch.setattr(main.send_window, "is_open", lambda *a, **k: True)
        job = database.create_job("send", {"email_ids": [queued["id"]],
                                           "attach_resume": False})
        database.update_email(queued["id"], {
            "scheduled_at": (datetime.now() - timedelta(minutes=1))
            .isoformat(timespec="seconds"),
            "scheduled_by_job": job["id"]})

        sent_to = []

        class _FakeSender:
            send_delay = 0

            def authenticate(self):
                return MagicMock()

            def send_email(self, email, from_email, resume_path=None):
                sent_to.append(email.get("recipient_email"))
                return {"email_id": email["id"], "success": True,
                        "gmail_message_id": "gm1"}

        monkeypatch.setattr(main, "email_sender", _FakeSender())
        monkeypatch.setattr(main, "_domain_accepts_mail", lambda *a, **k: True)
        # The sweep starts a thread; run it inline so the assertion is not a race.
        started = {}

        class _Inline:
            def __init__(self, target=None, args=(), daemon=None, **kw):
                self._target, self._args = target, args

            def start(self):
                started["ran"] = True
                self._target(*self._args)

        monkeypatch.setattr(main.threading, "Thread", _Inline)
        main.scheduled_send_sweep()

        assert started.get("ran"), "the sweep did not reach the send job"
        assert sent_to == []
        assert database.get_email(queued["id"])["status"] != "sent"

    def test_the_scheduler_still_sends_an_unsuppressed_address(
            self, client, monkeypatch):
        """The gate must not be the reason the scheduler stops working."""
        _, database = client
        company, contact = _contact(database, "eve@other.example")
        queued = database.create_email(
            company_id=company["id"], contact_id=contact["id"], subject="s",
            body="b", status="approved", recipient_email="eve@other.example")
        database.update_send_window({"enabled": True, "days": list(range(7)),
                                     "start_hour": 0, "end_hour": 23})
        monkeypatch.setattr(main.send_window, "is_open", lambda *a, **k: True)
        job = database.create_job("send", {"email_ids": [queued["id"]],
                                           "attach_resume": False})
        database.update_email(queued["id"], {
            "scheduled_at": (datetime.now() - timedelta(minutes=1))
            .isoformat(timespec="seconds"),
            "scheduled_by_job": job["id"]})

        sent_to = []

        class _FakeSender:
            send_delay = 0

            def authenticate(self):
                return MagicMock()

            def send_email(self, email, from_email, resume_path=None):
                sent_to.append(email.get("recipient_email"))
                return {"email_id": email["id"], "success": True,
                        "gmail_message_id": "gm1"}

        monkeypatch.setattr(main, "email_sender", _FakeSender())
        monkeypatch.setattr(main, "_domain_accepts_mail", lambda *a, **k: True)

        class _Inline:
            def __init__(self, target=None, args=(), daemon=None, **kw):
                self._target, self._args = target, args

            def start(self):
                self._target(*self._args)

        monkeypatch.setattr(main.threading, "Thread", _Inline)
        main.scheduled_send_sweep()
        assert sent_to == ["eve@other.example"]


class TestGenerationGate:
    def test_skips_a_suppressed_contact_instead_of_drafting(self, client):
        """Drafting burns paid quota on a message the send path will refuse,
        and leaves a draft whose only outcome is an error."""
        api, database = client
        _, contact = _contact(database, "dana@acme.com")
        api.post("/api/suppressions", json={"value": "dana@acme.com"})

        service = main.generation
        service.db = database
        job = database.create_job("generation", {"contact_ids": [contact["id"]]})
        service._run(job["id"], {"contact_ids": [contact["id"]],
                                 "use_template_only": True})

        finished = database.get_job(job["id"])
        assert "do-not-contact" in (finished["result"] or "")
        assert database.query_one(
            "SELECT COUNT(*) AS n FROM emails WHERE contact_id=?",
            (contact["id"],))["n"] == 0


class TestFollowUpGate:
    def test_refuses_to_draft_a_follow_up_to_a_suppressed_address(self, client):
        api, database = client
        company, contact = _contact(database, "dana@acme.com")
        database.create_email(company_id=company["id"], contact_id=contact["id"],
                              subject="s", body="b", status="sent",
                              sent_at=now_iso(), gmail_message_id="gm1",
                              recipient_email="dana@acme.com")
        api.post("/api/suppressions", json={"value": "dana@acme.com"})

        for manual in (True, False):
            # manual=False is the bulk "Draft all follow-ups" job — the path
            # that would quietly write to a suppressed contact in volume.
            plan = main._follow_up_plan(contact["id"], manual=manual)
            assert plan["refusal"], manual
            assert "do-not-contact" in plan["refusal"], manual
