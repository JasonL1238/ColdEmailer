from datetime import datetime, timedelta
from typing import Dict, Optional
import os


class RateLimiter:
    """Send/generation/research caps.

    Burst (per-minute) limits are in-memory: they only need to survive the
    current process. Daily send and generation counts are read from the
    database when one is available, because those caps exist to stop you
    mailing 500 strangers in a day — a limit that resets on every dev-server
    reload is not a limit at all.
    """

    def __init__(self, db=None):
        self.db = db
        self.email_generations = []   # timestamps (burst window)
        self.company_researches = []  # timestamps (burst window)
        self.emails_sent = []         # timestamps (fallback when no db)
        self.daily_limits = {
            'emails_per_day': int(os.getenv('MAX_EMAILS_PER_DAY', 50)),
            'generations_per_day': int(os.getenv('MAX_EMAIL_GENERATIONS_PER_DAY', 500)),
            'generations_per_minute': int(os.getenv('MAX_EMAIL_GENERATIONS_PER_MINUTE', 10)),
            'researches_per_minute': int(os.getenv('MAX_COMPANY_RESEARCH_PER_MINUTE', 5)),
        }

    # ---------- persistent daily counts ----------

    @staticmethod
    def _day_start() -> str:
        return datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")

    def _db_count(self, sql: str, extra_params: tuple = ()) -> Optional[int]:
        """Run a COUNT query whose final bound parameter is today's midnight."""
        if not self.db:
            return None
        try:
            row = self.db.query_one(sql, (*extra_params, self._day_start()))
            return int(row["n"]) if row else 0
        except Exception:
            return None

    def sent_today(self) -> int:
        count = self._db_count(
            "SELECT COUNT(*) AS n FROM emails WHERE status='sent' AND sent_at >= ?")
        if count is not None:
            return count
        now = datetime.now()
        return len([t for t in self.emails_sent if (now - t).days < 1])

    # Every event that represents one trip to the LLM.
    _GENERATION_EVENTS = ("generated", "regenerated", "follow_up_generated")

    def generated_today(self) -> int:
        """Count generation *events*, not surviving email rows.

        Counting rows undercharged every Rewrite (which updates a row instead
        of inserting one) and refunded quota whenever a draft was deleted —
        so the daily cap could be walked past indefinitely by deleting drafts.
        """
        placeholders = ", ".join("?" * len(self._GENERATION_EVENTS))
        count = self._db_count(
            f"SELECT COUNT(*) AS n FROM events "
            f"WHERE event IN ({placeholders}) AND created_at >= ?",
            extra_params=self._GENERATION_EVENTS)
        if count is not None:
            return count
        now = datetime.now()
        return len([t for t in self.email_generations if (now - t).days < 1])

    # ---------- checks ----------

    def can_generate_email(self) -> tuple[bool, str]:
        now = datetime.now()
        recent = [t for t in self.email_generations if (now - t).total_seconds() < 60]
        if len(recent) >= self.daily_limits['generations_per_minute']:
            return False, (f"Rate limit: max {self.daily_limits['generations_per_minute']} "
                           f"generations per minute. Try again in a moment.")
        if self.generated_today() >= self.daily_limits['generations_per_day']:
            return False, (f"Daily limit reached: {self.daily_limits['generations_per_day']} "
                           f"email generations per day.")
        return True, ""

    def generation_retry_after(self) -> Optional[float]:
        """Seconds until the per-minute generation window reopens.

        Returns None when waiting cannot help (the daily cap is what is
        blocking, or nothing is blocking at all). Callers running a batch the
        user explicitly asked for can wait out a burst limit instead of
        abandoning the rest of the queue.
        """
        if self.generated_today() >= self.daily_limits['generations_per_day']:
            return None
        now = datetime.now()
        recent = sorted(t for t in self.email_generations
                        if (now - t).total_seconds() < 60)
        if len(recent) < self.daily_limits['generations_per_minute']:
            return None
        return max(0.0, 60 - (now - recent[0]).total_seconds()) + 0.05

    def record_email_generation(self):
        self.email_generations.append(datetime.now())

    def can_research_company(self) -> tuple[bool, str]:
        now = datetime.now()
        recent = [t for t in self.company_researches if (now - t).total_seconds() < 60]
        if len(recent) >= self.daily_limits['researches_per_minute']:
            return False, (f"Rate limit: max {self.daily_limits['researches_per_minute']} "
                           f"company researches per minute. Try again in a moment.")
        return True, ""

    def record_company_research(self):
        self.company_researches.append(datetime.now())

    def can_send_email(self) -> tuple[bool, str]:
        if self.sent_today() >= self.daily_limits['emails_per_day']:
            return False, (f"Daily limit reached: {self.daily_limits['emails_per_day']} "
                           f"emails per day. This protects your sending reputation.")
        return True, ""

    def record_email_sent(self):
        self.emails_sent.append(datetime.now())

    def get_usage_stats(self) -> Dict:
        now = datetime.now()
        generated = self.generated_today()
        sent = self.sent_today()
        researches = len([t for t in self.company_researches if (now - t).days < 1])
        per_minute = self.daily_limits['generations_per_minute']
        recent = len([t for t in self.email_generations if (now - t).total_seconds() < 60])

        return {
            'emails_generated_today': generated,
            'emails_sent_today': sent,
            'company_researches_today': researches,
            'daily_limit': self.daily_limits['emails_per_day'],
            'remaining_emails': max(0, self.daily_limits['emails_per_day'] - sent),
            'generations_daily_limit': self.daily_limits['generations_per_day'],
            'remaining_generations': max(0, self.daily_limits['generations_per_day'] - generated),
            'generations_per_minute': per_minute,
            'remaining_generations_this_minute': max(0, per_minute - recent),
        }
