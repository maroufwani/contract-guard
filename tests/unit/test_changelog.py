"""Unit tests for the changelog grouping and markdown rendering."""
from app.core.changelog import NO_CHANGES, build_changelog, to_markdown


def _change(
    method: str = "GET",
    path: str = "/quotes",
    severity: str = "safe",
    reason: str = "A change.",
    field: str | None = None,
    status_code: str | None = None,
) -> dict:
    return {
        "endpoint": f"{method} {path}",
        "path": path,
        "location": "response",
        "status_code": status_code,
        "field": field,
        "kind": "field_removed",
        "severity": severity,
        "rule": f"{severity}-rule",
        "reason": reason,
    }


def test_groups_by_endpoint_and_orders_breaking_first():
    changes = [
        _change(reason="Safe addition.", severity="safe"),
        _change(reason="Field removed.", severity="breaking"),
        _change(reason="Format changed.", severity="warning"),
        _change(
            method="POST",
            path="/quotes",
            reason="Endpoint removed.",
            severity="breaking",
        ),
    ]

    result = build_changelog(changes, "1.0.0", "2.0.0")

    assert result["from"] == "1.0.0"
    assert result["to"] == "2.0.0"
    assert result["summary"] == {"breaking": 2, "warning": 1, "safe": 1}
    assert result["message"] is None
    assert [group["endpoint"] for group in result["groups"]] == [
        "GET /quotes",
        "POST /quotes",
    ]
    assert [change["severity"] for change in result["groups"][0]["changes"]] == [
        "breaking",
        "warning",
        "safe",
    ]
    assert result["groups"][0]["changes"][0]["reason"] == "Field removed."


def test_markdown_has_summary_line_then_endpoint_sections():
    changes = [
        _change(reason="Field removed.", severity="breaking"),
        _change(reason="Format changed.", severity="warning"),
        _change(reason="Safe addition.", severity="safe"),
    ]

    markdown = to_markdown(build_changelog(changes, "1.0.0", "2.0.0"))

    lines = markdown.splitlines()
    assert lines[0] == "# Changelog 1.0.0 to 2.0.0"
    assert "2 breaking, 3 warnings, 1 safe" not in markdown
    assert "1 breaking, 1 warning, 1 safe" in lines
    assert "## GET /quotes" in lines
    assert "- Field removed." in lines
    assert "- Format changed." in lines
    assert "- Safe addition." in lines


def test_summary_pluralises_warnings():
    changes = [
        _change(reason="One.", severity="warning"),
        _change(reason="Two.", severity="warning"),
        _change(reason="Three.", severity="warning"),
    ]

    result = build_changelog(changes, "1.0.0", "1.1.0")

    assert to_markdown(result).splitlines()[2] == "0 breaking, 3 warnings, 0 safe"


def test_same_versions_says_no_contract_changes():
    changes = [_change(reason="Field removed.", severity="breaking")]

    result = build_changelog(changes, "1.0.0", "1.0.0")

    assert result["summary"] == {"breaking": 0, "warning": 0, "safe": 0}
    assert result["groups"] == []
    assert result["message"] == NO_CHANGES
    markdown = to_markdown(result)
    assert NO_CHANGES in markdown
    assert "breaking," not in markdown


def test_no_changes_says_no_contract_changes():
    result = build_changelog([], "1.0.0", "1.1.0")

    assert result["message"] == NO_CHANGES
    assert result["groups"] == []
    assert to_markdown(result).endswith(NO_CHANGES + "\n")
