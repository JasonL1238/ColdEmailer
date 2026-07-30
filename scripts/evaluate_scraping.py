#!/usr/bin/env python3
"""Repeatable scraping capability benchmark.

Default mode is deterministic and network-free so regressions are comparable.
Use --live NAME=URL to inspect current first-party crawl coverage separately;
live results are diagnostic because websites and bot controls change.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from enrichment import (  # noqa: E402
    EnrichmentService,
    discover_internal_links,
    extract_contact_candidates,
    extract_emails_from_html,
    page_mentions_company,
    select_outreach_contacts,
)


def deterministic_benchmark():
    checks = []

    html = """
      <p>jane [at] acme [dot] com</p>
      <a href="mailto:careers@acme.com?subject=Hi">Careers</a>
      <p>noreply@acme.com privacy@acme.com sprite@2x.png</p>
    """
    actual = set(extract_emails_from_html(html))
    expected = {"jane@acme.com", "careers@acme.com"}
    checks.append(("email_obfuscation_precision_recall", actual == expected,
                   {"expected": sorted(expected), "actual": sorted(actual)}))

    nav = """
      <a href="/our-leadership">Leadership</a>
      <a href="/press/launch">News</a>
      <a href="/legal/privacy">Privacy</a>
      <a href="https://linkedin.com/acme">LinkedIn</a>
    """
    links = discover_internal_links(nav, "https://acme.com", "acme.com")
    expected_links = [
        "https://acme.com/our-leadership",
        "https://acme.com/press/launch",
    ]
    checks.append(("first_party_link_discovery", links == expected_links,
                   {"expected": expected_links, "actual": links}))

    people = [{
        "url": "https://acme.com/leadership",
        "html": """
          <p>Jane Doe — CEO. Wharton MBA, University of Pennsylvania.</p>
          <a href="mailto:jane.doe@acme.com">Jane</a>
          <a href="mailto:hello@acme.com">Hello</a>
          <a href="mailto:senior.person@gmail.com">External</a>
        """,
    }]
    emails = extract_emails_from_html(people[0]["html"])
    contacts = extract_contact_candidates(
        people, "acme.com", "University of Pennsylvania")
    chosen = select_outreach_contacts(contacts, emails, "acme.com")
    top = chosen[0] if chosen else {}
    checks.append(("upenn_senior_contact_priority",
                   top.get("email") == "jane.doe@acme.com"
                   and top.get("school_match") is True
                   and top.get("role") == "CEO",
                   {"top_contact": top}))
    checks.append(("off_domain_contact_safety",
                   all(c["email"].endswith("@acme.com") for c in chosen),
                   {"selected": [c["email"] for c in chosen]}))

    identity_cases = [
        ("Openlayer", "Openlayer helps teams test AI systems.", True),
        ("Sabanto", "Epson printer ink and toner for every office.", False),
        ("Runway ML", "RunwayML builds creative AI tools.", True),
    ]
    identity_actual = [
        page_mentions_company(name, text) == expected
        for name, text, expected in identity_cases
    ]
    checks.append(("company_identity_guard", all(identity_actual),
                   {"cases_passed": sum(identity_actual),
                    "cases_total": len(identity_actual)}))

    passed = sum(ok for _, ok, _ in checks)
    return {
        "mode": "deterministic",
        "passed": passed,
        "total": len(checks),
        "score_percent": round(100 * passed / len(checks), 1),
        "checks": [
            {"name": name, "passed": ok, "detail": detail}
            for name, ok, detail in checks
        ],
    }


def live_evaluation(targets, school):
    service = EnrichmentService()
    results = []
    for target in targets:
        if "=" not in target:
            raise SystemExit("--live values must look like Company=https://company.com")
        name, url = target.split("=", 1)
        result = service.enrich(name.strip(), url.strip(), preferred_school=school)
        results.append({
            "company": name.strip(),
            "url": result.get("url"),
            "identity_verified": result.get("identity_verified"),
            "ok": result.get("ok"),
            "research_quality": result.get("research_quality"),
            "pages_attempted": result.get("pages_attempted"),
            "pages_scraped": result.get("pages_scraped"),
            "sources": result.get("research_sources"),
            "emails_found": len(result.get("emails") or []),
            "contacts": result.get("contacts"),
        })
    return {"mode": "live", "results": results}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live", action="append", default=[],
        help="Current-site diagnostic in Company=https://company.com form")
    parser.add_argument("--school", default="University of Pennsylvania")
    args = parser.parse_args()
    report = (live_evaluation(args.live, args.school)
              if args.live else deterministic_benchmark())
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
