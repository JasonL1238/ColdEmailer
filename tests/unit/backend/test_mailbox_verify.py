"""Mailbox verification: what it may claim, and what it may never claim.

No test touches the network — the SMTP peer is scripted and the HTTPS provider
getter is injected.
"""
import pytest

import mailbox_verify as mv
from mailbox_verify import MailboxVerdict


class FakeTransport:
    """A scripted SMTP peer that refuses to be told to send mail."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.commands = []
        self.tls = False

    def connect(self, host, port, timeout):  # pragma: no cover - unused
        pass

    def read_reply(self, timeout):
        return self.replies.pop(0) if self.replies else (421, "closed")

    def send_command(self, verb, arg=""):
        assert verb in mv.ALLOWED_VERBS, f"forbidden SMTP verb: {verb}"
        self.commands.append(verb)

    def starttls(self):
        self.tls = True

    def close(self):
        pass


def connector_for(transport):
    return lambda _domain: transport


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setenv("MAILBOX_VERIFY", "1")
    monkeypatch.setenv("SMTP_PROBE_HELO", "mail.example.org")
    monkeypatch.setenv("HUNTER_API_KEY", "")
    mv.clear_mailbox_cache()
    yield
    mv.clear_mailbox_cache()


def _script(canary_code, target_code=None, *, greeting=220, ehlo=250):
    replies = [(greeting, "hello"), (ehlo, "SIZE"), (250, "sender ok"),
               (canary_code, "canary")]
    if target_code is not None:
        replies += [(250, "rset"), (target_code, "target")]
    return replies


class TestVerdicts:
    def test_real_mailbox_on_a_discriminating_server(self):
        t = FakeTransport(_script(550, 250))
        got = mv.verify_mailbox("jane.doe@acme.com", backend="smtp",
                                connector=connector_for(t))
        assert got.verdict is MailboxVerdict.DELIVERABLE
        assert got.accept_all is False

    def test_rejected_mailbox_is_undeliverable(self):
        t = FakeTransport(_script(550, 550))
        got = mv.verify_mailbox("nope@acme.com", backend="smtp",
                                connector=connector_for(t))
        assert got.verdict is MailboxVerdict.UNDELIVERABLE

    def test_catch_all_domain_never_returns_deliverable(self):
        """If a random address is accepted, no answer carries information."""
        t = FakeTransport(_script(250))
        got = mv.verify_mailbox("jane.doe@acme.com", backend="smtp",
                                connector=connector_for(t))
        assert got.verdict is MailboxVerdict.ACCEPT_ALL
        # and we do not waste a question we already know is meaningless
        assert t.commands.count("RCPT") == 1

    def test_a_550_on_a_catch_all_domain_is_still_not_undeliverable(self):
        t = FakeTransport(_script(250, 550))
        got = mv.verify_mailbox("jane.doe@acme.com", backend="smtp",
                                connector=connector_for(t))
        assert got.verdict is MailboxVerdict.ACCEPT_ALL

    def test_greylisting_is_unknown_not_undeliverable(self):
        t = FakeTransport(_script(550, 450))
        got = mv.verify_mailbox("jane.doe@acme.com", backend="smtp",
                                connector=connector_for(t))
        assert got.verdict is MailboxVerdict.UNKNOWN
        assert got.verdict is not MailboxVerdict.UNDELIVERABLE
        assert got.reason == "greylisted"

    @pytest.mark.parametrize("script,reason", [
        ([(421, "go away")], "bad_greeting"),
        ([(220, "hi"), (500, "no ehlo")], "ehlo_refused"),
    ])
    def test_unknown_never_collapses_to_undeliverable(self, script, reason):
        t = FakeTransport(script)
        got = mv.verify_mailbox("jane.doe@acme.com", backend="smtp",
                                connector=connector_for(t))
        assert got.verdict is MailboxVerdict.UNKNOWN
        assert got.reason == reason

    def test_blocked_port_is_unknown(self):
        got = mv.verify_mailbox("jane.doe@acme.com", backend="smtp",
                                connector=lambda _d: None)
        assert got.verdict is MailboxVerdict.UNKNOWN
        assert got.reason == "port_25_blocked"


class TestNeverSendsMail:
    def test_the_verb_whitelist_excludes_data(self):
        assert "DATA" not in mv.ALLOWED_VERBS
        assert "BDAT" not in mv.ALLOWED_VERBS

    def test_the_conversation_is_exactly_the_probe(self):
        t = FakeTransport(_script(550, 250))
        mv.verify_mailbox("jane.doe@acme.com", backend="smtp",
                          connector=connector_for(t))
        assert t.commands == ["EHLO", "MAIL", "RCPT", "RSET", "RCPT", "QUIT"]

    def test_a_transport_asked_to_send_data_would_raise(self):
        t = FakeTransport(_script(550, 250))
        with pytest.raises(AssertionError):
            t.send_command("DATA")


class TestKillSwitch:
    def test_disabled_makes_it_a_noop_without_connecting(self, monkeypatch):
        monkeypatch.setenv("MAILBOX_VERIFY", "0")

        def boom(_domain):
            raise AssertionError("must not connect while disabled")
        got = mv.verify_mailbox("jane.doe@acme.com", connector=boom)
        assert got.verdict is MailboxVerdict.UNKNOWN
        assert got.reason == "disabled"

    def test_missing_helo_disables_the_smtp_path(self, monkeypatch):
        monkeypatch.setenv("SMTP_PROBE_HELO", "")
        t = FakeTransport(_script(550, 250))
        got = mv.verify_mailbox("jane.doe@acme.com", backend="smtp",
                                connector=connector_for(t))
        assert got.reason == "no_helo_configured"
        assert t.commands == []


class TestHttpsProviderFallback:
    class _Resp:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload

        def json(self):
            return self._payload

    def test_auto_falls_back_to_the_provider_when_port_25_is_blocked(self):
        """The measured reality on this machine: 25 is filtered, 443 is not."""
        got = mv.verify_mailbox(
            "jane.doe@acme.com", backend="auto", connector=lambda _d: None,
            api_key="k",
            http_get=lambda *_a, **_k: self._Resp({"data": {"status": "valid"}}))
        assert got.verdict is MailboxVerdict.DELIVERABLE
        assert got.provider == "hunter"

    @pytest.mark.parametrize("status,expected", [
        ("invalid", MailboxVerdict.UNDELIVERABLE),
        ("accept_all", MailboxVerdict.ACCEPT_ALL),
        ("webmail", MailboxVerdict.ACCEPT_ALL),
        ("unknown", MailboxVerdict.UNKNOWN),
    ])
    def test_provider_statuses_map_conservatively(self, status, expected):
        got = mv.verify_mailbox(
            "jane.doe@acme.com", backend="api", api_key="k",
            http_get=lambda *_a, **_k: self._Resp({"data": {"status": status}}))
        assert got.verdict is expected

    def test_no_key_and_no_smtp_is_an_honest_unknown(self):
        got = mv.verify_mailbox("jane.doe@acme.com", backend="auto",
                                connector=lambda _d: None, api_key="")
        assert got.verdict is MailboxVerdict.UNKNOWN
        assert got.reason == "no_transport"


class TestCaching:
    def test_a_known_catch_all_domain_short_circuits(self):
        t = FakeTransport(_script(250))
        mv.verify_mailbox("a.person@acme.com", backend="smtp",
                          connector=connector_for(t))

        def boom(_domain):
            raise AssertionError("cached catch-all must not reconnect")
        got = mv.verify_mailbox("b.person@acme.com", backend="smtp",
                                connector=boom)
        assert got.verdict is MailboxVerdict.ACCEPT_ALL

    def test_overflow_clears_rather_than_grows(self):
        for i in range(mv._RESULT_MAX + 5):
            mv._cache_put(mv._RESULT_CACHE, f"x{i}@a.com", "v",
                          mv._RESULT_TTL, mv._RESULT_MAX)
        assert len(mv._RESULT_CACHE) <= mv._RESULT_MAX
