"""GET /api/services/{name}/diff between two stored versions."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
KIT = REPO_ROOT / "kit" / "specs"


def _read(*parts: str) -> str:
    return KIT.joinpath(*parts).read_text(encoding="utf-8")


def _upload(client, service: str, *spec_parts: str) -> object:
    return client.post(
        f"/api/services/{service}/versions",
        content=_read(*spec_parts),
        headers={"Content-Type": "application/x-yaml"},
    )


def test_diff_returns_changes_between_two_versions(client):
    client.post("/api/services", json={"name": "quote-api", "owner_team": "Quotes"})
    _upload(client, "quote-api", "quote-api", "2-reorganised.yaml")
    _upload(client, "quote-api", "quote-api", "3-price-lines.yaml")
    labels = [
        version["version"]
        for version in client.get("/api/services/quote-api/versions").json()
    ]

    response = client.get(
        "/api/services/quote-api/diff",
        params={"from": labels[0], "to": labels[1]},
    )
    assert response.status_code == 200
    body = response.json()
    changes = body["changes"]
    assert {
        (change["endpoint"], change["field"], change["kind"]) for change in changes
    } == {
        ("DELETE /internal/pricing-cache", "query:region", "param_added"),
        ("GET /quotes/{}", "premium.breakdown[].amount", "field_removed"),
        ("GET /quotes/{}", "premium.breakdown[].value", "field_added"),
        ("GET /quotes/{}", "status", "enum_values_added"),
        ("POST /quotes", "premium.breakdown[].amount", "field_removed"),
        ("POST /quotes", "premium.breakdown[].value", "field_added"),
        ("POST /quotes", "status", "enum_values_added"),
    }
    assert all(change["severity"] and change["rule"] and change["reason"] for change in changes)
    assert body["summary"] == {"breaking": 2, "warning": 3, "safe": 2}


def test_diff_unknown_version_is_404(client):
    client.post("/api/services", json={"name": "quote-api", "owner_team": "Quotes"})
    _upload(client, "quote-api", "quote-api", "1-initial.yaml")
    label = client.get("/api/services/quote-api/versions").json()[0]["version"]

    response = client.get(
        "/api/services/quote-api/diff",
        params={"from": label, "to": "9.9.9"},
    )
    assert response.status_code == 404
    assert "9.9.9" in response.json()["error"]
