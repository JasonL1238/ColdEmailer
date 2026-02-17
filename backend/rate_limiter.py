from datetime import datetime, timedelta
from typing import Dict
from collections import defaultdict
import os


class RateLimiter:
    """Rate limiting and usage monitoring"""
    
    def __init__(self):
        self.email_generations = []  # Timestamps
        self.company_researches = []  # Timestamps
        self.emails_sent = []  # Timestamps
        self.daily_limits = {
            'emails_per_day': int(os.getenv('MAX_EMAILS_PER_DAY', 50)),
            'generations_per_minute': int(os.getenv('MAX_EMAIL_GENERATIONS_PER_MINUTE', 10)),
            'researches_per_minute': int(os.getenv('MAX_COMPANY_RESEARCH_PER_MINUTE', 5)),
        }
    
    def can_generate_email(self) -> tuple[bool, str]:
        """Check if we can generate an email"""
        now = datetime.now()
        
        # Check per-minute limit
        recent = [t for t in self.email_generations if (now - t).total_seconds() < 60]
        if len(recent) >= self.daily_limits['generations_per_minute']:
            return False, f"Rate limit: Max {self.daily_limits['generations_per_minute']} generations per minute"
        
        # Check daily limit
        today = [t for t in self.email_generations if (now - t).days < 1]
        if len(today) >= self.daily_limits['emails_per_day']:
            return False, f"Daily limit: Max {self.daily_limits['emails_per_day']} emails per day"
        
        return True, ""
    
    def record_email_generation(self):
        """Record an email generation"""
        self.email_generations.append(datetime.now())
    
    def can_research_company(self) -> tuple[bool, str]:
        """Check if we can research a company"""
        now = datetime.now()
        
        # Check per-minute limit
        recent = [t for t in self.company_researches if (now - t).total_seconds() < 60]
        if len(recent) >= self.daily_limits['researches_per_minute']:
            return False, f"Rate limit: Max {self.daily_limits['researches_per_minute']} researches per minute"
        
        return True, ""
    
    def record_company_research(self):
        """Record a company research"""
        self.company_researches.append(datetime.now())
    
    def can_send_email(self) -> tuple[bool, str]:
        """Check if we can send an email"""
        now = datetime.now()
        
        # Check daily limit
        today = [t for t in self.emails_sent if (now - t).days < 1]
        if len(today) >= self.daily_limits['emails_per_day']:
            return False, f"Daily limit: Max {self.daily_limits['emails_per_day']} emails per day"
        
        return True, ""
    
    def record_email_sent(self):
        """Record an email sent"""
        self.emails_sent.append(datetime.now())
    
    def get_usage_stats(self) -> Dict:
        """Get current usage statistics"""
        now = datetime.now()
        
        emails_generated_today = len([t for t in self.email_generations if (now - t).days < 1])
        emails_sent_today = len([t for t in self.emails_sent if (now - t).days < 1])
        researches_today = len([t for t in self.company_researches if (now - t).days < 1])
        
        return {
            'emails_generated_today': emails_generated_today,
            'emails_sent_today': emails_sent_today,
            'company_researches_today': researches_today,
            'daily_limit': self.daily_limits['emails_per_day'],
            'remaining_emails': max(0, self.daily_limits['emails_per_day'] - emails_sent_today)
        }
