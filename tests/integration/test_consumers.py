"""Consumer registration and the impact endpoint through the JSON API."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONSUMERS = REPO_ROOT / "kit" / "consumers"
SPECS = REPO_ROOT / "kit" / "specs"


def _read_consumer(name: str) -> str:
    return (CONSUMERS / f"{name}.yaml").read_text(encoding="utf-8")


def _read_spec(*parts: str) -> str:
    return (SPECS.joinpath(*parts)).read_text(encoding="utf-8")


def _upload_consumer(client, name: str):
    return client.post(
        "/api/consumers",
        content=_read_consumer(name),
        headers={"Content-Type": "application/x-yaml"},
    )


def _upload_spec(client, service: str, *parts: str):
    return client.post(
        f"/api/services/{service}/versions",
        content=_read_spec(*parts),
        headers={"Content-Type": "application/x-yaml"},
    )


def test_upload_consumer_and_get_it(client):
    response = _upload_consumer(client, "mobile-app")
    assert response.status_code == 201
    body = response.json()
    assert body["consumer"] == "mobile-app"
    assert body["team"] == "Mobile"
    assert len(body["usages"]) == 4

    listed = client.get("/api/consumers").json()
    assert [consumer["consumer"] for consumer in listed] == ["mobile-app"]

    shown = client.get("/api/consumers/mobile-app").json()
    assert shown["consumer"] == "mobile-app"
    assert len(shown["usages"]) == 4
    assert "stale_references" in shown


def test_upload_consumer_replaces_existing_usages(client):
    _upload_consumer(client, "mobile-app")

    replacement = """
consumer: mobile-app
team: Mobile
uses:
- provider: quote-api
  method: get
  path: /quotes/{id}
  sends:
  - id
  reads:
  - quoteId
"""
    response = client.post(
        "/api/consumers",
        files={"file": ("mobile-app.yaml", replacement, "application/x-yaml")},
    )
    assert response.status_code == 201

    shown = client.get("/api/consumers/mobile-app").json()
    assert shown["team"] == "Mobile"
    assert len(shown["usages"]) == 1
    assert shown["usages"][0] == {
        "provider": "quote-api",
        "method": "GET",
        "path": "/quotes/{id}",
        "sends": ["id"],
        "reads": ["quoteId"],
    }


def test_consumer_with_bad_method_is_422(client):
    text = """
consumer: broken-app
team: Broken
uses:
- provider: quote-api
  method: FETCH
  path: /quotes
"""
    response = client.post(
        "/api/consumers",
        content=text,
        headers={"Content-Type": "application/x-yaml"},
    )
    assert response.status_code == 422
    assert "FETCH" in response.json()["error"]


def test_impact_endpoint_returns_consumers_and_saves_nothing(client):
    client.post("/api/services", json={"name": "quote-api", "owner_team": "Quotes"})
    _upload_spec(client, "quote-api", "quote-api", "2-reorganised.yaml")
    for consumer in ("mobile-app", "notifications-svc", "partner-portal"):
        _upload_consumer(client, consumer)

    versions_before = client.get("/api/services/quote-api/versions").json()
    attempts_before = client.get("/api/services/quote-api/attempts").json()

    response = client.post(
        "/api/services/quote-api/impact",
        content=_read_spec("quote-api", "3-price-lines.yaml"),
        params={"today": "2026-11-18"},
        headers={"Content-Type": "application/x-yaml"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == "2.0.0"
    assert body["compared_against"] == "1.1.0"
    assert body["summary"] == {"breaking": 2, "warning": 3, "safe": 2}
    assert {
        group["consumer"] for group in body["affected_consumers"]
    } == {"mobile-app", "notifications-svc"}
    assert len(body["unused_breaking"]) == 1
    unused = body["unused_breaking"][0]
    assert (unused["endpoint"], unused["field"], unused["severity"]) == (
        "GET /quotes/{}",
        "premium.breakdown[].amount",
        "breaking",
    )

    # The proposed spec was never stored and no attempt was recorded.
    assert client.get("/api/services/quote-api/versions").json() == versions_before
    assert client.get("/api/services/quote-api/attempts").json() == attempts_before
