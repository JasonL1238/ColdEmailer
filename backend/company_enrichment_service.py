from typing import Optional, Dict
from models import CompanyMetadata
from web_scraper import WebScraper
from metadata_extractor import MetadataExtractor
from company_cache import CompanyCache
from datetime import datetime
import os
import re

try:
    from duckduckgo_search import DDGS
    HAS_DDGS = True
except ImportError:
    HAS_DDGS = False


class CompanyEnrichmentService:
    """Company signal extractor service"""
    
    def __init__(self, cache_path: str = "data/company_cache.json"):
        self.scraper = WebScraper()
        self.extractor = MetadataExtractor()
        self.cache = CompanyCache(cache_path)
    
    def _is_empty_metadata(self, meta: CompanyMetadata) -> bool:
        """True when all content fields are None/empty (URL-only doesn't count)."""
        return not any([
            meta.summary,
            meta.industry,
            meta.product,
            meta.why_engineers_care,
            meta.hook_sentence,
        ])
    
    def _guess_urls(self, company_name: str) -> list[str]:
        """Generate candidate URLs from company name."""
        slug = re.sub(r'[^a-z0-9]+', '', company_name.lower())
        slug_dash = re.sub(r'[^a-z0-9]+', '-', company_name.lower()).strip('-')
        candidates = [
            f"https://www.{slug}.com",
            f"https://{slug}.com",
            f"https://{slug}.io",
            f"https://{slug}.co",
            f"https://www.{slug_dash}.com",
            f"https://{slug_dash}.com",
            f"https://{slug_dash}.io",
        ]
        seen = set()
        deduped = []
        for u in candidates:
            if u not in seen:
                seen.add(u)
                deduped.append(u)
        return deduped

    def find_website(self, company_name: str) -> Optional[str]:
        """
        Find company website from company name.
        Tries DuckDuckGo first, then falls back to URL guessing + HTTP probe.
        """
        skip_domains = [
            'linkedin.com', 'facebook.com', 'twitter.com', 'x.com',
            'indeed.com', 'glassdoor.com', 'crunchbase.com',
            'wikipedia.org', 'bloomberg.com', 'yelp.com',
        ]

        # Strategy 1: DuckDuckGo search
        if HAS_DDGS:
            try:
                with DDGS() as ddgs:
                    query = f"{company_name} company official website"
                    results = list(ddgs.text(query, max_results=5))
                    
                    for result in results:
                        url = result.get('href', '')
                        if any(skip in url.lower() for skip in skip_domains):
                            continue
                        if '.com' in url or '.io' in url or '.co' in url or '.org' in url:
                            return url
                    
                    if results:
                        return results[0].get('href')
            except Exception as e:
                print(f"DuckDuckGo search failed for {company_name}: {e}")

        # Strategy 2: Guess URLs and probe with HTTP HEAD
        for url in self._guess_urls(company_name):
            try:
                resp = self.scraper.session.head(url, timeout=5, allow_redirects=True)
                if resp.status_code < 400:
                    return str(resp.url)
            except Exception:
                continue

        return None
    
    def enrich_company(self, company_name: str, url: Optional[str] = None) -> CompanyMetadata:
        """
        Full company enrichment pipeline.
        Returns CompanyMetadata with all extracted information.
        """
        cached = self.cache.get(company_name)
        if cached and not self._is_empty_metadata(cached):
            return cached

        if not url:
            url = self.find_website(company_name)
        
        metadata = CompanyMetadata(
            company_name=company_name,
            url=url,
            cached_at=datetime.now()
        )
        
        if not url:
            self.cache.set(company_name, metadata)
            return metadata
        
        cleaned_text, success = self.scraper.scrape(url)
        
        if not success or not cleaned_text or len(cleaned_text) < 100:
            # Try scraping common sub-pages for more content
            for path in ['/about', '/about-us', '/company']:
                try:
                    sub_url = url.rstrip('/') + path
                    sub_text, sub_ok = self.scraper.scrape(sub_url)
                    if sub_ok and sub_text and len(sub_text) >= 100:
                        cleaned_text = sub_text
                        success = True
                        break
                except Exception:
                    continue

        if not success or not cleaned_text or len(cleaned_text) < 50:
            self.cache.set(company_name, metadata)
            return metadata
        
        extracted = self.extractor.extract(company_name, cleaned_text)
        
        metadata.summary = extracted.get('summary')
        metadata.industry = extracted.get('industry')
        metadata.product = extracted.get('product')
        metadata.why_engineers_care = extracted.get('why_engineers_care')
        metadata.hook_sentence = extracted.get('hook_sentence')
        metadata.confidence_score = extracted.get('confidence_score', 0.0)
        
        self.cache.set(company_name, metadata)
        
        return metadata
