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
import tempfile
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

import main
import send_window
from db import Database
from models import SendRequest, SendWindowUpdate

_seq = itertools.count()

# Monday 2026-08-03 at 09:00 — inside a default weekday 8–5 window.
MONDAY_9AM = datetime(2026, 8, 3, 9, 0)


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        yield Database(os.path.join(tmp, "test.db"))


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
    assert send_window.normalize_send_window(
        {"enabled": True, "start_hour": 17, "end_hour": 17})["end_hour"] == 17
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
        self.authenticated = 0

    def authenticate(self):
        self.authenticated += 1

    def send_email(self, email, from_email, resume_path=None):
        self.calls.append(email["id"])
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
    main.db.update_email(draft["id"], {"scheduled_at": "2020-01-01T09:00:00",
                                       "status": "approved"})

    assert main.scheduled_send_sweep() is None
    assert sender.calls == []
    assert sender.authenticated == 0


def test_the_sweep_does_nothing_outside_the_window(api, monkeypatch):
    sender, contact, draft = api
    main.db.update_send_window({"enabled": True, "days": [0, 1, 2, 3, 4],
                                "start_hour": 8, "end_hour": 17})
    main.db.update_email(draft["id"], {"scheduled_at": "2020-01-01T09:00:00",
                                       "status": "approved"})
    monkeypatch.setattr(send_window, "is_open", lambda w, now=None: False)

    assert main.scheduled_send_sweep() is None
    assert sender.calls == []


def test_the_sweep_sends_what_is_due_and_clears_the_stamp(api, monkeypatch):
    sender, contact, draft = api
    main.db.update_send_window({"enabled": True, "days": [0, 1, 2, 3, 4],
                                "start_hour": 8, "end_hour": 17})
    main.db.update_email(draft["id"], {"scheduled_at": "2020-01-01T09:00:00",
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
    main.db.update_email(draft["id"], {"scheduled_at": "2020-01-01T09:00:00",
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
    main.db.update_email(draft["id"], {"scheduled_at": "2020-01-01T09:00:00",
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
    main.db.update_email(draft["id"], {"scheduled_at": "2020-01-01T09:00:00",
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


def _await(job_id, timeout=10.0):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = main.db.get_job(job_id)
        if job and job["status"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} never finished")
