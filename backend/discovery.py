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

from contact_ingest import (attach_candidate, find_existing,
                            owned_elsewhere_note, verified_channels)
from db import Database
from domain_names import registered_domain
import jobs
from ddg_search import ddg_text_search
from enrichment import (EnrichmentService, scrape_status_for,
                        select_outreach_contacts)

try:
    from llm_client import complete_json, get_cloud_llm_provider
except ImportError:
    complete_json = None
    get_cloud_llm_provider = lambda: None

MAX_COMPANIES_PER_RUN = 100

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


def discovery_scrape_status(enriched: dict) -> str:
    """The status a freshly discovered company gets.

    Deliberately built on enrichment.scrape_status_for rather than repeating
    it: discovery used to carry its own hand-rolled copy of the mapping, and
    that copy had no identity_verified case. A company found on an unrelated
    website was therefore filed as "Research failed", which reads as transient
    — so the user re-runs research and gets the same wrong site — instead of
    "Wrong site found", which tells them the domain is what needs fixing.

    "scraped" means outreach-ready: a verified person email and/or a
    name-matching LinkedIn profile. Either channel counts.
    """
    status = scrape_status_for(enriched)
    if status != "scraped":
        return status
    has_outreach = any(
        c.get("name") and not c.get("name_from_email") and (
            c.get("linkedin_verified")
            or (
                (c.get("email") or "").strip()
                and c.get("email_kind") != "generic"
                and (
                    c.get("email_verified")
                    or (
                        c.get("email_person_match")
                        and c.get("email_mx_ok") is True
                    )
                )
            )
        )
        for c in (enriched.get("contacts") or [])
    )
    if has_outreach:
        return "scraped"
    return "no_emails_found"


# Columns a scrape may write. `scraped_at` and the crawl counters record the
# attempt itself, so they survive a wrong-site verdict; the profile fields
# describe a company and do not.
RESEARCH_COLUMNS = (
    "url", "domain", "summary", "industry", "product", "hook", "recent_news",
    "why_care", "location", "scraped_at", "research_sources", "pages_scraped",
    "pages_attempted", "research_quality",
)
PROFILE_COLUMNS = (
    "url", "domain", "summary", "industry", "product", "hook", "recent_news",
    "why_care", "location",
)


def research_updates(enriched: dict) -> Dict:
    """The company columns a scrape result should write, status included.

    On a wrong-site verdict the rejected URL and any older profile are cleared
    rather than left in place: a later retry must search again instead of
    scraping the same wrong company forever, and stale research must never
    reach a draft.
    """
    updates = {k: v for k, v in enriched.items()
               if k in RESEARCH_COLUMNS and v is not None}
    updates["scrape_status"] = discovery_scrape_status(enriched)
    if updates["scrape_status"] == "wrong_site":
        updates.update({key: None for key in PROFILE_COLUMNS})
    return updates


class DiscoveryService:
    def __init__(self, db: Database, enrichment: EnrichmentService):
        self.db = db
        self.enrichment = enrichment

    # ---------- public API ----------

    def start(self, query: str, count: int = 10, mode: str = "full",
              campaign_id: Optional[str] = None) -> Dict:
        count = max(1, min(int(count or 10), MAX_COMPANIES_PER_RUN))
        mode = "fast" if (mode or "full").lower() == "fast" else "full"
        job = self.db.create_job(
            "discovery", {"query": query, "count": count, "mode": mode})
        # A campaign per run, named from the search that produced it. Anchored
        # to something that happened rather than to a label the user has to
        # remember to apply — an opt-in tag would be blank on exactly the runs
        # worth comparing later.
        if campaign_id and not self.db.get_campaign(campaign_id):
            campaign_id = None
        if not campaign_id:
            campaign_id = self.db.create_campaign(
                query, query=query, job_id=job["id"])["id"]
        thread = threading.Thread(
            target=self._run_safe, args=(job["id"], query, count, mode, campaign_id),
            daemon=True,
        )
        thread.start()
        return {**job, "campaign_id": campaign_id}

    def cancel(self, job_id: str) -> bool:
        return jobs.cancel(self.db, job_id, "discovery")

    # ---------- pipeline ----------

    def _cancelled(self, job_id: str) -> bool:
        return jobs.is_cancelled(self.db, job_id)

    def _run_safe(self, job_id: str, query: str, count: int, mode: str = "full",
                  campaign_id: Optional[str] = None):
        try:
            self._run(job_id, query, count, mode=mode, campaign_id=campaign_id)
        except Exception as e:
            print(f"[discovery] job {job_id} crashed: {e}")
            self.db.finish_job(job_id, status="failed", error=str(e))

    def _run(self, job_id: str, query: str, count: int, mode: str = "full",
             campaign_id: Optional[str] = None):
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
                mode=mode,
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
                campaign_id=campaign_id,
                scraped_at=enriched.get("scraped_at"),
                scrape_status=status,
                research_sources=enriched.get("research_sources"),
                pages_scraped=enriched.get("pages_scraped"),
                pages_attempted=enriched.get("pages_attempted"),
                research_quality=enriched.get("research_quality"),
            )
            companies_added += 1

            emails_this_company = 0
            contacts_this_company = 0
            contact_conflicts = []
            for candidate in select_outreach_contacts(
                    enriched.get("contacts") or [],
                    enriched.get("emails") or [],
                    enriched.get("mail_domain") or company.get("domain"),
                    limit=3, person_only=True):
                addr, linkedin_url = verified_channels(candidate)
                if not addr and not linkedin_url:
                    continue
                existing = find_existing(self.db, addr, linkedin_url)
                if existing:
                    # Discovery only ever adds. Enriching a row it did not
                    # create belongs to re-research, which knows whether the
                    # person has already been emailed.
                    if existing.get("company_id") != company["id"]:
                        detail = owned_elsewhere_note(
                            self.db, existing.get("company_id"),
                            addr or linkedin_url, name)
                        contact_conflicts.append(detail)
                        self.db.log_event(
                            "company", company["id"], "contact_conflict", detail)
                    continue
                attached = attach_candidate(
                    self.db, company={"id": company["id"], "name": name},
                    candidate=candidate, email=addr, linkedin_url=linkedin_url,
                    source="discovery", campaign_id=campaign_id)
                contact_conflicts.extend(attached.conflicts)
                if attached.email_landed:
                    emails_this_company += 1
                if attached.is_new_outreach:
                    contacts_this_company += 1
                    contacts_added += 1

            if contact_conflicts:
                self.db.update_company(company["id"], {
                    "scrape_warnings": contact_conflicts,
                })
            else:
                self.db.update_company(company["id"], {"scrape_warnings": []})

            # Conflicts can wipe every outreach contact — don't leave "scraped"
            # with nobody to message.
            if status == "scraped" and contacts_this_company == 0:
                status = "no_emails_found"
                self.db.update_company(company["id"], {"scrape_status": status})

            results.append({"company_id": company["id"], "name": name,
                            "status": status, "emails_found": emails_this_company,
                            "contact_conflicts": contact_conflicts})
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
