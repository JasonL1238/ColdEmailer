import requests
from bs4 import BeautifulSoup
import trafilatura
from typing import Optional, Tuple
from text_cleaner import TextCleaner
import time
from urllib.robotparser import RobotFileParser


class WebScraper:
    """Web scraper using requests + BeautifulSoup + trafilatura"""
    
    def __init__(self):
        self.text_cleaner = TextCleaner()
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self.last_request_time = {}
        self.min_delay = 2  # 2 seconds between requests to same domain
    
    def _check_robots_txt(self, url: str) -> bool:
        """Check if we're allowed to scrape this URL"""
        try:
            from urllib.parse import urljoin, urlparse
            parsed = urlparse(url)
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
            rp = RobotFileParser()
            rp.set_url(robots_url)
            rp.read()
            return rp.can_fetch(self.session.headers['User-Agent'], url)
        except:
            # If we can't check robots.txt, allow (many sites don't have one)
            return True
    
    def _rate_limit(self, url: str):
        """Rate limit requests to same domain"""
        from urllib.parse import urlparse
        domain = urlparse(url).netloc
        if domain in self.last_request_time:
            elapsed = time.time() - self.last_request_time[domain]
            if elapsed < self.min_delay:
                time.sleep(self.min_delay - elapsed)
        self.last_request_time[domain] = time.time()
    
    def scrape(self, url: str) -> Tuple[Optional[str], bool]:
        """
        Scrape a URL and return cleaned text.
        Returns: (cleaned_text, success)
        """
        try:
            # Check robots.txt
            if not self._check_robots_txt(url):
                return None, False
            
            # Rate limit
            self._rate_limit(url)
            
            # Fetch HTML
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            # Extract main content with trafilatura
            extracted = trafilatura.extract(response.text)
            
            if extracted:
                # Clean the text
                cleaned = self.text_cleaner.clean(extracted)
                return cleaned, len(cleaned) > 100  # Success if we got substantial text
            
            # Fallback: try BeautifulSoup extraction
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Remove script and style elements
            for script in soup(["script", "style", "nav", "footer", "header"]):
                script.decompose()
            
            # Get text
            text = soup.get_text()
            
            # Clean it
            cleaned = self.text_cleaner.clean(text)
            
            return cleaned, len(cleaned) > 100
            
        except Exception as e:
            print(f"Error scraping {url}: {e}")
            return None, False
    
    def get_text_length(self, url: str) -> int:
        """Get text length without full scraping (for checking if we need Playwright)"""
        text, success = self.scrape(url)
        return len(text) if text else 0
