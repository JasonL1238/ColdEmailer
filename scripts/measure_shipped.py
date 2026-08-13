"""Before/after measurement of the shipped scraping changes, over the corpus.

Imports the *pristine* backend from a baseline directory and the working-tree
backend in separate subprocesses, runs the same extraction over the same 30
captured sites, and reports the delta. No network, no AI, no database.

    python scripts/measure_shipped.py <baseline_backend_dir>

Measures:
  H12  machine-generated addresses removed, and the MX lookups they caused
  H13  `\\uXXXX`-escape artifacts removed
  H11  page-parse work avoided when a crawl refreshes contacts repeatedly
  H8   people pages the link vocabulary can now reach
  H7   sitemap coverage and the people pages it adds
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
CORPUS = _REPO / "tests" / "fixtures" / "corpus"

# Runs inside a subprocess with one backend dir on sys.path, so the two
# versions of the module can never collide in one interpreter.
_WORKER = r'''
import json, sys, re
sys.path.insert(0, sys.argv[1])
from pathlib import Path
import enrichment

CORPUS = Path(sys.argv[2])
out = {
    "emails": {}, "contacts": 0, "stubs": 0, "mx_domains": {},
    "links": {}, "sitemap_people": {}, "escape_artifacts": [],
}
# The escape prefix must be followed by more local-part, and must not simply be
# the start of a real word: bare "gt" matches gtm@modal.com, a real address.
ESCAPE = re.compile(r"^(u00[0-9a-fA-F]{2}|x[0-9a-fA-F]{2})[A-Za-z0-9._%+-]")

for d in sorted(CORPUS.iterdir()):
    f = d / "site.json"
    if not f.exists():
        continue
    data = json.loads(f.read_text())
    slug, domain = d.name, data["domain"]
    pages = []
    site_emails = set()
    link_hits = set()
    for url, rec in data["pages"].items():
        body = rec.get("body") or ""
        if not body or "<" not in body:
            continue
        pages.append({"url": url, "html": body, "text": ""})
        for a in enrichment.extract_emails_from_html(body):
            site_emails.add(a.lower())
            if ESCAPE.match(a.split("@")[0]):
                out["escape_artifacts"].append(a.lower())
        for link in enrichment.discover_internal_links(body, url, domain, limit=64):
            link_hits.add(link)
    out["emails"][slug] = sorted(site_emails)
    out["links"][slug] = len(link_hits)
    # Domains the crawl would ask the resolver about, and how many times
    # annotate_contact would ask (once per contact, per refresh pass).
    cands = enrichment.extract_contact_candidates(pages, domain)
    out["contacts"] += len(cands)
    out["stubs"] += sum(1 for c in cands if c.get("name_from_email"))
    doms = [ (c.get("email") or "").split("@")[-1].lower()
             for c in cands if c.get("email") ]
    out["mx_domains"][slug] = [len(doms), len(set(d for d in doms if d))]
    # H7: what would the sitemap contribute, if we had one?
    sm = data.get("sitemap") or ""
    if sm and "<loc" in sm.lower():
        locs = [m.group(1) for m in re.finditer(r"<loc>\s*([^<\s]+)\s*</loc>", sm, re.I)]
        ranked = getattr(enrichment, "rank_sitemap_pages", None)
        out["sitemap_people"][slug] = (
            [len(locs), len(ranked(locs, domain))] if ranked else [len(locs), -1])
print(json.dumps(out))
'''


def collect(backend_dir: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, "-c", _WORKER, str(backend_dir), str(CORPUS)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-4000:])
        raise SystemExit(f"worker failed for {backend_dir}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


MACHINE = re.compile(r"^[0-9a-f]{16,}@")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    baseline_dir = Path(sys.argv[1])
    print(f"baseline: {baseline_dir}\nworking : {_REPO / 'backend'}\n")

    before = collect(baseline_dir)
    after = collect(_REPO / "backend")

    b_all = {a for v in before["emails"].values() for a in v}
    a_all = {a for v in after["emails"].values() for a in v}
    removed = b_all - a_all
    added = a_all - b_all
    machine = {a for a in removed if MACHINE.match(a)}

    print("=" * 72)
    print("H12/H13 — what the crawler now refuses to call a contact")
    print("=" * 72)
    print(f"  unique addresses extracted : {len(b_all)} -> {len(a_all)}  "
          f"({100.0 * (len(b_all) - len(a_all)) / max(len(b_all), 1):.1f}% removed)")
    print(f"  machine-generated removed  : {len(machine)}")
    print(f"  other removed              : {len(removed - machine)}")
    for a in sorted(removed - machine)[:12]:
        print(f"      - {a}")
    print(f"  newly found (escape fix)   : {len(added)}")
    for a in sorted(added)[:12]:
        print(f"      + {a}")
    print(f"  escape artifacts present   : "
          f"{len(set(before['escape_artifacts']))} -> "
          f"{len(set(after['escape_artifacts']))}")

    print()
    print("=" * 72)
    print("H12 — MX amplification")
    print("=" * 72)
    b_look = sum(v[0] for v in before["mx_domains"].values())
    a_look = sum(v[0] for v in after["mx_domains"].values())
    b_uniq = sum(v[1] for v in before["mx_domains"].values())
    a_uniq = sum(v[1] for v in after["mx_domains"].values())
    print(f"  contacts carrying an email : {b_look} -> {a_look}")
    print(f"  distinct domains behind them: {b_uniq} -> {a_uniq}")
    print(f"  resolver calls per refresh pass, uncached : {b_look} -> {a_look}")
    print(f"  resolver calls per refresh pass, memoized : {b_look} -> {a_uniq}"
          f"   ({b_look}x -> {a_uniq}x)")

    print()
    print("=" * 72)
    print("H8 — links the people vocabulary can now reach")
    print("=" * 72)
    gained = [(s, after["links"][s] - before["links"][s])
              for s in sorted(before["links"])
              if after["links"][s] != before["links"][s]]
    print(f"  total discoverable links   : "
          f"{sum(before['links'].values())} -> {sum(after['links'].values())}")
    print(f"  sites that gained links    : {len(gained)}/{len(before['links'])}")
    for slug, delta in sorted(gained, key=lambda x: -x[1])[:12]:
        print(f"      {slug:<20} {before['links'][slug]:>4} -> "
              f"{after['links'][slug]:>4}  ({delta:+d})")

    print()
    print("=" * 72)
    print("H7 — sitemap coverage")
    print("=" * 72)
    have = [s for s, v in after["sitemap_people"].items() if v[0] > 0]
    people = [(s, v[1]) for s, v in after["sitemap_people"].items() if v[1] > 0]
    print(f"  sites publishing a sitemap : {len(have)}/30")
    print(f"  sites where it names people pages: {len(people)}")
    for slug, n in sorted(people, key=lambda x: -x[1])[:12]:
        total = after["sitemap_people"][slug][0]
        print(f"      {slug:<20} {n:>3} people pages out of {total} sitemap URLs")

    print()
    print("=" * 72)
    print("Contact quality")
    print("=" * 72)
    print(f"  contacts built             : {before['contacts']} -> {after['contacts']}")
    print(f"  of those, name_from_email stubs: "
          f"{before['stubs']} -> {after['stubs']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
