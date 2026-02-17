import re
from typing import List


class TextCleaner:
    """Clean extracted text by removing navigation, footer, and other noise"""
    
    # Common navigation/footer words to remove
    NAV_WORDS = [
        'subscribe', 'book a demo', 'careers', 'cookie policy', 'privacy policy',
        'terms of service', 'sign up', 'log in', 'menu', 'navigation', 'skip to',
        'cookie consent', 'accept cookies', 'reject cookies', 'learn more',
        'get started', 'try free', 'contact us', 'about us', 'our team',
        'follow us', 'social media', 'linkedin', 'twitter', 'facebook',
        'instagram', 'youtube', 'newsletter', 'email updates'
    ]
    
    # Short line threshold (lines shorter than this are likely noise)
    MIN_LINE_LENGTH = 20
    
    # Maximum text length to keep
    MAX_TEXT_LENGTH = 3000
    
    def clean(self, text: str) -> str:
        """Clean extracted text"""
        if not text:
            return ""
        
        # Remove extra whitespace
        text = re.sub(r'\s+', ' ', text)
        
        # Split into lines
        lines = text.split('\n')
        
        # Filter lines
        cleaned_lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Skip short lines
            if len(line) < self.MIN_LINE_LENGTH:
                continue
            
            # Skip lines with nav words
            line_lower = line.lower()
            if any(nav_word in line_lower for nav_word in self.NAV_WORDS):
                continue
            
            # Skip lines that are mostly punctuation or special chars
            if len(re.sub(r'[^\w\s]', '', line)) < len(line) * 0.3:
                continue
            
            cleaned_lines.append(line)
        
        # Join and remove repetition
        cleaned_text = ' '.join(cleaned_lines)
        
        # Remove repeated phrases (simple approach)
        cleaned_text = self._remove_repetition(cleaned_text)
        
        # Limit length
        if len(cleaned_text) > self.MAX_TEXT_LENGTH:
            cleaned_text = cleaned_text[:self.MAX_TEXT_LENGTH] + "..."
        
        return cleaned_text.strip()
    
    def _remove_repetition(self, text: str) -> str:
        """Remove obvious repetition"""
        # Split into sentences
        sentences = re.split(r'[.!?]\s+', text)
        
        # Remove duplicate sentences
        seen = set()
        unique_sentences = []
        for sentence in sentences:
            sentence_lower = sentence.lower().strip()
            if sentence_lower and sentence_lower not in seen:
                seen.add(sentence_lower)
                unique_sentences.append(sentence)
        
        return '. '.join(unique_sentences)
