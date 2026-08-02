"""Reading a reply is reading something a stranger wrote.

Every test here is one of three things:

  * the message survives the trip — MIME nesting, base64url padding, HTML-only
    senders, charsets the app did not choose;
  * nothing a sender writes can act on the app — no markup reaches the
    browser, no body reaches the database, no size reaches memory unbounded;
  * the pane and the reply-rate agree — a message labelled "Reply" here is a
    message the dashboard counts, because both ask the same function.
"""
import base64
import os
import tempfile
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import main
import thread_reader
from db import Database, now_iso
from response_checker import AUTO, BOUNCE, OWN, REPLY


def b64(text: str) -> str:
    """Gmail's transport encoding, padding stripped exactly as the API does."""
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def part(mime, text=None, filename="", parts=None, raw=None, charset=None):
    node = {"mimeType": mime, "filename": filename, "body": {}}
    if text is not None:
        node["body"] = {"data": b64(text)}
    if raw is not None:
        node["body"] = {"data": raw}
    if parts is not None:
        node["parts"] = parts
    if charset:
        # format="full" returns each part's own headers, which is where the
        # sender's charset is declared.
        node["headers"] = [{"name": "Content-Type",
                            "value": f'{mime}; charset="{charset}"'}]
    return node


def encoded_part(mime, text, charset):
    """A part carrying `text` in `charset`, exactly as Gmail would return it."""
    data = base64.urlsafe_b64encode(text.encode(charset)).decode().rstrip("=")
    return part(mime, raw=data, charset=charset)


def msg(mid, headers=None, payload=None, when="1700000000000", labels=None,
        snippet=""):
    return {
        "id": mid, "internalDate": when, "labelIds": labels or [],
        "snippet": snippet,
        "payload": {
            "headers": [{"name": k, "value": v} for k, v in (headers or {}).items()],
            **(payload or {}),
        },
    }


# ---------------------------------------------------------------- decoding

class TestBodyExtraction:
    def test_reads_a_plain_body(self):
        out = thread_reader.extract_body(part("text/plain", "Sure, Thursday works."))
        assert out["text"] == "Sure, Thursday works."
        assert out["truncated"] is False

    def test_handles_missing_base64_padding(self):
        # Gmail strips '='. Feeding that straight to b64decode raises, and the
        # message came back as an empty pane with no error anywhere.
        for body in ("a", "ab", "abc", "abcd", "Hello there, thanks!"):
            encoded = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
            assert thread_reader._decode(encoded) == body

    def test_honours_the_charset_the_sender_declared(self):
        """Gmail does not transcode bodies, so an Outlook reply arrives as
        cp1252 and a German one as latin-1. Decoding everything as UTF-8 put
        replacement characters through the middle of the reply — in the one
        place a reply body is ever shown."""
        cases = [
            ("windows-1252", "Hi Jason — we don’t have openings, but let’s talk."),
            ("iso-8859-1", "Grüße aus München"),
            ("shift_jis", "ご連絡ありがとうございます"),
            ("iso-2022-jp", "ご連絡ありがとうございます"),
            ("utf-8", "Grüße — 👋"),
        ]
        for charset, text in cases:
            out = thread_reader.extract_body(
                encoded_part("text/plain", text, charset))
            assert out["text"] == text, charset
            assert "�" not in out["text"], charset

    def test_an_undeclared_body_still_decodes_as_utf8(self):
        out = thread_reader.extract_body(part("text/plain", "Grüße — 👋"))
        assert out["text"] == "Grüße — 👋"

    def test_a_bogus_charset_name_falls_through_instead_of_raising(self):
        node = part("text/plain", "plain words")
        node["headers"] = [{"name": "Content-Type",
                            "value": 'text/plain; charset="not-a-real-charset"'}]
        assert thread_reader.extract_body(node)["text"] == "plain words"

    def test_a_sender_who_lies_about_the_charset_still_reads(self):
        """Declared utf-8, actually cp1252. Never an empty pane."""
        data = base64.urlsafe_b64encode(
            "we don’t".encode("cp1252")).decode().rstrip("=")
        node = part("text/plain", raw=data, charset="utf-8")
        out = thread_reader.extract_body(node)
        assert "we don" in out["text"]

    def test_prefers_plain_over_html_when_both_are_offered(self):
        """A multipart/alternative sender supplies both; the plain half is what
        they wrote rather than a rendering of it, and needs no tag stripping."""
        out = thread_reader.extract_body(part("multipart/alternative", parts=[
            part("text/plain", "the real words"),
            part("text/html", "<p>the <b>marked up</b> words</p>"),
        ]))
        assert out["text"] == "the real words"

    def test_falls_back_to_html_when_that_is_all_there_is(self):
        out = thread_reader.extract_body(part("multipart/alternative", parts=[
            part("text/html", "<p>Hi Jason,</p><p>Let's talk.</p>"),
        ]))
        assert "Hi Jason," in out["text"]
        assert "Let's talk." in out["text"]
        assert "<p>" not in out["text"]

    def test_finds_text_nested_two_levels_down(self):
        """multipart/mixed → multipart/alternative → text/plain is the ordinary
        shape of a reply with an attachment. A one-level walk returned ''."""
        out = thread_reader.extract_body(part("multipart/mixed", parts=[
            part("multipart/alternative", parts=[
                part("text/plain", "deep enough"),
            ]),
            part("application/pdf", filename="deck.pdf"),
        ]))
        assert out["text"] == "deep enough"
        assert out["attachments"] == ["deck.pdf"]

    def test_a_named_text_file_is_an_attachment_not_the_message(self):
        """A .txt attachment has mimeType text/plain. Treating it as the body
        replaced the reply with the contents of a file."""
        out = thread_reader.extract_body(part("multipart/mixed", parts=[
            part("text/plain", "See attached."),
            part("text/plain", "LOG LINE 1\nLOG LINE 2", filename="notes.txt"),
        ]))
        assert out["text"] == "See attached."
        assert out["attachments"] == ["notes.txt"]

    def test_truncates_a_huge_body_and_says_so(self):
        out = thread_reader.extract_body(
            part("text/plain", "x" * (thread_reader.MAX_BODY_CHARS + 5000)))
        assert len(out["text"]) == thread_reader.MAX_BODY_CHARS
        assert out["truncated"] is True

    def test_a_long_quote_does_not_eat_the_reply_above_it(self):
        """The split has to happen before the size bound. Sharing one budget
        across both halves meant a long enough quoted thread consumed the whole
        allowance and the reply was cut off before it started."""
        reply = "Yes — Thursday at 2pm works, dial-in 555-0134."
        body = (reply + "\n\nOn Mon, Jason wrote:\n"
                + "> the original pitch, again and again\n" * 4000)
        out = thread_reader.extract_body(part("text/plain", body))
        assert out["text"] == reply
        assert out["truncated"] is True
        assert len(out["quoted"]) == thread_reader.MAX_QUOTED_CHARS

    def test_keeps_the_tail_of_a_bottom_posted_reply(self):
        """Outlook bottom-posts: quoted history first, the new sentence last.
        The split declines to cut (nothing is above the marker), so bounding
        from the front showed the user 40,000 characters of their own original
        email and none of the answer."""
        answer = "Jason - yes, we'd love to talk. Thursday 2pm works."
        body = ("-----Original Message-----\nFrom: Jason\n"
                + "> Following up on the intern posting.\n" * 3000
                + "\n" + answer + "\n")
        out = thread_reader.extract_body(part("text/plain", body))
        assert out["truncated"] is True
        assert answer in out["text"]

    def test_a_deep_mime_nest_degrades_instead_of_blowing_the_stack(self):
        """Gmail will not send this. The bound is here so a payload this app
        did not write cannot turn one unreadable thread into a 500."""
        node = part("text/plain", "buried")
        for _ in range(3000):
            node = part("multipart/mixed", parts=[node])
        out = thread_reader.extract_body(node)   # no RecursionError
        assert out["text"] == ""

    def test_undecodable_data_yields_nothing_rather_than_raising(self):
        assert thread_reader._decode("!!!not base64!!!") == ""
        assert thread_reader._decode(None) == ""
        out = thread_reader.extract_body(part("text/plain", raw="@@@@@"))
        assert out["text"] == ""


class TestHtmlToText:
    def test_strips_tags_and_unescapes_entities(self):
        assert thread_reader.html_to_text(
            "<p>Tom &amp; Jerry &lt;3</p>") == "Tom & Jerry <3"

    def test_removes_script_contents_not_just_the_tags(self):
        """Stripping <script> alone leaves the JavaScript behind as body text:
        unreadable, and an instruction-shaped thing to keep around."""
        out = thread_reader.html_to_text(
            "<p>Hi</p><script>alert('xss'); window.location='http://evil'</script>")
        assert out.strip() == "Hi"
        assert "alert" not in out
        assert "evil" not in out

    def test_removes_style_and_head_contents_too(self):
        out = thread_reader.html_to_text(
            "<head><title>Ignore</title></head><style>.a{color:red}</style><p>Body</p>")
        assert "color:red" not in out
        assert "Ignore" not in out
        assert "Body" in out

    def test_block_ends_become_line_breaks(self):
        out = thread_reader.html_to_text("<div>one</div><div>two</div>")
        assert out == "one\ntwo"

    def test_collapses_the_blank_run_a_stripped_table_leaves(self):
        out = thread_reader.html_to_text(
            "<p>a</p>" + "<div></div>" * 12 + "<p>b</p>")
        assert "\n\n\n" not in out

    def test_table_cells_do_not_run_together(self):
        """HTML mail is table-laid-out, and a signature block is one row of
        cells. Without a break on </td> it rendered as one unreadable word."""
        out = thread_reader.html_to_text(
            "<table><tbody><tr><td>Jane Doe</td><td>Director of Engineering"
            "</td><td>Acme Corp</td></tr></tbody></table>")
        assert "Jane DoeDirector" not in out
        assert "Jane Doe" in out and "Director of Engineering" in out

    def test_a_gt_inside_an_attribute_does_not_end_the_tag(self):
        """`<[^>]+>` stopped at the first '>', so the attribute's own '>' ended
        the match early: the word before it was eaten and raw markup was shown
        to the user as if the sender had written it."""
        out = thread_reader.html_to_text(
            '<p><img alt="Sales > Ops" src="x">Real body text</p>')
        assert out == "Real body text"

        link = thread_reader.html_to_text('<a title="a > b" href="#">link</a>')
        assert link == "link"

    def test_removes_a_comment_containing_a_bare_gt(self):
        out = thread_reader.html_to_text("<!-- a > b --><p>Body</p>")
        assert out.strip() == "Body"


# ------------------------------------------------------------- quoted text

class TestSplitQuote:
    def test_splits_at_the_attribution_line(self):
        out = thread_reader.split_quote(
            "Thursday works.\n\nOn Mon, 3 Jun 2024 at 09:12, Jason <j@x.com> wrote:\n"
            "> Hi Dana, I admired your work on…")
        assert out["text"] == "Thursday works."
        assert "I admired your work" in out["quoted"]

    def test_splits_at_a_bare_quote_marker(self):
        out = thread_reader.split_quote("Yes please.\n> original text here")
        assert out["text"] == "Yes please."
        assert out["quoted"] == "> original text here"

    def test_keeps_everything_when_the_message_is_only_a_quote(self):
        """A body that starts with the marker is more likely a heuristic miss
        than an empty message. Show it rather than show nothing."""
        text = "On Mon, Jason wrote:\n> the whole thing"
        assert thread_reader.split_quote(text)["text"] == text
        assert thread_reader.split_quote(text)["quoted"] == ""

    def test_a_sentence_mentioning_wrote_is_not_a_quote_marker(self):
        """Anchored to the start of a line: 'as I wrote:' mid-paragraph used to
        hide the rest of a genuine reply behind a disclosure."""
        text = "I liked what you wrote: the section on latency especially.\nCall Friday?"
        assert thread_reader.split_quote(text)["quoted"] == ""

    def test_nothing_is_ever_discarded(self):
        text = "Reply.\n\nOn Mon, X wrote:\n> quoted\n> more"
        out = thread_reader.split_quote(text)
        rejoined = (out["text"] + out["quoted"]).replace("\n", "").replace(" ", "")
        assert "quoted" in rejoined and "more" in rejoined and "Reply." in rejoined

    def test_outlook_and_apple_separators(self):
        for sep in ("-----Original Message-----", "Begin forwarded message:",
                    "________________"):
            out = thread_reader.split_quote(f"Short answer.\n{sep}\nold stuff")
            assert out["text"] == "Short answer.", sep
            assert "old stuff" in out["quoted"], sep

    def test_splits_an_attribution_gmail_wrapped_across_two_lines(self):
        """Gmail hard-wraps text/plain near 78 columns, which folds the
        attribution whenever the display name plus address is long enough —
        and this app's replies come back through Gmail, so that is the common
        case. The dangling "wrote:" was left sitting in the reply."""
        out = thread_reader.split_quote(
            "Thanks Jason, this is helpful.\n\n"
            "On Mon, Jun 3, 2024 at 9:12 AM Jason Li <jason.ye.li.7@gmail.com>\n"
            "wrote:\n> Hi Jane,\n> I'm a CS student...")
        assert out["text"] == "Thanks Jason, this is helpful."
        assert "wrote:" not in out["text"]
        assert "I'm a CS student" in out["quoted"]

    def test_a_line_starting_with_on_is_not_a_marker_without_the_wrote(self):
        text = "On Tuesday I'll be in the office.\nCall then?"
        assert thread_reader.split_quote(text)["quoted"] == ""

    def test_reports_a_body_that_opens_with_quoted_history(self):
        """The caller needs to know the sender's own words are at the end."""
        top = thread_reader.split_quote("Yes.\n> quoted")
        assert top["leading_quote"] is False
        bottom = thread_reader.split_quote("> quoted\nYes, at the bottom.")
        assert bottom["leading_quote"] is True


class TestParseFrom:
    def test_splits_a_display_name_from_the_address(self):
        assert thread_reader.parse_from('"Dana Lee" <dana@x.com>') == {
            "name": "Dana Lee", "email": "dana@x.com"}

    def test_handles_a_bare_address(self):
        assert thread_reader.parse_from("dana@x.com") == {
            "name": "", "email": "dana@x.com"}

    def test_lowercases_the_address_but_not_the_name(self):
        out = thread_reader.parse_from("Dana Lee <Dana@Example.COM>")
        assert out["email"] == "dana@example.com"
        assert out["name"] == "Dana Lee"

    def test_empty_header_is_empty_not_a_crash(self):
        assert thread_reader.parse_from("") == {"name": "", "email": ""}
        assert thread_reader.parse_from(None) == {"name": "", "email": ""}

    def test_handles_the_comment_form_bounce_daemons_use(self):
        """The pane falls back to from_email as the bold sender label, so the
        whole mangled header was displayed where an address belongs — on
        exactly the bounce and auto-reply messages the user needs to identify."""
        assert thread_reader.parse_from(
            "MAILER-DAEMON@googlemail.com (Mail Delivery System)") == {
                "name": "Mail Delivery System",
                "email": "mailer-daemon@googlemail.com"}

    def test_handles_a_trailing_comment_after_the_angle_brackets(self):
        assert thread_reader.parse_from("Jane Doe <jane@x.com> (via Workday)") == {
            "name": "Jane Doe", "email": "jane@x.com"}


# ------------------------------------------------------------ whole threads

def _thread(messages):
    return {"messages": messages}


class TestParseThread:
    def test_orders_oldest_first_whatever_gmail_returned(self):
        out = thread_reader.parse_thread(_thread([
            msg("b", when="1700000200000"),
            msg("a", when="1700000100000"),
        ]))
        assert [m["id"] for m in out["messages"]] == ["a", "b"]

    def test_labels_our_own_message_as_ours_via_the_sent_label(self):
        """Sending from an alias means From never matches the profile address.
        Without the label check the app's own mail read as the contact
        replying — and the pane would have shown a 'Reply' that never was."""
        out = thread_reader.parse_thread(
            _thread([msg("m1", {"From": "Alias <alias@me.com>"}, labels=["SENT"])]),
            own_address="jason@me.com")
        assert out["messages"][0]["kind"] == OWN
        assert out["messages"][0]["outgoing"] is True
        assert out["reply_count"] == 0

    def test_uses_the_same_classification_as_the_reply_rate(self):
        """Not a restatement of classify_headers' own tests: the point is that
        this module calls it rather than carrying a second copy of the rules."""
        out = thread_reader.parse_thread(_thread([
            msg("r", {"From": "Dana <dana@x.com>", "Subject": "Re: hello"}),
            msg("b", {"From": "Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
                      "Subject": "Delivery Status Notification (Failure)"}),
            msg("a", {"From": "Dana <dana@x.com>", "Subject": "Out of office"}),
        ]))
        kinds = {m["id"]: m["kind"] for m in out["messages"]}
        assert kinds == {"r": REPLY, "b": BOUNCE, "a": AUTO}
        assert out["reply_count"] == 1
        assert out["bounce_count"] == 1

    def test_marks_the_message_this_app_sent(self):
        out = thread_reader.parse_thread(
            _thread([msg("sent1", labels=["SENT"]), msg("other")]),
            sent_message_id="sent1")
        tracked = [m["id"] for m in out["messages"] if m["is_tracked_send"]]
        assert tracked == ["sent1"]

    def test_caps_a_runaway_thread_and_keeps_the_recent_end(self):
        many = [msg(f"m{i}", when=str(1700000000000 + i * 1000)) for i in range(80)]
        out = thread_reader.parse_thread(_thread(many))
        assert len(out["messages"]) == thread_reader.MAX_MESSAGES
        assert out["older_omitted"] == 80 - thread_reader.MAX_MESSAGES
        # newest kept, oldest dropped — the recent end is what is being read
        assert out["messages"][-1]["id"] == "m79"

    def test_a_missing_timestamp_is_no_date_not_1970(self):
        out = thread_reader.parse_thread(_thread([
            {"id": "x", "payload": {"headers": []}},
            {"id": "y", "internalDate": "not a number", "payload": {"headers": []}},
        ]))
        assert all(m["sent_at"] is None for m in out["messages"])

    def test_reads_a_real_date(self):
        out = thread_reader.parse_thread(_thread([msg("m", when="1700000000000")]))
        assert out["messages"][0]["sent_at"].startswith("2023-11-1")

    def test_an_empty_thread_is_empty_not_an_error(self):
        assert thread_reader.parse_thread({})["messages"] == []
        assert thread_reader.parse_thread(None)["messages"] == []

    def test_carries_the_body_and_the_quote_split_through(self):
        out = thread_reader.parse_thread(_thread([
            msg("m", {"From": "Dana <dana@x.com>"},
                payload=part("text/plain",
                             "Yes — Thursday.\n\nOn Mon, Jason wrote:\n> hi")),
        ]))
        message = out["messages"][0]
        assert message["text"] == "Yes — Thursday."
        assert "> hi" in message["quoted"]
        assert message["from_name"] == "Dana"
        assert message["from_email"] == "dana@x.com"


# ------------------------------------------------------- the HTTP endpoint

@pytest.fixture
def client(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        database = Database(os.path.join(tmp, "t.db"))
        monkeypatch.setattr(main, "db", database)
        yield TestClient(main.app), database


def _delivered_email(database, **over):
    company = database.create_company(name="ZZTEST Co", url="https://zz.example")
    contact = database.create_contact(company_id=company["id"], name="Dana",
                                      email="dana@zz.example")
    data = dict(company_id=company["id"], contact_id=contact["id"],
                subject="s", body="b", status="sent", sent_at=now_iso(),
                gmail_message_id="gm1", gmail_thread_id="t1")
    data.update(over)
    return database.create_email(**data)


def _service(thread, monkeypatch):
    service = MagicMock()
    service.users().threads().get.return_value.execute.return_value = thread
    monkeypatch.setattr(main.email_sender, "authenticate", lambda: None)
    monkeypatch.setattr(main.email_sender, "service", service, raising=False)
    return service


class TestThreadEndpoint:
    def test_returns_the_parsed_conversation(self, client, monkeypatch):
        api, database = client
        email = _delivered_email(database)
        _service(_thread([
            msg("gm1", {"From": "Me <me@x.com>"}, labels=["SENT"],
                payload=part("text/plain", "my pitch")),
            msg("r1", {"From": "Dana <dana@zz.example>"},
                payload=part("text/plain", "Happy to chat.")),
        ]), monkeypatch)

        response = api.get(f"/api/emails/{email['id']}/thread")
        assert response.status_code == 200
        body = response.json()
        assert [m["text"] for m in body["messages"]] == ["my pitch", "Happy to chat."]
        assert body["reply_count"] == 1
        assert body["thread_id"] == "t1"

    def test_refuses_an_email_that_was_never_sent(self, client):
        api, database = client
        email = _delivered_email(database, status="draft", sent_at=None,
                                 gmail_message_id=None, gmail_thread_id=None)
        response = api.get(f"/api/emails/{email['id']}/thread")
        assert response.status_code == 409
        assert "not been sent" in response.json()["detail"]

    def test_unknown_email_is_a_404(self, client):
        api, _ = client
        assert api.get("/api/emails/nope/thread").status_code == 404

    def test_a_gmail_failure_is_reported_not_swallowed(self, client, monkeypatch):
        api, database = client
        email = _delivered_email(database)
        service = _service({}, monkeypatch)
        service.users().threads().get.return_value.execute.side_effect = \
            RuntimeError("rate limited")
        response = api.get(f"/api/emails/{email['id']}/thread")
        assert response.status_code == 502
        assert "rate limited" in response.json()["detail"]

    def test_reading_a_thread_writes_nothing(self, client, monkeypatch):
        """The whole point of the separation: a read-only pane cannot mark a
        contact replied. Only check-replies decides that, on its own rules."""
        api, database = client
        email = _delivered_email(database)
        _service(_thread([
            msg("r1", {"From": "Dana <dana@zz.example>", "Subject": "Re: hi"},
                payload=part("text/plain", "yes!")),
        ]), monkeypatch)

        api.get(f"/api/emails/{email['id']}/thread")
        after = database.get_email(email["id"])
        assert not after["has_response"]
        assert after.get("response_verified_at") is None
        assert not after.get("bounced_at")
        contact = database.get_contact(email["contact_id"])
        assert contact["status"] != "replied"

    def test_says_when_it_can_see_a_reply_the_record_does_not(self, client, monkeypatch):
        api, database = client
        email = _delivered_email(database)
        _service(_thread([
            msg("r1", {"From": "Dana <dana@zz.example>"},
                payload=part("text/plain", "yes!")),
        ]), monkeypatch)
        body = api.get(f"/api/emails/{email['id']}/thread").json()
        assert body["unrecorded_reply"] is True

    def test_no_disagreement_once_the_reply_is_recorded(self, client, monkeypatch):
        api, database = client
        email = _delivered_email(database, has_response=True,
                                 response_at=now_iso())
        _service(_thread([
            msg("r1", {"From": "Dana <dana@zz.example>"},
                payload=part("text/plain", "yes!")),
        ]), monkeypatch)
        body = api.get(f"/api/emails/{email['id']}/thread").json()
        assert body["unrecorded_reply"] is False

    def test_says_when_it_can_see_an_unrecorded_bounce(self, client, monkeypatch):
        api, database = client
        email = _delivered_email(database)
        _service(_thread([
            msg("b1", {"From": "mailer-daemon@googlemail.com",
                       "Subject": "Delivery Status Notification (Failure)"},
                payload=part("text/plain", "550 no such user")),
        ]), monkeypatch)
        body = api.get(f"/api/emails/{email['id']}/thread").json()
        assert body["unrecorded_bounce"] is True
        assert body["bounce_count"] == 1

    def test_asks_gmail_for_the_full_message_not_metadata(self, client, monkeypatch):
        """format='metadata' returns headers and no body — the pane came up
        empty for every message and nothing said why."""
        api, database = client
        email = _delivered_email(database)
        service = _service(_thread([msg("m")]), monkeypatch)
        api.get(f"/api/emails/{email['id']}/thread")
        _, kwargs = service.users().threads().get.call_args
        assert kwargs.get("format") == "full"
        assert kwargs.get("id") == "t1"

    def test_finds_the_thread_id_for_a_legacy_row_that_has_none(self, client, monkeypatch):
        api, database = client
        email = _delivered_email(database, gmail_thread_id=None)
        service = _service(_thread([msg("m")]), monkeypatch)
        service.users().messages().get.return_value.execute.return_value = \
            {"threadId": "t1"}
        response = api.get(f"/api/emails/{email['id']}/thread")
        assert response.status_code == 200
        assert response.json()["thread_id"] == "t1"

    def test_a_dead_gmail_login_is_a_401(self, client, monkeypatch):
        api, database = client
        email = _delivered_email(database)
        def _boom():
            raise RuntimeError("token expired")
        monkeypatch.setattr(main.email_sender, "authenticate", _boom)
        response = api.get(f"/api/emails/{email['id']}/thread")
        assert response.status_code == 401
