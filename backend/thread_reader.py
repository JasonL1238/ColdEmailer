"""Turn a Gmail thread into something readable in the app.

Until now the only thing this app knew about a reply was that one existed.
`response_checker` asks Gmail for headers alone (`format="metadata"`) and
stores no body, so "replied 3 days ago" was the whole story — to read what the
person actually said you left the app and opened Gmail, which is where the
next twenty minutes went.

Three rules shape everything here.

**A reply body is untrusted input**, in exactly the sense `web_scraper`'s
output is: it is written by someone outside this system who may want the app
to do something for them. It is therefore never persisted, never handed to an
LLM, and reduced to plain text before it leaves this module — no markup
reaches the browser, so nothing in a reply can style, script or restructure
the page it is displayed in.

**Nothing is hidden.** Quoted history is split off rather than deleted, so the
UI can collapse it behind a disclosure and the user can still read every word.
An oversized body is truncated with `truncated: True` set, never silently cut.

**One definition of "reply".** Classification comes from
`response_checker.classify_headers`, the same function the reply-rate uses.
A pane that labelled a message a reply while the dashboard reported the
contact as never having answered would leave the user with two truths and no
way to tell which one to act on.
"""
import base64
import binascii
import codecs
import html
import re
from datetime import datetime
from email.utils import parseaddr
from typing import Any, Dict, List, Optional

from response_checker import BOUNCE, OWN, REPLY, classify_headers

# One message's text. Generous enough for any real email, small enough that a
# mail-loop transcript or a base64 blob pasted into a body cannot push megabytes
# through the API into a browser tab.
MAX_BODY_CHARS = 40_000
# Quoted history gets its own, smaller budget. It has to: sharing one made a
# long enough quote eat the entire allowance and leave nothing of the reply.
MAX_QUOTED_CHARS = 20_000
# Whole-thread ceiling, for the same reason applied to a 200-message thread.
MAX_MESSAGES = 50
# MIME nesting depth. Gmail will not return anything close to this; the bound
# exists so a malformed payload degrades to a short message instead of a
# RecursionError escaping as a 500.
MAX_DEPTH = 30

_CHARSET_RE = re.compile(r"charset\s*=\s*\"?([\w.:+-]+)\"?", re.I)


def _charset_of(part: Dict[str, Any]) -> Optional[str]:
    """The charset this MIME part declares, if any.

    `format="full"` returns each part's own headers, and Gmail does not
    transcode bodies — an Outlook sender's cp1252 stays cp1252 on the wire.
    """
    for header in (part or {}).get("headers") or []:
        if str(header.get("name", "")).lower() == "content-type":
            match = _CHARSET_RE.search(header.get("value") or "")
            if match:
                return match.group(1)
    return None


def _decode(data: Optional[str], charset: Optional[str] = None) -> str:
    """Gmail's base64url payload → text.

    Padding is stripped by the API and has to be put back. The charset is the
    sender's, not ours: decoding everything as UTF-8 turned an Outlook reply's
    smart quotes into replacement characters, a German one into `Gr��e`, and a
    Japanese one into nothing readable at all — in the only place a reply body
    is ever shown. The declared charset is tried first, then UTF-8, and cp1252
    last with `errors="replace"`, so a sender who lies about their encoding
    still gets a readable-ish message rather than an empty pane.
    """
    if not data:
        return ""
    try:
        raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except (binascii.Error, ValueError, TypeError):
        return ""

    attempts: List[str] = []
    if charset:
        try:
            # A bogus or unknown charset name must not raise out of here.
            attempts.append(codecs.lookup(charset).name)
        except (LookupError, TypeError):
            pass
    for fallback in ("utf-8", "cp1252"):
        if fallback not in attempts:
            attempts.append(fallback)

    for name in attempts[:-1]:
        try:
            return raw.decode(name)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode(attempts[-1], errors="replace")


_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_SCRIPTISH_RE = re.compile(r"<(script|style|head|title)\b[^>]*>.*?</\1\s*>",
                           re.I | re.S)
# `</td>` and `</th>` break too: HTML mail is table-laid-out, and without them
# a three-cell signature row rendered as "Jane DoeDirector of EngineeringAcme".
_BREAK_RE = re.compile(r"<(br|/p|/div|/tr|/td|/th|/li|/h[1-6])\b[^>]*>", re.I)
# Quote-aware. `<[^>]+>` stops at the first `>`, so an attribute value
# containing one — `<img alt="Sales > Ops" src="x">` — ended the match early,
# destroying the word before it and leaking `Ops" src="x">` into the text.
_TAG_RE = re.compile(r"""</?[a-zA-Z!?][^>"']*(?:(?:"[^"]*"|'[^']*')[^>"']*)*>""")
_BLANK_RUN_RE = re.compile(r"\n{3,}")


def html_to_text(markup: str) -> str:
    """A best-effort plain-text rendering of an HTML mail part.

    Element *contents* are removed for script/style/head, not just their tags:
    stripping `<script>` alone leaves the JavaScript behind as body text, which
    is both unreadable and an instruction-shaped thing to put in front of a
    model later.
    """
    if not markup:
        return ""
    text = _SCRIPTISH_RE.sub(" ", markup)
    text = _COMMENT_RE.sub(" ", text)
    text = _BREAK_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("‌", "")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return _BLANK_RUN_RE.sub("\n\n", text).strip()


def _walk(part: Dict[str, Any], out: Dict[str, List[str]],
          attachments: List[str], depth: int = 0) -> None:
    """Collect text parts and attachment names from a MIME tree."""
    if not isinstance(part, dict) or depth > MAX_DEPTH:
        return
    mime = (part.get("mimeType") or "").lower()
    filename = part.get("filename") or ""
    body = part.get("body") or {}
    if filename:
        # An attachment, even when its mimeType is text/plain — a .txt file is
        # a file, not the message. Named, never fetched.
        attachments.append(filename)
    elif mime == "text/plain":
        out["plain"].append(_decode(body.get("data"), _charset_of(part)))
    elif mime == "text/html":
        out["html"].append(_decode(body.get("data"), _charset_of(part)))
    for child in part.get("parts") or []:
        _walk(child, out, attachments, depth + 1)


def extract_body(payload: Dict[str, Any]) -> Dict[str, Any]:
    """The readable text of one message, split from its quoted history.

    `text/plain` wins over `text/html` when a sender supplies both: it is what
    they wrote, rather than a rendering of it, and it needs no tag stripping to
    become safe.

    The split happens *before* any size bound, and each half is bounded
    separately. Doing it the other way round lost whole replies: an Outlook
    bottom-post is a quoted thread followed by the new sentence, so a single
    budget applied to the joined text spent all 40,000 characters on history
    and cut immediately before the only part worth reading.
    """
    found: Dict[str, List[str]] = {"plain": [], "html": []}
    attachments: List[str] = []
    _walk(payload or {}, found, attachments)

    text = "\n".join(p for p in found["plain"] if p.strip()).strip()
    if not text:
        text = html_to_text("\n".join(found["html"])).strip()
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _BLANK_RUN_RE.sub("\n\n", text)

    split = split_quote(text)
    body, quoted = split["text"], split["quoted"]
    truncated = False
    if len(body) > MAX_BODY_CHARS:
        truncated = True
        # A body that *opens* with quoted history is one the split declined to
        # cut, which means the sender's own words are at the end of it. Keeping
        # the head there would show the user 40,000 characters of their own
        # original email and none of the answer.
        body = (body[-MAX_BODY_CHARS:] if split["leading_quote"]
                else body[:MAX_BODY_CHARS])
    if len(quoted) > MAX_QUOTED_CHARS:
        truncated = True
        quoted = quoted[:MAX_QUOTED_CHARS]
    return {"text": body, "quoted": quoted, "truncated": truncated,
            "attachments": attachments}


# "On Mon, 3 Jun 2024 at 09:12, Jane <jane@x.com> wrote:" and its cousins, plus
# the Outlook and Apple Mail separators. Anchored to the start of a line so a
# sentence containing the words cannot trigger it.
_QUOTE_LEAD_RE = re.compile(
    r"^\s*(?:"
    r"On .{0,200}?\bwrote:\s*$"
    r"|-{2,}\s*Original Message\s*-{2,}\s*$"
    r"|_{5,}\s*$"
    r"|From:\s*.+$"
    r"|Begin forwarded message:\s*$"
    r"|Le .{0,200}? a écrit\s*:\s*$"
    r")", re.I)
# The same attribution after Gmail's own 78-column fold, which splits it
# whenever the display name plus address is long enough — and this app's mail
# comes back through Gmail, so that is the common case rather than an edge one.
_FOLDED_LEAD_RE = re.compile(r"^\s*On\b.{0,200}$", re.I | re.S)
_WROTE_TAIL_RE = re.compile(r"^\s*wrote:\s*$", re.I)


def _is_quote_start(lines: List[str], i: int) -> bool:
    line = lines[i]
    if _QUOTE_LEAD_RE.match(line):
        return True
    if line.startswith(">") and line.strip() != ">":
        return True
    # "On <date> <someone>\nwrote:" — only when the *next* line closes it, so
    # an ordinary sentence beginning "On Tuesday I…" is not a quote marker.
    return bool(i + 1 < len(lines) and _FOLDED_LEAD_RE.match(line)
                and _WROTE_TAIL_RE.match(lines[i + 1]))


def split_quote(text: str) -> Dict[str, Any]:
    """Separate what this message says from the history it quotes back.

    The reply to a cold email is usually two lines above a verbatim copy of the
    email itself, and showing the copy first buries the answer. Deliberately
    conservative in one direction: a cut is only made when there is real text
    above it, because a message that is *entirely* a quote line followed by
    ">"-prefixed history is more likely a top-post the heuristic misread than a
    message with nothing in it. Nothing is discarded either way — the quoted
    half comes back for the UI to collapse.

    `leading_quote` reports that second case, so a caller that has to shorten
    the text knows the sender's own words are at the end of it rather than the
    start.
    """
    lines = (text or "").split("\n")
    cut = None
    for i in range(len(lines)):
        if _is_quote_start(lines, i):
            cut = i
            break
    if cut is None:
        return {"text": text or "", "quoted": "", "leading_quote": False}
    above = "\n".join(lines[:cut]).strip()
    if not above:
        return {"text": text or "", "quoted": "", "leading_quote": True}
    return {"text": above, "quoted": "\n".join(lines[cut:]).strip(),
            "leading_quote": False}


def parse_from(value: str) -> Dict[str, str]:
    """`"Jane Doe" <jane@x.com>` → name and address, either of which may be ''.

    `email.utils.parseaddr` rather than a regex, because the forms that broke
    the regex are the ones this app sees most: a bounce daemon writes
    `MAILER-DAEMON@googlemail.com (Mail Delivery System)`, and the whole header
    came back as the "address" — which the pane then renders as the sender's
    name, on precisely the messages the user most needs to identify.
    """
    raw = (value or "").strip()
    name, address = parseaddr(raw)
    if "@" in address:
        return {"name": name.strip().strip('"').strip(),
                "email": address.strip().lower()}
    if "@" in raw:
        return {"name": "", "email": raw.strip("<> ").lower()}
    return {"name": raw.strip('"'), "email": ""}


def _headers_of(message: Dict[str, Any]) -> Dict[str, str]:
    return {str(h.get("name", "")).lower(): (h.get("value") or "")
            for h in (message.get("payload") or {}).get("headers", [])}


def _epoch_ms(message: Dict[str, Any]) -> int:
    """internalDate as a number, or 0.

    Used for both ordering and display, so a message Gmail sent without a
    usable timestamp sorts to the top rather than crashing the sort — the
    alternative was one malformed message emptying the whole pane.
    """
    try:
        return int(message.get("internalDate"))
    except (TypeError, ValueError):
        return 0


def _sent_at(message: Dict[str, Any]) -> Optional[str]:
    """Gmail's internalDate (ms since epoch) as a local ISO string.

    Returns None rather than the epoch when the field is missing or unparseable
    — "1970-01-01" beside a message from last week is a worse answer than no
    date at all.
    """
    stamp = _epoch_ms(message)
    if stamp <= 0:
        return None
    try:
        return datetime.fromtimestamp(stamp / 1000).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


def parse_thread(thread: Dict[str, Any], own_address: Optional[str] = None,
                 sent_message_id: Optional[str] = None) -> Dict[str, Any]:
    """A Gmail thread → the messages to show, oldest first.

    `sent_message_id` is the Gmail id this app recorded when it sent, so the
    message the user already has in the app is marked rather than repeated as a
    discovery.
    """
    messages = (thread or {}).get("messages") or []
    # Oldest first, so the pane reads as a conversation. Gmail returns them in
    # order today; sorting makes that a property of this code rather than of
    # someone else's.
    messages = sorted(messages, key=_epoch_ms)
    dropped = max(0, len(messages) - MAX_MESSAGES)
    if dropped:
        # Keep the *newest*: the recent end of a long thread is what the user
        # is trying to read.
        messages = messages[-MAX_MESSAGES:]

    out: List[Dict[str, Any]] = []
    for message in messages:
        headers = _headers_of(message)
        labels = message.get("labelIds") or []
        kind = classify_headers(headers, own_address)
        # A label beats a header guess: Gmail knows what it sent. This also
        # catches sending from an alias, where From never matches the profile
        # address and the app's own mail would read as the contact replying.
        outgoing = "SENT" in labels or kind == OWN
        if outgoing:
            kind = OWN
        body = extract_body(message.get("payload") or {})
        sender = parse_from(headers.get("from", ""))
        out.append({
            "id": message.get("id"),
            "kind": kind,
            "outgoing": outgoing,
            "is_tracked_send": bool(sent_message_id
                                    and message.get("id") == sent_message_id),
            "from_name": sender["name"],
            "from_email": sender["email"],
            "subject": headers.get("subject", ""),
            "to": headers.get("to", ""),
            "sent_at": _sent_at(message),
            "snippet": (message.get("snippet") or "")[:300],
            "text": body["text"],
            "quoted": body["quoted"],
            "truncated": body["truncated"],
            "attachments": body["attachments"],
        })

    return {
        "messages": out,
        "older_omitted": dropped,
        # What the app would count as an answer, computed here so a caller
        # cannot arrive at a different total than the reply-rate does.
        "reply_count": sum(1 for m in out if m["kind"] == REPLY),
        "bounce_count": sum(1 for m in out if m["kind"] == BOUNCE),
    }
