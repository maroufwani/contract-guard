"""Decide whether a diff may publish, from the classified changes and impact.

This is pure: callers pass plain classified changes (Change, Classification),
the impact dict from app.core.impact.compute_impact, and an optional semver
warning from app.core.classify.check_version_bump.

A publish is blocked only when a breaking change affects at least one known
consumer. Everything else (warnings, a semver warning, breaking changes nobody
uses) is reported in the reasons but never blocks.
"""
from dataclasses import dataclass

from app.core.classify import Classification
from app.core.diff import Change


@dataclass(frozen=True)
class Decision:
    outcome: str
    reasons: list[str]


def decide(
    classified: list[tuple[Change, Classification]],
    impact: dict,
    version_warning: Classification | None,
) -> Decision:
    """Return published/blocked plus plain-English reasons for the decision."""
    reasons: list[str] = []
    blocking = False

    for _change, classification in classified:
        if classification.severity in {"breaking", "warning"}:
            reasons.append(classification.reason)

    for group in impact.get("affected_consumers") or []:
        for item in group.get("affected") or []:
            if item.get("severity") == "breaking":
                blocking = True
                reasons.append(f"Blocking: {item['reason']}")

    for item in impact.get("unused_breaking") or []:
        reasons.append(
            f"{item['reason']} No known consumer is affected, so this does not "
            "block the publish."
        )

    if version_warning is not None:
        reasons.append(version_warning.reason)

    if not reasons:
        reasons.append("No breaking or warning changes were found.")

    return Decision(
        outcome="blocked" if blocking else "published",
        reasons=reasons,
    )
