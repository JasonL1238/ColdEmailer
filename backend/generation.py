"""
Background email-generation jobs: for each selected contact, make sure the
company is researched (scrape on demand), then compose a draft with the
selected email type + resume. Progress is reported via the jobs table.
"""
import threading
import time
from typing import Dict, List, Optional

import suppression

from db import Database
from email_composer import (EmailComposer, EMAIL_TYPES, DEFAULT_TYPE,
                            TemplateUnavailable)
from discovery import research_updates
import jobs
from enrichment import EnrichmentService
from rate_limiter import RateLimiter


class GenerationBusy(RuntimeError):
    """A generation worker is still alive, so a second run cannot start."""


class GenerationService:
    def __init__(self, db: Database, composer: EmailComposer,
                 enrichment: EnrichmentService, rate_limiter: RateLimiter):
        self.db = db
        self.composer = composer
        self.enrichment = enrichment
        self.rate_limiter = rate_limiter
        # Held for the lifetime of the worker thread, not just while the job row
        # says "running". Cancelling flips the row immediately while the thread
        # is still inside compose(), so a job-status check alone lets the next
        # run start alongside it — and both then draft the same first-contact
        # email, because neither has inserted a row for the other to see.
        self._busy = threading.Lock()

    def is_busy(self) -> bool:
        return self._busy.locked()

    def start(self, contact_ids: List[str], email_type: str = DEFAULT_TYPE,
              resume_id: Optional[str] = None,
              custom_instructions: Optional[str] = None,
              use_template_only: bool = False,
              allow_recontact: bool = False) -> Dict:
        email_type = email_type if email_type in EMAIL_TYPES else DEFAULT_TYPE
        payload = {
            "contact_ids": contact_ids,
            "email_type": email_type,
            "resume_id": resume_id,
            "custom_instructions": custom_instructions,
            "use_template_only": use_template_only,
            "allow_recontact": allow_recontact,
        }
        if not self._busy.acquire(blocking=False):
            raise GenerationBusy(
                "The last generation run is still finishing up — give it a few "
                "seconds and try again.")
        try:
            job = self.db.create_job("generation", payload)
            threading.Thread(target=self._run_safe, args=(job["id"], payload),
                             daemon=True).start()
        except Exception:
            # The worker owns the release, and it never started.
            self._busy.release()
            raise
        return job

    def cancel(self, job_id: str) -> bool:
        return jobs.cancel(self.db, job_id, "generation")

    def _cancelled(self, job_id: str) -> bool:
        return jobs.is_cancelled(self.db, job_id)

    def _skip_entry(self, contact_id: str, reason: str) -> Dict:
        contact = self.db.get_contact(contact_id) or {}
        return {"contact_id": contact_id, "name": contact.get("name"),
                "email": contact.get("email"),
                "company_name": contact.get("company_name"),
                "reason": reason}

    def _already_contacted(self, contact_id: str) -> bool:
        """True when this person has already received a real email from us
        (or has one queued), so a fresh first-contact draft would duplicate."""
        row = self.db.query_one(
            """SELECT COUNT(*) AS n FROM emails
               WHERE contact_id = ? AND is_follow_up = 0
                 AND status IN ('sent', 'draft', 'approved')""",
            (contact_id,))
        return bool(row and row["n"])

    def _wait_for_slot(self, job_id: str, seconds: float) -> bool:
        """Sleep out a per-minute window in small steps so Cancel still works.
        Returns True when the job was cancelled while waiting."""
        deadline = time.monotonic() + max(0.0, seconds)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if self._cancelled(job_id):
                return True
            time.sleep(min(0.5, remaining))
        return self._cancelled(job_id)

    def _run_safe(self, job_id: str, payload: Dict):
        try:
            self._run(job_id, payload)
        except Exception as e:
            print(f"[generation] job {job_id} crashed: {e}")
            self.db.finish_job(job_id, status="failed", error=str(e))
        finally:
            if self._busy.locked():
                self._busy.release()

    def _ensure_company_researched(self, company_id: Optional[str],
                                   allow_research: bool = True) -> Optional[Dict]:
        """Return company row, scraping it first if it has never been enriched.
        allow_research=False (template-only mode) skips the network entirely —
        the checkbox promises no API calls, so make that true end to end."""
        if not company_id:
            return None
        company = self.db.get_company(company_id)
        if not company:
            return None
        if not allow_research:
            return company
        if company.get("summary") or company.get("scrape_status") in ("scraped", "no_website"):
            return company
        can, _ = self.rate_limiter.can_research_company()
        if not can:
            return company
        self.rate_limiter.record_company_research()
        enriched = self.enrichment.enrich(
            company["name"], company.get("url"),
            preferred_school=self.db.get_profile().get("school"),
        )
        self.db.update_company(company["id"], research_updates(enriched))
        return self.db.get_company(company["id"])

    def _run(self, job_id: str, payload: Dict):
        contact_ids = payload.get("contact_ids") or []
        total = len(contact_ids)
        self.db.update_job(job_id, stage="Generating emails", progress_total=total)

        generated_ids: List[str] = []
        skipped: List[Dict] = []
        # Read once per job. Drafting for somebody on the do-not-contact list
        # burns paid quota on a message the send path will refuse, and leaves
        # a draft in the queue whose only outcome is an error.
        suppressions = self.db.list_suppressions()

        for i, contact_id in enumerate(contact_ids):
            if self._cancelled(job_id):
                break
            contact = self.db.get_contact(contact_id)
            if not contact:
                skipped.append({"contact_id": contact_id, "reason": "contact not found"})
                continue
            if not (contact.get("email") or "").strip():
                skipped.append({"contact_id": contact_id,
                                "name": contact.get("name"),
                                "reason": "contact has no email address"})
                continue
            blocked = suppression.match(contact.get("email") or "", suppressions)
            if blocked:
                skipped.append(self._skip_entry(
                    contact_id,
                    suppression.blocked_reason(blocked, contact.get("email") or "")))
                continue
            # Archiving a contact means "stop reaching out" — honour it even if
            # the row is still selected in the table.
            if contact.get("status") == "archived":
                skipped.append(self._skip_entry(
                    contact_id, "archived — unarchive them first if you want to reach out"))
                continue
            # A second first-contact email to someone already emailed reads as
            # spam to them and as a mistake to us. Follow-ups are the supported
            # way to reach back out, so send the user there instead.
            if not payload.get("allow_recontact") and self._already_contacted(contact_id):
                skipped.append(self._skip_entry(
                    contact_id,
                    "already emailed — use Draft follow-up instead of a new first email"))
                continue

            can, err = self.rate_limiter.can_generate_email()
            if not can:
                # The per-minute cap is a speed bump, not a verdict: the user
                # asked for these emails, so wait for the window to reopen
                # instead of abandoning the rest of the batch.
                wait = self.rate_limiter.generation_retry_after()
                if wait is not None:
                    self.db.update_job(
                        job_id, progress_current=i,
                        stage="Pausing — per-minute generation limit reached")
                    if self._wait_for_slot(job_id, wait):
                        break
                    can, err = self.rate_limiter.can_generate_email()
            if not can:
                # Out of quota for good. Every remaining contact is untouched,
                # so record each one — dropping the tail silently would report
                # a finished batch that never happened.
                skipped.extend(self._skip_entry(cid, err)
                               for cid in contact_ids[i:])
                break

            label = contact.get("name") or contact.get("company_name") or contact["email"]
            self.db.update_job(job_id, stage=f"Writing email for {label}",
                               progress_current=i)

            company = self._ensure_company_researched(
                contact.get("company_id"),
                allow_research=not payload.get("use_template_only"))
            try:
                composed = self.composer.compose(
                    contact, company,
                    email_type=payload.get("email_type") or DEFAULT_TYPE,
                    resume_id=payload.get("resume_id"),
                    custom_instructions=payload.get("custom_instructions"),
                    use_template_only=bool(payload.get("use_template_only")),
                )
            except TemplateUnavailable as e:
                # e.g. a custom email with no AI available: writing the plain
                # template here would ignore the instructions entirely.
                skipped.append(self._skip_entry(contact_id, str(e)))
                continue
            # compose() takes seconds, so a Cancel click almost always lands
            # inside it. Discarding the composed text here is what stops a
            # cancelled run inserting one more draft (and charging quota for it).
            if self._cancelled(job_id):
                break
            # Re-checked in the same breath as the insert: the guard above ran
            # before compose(), long enough ago for a concurrent run to have
            # drafted this person in the meantime.
            if not payload.get("allow_recontact") and self._already_contacted(contact_id):
                skipped.append(self._skip_entry(
                    contact_id,
                    "already emailed — use Draft follow-up instead of a new first email"))
                continue
            self.rate_limiter.record_email_generation()
            email = self.db.create_email(
                contact_id=contact["id"],
                company_id=contact.get("company_id"),
                email_type=payload.get("email_type") or DEFAULT_TYPE,
                resume_id=payload.get("resume_id"),
                subject=composed["subject"],
                body=composed["body"],
                status="draft",
                used_template_fallback=composed["used_template_fallback"],
                fallback_reason=composed["fallback_reason"],
                # Which model actually wrote it. The ladder substitutes
                # silently, sometimes across vendors at very different prices.
                llm_model=composed.get("llm_model"),
                custom_instructions=payload.get("custom_instructions"),
            )
            if contact.get("status") in (None, "", "new"):
                self.db.update_contact(contact["id"], {"status": "drafted"})
            self.db.log_event("email", email["id"], "generated",
                              f"{label} ({payload.get('email_type')})")
            generated_ids.append(email["id"])
            self.db.update_job(job_id, progress_current=i + 1)

        was_cancelled = self._cancelled(job_id)
        # Progress counts drafts written, nothing else. Counting skips as
        # progress filled the bar to 100% and labelled skipped contacts "done",
        # so a run that drafted nothing at all still reported "3/3 done" under
        # the title "Drafts ready". Skips are reported on their own, by name.
        self.db.update_job(job_id, progress_current=min(len(generated_ids), total),
                           stage=f"{len(generated_ids)} drafted"
                                 + (f", {len(skipped)} skipped" if skipped else ""))
        self.db.finish_job(
            job_id,
            status="cancelled" if was_cancelled else "done",
            result={"generated": len(generated_ids), "total": total,
                    "skipped_count": len(skipped),
                    "email_ids": generated_ids, "skipped": skipped},
        )
