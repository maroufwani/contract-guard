"""Unit tests for normalize: the kit specs are the test data, read in place."""
from pathlib import Path

import pytest

from app.core.errors import SpecError
from app.core.normalize import load_spec, normalize_spec, validate_spec

REPO_ROOT = Path(__file__).resolve().parents[2]
KIT = REPO_ROOT / "kit" / "specs"


def _normalized(relative: str) -> dict:
    text = (KIT / relative).read_text(encoding="utf-8")
    spec = load_spec(text)
    validate_spec(spec)
    return normalize_spec(spec)


def _contract(normalized: dict) -> dict:
    """Endpoints without the display path, which may change spelling ({id} -> {quoteId})."""
    return {
        key: {name: value for name, value in endpoint.items() if name != "path"}
        for key, endpoint in normalized["endpoints"].items()
    }


def test_quote_v1_and_v2_normalize_to_the_same_endpoints():
    """The most important test: the v2 restructure is not a contract change.

    v2 inlines Vehicle/Rider into QuoteRequest, renames Premium ->
    PremiumDetails and BreakdownItem -> PriceLine, and renames the path
    parameter {id} -> {quoteId}. The normalized endpoints must not notice.
    """
    v1 = _normalized("quote-api/1-initial.yaml")
    v2 = _normalized("quote-api/2-reorganised.yaml")
    assert v1["endpoints"]
    assert _contract(v1) == _contract(v2)
    # The endpoint keys are identical; only the display spelling moves.
    assert v1["endpoints"]["GET /quotes/{}"]["path"] == "/quotes/{id}"
    assert v2["endpoints"]["GET /quotes/{}"]["path"] == "/quotes/{quoteId}"
    assert set(v1["endpoints"]) == {
        "POST /quotes",
        "GET /quotes/{}",
        "DELETE /internal/pricing-cache",
    }
    assert v1["version"] == "1.0.0"
    assert v2["version"] == "1.1.0"


def test_quote_1_request_and_response_fields():
    normalized = _normalized("quote-api/1-initial.yaml")
    request = normalized["endpoints"]["POST /quotes"]["request"]
    assert request["required"] is True
    assert set(request["fields"]) == {
        "vehicle.make",
        "vehicle.model",
        "vehicle.cc",
        "rider.age",
        "rider.city",
    }
    assert request["fields"]["vehicle.make"]["required"] is True
    assert request["fields"]["rider.city"]["required"] is False

    response = normalized["endpoints"]["POST /quotes"]["responses"]["201"]["fields"]
    assert set(response) == {
        "quoteId",
        "status",
        "createdAt",
        "premium.total",
        "premium.currency",
        "premium.breakdown",
        "premium.breakdown[].label",
        "premium.breakdown[].amount",
    }
    assert response["status"]["enum"] == ["DRAFT", "FINAL"]
    assert response["premium.breakdown"]["type"] == "array"
    assert response["premium.breakdown[].amount"]["required"] is True


def test_quote_3_breakdown_value_not_amount():
    """The v2 -> v3 change is real: amount became value in the response."""
    normalized = _normalized("quote-api/3-price-lines.yaml")
    fields = normalized["endpoints"]["POST /quotes"]["responses"]["201"]["fields"]
    assert "premium.breakdown[].value" in fields
    assert "premium.breakdown[].amount" not in fields
    # Enums are stored sorted.
    assert fields["status"]["enum"] == ["DRAFT", "EXPIRED", "FINAL"]


def test_quote_3_internal_endpoint_keeps_required_region_query():
    normalized = _normalized("quote-api/3-price-lines.yaml")
    endpoint = normalized["endpoints"]["DELETE /internal/pricing-cache"]
    assert endpoint["internal"] is True
    assert endpoint["params"]["query:region"] == {
        "name": "region",
        "required": True,
        "type": "string",
        "enum": None,
    }
    assert endpoint["responses"]["204"]["fields"] == {}


def test_policy_2_search_response_is_a_top_level_array():
    normalized = _normalized("policy-api/2-deprecations.yaml")
    fields = normalized["endpoints"]["GET /policies"]["responses"]["200"]["fields"]
    assert fields["[]"] == {
        "type": "array",
        "format": None,
        "required": True,
        "enum": None,
        "deprecated": False,
        "sunset": None,
        "internal": False,
    }
    legacy = fields["[].legacyCode"]
    assert legacy["sunset"] == "2026-11-01"
    assert legacy["deprecated"] is True
    # holder.phone is deprecated with a sunset date as well.
    assert fields["[].holder.phone"]["sunset"] == "2027-06-30"


def test_claims_2_broken_ref_is_rejected_with_a_clear_message():
    with pytest.raises(SpecError) as excinfo:
        _normalized("claims-api/2-channel-and-notes.yaml")
    message = str(excinfo.value)
    assert "ClaimDocumnt" in message
    assert "#/components/schemas/ClaimDocumnt" in message


def test_recursive_schema_stops_with_type_recursive():
    text = """
openapi: 3.0.3
info: {title: Tree, version: 1.0.0}
paths:
  /trees:
    get:
      responses:
        '200':
          description: A tree
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Node'
components:
  schemas:
    Node:
      type: object
      required: [name]
      properties:
        name:
          type: string
        child:
          $ref: '#/components/schemas/Node'
"""
    spec = load_spec(text)
    validate_spec(spec)
    normalized = normalize_spec(spec)
    fields = normalized["endpoints"]["GET /trees"]["responses"]["200"]["fields"]
    assert fields["name"]["required"] is True
    # The cycle is detected where the repeated $ref appears, so the second
    # level stops as a leaf of type "recursive".
    assert fields["child.child"]["type"] == "recursive"
    assert set(fields) == {"name", "child.name", "child.child"}


def test_load_spec_makes_yaml_values_json_safe():
    """Unquoted dates become ISO strings, integer response codes stay strings."""
    text = """
openapi: 3.0.3
info: {title: T, version: 1.0.0, x-day: 2026-11-01}
paths:
  /x:
    get:
      responses:
        200:
          description: ok
"""
    data = load_spec(text)
    assert data["info"]["x-day"] == "2026-11-01"
    normalized = normalize_spec(data)
    assert "200" in normalized["endpoints"]["GET /x"]["responses"]


def test_external_ref_is_an_error():
    text = """
openapi: 3.0.3
info: {title: T, version: 1.0.0}
paths:
  /x:
    get:
      responses:
        '200':
          description: ok
          content:
            application/json:
              schema:
                $ref: 'https://example.com/schemas/common.yaml#/Thing'
"""
    with pytest.raises(SpecError) as excinfo:
        validate_spec(load_spec(text))
    assert "not supported" in str(excinfo.value)
