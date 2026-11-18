"""Publish the real kit in order and check the exact gate outcomes."""
from collections import Counter
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


def _versions(client, name: str) -> list[dict]:
    return client.get(f"/api/services/{name}/versions").json()


def _attempts(client, name: str) -> list[dict]:
    return client.get(f"/api/services/{name}/attempts").json()


def _upload_quote_4(client):
    text = (KIT / "specs" / "quote-api" / "4-ncb-and-taxes.yaml").read_text(
        encoding="utf-8"
    )
    return client.post(
        "/api/services/quote-api/versions",
        content=text,
        params={"today": TODAY.isoformat()},
        headers={"Content-Type": "application/x-yaml"},
    )


def test_seed_matches_expected_kit_outcomes(client):
    _seed()

    assert [(v["version"], v["status"]) for v in _versions(client, "quote-api")] == [
        ("1.0.0", "published"),
        ("1.1.0", "published"),
        ("2.0.0", "blocked"),
        ("2.1.0", "blocked"),
    ]
    assert [(v["version"], v["status"]) for v in _versions(client, "policy-api")] == [
        ("1.0.0", "published"),
        ("1.1.0", "published"),
        ("2.0.0", "published"),
        ("2.1.0", "blocked"),
    ]
    assert [(v["version"], v["status"]) for v in _versions(client, "claims-api")] == [
        ("1.0.0", "published"),
        ("1.1.0", "published"),
        ("2.0.0", "blocked"),
    ]

    quote_attempts = _attempts(client, "quote-api")
    policy_attempts = _attempts(client, "policy-api")
    claims_attempts = _attempts(client, "claims-api")

    assert [
        (a["version"], a["outcome"]) for a in claims_attempts
    ] == [
        ("1.0.0", "published"),
        ("1.1.0", "rejected"),
        ("1.1.0", "published"),
        ("2.0.0", "blocked"),
    ]
    assert "ClaimDocumnt" in claims_attempts[1]["error"]

    # Quote 2.1.0 is still compared with 1.1.0, because 2.0.0 is blocked.
    assert quote_attempts[3]["version"] == "2.1.0"
    assert quote_attempts[3]["outcome"] == "blocked"
    assert quote_attempts[3]["compared_against"] == "1.1.0"
    assert any(
        change["field"] == "premium.breakdown[].amount"
        and change["severity"] == "breaking"
        for change in quote_attempts[3]["changes"]
    )

    policy_2 = policy_attempts[2]
    assert (policy_2["version"], policy_2["outcome"]) == ("2.0.0", "published")
    assert any(change["severity"] == "warning" for change in policy_2["changes"])

    policy_2_1 = policy_attempts[3]
    assert (policy_2_1["version"], policy_2_1["outcome"]) == ("2.1.0", "blocked")
    assert {
        group["consumer"] for group in policy_2_1["affected_consumers"]
    } >= {"partner-portal"}

    everything = [*quote_attempts, *policy_attempts, *claims_attempts]
    assert len(everything) == 12
    assert Counter(a["outcome"] for a in everything) == {
        "published": 7,
        "blocked": 4,
        "rejected": 1,
    }

    payments = client.get("/api/consumers/payments-svc").json()
    legacy = [
        stale
        for stale in payments["stale_references"]
        if stale["field"] == "legacyCode"
    ]
    assert legacy
    assert legacy[0]["since_version"] == "2.0.0"


def test_override_quote_2_0_0_unblocks_the_next_version(client):
    _seed()

    blocked = next(
        attempt
        for attempt in _attempts(client, "quote-api")
        if attempt["version"] == "2.0.0" and attempt["outcome"] == "blocked"
    )
    response = client.post(
        f"/api/services/quote-api/attempts/{blocked['id']}/override",
        json={
            "approved_by": "quotes team",
            "reason": "Mobile is migrating off premium.breakdown this sprint.",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["outcome"] == "overridden"
    assert body["approved_by"] == "quotes team"
    assert body["override_reason"].startswith("Mobile is migrating")

    quote_versions = {
        version["version"]: version["status"] for version in _versions(client, "quote-api")
    }
    assert quote_versions["2.0.0"] == "overridden"
    assert quote_versions["2.1.0"] == "blocked"

    mobile = client.get("/api/consumers/mobile-app").json()
    amount = [
        stale
        for stale in mobile["stale_references"]
        if stale["provider"] == "quote-api"
        and stale["field"] == "premium.breakdown[].amount"
    ]
    assert amount
    assert amount[0]["since_version"] == "2.0.0"

    # 2.1.0 now compares with the overridden 2.0.0 and is publishable.
    response = _upload_quote_4(client)
    assert response.status_code == 201
    body = response.json()
    assert (body["version"], body["outcome"]) == ("2.1.0", "published")
    assert body["compared_against"] == "2.0.0"
