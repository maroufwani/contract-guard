"""Integration tests for the changelog API through the TestClient."""
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
KIT = REPO_ROOT / "kit"
TODAY = date(2026, 11, 18)
SEVERITY_ORDER = {"breaking": 0, "warning": 1, "safe": 2}


def _upload(client, service: str, filename: str):
    text = (KIT / "specs" / service / filename).read_text(encoding="utf-8")
    return client.post(
        f"/api/services/{service}/versions",
        content=text,
        params={"today": TODAY.isoformat()},
        headers={"Content-Type": "application/x-yaml"},
    )


def _policy_api(client) -> None:
    client.post(
        "/api/services", json={"name": "policy-api", "owner_team": "Policy team"}
    )
    first = _upload(client, "policy-api", "1-initial.yaml")
    second = _upload(client, "policy-api", "2-deprecations.yaml")
    assert first.status_code == 201
    assert second.status_code == 201


def test_changelog_json_groups_by_endpoint(client):
    _policy_api(client)

    response = client.get(
        "/api/services/policy-api/changelog",
        params={"from": "1.0.0", "to": "1.1.0"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["from"] == "1.0.0"
    assert body["to"] == "1.1.0"
    assert body["message"] is None
    assert body["groups"]
    assert sum(body["summary"].values()) == sum(
        len(group["changes"]) for group in body["groups"]
    )
    for group in body["groups"]:
        assert group["endpoint"].startswith(("GET ", "POST ", "PUT ", "DELETE ", "PATCH "))
        severities = [change["severity"] for change in group["changes"]]
        assert severities == sorted(severities, key=SEVERITY_ORDER.__getitem__)


def test_changelog_accepts_versions_in_either_order(client):
    _policy_api(client)

    forward = client.get(
        "/api/services/policy-api/changelog",
        params={"from": "1.0.0", "to": "1.1.0"},
    )
    backward = client.get(
        "/api/services/policy-api/changelog",
        params={"from": "1.1.0", "to": "1.0.0"},
    )

    assert forward.status_code == 200
    assert backward.status_code == 200
    assert backward.json()["from"] == "1.1.0"
    assert backward.json()["to"] == "1.0.0"
    assert backward.json()["groups"]


def test_changelog_markdown(client):
    _policy_api(client)

    response = client.get(
        "/api/services/policy-api/changelog",
        params={"from": "1.0.0", "to": "1.1.0", "format": "markdown"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    lines = response.text.splitlines()
    assert lines[0] == "# Changelog 1.0.0 to 1.1.0"
    summary = lines[2]
    assert "breaking," in summary and "safe" in summary
    assert "## " in response.text


def test_changelog_of_the_same_version_is_empty(client):
    _policy_api(client)

    body = client.get(
        "/api/services/policy-api/changelog",
        params={"from": "1.1.0", "to": "1.1.0"},
    ).json()
    markdown = client.get(
        "/api/services/policy-api/changelog",
        params={"from": "1.1.0", "to": "1.1.0", "format": "markdown"},
    ).text

    assert body["message"] == "No contract changes"
    assert body["groups"] == []
    assert "No contract changes" in markdown


def test_changelog_rejects_unknown_format_and_unknown_version(client):
    _policy_api(client)

    bad_format = client.get(
        "/api/services/policy-api/changelog",
        params={"from": "1.0.0", "to": "1.1.0", "format": "html"},
    )
    unknown = client.get(
        "/api/services/policy-api/changelog",
        params={"from": "9.9.9", "to": "1.1.0"},
    )

    assert bad_format.status_code == 422
    assert unknown.status_code == 404
