"""
Cloud LLM client for email generation. Reads API keys from environment only.
Supports OpenRouter, OpenAI, and Google Gemini. No keys in code.
Gemini: tries models in order; on quota (429) falls back to next in list.
"""
import os
from typing import Optional

# Gemini model fallback order: try in order; on ResourceExhausted (429) use next.
GEMINI_MODEL_FALLBACK_ORDER = [
    "gemini-2.5-flash",
    "gemini-3-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
    "gemma-3-27b-it",
    "gemma-3-12b-it",
    "gemma-3-4b-it",
    "gemma-3-2b-it",
    "gemma-3-1b-it",
]

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
) -> tuple[str, bool]:
    """Call Google Gemini API. Returns (text, was_truncated). was_truncated=True if finish_reason was MAX_TOKENS."""
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    full_prompt = (system + "\n\n" + prompt) if system else prompt
    gemini = genai.GenerativeModel(model)
    resp = gemini.generate_content(
        full_prompt,
        generation_config={"max_output_tokens": max_tokens, "temperature": 0.7},
    )
    if not resp or not resp.text:
        return "", False
    # Detect truncation: finish_reason MAX_TOKENS (2) means output was cut off
    was_truncated = False
    if getattr(resp, "candidates", None) and len(resp.candidates) > 0:
        finish_reason = getattr(resp.candidates[0], "finish_reason", None)
        if finish_reason is not None:
            # FinishReason enum: STOP=1, MAX_TOKENS=2, SAFETY=3, etc.
            reason_val = getattr(finish_reason, "name", None) or getattr(finish_reason, "value", finish_reason)
            if reason_val == 2 or reason_val == "MAX_TOKENS":
                was_truncated = True
    return resp.text.strip(), was_truncated


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


def complete(
    prompt: str,
    system: Optional[str] = None,
    max_tokens: int = 1024,
) -> Optional[str]:
    """
    Call the configured cloud LLM. Keys and provider come from env only.
    Returns generated text, or None if provider/key missing or on API error.
    """
    provider = get_cloud_llm_provider()
    if not provider:
        return None

    model = (os.getenv("EMAIL_LLM_MODEL") or "").strip()
    if not model:
        if provider == "openrouter":
            model = "anthropic/claude-sonnet-4"
        elif provider == "openai":
            model = "gpt-4o-mini"
        else:
            model = "gemini-2.0-flash"

    try:
        if provider == "openrouter":
            api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
            if not api_key:
                return None
            return _openai_complete(
                prompt=prompt,
                system=system,
                model=model,
                api_key=api_key,
                base_url="https://openrouter.ai/api/v1",
                max_tokens=max_tokens,
            )
        if provider == "openai":
            api_key = os.getenv("OPENAI_API_KEY", "").strip()
            if not api_key:
                return None
            return _openai_complete(
                prompt=prompt,
                system=system,
                model=model,
                api_key=api_key,
                base_url=None,
                max_tokens=max_tokens,
            )
        if provider == "gemini":
            api_key = os.getenv("GOOGLE_AI_API_KEY", "").strip()
            if not api_key:
                return None
            # Try each model in order; on quota (429) or failure, try next.
            last_error = None
            for model in GEMINI_MODEL_FALLBACK_ORDER:
                try:
                    out, was_truncated = _gemini_complete(
                        prompt=prompt,
                        system=system,
                        model=model,
                        api_key=api_key,
                        max_tokens=max_tokens,
                    )
                    if was_truncated:
                        print(f"[LLM] {model} output truncated (MAX_TOKENS), trying next model.")
                        continue
                    if out:
                        return out
                except Exception as _e:
                    last_error = _e
                    # Quota or other error: try next model
                    if type(_e).__name__ == "ResourceExhausted" or "quota" in str(_e).lower() or "429" in str(_e):
                        print(f"[LLM] {model} quota exceeded, trying next model.")
                    else:
                        print(f"[LLM] {model} failed ({type(_e).__name__}), trying next model.")
                    continue
            if last_error:
                if type(last_error).__name__ == "ResourceExhausted" or "quota" in str(last_error).lower():
                    print("[LLM] All Gemini models exhausted (free tier). Use a new key, enable billing, or wait for reset.")
                else:
                    print(f"[LLM] {type(last_error).__name__}: {last_error}")
            return None
    except Exception as _e:
        if type(_e).__name__ == "ResourceExhausted" or "quota" in str(_e).lower():
            print("[LLM] Gemini quota exceeded (free tier). Each generate attempt counts. Use a new key, enable billing, or wait for daily reset.")
        else:
            print(f"[LLM] {type(_e).__name__}: {_e}")
        return None
    return None