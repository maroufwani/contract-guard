"""Unit tests for the dashboard metrics over hand-made attempts."""
from app.core.metrics import compute_metrics


def _breaking(path: str, field: str, rule: str) -> dict:
    return {
        "endpoint": f"GET {path}",
        "path": path,
        "field": field,
        "status_code": "200",
        "severity": "breaking",
        "rule": rule,
        "reason": "Breaking change.",
    }


def _affected(consumer: str, path: str, field: str, rule: str) -> dict:
    return {
        "consumer": consumer,
        "affected": [
            {
                "consumer": consumer,
                "endpoint": path,
                "field": field,
                "status_code": "200",
                "severity": "breaking",
                "rule": rule,
                "reason": "Breaking change.",
            }
        ],
    }


def _warning_affected(consumer: str, path: str, field: str) -> dict:
    return {
        "consumer": consumer,
        "affected": [
            {
                "consumer": consumer,
                "endpoint": path,
                "field": field,
                "status_code": "200",
                "severity": "warning",
                "rule": "warning-rule",
                "reason": "Warning change.",
            }
        ],
    }


def _attempt(outcome: str, **extra) -> dict:
    return {
        "outcome": outcome,
        "changes": extra.get("changes", []),
        "affected_consumers": extra.get("affected_consumers", []),
        "unused_breaking": extra.get("unused_breaking", []),
    }


def _hand_made_attempts() -> list[dict]:
    quote_removed = _breaking("/quotes", "premium", "removed")
    quote_unused = _breaking("/quotes", "legacy", "removed")
    policy_removed = _breaking("/policies", "type", "enum-removed")
    return [
        _attempt(
            "published",
            unused_breaking=[_breaking("/prices", "oldPrice", "removed")],
        ),
        _attempt(
            "blocked",
            changes=[quote_removed, quote_unused],
            affected_consumers=[_affected("mobile-app", "/quotes", "premium", "removed")],
            unused_breaking=[quote_unused],
        ),
        _attempt(
            "blocked",
            changes=[policy_removed],
            affected_consumers=[
                _affected("partner-portal", "/policies", "type", "enum-removed"),
                _warning_affected("notifications-svc", "/policies", "validUntil"),
            ],
        ),
        _attempt("rejected"),
        # The override is a second record; the blocked one above already caught it.
        _attempt(
            "overridden",
            changes=[quote_removed, quote_unused],
            affected_consumers=[_affected("mobile-app", "/quotes", "premium", "removed")],
            unused_breaking=[quote_unused],
        ),
    ]


def test_counts_attempts_and_outcomes():
    metrics = compute_metrics(_hand_made_attempts(), stale_count=3)

    assert metrics["total_attempts"] == 5
    assert metrics["outcomes"] == {
        "published": 1,
        "blocked": 2,
        "rejected": 1,
        "overridden": 1,
    }
    assert metrics["block_rate"] == 0.4


def test_caught_breaking_is_not_double_counted_by_an_override():
    metrics = compute_metrics(_hand_made_attempts(), stale_count=3)

    assert metrics["breaking_caught"] == 2
    assert metrics["overrides"] == 1
    assert metrics["overridden_breaking"] == 1


def test_consumers_protected_are_distinct_and_only_hit_by_breaking_changes():
    metrics = compute_metrics(_hand_made_attempts(), stale_count=3)

    # notifications-svc only sees a warning, so it is not "protected".
    assert metrics["consumers_protected"] == 2


def test_unused_breaking_only_counts_what_shipped():
    metrics = compute_metrics(_hand_made_attempts(), stale_count=3)

    # /prices from the published attempt, /quotes legacy from the override.
    assert metrics["breaking_with_no_consumer"] == 2


def test_stale_references_are_passed_through():
    metrics = compute_metrics([], stale_count=4)

    assert metrics["stale_references"] == 4
    assert metrics["total_attempts"] == 0
    assert metrics["block_rate"] == 0.0
    assert metrics["outcomes"] == {
        "published": 0,
        "blocked": 0,
        "rejected": 0,
        "overridden": 0,
    }
