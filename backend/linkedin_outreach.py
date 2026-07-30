"""Draft LinkedIn outreach without logging in, scraping, or sending for the user."""
from typing import Dict, Optional

try:
    from llm_client import complete as llm_complete
except ImportError:
    llm_complete = None


def _clean(value, limit: int = 300) -> str:
    return " ".join(str(value or "").split())[:limit]


def _fallback(contact: Dict, company: Optional[Dict], profile: Dict) -> str:
    first = (_clean(contact.get("name")).split() or ["there"])[0]
    sender = _clean(profile.get("full_name")) or "Jason"
    company_name = _clean(
        contact.get("company_name") or (company or {}).get("name")) or "your company"
    affinity = _clean(contact.get("affinity"))
    warm_line = f" I noticed we share {affinity}." if affinity else ""
    role_line = (
        f" your work as {_clean(contact.get('role'))}"
        if contact.get("role") else " your work"
    )
    return (
        f"Hi {first} — I came across{role_line} at {company_name}.{warm_line} "
        f"I'm exploring ways I could contribute and would value hearing about "
        f"what your team is building. Open to a brief chat? — {sender}"
    )


def draft_linkedin_message(
        contact: Dict, company: Optional[Dict], profile: Dict,
        custom_instructions: Optional[str] = None) -> str:
    """Return a short draft. This function never performs an external action."""
    fallback = _fallback(contact, company, profile)
    if not llm_complete:
        return fallback
    prompt = f"""Write one concise LinkedIn message under 700 characters.
This is a manual-send draft, not an email. Be warm and specific, not salesy.
Do not invent facts. Do not mention a shared connection, school, or employer
unless it appears in AFFINITY. End with a low-pressure request for a brief chat.
Return only the message.

SENDER: {_clean(profile.get("full_name"))}
SENDER BACKGROUND: {_clean(profile.get("background"), 600)}
RECIPIENT: {_clean(contact.get("name"))}
ROLE: {_clean(contact.get("role"))}
COMPANY: {_clean(contact.get("company_name") or (company or {}).get("name"))}
COMPANY SUMMARY: {_clean((company or {}).get("summary"), 600)}
AFFINITY: {_clean(contact.get("affinity"))}
USER INSTRUCTIONS: {_clean(custom_instructions, 500)}
"""
    try:
        result = llm_complete(prompt=prompt, max_tokens=300)
    except Exception:
        result = None
    result = (result or "").strip().strip('"')
    if not result:
        return fallback
    return result[:700].rstrip()
