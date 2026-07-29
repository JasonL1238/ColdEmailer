"""Tests for email composition: parsing LLM output and template fallbacks."""
import os
import tempfile

import pytest

from db import Database
from email_composer import (EMAIL_TYPES, EmailComposer, TemplateUnavailable,
                            _parse_subject_body)
from resume_service import ResumeService


@pytest.fixture
def composer():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "test.db"))
        db.update_profile({
            "full_name": "Ada Lovelace",
            "school": "Cambridge",
            "email": "ada@cam.ac.uk",
            "background": "I built the first algorithm for the Analytical Engine",
        })
        yield EmailComposer(db, ResumeService(db))


class TestParseSubjectBody:
    def test_parses_standard_format(self):
        parsed = _parse_subject_body(
            "Subject: Hello there\nBody:\nHi Jane,\n\nThis is the email body text here.")
        assert parsed["subject"] == "Hello there"
        assert parsed["body"].startswith("Hi Jane,")

    def test_strips_quotes_from_subject(self):
        parsed = _parse_subject_body(
            'Subject: "Quoted subject"\nBody:\nHi Jane, this is a sufficiently long body.')
        assert parsed["subject"] == "Quoted subject"

    def test_rejects_output_with_no_body(self):
        assert _parse_subject_body("Subject: Only a subject line") is None

    def test_rejects_body_that_is_too_short(self):
        assert _parse_subject_body("Subject: Hi\nBody:\nshort") is None

    def test_removes_subject_line_leaked_into_body(self):
        parsed = _parse_subject_body(
            "Subject: Real subject\nBody:\nSubject: Real subject\n\nHi Jane, the actual body goes here.")
        assert not parsed["body"].lower().startswith("subject:")

    def test_handles_empty_input(self):
        assert _parse_subject_body("") is None
        assert _parse_subject_body(None) is None


class TestTemplateFallback:
    """No LLM configured -> every type still produces a sendable email."""

    @pytest.mark.parametrize("email_type", ["application", "coffee_chat", "sales"])
    def test_every_type_produces_subject_and_body(self, composer, email_type):
        contact = {"name": "Jane Smith", "company_name": "Acme", "email": "jane@acme.com"}
        company = {"name": "Acme", "summary": "Acme builds widgets"}

        result = composer.compose(contact, company, email_type=email_type,
                                  use_template_only=True)
        assert result["subject"]
        assert len(result["body"]) > 80
        assert result["used_template_fallback"] is True
        assert result["fallback_reason"] == "user_requested"

    def test_uses_recipient_first_name_in_greeting(self, composer):
        contact = {"name": "Jane Smith", "company_name": "Acme"}
        result = composer.compose(contact, None, use_template_only=True)
        assert result["body"].startswith("Hi Jane,")

    def test_falls_back_to_generic_greeting_without_a_name(self, composer):
        result = composer.compose({"company_name": "Acme"}, None, use_template_only=True)
        assert result["body"].startswith("Hi there,")

    def test_appends_signature_from_profile(self, composer):
        result = composer.compose({"name": "Jane", "company_name": "Acme"}, None,
                                  use_template_only=True)
        assert "Ada Lovelace" in result["body"]
        assert "Cambridge" in result["body"]

    def test_unknown_type_falls_back_to_application(self, composer):
        result = composer.compose({"name": "Jane", "company_name": "Acme"}, None,
                                  email_type="nonsense", use_template_only=True)
        assert "Internship inquiry" in result["subject"]

    def test_custom_type_refuses_the_template_instead_of_ignoring_instructions(self, composer):
        """The template has no branch for custom, so it used to emit the
        internship email verbatim — a ready-to-send draft that contradicts
        the instructions the app forces the user to write."""
        with pytest.raises(TemplateUnavailable) as exc:
            composer.compose({"name": "Jane", "company_name": "Acme"}, None,
                             email_type="custom",
                             custom_instructions="Ask about their summer research "
                                                 "program. Do not mention internships.",
                             use_template_only=True)
        assert "Custom emails need AI" in str(exc.value)

    def test_custom_type_refuses_when_no_llm_is_configured(self, composer, monkeypatch):
        # No provider => the LLM branch is skipped entirely (and no network call).
        monkeypatch.setattr("email_composer.get_cloud_llm_provider", lambda: None)
        with pytest.raises(TemplateUnavailable):
            composer.compose({"name": "Jane", "company_name": "Acme"}, None,
                             email_type="custom", custom_instructions="Be brief.")

    def test_background_with_a_self_intro_is_not_doubled(self, composer):
        """A background opening "I'm a student at X" used to stack onto the
        template's own intro: "I'm Ada Lovelace, a student at Cambridge. I'm a
        student at Cambridge studying…"."""
        composer.db.update_profile({
            "background": "I'm a student at Cambridge studying maths, and I built "
                          "the first algorithm for the Analytical Engine."})
        body = composer.compose({"name": "Jane", "company_name": "Acme"}, None,
                                use_template_only=True)["body"]
        assert "I'm Ada Lovelace. I'm a student at Cambridge" in body
        assert "Ada Lovelace, a student at Cambridge. I'm a student" not in body

    def test_background_without_a_self_intro_keeps_the_full_lead_in(self, composer):
        composer.db.update_profile({
            "background": "Built computer-vision pipelines for two research labs."})
        body = composer.compose({"name": "Jane", "company_name": "Acme"}, None,
                                use_template_only=True)["body"]
        assert "I'm Ada Lovelace, a student at Cambridge." in body

    def test_no_name_still_produces_a_grammatical_intro(self, composer):
        composer.db.set_setting("profile", {})
        composer.db.update_profile({"background": "Built CV pipelines for two labs."})
        body = composer.compose({"name": "Jane", "company_name": "Acme"}, None,
                                use_template_only=True)["body"]
        assert "I'm I." not in body
        assert "I'm reaching out on my own behalf." in body

    def test_company_name_appears_in_subject(self, composer):
        result = composer.compose({"name": "Jane", "company_name": "Acme"}, None,
                                  use_template_only=True)
        assert "Acme" in result["subject"]


class TestPromptBuilding:
    def test_sales_prompt_excludes_resume_text(self, composer):
        prompt = composer._build_prompt(
            {"name": "Jane", "company_name": "Acme"}, {"name": "Acme"},
            "sales", composer.db.get_profile(), "SECRET RESUME TEXT", None)
        assert "SECRET RESUME TEXT" not in prompt

    def test_application_prompt_includes_resume_text(self, composer):
        prompt = composer._build_prompt(
            {"name": "Jane", "company_name": "Acme"}, {"name": "Acme"},
            "application", composer.db.get_profile(), "RESUME MARKER", None)
        assert "RESUME MARKER" in prompt

    def test_custom_instructions_are_included(self, composer):
        prompt = composer._build_prompt(
            {"name": "Jane", "company_name": "Acme"}, None, "custom",
            composer.db.get_profile(), "", "Mention their Show HN post")
        assert "Mention their Show HN post" in prompt

    def test_company_research_is_included(self, composer):
        prompt = composer._build_prompt(
            {"name": "Jane", "company_name": "Acme"},
            {"name": "Acme", "summary": "They build widgets", "hook": "Series A last month"},
            "application", composer.db.get_profile(), "", None)
        assert "They build widgets" in prompt
        assert "Series A last month" in prompt


def test_every_declared_type_has_required_spec_fields():
    for key, spec in EMAIL_TYPES.items():
        assert spec["label"] and spec["goal"]
        assert spec["resume_weight"] in ("none", "low", "medium", "high")
        assert len(spec["structure"]) >= 3
