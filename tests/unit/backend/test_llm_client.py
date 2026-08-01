"""The LLM client must degrade honestly and cheaply.

Three real defects motivated these tests:
  * EMAIL_LLM_MODEL was computed and then thrown away for Gemini — the
    fallback loop rebound `model` on its first iteration, so a user who set
    it got a different model with no way to tell.
  * Six of the nine ladder entries were models the endpoint does not serve,
    so every quota-exhausted generation paid for six guaranteed 404s first.
  * OpenAI and OpenRouter had no ladder at all: one call, and any hiccup
    became a template email with the reason "llm_unavailable".
"""
import os

import pytest

import llm_client
from llm_client import (GEMINI_MODEL_FALLBACK_ORDER, REASON_AUTH, REASON_EMPTY,
                        REASON_NETWORK, REASON_NOT_FOUND, REASON_NO_PROVIDER,
                        REASON_QUOTA, _classify, _ladder,
                        complete_with_reason, get_cloud_llm_provider,
                        last_failure_reason, reset_failure_reason)


@pytest.fixture(autouse=True)
def clean_dead_models():
    llm_client._dead_models.clear()
    reset_failure_reason()
    yield
    llm_client._dead_models.clear()


class _Boom(Exception):
    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code


class TestClassify:
    @pytest.mark.parametrize("exc,expected", [
        (_Boom("429 Too Many Requests"), REASON_QUOTA),
        (_Boom("You exceeded your current quota"), REASON_QUOTA),
        (_Boom("rate limit reached"), REASON_QUOTA),
        (_Boom("nope", code=429), REASON_QUOTA),
        (_Boom("Invalid API key provided"), REASON_AUTH),
        (_Boom("nope", code=401), REASON_AUTH),
        (_Boom("nope", code=403), REASON_AUTH),
        (_Boom("model not found"), REASON_NOT_FOUND),
        (_Boom("nope", code=404), REASON_NOT_FOUND),
        (_Boom("Connection timed out"), REASON_NETWORK),
        (_Boom("dns failure"), REASON_NETWORK),
    ])
    def test_maps_provider_errors_to_a_reason(self, exc, expected):
        assert _classify(exc) == expected

    def test_quota_wins_over_a_generic_message(self):
        """A 429 that also says 'connection' must not be read as a network blip
        — the user needs to be told to wait or switch keys."""
        assert _classify(_Boom("connection reset: 429 quota")) == REASON_QUOTA


class TestLadder:
    def test_the_configured_model_goes_first(self):
        order = _ladder("gemini-2.0-flash", GEMINI_MODEL_FALLBACK_ORDER)
        assert order[0] == "gemini-2.0-flash"

    def test_the_configured_model_is_not_duplicated(self):
        order = _ladder("gemini-2.5-flash", GEMINI_MODEL_FALLBACK_ORDER)
        assert order.count("gemini-2.5-flash") == 1
        assert order[0] == "gemini-2.5-flash"

    def test_no_configured_model_leaves_the_ladder_alone(self):
        assert _ladder("", GEMINI_MODEL_FALLBACK_ORDER) == GEMINI_MODEL_FALLBACK_ORDER

    def test_the_shipped_ladder_has_no_gemma_or_gemini_3_flash(self):
        """These were in the ladder and are not served by this endpoint; each
        one cost a guaranteed 404 on every exhausted generation."""
        assert not [m for m in GEMINI_MODEL_FALLBACK_ORDER if m.startswith("gemma")]
        assert "gemini-3-flash" not in GEMINI_MODEL_FALLBACK_ORDER


class TestFallbackWalksTheLadder:
    def _gemini_env(self, monkeypatch):
        monkeypatch.setenv("EMAIL_LLM_PROVIDER", "gemini")
        monkeypatch.setenv("GOOGLE_AI_API_KEY", "test-key")
        monkeypatch.delenv("EMAIL_LLM_MODEL", raising=False)

    def test_moves_past_an_exhausted_model_to_a_working_one(self, monkeypatch):
        self._gemini_env(monkeypatch)
        tried = []

        def fake(prompt, system, model, api_key, max_tokens):
            tried.append(model)
            if len(tried) == 1:
                raise _Boom("quota exceeded", code=429)
            return "the email", False

        monkeypatch.setattr(llm_client, "_gemini_complete", fake)
        text, reason = complete_with_reason("hi")
        assert text == "the email"
        assert reason is None
        assert len(tried) == 2

    def test_reports_quota_when_every_model_is_exhausted(self, monkeypatch):
        self._gemini_env(monkeypatch)
        monkeypatch.setattr(llm_client, "_gemini_complete",
                            lambda **kw: (_ for _ in ()).throw(_Boom("quota", code=429)))
        text, reason = complete_with_reason("hi")
        assert text is None
        assert reason == REASON_QUOTA

    def test_a_rejected_key_stops_immediately(self, monkeypatch):
        """Every model fails identically on a bad key; walking the ladder is
        pure latency and pointless load on the provider."""
        self._gemini_env(monkeypatch)
        calls = []

        def fake(**kw):
            calls.append(kw["model"])
            raise _Boom("invalid api key", code=401)

        monkeypatch.setattr(llm_client, "_gemini_complete", fake)
        text, reason = complete_with_reason("hi")
        assert text is None
        assert reason == REASON_AUTH
        assert len(calls) == 1

    def test_a_404_model_is_not_retried_within_the_process(self, monkeypatch):
        self._gemini_env(monkeypatch)
        calls = []

        def fake(**kw):
            calls.append(kw["model"])
            if kw["model"] == GEMINI_MODEL_FALLBACK_ORDER[0]:
                raise _Boom("not found", code=404)
            return "ok", False

        monkeypatch.setattr(llm_client, "_gemini_complete", fake)
        assert complete_with_reason("hi")[0] == "ok"
        first_round = list(calls)
        calls.clear()
        assert complete_with_reason("hi")[0] == "ok"
        assert GEMINI_MODEL_FALLBACK_ORDER[0] in first_round
        assert GEMINI_MODEL_FALLBACK_ORDER[0] not in calls

    def test_truncated_output_moves_on_rather_than_returning_half_an_email(
            self, monkeypatch):
        self._gemini_env(monkeypatch)
        seen = []

        def fake(prompt, system, model, api_key, max_tokens):
            seen.append(model)
            if len(seen) == 1:
                return "half an em", True      # was_truncated
            return "a whole email", False

        monkeypatch.setattr(llm_client, "_gemini_complete", fake)
        text, reason = complete_with_reason("hi")
        assert text == "a whole email"
        assert reason is None

    def test_no_provider_configured_says_so(self, monkeypatch):
        for key in ("EMAIL_LLM_PROVIDER", "GOOGLE_AI_API_KEY",
                    "OPENAI_API_KEY", "OPENROUTER_API_KEY"):
            monkeypatch.delenv(key, raising=False)
        assert get_cloud_llm_provider() is None
        assert complete_with_reason("hi") == (None, REASON_NO_PROVIDER)

    def test_a_configured_provider_with_no_key_is_an_auth_problem(self, monkeypatch):
        monkeypatch.setenv("EMAIL_LLM_PROVIDER", "openai")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        assert complete_with_reason("hi") == (None, REASON_AUTH)


class TestOpenAINowHasALadderToo:
    def test_openai_falls_past_a_failing_model(self, monkeypatch):
        monkeypatch.setenv("EMAIL_LLM_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.delenv("EMAIL_LLM_MODEL", raising=False)
        tried = []

        def fake(prompt, system, model, api_key, base_url, max_tokens):
            tried.append(model)
            if len(tried) == 1:
                raise _Boom("500 server error")
            return "the email"

        monkeypatch.setattr(llm_client, "_openai_complete", fake)
        text, reason = complete_with_reason("hi")
        assert text == "the email"
        assert reason is None
        assert len(tried) == 2, "one failure used to mean a template email"

    def test_openrouter_uses_the_openrouter_base_url(self, monkeypatch):
        monkeypatch.setenv("EMAIL_LLM_PROVIDER", "openrouter")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        seen = {}

        def fake(prompt, system, model, api_key, base_url, max_tokens):
            seen["base_url"] = base_url
            return "ok"

        monkeypatch.setattr(llm_client, "_openai_complete", fake)
        complete_with_reason("hi")
        assert seen["base_url"] == "https://openrouter.ai/api/v1"


class TestTheReasonSeamSurvivesStubbing:
    """`complete` stays the single patch point.

    Returning a tuple from it instead would have bypassed every
    monkeypatch.setattr(..., 'llm_complete', ...) in this suite — which, when
    tried, pointed the whole test run at the live API and spent real quota.
    """

    def test_complete_records_the_reason_for_the_caller(self, monkeypatch):
        monkeypatch.setenv("EMAIL_LLM_PROVIDER", "gemini")
        monkeypatch.setenv("GOOGLE_AI_API_KEY", "test-key")
        monkeypatch.setattr(llm_client, "_gemini_complete",
                            lambda **kw: (_ for _ in ()).throw(_Boom("quota", code=429)))
        reset_failure_reason()
        assert llm_client.complete("hi") is None
        assert last_failure_reason() == REASON_QUOTA

    def test_a_successful_call_leaves_no_stale_reason(self, monkeypatch):
        monkeypatch.setenv("EMAIL_LLM_PROVIDER", "gemini")
        monkeypatch.setenv("GOOGLE_AI_API_KEY", "test-key")
        monkeypatch.setattr(llm_client, "_gemini_complete",
                            lambda **kw: ("text", False))
        assert llm_client.complete("hi") == "text"
        assert last_failure_reason() is None

    def test_reset_clears_a_previous_failure(self, monkeypatch):
        monkeypatch.setenv("EMAIL_LLM_PROVIDER", "gemini")
        monkeypatch.setenv("GOOGLE_AI_API_KEY", "test-key")
        monkeypatch.setattr(llm_client, "_gemini_complete",
                            lambda **kw: (_ for _ in ()).throw(_Boom("quota", code=429)))
        llm_client.complete("hi")
        assert last_failure_reason() == REASON_QUOTA
        reset_failure_reason()
        assert last_failure_reason() is None


class TestTheSuiteCannotReachALiveProvider:
    def test_conftest_strips_every_provider_key(self):
        """A stub that misses must fail offline, not spend the user's quota."""
        for key in ("GOOGLE_AI_API_KEY", "OPENAI_API_KEY",
                    "OPENROUTER_API_KEY", "HUNTER_API_KEY"):
            assert not os.getenv(key), f"{key} leaked into the test environment"
        assert get_cloud_llm_provider() is None
