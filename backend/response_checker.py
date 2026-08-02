"""Check Gmail threads for genuine replies to sent emails.

Bounces and auto-replies are not replies. They were also, until now, thrown
away entirely — a bounce came back as "no reply", which is precisely the
condition that makes a contact a follow-up candidate. So a hard-bounced
address got chased on a schedule, and every retry damaged the sending
reputation that decides whether the *good* emails arrive.
"""
import re
from datetime import datetime
from email.utils import parseaddr
from typing import Dict, Optional, Tuple

# The postmaster told us the message could not be delivered.
#
# Provenance, not wording. Subject text is written by whoever is replying, and
# this app's own subject lines quote the target company's talking points — so a
# logistics company answering "Re: Delivery failure rates in your fleet" was
# being classified as a bounce, which discarded their reply, removed them from
# follow-ups and permanently refused every future send to them. A delivery
# status notification is structurally identifiable; its subject is not.
_BOUNCE_SENDER_RE = re.compile(r"\bmailer-daemon\b|\bpostmaster@", re.I)
_BOUNCE_SUBJECT_RE = re.compile(
    r"delivery status notification|delivery (?:has )?failed|delivery failure|"
    r"undeliverable|failure notice|returned mail|delivery incomplete|"
    r"address not found|mail delivery (?:failed|subsystem)|"
    r"couldn'?t be delivered|recipient.{0,20}(?:not found|rejected)",
    re.I)

# A machine answered on the human's behalf. Not a reply, but the mailbox is
# alive — an out-of-office is if anything a reason to follow up later.
#
# This list used to carry bounce wording too ("delivery status", "delivery
# failure", "undeliverable", "failure notice"). Those are not things an
# auto-responder says, and keeping them here silently discarded genuine replies
# from anyone whose subject touched delivery — which, for a tool that quotes a
# logistics company's own talking points back at them, is the common case.
# Bounce wording now lives only in _BOUNCE_SUBJECT_RE, where it is
# corroborating evidence rather than a verdict.
_AUTO_SENDER_RE = re.compile(
    # Anchored to the mailbox, not the whole header: a bare "postmaster" also
    # matched a person at postmasterlabs.com.
    r"\bmailer-daemon\b|postmaster@|no-?reply@|donotreply@|auto-?responder@",
    re.I)
_AUTO_SUBJECT_RE = re.compile(
    r"out of office|automatic reply|auto-?reply|autoreply|away from",
    re.I)

REPLY = "reply"
BOUNCE = "bounce"
AUTO = "auto"
OWN = "own"

# The headers this classification reads. The thread reader asks Gmail for the
# full message and hands the same names down, so both callers see the same
# evidence.
CLASSIFY_HEADERS = ["From", "Subject", "Auto-Submitted", "Content-Type",
                    "X-Failed-Recipients", "Return-Path"]


def classify_headers(headers: Dict[str, str],
                     own_address: Optional[str] = None) -> str:
    """REPLY | BOUNCE | AUTO | OWN from one message's headers.

    Pure, and the *only* implementation. The reply pane shows the user which
    messages in a thread are real replies, and the reply-rate counts them; if
    those two came from separate rules the app would show a message labelled
    "reply" beside a contact it still reports as never having answered. Keys
    are matched case-insensitively — Gmail's header casing is the sender's,
    not a constant.
    """
    headers = {str(k).lower(): (v or "") for k, v in (headers or {}).items()}
    sender = headers.get("from", "")
    subject = headers.get("subject", "")
    auto_submitted = headers.get("auto-submitted", "").lower()
    own = (own_address or "").strip().lower() or None

    # Our own mail first. The bounce test used to run ahead of this, so a
    # subject match bypassed the guard entirely.
    #
    # Compared as a parsed address, not as a substring of the header. `own in
    # sender` made every address that merely *contains* the user's own read as
    # the user: a profile of hr@acme.com classified a genuine reply from
    # chr@acme.com as OWN, which silently dropped it from the reply rate and
    # left that contact queued for another follow-up. No attacker needed —
    # short local parts collide on their own — though a sender is free to
    # arrange one, and a header this cheap to spoof should not be matched
    # loosely.
    if own and parseaddr(sender)[1].strip().lower() == own:
        return OWN

    # A bounce needs machine provenance. Any ONE of these is proof the
    # message came from a mail system rather than a person:
    #   * the daemon itself in From or Return-Path
    #   * an RFC 3464 delivery-status report
    #   * X-Failed-Recipients, which only a reporting MTA sets
    # A bounce-shaped subject is then corroborating evidence, never
    # evidence on its own.
    from_daemon = bool(
        _BOUNCE_SENDER_RE.search(sender)
        or _BOUNCE_SENDER_RE.search(headers.get("return-path", "")))
    is_report = "report-type=delivery-status" in (
        headers.get("content-type", "").lower().replace(" ", ""))
    failed_recipients = bool(headers.get("x-failed-recipients"))
    machine_sent = auto_submitted.startswith("auto-replied") or \
        auto_submitted.startswith("auto-generated")
    if from_daemon or is_report or failed_recipients or (
            machine_sent and _BOUNCE_SUBJECT_RE.search(subject)):
        return BOUNCE

    if _AUTO_SENDER_RE.search(sender):
        return AUTO
    if _AUTO_SUBJECT_RE.search(subject):
        return AUTO
    if auto_submitted and auto_submitted != "no":
        return AUTO
    return REPLY


class ResponseChecker:
    """Given an authenticated Gmail service, detect replies to sent messages."""

    def __init__(self, service, own_address: Optional[str] = None):
        self.service = service
        self.own_address = (own_address or "").strip().lower() or None

    def _classify(self, message_id: str) -> str:
        """REPLY | BOUNCE | AUTO | OWN for one message in the thread."""
        try:
            msg = self.service.users().messages().get(
                userId="me", id=message_id, format="metadata",
                metadataHeaders=list(CLASSIFY_HEADERS),
            ).execute()
            headers = {h["name"].lower(): h.get("value", "")
                       for h in msg.get("payload", {}).get("headers", [])}
            return classify_headers(headers, self.own_address)
        except Exception:
            # If we can't inspect it, count it as a reply — better a false
            # positive than silently dropping a real one. Never guess BOUNCE
            # here: that would mark a live address dead on a network blip.
            return REPLY

    def check_response(self, gmail_message_id: str,
                       gmail_thread_id: Optional[str] = None
                       ) -> Tuple[Optional[bool], Optional[datetime]]:
        """Returns (has_real_reply, reply_datetime). has_real_reply is None
        when the check itself failed (network error, rate limit, ...) —
        callers must treat that as 'unknown', never as 'no reply'."""
        verdict = self.check_thread(gmail_message_id, gmail_thread_id)
        return verdict["has_reply"], verdict["replied_at"]

    def check_thread(self, gmail_message_id: str,
                     gmail_thread_id: Optional[str] = None) -> Dict:
        """Everything one thread tells us.

        {has_reply, replied_at, bounced, bounced_at}. `has_reply` is None when
        the check itself failed — callers must treat that as 'unknown', never
        as 'no reply'. `bounced` is only ever True on positive evidence, so an
        unreachable Gmail cannot mark a live address dead.
        """
        blank = {"has_reply": False, "replied_at": None,
                 "bounced": False, "bounced_at": None}
        if not gmail_message_id:
            return blank
        try:
            thread_id = gmail_thread_id
            if not thread_id:
                original = self.service.users().messages().get(
                    userId="me", id=gmail_message_id, format="minimal"
                ).execute()
                thread_id = original.get("threadId")
            if not thread_id:
                return blank

            thread = self.service.users().threads().get(
                userId="me", id=thread_id, format="minimal"
            ).execute()
            messages = thread.get("messages", [])
            candidates = [
                m for m in messages
                if m.get("id") != gmail_message_id
                # Messages we sent live in the same thread — a follow-up of our
                # own is not the recipient replying. Counting them inflates the
                # reply rate and hides contacts who never answered.
                and "SENT" not in (m.get("labelIds") or [])
            ]
            verdicts = [(m, self._classify(m["id"])) for m in candidates]

            def _when(message):
                return datetime.fromtimestamp(
                    int(message.get("internalDate", 0)) / 1000)

            bounces = [m for m, v in verdicts if v == BOUNCE]
            replies = [m for m, v in verdicts if v == REPLY]
            result = dict(blank)
            if bounces:
                result["bounced"] = True
                result["bounced_at"] = _when(
                    max(bounces, key=lambda m: int(m.get("internalDate", 0))))
            if replies:
                # A real reply outranks a bounce in the same thread: people do
                # answer from a second address after the first one bounces.
                result["has_reply"] = True
                result["replied_at"] = _when(
                    max(replies, key=lambda m: int(m.get("internalDate", 0))))
            return result
        except Exception as e:
            print(f"[replies] check failed for {gmail_message_id}: {e}")
            # unknown — do NOT treat as 'no reply', and do NOT claim a bounce.
            return {"has_reply": None, "replied_at": None,
                    "bounced": False, "bounced_at": None}
