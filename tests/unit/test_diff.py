"""Unit tests for diff: the kit specs in publish order, plus small fixtures."""
from pathlib import Path

from app.core.diff import diff_specs
from app.core.normalize import load_spec, normalize_spec, validate_spec

REPO_ROOT = Path(__file__).resolve().parents[2]
KIT = REPO_ROOT / "kit" / "specs"


def _normalized(relative: str) -> dict:
    text = (KIT / relative).read_text(encoding="utf-8")
    spec = load_spec(text)
    validate_spec(spec)
    return normalize_spec(spec)


def _diff(old: dict, new: dict) -> list:
    return diff_specs(old, new)


def _summary(changes: list) -> list[tuple]:
    return [
        (
            change.endpoint,
            change.location,
            change.status_code,
            change.field,
            change.kind,
        )
        for change in changes
    ]


def _from_text(old_text: str, new_text: str) -> list:
    old_spec = load_spec(old_text)
    new_spec = load_spec(new_text)
    validate_spec(old_spec)
    validate_spec(new_spec)
    return _diff(normalize_spec(old_spec), normalize_spec(new_spec))


def _object_spec(paths_yaml: str) -> str:
    return f"""
openapi: 3.0.3
info: {{title: T, version: 1.0.0}}
paths:
{paths_yaml}
"""


# ---------------------------------------------------------------------------
# Kit specs
# ---------------------------------------------------------------------------


def test_quote_1_to_2_has_no_changes():
    old = _normalized("quote-api/1-initial.yaml")
    new = _normalized("quote-api/2-reorganised.yaml")
    assert _diff(old, new) == []


def test_quote_2_to_3_changes():
    changes = _diff(
        _normalized("quote-api/2-reorganised.yaml"),
        _normalized("quote-api/3-price-lines.yaml"),
    )
    assert _summary(changes) == [
        ("DELETE /internal/pricing-cache", "param", None, "query:region", "param_added"),
        ("GET /quotes/{}", "response", "200", "premium.breakdown[].amount", "field_removed"),
        ("GET /quotes/{}", "response", "200", "premium.breakdown[].value", "field_added"),
        ("GET /quotes/{}", "response", "200", "status", "enum_values_added"),
        ("POST /quotes", "response", "201", "premium.breakdown[].amount", "field_removed"),
        ("POST /quotes", "response", "201", "premium.breakdown[].value", "field_added"),
        ("POST /quotes", "response", "201", "status", "enum_values_added"),
    ]
    param = next(change for change in changes if change.field == "query:region")
    assert param.internal is True
    assert param.info["new_required"] is True
    status = next(change for change in changes if change.field == "status")
    assert status.new == ["EXPIRED"]


def test_quote_3_to_4_changes():
    changes = _diff(
        _normalized("quote-api/3-price-lines.yaml"),
        _normalized("quote-api/4-ncb-and-taxes.yaml"),
    )
    assert _summary(changes) == [
        ("GET /quotes/{}", "response", "200", "premium.taxes", "field_added"),
        ("POST /quotes", "request", None, "rider.ncbPercent", "field_added"),
        ("POST /quotes", "response", "201", "premium.taxes", "field_added"),
    ]
    ncb = next(change for change in changes if change.field == "rider.ncbPercent")
    assert ncb.info["new_required"] is False


def test_policy_1_to_2_deprecations_and_sunsets():
    changes = _diff(
        _normalized("policy-api/1-initial.yaml"),
        _normalized("policy-api/2-deprecations.yaml"),
    )
    fields = {"legacyCode", "[].legacyCode", "holder.phone", "[].holder.phone"}
    assert {change.field for change in changes} == fields
    assert {change.kind for change in changes} == {"deprecated_changed", "sunset_changed"}
    for field in fields:
        kinds = {change.kind for change in changes if change.field == field}
        assert kinds == {"deprecated_changed", "sunset_changed"}


def test_policy_2_to_3_removes_old_fields_with_sunset_info():
    changes = _diff(
        _normalized("policy-api/2-deprecations.yaml"),
        _normalized("policy-api/3-remove-old-fields.yaml"),
    )
    assert all(change.kind == "field_removed" for change in changes)
    assert {change.field for change in changes} == {
        "legacyCode",
        "[].legacyCode",
        "holder.phone",
        "[].holder.phone",
    }
    for change in changes:
        expected = "2026-11-01" if "legacyCode" in change.field else "2027-06-30"
        assert change.info["old_sunset"] == expected


def test_policy_3_to_4_endorsement_required_and_enum_removed():
    changes = _diff(
        _normalized("policy-api/3-remove-old-fields.yaml"),
        _normalized("policy-api/4-endorsement-rules.yaml"),
    )
    assert len(changes) == 2
    requested = changes[0]
    assert requested.kind == "field_added"
    assert requested.location == "request"
    assert requested.field == "requestedBy"
    assert requested.info["new_required"] is True
    enum_removed = changes[1]
    assert enum_removed.kind == "enum_values_removed"
    assert enum_removed.field == "type"
    assert enum_removed.old == ["VEHICLE"]
    assert enum_removed.new is None


def test_claims_1_to_3_channel_and_assessor_notes():
    changes = _diff(
        _normalized("claims-api/1-initial.yaml"),
        _normalized("claims-api/3-channel-and-notes-fixed.yaml"),
    )
    assert _summary(changes) == [
        ("GET /claims/{}", "response", "200", "assessorNotes", "field_added"),
        ("POST /claims", "request", None, "channel", "field_added"),
        ("POST /claims", "response", "201", "assessorNotes", "field_added"),
    ]


def test_claims_3_to_4_approval_later_and_new_status():
    changes = _diff(
        _normalized("claims-api/3-channel-and-notes-fixed.yaml"),
        _normalized("claims-api/4-approval-later.yaml"),
    )
    assert _summary(changes) == [
        ("GET /claims/{}", "response", "200", "amountApproved", "required_changed"),
        ("POST /claims", "response", "201", "amountApproved", "required_changed"),
        ("POST /claims", "response", "202", None, "response_added"),
    ]
    for change in changes:
        if change.kind == "required_changed":
            assert change.old is True
            assert change.new is False


# ---------------------------------------------------------------------------
# Small hand-written specs
# ---------------------------------------------------------------------------


def test_type_change():
    old = _object_spec(
        """
  /things:
    get:
      responses:
        '200':
          description: ok
          content:
            application/json:
              schema:
                type: object
                properties:
                  count: {type: string}
"""
    )
    new = old.replace("count: {type: string}", "count: {type: integer}")
    changes = _from_text(old, new)
    assert _summary(changes) == [
        ("GET /things", "response", "200", "count", "type_changed"),
    ]
    assert changes[0].old == "string"
    assert changes[0].new == "integer"


def test_endpoint_removed():
    old = _object_spec(
        """
  /things:
    get:
      responses:
        '200': {description: ok}
"""
    )
    new = _object_spec("  {}")
    changes = _from_text(old, new)
    assert _summary(changes) == [
        ("GET /things", "endpoint", None, None, "endpoint_removed"),
    ]
    assert changes[0].path == "/things"


def test_param_removed():
    old = _object_spec(
        """
  /things:
    get:
      parameters:
        - {name: region, in: query, required: true, schema: {type: string}}
      responses:
        '200': {description: ok}
"""
    )
    new = _object_spec(
        """
  /things:
    get:
      responses:
        '200': {description: ok}
"""
    )
    changes = _from_text(old, new)
    assert _summary(changes) == [
        ("GET /things", "param", None, "query:region", "param_removed"),
    ]


def test_request_body_becoming_required():
    old = _object_spec(
        """
  /things:
    post:
      requestBody:
        required: false
        content:
          application/json:
            schema: {type: object, properties: {name: {type: string}}}
      responses:
        '200': {description: ok}
"""
    )
    new = old.replace("required: false", "required: true")
    changes = _from_text(old, new)
    assert _summary(changes) == [
        ("POST /things", "request", None, None, "required_changed"),
    ]
    assert changes[0].old is False
    assert changes[0].new is True


def test_object_field_becoming_required():
    old = _object_spec(
        """
  /things:
    post:
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                vehicle:
                  type: object
                  properties:
                    make: {type: string}
      responses:
        '200': {description: ok}
"""
    )
    new = old.replace(
        "              properties:\n                vehicle:",
        "              required: [vehicle]\n              properties:\n                vehicle:",
    )
    changes = _from_text(old, new)
    assert _summary(changes) == [
        ("POST /things", "request", None, "vehicle", "required_changed"),
    ]
    assert changes[0].old is False
    assert changes[0].new is True


def test_parent_removed_reports_only_the_parent():
    old = _object_spec(
        """
  /things:
    get:
      responses:
        '200':
          description: ok
          content:
            application/json:
              schema:
                type: object
                properties:
                  premium:
                    type: object
                    properties:
                      total: {type: number}
                      breakdown:
                        type: array
                        items:
                          type: object
                          properties:
                            amount: {type: number}
"""
    )
    new = _object_spec(
        """
  /things:
    get:
      responses:
        '200':
          description: ok
          content:
            application/json:
              schema: {type: object}
"""
    )
    changes = _from_text(old, new)
    assert _summary(changes) == [
        ("GET /things", "response", "200", "premium", "field_removed"),
    ]
