"""Integration tests for every HTML page, seeded with the real kit."""
from datetime import date
from pathlib import Path

from app.db.session import get_session_factory
from scripts.seed import seed_kit

REPO_ROOT = Path(__file__).resolve().parents[2]
KIT = REPO_ROOT / "kit"
TODAY = date(2026, 11, 18)


def _seed() -> None:
    with get_session_factory()() as db:
        seed_kit(db, KIT, TODAY)


def _blocked_quote_attempt(client) -> dict:
    attempts = client.get("/api/services/quote-api/attempts").json()
    return next(
        attempt
        for attempt in attempts
        if attempt["version"] == "2.0.0" and attempt["outcome"] == "blocked"
    )


def test_services_page_lists_services_and_last_outcome(client):
    _seed()

    response = client.get("/")

    assert response.status_code == 200
    assert "quote-api" in response.text
    assert "policy-api" in response.text
    assert "Quotes team" in response.text
    assert "1.1.0" in response.text
    assert "blocked" in response.text


def test_service_page_has_versions_history_and_forms(client):
    _seed()

    response = client.get("/services/quote-api")

    assert response.status_code == 200
    assert "1.0.0" in response.text
    assert "2.0.0" in response.text
    assert "Publish history" in response.text
    assert "Check impact without publishing" in response.text
    assert "Compare two versions" in response.text
    assert "hx-encoding" in response.text


def test_compare_page_is_breaking_and_names_the_field(client):
    _seed()

    response = client.get(
        "/services/quote-api/compare", params={"from": "1.1.0", "to": "2.0.0"}
    )

    assert response.status_code == 200
    assert "premium.breakdown[].amount" in response.text
    assert "breaking" in response.text


def test_attempt_page_lists_affected_consumers(client):
    _seed()
    attempt = _blocked_quote_attempt(client)

    response = client.get(f"/services/quote-api/attempts/{attempt['id']}")

    assert response.status_code == 200
    assert "mobile-app" in response.text
    assert "premium.breakdown[].amount" in response.text
    assert "Override" in response.text


def test_consumers_pages(client):
    _seed()

    listing = client.get("/consumers")
    detail = client.get("/consumers/payments-svc")

    assert listing.status_code == 200
    assert "payments-svc" in listing.text
    assert "stale" in listing.text
    assert detail.status_code == 200
    assert "legacyCode" in detail.text
    assert "2.0.0" in detail.text


def test_metrics_page_shows_the_seeded_total(client):
    _seed()

    response = client.get("/metrics")

    assert response.status_code == 200
    assert "Total attempts" in response.text
    assert ">12<" in response.text


def test_register_service_form(client):
    _seed()

    response = client.post(
        "/services",
        data={"name": "billing-api", "owner_team": "Billing team"},
        follow_redirects=False,
    )
    page = client.get("/services/billing-api")

    assert response.status_code == 303
    assert page.status_code == 200
    assert "Billing team" in page.text


def test_upload_through_the_web_form(client):
    _seed()
    client.post(
        "/services",
        data={"name": "fresh-api", "owner_team": "Fresh team"},
        follow_redirects=False,
    )
    spec = (KIT / "specs" / "quote-api" / "1-initial.yaml").read_text(
        encoding="utf-8"
    )

    response = client.post(
        "/services/fresh-api/upload",
        files={"file": ("1-initial.yaml", spec, "application/x-yaml")},
        data={"spec_text": ""},
    )
    page = client.get("/services/fresh-api")

    assert response.status_code == 200
    assert "published" in response.text
    assert "1.0.0" in page.text


def test_empty_web_upload_saves_no_attempt(client):
    _seed()
    client.post(
        "/services",
        data={"name": "empty-api", "owner_team": "Empty team"},
        follow_redirects=False,
    )

    response = client.post(
        "/services/empty-api/upload",
        data={"spec_text": "   \n"},
    )
    attempts = client.get("/api/services/empty-api/attempts").json()

    assert response.status_code == 200
    assert "Choose a spec file or paste the YAML" in response.text
    assert attempts == []


def test_empty_api_upload_returns_422_and_saves_no_attempt(client):
    _seed()
    client.post(
        "/api/services",
        json={"name": "empty-api", "owner_team": "Empty team"},
    )

    response = client.post(
        "/api/services/empty-api/versions",
        content=b"   ",
        headers={"content-type": "text/plain"},
    )
    attempts = client.get("/api/services/empty-api/attempts").json()

    assert response.status_code == 422
    assert response.json()["error"] == "Choose a spec file or paste the YAML"
    assert attempts == []


def test_empty_impact_form_shows_the_message_and_saves_no_attempt(client):
    _seed()
    before = client.get("/api/services/quote-api/attempts").json()

    response = client.post(
        "/services/quote-api/impact",
        data={"spec_text": "  \n"},
    )
    attempts = client.get("/api/services/quote-api/attempts").json()

    assert response.status_code == 200
    assert "Choose a spec file or paste the YAML" in response.text
    assert len(attempts) == len(before)
