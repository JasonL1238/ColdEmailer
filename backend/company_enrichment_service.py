from typing import Optional, Dict
from models import CompanyMetadata
from web_scraper import WebScraper
from metadata_extractor import MetadataExtractor
from company_cache import CompanyCache
from datetime import datetime
import os
from duckduckgo_search import DDGS


class CompanyEnrichmentService:
    """Company signal extractor service"""
    
    def __init__(self, cache_path: str = "data/company_cache.json"):
        self.scraper = WebScraper()
        self.extractor = MetadataExtractor()
        self.cache = CompanyCache(cache_path)
    
    def find_website(self, company_name: str) -> Optional[str]:
        """
        Find company website from company name.
        Don't use email domain - search for the actual homepage.
        """
        try:
            # Use DuckDuckGo search (free, no API key needed)
            with DDGS() as ddgs:
                query = f"{company_name} company website"
                results = list(ddgs.text(query, max_results=3))
                
                for result in results:
                    url = result.get('href', '')
                    # Filter out social media, job boards, etc.
                    if any(skip in url.lower() for skip in ['linkedin.com', 'facebook.com', 'twitter.com', 
                                                           'indeed.com', 'glassdoor.com', 'crunchbase.com']):
                        continue
                    # Prefer .com domains
                    if '.com' in url or '.io' in url or '.co' in url:
                        return url
                
                # If no good result, return first result
                if results:
                    return results[0].get('href')
        except Exception as e:
            print(f"Error finding website for {company_name}: {e}")
        
        return None
    
    def enrich_company(self, company_name: str, url: Optional[str] = None) -> CompanyMetadata:
        """
        Full company enrichment pipeline.
        Returns CompanyMetadata with all extracted information.
        """
        # Check cache first
        cached = self.cache.get(company_name)
        if cached:
            return cached
        
        # Find website if not provided
        if not url:
            url = self.find_website(company_name)
        
        # Initialize metadata
        metadata = CompanyMetadata(
            company_name=company_name,
            url=url,
            cached_at=datetime.now()
        )
        
        if not url:
            # No URL found, cache empty metadata
            self.cache.set(company_name, metadata)
            return metadata
        
        # Scrape website
        cleaned_text, success = self.scraper.scrape(url)
        
        if not success or not cleaned_text or len(cleaned_text) < 100:
            # Scraping failed or got too little text
            # Could try Playwright fallback here, but for now just cache what we have
            self.cache.set(company_name, metadata)
            return metadata
        
        # Extract structured metadata with LLM
        extracted = self.extractor.extract(company_name, cleaned_text)
        
        # Update metadata with extracted info
        metadata.summary = extracted.get('summary')
        metadata.industry = extracted.get('industry')
        metadata.product = extracted.get('product')
        metadata.why_engineers_care = extracted.get('why_engineers_care')
        metadata.hook_sentence = extracted.get('hook_sentence')
        metadata.confidence_score = extracted.get('confidence_score', 0.0)
        
        # Cache the result
        self.cache.set(company_name, metadata)
        
        return metadata
