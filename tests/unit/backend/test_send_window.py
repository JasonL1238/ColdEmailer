"""Sending inside business hours, and the two gates that keep it from firing.

A message sent at 03:00 is at the bottom of the pile by morning. Holding it
until 09:00 is worth real reply rate — but the mechanism is a background thread
that hands mail to a live Gmail token with nobody watching, which is the single
most dangerous thing in this app. So it is off when shipped and needs *two*
independent yeses before anything can leave:

    1. the sending window switched on in Settings, and
    2. that specific batch asking to be scheduled.

Every test below either pins one of those gates or pins the arithmetic that
decides when "later" is. `scheduled_send_sweep()` is called directly rather
than waited for: a behaviour this consequential should not only be reachable
through a sleeping thread.
"""
import itertools
import os
import sys
import types
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

from zoneinfo import ZoneInfo

import main
import send_window
from models import SendRequest, SendWindowUpdate

_seq = itertools.count()

# Monday 2026-08-03 at 09:00 — inside a default weekday 8–5 window.
MONDAY_9AM = datetime(2026, 8, 3, 9, 0)


def _recently(hours=1):
    """A stamp whose moment has passed but is still inside the staleness
    floor — the ordinary "this is due now" case."""
    return (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")


def _a_zone_offset_from_here():
    """A real IANA zone whose current offset differs from this machine's.

    Hardcoding one makes the frame assertions tautologies on any host that
    happens to run it — which is precisely the bug those assertions guard.
    """
    from zoneinfo import ZoneInfo as _Z
    here = datetime.now().astimezone().utcoffset()
    for name in ("Pacific/Kiritimati", "Pacific/Midway", "Asia/Kathmandu",
                 "Australia/Sydney", "America/Sao_Paulo", "UTC"):
        try:
            if datetime.now(_Z(name)).utcoffset() != here:
                return name
        except Exception:
            continue
    raise AssertionError("no zone differs from this host's offset")


def _window(**kw):
    return send_window.normalize_send_window(
        {"enabled": True, "days": [0, 1, 2, 3, 4],
         "start_hour": 8, "end_hour": 17, **kw})


# ---------- when the window is open ----------

def test_the_window_is_open_only_inside_the_configured_hours_and_days():
    w = _window()
    assert send_window.is_open(w, MONDAY_9AM)
    assert send_window.is_open(w, MONDAY_9AM.replace(hour=8))       # inclusive start
    assert not send_window.is_open(w, MONDAY_9AM.replace(hour=17))  # exclusive end
    assert not send_window.is_open(w, MONDAY_9AM.replace(hour=7, minute=59))
    assert not send_window.is_open(w, MONDAY_9AM.replace(hour=3))
    saturday = MONDAY_9AM + timedelta(days=5)
    assert saturday.weekday() == 5 and not send_window.is_open(w, saturday)


def test_the_next_opening_is_now_when_it_is_already_open():
    assert send_window.next_opening(_window(), MONDAY_9AM) == MONDAY_9AM


def test_the_next_opening_skips_the_night_the_weekend_and_closed_days():
    w = _window()
    # 3am Monday -> 8am the same morning
    assert send_window.next_opening(w, MONDAY_9AM.replace(hour=3)) \
        == MONDAY_9AM.replace(hour=8)
    # 6pm Monday -> 8am Tuesday
    assert send_window.next_opening(w, MONDAY_9AM.replace(hour=18)) \
        == MONDAY_9AM.replace(hour=8) + timedelta(days=1)
    # Saturday -> Monday morning
    saturday = MONDAY_9AM + timedelta(days=5)
    assert send_window.next_opening(w, saturday) \
        == MONDAY_9AM.replace(hour=8) + timedelta(days=7)
    # a Tuesday-only window from a Wednesday goes almost a full week
    tuesday_only = _window(days=[1])
    wednesday = MONDAY_9AM + timedelta(days=2)
    assert send_window.next_opening(tuesday_only, wednesday) \
        == MONDAY_9AM.replace(hour=8) + timedelta(days=8)


def test_a_disabled_window_never_schedules_anything():
    assert send_window.next_opening(_window(enabled=False), MONDAY_9AM) is None
    assert send_window.is_open(_window(enabled=False), MONDAY_9AM) is False


# ---------- the configuration itself ----------

def test_the_shipped_default_is_off():
    """Nothing may start sending itself because the user updated the app."""
    assert send_window.SEND_WINDOW_DEFAULT["enabled"] is False


def test_a_corrupt_window_falls_back_instead_of_never_sending(db):
    """This value decides when real mail leaves, so a hand-edited or legacy
    settings row is repaired rather than obeyed — and an end at or before the
    start would otherwise make next_opening search for a minute that never
    comes."""
    fixed = send_window.normalize_send_window({"enabled": True, "start_hour": 17,
                                               "end_hour": 9})
    assert fixed["start_hour"] == 8 and fixed["end_hour"] == 17
    # Equal hours are a zero-length window. Asserting only end_hour == 17 was
    # a tautology — 17 is the answer with the repair and without it.
    equal = send_window.normalize_send_window(
        {"enabled": True, "start_hour": 17, "end_hour": 17})
    assert equal["start_hour"] == 8 and equal["end_hour"] == 17
    # junk days are dropped; an empty result falls back to weekdays
    assert send_window.normalize_send_window(
        {"days": ["x", 9, -1, True, 2, 2]})["days"] == [2]
    assert send_window.normalize_send_window({"days": []})["days"] == [0, 1, 2, 3, 4]
    assert send_window.normalize_send_window("not a dict")["enabled"] is False
    # an unknown timezone degrades to local time rather than refusing to send
    assert send_window.normalize_send_window(
        {"timezone": "Mars/Olympus"})["timezone"] == ""


def test_next_opening_always_terminates_on_any_normalized_window():
    """The search is bounded. Normalization guarantees a non-empty day list and
    a positive-length window, so an answer always exists — but a future change
    to either must produce a wrong answer, not a hung request thread."""
    for days in ([0], [6], [0, 1, 2, 3, 4, 5, 6]):
        for start, end in ((0, 1), (8, 17), (22, 23)):
            w = _window(days=days, start_hour=start, end_hour=end)
            assert send_window.next_opening(w, MONDAY_9AM) is not None


def test_the_window_is_stored_and_read_back_normalized(db):
    assert db.get_send_window()["enabled"] is False        # never configured
    saved = db.update_send_window({"enabled": True, "days": [0, 4],
                                   "start_hour": 9, "end_hour": 18})
    assert saved == db.get_send_window()
    assert saved["days"] == [0, 4] and saved["start_hour"] == 9


def test_the_request_model_rejects_a_timezone_it_cannot_resolve():
    with pytest.raises(Exception):
        SendWindowUpdate(enabled=True, timezone="Mars/Olympus")
    assert SendWindowUpdate(enabled=True, timezone="America/New_York").timezone \
        == "America/New_York"
    assert SendWindowUpdate(enabled=True, timezone="").timezone == ""


def test_the_description_says_what_the_setting_will_actually_do():
    assert "not held" in send_window.describe(_window(enabled=False))
    text = send_window.describe(_window(timezone="America/New_York"))
    assert "8am" in text and "5pm" in text and "weekdays" in text
    assert "America/New_York" in text
    assert "Sat" in send_window.describe(_window(days=[5]))


# ---------- the two gates ----------

class _FakeSender:
    send_delay = 0
    credentials_path = __file__          # exists, so the endpoint gets past it

    def __init__(self):
        self.calls = []
        self.options = []
        self.authenticated = 0

    def authenticate(self):
        self.authenticated += 1

    def send_email(self, email, from_email, resume_path=None):
        self.calls.append(email["id"])
        # Recorded, not ignored. Dropping these was why a scheduled batch could
        # staple the default resume onto mail the user had unticked "Attach
        # resume" for, with every test still green.
        self.options.append({"email_id": email["id"], "resume_path": resume_path,
                             "from_email": from_email})
        return {"success": True, "email_id": email["id"],
                "gmail_message_id": "m", "gmail_thread_id": "t"}

    def find_delivered_message(self, *a, **kw):
        return None

    def get_thread_context(self, gmail_message_id):
        return {"message_id": None, "thread_id": None}


@pytest.fixture
def api(monkeypatch):
    sender = _FakeSender()
    monkeypatch.setattr(main, "email_sender", sender)
    monkeypatch.setattr(main, "_domain_accepts_mail", lambda addr, cache: True)
    seq = next(_seq)
    contact = main.db.create_contact(name=f"ZZTEST Sched {seq}",
                                     email=f"zztestsched{seq}@example.com")
    draft = main.db.create_email(contact_id=contact["id"], status="draft",
                                 subject="ZZTEST hello", body="Hi there,")
    yield sender, contact, draft
    main.db.delete_contact(contact["id"])
    main.db.execute("DELETE FROM settings WHERE key='send_window'")
    if main._send_lock.locked():
        main._send_lock.release()


def _send(draft_id, **kw):
    return main.send_emails(SendRequest(email_ids=[draft_id], **kw))


def test_scheduling_is_refused_while_the_window_is_off(api):
    """Gate one. The batch asked, but nothing in Settings said yes."""
    sender, contact, draft = api
    with pytest.raises(HTTPException) as exc:
        _send(draft["id"], schedule="next_window")
    assert exc.value.status_code == 400
    assert "switched off" in exc.value.detail
    assert sender.calls == []
    assert main.db.get_email(draft["id"])["scheduled_at"] is None


def test_an_enabled_window_alone_does_not_delay_an_ordinary_send(api):
    """Gate two. Turning the window on must not silently start holding mail
    the user pressed Send on — that would be the app deciding to sit on a
    message they were watching go out."""
    sender, contact, draft = api
    main.db.update_send_window({"enabled": True, "days": [0, 1, 2, 3, 4],
                                "start_hour": 8, "end_hour": 17})

    job = _send(draft["id"])                       # no schedule= argument

    _await(job["id"])
    assert sender.calls == [draft["id"]]
    assert main.db.get_email(draft["id"])["status"] == "sent"
    assert main.db.get_email(draft["id"])["scheduled_at"] is None


def test_both_gates_together_queue_instead_of_sending(api):
    sender, contact, draft = api
    main.db.update_send_window({"enabled": True, "days": [0, 1, 2, 3, 4],
                                "start_hour": 8, "end_hour": 17})

    job = _send(draft["id"], schedule="next_window")

    assert sender.calls == []                       # nothing left the building
    row = main.db.get_email(draft["id"])
    assert row["scheduled_at"] is not None
    assert row["status"] == "approved" and row["sent_at"] is None
    assert job["result"]["scheduled"] == 1
    # the lock is released, or every later send 409s forever
    assert not main._send_lock.locked()


def test_the_sweep_does_nothing_while_the_window_is_off(api):
    """Even with a row already stamped — switching the window off is a stop
    button, not just a refusal to queue new work."""
    sender, contact, draft = api
    main.db.update_email(draft["id"], {"scheduled_at": _recently(),
                                       "status": "approved"})

    assert main.scheduled_send_sweep() is None
    assert sender.calls == []
    assert sender.authenticated == 0


def test_the_sweep_does_nothing_outside_the_window(api, monkeypatch):
    sender, contact, draft = api
    main.db.update_send_window({"enabled": True, "days": [0, 1, 2, 3, 4],
                                "start_hour": 8, "end_hour": 17})
    main.db.update_email(draft["id"], {"scheduled_at": _recently(),
                                       "status": "approved"})
    monkeypatch.setattr(send_window, "is_open", lambda w, now=None: False)

    assert main.scheduled_send_sweep() is None
    assert sender.calls == []


def test_the_sweep_sends_what_is_due_and_clears_the_stamp(api, monkeypatch):
    sender, contact, draft = api
    main.db.update_send_window({"enabled": True, "days": [0, 1, 2, 3, 4],
                                "start_hour": 8, "end_hour": 17})
    main.db.update_email(draft["id"], {"scheduled_at": _recently(),
                                       "status": "approved"})
    monkeypatch.setattr(send_window, "is_open", lambda w, now=None: True)

    job_id = main.scheduled_send_sweep()

    assert job_id
    _await(job_id)
    assert sender.calls == [draft["id"]]
    row = main.db.get_email(draft["id"])
    assert row["status"] == "sent"
    # cleared before the send, so a crash mid-batch leaves an ordinary draft
    # rather than something a later sweep picks up and sends again
    assert row["scheduled_at"] is None


def test_the_sweep_leaves_a_message_whose_time_has_not_come(api, monkeypatch):
    sender, contact, draft = api
    main.db.update_send_window({"enabled": True, "days": [0, 1, 2, 3, 4],
                                "start_hour": 8, "end_hour": 17})
    future = (datetime.now() + timedelta(days=2)).isoformat(timespec="seconds")
    main.db.update_email(draft["id"], {"scheduled_at": future, "status": "approved"})
    monkeypatch.setattr(send_window, "is_open", lambda w, now=None: True)

    assert main.scheduled_send_sweep() is None
    assert sender.calls == []
    assert main.db.get_email(draft["id"])["scheduled_at"] == future


def test_the_sweep_never_resends_something_already_delivered(api, monkeypatch):
    """A stamp left on a row that has since gone out — by a manual send, or a
    reconciliation — must not put a second copy in someone's inbox."""
    sender, contact, draft = api
    main.db.update_send_window({"enabled": True, "days": [0, 1, 2, 3, 4],
                                "start_hour": 8, "end_hour": 17})
    main.db.update_email(draft["id"], {"scheduled_at": _recently(),
                                       "status": "sent", "sent_at": "2020-01-02T09:00:00",
                                       "gmail_message_id": "already-gone"})
    monkeypatch.setattr(send_window, "is_open", lambda w, now=None: True)

    assert main.scheduled_send_sweep() is None
    assert sender.calls == []


def test_the_sweep_stands_down_while_a_manual_batch_holds_the_lock(api, monkeypatch):
    """One send batch at a time is an invariant of this app. The scheduler is
    a second writer to the same path, so it has to respect it."""
    sender, contact, draft = api
    main.db.update_send_window({"enabled": True, "days": [0, 1, 2, 3, 4],
                                "start_hour": 8, "end_hour": 17})
    main.db.update_email(draft["id"], {"scheduled_at": _recently(),
                                       "status": "approved"})
    monkeypatch.setattr(send_window, "is_open", lambda w, now=None: True)
    main._send_lock.acquire()
    try:
        assert main.scheduled_send_sweep() is None
        assert sender.calls == []
        # still queued for the next tick, not dropped
        assert main.db.get_email(draft["id"])["scheduled_at"] is not None
    finally:
        main._send_lock.release()


def test_the_sweep_leaves_everything_queued_when_gmail_is_not_authenticated(
        api, monkeypatch):
    sender, contact, draft = api
    main.db.update_send_window({"enabled": True, "days": [0, 1, 2, 3, 4],
                                "start_hour": 8, "end_hour": 17})
    main.db.update_email(draft["id"], {"scheduled_at": _recently(),
                                       "status": "approved"})
    monkeypatch.setattr(send_window, "is_open", lambda w, now=None: True)

    def _fail():
        raise RuntimeError("token expired")
    monkeypatch.setattr(sender, "authenticate", _fail)

    assert main.scheduled_send_sweep() is None
    assert sender.calls == []
    assert main.db.get_email(draft["id"])["scheduled_at"] is not None
    assert not main._send_lock.locked()          # released on the way out


def test_a_queued_message_can_be_taken_back_out(api):
    sender, contact, draft = api
    main.db.update_send_window({"enabled": True, "days": [0, 1, 2, 3, 4],
                                "start_hour": 8, "end_hour": 17})
    _send(draft["id"], schedule="next_window")
    assert main.db.get_email(draft["id"])["scheduled_at"] is not None

    from models import BulkIds
    import asyncio
    result = asyncio.run(main.unschedule_emails(BulkIds(ids=[draft["id"]])))

    assert result["cleared"] == 1
    assert main.db.get_email(draft["id"])["scheduled_at"] is None


# ---------- what the queue remembers ----------

def test_a_queued_batch_keeps_the_send_options_the_user_chose(api, monkeypatch):
    """The batch is sent minutes or days later by a thread that can only know
    what the user picked if it was written down. Storing just the ids meant
    "Attach resume" unticked came back as True — the default resume stapled to
    unattended mail — and a confirmed resume override came back as None."""
    sender, contact, draft = api
    main.db.update_send_window({"enabled": True, "days": [0, 1, 2, 3, 4],
                                "start_hour": 8, "end_hour": 17})
    monkeypatch.setattr(send_window, "is_open", lambda w, now=None: True)
    monkeypatch.setattr(main.resumes, "resolve_attachment_path",
                        lambda rid: f"/tmp/{rid}.pdf" if rid else None)

    main.send_emails(SendRequest(email_ids=[draft["id"]], attach_resume=False,
                                 from_email="alias@example.com",
                                 schedule="next_window"))
    main.db.update_email(draft["id"], {"scheduled_at": _recently()})

    _await(main.scheduled_send_sweep())

    assert len(sender.options) == 1
    assert sender.options[0]["resume_path"] is None      # not the default resume
    assert sender.options[0]["from_email"] == "alias@example.com"


def test_a_queued_batch_honours_a_chosen_resume(api, monkeypatch):
    sender, contact, draft = api
    main.db.update_send_window({"enabled": True, "days": [0, 1, 2, 3, 4],
                                "start_hour": 8, "end_hour": 17})
    monkeypatch.setattr(send_window, "is_open", lambda w, now=None: True)
    monkeypatch.setattr(main.resumes, "resolve_attachment_path",
                        lambda rid: f"/tmp/{rid}.pdf" if rid else None)

    main.send_emails(SendRequest(email_ids=[draft["id"]], attach_resume=True,
                                 resume_id="chosen-one", schedule="next_window"))
    main.db.update_email(draft["id"], {"scheduled_at": _recently()})

    _await(main.scheduled_send_sweep())

    assert sender.options[0]["resume_path"] == "/tmp/chosen-one.pdf"


def test_a_draft_rewritten_while_queued_is_dropped_rather_than_sent(api, monkeypatch):
    """The attachment check ran once, at request time, against options that
    were then discarded. A body edited in the days before the batch fires can
    start promising an attachment these options would not send — so the
    question is asked again, at the moment nobody is watching."""
    sender, contact, draft = api
    main.db.update_send_window({"enabled": True, "days": [0, 1, 2, 3, 4],
                                "start_hour": 8, "end_hour": 17})
    monkeypatch.setattr(send_window, "is_open", lambda w, now=None: True)

    main.send_emails(SendRequest(email_ids=[draft["id"]], attach_resume=False,
                                 schedule="next_window"))
    # the user reopens it and adds a promise the queued options cannot keep
    main.db.execute("UPDATE emails SET body=? WHERE id=?",
                    ("Hi there,\n\nMy resume is attached.", draft["id"]))
    main.db.update_email(draft["id"], {"scheduled_at": _recently()})

    assert main.scheduled_send_sweep() is None
    assert sender.calls == []
    assert main.db.get_email(draft["id"])["scheduled_at"] is None
    assert not main._send_lock.locked()


# ---------- a queued row stops being queued when it changes ----------

def test_trashing_a_queued_email_takes_it_out_of_the_queue(api):
    """Trashing only dropped it out of the sweep's status filter — the stamp
    survived, so restoring the draft a week later armed a background send of
    week-old text with no fresh opt-in."""
    sender, contact, draft = api
    main.db.update_send_window({"enabled": True, "days": [0, 1, 2, 3, 4],
                                "start_hour": 8, "end_hour": 17})
    main.send_emails(SendRequest(email_ids=[draft["id"]], schedule="next_window"))
    assert main.db.get_email(draft["id"])["scheduled_at"] is not None

    main.db.update_email(draft["id"], {"status": "trashed"})
    assert main.db.get_email(draft["id"])["scheduled_at"] is None

    main.db.update_email(draft["id"], {"status": "draft"})
    assert main.db.get_email(draft["id"])["scheduled_at"] is None


def test_editing_a_queued_email_takes_it_out_of_the_queue(api):
    """What was approved for unattended sending is the text as it stood. Edit
    it and the queue would deliver something else."""
    sender, contact, draft = api
    main.db.update_send_window({"enabled": True, "days": [0, 1, 2, 3, 4],
                                "start_hour": 8, "end_hour": 17})
    main.send_emails(SendRequest(email_ids=[draft["id"]], schedule="next_window"))

    main.db.update_email(draft["id"], {"body": "A different message entirely."})

    row = main.db.get_email(draft["id"])
    assert row["scheduled_at"] is None
    # the job link goes too, or the next sweep groups against a dead batch
    assert row["scheduled_by_job"] is None


def test_editing_only_the_subject_also_takes_it_out_of_the_queue(api):
    """PATCH /api/emails/{id} accepts a subject on its own, and the subject is
    the whole of what most recipients read. Triggering only on body meant the
    queue could deliver a subject line nobody approved."""
    sender, contact, draft = api
    main.db.update_send_window({"enabled": True, "days": [0, 1, 2, 3, 4],
                                "start_hour": 8, "end_hour": 17})
    main.send_emails(SendRequest(email_ids=[draft["id"]], schedule="next_window"))

    main.db.update_email(draft["id"], {"subject": "Something else"})

    assert main.db.get_email(draft["id"])["scheduled_at"] is None


def test_rescheduling_an_already_queued_row_keeps_the_new_time(api):
    """The clearing skips itself when the caller is setting `scheduled_at` —
    otherwise the scheduling write, which sets status and stamp together, would
    wipe the stamp it had just written."""
    sender, contact, draft = api
    main.db.update_send_window({"enabled": True, "days": [0, 1, 2, 3, 4],
                                "start_hour": 8, "end_hour": 17})
    main.send_emails(SendRequest(email_ids=[draft["id"]], schedule="next_window"))
    first = main.db.get_email(draft["id"])["scheduled_at"]
    assert first

    main.db.update_email(draft["id"], {"status": "approved",
                                       "scheduled_at": "2030-01-01T08:00:00"})
    assert main.db.get_email(draft["id"])["scheduled_at"] == "2030-01-01T08:00:00"


def test_switching_the_window_off_empties_the_queue(api):
    """The card then reads "Sending is not held for business hours", which is
    a claim about the future the app has to keep. Leaving rows stamped merely
    paused them, invisibly, until the window came back on."""
    import asyncio
    sender, contact, draft = api
    main.db.update_send_window({"enabled": True, "days": [0, 1, 2, 3, 4],
                                "start_hour": 8, "end_hour": 17})
    main.send_emails(SendRequest(email_ids=[draft["id"]], schedule="next_window"))

    result = asyncio.run(main.set_send_window(SendWindowUpdate(enabled=False)))

    assert result["unscheduled"] == 1
    assert main.db.get_email(draft["id"])["scheduled_at"] is None


def test_a_stamp_left_behind_too_long_is_dropped_not_sent(api, monkeypatch):
    """A queue abandoned while the window was off would otherwise all satisfy
    `scheduled_at <= now` and leave in one burst the minute it came back —
    months-old drafts, in a batch nobody asked for today."""
    sender, contact, draft = api
    main.db.update_send_window({"enabled": True, "days": [0, 1, 2, 3, 4],
                                "start_hour": 8, "end_hour": 17})
    monkeypatch.setattr(send_window, "is_open", lambda w, now=None: True)
    main.db.update_email(draft["id"], {"status": "approved"})
    main.db.execute("UPDATE emails SET scheduled_at=? WHERE id=?",
                    (_recently(hours=24 * 30), draft["id"]))

    assert main.scheduled_send_sweep() is None
    assert sender.calls == []
    assert main.db.get_email(draft["id"])["scheduled_at"] is None


# ---------- the stamp and the clock agree ----------

def test_the_stamp_is_written_in_the_frame_the_sweep_compares_against(api):
    """`next_opening` answers in the *window's* zone; the sweep compares
    against naive server-local time. Storing the window's wall clock meant a
    UTC container with an Australia/Sydney window fired ten hours out.

    The zone is chosen at runtime to differ from this host's, rather than
    hardcoded — on a machine already running Australia/Sydney the assertion
    held with and without the conversion it exists to protect.
    """
    sender, contact, draft = api
    zone = _a_zone_offset_from_here()
    main.db.update_send_window({"enabled": True, "timezone": zone,
                                "days": [0, 1, 2, 3, 4, 5, 6],
                                "start_hour": 0, "end_hour": 23})
    main.send_emails(SendRequest(email_ids=[draft["id"]], schedule="next_window"))

    stamp = main.db.get_email(draft["id"])["scheduled_at"]
    stored = datetime.fromisoformat(stamp)
    # The stored instant is within a minute of now in *this machine's* frame,
    # whatever offset Sydney happens to be at.
    assert abs((stored - datetime.now()).total_seconds()) < 90, stamp


def test_a_zone_aware_window_answers_in_that_zone():
    """No test set a timezone at all, so the conversion branch was never taken
    — which is how the frame mismatch above shipped."""
    from datetime import timezone as _tz
    w = _window(timezone="Australia/Sydney", days=[0, 1, 2, 3, 4],
                start_hour=8, end_hour=17)
    # 2026-08-03 22:00 UTC is Tuesday 08:00 in Sydney (UTC+10)
    utc_moment = datetime(2026, 8, 3, 22, 0, tzinfo=_tz.utc)
    assert send_window.is_open(w, utc_moment)
    assert send_window.next_opening(w, utc_moment).tzinfo is not None
    # ...and 2026-08-03 12:00 UTC is Monday 22:00 Sydney — shut
    assert not send_window.is_open(w, datetime(2026, 8, 3, 12, 0, tzinfo=_tz.utc))


def test_a_spring_forward_hour_that_does_not_exist_is_never_promised():
    """`probe += timedelta(hours=1)` is wall-clock arithmetic, so it can land
    on 02:00 on a US changeover day — a time that never occurs. Returning it
    promises a moment that never arrives and the batch waits a whole week."""
    from datetime import timezone as _tz
    w = _window(timezone="America/New_York", days=[6], start_hour=2, end_hour=3)
    saturday = datetime(2026, 3, 7, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    opening = send_window.next_opening(w, saturday)
    assert opening is not None
    # whatever it picked, it must be a real instant that round-trips
    assert opening == opening.astimezone(_tz.utc).astimezone(opening.tzinfo)


def test_a_skipped_hour_is_normalized_on_the_default_no_timezone_setting():
    """`timezone: ""` is the shipped default — "use this machine's clock" — so
    a host in a DST zone takes the naive path every single time. Short-circuiting
    _real_instant on naive input left the spring-forward hole open on precisely
    the configuration most people run: next_opening would answer 02:00 on the
    changeover Sunday, a wall time that never occurs, is_open would be False all
    day, and the batch would wait another week.
    """
    import time as _time
    if not hasattr(_time, "tzset"):
        pytest.skip("no tzset on this platform")
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    _time.tzset()
    try:
        # Sunday 2026-03-08: the clock goes 01:59:59 EST -> 03:00:00 EDT.
        w = _window(timezone="", days=[6], start_hour=2, end_hour=4)
        saturday = datetime(2026, 3, 7, 12, 0)
        opening = send_window.next_opening(w, saturday)
        assert opening is not None
        assert opening.hour != 2, f"promised an hour that never occurs: {opening}"
        # ...and whatever it picked is a moment the window really is open at
        assert send_window.is_open(w, opening), opening
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        _time.tzset()


def test_the_detected_timezone_is_a_region_not_a_fixed_offset_alias(monkeypatch):
    """'EST' is a real tzdb entry — the legacy fixed UTC-05:00 zone that never
    observes DST — so accepting it left the suggestion an hour wrong for eight
    months of the year, silently, because it parses.

    Forced down the abbreviation path rather than trusting whatever this
    machine happens to report: on a box where tzlocal answers correctly the
    fallback is never reached, and the bug lived there."""
    import time as _time
    import builtins
    real_import = builtins.__import__

    def _no_tzlocal(name, *a, **kw):
        if name == "tzlocal":
            raise ImportError("not installed")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _no_tzlocal)
    monkeypatch.setattr(_time, "tzname", ("EST", "EDT"))
    assert send_window.local_timezone_name() == ""

    monkeypatch.setattr(_time, "tzname", ("America/New_York", "EDT"))
    assert send_window.local_timezone_name() == "America/New_York"
    monkeypatch.undo()

    # The tzlocal branch itself, which the ImportError above never reaches —
    # asserting only "" or a slash-name was satisfied by "", i.e. by the branch
    # not existing at all.
    fake = types.ModuleType("tzlocal")
    fake.get_localzone = lambda: "Europe/Berlin"
    monkeypatch.setitem(sys.modules, "tzlocal", fake)
    monkeypatch.setattr(_time, "tzname", ("CET", "CEST"))
    assert send_window.local_timezone_name() == "Europe/Berlin"

    # tzlocal can answer 'local' on a machine with no zone configured; that is
    # not a region name, so the abbreviation scan gets its turn.
    fake.get_localzone = lambda: "local"
    monkeypatch.setattr(_time, "tzname", ("Europe/Berlin", "CEST"))
    assert send_window.local_timezone_name() == "Europe/Berlin"


def test_moving_the_address_takes_the_queued_mail_out(api):
    """A queued row resolves its recipient at send time, so correcting the
    address re-aims already-approved mail at a different human, days later,
    unattended. `force` does not cover it: nothing has been sent or attempted
    on a queued row, so the has-been-emailed guard never fires."""
    import asyncio
    from models import ContactUpdate
    sender, contact, draft = api
    main.db.update_send_window({"enabled": True, "days": [0, 1, 2, 3, 4],
                                "start_hour": 8, "end_hour": 17})
    main.send_emails(SendRequest(email_ids=[draft["id"]], schedule="next_window"))
    assert main.db.get_email(draft["id"])["scheduled_at"] is not None

    asyncio.run(main.update_contact(
        contact["id"], ContactUpdate(email="zztestmoved@example.com"), force=True))

    assert main.db.get_email(draft["id"])["scheduled_at"] is None


def test_a_queued_batch_never_replays_a_resend_confirmation(api, monkeypatch):
    """"Send it anyway" is a statement about the rows in front of the user at
    that moment. Days later a different row in the same batch can have picked
    up an unconfirmed attempt, and honouring the stored flag would hand that
    person a second copy on the strength of a decision about someone else."""
    sender, contact, draft = api
    main.db.update_send_window({"enabled": True, "days": [0, 1, 2, 3, 4],
                                "start_hour": 8, "end_hour": 17})
    monkeypatch.setattr(send_window, "is_open", lambda w, now=None: True)
    main.send_emails(SendRequest(email_ids=[draft["id"]], confirm_resend=True,
                                 schedule="next_window"))
    # the row's delivery becomes unknown after it was queued
    main.db.update_email(draft["id"], {"send_attempted_at": _recently(hours=2)})
    main.db.execute("UPDATE emails SET scheduled_at=? WHERE id=?",
                    (_recently(), draft["id"]))

    job_id = main.scheduled_send_sweep()
    if job_id:
        _await(job_id)

    assert sender.calls == []          # left for a human, not sent twice


def test_a_queued_batch_keeps_the_resume_it_was_written_around(api, monkeypatch):
    """Promoting a different default resume between queueing and sending used
    to restaple a PDF the draft was never written around — invisibly, because
    the row's own resume_id was NULL so the attachment check saw no change."""
    sender, contact, draft = api
    main.db.update_send_window({"enabled": True, "days": [0, 1, 2, 3, 4],
                                "start_hour": 8, "end_hour": 17})
    monkeypatch.setattr(main.db, "get_default_resume", lambda: {"id": "resume-A"})
    monkeypatch.setattr(main.resumes, "resolve_attachment_path",
                        lambda rid: f"/tmp/{rid}.pdf" if rid else None)

    main.send_emails(SendRequest(email_ids=[draft["id"]], attach_resume=True,
                                 schedule="next_window"))
    assert main.db.get_email(draft["id"])["resume_id"] == "resume-A"

    # the user promotes a different default before the window opens
    monkeypatch.setattr(main.db, "get_default_resume", lambda: {"id": "resume-B"})
    monkeypatch.setattr(send_window, "is_open", lambda w, now=None: True)
    main.db.execute("UPDATE emails SET scheduled_at=? WHERE id=?",
                    (_recently(), draft["id"]))

    _await(main.scheduled_send_sweep())

    assert sender.options[0]["resume_path"] == "/tmp/resume-A.pdf"


def test_a_confirmed_attachment_change_survives_the_queue(api, monkeypatch):
    """The mirror of the recheck: a conflict the user was warned about and
    accepted must still go out, or the sweep silently unschedules mail they
    explicitly approved."""
    sender, contact, draft = api
    main.db.update_send_window({"enabled": True, "days": [0, 1, 2, 3, 4],
                                "start_hour": 8, "end_hour": 17})
    monkeypatch.setattr(send_window, "is_open", lambda w, now=None: True)
    main.db.execute("UPDATE emails SET body=? WHERE id=?",
                    ("Hi there,\n\nMy resume is attached.", draft["id"]))

    main.send_emails(SendRequest(email_ids=[draft["id"]], attach_resume=False,
                                 confirm_attachment_change=True,
                                 schedule="next_window"))
    main.db.execute("UPDATE emails SET scheduled_at=? WHERE id=?",
                    (_recently(), draft["id"]))

    _await(main.scheduled_send_sweep())

    assert sender.calls == [draft["id"]]


def test_the_reported_next_opening_is_in_the_same_frame_as_the_queue(api):
    """Two frames in one payload with no marker on either: the "Send at Mon
    8:00" button and the "queued Sun 10:00 PM" chip named the same instant ten
    hours apart, and a user who reads the button and closes the app cannot
    tell which is the bug."""
    import asyncio
    sender, contact, draft = api
    # A zone deliberately far from any plausible host, so the two frames differ.
    main.db.update_send_window({"enabled": True, "timezone": "Pacific/Kiritimati",
                                "days": [0, 1, 2, 3, 4, 5, 6],
                                "start_hour": 8, "end_hour": 9})
    reported = asyncio.run(main.get_send_window())
    if reported["open_now"]:
        return                                   # nothing to schedule against
    main.send_emails(SendRequest(email_ids=[draft["id"]], schedule="next_window"))

    stamp = main.db.get_email(draft["id"])["scheduled_at"]
    assert reported["next_opening"] == stamp, (reported["next_opening"], stamp)


def _await(job_id, timeout=10.0):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = main.db.get_job(job_id)
        if job and job["status"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} never finished")
