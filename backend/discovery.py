"""
Company discovery: natural-language query -> real companies -> scraped
sites -> contact emails + metadata, stored in the database.

Runs as a background thread reporting progress through the jobs table:
  Stage 1  find candidate companies (cloud LLM + DuckDuckGo, deduped)
  Stage 2  per company: find site, scrape, extract emails + metadata
"""
import re
import threading
from typing import Dict, List, Optional

from db import Database
from ddg_search import ddg_text_search
from enrichment import (EnrichmentService, registered_domain,
                        scrape_status_for, select_outreach_contacts)

try:
    from llm_client import complete_json, get_cloud_llm_provider
except ImportError:
    complete_json = None
    get_cloud_llm_provider = lambda: None

MAX_COMPANIES_PER_RUN = 25

# Domains that are lists/aggregators/socials — never actual companies to email
AGGREGATOR_DOMAINS = {
    "linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com",
    "indeed.com", "glassdoor.com", "crunchbase.com", "wikipedia.org",
    "bloomberg.com", "yelp.com", "youtube.com", "github.com", "medium.com",
    "angel.co", "wellfound.com", "pitchbook.com", "zoominfo.com", "apollo.io",
    "owler.com", "cbinsights.com", "reddit.com", "forbes.com", "techcrunch.com",
    "ycombinator.com", "producthunt.com", "g2.com", "capterra.com",
    "builtin.com", "builtinnyc.com", "levels.fyi", "quora.com", "clutch.co",
    "goodfirms.co", "themanifest.com", "upwork.com", "fiverr.com",
    "google.com", "bing.com", "duckduckgo.com", "amazon.com", "apple.com",
    "eventbrite.com", "meetup.com", "startupsavant.com", "failory.com",
    "explodingtopics.com", "seedtable.com", "topstartups.io", "welcometothejungle.com",
    "spotify.com", "soundcloud.com", "vimeo.com", "tiktok.com", "pinterest.com",
    "substack.com", "notion.site", "wordpress.com", "blogspot.com", "wixsite.com",
}

# Domain-parking / for-sale landing pages and content farms. A company whose
# "website" is one of these was mis-identified — scraping it yields nonsense
# and any address on it belongs to someone else entirely.
JUNK_HOST_PATTERNS = (
    "forsale.", "domainmarket", "dan.com", "sedo.com", "afternic",
    "hugedomains", "buydomains", "godaddy", "namecheap", "parkingcrew",
    "bodis.com", "above.com", "undeveloped.com",
)


def is_junk_site(url: Optional[str]) -> bool:
    """True for parked/for-sale pages and obvious content farms."""
    if not url:
        return False
    host = (registered_domain(url) or "").lower()
    full = url.lower()
    if any(p in full for p in JUNK_HOST_PATTERNS):
        return True
    if host in AGGREGATOR_DOMAINS:
        return True
    # Content farms: long keyword-stuffed hostnames like
    # "dailythemedcrosswordanswers.com"
    stem = host.split(".")[0]
    if len(stem) > 24 and stem.isalpha():
        return True
    return False


def _guess_name_from_email(local: str) -> str:
    """'jane.doe' -> 'Jane Doe'; generic/unparseable locals -> ''."""
    generic = {"hello", "hi", "hey", "info", "contact", "team", "careers", "jobs",
               "recruiting", "talent", "press", "sales", "support", "admin",
               "office", "founders", "founder", "ceo", "general"}
    if local.lower() in generic:
        return ""
    parts = re.split(r"[._-]", local)
    words = [p for p in parts if p.isalpha() and len(p) > 1]
    if len(words) >= 2:
        return " ".join(w.capitalize() for w in words[:3])
    return ""


def _role_for_email(local: str) -> Optional[str]:
    local = local.lower()
    mapping = {
        "careers": "Careers inbox", "jobs": "Careers inbox",
        "recruiting": "Recruiting", "talent": "Recruiting",
        "founders": "Founders", "founder": "Founders", "ceo": "CEO",
        "hello": "General inbox", "hi": "General inbox", "hey": "General inbox",
        "contact": "General inbox", "info": "General inbox", "team": "Team inbox",
        "press": "Press", "sales": "Sales", "support": "Support",
    }
    return mapping.get(local)


def contact_notes(candidate: Dict) -> Optional[str]:
    """Human-readable provenance for affinity matches and risky addresses."""
    notes = []
    if candidate.get("school_match"):
        school = candidate.get("school") or "your school"
        notes.append(
            f"Same-school match: {school} is mentioned in this person's "
            "public company biography. Verify before referencing it.")
    for affinity in candidate.get("affinity") or []:
        if affinity and affinity not in (candidate.get("school") or ""):
            notes.append(f"Warm match: {affinity}. Verify before referencing it.")
    if candidate.get("source_url"):
        notes.append(f"Source: {candidate['source_url']}")
    if candidate.get("evidence"):
        notes.append(f'Evidence: "{candidate["evidence"][:240]}"')
    if candidate.get("warning"):
        notes.append(candidate["warning"])
    return " ".join(notes) or None


def discovery_scrape_status(enriched: dict) -> str:
    """The status a freshly discovered company gets.

    Deliberately built on enrichment.scrape_status_for rather than repeating
    it: discovery used to carry its own hand-rolled copy of the mapping, and
    that copy had no identity_verified case. A company found on an unrelated
    website was therefore filed as "Research failed", which reads as transient
    — so the user re-runs research and gets the same wrong site — instead of
    "Wrong site found", which tells them the domain is what needs fixing.

    The one addition is discovery-specific: a page that was read but listed no
    addresses is not a failure, and the summary it produced is real evidence.
    """
    status = scrape_status_for(enriched)
    if (status == "scraped" and not enriched.get("emails")
            and not enriched.get("contacts")):
        return "no_emails_found"
    return status


class DiscoveryService:
    def __init__(self, db: Database, enrichment: EnrichmentService):
        self.db = db
        self.enrichment = enrichment

    # ---------- public API ----------

    def start(self, query: str, count: int = 10) -> Dict:
        count = max(1, min(int(count or 10), MAX_COMPANIES_PER_RUN))
        job = self.db.create_job("discovery", {"query": query, "count": count})
        thread = threading.Thread(
            target=self._run_safe, args=(job["id"], query, count), daemon=True
        )
        thread.start()
        return job

    def cancel(self, job_id: str) -> bool:
        """Only cancels discovery jobs — the id comes from the URL, so without
        the type check this route could kill a running generation or send."""
        job = self.db.get_job(job_id)
        if job and job["type"] == "discovery" and job["status"] == "running":
            self.db.update_job(job_id, status="cancelled")
            return True
        return False

    # ---------- pipeline ----------

    def _cancelled(self, job_id: str) -> bool:
        job = self.db.get_job(job_id)
        return not job or job["status"] == "cancelled"

    def _run_safe(self, job_id: str, query: str, count: int):
        try:
            self._run(job_id, query, count)
        except Exception as e:
            print(f"[discovery] job {job_id} crashed: {e}")
            self.db.finish_job(job_id, status="failed", error=str(e))

    def _run(self, job_id: str, query: str, count: int):
        self.db.update_job(job_id, stage="Finding companies", progress_total=count)
        self.db.log_event("discovery", job_id, "started", query)

        candidates = self._find_candidates(query, count)
        if self._cancelled(job_id):
            return
        if not candidates:
            self.db.finish_job(
                job_id, status="failed",
                error="No companies found for this search. Try a broader or more "
                      "specific query (e.g. 'seed-stage fintech startups in New York').",
            )
            return

        results: List[Dict] = []
        companies_added = 0
        contacts_added = 0

        self.db.update_job(job_id, progress_total=len(candidates))
        for i, cand in enumerate(candidates):
            if self._cancelled(job_id):
                break
            name = cand["name"]
            self.db.update_job(
                job_id, stage=f"Researching {name}",
                progress_current=i, progress_total=len(candidates),
            )

            existing = self.db.find_company_by_name(name)
            if not existing and cand.get("domain"):
                existing = self.db.find_company_by_domain(cand["domain"])
            if existing:
                results.append({"company_id": existing["id"], "name": existing["name"],
                                "status": "already_in_database", "emails_found": 0})
                continue

            enriched = self.enrichment.enrich(
                name, cand.get("website"),
                preferred_school=self.db.get_profile().get("school"),
                preferred_affiliations=self.db.get_profile().get("affiliations"),
            )
            status = discovery_scrape_status(enriched)
            trusted_site = status != "wrong_site"

            company = self.db.create_company(
                name,
                url=enriched.get("url") if trusted_site else None,
                domain=((enriched.get("domain") or cand.get("domain"))
                        if trusted_site else None),
                summary=enriched.get("summary"),
                discovery_note=cand.get("reason"),
                industry=enriched.get("industry"),
                product=enriched.get("product"),
                hook=enriched.get("hook"),
                recent_news=enriched.get("recent_news"),
                why_care=enriched.get("why_care"),
                location=enriched.get("location"),
                source="discovery",
                job_id=job_id,
                scraped_at=enriched.get("scraped_at"),
                scrape_status=status,
                research_sources=enriched.get("research_sources"),
                pages_scraped=enriched.get("pages_scraped"),
                pages_attempted=enriched.get("pages_attempted"),
                research_quality=enriched.get("research_quality"),
            )
            companies_added += 1

            emails_this_company = 0
            for candidate in select_outreach_contacts(
                    enriched.get("contacts") or [],
                    enriched.get("emails") or [], company.get("domain")):
                addr = candidate.get("email") or ""
                linkedin_url = candidate.get("linkedin_url") or ""
                if ((addr and self.db.find_contact_by_email(addr))
                        or (linkedin_url and self.db.find_contact_by_linkedin(linkedin_url))):
                    continue
                local = addr.split("@", 1)[0] if addr else ""
                self.db.create_contact(
                    company_id=company["id"],
                    name=candidate.get("name") or _guess_name_from_email(local),
                    email=addr,
                    linkedin_url=linkedin_url,
                    role=candidate.get("role") or _role_for_email(local),
                    source="discovery",
                    status="new",
                    notes=contact_notes(candidate),
                    source_url=candidate.get("source_url"),
                    evidence=candidate.get("evidence"),
                    affinity=", ".join(candidate.get("affinity") or []) or None,
                    seniority_rank=candidate.get("seniority_rank", 20),
                )
                contacts_added += 1
                emails_this_company += bool(addr)

            results.append({"company_id": company["id"], "name": name,
                            "status": status, "emails_found": emails_this_company})
            self.db.update_job(job_id, progress_current=i + 1)

        was_cancelled = self._cancelled(job_id)
        summary = {
            "query": query,
            "companies_added": companies_added,
            "contacts_added": contacts_added,
            "results": results,
        }
        self.db.finish_job(
            job_id,
            status="cancelled" if was_cancelled else "done",
            result=summary,
        )
        self.db.log_event(
            "discovery", job_id, "finished",
            f"{companies_added} companies, {contacts_added} contacts for '{query}'",
        )

    # ---------- candidate finding ----------

    def _find_candidates(self, query: str, count: int) -> List[Dict]:
        """Merge LLM-suggested + DDG-found companies, deduped, capped at count."""
        candidates: List[Dict] = []
        seen_names = set()
        seen_domains = set()

        def add(name: str, website: Optional[str] = None, reason: Optional[str] = None):
            name = (name or "").strip().strip(".")
            if not name or len(name) > 80:
                return
            key = name.lower()
            domain = registered_domain(website) if website else None
            if key in seen_names or (domain and domain in seen_domains):
                return
            if domain and domain in AGGREGATOR_DOMAINS:
                return
            seen_names.add(key)
            if domain:
                seen_domains.add(domain)
            candidates.append({"name": name, "website": website,
                               "domain": domain, "reason": reason})

        for c in self._llm_candidates(query, count):
            add(c.get("name"), c.get("website"), c.get("reason"))
            if len(candidates) >= count:
                break

        if len(candidates) < count:
            for c in self._ddg_candidates(query, count - len(candidates)):
                add(c.get("name"), c.get("website"), c.get("reason"))
                if len(candidates) >= count:
                    break

        return candidates[:count]

    def _llm_candidates(self, query: str, count: int) -> List[Dict]:
        if not complete_json or not get_cloud_llm_provider():
            return []
        prompt = f"""I'm building a targeted outreach list. Find real companies matching this search:

"{query}"

Return ONLY a JSON array of up to {count} companies. Each element:
{{"name": "Company Name", "website": "https://company.com", "reason": "one line on why it matches the search"}}

Rules:
- Only real companies you are confident actually exist.
- Prefer companies where the website is their official site (not LinkedIn/Crunchbase).
- If unsure of the website, set it to null rather than guessing.
- Match the search intent: industry, stage, location, and any other constraints mentioned."""
        data = complete_json(prompt, max_tokens=2048)
        if not isinstance(data, list):
            return []
        out = []
        for item in data:
            if isinstance(item, dict) and item.get("name"):
                website = item.get("website")
                if website and not str(website).startswith("http"):
                    website = None
                out.append({"name": str(item["name"]),
                            "website": website,
                            "reason": item.get("reason")})
        return out

    def _ddg_candidates(self, query: str, count: int) -> List[Dict]:
        """Use DDG results whose domains look like company homepages."""
        out: List[Dict] = []
        queries = [query, f"{query} companies", f"top {query} startups"]
        for q in queries:
            if len(out) >= count:
                break
            for r in ddg_text_search(q, max_results=10):
                url = r.get("href") or ""
                domain = registered_domain(url)
                if not domain or domain in AGGREGATOR_DOMAINS:
                    continue
                name = self._name_from_result(r.get("title") or "", domain)
                if name:
                    out.append({"name": name,
                                "website": f"https://{domain}",
                                "reason": (r.get("body") or "")[:160] or None})
                if len(out) >= count:
                    break
        return out

    @staticmethod
    def _name_from_result(title: str, domain: str) -> Optional[str]:
        """'Acme – AI for dentists' -> 'Acme'; fall back to domain stem."""
        for sep in (" | ", " – ", " — ", " - ", ": "):
            if sep in title:
                title = title.split(sep, 1)[0]
                break
        title = title.strip()
        if 1 < len(title) <= 40 and not re.search(
                r"\b(top|best|list|guide|companies|startups|\d{4})\b", title, re.I):
            return title
        stem = domain.split(".")[0]
        if len(stem) > 2:
            return stem.capitalize()
        return None
