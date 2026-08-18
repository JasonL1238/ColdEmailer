import pytest
from text_cleaner import TextCleaner


class TestTextCleaner:
    """Test TextCleaner with edge cases and various inputs"""
    
    @pytest.fixture
    def cleaner(self):
        """Create TextCleaner instance"""
        return TextCleaner()
    
    def test_clean_empty_string(self, cleaner):
        """Test cleaning empty string"""
        result = cleaner.clean("")
        assert result == ""
    
    def test_clean_none(self, cleaner):
        """Test cleaning None"""
        result = cleaner.clean(None)
        assert result == ""
    
    def test_clean_whitespace_only(self, cleaner):
        """Test cleaning whitespace-only string"""
        result = cleaner.clean("   \n\t  \n  ")
        assert result == ""
    
    def test_clean_removes_extra_whitespace(self, cleaner):
        """Test that extra whitespace is removed"""
        text = "This   has    multiple    spaces"
        result = cleaner.clean(text)
        assert "   " not in result
        assert "    " not in result
    
    def test_clean_removes_short_lines(self, cleaner):
        """Lines under MIN_LINE_LENGTH are labels and nav crumbs, not prose."""
        text = "Pricing\nDocs v2\nAcme builds industrial robots for warehouse fulfilment centres."
        result = cleaner.clean(text)
        assert "Acme builds industrial robots" in result
        assert "Pricing" not in result
        assert "Docs v2" not in result

    def test_one_nav_line_does_not_discard_the_whole_page(self, cleaner):
        """The filter is per line. Collapsing every whitespace character first
        (\\s+ instead of [^\\S\\n]+) merged the page into one line, so a single
        'Careers' anywhere threw the entire page away — about one real company
        site in five, each surfacing to the user as a scrape failure."""
        text = ("Acme builds industrial robots for warehouse fulfilment centres in Europe.\n"
                "Careers\n"
                "Their flagship system moves twelve thousand totes an hour.")
        result = cleaner.clean(text)
        assert "Careers" not in result
        assert "Acme builds industrial robots" in result
        assert "twelve thousand totes" in result


    def test_clean_removes_nav_words(self, cleaner):
        """Test that navigation words are removed"""
        # First line has no nav words, second line has nav words
        text = "Product features are great and innovative with many capabilities that customers love using daily\nSubscribe to newsletter"
        result = cleaner.clean(text)
        # Lines with nav words should be removed
        assert "subscribe" not in result.lower()
        assert "newsletter" not in result.lower()
        # ...and the content line must survive. Guarding this behind
        # `if len(result) > 0` made it unfalsifiable: an empty result satisfies
        # every "not in" assertion above, which is how a cleaner that discarded
        # whole pages passed this file for so long.
        assert "Product features are great" in result
    
    def test_clean_removes_cookie_policy(self, cleaner):
        """Test that cookie policy text is removed"""
        # Test with single line that has nav words vs one without
        # Put valid line first to ensure it's processed
        text = "Company does amazing things with technology and innovation with clients worldwide\nAccept cookies to continue browsing website"
        result = cleaner.clean(text)
        # Lines with cookie-related nav words should be removed
        assert "Accept cookies" not in result
        assert "browsing" not in result
        # ...without taking the real content with them.
        assert "amazing things with technology" in result
    
    def test_clean_removes_repetition(self, cleaner):
        """Test that repeated sentences are removed"""
        text = "This is important. This is important. This is important. New information here."
        result = cleaner.clean(text)
        # Should have fewer occurrences of "This is important"
        assert result.count("This is important") <= 1
    
    def test_clean_limits_length(self, cleaner):
        """Test that text is truncated to MAX_TEXT_LENGTH"""
        # Create text longer than MAX_TEXT_LENGTH (3000)
        long_text = "A" * 5000
        result = cleaner.clean(long_text)
        assert len(result) <= 3003  # 3000 + "..."
        if len(result) > 3000:
            assert result.endswith("...")
    
    def test_clean_preserves_valid_content(self, cleaner):
        """Test that valid content is preserved"""
        text = "Our company provides innovative solutions for businesses. We help companies grow and succeed."
        result = cleaner.clean(text)
        assert "innovative solutions" in result
        assert "companies grow" in result
    
    def test_clean_handles_special_characters(self, cleaner):
        """Test cleaning text with special characters"""
        # Single long line with special chars (no nav words, no "contact us" which is a nav word)
        text = "Price: $99.99! Reach out at info@company.com or call (555) 123-4567 for more information about our products and services"
        result = cleaner.clean(text)
        # Text should be preserved if it's long enough and has no nav words
        assert len(result) > 0
        # Should contain some of the original content
        assert "99.99" in result or "company.com" in result or "123-4567" in result or "products" in result or "services" in result
    
    def test_clean_removes_mostly_punctuation_lines(self, cleaner):
        """Test that lines with mostly punctuation are removed"""
        text = ("Acme builds industrial robots for fulfilment centres.\n"
                "!!! *** ??? --- +++ ///\n"
                "They deploy across twelve warehouses in Europe.")
        result = cleaner.clean(text)
        assert "!!!" not in result
        assert "***" not in result
        assert "Acme builds industrial robots" in result
        assert "twelve warehouses" in result
    
    def test_clean_handles_newlines(self, cleaner):
        """Test that newlines are handled correctly"""
        text = "Line one\n\nLine two\n\n\nLine three"
        result = cleaner.clean(text)
        # Should be joined with spaces, no double spaces from empty lines
        assert "\n\n" not in result
    
    def test_clean_very_large_input(self, cleaner):
        """Test cleaning very large input"""
        # 100KB of text
        large_text = "This is a test sentence. " * 5000
        result = cleaner.clean(large_text)
        assert len(result) <= 3003
        assert isinstance(result, str)
    
    def test_clean_unicode_characters(self, cleaner):
        """Test cleaning text with unicode characters"""
        text = "Café résumé naïve 中文 🚀"
        result = cleaner.clean(text)
        assert "Café" in result or "résumé" in result or "中文" in result
    
    def test_clean_empty_lines_removed(self, cleaner):
        """Test that empty lines are removed"""
        text = "First line\n\n\nSecond line\n\nThird line"
        result = cleaner.clean(text)
        # Should not have excessive whitespace from empty lines
        assert result.count("  ") < 3
    
    def test_clean_multiple_nav_words_in_line(self, cleaner):
        """Test line with multiple nav words is removed"""
        # Second line contains "follow" which is a nav word, so it gets removed too
        # Use a line without any nav words
        text = "Subscribe to newsletter and follow on social media platforms\nContent provided here has enough length to pass minimum threshold and provides useful information"
        result = cleaner.clean(text)
        # Line with nav words should be removed
        assert "newsletter" not in result.lower()
        assert "social media" not in result.lower()
        assert "follow" not in result.lower()
        # Valid content without nav words should be preserved
        # Note: "follow" is in nav words, so second line might also be removed
        # Test that cleaner removes lines with nav words
        assert "Subscribe" not in result
    
    def test_clean_case_insensitive_nav_words(self, cleaner):
        """Test nav word detection is case insensitive"""
        # First line has no nav words, others do - put valid line first
        text = "Content provided here has sufficient length to pass filters and provides value to users\nSUBSCRIBE NOW and newsletter signup\nFollow Us on Twitter"
        result = cleaner.clean(text)
        # Nav words should be removed (case insensitive)
        assert "subscribe" not in result.lower()
        assert "follow us" not in result.lower()
        assert "twitter" not in result.lower()
        # ...and the one line with no nav words is kept.
        assert "Content provided here has sufficient length" in result
