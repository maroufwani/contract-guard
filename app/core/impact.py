"""Match classified changes against consumer usages, and find stale references.

`compute_impact` only reports breaking and warning changes. A change can hit a
consumer because the consumer sends the field, reads the field, or simply calls
the endpoint (for endpoint-wide changes such as a new required field).

`find_stale_references` checks one provider's consumer references against the
published versions of that provider, in publish order. It never raises for an
unknown provider, endpoint or field: those are stale references too.
"""
import re
from typing import Any

from app.core.classify import Classification
from app.core.diff import Change

_PATH_PARAM = re.compile(r"\{([^/{}]+)\}")
_PLACEHOLDER = re.compile(r"\{[^/{}]+\}")


def endpoint_key(method: str, path: str) -> str:
    """'GET', '/quotes/{id}' -> 'GET /quotes/{}'."""
    return f"{str(method).upper()} {_PLACEHOLDER.sub('{}', str(path))}"


def _is_child_of(child: str, parent: str) -> bool:
    return child.startswith(parent + ".") or child.startswith(parent + "[]")


def _paths_overlap(left: str, right: str) -> bool:
    return left == right or _is_child_of(left, right) or _is_child_of(right, left)


def _label(change: Change) -> str:
    method, _, fallback_path = change.endpoint.partition(" ")
    path = change.path or fallback_path
    label = f"{method} {path}".strip()
    if change.status_code:
        label = f"{label} ({change.status_code})"
    return label


def _match_param(change: Change, usage: dict) -> str | None:
    field = change.field or ""
    if field.startswith("path:"):
        return "calls endpoint"
    name = field.split(":", 1)[1] if ":" in field else field
    if change.kind == "param_added" and change.info.get("new_required"):
        return "calls endpoint"
    if change.kind == "required_changed" and change.new is True:
        return "calls endpoint"
    if name in (usage.get("sends") or []):
        return "sends"
    return None


def _match_request(change: Change, usage: dict) -> str | None:
    if change.field is None:
        if change.kind == "request_body_added":
            return "calls endpoint" if change.info.get("new_required") else None
        if change.kind == "required_changed":
            return "calls endpoint" if change.new is True else None
        # A removed request body affects every caller that used to send one.
        return "calls endpoint"

    if change.kind == "field_added" and change.info.get("new_required"):
        return "calls endpoint"
    if change.kind == "required_changed" and change.new is True:
        return "calls endpoint"
    if any(_paths_overlap(change.field, sent) for sent in usage.get("sends") or []):
        return "sends"
    return None


def _match_response(change: Change, usage: dict) -> str | None:
    if change.kind in {"response_added", "response_removed"}:
        return "calls endpoint"
    if change.field is None:
        return None
    if not str(change.status_code or "").startswith("2"):
        return None
    if any(_paths_overlap(change.field, read) for read in usage.get("reads") or []):
        return "reads"
    return None


def _match(change: Change, usage: dict) -> str | None:
    if change.location == "endpoint":
        if change.kind in {"endpoint_removed", "deprecated_changed"}:
            return "calls endpoint"
        return None
    if change.location == "param":
        return _match_param(change, usage)
    if change.location == "request":
        return _match_request(change, usage)
    if change.location == "response":
        return _match_response(change, usage)
    return None


def _reason(consumer: str, via: str, change: Change, classification: Classification) -> str:
    label = _label(change)
    if via == "calls endpoint":
        return f"{consumer} calls {label}. {classification.reason}"
    return f"{consumer} {via} {change.field} on {label}. {classification.reason}"


def _item(
    usage: dict, via: str, change: Change, classification: Classification
) -> dict:
    return {
        "consumer": usage["consumer"],
        "team": usage["team"],
        "endpoint": change.path,
        "method": change.endpoint.partition(" ")[0],
        "status_code": change.status_code,
        "field": change.field,
        "via": via,
        "severity": classification.severity,
        "rule": classification.rule,
        "reason": _reason(usage["consumer"], via, change, classification),
    }


def _change_dict(change: Change, classification: Classification) -> dict:
    return {
        **change.to_dict(),
        "severity": classification.severity,
        "rule": classification.rule,
        "reason": classification.reason,
    }


def compute_impact(
    classified: list[tuple[Change, Classification]],
    usages: list[dict],
    provider: str,
) -> dict:
    """Group affected consumers, and list breaking changes nobody uses."""
    groups: dict[str, dict] = {}
    seen: set[tuple[str, int]] = set()
    used_changes: set[int] = set()

    for change, classification in classified:
        if classification.severity not in {"breaking", "warning"}:
            continue
        for usage in usages:
            if usage.get("provider") != provider:
                continue
            if endpoint_key(usage.get("method", ""), usage.get("path", "")) != change.endpoint:
                continue
            via = _match(change, usage)
            if via is None:
                continue

            key = (usage["consumer"], id(change))
            if key in seen:
                continue
            seen.add(key)
            used_changes.add(id(change))

            group = groups.setdefault(
                usage["consumer"],
                {
                    "consumer": usage["consumer"],
                    "name": usage["consumer"],
                    "team": usage["team"],
                    "affected": [],
                },
            )
            group["affected"].append(_item(usage, via, change, classification))

    unused = [
        _change_dict(change, classification)
        for change, classification in classified
        if classification.severity == "breaking" and id(change) not in used_changes
    ]
    affected = sorted(groups.values(), key=lambda group: group["consumer"])
    return {"affected_consumers": affected, "unused_breaking": unused}


# ---------------------------------------------------------------------------
# Stale references
# ---------------------------------------------------------------------------


def _sent_is_valid(name: str, endpoint: dict, own_path_params: set[str]) -> bool:
    # A consumer's own path parameter name is fine even when the provider
    # renamed it ({id} vs {quoteId}); path params match by position.
    if name in own_path_params:
        return True
    if name in _PATH_PARAM.findall(endpoint.get("path", "")):
        return True
    for key in endpoint.get("params") or {}:
        if key.startswith("path:"):
            continue
        if ":" in key and key.split(":", 1)[1] == name:
            return True
    request = endpoint.get("request") or {}
    return name in (request.get("fields") or {})


def _read_is_valid(path: str, endpoint: dict) -> bool:
    for code, response in (endpoint.get("responses") or {}).items():
        if str(code).startswith("2") and path in ((response or {}).get("fields") or {}):
            return True
    return False


def _reference_is_valid(
    endpoint: dict | None,
    own_path_params: set[str],
    sent: str | None,
    read: str | None,
) -> bool:
    if endpoint is None:
        return False
    if sent is not None and not _sent_is_valid(sent, endpoint, own_path_params):
        return False
    if read is not None and not _read_is_valid(read, endpoint):
        return False
    return True


def _first_invalid_version(
    published: list[tuple[str, dict]],
    key: str,
    own_path_params: set[str],
    sent: str | None = None,
    read: str | None = None,
) -> str | None:
    """First version where the reference stops matching after matching before."""
    matched_before = False
    for label, spec in published:
        endpoint = (spec.get("endpoints") or {}).get(key)
        valid = _reference_is_valid(endpoint, own_path_params, sent, read)
        if valid:
            matched_before = True
        elif matched_before:
            return label
    if not matched_before:
        return published[0][0] if published else None
    return None


def find_stale_references(
    usages: list[dict], published: list[tuple[str, dict]]
) -> list[dict]:
    """Every consumer reference that no longer matches the latest spec."""
    stale: list[dict] = []
    latest_spec = published[-1][1] if published else None

    for usage in usages:
        key = endpoint_key(usage.get("method", ""), usage.get("path", ""))
        own_path_params = set(_PATH_PARAM.findall(usage.get("path", "")))
        endpoint = (latest_spec.get("endpoints") or {}).get(key) if latest_spec else None
        display_path = endpoint.get("path") if endpoint else usage.get("path", "")

        def add(field: str | None, via: str, since: str | None) -> None:
            stale.append(
                {
                    "consumer": usage["consumer"],
                    "provider": usage["provider"],
                    "endpoint": display_path,
                    "field": field,
                    "via": via,
                    "severity": "error",
                    "since_version": since,
                }
            )

        if endpoint is None:
            add(
                None,
                "calls endpoint",
                _first_invalid_version(published, key, own_path_params),
            )
            continue

        for sent in usage.get("sends") or []:
            if not _sent_is_valid(sent, endpoint, own_path_params):
                add(
                    sent,
                    "sends",
                    _first_invalid_version(published, key, own_path_params, sent=sent),
                )
        for read in usage.get("reads") or []:
            if not _read_is_valid(read, endpoint):
                add(
                    read,
                    "reads",
                    _first_invalid_version(published, key, own_path_params, read=read),
                )
    return stale
