"""Aggregate the publish history into the dashboard numbers.

Pure: `attempts` are plain attempt dicts (outcome, changes,
affected_consumers, unused_breaking). An overridden attempt is a stored copy
of the blocked attempt it came from, so its changes are not counted as caught
a second time: the blocked record keeps the catch, the overridden record only
counts the override.
"""
OUTCOMES = ("published", "blocked", "rejected", "overridden")


def _change_key(change: dict) -> tuple:
    """Identify a change inside one attempt, across changes/affected item shapes."""
    return (
        change.get("path") or change.get("endpoint") or "",
        change.get("field"),
        change.get("status_code"),
        change.get("rule"),
    )


def _breaking_hits(attempt: dict) -> set[tuple]:
    """Keys of breaking changes that reached at least one consumer."""
    keys: set[tuple] = set()
    for group in attempt.get("affected_consumers") or []:
        for item in group.get("affected") or []:
            if item.get("severity") == "breaking":
                keys.add(_change_key(item))
    return keys


def _caught_breaking(attempt: dict) -> int:
    """How many breaking changes in this attempt hit a consumer."""
    hits = _breaking_hits(attempt)
    return len(
        {
            _change_key(change)
            for change in attempt.get("changes") or []
            if change.get("severity") == "breaking"
            and _change_key(change) in hits
        }
    )


def _unused_breaking(attempt: dict) -> set[tuple]:
    return {
        _change_key(change)
        for change in attempt.get("unused_breaking") or []
        if change.get("severity", "breaking") == "breaking"
    }


def compute_metrics(attempts: list[dict], stale_count: int) -> dict:
    """Totals, gate catches and the human cost of the overrides."""
    outcomes = {outcome: 0 for outcome in OUTCOMES}
    caught = 0
    protected: set[str] = set()
    overridden_breaking = 0
    unused: set[tuple] = set()

    for attempt in attempts:
        outcome = attempt.get("outcome")
        if outcome in outcomes:
            outcomes[outcome] += 1

        if outcome == "blocked":
            # Still counted after an override: the blocked record is what
            # caught the change, the overridden record is added separately.
            caught += _caught_breaking(attempt)
            for group in attempt.get("affected_consumers") or []:
                if any(
                    item.get("severity") == "breaking"
                    for item in group.get("affected") or []
                ):
                    protected.add(group["consumer"])

        if outcome == "overridden":
            overridden_breaking += _caught_breaking(attempt)

        if outcome in {"published", "overridden"}:
            # Only changes that shipped can have been "warned only".
            unused |= _unused_breaking(attempt)

    total = len(attempts)
    blocked = outcomes["blocked"]
    return {
        "total_attempts": total,
        "outcomes": outcomes,
        "breaking_caught": caught,
        "consumers_protected": len(protected),
        "breaking_with_no_consumer": len(unused),
        "overrides": outcomes["overridden"],
        "overridden_breaking": overridden_breaking,
        "stale_references": stale_count,
        "block_rate": round(blocked / total, 3) if total else 0.0,
    }
