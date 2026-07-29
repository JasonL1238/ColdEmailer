import requests
from bs4 import BeautifulSoup
import trafilatura
from typing import Optional, Tuple
from text_cleaner import TextCleaner
import ipaddress
import socket
import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # cap fetched page size
MAX_REDIRECTS = 5


def is_safe_public_url(url: str) -> bool:
    """True only for http(s) URLs on standard ports whose host resolves
    exclusively to public IP addresses. Blocks SSRF against loopback,
    link-local (169.254.0.0/16), RFC1918, and reserved ranges."""
    if not url:
        return False
    try:
        parsed = urlparse(url)
        port = parsed.port  # raises ValueError on malformed ports
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if port not in (None, 80, 443):
        return False
    host = parsed.hostname
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(
            host, port or (443 if parsed.scheme == "https" else 80),
            proto=socket.IPPROTO_TCP)
    except (socket.gaierror, OSError, UnicodeError):
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if not ip.is_global:
            return False
    return True


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
    
    def _safe_get(self, url: str, timeout: int = 10) -> Optional[requests.Response]:
        """GET with SSRF protection: validate the URL (and every redirect hop)
        as public before fetching, and cap the response size."""
        for _ in range(MAX_REDIRECTS + 1):
            if not is_safe_public_url(url):
                return None
            response = self.session.get(url, timeout=timeout,
                                        allow_redirects=False, stream=True)
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                response.close()
                if not location:
                    return None
                url = urljoin(url, location)
                continue
            content = b""
            for chunk in response.iter_content(chunk_size=65536):
                content += chunk
                if len(content) >= MAX_RESPONSE_BYTES:
                    break
            response._content = content[:MAX_RESPONSE_BYTES]
            return response
        return None  # too many redirects

    def fetch_html(self, url: str) -> Optional[str]:
        """Fetch raw HTML for a URL (SSRF-checked + robots-checked +
        rate-limited). None on failure."""
        try:
            if not is_safe_public_url(url):
                return None
            if not self._check_robots_txt(url):
                return None
            self._rate_limit(url)
            response = self._safe_get(url, timeout=10)
            if response is None:
                return None
            response.raise_for_status()
            ctype = (response.headers.get("Content-Type") or "").lower()
            if "html" not in ctype and "<html" not in response.text[:2000].lower():
                return None
            return response.text
        except Exception:
            return None

    def extract_text(self, html: str) -> Optional[str]:
        """Extract cleaned main-content text from an HTML string."""
        if not html:
            return None
        try:
            extracted = trafilatura.extract(html)
            if extracted:
                return self.text_cleaner.clean(extracted)
            soup = BeautifulSoup(html, 'html.parser')
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            return self.text_cleaner.clean(soup.get_text())
        except Exception:
            return None

    def scrape(self, url: str) -> Tuple[Optional[str], bool]:
        """
        Scrape a URL and return cleaned text.
        Returns: (cleaned_text, success)
        """
        try:
            # Fetch HTML (SSRF-checked + robots-checked + rate-limited)
            html = self.fetch_html(url)
            if not html:
                return None, False

            # Extract main content with trafilatura
            extracted = trafilatura.extract(html)

            if extracted:
                # Clean the text
                cleaned = self.text_cleaner.clean(extracted)
                return cleaned, len(cleaned) > 100  # Success if we got substantial text

            # Fallback: try BeautifulSoup extraction
            soup = BeautifulSoup(html, 'html.parser')
            
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
