"""Impact and stale-reference checks against the real kit files."""
from datetime import date
from pathlib import Path

from app.core.classify import classify_all
from app.core.consumers import parse_consumer
from app.core.diff import diff_specs
from app.core.impact import compute_impact, find_stale_references
from app.core.normalize import load_spec, normalize_spec, validate_spec

REPO_ROOT = Path(__file__).resolve().parents[2]
CONSUMERS = REPO_ROOT / "kit" / "consumers"
SPECS = REPO_ROOT / "kit" / "specs"
TODAY = date(2026, 11, 18)


def _consumer(name: str) -> dict:
    text = (CONSUMERS / f"{name}.yaml").read_text(encoding="utf-8")
    return parse_consumer(load_spec(text))


def _usages(*names: str) -> list[dict]:
    usages: list[dict] = []
    for name in names:
        parsed = _consumer(name)
        for use in parsed["uses"]:
            usages.append(
                {
                    "consumer": parsed["consumer"],
                    "team": parsed["team"],
                    **use,
                }
            )
    return usages


def _normalized(relative: str) -> dict:
    text = (SPECS / relative).read_text(encoding="utf-8")
    spec = load_spec(text)
    validate_spec(spec)
    return normalize_spec(spec)


def _impact(provider: str, old: str, new: str, *consumers: str) -> dict:
    classified = classify_all(
        diff_specs(_normalized(old), _normalized(new)), TODAY
    )
    names = consumers or ("mobile-app", "notifications-svc", "partner-portal", "payments-svc")
    return compute_impact(classified, _usages(*names), provider)


def _groups(result: dict) -> dict[str, list[dict]]:
    return {
        group["consumer"]: group["affected"]
        for group in result["affected_consumers"]
    }


def test_quote_2_to_3_breaks_mobile_app_and_warns_quote_readers():
    result = _impact(
        "quote-api",
        "quote-api/2-reorganised.yaml",
        "quote-api/3-price-lines.yaml",
    )
    groups = _groups(result)

    assert set(groups) == {"mobile-app", "notifications-svc"}
    assert "partner-portal" not in groups

    amount = [
        item
        for item in groups["mobile-app"]
        if item["field"] == "premium.breakdown[].amount"
    ]
    assert len(amount) == 1
    assert (
        amount[0]["via"],
        amount[0]["severity"],
        amount[0]["method"],
        amount[0]["endpoint"],
        amount[0]["status_code"],
    ) == ("reads", "breaking", "POST", "/quotes", "201")
    assert "mobile-app" in amount[0]["reason"]

    expired = [
        item
        for item in groups["notifications-svc"]
        if item["field"] == "status"
    ]
    assert expired
    assert all(item["via"] == "reads" and item["severity"] == "warning" for item in expired)
    assert {item["endpoint"] for item in expired} == {"/quotes/{quoteId}"}

    mobile_expired = [
        item
        for item in groups["mobile-app"]
        if item["field"] == "status" and item["severity"] == "warning"
    ]
    assert mobile_expired

    assert all(
        item["field"] != "query:region"
        for items in groups.values()
        for item in items
    )


def test_quote_3_to_4_affects_nobody():
    result = _impact(
        "quote-api",
        "quote-api/3-price-lines.yaml",
        "quote-api/4-ncb-and-taxes.yaml",
    )
    assert result["affected_consumers"] == []
    assert result["unused_breaking"] == []


def test_policy_1_to_2_warns_payments_for_legacy_code_only():
    result = _impact(
        "policy-api", "policy-api/1-initial.yaml", "policy-api/2-deprecations.yaml"
    )
    groups = _groups(result)
    assert set(groups) == {"payments-svc"}
    assert "notifications-svc" not in groups
    assert groups["payments-svc"]
    assert all(item["field"] == "legacyCode" for item in groups["payments-svc"])
    assert all(item["severity"] == "warning" for item in groups["payments-svc"])


def test_policy_2_to_3_warns_payments_and_phone_is_unused_breaking():
    result = _impact(
        "policy-api",
        "policy-api/2-deprecations.yaml",
        "policy-api/3-remove-old-fields.yaml",
    )
    groups = _groups(result)
    assert set(groups) == {"payments-svc"}
    assert all(item["field"] == "legacyCode" for item in groups["payments-svc"])
    assert all(item["severity"] == "warning" for item in groups["payments-svc"])

    assert any(
        change["field"] == "holder.phone" for change in result["unused_breaking"]
    )


def test_policy_3_to_4_breaks_partner_portal_twice():
    result = _impact(
        "policy-api",
        "policy-api/3-remove-old-fields.yaml",
        "policy-api/4-endorsement-rules.yaml",
    )
    groups = _groups(result)
    assert set(groups) == {"partner-portal"}

    requested = [
        item
        for item in groups["partner-portal"]
        if item["field"] == "requestedBy"
        and item["via"] == "calls endpoint"
        and item["severity"] == "breaking"
    ]
    vehicle = [
        item
        for item in groups["partner-portal"]
        if item["field"] == "type"
        and item["via"] == "sends"
        and item["severity"] == "breaking"
    ]
    assert requested
    assert vehicle


def test_claims_3_to_4_breaks_amount_approved_and_warns_202():
    result = _impact(
        "claims-api",
        "claims-api/3-channel-and-notes-fixed.yaml",
        "claims-api/4-approval-later.yaml",
    )
    groups = _groups(result)
    assert set(groups) == {"mobile-app"}
    assert "notifications-svc" not in groups

    amount = [
        item
        for item in groups["mobile-app"]
        if item["field"] == "amountApproved"
    ]
    assert len(amount) == 1
    assert (
        amount[0]["via"],
        amount[0]["severity"],
        amount[0]["method"],
        amount[0]["endpoint"],
        amount[0]["status_code"],
    ) == ("reads", "breaking", "GET", "/claims/{claimId}", "200")

    added = [
        item
        for item in groups["mobile-app"]
        if item["via"] == "calls endpoint"
        and item["method"] == "POST"
        and item["status_code"] == "202"
    ]
    assert added
    assert all(item["severity"] == "warning" for item in added)


def test_stale_legacy_code_since_policy_2_0_0():
    published = []
    for filename in ("1-initial.yaml", "2-deprecations.yaml", "3-remove-old-fields.yaml"):
        normalized = _normalized(f"policy-api/{filename}")
        published.append((normalized["version"], normalized))

    usages = _usages("payments-svc")
    stale = find_stale_references(usages, published)

    legacy = [item for item in stale if item["field"] == "legacyCode"]
    assert len(legacy) == 1
    assert legacy[0]["consumer"] == "payments-svc"
    assert legacy[0]["provider"] == "policy-api"
    assert legacy[0]["via"] == "reads"
    assert legacy[0]["severity"] == "error"
    assert legacy[0]["since_version"] == "2.0.0"
    assert stale == legacy


def test_path_param_names_are_not_stale_after_rename():
    normalized = _normalized("quote-api/2-reorganised.yaml")
    published = [(normalized["version"], normalized)]
    usages = [
        usage
        for usage in _usages("mobile-app", "notifications-svc")
        if usage["provider"] == "quote-api"
    ]

    stale = find_stale_references(usages, published)
    assert stale == []
