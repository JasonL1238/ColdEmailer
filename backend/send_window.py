"""When it is reasonable to put a cold email in someone's inbox.

A message sent at 03:00 is at the bottom of the pile by the time its recipient
opens their laptop; one sent at 09:15 on a Tuesday is near the top. This module
answers two questions and nothing else:

    * is *now* inside the sending window?
    * if not, when does it next open?

Deliberately pure — no database, no clock of its own, no side effects — because
the thing it decides is when real mail leaves. Every branch is exercised
directly in tests rather than inferred from a scheduler's behaviour.

The window is the *sender's* local business hours, not the recipient's. We
rarely know where a recipient is (the company `location` field is a scraped
free-text string like "Remote / NYC"), and guessing wrong is worse than not
guessing: a confident 09:00 in the wrong zone is 04:00 somewhere real.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

try:
    from zoneinfo import ZoneInfo, available_timezones
except ImportError:                                     # pragma: no cover
    ZoneInfo = None
    available_timezones = None


# Monday is 0, matching datetime.weekday(). Weekdays only by default: a cold
# email arriving on Sunday reads as automated, because it usually is.
DEFAULT_DAYS = [0, 1, 2, 3, 4]
DEFAULT_START_HOUR = 8
DEFAULT_END_HOUR = 17

# Off. Enabling it is what lets a background thread hand mail to a live Gmail
# token with nobody watching, so it is a decision the user makes explicitly
# rather than one an upgrade makes for them.
SEND_WINDOW_DEFAULT = {
    "enabled": False,
    "timezone": "",              # empty = this machine's local time
    "days": list(DEFAULT_DAYS),
    "start_hour": DEFAULT_START_HOUR,
    "end_hour": DEFAULT_END_HOUR,
}


def local_timezone_name() -> str:
    """The machine's IANA zone name, or "" when it cannot be determined.

    Only region/city names are accepted. `time.tzname` gives abbreviations —
    ('EST', 'EDT') on a New York machine — and 'EST' happens to *be* a tzdb
    entry: the legacy fixed UTC-05:00 zone that never observes daylight saving.
    Taking it left the suggested timezone an hour wrong for eight months of the
    year, silently, because it parses perfectly. A '/' is what separates a real
    region from those fixed-offset aliases.
    """
    try:
        import tzlocal                                  # optional, exact
        name = str(tzlocal.get_localzone())
        if "/" in name and resolve_timezone(name):
            return name
    except Exception:
        pass
    try:
        zones = available_timezones() if available_timezones else set()
        import time as _time
        for candidate in (getattr(_time, "tzname", ()) or ()):
            if candidate and "/" in candidate and candidate in zones:
                return candidate
    except Exception:
        pass
    return ""


def resolve_timezone(name: Optional[str]):
    """A tzinfo for `name`, or None meaning "use naive local time".

    Returning None rather than raising: an unknown zone must not be able to
    stop sending altogether, and local time is the honest fallback — it is what
    every other timestamp in this app already uses.
    """
    if not name or ZoneInfo is None:
        return None
    try:
        return ZoneInfo(str(name))
    except Exception:
        return None


def normalize_send_window(raw: Any) -> Dict[str, Any]:
    """Coerce stored or posted config into something safe to act on.

    Total by design. A corrupt settings row must not be able to widen the
    window, and must not be able to break sending either — the worst it can do
    is fall back to the default weekday 08:00–17:00.
    """
    if not isinstance(raw, dict):
        raw = {}

    def _hour(key: str, fallback: int) -> int:
        value = raw.get(key, fallback)
        if isinstance(value, bool):
            return fallback
        try:
            return max(0, min(23, int(value)))
        except (TypeError, ValueError):
            return fallback

    days: List[int] = []
    for value in (raw.get("days") if isinstance(raw.get("days"), (list, tuple))
                  else DEFAULT_DAYS):
        if isinstance(value, bool):
            continue
        try:
            day = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= day <= 6 and day not in days:
            days.append(day)
    days.sort()

    start = _hour("start_hour", DEFAULT_START_HOUR)
    end = _hour("end_hour", DEFAULT_END_HOUR)
    if end <= start:
        # A zero-or-negative-length window would make next_opening loop
        # forever looking for a minute that never comes. Widen to the default
        # rather than silently never sending.
        start, end = DEFAULT_START_HOUR, DEFAULT_END_HOUR
    if not days:
        days = list(DEFAULT_DAYS)

    zone = str(raw.get("timezone") or "").strip()
    if zone and resolve_timezone(zone) is None:
        zone = ""                                # unknown name → local time

    return {"enabled": bool(raw.get("enabled")), "timezone": zone,
            "days": days, "start_hour": start, "end_hour": end}


def _in_window(moment: datetime, window: Dict[str, Any]) -> bool:
    return (moment.weekday() in window["days"]
            and window["start_hour"] <= moment.hour < window["end_hour"])


def is_open(window: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    """Is the window open right now, in the window's own timezone?

    A disabled window is never open. The scheduler checks `enabled` first
    anyway, but this function's name invites use as a standalone gate, and the
    safe answer has to be the one a forgetful caller gets.
    """
    window = normalize_send_window(window)
    if not window["enabled"]:
        return False
    return _in_window(_now_in_zone(window, now), window)


def _now_in_zone(window: Dict[str, Any], now: Optional[datetime]) -> datetime:
    zone = resolve_timezone(window.get("timezone"))
    if now is not None:
        # A caller-supplied moment is the truth; convert only if both sides
        # carry zone information, so tests can pass naive local datetimes.
        if zone is not None and now.tzinfo is not None:
            return now.astimezone(zone)
        return now
    return datetime.now(zone) if zone is not None else datetime.now()


def next_opening(window: Dict[str, Any],
                 now: Optional[datetime] = None) -> Optional[datetime]:
    """When the window is next open, as a datetime in the window's timezone.

    Returns `now` unchanged when it is already open, and None when the window
    is disabled — callers read None as "no scheduling applies, send now".

    Bounded to a fortnight of lookahead: `days` cannot be empty after
    normalization, so a week is always enough, and the bound means a future
    change to that invariant produces a wrong answer rather than a hung
    request thread.
    """
    window = normalize_send_window(window)
    if not window["enabled"]:
        return None
    moment = _now_in_zone(window, now)
    if _in_window(moment, window):
        return moment
    # Walk forward to the start of the next allowed day/hour. Minute-level
    # precision is pointless here; the opening minute of the window is the
    # answer, and stepping by hour keeps the arithmetic obvious.
    probe = moment.replace(minute=0, second=0, microsecond=0)
    for _ in range(24 * 15):
        probe += timedelta(hours=1)
        # Wall-clock arithmetic enumerates hours that do not exist on a
        # spring-forward day — 02:00 on the US changeover is 01:59:59 EST
        # followed by 03:00:00 EDT. Returning one would promise a moment that
        # never arrives, and the batch would sit unsent until the next week's
        # opening. Normalizing through UTC maps a skipped hour onto the real
        # instant the clock jumped to.
        probe = _real_instant(probe)
        if probe.weekday() in window["days"] and probe.hour == window["start_hour"]:
            return probe
        if _in_window(probe, window):
            return probe
    return None


def _real_instant(moment: datetime) -> datetime:
    """Map a wall time onto the instant that actually occurs in its zone."""
    if moment.tzinfo is None:
        return moment
    try:
        return moment.astimezone(timezone.utc).astimezone(moment.tzinfo)
    except Exception:
        return moment


def describe(window: Dict[str, Any]) -> str:
    """One line the UI can print, so the setting and its effect cannot drift."""
    window = normalize_send_window(window)
    if not window["enabled"]:
        return "Sending is not held for business hours."
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    chosen = [names[d] for d in window["days"]]
    if window["days"] == DEFAULT_DAYS:
        when = "weekdays"
    elif len(chosen) == 7:
        when = "every day"
    else:
        when = ", ".join(chosen)
    zone = window["timezone"] or "your local time"
    return (f"{_clock(window['start_hour'])}–{_clock(window['end_hour'])} on "
            f"{when}, {zone}.")


def _clock(hour: int) -> str:
    suffix = "am" if hour < 12 else "pm"
    display = hour % 12 or 12
    return f"{display}{suffix}"
