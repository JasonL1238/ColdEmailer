"""Does this mailbox exist? — asked without ever sending mail.

A pattern guess like ken.hirsch@gs.com is plausible and unusable. Asking the
receiving server whether the mailbox exists is the only way to tell, and there
are exactly two ways to ask:

  - Speak SMTP to the MX directly. Free and precise, and impossible on most
    home networks: outbound TCP/25 is blocked by nearly every consumer ISP as
    an anti-spam measure. Measured on the developer's own machine — port 587
    to Gmail opens in 0.26s with a real banner, while port 25 to Google, OVH,
    Zoho, Fastmail and Proofpoint all time out. Proofpoint *refusing* 465/587
    in 70ms while 25 hangs proves the route is healthy and the filter is
    port-25-specific.
  - Ask a verification provider over HTTPS, which runs that same conversation
    from an IP that is allowed to. Port 443 is unaffected.

So the transport is pluggable and `backend="auto"` picks whichever is actually
available, returning an honest `unknown` when neither is.

WHAT A RESULT MEANS, precisely, because it is easy to over-read:
a `deliverable` verdict says a mailbox exists at that address on that server.
It does NOT say the mailbox belongs to your target — john.smith@gs.com may
well accept mail and be a different John Smith — and it does not promise
delivery, since filtering and reputation all happen after RCPT. That is why a
result here can never write `contacts.email_verified`, which asserts the
stronger claim that the address matches *this person*.
"""
from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, Optional, Protocol, Tuple

from contact_verify import normalize_email
from ttl_cache import cache_clear, cache_get as _cache_get, cache_put as _cache_put

# Only these ever reach a socket. DATA/BDAT are absent by construction, not by
# review: the transport raises on anything outside this set, and a test asserts
# the set itself. This module must never be able to send mail.
ALLOWED_VERBS = frozenset(
    {"EHLO", "HELO", "STARTTLS", "MAIL", "RCPT", "RSET", "NOOP", "QUIT"})

_ACCEPT_ALL_TTL = 3600.0        # a catch-all policy does not flip within an hour
_ACCEPT_ALL_MAX = 512
_RESULT_TTL = 900.0             # mailboxes are created and deleted
_RESULT_TTL_UNKNOWN = 60.0
_RESULT_MAX = 2048

_ACCEPT_ALL_CACHE: Dict[str, Tuple[float, Optional[bool]]] = {}
_RESULT_CACHE: Dict[str, Tuple[float, "MailboxResult"]] = {}


class MailboxVerdict(str, Enum):
    DELIVERABLE = "deliverable"
    UNDELIVERABLE = "undeliverable"
    ACCEPT_ALL = "accept_all"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MailboxResult:
    email: str
    verdict: MailboxVerdict
    reason: str
    provider: Optional[str] = None
    accept_all: Optional[bool] = None
    code: Optional[int] = None
    checked_at: float = 0.0


class SmtpTransport(Protocol):  # pragma: no cover - structural type
    def connect(self, host: str, port: int, timeout: float) -> None: ...
    def read_reply(self, timeout: float) -> Tuple[int, str]: ...
    def send_command(self, verb: str, arg: str = "") -> None: ...
    def starttls(self) -> None: ...
    def close(self) -> None: ...


Connector = Callable[[str], Optional[SmtpTransport]]


def _enabled() -> bool:
    return os.getenv("MAILBOX_VERIFY", "0").strip() != "0"


def _canary(domain: str) -> str:
    """An address that cannot exist, regenerated every probe.

    A fixed canary would eventually be denylisted and start answering like a
    real mailbox, which would silently turn every verdict into a lie.
    """
    return f"zz-probe-{secrets.token_hex(16)}@{domain}"


def clear_mailbox_cache() -> None:
    cache_clear(_ACCEPT_ALL_CACHE, _RESULT_CACHE)


def _result(email, verdict, reason, **kw) -> MailboxResult:
    return MailboxResult(email=email, verdict=verdict, reason=reason,
                         checked_at=time.time(), **kw)


# --------------------------------------------------------------------------
# HTTPS provider transport (works where port 25 is blocked)
# --------------------------------------------------------------------------

def _hunter_verify(email: str, *, api_key=None, http_get=None
                   ) -> Optional[MailboxResult]:
    key = (api_key if api_key is not None
           else os.getenv("HUNTER_API_KEY", "").strip())
    if not key:
        return None
    getter = http_get
    if getter is None:
        try:
            import requests
        except ImportError:  # pragma: no cover
            return None
        getter = requests.get
    try:
        resp = getter("https://api.hunter.io/v2/email-verifier",
                      params={"email": email, "api_key": key}, timeout=10)
        if getattr(resp, "status_code", 0) != 200:
            return None
        data = (resp.json() or {}).get("data") or {}
    except Exception:
        return None
    status = (data.get("status") or "").lower()
    # Map conservatively. accept_all/webmail/unknown are NOT deliverability
    # answers, and promoting them would be exactly the lie this module exists
    # to avoid.
    if status == "valid":
        return _result(email, MailboxVerdict.DELIVERABLE, "provider_valid",
                       provider="hunter", accept_all=False)
    if status == "invalid":
        return _result(email, MailboxVerdict.UNDELIVERABLE,
                       "provider_invalid", provider="hunter", accept_all=False)
    if status in ("accept_all", "webmail"):
        return _result(email, MailboxVerdict.ACCEPT_ALL, f"provider_{status}",
                       provider="hunter", accept_all=True)
    return _result(email, MailboxVerdict.UNKNOWN, f"provider_{status or 'unknown'}",
                   provider="hunter")


# --------------------------------------------------------------------------
# Direct SMTP transport (works only where outbound 25 is open)
# --------------------------------------------------------------------------

def _rcpt(transport, address: str, timeout: float) -> Optional[int]:
    transport.send_command("RCPT", f"TO:<{address}>")
    code, _ = transport.read_reply(timeout)
    return code


def _smtp_probe(email: str, *, connector: Connector,
                timeout: float = 10.0) -> MailboxResult:
    helo = os.getenv("SMTP_PROBE_HELO", "").strip()
    if not helo:
        # An unqualified HELO name is refused by many MTAs, so without a real
        # FQDN the probe cannot produce an answer worth having.
        return _result(email, MailboxVerdict.UNKNOWN, "no_helo_configured")
    domain = email.split("@", 1)[-1]
    transport = None
    try:
        transport = connector(domain)
        if transport is None:
            return _result(email, MailboxVerdict.UNKNOWN, "port_25_blocked")
        code, _ = transport.read_reply(timeout)
        if code != 220:
            return _result(email, MailboxVerdict.UNKNOWN, "bad_greeting",
                           code=code)
        transport.send_command("EHLO", helo)
        code, caps = transport.read_reply(timeout)
        if code != 250:
            return _result(email, MailboxVerdict.UNKNOWN, "ehlo_refused",
                           code=code)
        if "starttls" in (caps or "").lower():
            try:
                transport.send_command("STARTTLS")
                if transport.read_reply(timeout)[0] == 220:
                    transport.starttls()
                    transport.send_command("EHLO", helo)
                    transport.read_reply(timeout)
            except Exception:
                return _result(email, MailboxVerdict.UNKNOWN, "tls_failed")
        transport.send_command("MAIL", "FROM:<>")
        code, _ = transport.read_reply(timeout)
        if code >= 500:
            sender = os.getenv("SMTP_PROBE_FROM", "").strip()
            if not sender:
                return _result(email, MailboxVerdict.UNKNOWN,
                               "null_sender_rejected", code=code)
            transport.send_command("MAIL", f"FROM:<{sender}>")
            if transport.read_reply(timeout)[0] >= 400:
                return _result(email, MailboxVerdict.UNKNOWN,
                               "sender_rejected", code=code)

        canary_code = _rcpt(transport, _canary(domain), timeout)
        if canary_code is None or canary_code >= 400 and canary_code < 500:
            return _result(email, MailboxVerdict.UNKNOWN, "greylisted",
                           code=canary_code)
        if canary_code < 300:
            # The server accepts addresses that cannot exist, so nothing it
            # says about the real one carries information. Do not even ask.
            _cache_put(_ACCEPT_ALL_CACHE, domain, True,
                       _ACCEPT_ALL_TTL, _ACCEPT_ALL_MAX)
            return _result(email, MailboxVerdict.ACCEPT_ALL, "catch_all",
                           provider="smtp", accept_all=True, code=canary_code)
        _cache_put(_ACCEPT_ALL_CACHE, domain, False,
                   _ACCEPT_ALL_TTL, _ACCEPT_ALL_MAX)

        transport.send_command("RSET")
        transport.read_reply(timeout)
        code = _rcpt(transport, email, timeout)
        if code is None or 400 <= code < 500:
            return _result(email, MailboxVerdict.UNKNOWN, "greylisted",
                           provider="smtp", accept_all=False, code=code)
        if code < 300:
            return _result(email, MailboxVerdict.DELIVERABLE, "ok",
                           provider="smtp", accept_all=False, code=code)
        return _result(email, MailboxVerdict.UNDELIVERABLE, "rejected",
                       provider="smtp", accept_all=False, code=code)
    except Exception:
        return _result(email, MailboxVerdict.UNKNOWN, "smtp_error")
    finally:
        if transport is not None:
            try:
                transport.send_command("QUIT")
                transport.close()
            except Exception:
                pass


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def verify_mailbox(email: str, *, backend: str = "auto", http_get=None,
                   connector: Optional[Connector] = None,
                   api_key=None) -> MailboxResult:
    """Ask whether `email` accepts mail. Never sends anything."""
    email = normalize_email(email)
    if not email or "@" not in email:
        return _result(email, MailboxVerdict.UNKNOWN, "invalid_address")
    if not _enabled():
        return _result(email, MailboxVerdict.UNKNOWN, "disabled")

    cached = _cache_get(_RESULT_CACHE, email)
    if cached is not None:
        return cached

    domain = email.split("@", 1)[-1]
    known_accept_all = _cache_get(_ACCEPT_ALL_CACHE, domain)
    if known_accept_all is True:
        return _result(email, MailboxVerdict.ACCEPT_ALL, "catch_all_cached",
                       accept_all=True)

    result: Optional[MailboxResult] = None
    if backend in ("auto", "smtp") and connector is not None:
        result = _smtp_probe(email, connector=connector)
        if backend == "auto" and result.reason in (
                "port_25_blocked", "smtp_error", "no_helo_configured"):
            result = None  # fall through to the provider
    if result is None and backend in ("auto", "api"):
        result = _hunter_verify(email, api_key=api_key, http_get=http_get)
    if result is None:
        result = _result(email, MailboxVerdict.UNKNOWN, "no_transport")

    ttl = (_RESULT_TTL if result.verdict is not MailboxVerdict.UNKNOWN
           else _RESULT_TTL_UNKNOWN)
    _cache_put(_RESULT_CACHE, email, result, ttl, _RESULT_MAX)
    return result
