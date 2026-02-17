import json
import os
from datetime import datetime
from typing import Optional, Dict
from models import CompanyMetadata


class CompanyCache:
    """Cache company enrichment results"""
    
    def __init__(self, cache_path: str = "data/company_cache.json"):
        self.cache_path = cache_path
        self._ensure_data_dir()
        self._cache = self._load_cache()
    
    def _ensure_data_dir(self):
        """Ensure data directory exists"""
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
    
    def _load_cache(self) -> Dict:
        """Load cache from file"""
        if not os.path.exists(self.cache_path):
            return {}
        
        try:
            with open(self.cache_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading cache: {e}")
            return {}
    
    def _save_cache(self):
        """Save cache to file"""
        try:
            with open(self.cache_path, 'w', encoding='utf-8') as f:
                json.dump(self._cache, f, indent=2, default=str)
        except Exception as e:
            print(f"Error saving cache: {e}")
    
    def _normalize_company_name(self, name: str) -> str:
        """Normalize company name for cache key"""
        return name.lower().strip()
    
    def get(self, company_name: str) -> Optional[CompanyMetadata]:
        """Get cached metadata for a company"""
        key = self._normalize_company_name(company_name)
        if key in self._cache:
            data = self._cache[key]
            return CompanyMetadata(**data)
        return None
    
    def set(self, company_name: str, metadata: CompanyMetadata):
        """Cache metadata for a company"""
        key = self._normalize_company_name(company_name)
        metadata_dict = metadata.model_dump()
        metadata_dict['cached_at'] = datetime.now().isoformat()
        self._cache[key] = metadata_dict
        self._save_cache()
    
    def has(self, company_name: str) -> bool:
        """Check if company is cached"""
        key = self._normalize_company_name(company_name)
        return key in self._cache
