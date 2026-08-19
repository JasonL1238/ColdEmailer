"""Does this mailbox exist? — asked without ever sending mail.

A pattern guess like ken.hirsch@gs.com is plausible and unusable. Asking the
receiving server whether the mailbox exists is the only way to tell, and there
are exactly two ways to ask:

  - Speak SMTP to the MX directly. Free and precise, but network-dependent:
    outbound TCP/25 is blocked by many consumer ISPs as an anti-spam measure.
    It was blocked on the developer's home network on 2026-08-16, while an
    iPhone hotspot reached Google, Zoho and Proofpoint MX hosts on 2026-08-19.
  - Ask a verification provider over HTTPS, which runs that same conversation
    from an IP that is allowed to. Port 443 is unaffected.

So `backend="auto"` tries direct SMTP first and asks Hunter only when SMTP is
unavailable or inconclusive. It returns an honest `unknown` when neither can
answer.

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
import socket
import ssl
import time
from dataclasses import dataclass
from enum import Enum
from typing import BinaryIO, Callable, Dict, List, Optional, Protocol, Tuple

from contact_verify import normalize_email
from ttl_cache import cache_clear, cache_get as _cache_get, cache_put as _cache_put
from web_scraper import resolve_public_tcp_addresses

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
_SMTP_CONNECT_TIMEOUT = 5.0
_SMTP_MAX_MX_HOSTS = 3
_SMTP_MAX_REPLY_LINES = 100
_SMTP_MAX_REPLY_BYTES = 64 * 1024

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
    def read_reply(self, timeout: float) -> Tuple[int, str]: ...
    def send_command(self, verb: str, arg: str = "") -> None: ...
    def starttls(self) -> None: ...
    def close(self) -> None: ...


Connector = Callable[[str], Optional[SmtpTransport]]


class _SocketSmtpTransport:
    """Minimal SMTP socket restricted to the verifier's command whitelist."""

    def __init__(self, sock: socket.socket, server_hostname: str):
        self.sock = sock
        self.server_hostname = server_hostname
        self.reader: BinaryIO = sock.makefile("rb")

    def read_reply(self, timeout: float) -> Tuple[int, str]:
        self.sock.settimeout(timeout)
        lines: List[str] = []
        total = 0
        code: Optional[int] = None
        for _ in range(_SMTP_MAX_REPLY_LINES):
            raw = self.reader.readline(4096)
            if not raw:
                raise OSError("SMTP peer closed before completing its reply")
            total += len(raw)
            if total > _SMTP_MAX_REPLY_BYTES:
                raise OSError("SMTP reply exceeded safety limit")
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            lines.append(line)
            if len(line) < 3 or not line[:3].isdigit():
                raise OSError("Malformed SMTP reply")
            current = int(line[:3])
            if code is None:
                code = current
            elif current != code:
                raise OSError("Inconsistent SMTP reply codes")
            if len(line) == 3 or line[3:4] == " ":
                return code, "\n".join(lines)
            if line[3:4] != "-":
                raise OSError("Malformed SMTP continuation")
        raise OSError("SMTP reply used too many continuation lines")

    def send_command(self, verb: str, arg: str = "") -> None:
        verb = (verb or "").upper()
        if verb not in ALLOWED_VERBS:
            raise ValueError(f"SMTP verb is not allowed: {verb}")
        if "\r" in arg or "\n" in arg:
            raise ValueError("SMTP command argument contains a newline")
        command = verb if not arg else f"{verb} {arg}"
        self.sock.sendall(command.encode("ascii") + b"\r\n")

    def starttls(self) -> None:
        self.reader.close()
        context = ssl.create_default_context()
        self.sock = context.wrap_socket(
            self.sock, server_hostname=self.server_hostname)
        self.reader = self.sock.makefile("rb")

    def close(self) -> None:
        try:
            self.reader.close()
        finally:
            self.sock.close()


def _mx_hosts(domain: str) -> List[str]:
    """MX targets in delivery order, with RFC A/AAAA fallback."""
    try:
        import dns.resolver
    except ImportError:  # pragma: no cover - dependency is required in prod
        return [domain]
    try:
        answers = dns.resolver.resolve(domain, "MX")
    except dns.resolver.NoAnswer:
        return [domain]
    except Exception:
        return []
    ranked = sorted(
        (int(getattr(answer, "preference", 0)),
         str(getattr(answer, "exchange", "")).rstrip("."))
        for answer in answers
    )
    # A lone dot is RFC 7505 null MX: this domain accepts no mail.
    return [host for _, host in ranked if host and host != "."]


def _connect_smtp_host(host: str, timeout: float) -> Optional[SmtpTransport]:
    for family, socktype, proto, _, sockaddr in resolve_public_tcp_addresses(
            host, 25):
        sock = socket.socket(family, socktype, proto)
        try:
            sock.settimeout(timeout)
            sock.connect(sockaddr)
            return _SocketSmtpTransport(sock, host)
        except OSError:
            sock.close()
    return None


def smtp_connector(domain: str) -> Optional[SmtpTransport]:
    """Connect to the first reachable public MX for ``domain`` on port 25."""
    for host in _mx_hosts(domain)[:_SMTP_MAX_MX_HOSTS]:
        transport = _connect_smtp_host(host, _SMTP_CONNECT_TIMEOUT)
        if transport is not None:
            return transport
    return None


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
                timeout: float = 10.0,
                skip_canary: bool = False) -> MailboxResult:
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
        mail_from = "FROM:<>"
        transport.send_command("MAIL", mail_from)
        code, _ = transport.read_reply(timeout)
        if code >= 500:
            sender = os.getenv("SMTP_PROBE_FROM", "").strip()
            if not sender:
                return _result(email, MailboxVerdict.UNKNOWN,
                               "null_sender_rejected", code=code)
            mail_from = f"FROM:<{sender}>"
            transport.send_command("MAIL", mail_from)
            if transport.read_reply(timeout)[0] >= 400:
                return _result(email, MailboxVerdict.UNKNOWN,
                               "sender_rejected", code=code)

        if not skip_canary:
            canary_code = _rcpt(transport, _canary(domain), timeout)
            if (canary_code is None
                    or 400 <= canary_code < 500):
                return _result(email, MailboxVerdict.UNKNOWN, "greylisted",
                               code=canary_code)
            if canary_code < 300:
                # The server accepts addresses that cannot exist, so nothing
                # it says about the real one carries information. Do not ask.
                _cache_put(_ACCEPT_ALL_CACHE, domain, True,
                           _ACCEPT_ALL_TTL, _ACCEPT_ALL_MAX)
                return _result(
                    email, MailboxVerdict.ACCEPT_ALL, "catch_all",
                    provider="smtp", accept_all=True, code=canary_code)
            if canary_code not in (550, 551, 553):
                # A sequence, policy, storage, or transaction failure does not
                # prove the random mailbox was rejected as nonexistent.
                return _result(email, MailboxVerdict.UNKNOWN,
                               "canary_inconclusive", provider="smtp",
                               code=canary_code)
            _cache_put(_ACCEPT_ALL_CACHE, domain, False,
                       _ACCEPT_ALL_TTL, _ACCEPT_ALL_MAX)

            # RSET clears the envelope, including MAIL FROM. Start a fresh
            # transaction before asking about the real address.
            transport.send_command("RSET")
            if transport.read_reply(timeout)[0] >= 400:
                return _result(email, MailboxVerdict.UNKNOWN, "rset_failed",
                               provider="smtp", accept_all=False)
            transport.send_command("MAIL", mail_from)
            mail_code, _ = transport.read_reply(timeout)
            if mail_code >= 400:
                return _result(email, MailboxVerdict.UNKNOWN,
                               "sender_rejected_after_rset", provider="smtp",
                               accept_all=False, code=mail_code)
        code = _rcpt(transport, email, timeout)
        if code is None or 400 <= code < 500:
            return _result(email, MailboxVerdict.UNKNOWN, "greylisted",
                           provider="smtp", accept_all=False, code=code)
        if code < 300:
            return _result(email, MailboxVerdict.DELIVERABLE, "ok",
                           provider="smtp", accept_all=False, code=code)
        if code in (550, 551, 553):
            return _result(email, MailboxVerdict.UNDELIVERABLE, "rejected",
                           provider="smtp", accept_all=False, code=code)
        return _result(email, MailboxVerdict.UNKNOWN, "target_inconclusive",
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
    """Ask whether `email` accepts mail. Never sends anything.

    ``auto`` tries direct SMTP first.  Hunter is consulted only when SMTP is
    unavailable or inconclusive; a definitive SMTP accept/reject spends no
    provider credit and does not disclose the address to the provider.
    """
    email = normalize_email(email)
    if not email or "@" not in email:
        return _result(email, MailboxVerdict.UNKNOWN, "invalid_address")
    if not _enabled():
        return _result(email, MailboxVerdict.UNKNOWN, "disabled")

    provider_key = (api_key if api_key is not None
                    else os.getenv("HUNTER_API_KEY", "").strip())
    cache_key = f"{backend}:{bool(provider_key)}:{email}"
    cached = _cache_get(_RESULT_CACHE, cache_key)
    if cached is not None:
        return cached

    domain = email.split("@", 1)[-1]
    known_accept_all = _cache_get(_ACCEPT_ALL_CACHE, domain)
    if known_accept_all is True:
        return _result(email, MailboxVerdict.ACCEPT_ALL, "catch_all_cached",
                       accept_all=True)

    result: Optional[MailboxResult] = None
    if backend in ("auto", "smtp"):
        result = _smtp_probe(
            email, connector=connector or smtp_connector,
            skip_canary=known_accept_all is False)
        if backend == "auto" and result.verdict in (
                MailboxVerdict.UNKNOWN, MailboxVerdict.ACCEPT_ALL):
            provider_result = _hunter_verify(
                email, api_key=api_key, http_get=http_get)
            if provider_result is not None:
                result = provider_result
    elif backend == "api":
        result = _hunter_verify(email, api_key=api_key, http_get=http_get)
    if result is None:
        result = _result(email, MailboxVerdict.UNKNOWN, "no_transport")

    ttl = (_RESULT_TTL if result.verdict is not MailboxVerdict.UNKNOWN
           else _RESULT_TTL_UNKNOWN)
    _cache_put(_RESULT_CACHE, cache_key, result, ttl, _RESULT_MAX)
    return result
