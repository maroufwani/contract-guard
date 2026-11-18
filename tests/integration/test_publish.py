"""Publish gate and override behaviour through the JSON API."""
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

from app.db.models import (
    Consumer,
    PublishAttempt,
    Service,
    SpecVersion,
)
from app.db.session import get_session_factory
from scripts.seed import seed_kit

REPO_ROOT = Path(__file__).resolve().parents[2]
KIT = REPO_ROOT / "kit"
TODAY = date(2026, 11, 18)


def _read_spec(service: str, filename: str) -> str:
    return (KIT / "specs" / service / filename).read_text(encoding="utf-8")


def _upload(client, service: str, filename: str):
    return client.post(
        f"/api/services/{service}/versions",
        content=_read_spec(service, filename),
        params={"today": TODAY.isoformat()},
        headers={"Content-Type": "application/x-yaml"},
    )


def _register_quote(client):
    response = client.post(
        "/api/services",
        json={"name": "quote-api", "owner_team": "Quotes team"},
    )
    assert response.status_code == 201


def _blocked_quote_attempt(client) -> dict:
    # The kit consumers are needed for quote 2.0.0 to block.
    with get_session_factory()() as db:
        seed_kit(db, KIT, TODAY)
    attempts = client.get("/api/services/quote-api/attempts").json()
    return next(
        attempt
        for attempt in attempts
        if attempt["version"] == "2.0.0" and attempt["outcome"] == "blocked"
    )


def test_override_by_wrong_team_is_403(client):
    blocked = _blocked_quote_attempt(client)
    response = client.post(
        f"/api/services/quote-api/attempts/{blocked['id']}/override",
        json={"approved_by": "Policy team", "reason": "Please let this through."},
    )
    assert response.status_code == 403
    assert "Quotes team" in response.json()["error"]


def test_override_with_empty_reason_is_422(client):
    blocked = _blocked_quote_attempt(client)
    response = client.post(
        f"/api/services/quote-api/attempts/{blocked['id']}/override",
        json={"approved_by": "quotes team", "reason": "   "},
    )
    assert response.status_code == 422
    assert "reason" in response.json()["error"].lower()


def test_override_of_published_attempt_is_409(client):
    _register_quote(client)
    _upload(client, "quote-api", "1-initial.yaml")
    published = client.get("/api/services/quote-api/attempts").json()[0]
    assert published["outcome"] == "published"

    response = client.post(
        f"/api/services/quote-api/attempts/{published['id']}/override",
        json={"approved_by": "Quotes team", "reason": "No need for this."},
    )
    assert response.status_code == 409


def test_lower_version_is_rejected(client):
    _register_quote(client)
    assert _upload(client, "quote-api", "2-reorganised.yaml").status_code == 201

    response = _upload(client, "quote-api", "1-initial.yaml")
    assert response.status_code == 422
    assert "1.1.0" in response.json()["error"]

    attempts = client.get("/api/services/quote-api/attempts").json()
    assert [attempt["outcome"] for attempt in attempts] == ["published", "rejected"]


def test_seeding_twice_does_not_duplicate(client):
    with get_session_factory()() as db:
        first = seed_kit(db, KIT, TODAY)
    assert first["seeded"] is True

    def counts() -> tuple[int, int, int, int]:
        with get_session_factory()() as db:
            return (
                db.scalar(select(func.count()).select_from(Service)),
                db.scalar(select(func.count()).select_from(Consumer)),
                db.scalar(select(func.count()).select_from(SpecVersion)),
                db.scalar(select(func.count()).select_from(PublishAttempt)),
            )

    before = counts()
    with get_session_factory()() as db:
        second = seed_kit(db, KIT, TODAY)
    assert second["seeded"] is False
    assert counts() == before
    assert before[0:2] == (3, 4)
    assert before[3] == 12
