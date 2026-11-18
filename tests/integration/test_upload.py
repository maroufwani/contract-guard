"""Service registration and spec upload through the JSON API."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
KIT = REPO_ROOT / "kit" / "specs"


def _read(*parts: str) -> str:
    return (KIT.joinpath(*parts)).read_text(encoding="utf-8")


def _upload(client, service: str, *spec_parts: str) -> object:
    return client.post(
        f"/api/services/{service}/versions",
        content=_read(*spec_parts),
        headers={"Content-Type": "application/x-yaml"},
    )


def test_create_service(client):
    response = client.post(
        "/api/services",
        json={"name": "quote-api", "owner_team": "Quotes", "contact": "quotes@example.com"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "quote-api"
    assert body["latest_published"] is None

    listed = client.get("/api/services").json()
    assert [service["name"] for service in listed] == ["quote-api"]


def test_duplicate_service_is_409(client):
    client.post("/api/services", json={"name": "quote-api", "owner_team": "Quotes"})
    response = client.post("/api/services", json={"name": "quote-api", "owner_team": "Other"})
    assert response.status_code == 409
    assert "error" in response.json()


def test_service_404_is_json_error(client):
    response = client.get("/api/services/missing")
    assert response.status_code == 404
    assert "error" in response.json()


def test_upload_raw_yaml_body(client):
    client.post("/api/services", json={"name": "quote-api", "owner_team": "Quotes"})
    response = _upload(client, "quote-api", "quote-api", "1-initial.yaml")
    assert response.status_code == 201
    body = response.json()
    assert body["version"] == "1.0.0"
    assert body["endpoints"] == ["DELETE /internal/pricing-cache", "GET /quotes/{}", "POST /quotes"]

    versions = client.get("/api/services/quote-api/versions").json()
    assert [version["status"] for version in versions] == ["published"]
    attempts = client.get("/api/services/quote-api/attempts").json()
    assert [attempt["outcome"] for attempt in attempts] == ["published"]


def test_upload_multipart_file(client):
    client.post("/api/services", json={"name": "quote-api", "owner_team": "Quotes"})
    response = client.post(
        "/api/services/quote-api/versions",
        files={"file": ("2-reorganised.yaml", _read("quote-api", "2-reorganised.yaml"), "application/x-yaml")},
    )
    assert response.status_code == 201
    assert response.json()["version"] == "1.1.0"


def test_upload_invalid_ref_is_422_and_recorded_as_rejected(client):
    client.post("/api/services", json={"name": "claims-api", "owner_team": "Claims"})
    _upload(client, "claims-api", "claims-api", "1-initial.yaml")

    response = _upload(client, "claims-api", "claims-api", "2-channel-and-notes.yaml")
    assert response.status_code == 422
    assert "ClaimDocumnt" in response.json()["error"]

    attempts = client.get("/api/services/claims-api/attempts").json()
    assert [attempt["outcome"] for attempt in attempts] == ["published", "rejected"]
    assert "ClaimDocumnt" in attempts[1]["error"]
    assert attempts[1]["version"] == "1.1.0"

    # The invalid upload stored nothing, and its number stays reusable.
    versions = client.get("/api/services/claims-api/versions").json()
    assert [version["version"] for version in versions] == ["1.0.0"]


def test_upload_version_not_higher_than_published_is_rejected(client):
    client.post("/api/services", json={"name": "quote-api", "owner_team": "Quotes"})
    _upload(client, "quote-api", "quote-api", "2-reorganised.yaml")  # publishes 1.1.0

    response = _upload(client, "quote-api", "quote-api", "1-initial.yaml")  # 1.0.0 < 1.1.0
    assert response.status_code == 422
    assert "1.1.0" in response.json()["error"]

    attempts = client.get("/api/services/quote-api/attempts").json()
    assert attempts[-1]["outcome"] == "rejected"
    assert attempts[-1]["compared_against"] == "1.1.0"


def test_upload_to_unknown_service_is_404(client):
    response = _upload(client, "ghost", "quote-api", "1-initial.yaml")
    assert response.status_code == 404
    assert "error" in response.json()
