import json
from typing import Dict, Optional
import ollama


class MetadataExtractor:
    """Extract structured company metadata from cleaned text using LLM"""
    
    def __init__(self, model: str = "llama3.2", base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url
        self.client = ollama.Client(host=base_url)
    
    def extract(self, company_name: str, cleaned_text: str) -> Dict[str, Optional[str]]:
        """
        Extract structured metadata from cleaned company text.
        Returns: {summary, industry, product, why_engineers_care, hook_sentence, confidence_score}
        """
        if not cleaned_text or len(cleaned_text) < 50:
            return self._empty_metadata()
        
        prompt = f"""Analyze the following company information and extract structured metadata.

Company Name: {company_name}

Company Information:
{cleaned_text[:2000]}

Extract the following information in JSON format:
{{
  "summary": "Brief 1-2 sentence description of what the company does",
  "industry": "Industry or sector (e.g., 'AI/ML', 'SaaS', 'Fintech')",
  "product": "What product or service they build",
  "why_engineers_care": "Why an engineer/developer would be interested in this company",
  "hook_sentence": "A compelling one-liner that could be used to personalize an email (e.g., 'turning casual phone capture into production-quality scenes')",
  "confidence_score": 0.0-1.0 based on how clear the information is
}}

Return ONLY valid JSON, no other text."""

        try:
            response = self.client.generate(
                model=self.model,
                prompt=prompt,
                options={
                    "temperature": 0.3,  # Lower temperature for more consistent extraction
                }
            )
            
            # Extract JSON from response
            # Ollama response structure: {'model': ..., 'response': '...', 'done': ...}
            response_text = response.get('response', '').strip()
            
            # Try to find JSON in the response
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            
            if json_start >= 0 and json_end > json_start:
                json_str = response_text[json_start:json_end]
                metadata = json.loads(json_str)
                
                # Validate and clean metadata
                return {
                    'summary': metadata.get('summary', '').strip(),
                    'industry': metadata.get('industry', '').strip(),
                    'product': metadata.get('product', '').strip(),
                    'why_engineers_care': metadata.get('why_engineers_care', '').strip(),
                    'hook_sentence': metadata.get('hook_sentence', '').strip(),
                    'confidence_score': float(metadata.get('confidence_score', 0.5))
                }
            else:
                return self._empty_metadata()
                
        except Exception as e:
            print(f"Error extracting metadata: {e}")
            return self._empty_metadata()
    
    def _empty_metadata(self) -> Dict[str, Optional[str]]:
        """Return empty metadata structure"""
        return {
            'summary': None,
            'industry': None,
            'product': None,
            'why_engineers_care': None,
            'hook_sentence': None,
            'confidence_score': 0.0
        }
