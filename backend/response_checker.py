"""Check Gmail threads for genuine replies to sent emails (ignoring
bounces and auto-replies, which the legacy checker wrongly counted)."""
import re
from datetime import datetime
from typing import Optional, Tuple

_AUTO_SENDER_RE = re.compile(
    r"mailer-daemon|postmaster|no-?reply|donotreply|auto-?responder", re.I)
_AUTO_SUBJECT_RE = re.compile(
    r"delivery status|delivery failure|undeliverable|failure notice|"
    r"out of office|automatic reply|auto-?reply|autoreply|away from",
    re.I)


class ResponseChecker:
    """Given an authenticated Gmail service, detect replies to sent messages."""

    def __init__(self, service, own_address: Optional[str] = None):
        self.service = service
        self.own_address = (own_address or "").strip().lower() or None

    def _is_real_reply(self, message_id: str) -> bool:
        """Fetch headers and reject bounces, auto-replies, and our own mail."""
        try:
            msg = self.service.users().messages().get(
                userId="me", id=message_id, format="metadata",
                metadataHeaders=["From", "Subject", "Auto-Submitted"],
            ).execute()
            headers = {h["name"].lower(): h.get("value", "")
                       for h in msg.get("payload", {}).get("headers", [])}
            sender = headers.get("from", "")
            if _AUTO_SENDER_RE.search(sender):
                return False
            # Belt and braces alongside the SENT-label filter: a message from
            # our own address is our follow-up, not the recipient answering.
            if self.own_address and self.own_address in sender.lower():
                return False
            if _AUTO_SUBJECT_RE.search(headers.get("subject", "")):
                return False
            auto_submitted = headers.get("auto-submitted", "").lower()
            if auto_submitted and auto_submitted != "no":
                return False
            return True
        except Exception:
            # If we can't inspect it, count it — better a false positive
            # than silently dropping a real reply.
            return True

    def check_response(self, gmail_message_id: str,
                       gmail_thread_id: Optional[str] = None
                       ) -> Tuple[Optional[bool], Optional[datetime]]:
        """Returns (has_real_reply, reply_datetime). has_real_reply is None
        when the check itself failed (network error, rate limit, ...) —
        callers must treat that as 'unknown', never as 'no reply'."""
        if not gmail_message_id:
            return False, None
        try:
            thread_id = gmail_thread_id
            if not thread_id:
                original = self.service.users().messages().get(
                    userId="me", id=gmail_message_id, format="minimal"
                ).execute()
                thread_id = original.get("threadId")
            if not thread_id:
                return False, None

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
            real = [m for m in candidates if self._is_real_reply(m["id"])]
            if not real:
                return False, None
            latest = max(real, key=lambda m: int(m.get("internalDate", 0)))
            when = datetime.fromtimestamp(int(latest.get("internalDate", 0)) / 1000)
            return True, when
        except Exception as e:
            print(f"[replies] check failed for {gmail_message_id}: {e}")
            return None, None  # unknown — do NOT treat as 'no reply'
