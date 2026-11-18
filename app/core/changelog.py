"""Turn classified changes into a changelog grouped by endpoint.

Pure: the input is the list of change dicts that publish stores (each one
has the diff fields plus severity, rule and reason). No database here.
"""
from typing import Any

_SEVERITY_ORDER = {"breaking": 0, "warning": 1, "safe": 2}
NO_CHANGES = "No contract changes"


def _endpoint_label(item: dict) -> str:
    """'GET /quotes' from the endpoint key and the display path."""
    method, _, fallback_path = str(item.get("endpoint") or "").partition(" ")
    path = item.get("path") or fallback_path
    label = f"{method} {path}".strip()
    return label or "(unknown endpoint)"


def _severity(item: dict) -> str:
    severity = item.get("severity")
    return severity if severity in _SEVERITY_ORDER else "safe"


def build_changelog(
    classified: list[dict], from_label: str, to_label: str
) -> dict:
    """Group changes by endpoint, breaking first, then warning, then safe.

    Returns {"from", "to", "summary", "groups", "message"}. message is
    "No contract changes" when the labels are the same or nothing changed.
    """
    summary = {"breaking": 0, "warning": 0, "safe": 0}
    grouped: dict[str, list[dict]] = {}

    if from_label != to_label:
        for item in classified:
            severity = _severity(item)
            summary[severity] += 1
            grouped.setdefault(_endpoint_label(item), []).append(
                {"severity": severity, "reason": item.get("reason") or ""}
            )

    groups: list[dict[str, Any]] = []
    for endpoint, changes in grouped.items():
        changes.sort(key=lambda change: _SEVERITY_ORDER[change["severity"]])
        groups.append({"endpoint": endpoint, "changes": changes})
    groups.sort(
        key=lambda group: (
            _SEVERITY_ORDER[group["changes"][0]["severity"]],
            group["endpoint"],
        )
    )

    return {
        "from": from_label,
        "to": to_label,
        "summary": summary,
        "groups": groups,
        "message": NO_CHANGES if not groups else None,
    }


def _summary_line(summary: dict) -> str:
    warnings = summary["warning"]
    plural = "" if warnings == 1 else "s"
    return f"{summary['breaking']} breaking, {warnings} warning{plural}, {summary['safe']} safe"


def to_markdown(changelog: dict) -> str:
    """Readable text changelog: summary line first, then one section per endpoint."""
    lines = [f"# Changelog {changelog['from']} to {changelog['to']}", ""]
    if changelog.get("message") or not changelog.get("groups"):
        lines.append(NO_CHANGES)
        return "\n".join(lines) + "\n"

    lines.append(_summary_line(changelog["summary"]))
    for group in changelog["groups"]:
        lines.extend(["", f"## {group['endpoint']}", ""])
        lines.extend(f"- {change['reason']}" for change in group["changes"])
    return "\n".join(lines) + "\n"
