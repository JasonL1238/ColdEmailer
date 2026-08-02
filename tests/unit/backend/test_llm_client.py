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
import time

import pytest

import llm_client
from llm_client import (GEMINI_MODEL_FALLBACK_ORDER, REASON_AUTH,
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
    """google.genai shape: int `.code`, no `.status_code`."""

    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code


class _OpenAIBoom(Exception):
    """openai shape: the *string* body error-code on `.code`, the number on
    `.status_code`. Reading `.code or .status_code` short-circuits on the
    string, which silently killed every numeric branch for this provider."""

    def __init__(self, msg, code=None, status_code=None):
        super().__init__(msg)
        self.code = code
        self.status_code = status_code


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

    def test_googles_quota_project_403_is_an_auth_problem_not_a_quota_one(self):
        """Real message. It contains the word "quota", and a quota-first check
        told the user to wait for a reset that will never come — the API is
        simply not enabled for the project."""
        exc = _Boom(
            "403 PERMISSION_DENIED. Generative Language API has not been used "
            "in project 764086 before or it is disabled... the request requires "
            "a quota project; set one with `gcloud auth application-default "
            "set-quota-project`.", code=403)
        assert _classify(exc) == REASON_AUTH

    def test_a_403_naming_one_model_does_not_condemn_the_key(self):
        """Real message. Treating this as an auth failure aborted the whole
        ladder over a single entitlement gap and blamed the user's key."""
        exc = _Boom("403 PERMISSION_DENIED. Permission denied on resource "
                    "model gemini-3.1-flash-lite.", code=403)
        assert _classify(exc) == REASON_NOT_FOUND

    def test_a_token_count_containing_429_is_not_a_quota_error(self):
        """Real message. Bare `"429" in text` fired on ordinary prose and told
        the user to wait for a reset; the fix is a shorter prompt."""
        exc = _OpenAIBoom(
            "This model's maximum context length is 4096 tokens, however you "
            "requested 4297 tokens (4297 in your prompt). Please reduce your "
            "prompt.", code="context_length_exceeded", status_code=400)
        assert _classify(exc) != REASON_QUOTA

    @pytest.mark.parametrize("code,status,expected", [
        ("invalid_api_key", 401, REASON_AUTH),
        ("no_credentials", 401, REASON_AUTH),
        ("account_deactivated", 403, REASON_AUTH),
        ("model_not_found", 400, REASON_NOT_FOUND),
        ("rate_limit_exceeded", 429, REASON_QUOTA),
        ("insufficient_quota", 429, REASON_QUOTA),
    ])
    def test_openais_string_code_does_not_hide_the_http_status(
            self, code, status, expected):
        """`.code or .status_code` returned the string every time, so `== 401`
        and friends were dead code and hard auth failures fell through to the
        catch-all this whole mechanism exists to eliminate."""
        assert _classify(_OpenAIBoom(code, code=code, status_code=status)) == expected

    def test_openrouters_unknown_model_wording_is_recognised(self):
        """Matching neither "not found" nor "does not exist" meant the model
        was never marked dead and was re-tried on every single generation."""
        exc = _OpenAIBoom("No endpoints found for meta-llama/imaginary-405b.",
                          code="model_not_found", status_code=404)
        assert _classify(exc) == REASON_NOT_FOUND


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


class TestTheGeminiCallItself:
    """The migrated function, which every other test stubs away.

    Mutation-testing the first version of this suite showed that deleting the
    system-instruction line entirely, and hardcoding was_truncated=False, both
    survived — the riskiest part of the SDK migration rested on one manual
    check.
    """

    def test_the_system_prompt_survives_into_the_request_config(self):
        """google-generativeai concatenated `system` onto the prompt string;
        google-genai carries it on the config object instead. If that
        assignment is dropped the model silently loses its instructions and
        the emails just get quietly worse."""
        from google.genai import types
        config = types.GenerateContentConfig(max_output_tokens=64,
                                             temperature=0.7)
        config.system_instruction = "You are a careful editor."
        assert config.model_dump()["system_instruction"] == "You are a careful editor."

    def test_a_typo_in_a_config_field_raises_rather_than_being_ignored(self):
        from google.genai import types
        with pytest.raises(Exception):
            types.GenerateContentConfig(max_output_takens=64)

    class _Resp:
        def __init__(self, text, finish):
            self.text = text
            self.candidates = [type("C", (), {"finish_reason": finish})()]

    class _Finish:
        def __init__(self, name):
            self.name = name

    def _call(self, monkeypatch, resp):
        captured = {}

        class _Models:
            def generate_content(self, **kwargs):
                captured.update(kwargs)
                return resp

        class _Client:
            def __init__(self, api_key=None):
                self.models = _Models()

        import google.genai as genai_mod
        monkeypatch.setattr(genai_mod, "Client", _Client)
        out = llm_client._gemini_complete(
            prompt="write it", system="be brief", model="gemini-2.5-flash",
            api_key="k", max_tokens=64)
        return out, captured

    def test_passes_the_prompt_and_system_through(self, monkeypatch):
        resp = self._Resp("the email", self._Finish("STOP"))
        (text, truncated), captured = self._call(monkeypatch, resp)
        assert text == "the email"
        assert truncated is False
        assert captured["contents"] == "write it"
        assert captured["model"] == "gemini-2.5-flash"
        assert captured["config"].system_instruction == "be brief"
        assert captured["config"].max_output_tokens == 64

    def test_detects_truncation_from_the_finish_reason(self, monkeypatch):
        resp = self._Resp("half an em", self._Finish("MAX_TOKENS"))
        (text, truncated), _ = self._call(monkeypatch, resp)
        assert truncated is True

    def test_a_blocked_candidate_yields_empty_rather_than_raising(self, monkeypatch):
        """google-generativeai's .text raised on a safety block; google-genai
        returns None. Treating that as a crash would fail the whole job."""
        resp = self._Resp(None, self._Finish("SAFETY"))
        (text, truncated), _ = self._call(monkeypatch, resp)
        assert text == ""
        assert truncated is False

    def test_survives_a_response_with_no_candidates(self, monkeypatch):
        resp = type("R", (), {"text": "ok", "candidates": []})()
        (text, truncated), _ = self._call(monkeypatch, resp)
        assert text == "ok"
        assert truncated is False


class TestDeadModelsExpire:
    def test_a_model_comes_back_after_the_window(self, monkeypatch):
        """A 403 on a model this key is not entitled to stops being true the
        moment billing is enabled; a permanent mark needed a restart to clear."""
        llm_client._mark_dead("gemini-2.5-flash")
        assert llm_client._is_dead("gemini-2.5-flash") is True
        # Capture the real clock first: llm_client.time IS the time module, so
        # patching through it would also rebind the call inside the lambda.
        real_monotonic = time.monotonic
        monkeypatch.setattr(
            llm_client.time, "monotonic",
            lambda: real_monotonic() + llm_client.DEAD_MODEL_TTL_SECONDS + 1)
        assert llm_client._is_dead("gemini-2.5-flash") is False


class TestOneRefusalDoesNotBlacklistTheLadder:
    """A 403 that merely quotes the model name used to walk the whole ladder
    marking every entry dead, so the next fifteen minutes of drafts fell back
    to the template with "No usable AI model" and made zero network calls."""

    def _env(self, monkeypatch):
        monkeypatch.setenv("EMAIL_LLM_PROVIDER", "openrouter")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.delenv("EMAIL_LLM_MODEL", raising=False)

    def test_a_moderation_403_quoting_the_model_marks_nothing_dead(self, monkeypatch):
        self._env(monkeypatch)
        exc = _OpenAIBoom(
            "Your request was flagged. metadata: {'model_slug': "
            "'anthropic/claude-sonnet-4'}", code="moderation", status_code=403)
        monkeypatch.setattr(llm_client, "_openai_complete",
                            lambda **kw: (_ for _ in ()).throw(exc))
        complete_with_reason("hi")
        assert llm_client._dead_models == {}, "one refusal disabled the ladder"

    def test_the_next_request_still_reaches_the_provider(self, monkeypatch):
        self._env(monkeypatch)
        exc = _OpenAIBoom("flagged, model_slug anthropic/claude-sonnet-4",
                          code="moderation", status_code=403)
        monkeypatch.setattr(llm_client, "_openai_complete",
                            lambda **kw: (_ for _ in ()).throw(exc))
        complete_with_reason("bad prompt")
        calls = []
        monkeypatch.setattr(llm_client, "_openai_complete",
                            lambda **kw: (calls.append(kw["model"]) or "the email"))
        text, reason = complete_with_reason("an innocent prompt")
        assert text == "the email"
        assert calls, "the ladder was still blacklisted from the previous call"

    def test_a_genuinely_unknown_model_is_still_remembered(self, monkeypatch):
        self._env(monkeypatch)
        exc = _OpenAIBoom("No endpoints found for imaginary/model.",
                          code="model_not_found", status_code=404)
        monkeypatch.setattr(llm_client, "_openai_complete",
                            lambda **kw: (_ for _ in ()).throw(exc))
        complete_with_reason("hi")
        assert llm_client._dead_models, "a real unknown model must be skipped"

    def test_a_revoked_key_beats_a_message_that_names_a_model(self, monkeypatch):
        """Reading this as "unknown model" walked every entry and told the user
        to fix their model list instead of their key."""
        exc = _OpenAIBoom(
            "Your API key has been revoked. Requested model: gpt-4o-mini.",
            code="invalid_api_key", status_code=403)
        assert _classify(exc) == REASON_AUTH


class TestOutOfCredits:
    def test_a_402_is_a_spending_problem_not_a_broken_request(self):
        """OpenRouter's out-of-credits body is "You requested up to 4297
        tokens, but can only afford 100" — which is why bare "429" substring
        matching was accidentally right about it, and why removing that match
        regressed the most common paid-tier failure to "AI unavailable"."""
        exc = _OpenAIBoom(
            "This request requires more credits, or fewer max_tokens. You "
            "requested up to 4297 tokens, but can only afford 100.",
            code="insufficient_credits", status_code=402)
        assert _classify(exc) == REASON_QUOTA


class TestWhichModelActuallyWrote:
    def test_records_the_substitute(self, monkeypatch):
        """The ladder can silently swap vendors — a Gemini name configured
        against an OpenRouter provider yields an Anthropic model at a very
        different price, and the draft looks identical."""
        monkeypatch.setenv("EMAIL_LLM_PROVIDER", "gemini")
        monkeypatch.setenv("GOOGLE_AI_API_KEY", "test-key")
        monkeypatch.setenv("EMAIL_LLM_MODEL", "gemini-2.0-flash")
        tried = []

        def fake(prompt, system, model, api_key, max_tokens):
            tried.append(model)
            if model == "gemini-2.0-flash":
                raise _Boom("quota", code=429)
            return "the email", False

        monkeypatch.setattr(llm_client, "_gemini_complete", fake)
        text, reason = complete_with_reason("hi")
        assert text == "the email"
        assert llm_client.last_model_used() == tried[-1]
        assert llm_client.last_model_used() != "gemini-2.0-flash"

    def test_a_failed_call_records_no_model(self, monkeypatch):
        monkeypatch.setenv("EMAIL_LLM_PROVIDER", "gemini")
        monkeypatch.setenv("GOOGLE_AI_API_KEY", "test-key")
        monkeypatch.setattr(llm_client, "_gemini_complete",
                            lambda **kw: (_ for _ in ()).throw(_Boom("quota", code=429)))
        complete_with_reason("hi")
        assert llm_client.last_model_used() is None


class TestTheSuiteCannotReachALiveProvider:
    def test_conftest_strips_every_provider_key(self):
        """A stub that misses must fail offline, not spend the user's quota."""
        for key in ("GOOGLE_AI_API_KEY", "OPENAI_API_KEY",
                    "OPENROUTER_API_KEY", "HUNTER_API_KEY"):
            assert not os.getenv(key), f"{key} leaked into the test environment"
        assert get_cloud_llm_provider() is None
