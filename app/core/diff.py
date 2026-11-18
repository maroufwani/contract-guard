"""Compare two normalized specs and list the contract changes between them.

The input is the output of app.core.normalize.normalize_spec: plain dicts of
endpoints, params and field paths. Severity is not decided here; that is the
classifier's job.
"""
from dataclasses import dataclass, field as dataclass_field
from typing import Any


@dataclass
class Change:
    endpoint: str
    path: str
    location: str
    status_code: str | None
    field: str | None
    kind: str
    old: Any
    new: Any
    internal: bool
    info: dict = dataclass_field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "endpoint": self.endpoint,
            "path": self.path,
            "location": self.location,
            "status_code": self.status_code,
            "field": self.field,
            "kind": self.kind,
            "old": self.old,
            "new": self.new,
            "internal": self.internal,
            "info": self.info,
        }


def _sort_key(change: Change) -> tuple:
    return (
        change.endpoint,
        change.location,
        change.status_code or "",
        change.field or "",
        change.kind,
    )


def _is_child_of(child: str, parent: str) -> bool:
    """True when `child` sits under `parent` in the kit path notation."""
    return child.startswith(parent + ".") or child.startswith(parent + "[]")


def _without_removed_children(keys: set[str]) -> list[str]:
    """Keep only the topmost paths: removing a parent hides its children."""
    kept: list[str] = []
    for key in sorted(keys, key=len):
        if any(_is_child_of(key, parent) for parent in kept):
            continue
        kept.append(key)
    return sorted(kept)


def _field_info(old: dict | None, new: dict | None) -> dict:
    old = old or {}
    new = new or {}
    return {
        "old_deprecated": old.get("deprecated"),
        "old_sunset": old.get("sunset"),
        "new_required": new.get("required"),
    }


def _entry_type(entry: dict | None) -> str | None:
    return entry.get("type") if entry else None


def _endpoint_internal(*endpoints: dict | None) -> bool:
    return any(bool(endpoint and endpoint.get("internal")) for endpoint in endpoints)


def _field_internal(old: dict | None, new: dict | None) -> bool:
    return bool((old or {}).get("internal") or (new or {}).get("internal"))


def _compare_entry(
    endpoint_key: str,
    display_path: str,
    location: str,
    status_code: str | None,
    field_name: str,
    old: dict,
    new: dict,
    internal: bool,
) -> list[Change]:
    """Differences for one field or param that exists in both versions."""
    changes: list[Change] = []
    info = _field_info(old, new)

    def add(kind: str, old_value: Any, new_value: Any) -> None:
        changes.append(
            Change(
                endpoint=endpoint_key,
                path=display_path,
                location=location,
                status_code=status_code,
                field=field_name,
                kind=kind,
                old=old_value,
                new=new_value,
                internal=internal,
                info=dict(info),
            )
        )

    if old.get("type") != new.get("type"):
        add("type_changed", old.get("type"), new.get("type"))
    if old.get("format") != new.get("format"):
        add("format_changed", old.get("format"), new.get("format"))
    if old.get("required") != new.get("required"):
        add("required_changed", old.get("required"), new.get("required"))

    old_enum = set(old.get("enum") or [])
    new_enum = set(new.get("enum") or [])
    added = sorted(new_enum - old_enum)
    if added:
        add("enum_values_added", None, added)
    removed = sorted(old_enum - new_enum)
    if removed:
        add("enum_values_removed", removed, None)

    if old.get("deprecated") != new.get("deprecated"):
        add("deprecated_changed", old.get("deprecated"), new.get("deprecated"))
    if old.get("sunset") != new.get("sunset"):
        add("sunset_changed", old.get("sunset"), new.get("sunset"))
    return changes


def _compare_fields(
    endpoint_key: str,
    display_path: str,
    location: str,
    status_code: str | None,
    old_fields: dict[str, dict],
    new_fields: dict[str, dict],
    internal: bool,
) -> list[Change]:
    changes: list[Change] = []
    removed = _without_removed_children(set(old_fields) - set(new_fields))
    added = _without_removed_children(set(new_fields) - set(old_fields))

    for name in removed:
        old = old_fields[name]
        changes.append(
            Change(
                endpoint=endpoint_key,
                path=display_path,
                location=location,
                status_code=status_code,
                field=name,
                kind="field_removed",
                old=_entry_type(old),
                new=None,
                internal=internal or _field_internal(old, None),
                info=_field_info(old, None),
            )
        )
    for name in added:
        new = new_fields[name]
        changes.append(
            Change(
                endpoint=endpoint_key,
                path=display_path,
                location=location,
                status_code=status_code,
                field=name,
                kind="field_added",
                old=None,
                new=_entry_type(new),
                internal=internal or _field_internal(None, new),
                info=_field_info(None, new),
            )
        )
    for name in sorted(set(old_fields) & set(new_fields)):
        old = old_fields[name]
        new = new_fields[name]
        changes.extend(
            _compare_entry(
                endpoint_key,
                display_path,
                location,
                status_code,
                name,
                old,
                new,
                internal or _field_internal(old, new),
            )
        )
    return changes


def _compare_params(
    endpoint_key: str,
    display_path: str,
    old_params: dict[str, dict],
    new_params: dict[str, dict],
    internal: bool,
) -> list[Change]:
    changes: list[Change] = []
    for name in sorted(set(old_params) - set(new_params)):
        old = old_params[name]
        changes.append(
            Change(
                endpoint=endpoint_key,
                path=display_path,
                location="param",
                status_code=None,
                field=name,
                kind="param_removed",
                old=old.get("type"),
                new=None,
                internal=internal,
                info=_field_info(old, None),
            )
        )
    for name in sorted(set(new_params) - set(old_params)):
        new = new_params[name]
        changes.append(
            Change(
                endpoint=endpoint_key,
                path=display_path,
                location="param",
                status_code=None,
                field=name,
                kind="param_added",
                old=None,
                new=new.get("type"),
                internal=internal,
                info=_field_info(None, new),
            )
        )
    for name in sorted(set(old_params) & set(new_params)):
        changes.extend(
            _compare_entry(
                endpoint_key,
                display_path,
                "param",
                None,
                name,
                old_params[name],
                new_params[name],
                internal,
            )
        )
    return changes


def _compare_endpoint(
    endpoint_key: str,
    old: dict,
    new: dict,
    display_path: str,
) -> list[Change]:
    internal = _endpoint_internal(old, new)
    changes: list[Change] = []

    if old.get("deprecated") != new.get("deprecated"):
        changes.append(
            Change(
                endpoint=endpoint_key,
                path=display_path,
                location="endpoint",
                status_code=None,
                field=None,
                kind="deprecated_changed",
                old=old.get("deprecated"),
                new=new.get("deprecated"),
                internal=internal,
                info={},
            )
        )

    changes.extend(
        _compare_params(endpoint_key, display_path, old.get("params") or {}, new.get("params") or {}, internal)
    )

    old_request = old.get("request")
    new_request = new.get("request")
    if old_request is None and new_request is not None:
        changes.append(
            Change(
                endpoint=endpoint_key,
                path=display_path,
                location="request",
                status_code=None,
                field=None,
                kind="request_body_added",
                old=None,
                new=True,
                internal=internal,
                info={"new_required": new_request.get("required") is True},
            )
        )
    elif old_request is not None and new_request is None:
        changes.append(
            Change(
                endpoint=endpoint_key,
                path=display_path,
                location="request",
                status_code=None,
                field=None,
                kind="request_body_removed",
                old=True,
                new=None,
                internal=internal,
                info={},
            )
        )
    elif old_request is not None and new_request is not None:
        if old_request.get("required") != new_request.get("required"):
            changes.append(
                Change(
                    endpoint=endpoint_key,
                    path=display_path,
                    location="request",
                    status_code=None,
                    field=None,
                    kind="required_changed",
                    old=old_request.get("required"),
                    new=new_request.get("required"),
                    internal=internal,
                    info={},
                )
            )
        changes.extend(
            _compare_fields(
                endpoint_key,
                display_path,
                "request",
                None,
                old_request.get("fields") or {},
                new_request.get("fields") or {},
                internal,
            )
        )

    old_responses = old.get("responses") or {}
    new_responses = new.get("responses") or {}
    for code in sorted(set(old_responses) - set(new_responses)):
        changes.append(
            Change(
                endpoint=endpoint_key,
                path=display_path,
                location="response",
                status_code=code,
                field=None,
                kind="response_removed",
                old=True,
                new=None,
                internal=internal,
                info={},
            )
        )
    for code in sorted(set(new_responses) - set(old_responses)):
        changes.append(
            Change(
                endpoint=endpoint_key,
                path=display_path,
                location="response",
                status_code=code,
                field=None,
                kind="response_added",
                old=None,
                new=True,
                internal=internal,
                info={},
            )
        )
    for code in sorted(set(old_responses) & set(new_responses)):
        changes.extend(
            _compare_fields(
                endpoint_key,
                display_path,
                "response",
                code,
                old_responses[code].get("fields") or {},
                new_responses[code].get("fields") or {},
                internal,
            )
        )
    return changes


def diff_specs(old: dict, new: dict) -> list[Change]:
    """Return every contract change from normalized spec `old` to `new`."""
    old_endpoints = old.get("endpoints") or {}
    new_endpoints = new.get("endpoints") or {}
    changes: list[Change] = []

    for endpoint_key in sorted(set(old_endpoints) - set(new_endpoints)):
        endpoint = old_endpoints[endpoint_key]
        changes.append(
            Change(
                endpoint=endpoint_key,
                path=endpoint.get("path", ""),
                location="endpoint",
                status_code=None,
                field=None,
                kind="endpoint_removed",
                old=True,
                new=None,
                internal=_endpoint_internal(endpoint),
                info={},
            )
        )
    for endpoint_key in sorted(set(new_endpoints) - set(old_endpoints)):
        endpoint = new_endpoints[endpoint_key]
        changes.append(
            Change(
                endpoint=endpoint_key,
                path=endpoint.get("path", ""),
                location="endpoint",
                status_code=None,
                field=None,
                kind="endpoint_added",
                old=None,
                new=True,
                internal=_endpoint_internal(endpoint),
                info={},
            )
        )
    for endpoint_key in sorted(set(old_endpoints) & set(new_endpoints)):
        old_endpoint = old_endpoints[endpoint_key]
        new_endpoint = new_endpoints[endpoint_key]
        display_path = new_endpoint.get("path") or old_endpoint.get("path") or ""
        changes.extend(
            _compare_endpoint(endpoint_key, old_endpoint, new_endpoint, display_path)
        )

    return sorted(changes, key=_sort_key)
