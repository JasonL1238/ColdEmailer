"""Cloud LLM client for email generation. Reads API keys from environment only.

Supports OpenRouter, OpenAI, and Google Gemini. No keys in code.

Every provider gets the same three things:
  * a model ladder, so one exhausted model is not the end of the road;
  * a typed failure reason, so the UI can say *why* a draft fell back to the
    offline template instead of the useless catch-all "AI unavailable";
  * a per-process memory of models the API says do not exist, so a stale
    ladder entry costs one wasted call per process rather than one per
    generation.
"""
import os
import threading
from typing import List, Optional, Tuple

# Why a call gave up. These are the values that reach `emails.fallback_reason`
# and then the drafts screen, so they are user-facing vocabulary, not debug
# strings.
REASON_QUOTA = "llm_quota"          # out of quota / rate limited
REASON_AUTH = "llm_auth"            # key missing, invalid, or refused
REASON_NOT_FOUND = "llm_no_model"   # every model in the ladder is unknown
REASON_NETWORK = "llm_network"      # transport failed
REASON_EMPTY = "llm_empty"          # provider answered, with nothing usable
REASON_NO_PROVIDER = "llm_no_key"   # nothing configured at all
REASON_UNAVAILABLE = "llm_unavailable"  # legacy catch-all

# Human-readable, shown as-is in the UI.
REASON_LABELS = {
    REASON_QUOTA: "AI quota exhausted",
    REASON_AUTH: "AI key rejected",
    REASON_NOT_FOUND: "No usable AI model",
    REASON_NETWORK: "Could not reach the AI provider",
    REASON_EMPTY: "AI returned nothing usable",
    REASON_NO_PROVIDER: "No AI provider configured",
    REASON_UNAVAILABLE: "AI unavailable",
}

# Gemini ladder: tried in order, next one used on quota/error.
#
# Keep every entry a model the API actually serves. A previous ladder listed
# gemini-3-flash and five gemma-3-*-it sizes that this endpoint does not
# expose at all, so each exhausted-quota generation burned six guaranteed-404
# round trips before reaching a model that works.
GEMINI_MODEL_FALLBACK_ORDER = [
    "gemini-2.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
]

# OpenAI and OpenRouter had no ladder at all: one call, and any hiccup became
# a template email. Same treatment as Gemini.
OPENAI_MODEL_FALLBACK_ORDER = [
    "gpt-4o-mini",
    "gpt-4.1-mini",
]
OPENROUTER_MODEL_FALLBACK_ORDER = [
    "anthropic/claude-sonnet-4",
    "anthropic/claude-3.5-haiku",
]

# Models the provider has told us it does not serve. Retrying them within the
# same process is pure latency.
_dead_models: set = set()
_dead_lock = threading.Lock()


def _mark_dead(model: str) -> None:
    with _dead_lock:
        _dead_models.add(model)


def _is_dead(model: str) -> bool:
    with _dead_lock:
        return model in _dead_models


def _classify(exc: Exception) -> str:
    """Map a provider exception onto one of the REASON_* values."""
    name = type(exc).__name__
    text = str(exc).lower()
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if name == "ResourceExhausted" or code == 429 or "quota" in text \
            or "429" in text or "rate limit" in text:
        return REASON_QUOTA
    if code in (401, 403) or "api key" in text or "unauthorized" in text \
            or "permission" in text or "invalid_api_key" in text:
        return REASON_AUTH
    if code == 404 or "not found" in text or "does not exist" in text:
        return REASON_NOT_FOUND
    if any(w in text for w in ("timeout", "timed out", "connection",
                               "network", "unreachable", "dns")):
        return REASON_NETWORK
    return REASON_UNAVAILABLE


def _ladder(configured: str, default_order: List[str]) -> List[str]:
    """The configured model first, then the rest of the ladder.

    EMAIL_LLM_MODEL used to be computed and then thrown away for Gemini — the
    fallback loop rebound `model` on its first iteration, so a user who set
    EMAIL_LLM_MODEL=gemini-2.0-flash silently got gemini-2.5-flash instead and
    had no way to tell.
    """
    order = [m for m in default_order]
    if configured:
        order = [configured] + [m for m in order if m != configured]
    return order


def _openai_complete(
    prompt: str,
    system: Optional[str],
    model: str,
    api_key: str,
    base_url: Optional[str] = None,
    max_tokens: int = 1024,
) -> str:
    """Call OpenAI-compatible API (OpenAI or OpenRouter)."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=0.7,
    )
    choice = resp.choices[0] if resp.choices else None
    if not choice or not getattr(choice, "message", None):
        return ""
    return (choice.message.content or "").strip()


def _gemini_complete(
    prompt: str,
    system: Optional[str],
    model: str,
    api_key: str,
    max_tokens: int = 1024,
) -> Tuple[str, bool]:
    """Call Google Gemini. Returns (text, was_truncated).

    Uses the current `google-genai` SDK; `google-generativeai` is end-of-life
    and no longer receives fixes.
    """
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        max_output_tokens=max_tokens,
        temperature=0.7,
    )
    if system:
        config.system_instruction = system
    resp = client.models.generate_content(
        model=model, contents=prompt, config=config)

    text = (getattr(resp, "text", None) or "").strip()
    was_truncated = False
    candidates = getattr(resp, "candidates", None) or []
    if candidates:
        finish = getattr(candidates[0], "finish_reason", None)
        label = getattr(finish, "name", None) or str(finish or "")
        was_truncated = label.upper().endswith("MAX_TOKENS")
    return text, was_truncated


def get_cloud_llm_provider() -> Optional[str]:
    """
    Determine which cloud provider to use based on env.
    Returns 'openrouter' | 'openai' | 'gemini' | None if no cloud key is set.
    """
    provider = (os.getenv("EMAIL_LLM_PROVIDER") or "").strip().lower()
    if provider in ("openrouter", "openai", "gemini"):
        return provider
    # Auto-detect by key presence
    if os.getenv("OPENROUTER_API_KEY", "").strip():
        return "openrouter"
    if os.getenv("OPENAI_API_KEY", "").strip():
        return "openai"
    if os.getenv("GOOGLE_AI_API_KEY", "").strip():
        return "gemini"
    return None


def complete_json(
    prompt: str,
    system: Optional[str] = None,
    max_tokens: int = 2048,
):
    """Call the LLM and parse a JSON object/array out of the response.
    Returns parsed JSON or None."""
    import json as _json
    import re as _re

    out = complete(prompt=prompt, system=system, max_tokens=max_tokens)
    if not out:
        return None
    text = out.strip()
    # Strip markdown fences if present
    fence = _re.search(r"```(?:json)?\s*(.*?)```", text, _re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # Find outermost JSON object or array
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        end = text.rfind(close_ch)
        if start >= 0 and end > start:
            try:
                return _json.loads(text[start:end + 1])
            except Exception:
                continue
    return None


def complete_with_reason(
    prompt: str,
    system: Optional[str] = None,
    max_tokens: int = 1024,
) -> Tuple[Optional[str], Optional[str]]:
    """Call the configured cloud LLM.

    Returns (text, None) on success, or (None, REASON_*) explaining why every
    model in the ladder gave up. The reason is what lets a draft say "AI quota
    exhausted" rather than the unfalsifiable "AI unavailable".
    """
    provider = get_cloud_llm_provider()
    if not provider:
        return None, REASON_NO_PROVIDER

    configured = (os.getenv("EMAIL_LLM_MODEL") or "").strip()

    if provider == "openrouter":
        key = os.getenv("OPENROUTER_API_KEY", "").strip()
        order = _ladder(configured, OPENROUTER_MODEL_FALLBACK_ORDER)
        base_url = "https://openrouter.ai/api/v1"
    elif provider == "openai":
        key = os.getenv("OPENAI_API_KEY", "").strip()
        order = _ladder(configured, OPENAI_MODEL_FALLBACK_ORDER)
        base_url = None
    else:
        key = os.getenv("GOOGLE_AI_API_KEY", "").strip()
        order = _ladder(configured, GEMINI_MODEL_FALLBACK_ORDER)
        base_url = None

    if not key:
        return None, REASON_AUTH

    last_reason = REASON_UNAVAILABLE
    tried_any = False
    for model in order:
        if _is_dead(model):
            continue
        tried_any = True
        try:
            if provider == "gemini":
                out, was_truncated = _gemini_complete(
                    prompt=prompt, system=system, model=model,
                    api_key=key, max_tokens=max_tokens)
                if was_truncated:
                    print(f"[LLM] {model} output truncated (MAX_TOKENS), trying next model.")
                    last_reason = REASON_EMPTY
                    continue
            else:
                out = _openai_complete(
                    prompt=prompt, system=system, model=model, api_key=key,
                    base_url=base_url, max_tokens=max_tokens)
            if out:
                return out, None
            last_reason = REASON_EMPTY
            print(f"[LLM] {model} returned nothing, trying next model.")
        except Exception as exc:
            last_reason = _classify(exc)
            if last_reason == REASON_NOT_FOUND:
                # The ladder is stale. Do not pay for this again this process.
                _mark_dead(model)
                print(f"[LLM] {model} is not served by {provider}; skipping it from now on.")
            elif last_reason == REASON_AUTH:
                # A bad key fails identically for every model — stop early.
                print(f"[LLM] {provider} rejected the API key.")
                return None, REASON_AUTH
            elif last_reason == REASON_QUOTA:
                print(f"[LLM] {model} quota exceeded, trying next model.")
            else:
                print(f"[LLM] {model} failed ({type(exc).__name__}), trying next model.")

    if not tried_any:
        return None, REASON_NOT_FOUND
    if last_reason == REASON_QUOTA:
        print(f"[LLM] every {provider} model is out of quota. "
              f"Use another key, enable billing, or wait for the reset.")
    return None, last_reason


# Why the most recent complete() on THIS thread gave up. Generation runs in
# worker threads, so this is thread-local rather than global.
#
# It exists so `complete` can stay the single patch point every caller and
# test already uses. Returning a tuple instead would have quietly bypassed
# every `monkeypatch.setattr(..., "llm_complete", ...)` in the suite — which,
# when tried, sent the test run at the live API and spent real quota.
_last = threading.local()


def reset_failure_reason() -> None:
    _last.reason = None


def last_failure_reason() -> Optional[str]:
    return getattr(_last, "reason", None)


def complete(
    prompt: str,
    system: Optional[str] = None,
    max_tokens: int = 1024,
) -> Optional[str]:
    """Text-only wrapper. Returns None on any failure.

    Most callers only care whether they got text. Anything that wants to tell
    the user *why* calls reset_failure_reason() first and last_failure_reason()
    after, or uses complete_with_reason directly.
    """
    text, reason = complete_with_reason(
        prompt=prompt, system=system, max_tokens=max_tokens)
    _last.reason = reason
    return text
