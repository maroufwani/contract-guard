"""Decide how serious each contract change is and explain it in plain English.

The rules are a table keyed by the part of the contract that changed and the
kind of change. Severity and reason can be functions when the answer depends
on the change itself (for example a new required field vs an optional one).

`today` is always passed in so tests and the API agree on what "already passed
its sunset date" means.
"""
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.core.diff import Change
from app.core.versions import parse_version

Severity = Literal["breaking", "warning", "safe"]
SeverityRule = str | Callable[[Change, date], str]
ReasonRule = Callable[[Change, date], str]


@dataclass(frozen=True)
class Classification:
    severity: Severity
    rule: str
    reason: str


@dataclass(frozen=True)
class Rule:
    rule: str
    severity: SeverityRule
    reason: ReasonRule


# ---------------------------------------------------------------------------
# Small helpers for readable reasons
# ---------------------------------------------------------------------------


def _endpoint_label(change: Change) -> str:
    """'POST /quotes (201)' with the display spelling of the path."""
    method, _, fallback_path = change.endpoint.partition(" ")
    path = change.path or fallback_path
    label = f"{method} {path}".strip()
    if change.status_code:
        label = f"{label} ({change.status_code})"
    return label


def _field(change: Change) -> str:
    return change.field or "value"


def _param(change: Change) -> str:
    """'query:region' -> 'region' for a friendlier sentence."""
    if change.field and ":" in change.field:
        return change.field.split(":", 1)[1]
    return change.field or "parameter"


def _values(values) -> str:
    return ", ".join(str(value) for value in values or [])


def _sunset_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Severity rules that depend on the change
# ---------------------------------------------------------------------------


def _added_required(change: Change, today: date) -> str:
    return "breaking" if change.info.get("new_required") else "safe"


def _request_required(change: Change, today: date) -> str:
    return "breaking" if change.new is True else "safe"


def _response_required(change: Change, today: date) -> str:
    return "breaking" if change.new is False else "safe"


def _response_field_removed_severity(change: Change, today: date) -> str:
    sunset = _sunset_date(change.info.get("old_sunset"))
    if sunset is not None and sunset <= today:
        return "warning"
    return "breaking"


def _response_removed_severity(change: Change, today: date) -> str:
    return "breaking" if str(change.status_code or "").startswith("2") else "warning"


# ---------------------------------------------------------------------------
# Reason builders
# ---------------------------------------------------------------------------


def _endpoint_removed_reason(change: Change, today: date) -> str:
    return (
        f"Endpoint {_endpoint_label(change)} was removed. "
        "Clients that call it will break."
    )


def _endpoint_added_reason(change: Change, today: date) -> str:
    return (
        f"Endpoint {_endpoint_label(change)} was added. "
        "Existing clients are unaffected."
    )


def _endpoint_deprecated_reason(change: Change, today: date) -> str:
    return (
        f"Endpoint {_endpoint_label(change)} changed its deprecated flag "
        f"from {change.old} to {change.new}."
    )


def _param_added_reason(change: Change, today: date) -> str:
    name = _param(change)
    if change.info.get("new_required"):
        return (
            f"Required parameter {name} was added to {_endpoint_label(change)}. "
            "Clients that do not send it will break."
        )
    return (
        f"Optional parameter {name} was added to {_endpoint_label(change)}. "
        "Clients are unaffected."
    )


def _param_removed_reason(change: Change, today: date) -> str:
    return (
        f"Parameter {_param(change)} was removed from {_endpoint_label(change)}. "
        "Clients that send it may need updating."
    )


def _param_required_reason(change: Change, today: date) -> str:
    name = _param(change)
    if change.new is True:
        return (
            f"Parameter {name} became required on {_endpoint_label(change)}. "
            "Clients that leave it out will break."
        )
    return (
        f"Parameter {name} became optional on {_endpoint_label(change)}. "
        "Clients that send it are unaffected."
    )


def _param_type_reason(change: Change, today: date) -> str:
    return (
        f"Parameter {_param(change)} changed type from {change.old} to "
        f"{change.new} on {_endpoint_label(change)}. "
        "Clients that send the old type will break."
    )


def _param_enum_removed_reason(change: Change, today: date) -> str:
    return (
        f"Enum values {_values(change.old)} were removed from parameter "
        f"{_param(change)} on {_endpoint_label(change)}. "
        "Clients that send them will break."
    )


def _param_enum_added_reason(change: Change, today: date) -> str:
    return (
        f"Enum values {_values(change.new)} were added to parameter "
        f"{_param(change)} on {_endpoint_label(change)}. "
        "Clients that send the old values are unaffected."
    )


def _request_body_added_reason(change: Change, today: date) -> str:
    if change.info.get("new_required"):
        return (
            f"A required request body was added to {_endpoint_label(change)}. "
            "Clients that send no body will break."
        )
    return (
        f"An optional request body was added to {_endpoint_label(change)}. "
        "Clients are unaffected."
    )


def _request_body_removed_reason(change: Change, today: date) -> str:
    return (
        f"The request body was removed from {_endpoint_label(change)}. "
        "Clients that send a body may need updating."
    )


def _request_body_required_reason(change: Change, today: date) -> str:
    if change.new is True:
        return (
            f"The request body on {_endpoint_label(change)} became required. "
            "Clients that send no body will break."
        )
    return (
        f"The request body on {_endpoint_label(change)} became optional. "
        "Clients that send a body are unaffected."
    )


def _request_field_added_reason(change: Change, today: date) -> str:
    if change.info.get("new_required"):
        return (
            f"Request field {_field(change)} was added as required to "
            f"{_endpoint_label(change)}. Clients that do not send it will break."
        )
    return (
        f"Optional request field {_field(change)} was added to "
        f"{_endpoint_label(change)}. Clients are unaffected."
    )


def _request_field_removed_reason(change: Change, today: date) -> str:
    return (
        f"Request field {_field(change)} was removed from "
        f"{_endpoint_label(change)}. Clients that send it may need updating."
    )


def _request_field_required_reason(change: Change, today: date) -> str:
    if change.new is True:
        return (
            f"Request field {_field(change)} became required on "
            f"{_endpoint_label(change)}. Clients that leave it out will break."
        )
    return (
        f"Request field {_field(change)} became optional on "
        f"{_endpoint_label(change)}. Clients that send it are unaffected."
    )


def _request_field_type_reason(change: Change, today: date) -> str:
    return (
        f"Request field {_field(change)} changed type from {change.old} to "
        f"{change.new} on {_endpoint_label(change)}. "
        "Clients that send the old type will break."
    )


def _request_field_format_reason(change: Change, today: date) -> str:
    return (
        f"Request field {_field(change)} changed format from {change.old} to "
        f"{change.new} on {_endpoint_label(change)}. "
        "Clients should check the new format."
    )


def _request_field_enum_removed_reason(change: Change, today: date) -> str:
    return (
        f"Enum values {_values(change.old)} were removed from request field "
        f"{_field(change)} on {_endpoint_label(change)}. "
        "Clients that send them will break."
    )


def _request_field_enum_added_reason(change: Change, today: date) -> str:
    return (
        f"Enum values {_values(change.new)} were added to request field "
        f"{_field(change)} on {_endpoint_label(change)}. "
        "Clients that send the old values are unaffected."
    )


def _deprecated_reason(change: Change, today: date) -> str:
    sunset = change.info.get("old_sunset")
    text = (
        f"Field {_field(change)} on {_endpoint_label(change)} changed its "
        f"deprecated flag from {change.old} to {change.new}."
    )
    if sunset:
        text += f" Its sunset date is {sunset}."
    return text


def _sunset_reason(change: Change, today: date) -> str:
    return (
        f"Field {_field(change)} on {_endpoint_label(change)} changed its "
        f"sunset date from {change.old} to {change.new}."
    )


def _response_field_added_reason(change: Change, today: date) -> str:
    return (
        f"Response field {_field(change)} was added to "
        f"{_endpoint_label(change)}. Clients are unaffected."
    )


def _response_field_removed_reason(change: Change, today: date) -> str:
    sunset = _sunset_date(change.info.get("old_sunset"))
    if sunset is not None and sunset <= today:
        return (
            f"Response field {_field(change)} was removed from "
            f"{_endpoint_label(change)} after its sunset date "
            f"{change.info['old_sunset']}. Clients should already have migrated."
        )
    if sunset is not None:
        return (
            f"Response field {_field(change)} was removed early from "
            f"{_endpoint_label(change)}, before its sunset date "
            f"{change.info['old_sunset']}. Clients that read it will break."
        )
    return (
        f"Response field {_field(change)} was removed from "
        f"{_endpoint_label(change)}. Clients that read it will break."
    )


def _response_field_required_reason(change: Change, today: date) -> str:
    if change.new is False:
        return (
            f"Response field {_field(change)} became optional on "
            f"{_endpoint_label(change)}. Clients may now get no value and break."
        )
    return (
        f"Response field {_field(change)} became required on "
        f"{_endpoint_label(change)}. Clients that handled a missing value are unaffected."
    )


def _response_field_type_reason(change: Change, today: date) -> str:
    return (
        f"Response field {_field(change)} changed type from {change.old} to "
        f"{change.new} on {_endpoint_label(change)}. "
        "Clients that read the old type will break."
    )


def _response_field_format_reason(change: Change, today: date) -> str:
    return (
        f"Response field {_field(change)} changed format from {change.old} to "
        f"{change.new} on {_endpoint_label(change)}. "
        "Clients should check the new format."
    )


def _response_field_enum_added_reason(change: Change, today: date) -> str:
    return (
        f"Response field {_field(change)} gained new enum values "
        f"{_values(change.new)} on {_endpoint_label(change)}. "
        "Strict clients may fail on an unknown value."
    )


def _response_field_enum_removed_reason(change: Change, today: date) -> str:
    return (
        f"Enum values {_values(change.old)} were removed from response field "
        f"{_field(change)} on {_endpoint_label(change)}. Clients are unaffected."
    )


def _response_added_reason(change: Change, today: date) -> str:
    return (
        f"Response {change.status_code} was added to "
        f"{_endpoint_label(change)}. Clients may need to handle it."
    )


def _response_removed_reason(change: Change, today: date) -> str:
    if str(change.status_code or "").startswith("2"):
        return (
            f"Response {change.status_code} was removed from "
            f"{_endpoint_label(change)}. Clients that read it will break."
        )
    return (
        f"Response {change.status_code} was removed from "
        f"{_endpoint_label(change)}. Clients that rely on it may need updating."
    )


# ---------------------------------------------------------------------------
# The rule table
# ---------------------------------------------------------------------------

RULES: dict[tuple[str, str], Rule] = {
    ("endpoint", "endpoint_removed"): Rule(
        "endpoint-removed", "breaking", _endpoint_removed_reason
    ),
    ("endpoint", "endpoint_added"): Rule(
        "endpoint-added", "safe", _endpoint_added_reason
    ),
    ("endpoint", "deprecated_changed"): Rule(
        "endpoint-deprecated-changed", "warning", _endpoint_deprecated_reason
    ),
    ("param", "param_added"): Rule(
        "param-added", _added_required, _param_added_reason
    ),
    ("param", "param_removed"): Rule(
        "param-removed", "warning", _param_removed_reason
    ),
    ("param", "required_changed"): Rule(
        "param-required-changed", _request_required, _param_required_reason
    ),
    ("param", "type_changed"): Rule(
        "param-type-changed", "breaking", _param_type_reason
    ),
    ("param", "enum_values_removed"): Rule(
        "param-enum-values-removed", "breaking", _param_enum_removed_reason
    ),
    ("param", "enum_values_added"): Rule(
        "param-enum-values-added", "safe", _param_enum_added_reason
    ),
    ("request-body", "request_body_added"): Rule(
        "request-body-added", _added_required, _request_body_added_reason
    ),
    ("request-body", "request_body_removed"): Rule(
        "request-body-removed", "warning", _request_body_removed_reason
    ),
    ("request-body", "required_changed"): Rule(
        "request-body-required-changed", _request_required, _request_body_required_reason
    ),
    ("request-field", "field_added"): Rule(
        "request-field-added", _added_required, _request_field_added_reason
    ),
    ("request-field", "field_removed"): Rule(
        "request-field-removed", "warning", _request_field_removed_reason
    ),
    ("request-field", "required_changed"): Rule(
        "request-field-required-changed",
        _request_required,
        _request_field_required_reason,
    ),
    ("request-field", "type_changed"): Rule(
        "request-field-type-changed", "breaking", _request_field_type_reason
    ),
    ("request-field", "format_changed"): Rule(
        "request-field-format-changed", "warning", _request_field_format_reason
    ),
    ("request-field", "enum_values_removed"): Rule(
        "request-field-enum-values-removed",
        "breaking",
        _request_field_enum_removed_reason,
    ),
    ("request-field", "enum_values_added"): Rule(
        "request-field-enum-values-added",
        "safe",
        _request_field_enum_added_reason,
    ),
    ("request-field", "deprecated_changed"): Rule(
        "request-field-deprecated-changed", "warning", _deprecated_reason
    ),
    ("request-field", "sunset_changed"): Rule(
        "request-field-sunset-changed", "warning", _sunset_reason
    ),
    ("response-field", "field_added"): Rule(
        "response-field-added", "safe", _response_field_added_reason
    ),
    ("response-field", "field_removed"): Rule(
        "response-field-removed",
        _response_field_removed_severity,
        _response_field_removed_reason,
    ),
    ("response-field", "required_changed"): Rule(
        "response-field-required-changed",
        _response_required,
        _response_field_required_reason,
    ),
    ("response-field", "type_changed"): Rule(
        "response-field-type-changed", "breaking", _response_field_type_reason
    ),
    ("response-field", "format_changed"): Rule(
        "response-field-format-changed", "warning", _response_field_format_reason
    ),
    ("response-field", "enum_values_added"): Rule(
        "response-field-enum-values-added",
        "warning",
        _response_field_enum_added_reason,
    ),
    ("response-field", "enum_values_removed"): Rule(
        "response-field-enum-values-removed",
        "safe",
        _response_field_enum_removed_reason,
    ),
    ("response-field", "deprecated_changed"): Rule(
        "response-field-deprecated-changed", "warning", _deprecated_reason
    ),
    ("response-field", "sunset_changed"): Rule(
        "response-field-sunset-changed", "warning", _sunset_reason
    ),
    ("response", "response_added"): Rule(
        "response-added", "warning", _response_added_reason
    ),
    ("response", "response_removed"): Rule(
        "response-removed", _response_removed_severity, _response_removed_reason
    ),
}

_FALLBACK_RULE = Rule(
    "unclassified-change",
    "warning",
    lambda change, today: (
        f"An unclassified change ({change.kind}) was found on "
        f"{_endpoint_label(change)}. Review it manually."
    ),
)


def _scope(change: Change) -> str:
    """Which part of the rule table applies to this change."""
    if change.location == "endpoint":
        return "endpoint"
    if change.location == "param":
        return "param"
    if change.location == "request":
        if change.field is None and change.kind in {
            "request_body_added",
            "request_body_removed",
            "required_changed",
        }:
            return "request-body"
        return "request-field"
    if change.location == "response":
        return "response" if change.field is None else "response-field"
    return change.location


def _rule_for(change: Change) -> Rule:
    return RULES.get((_scope(change), change.kind), _FALLBACK_RULE)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def classify(change: Change, today: date) -> Classification:
    """Severity, rule id and a one-sentence reason for one change."""
    rule = _rule_for(change)
    severity = rule.severity(change, today) if callable(rule.severity) else rule.severity
    reason = rule.reason(change, today)
    if change.internal and severity == "breaking":
        severity = "warning"
        reason = (
            f"{reason} This change is marked internal, so only the owning "
            "team uses it."
        )
    return Classification(severity=severity, rule=rule.rule, reason=reason)


def classify_all(changes: list[Change], today: date) -> list[tuple[Change, Classification]]:
    return [(change, classify(change, today)) for change in changes]


def check_version_bump(
    old_version: str, new_version: str, has_breaking: bool
) -> Classification | None:
    """Warn when breaking changes ship without a major version bump."""
    if not has_breaking:
        return None
    old_major = parse_version(old_version)[0]
    new_major = parse_version(new_version)[0]
    if new_major > old_major:
        return None
    return Classification(
        severity="warning",
        rule="semver-minor-breaking",
        reason=(
            f"Breaking changes were published with a {old_version} to "
            f"{new_version} bump instead of a major version bump. "
            "Consumers may not expect breaking changes without a major bump."
        ),
    )
