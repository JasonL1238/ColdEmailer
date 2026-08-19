"""Offline tests for conservative employee-mail domain discovery."""
import mail_domain as md


def test_observed_addresses_are_deduplicated_and_aggregators_are_ignored():
    counts = md.count_observed_email_domains([
        "Jane@Acme.com", "jane@acme.com", "other@acme.com",
        "lead@rocketreach.co",
    ])
    assert counts == {"acme.com": 2}


def test_only_named_contacts_missing_email_need_mail_domain():
    assert md.contacts_need_mail_domain([
        {"name": "Jane Doe", "email": "", "name_from_email": False},
    ]) is True
    assert md.contacts_need_mail_domain([
        {"name": "Jane Doe", "email": "jane@acme.com"},
        {"name": "support", "email": "", "name_from_email": True},
    ]) is False


def test_discovery_combines_site_evidence_and_two_bounded_searches(monkeypatch):
    calls = []

    def search(query, max_results=8):
        calls.append((query, max_results))
        if "format" in query:
            return [{
                "title": "Goldman Sachs email format",
                "body": "a@gs.com b@gs.com c@gs.com d@gs.com",
            }]
        return [{"title": "Contact", "body": "press@goldmansachs.com"}]

    shared = {"mx0a-0014b501.pphosted.com"}
    monkeypatch.setattr(md, "domain_has_mx", lambda _domain: True)
    monkeypatch.setattr(md, "_mx_hosts", lambda _domain: shared)

    domain, reason, counts = md.discover_mail_domain(
        "Goldman Sachs",
        "goldmansachs.com",
        search_fn=search,
        observed_emails=["media@goldmansachs.com"],
    )

    assert domain == "gs.com"
    assert "mail tenancy" in reason
    assert counts == {"goldmansachs.com": 2, "gs.com": 4}
    assert calls == [
        ('"@goldmansachs.com" email contact', 8),
        ("Goldman Sachs email address format", 8),
    ]


def test_popular_unrelated_domain_cannot_redirect_guesses(monkeypatch):
    def search(_query, max_results=8):
        return [{
            "title": "Directory",
            "body": " ".join(f"person{i}@camping-arize.com" for i in range(8)),
        }]

    monkeypatch.setattr(md, "domain_has_mx", lambda _domain: True)
    monkeypatch.setattr(md, "_mx_hosts", lambda _domain: set())
    domain, _, _ = md.discover_mail_domain(
        "Arize AI", "arize.com", search_fn=search)
    assert domain == "arize.com"
