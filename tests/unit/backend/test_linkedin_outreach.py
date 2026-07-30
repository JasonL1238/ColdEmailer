from linkedin_outreach import draft_linkedin_message
from models import validate_linkedin_profile_url
from fastapi.testclient import TestClient


def test_fallback_draft_uses_verified_affinity_and_never_sends(monkeypatch):
    import linkedin_outreach

    monkeypatch.setattr(linkedin_outreach, "llm_complete", None)
    message = draft_linkedin_message(
        {
            "name": "Jane Doe",
            "role": "CTO",
            "company_name": "Acme",
            "affinity": "University of Pennsylvania, Shared: Stripe",
        },
        {"name": "Acme"},
        {"full_name": "Jason Li"},
    )

    assert message.startswith("Hi Jane")
    assert "University of Pennsylvania" in message
    assert "Stripe" in message
    assert "brief chat" in message


def test_linkedin_url_validation_accepts_member_profiles_only():
    assert validate_linkedin_profile_url(
        "https://linkedin.com/in/jane-doe/?trk=company"
    ) == "https://www.linkedin.com/in/jane-doe"

    for url in (
        "https://linkedin.com/company/acme",
        "https://evil.example/in/jane-doe",
        "http://linkedin.com/in/jane-doe",
    ):
        try:
            validate_linkedin_profile_url(url)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted unsafe LinkedIn URL: {url}")


def test_linkedin_connections_export_imports_as_a_direct_connection():
    import main

    csv_text = (
        "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
        "Alex,Rivera,https://www.linkedin.com/in/alex-rivera-coldemailer,"
        ",Example Robotics,VP Engineering,29 Jul 2026\n"
    )
    response = TestClient(main.app).post(
        "/api/contacts/import",
        files={"file": ("Connections.csv", csv_text, "text/csv")},
    )

    assert response.status_code == 200
    assert response.json()["added"] == 1
    contact = main.db.find_contact_by_linkedin(
        "https://www.linkedin.com/in/alex-rivera-coldemailer")
    assert contact["name"] == "Alex Rivera"
    assert contact["role"] == "VP Engineering"
    assert contact["affinity"] == "Direct LinkedIn connection"
    assert contact["source"] == "linkedin_export"
