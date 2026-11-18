"""Acceptance: the metrics of the real kit after seeding it in publish order."""
from datetime import date
from pathlib import Path

from app.db.session import get_session_factory
from scripts.seed import seed_kit

REPO_ROOT = Path(__file__).resolve().parents[2]
KIT = REPO_ROOT / "kit"
TODAY = date(2026, 11, 18)


def _seed() -> dict:
    with get_session_factory()() as db:
        return seed_kit(db, KIT, TODAY)


def test_seeded_kit_metrics(client):
    _seed()

    body = client.get("/api/metrics").json()

    assert body["total_attempts"] == 12
    assert body["outcomes"] == {
        "published": 7,
        "blocked": 4,
        "rejected": 1,
        "overridden": 0,
    }
    assert body["overrides"] == 0
    assert body["overridden_breaking"] == 0
    assert body["stale_references"] == 1
    assert body["block_rate"] == 0.333

    # quote 2.0.0 + quote 2.1.0 (same removed field, compared with 1.1.0 both
    # times) + policy 2.1.0 (two request changes) + claims 2.0.0 (one response
    # change).
    assert body["breaking_caught"] == 5
    assert body["consumers_protected"] == 2
    assert body["breaking_with_no_consumer"] == 2


def test_seeded_changelog_includes_blocked_versions(client):
    _seed()

    blocked = client.get(
        "/api/services/quote-api/changelog",
        params={"from": "1.1.0", "to": "2.0.0"},
    )
    assert blocked.status_code == 200
    body = blocked.json()
    assert body["summary"]["breaking"] >= 1
    assert any(
        change["severity"] == "breaking"
        for group in body["groups"]
        for change in group["changes"]
    )

    markdown = client.get(
        "/api/services/quote-api/changelog",
        params={"from": "2.0.0", "to": "2.0.0", "format": "markdown"},
    ).text
    assert "No contract changes" in markdown
