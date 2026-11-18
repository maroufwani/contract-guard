"""Unit tests for the classifier: one rule at a time, then the kit versions."""
from datetime import date
from pathlib import Path

from app.core.classify import (
    check_version_bump,
    classify,
    classify_all,
)
from app.core.diff import Change, diff_specs
from app.core.normalize import load_spec, normalize_spec, validate_spec

REPO_ROOT = Path(__file__).resolve().parents[2]
KIT = REPO_ROOT / "kit" / "specs"
TODAY = date(2026, 11, 18)


def _change(**overrides) -> Change:
    defaults = {
        "endpoint": "POST /things",
        "path": "/things",
        "location": "response",
        "status_code": "200",
        "field": "amount",
        "kind": "field_added",
        "old": None,
        "new": "number",
        "internal": False,
        "info": {},
    }
    defaults.update(overrides)
    return Change(**defaults)


def _classified(**overrides):
    return classify(_change(**overrides), TODAY)


def _normalized(relative: str) -> dict:
    text = (KIT / relative).read_text(encoding="utf-8")
    spec = load_spec(text)
    validate_spec(spec)
    return normalize_spec(spec)


def _classified_kit(old: str, new: str, today=TODAY):
    changes = diff_specs(_normalized(old), _normalized(new))
    return classify_all(changes, today)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def test_endpoint_removed_is_breaking():
    result = _classified(
        location="endpoint",
        status_code=None,
        field=None,
        kind="endpoint_removed",
        old=True,
        new=None,
    )
    assert (result.severity, result.rule) == ("breaking", "endpoint-removed")
    assert "POST /things" in result.reason


def test_endpoint_added_is_safe():
    result = _classified(
        location="endpoint",
        status_code=None,
        field=None,
        kind="endpoint_added",
        old=None,
        new=True,
    )
    assert (result.severity, result.rule) == ("safe", "endpoint-added")


def test_endpoint_deprecated_changed_is_warning():
    result = _classified(
        location="endpoint",
        status_code=None,
        field=None,
        kind="deprecated_changed",
        old=False,
        new=True,
    )
    assert (result.severity, result.rule) == (
        "warning",
        "endpoint-deprecated-changed",
    )


# ---------------------------------------------------------------------------
# Parameters (request side)
# ---------------------------------------------------------------------------


def test_param_added_required_is_breaking_and_optional_is_safe():
    required = _classified(
        location="param",
        status_code=None,
        field="query:region",
        kind="param_added",
        new="string",
        info={"new_required": True},
    )
    optional = _classified(
        location="param",
        status_code=None,
        field="query:region",
        kind="param_added",
        new="string",
        info={"new_required": False},
    )
    assert required.severity == "breaking"
    assert "Required parameter region" in required.reason
    assert optional.severity == "safe"
    assert optional.rule == "param-added"


def test_param_removed_is_warning():
    result = _classified(
        location="param",
        status_code=None,
        field="query:region",
        kind="param_removed",
        old="string",
        new=None,
    )
    assert (result.severity, result.rule) == ("warning", "param-removed")
    assert "region" in result.reason


def test_param_required_changed_stricter_is_breaking_relaxed_is_safe():
    stricter = _classified(
        location="param",
        status_code=None,
        field="query:region",
        kind="required_changed",
        old=False,
        new=True,
    )
    relaxed = _classified(
        location="param",
        status_code=None,
        field="query:region",
        kind="required_changed",
        old=True,
        new=False,
    )
    assert stricter.severity == "breaking"
    assert relaxed.severity == "safe"


def test_param_type_changed_is_breaking():
    result = _classified(
        location="param",
        status_code=None,
        field="query:limit",
        kind="type_changed",
        old="string",
        new="integer",
    )
    assert (result.severity, result.rule) == ("breaking", "param-type-changed")


def test_param_enum_removed_is_breaking_added_is_safe():
    removed = _classified(
        location="param",
        status_code=None,
        field="query:sort",
        kind="enum_values_removed",
        old=["asc"],
        new=None,
    )
    added = _classified(
        location="param",
        status_code=None,
        field="query:sort",
        kind="enum_values_added",
        old=None,
        new=["desc"],
    )
    assert removed.severity == "breaking"
    assert added.severity == "safe"
    assert added.rule == "param-enum-values-added"


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


def test_request_body_added_required_is_breaking_optional_is_safe():
    required = _classified(
        location="request",
        status_code=None,
        field=None,
        kind="request_body_added",
        new=True,
        info={"new_required": True},
    )
    optional = _classified(
        location="request",
        status_code=None,
        field=None,
        kind="request_body_added",
        new=True,
        info={"new_required": False},
    )
    assert required.severity == "breaking"
    assert required.rule == "request-body-added"
    assert optional.severity == "safe"


def test_request_body_removed_is_warning():
    result = _classified(
        location="request",
        status_code=None,
        field=None,
        kind="request_body_removed",
        old=True,
        new=None,
    )
    assert (result.severity, result.rule) == ("warning", "request-body-removed")


def test_request_body_required_changed_stricter_is_breaking():
    stricter = _classified(
        location="request",
        status_code=None,
        field=None,
        kind="required_changed",
        old=False,
        new=True,
    )
    relaxed = _classified(
        location="request",
        status_code=None,
        field=None,
        kind="required_changed",
        old=True,
        new=False,
    )
    assert stricter.severity == "breaking"
    assert stricter.rule == "request-body-required-changed"
    assert relaxed.severity == "safe"


# ---------------------------------------------------------------------------
# Request fields
# ---------------------------------------------------------------------------


def test_request_field_added_required_is_breaking_optional_is_safe():
    required = _classified(
        location="request",
        field="rider.ncbPercent",
        kind="field_added",
        new="number",
        info={"new_required": True},
    )
    optional = _classified(
        location="request",
        field="rider.ncbPercent",
        kind="field_added",
        new="number",
        info={"new_required": False},
    )
    assert required.severity == "breaking"
    assert required.rule == "request-field-added"
    assert optional.severity == "safe"


def test_request_field_removed_is_warning():
    result = _classified(
        location="request",
        field="rider.old",
        kind="field_removed",
        old="string",
        new=None,
    )
    assert (result.severity, result.rule) == ("warning", "request-field-removed")


def test_request_field_required_changed_stricter_is_breaking():
    stricter = _classified(
        location="request",
        field="vehicle",
        kind="required_changed",
        old=False,
        new=True,
    )
    relaxed = _classified(
        location="request",
        field="vehicle",
        kind="required_changed",
        old=True,
        new=False,
    )
    assert stricter.severity == "breaking"
    assert stricter.rule == "request-field-required-changed"
    assert relaxed.severity == "safe"


def test_request_field_type_changed_is_breaking():
    result = _classified(
        location="request",
        field="vehicle.year",
        kind="type_changed",
        old="string",
        new="integer",
    )
    assert (result.severity, result.rule) == (
        "breaking",
        "request-field-type-changed",
    )


def test_request_field_format_changed_is_warning():
    result = _classified(
        location="request",
        field="holder.email",
        kind="format_changed",
        old=None,
        new="email",
    )
    assert (result.severity, result.rule) == (
        "warning",
        "request-field-format-changed",
    )


def test_request_field_enum_removed_is_breaking_added_is_safe():
    removed = _classified(
        location="request",
        field="type",
        kind="enum_values_removed",
        old=["VEHICLE"],
        new=None,
    )
    added = _classified(
        location="request",
        field="type",
        kind="enum_values_added",
        old=None,
        new=["PROPERTY"],
    )
    assert removed.severity == "breaking"
    assert added.severity == "safe"


def test_request_field_deprecated_and_sunset_changes_are_warnings():
    deprecated = _classified(
        location="request",
        field="legacyCode",
        kind="deprecated_changed",
        old=False,
        new=True,
        info={"old_sunset": "2026-11-01"},
    )
    sunset = _classified(
        location="request",
        field="legacyCode",
        kind="sunset_changed",
        old=None,
        new="2026-11-01",
    )
    assert deprecated.severity == "warning"
    assert deprecated.rule == "request-field-deprecated-changed"
    assert "2026-11-01" in deprecated.reason
    assert sunset.severity == "warning"
    assert sunset.rule == "request-field-sunset-changed"


# ---------------------------------------------------------------------------
# Response fields
# ---------------------------------------------------------------------------


def test_response_field_added_is_safe():
    result = _classified(kind="field_added", new="number")
    assert (result.severity, result.rule) == ("safe", "response-field-added")


def test_response_field_removed_is_breaking_without_sunset():
    result = _classified(
        kind="field_removed",
        old="number",
        new=None,
    )
    assert (result.severity, result.rule) == (
        "breaking",
        "response-field-removed",
    )
    assert "amount" in result.reason


def test_response_field_removed_after_sunset_is_warning():
    result = _classified(
        kind="field_removed",
        old="number",
        new=None,
        info={"old_sunset": "2026-11-01"},
    )
    assert result.severity == "warning"
    assert "after its sunset date" in result.reason


def test_response_field_removed_before_sunset_is_breaking():
    result = _classified(
        kind="field_removed",
        old="number",
        new=None,
        info={"old_sunset": "2027-06-30"},
    )
    assert result.severity == "breaking"
    assert "removed early" in result.reason


def test_response_field_required_changed_to_optional_is_breaking():
    stricter = _classified(
        kind="required_changed",
        old=False,
        new=True,
    )
    relaxed = _classified(
        kind="required_changed",
        old=True,
        new=False,
    )
    assert relaxed.severity == "breaking"
    assert relaxed.rule == "response-field-required-changed"
    assert stricter.severity == "safe"


def test_response_field_type_changed_is_breaking():
    result = _classified(kind="type_changed", old="string", new="integer")
    assert (result.severity, result.rule) == (
        "breaking",
        "response-field-type-changed",
    )


def test_response_field_format_changed_is_warning():
    result = _classified(kind="format_changed", old="date", new="date-time")
    assert (result.severity, result.rule) == (
        "warning",
        "response-field-format-changed",
    )


def test_response_field_enum_added_is_warning_removed_is_safe():
    added = _classified(
        field="status",
        kind="enum_values_added",
        old=None,
        new=["EXPIRED"],
    )
    removed = _classified(
        field="status",
        kind="enum_values_removed",
        old=["EXPIRED"],
        new=None,
    )
    assert (added.severity, added.rule) == (
        "warning",
        "response-field-enum-values-added",
    )
    assert removed.severity == "safe"
    assert removed.rule == "response-field-enum-values-removed"


def test_response_field_deprecated_and_sunset_changes_are_warnings():
    deprecated = _classified(
        field="legacyCode",
        kind="deprecated_changed",
        old=False,
        new=True,
        info={"old_sunset": "2026-11-01"},
    )
    sunset = _classified(
        field="legacyCode",
        kind="sunset_changed",
        old=None,
        new="2026-11-01",
    )
    assert deprecated.severity == "warning"
    assert deprecated.rule == "response-field-deprecated-changed"
    assert sunset.severity == "warning"
    assert sunset.rule == "response-field-sunset-changed"


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


def test_response_added_is_warning():
    result = _classified(
        location="response",
        status_code="202",
        field=None,
        kind="response_added",
        old=None,
        new=True,
    )
    assert (result.severity, result.rule) == ("warning", "response-added")


def test_response_removed_2xx_is_breaking_others_are_warning():
    ok = _classified(
        location="response",
        status_code="200",
        field=None,
        kind="response_removed",
        old=True,
        new=None,
    )
    error = _classified(
        location="response",
        status_code="404",
        field=None,
        kind="response_removed",
        old=True,
        new=None,
    )
    assert ok.severity == "breaking"
    assert ok.rule == "response-removed"
    assert error.severity == "warning"


# ---------------------------------------------------------------------------
# Cross-cutting rules
# ---------------------------------------------------------------------------


def test_internal_breaking_change_becomes_warning():
    result = _classified(
        location="param",
        status_code=None,
        field="query:region",
        kind="param_added",
        new="string",
        internal=True,
        info={"new_required": True},
    )
    assert result.severity == "warning"
    assert result.rule == "param-added"
    assert "internal" in result.reason


def test_semver_minor_breaking_warns():
    result = check_version_bump("2.0.0", "2.1.0", True)
    assert result is not None
    assert (result.severity, result.rule) == ("warning", "semver-minor-breaking")


def test_semver_major_bump_or_no_breaking_has_no_warning():
    assert check_version_bump("1.0.0", "2.0.0", True) is None
    assert check_version_bump("2.0.0", "2.1.0", False) is None


# ---------------------------------------------------------------------------
# Kit checks
# ---------------------------------------------------------------------------


def test_quote_2_to_3_classification():
    classified = _classified_kit(
        "quote-api/2-reorganised.yaml", "quote-api/3-price-lines.yaml"
    )
    by_field = {}
    for change, result in classified:
        by_field.setdefault(change.field, []).append((change, result))

    amount = by_field["premium.breakdown[].amount"]
    assert {change.endpoint for change, _ in amount} == {
        "GET /quotes/{}",
        "POST /quotes",
    }
    assert all(result.severity == "breaking" for _, result in amount)

    expired = [result for change, result in by_field["status"]]
    assert all(result.severity == "warning" for result in expired)

    region_change, region_result = by_field["query:region"][0]
    assert region_change.internal is True
    assert region_result.severity == "warning"

    value = by_field["premium.breakdown[].value"]
    assert all(result.severity == "safe" for _, result in value)


def test_quote_3_to_4_is_all_safe():
    classified = _classified_kit(
        "quote-api/3-price-lines.yaml", "quote-api/4-ncb-and-taxes.yaml"
    )
    assert classified
    assert all(result.severity == "safe" for _, result in classified)


def test_policy_1_to_2_is_only_warnings():
    classified = _classified_kit(
        "policy-api/1-initial.yaml", "policy-api/2-deprecations.yaml"
    )
    assert classified
    assert all(result.severity == "warning" for _, result in classified)


def test_policy_2_to_3_uses_sunset_dates():
    classified = _classified_kit(
        "policy-api/2-deprecations.yaml", "policy-api/3-remove-old-fields.yaml"
    )
    legacy = [(change, result) for change, result in classified if "legacyCode" in change.field]
    phone = [(change, result) for change, result in classified if "holder.phone" in change.field]
    assert legacy and all(result.severity == "warning" for _, result in legacy)
    assert phone and all(result.severity == "breaking" for _, result in phone)

    early = _classified_kit(
        "policy-api/2-deprecations.yaml",
        "policy-api/3-remove-old-fields.yaml",
        today=date(2026, 10, 1),
    )
    early_legacy = [
        result for change, result in early if "legacyCode" in change.field
    ]
    assert early_legacy and all(result.severity == "breaking" for result in early_legacy)


def test_policy_3_to_4_breaking_and_semver():
    classified = _classified_kit(
        "policy-api/3-remove-old-fields.yaml", "policy-api/4-endorsement-rules.yaml"
    )
    requested = [
        result
        for change, result in classified
        if change.kind == "field_added" and change.field == "requestedBy"
    ]
    vehicle = [
        result for change, result in classified if change.kind == "enum_values_removed"
    ]
    assert requested and requested[0].severity == "breaking"
    assert vehicle and vehicle[0].severity == "breaking"

    semver = check_version_bump("2.0.0", "2.1.0", True)
    assert semver is not None
    assert semver.rule == "semver-minor-breaking"


def test_claims_3_to_4_breaking_and_response_added():
    classified = _classified_kit(
        "claims-api/3-channel-and-notes-fixed.yaml",
        "claims-api/4-approval-later.yaml",
    )
    amount = [
        (change, result)
        for change, result in classified
        if change.field == "amountApproved"
    ]
    assert {change.status_code for change, _ in amount} == {"200", "201"}
    assert all(result.severity == "breaking" for _, result in amount)

    added = [
        result
        for change, result in classified
        if change.kind == "response_added" and change.status_code == "202"
    ]
    assert added and added[0].severity == "warning"
    assert check_version_bump("1.0.0", "2.0.0", True) is None


def test_claims_1_to_3_and_quote_1_to_2_have_nothing_breaking():
    claims = _classified_kit(
        "claims-api/1-initial.yaml", "claims-api/3-channel-and-notes-fixed.yaml"
    )
    assert claims
    assert all(result.severity != "breaking" for _, result in claims)

    quote = _classified_kit(
        "quote-api/1-initial.yaml", "quote-api/2-reorganised.yaml"
    )
    assert all(result.severity != "breaking" for _, result in quote)
