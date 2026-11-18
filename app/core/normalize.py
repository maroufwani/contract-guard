"""Load, validate and normalize an OpenAPI spec into field paths.

The normalized form contains only the contract surface (endpoints, params,
request and response fields), never how the YAML file happens to be
organized: every internal $ref is resolved and schemas become field paths
in the kit notation (dots for nesting, [] for list items).
"""
import json
import re
from collections.abc import Iterator
from datetime import date, time  # datetime is a subclass of date
from typing import Any

import yaml
from openapi_spec_validator import validate as osv_validate

from app.core.errors import SpecError

HTTP_METHODS = {"GET", "PUT", "POST", "DELETE", "OPTIONS", "HEAD", "PATCH", "TRACE"}
SCALAR_TYPES = {"string", "number", "integer", "boolean", "null"}

_PATH_PARAM = re.compile(r"\{([^/{}]+)\}")
_PLACEHOLDER = re.compile(r"\{[^/{}]+\}")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _json_safe(value: Any) -> Any:
    """Turn YAML-only values (dates, times, non-string keys) into JSON ones."""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, (date, time)):
        return value.isoformat()
    return value


def load_spec(text: str) -> dict:
    """Parse a YAML or JSON document into a JSON-safe dict."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SpecError(f"Invalid YAML: {exc}") from exc
    if isinstance(data, str):
        # The whole document parsed as one YAML scalar; a spec is never that.
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SpecError("Document is neither a valid YAML mapping nor JSON") from exc
    if not isinstance(data, dict):
        raise SpecError("Spec must be a mapping at the top level")
    return _json_safe(data)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _iter_ref_locations(node: Any, location: str) -> Iterator[tuple[str, str]]:
    """Yield (location, $ref) for every $ref object in the document."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            yield location, ref
        for key, value in node.items():
            child = f"{location}.{key}" if location else str(key)
            yield from _iter_ref_locations(value, child)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            child = f"{location}[{index}]"
            yield from _iter_ref_locations(item, child)


def _resolve_pointer(document: Any, ref: str) -> Any:
    """Follow a local JSON pointer like #/components/schemas/Quote."""
    if not ref.startswith("#"):
        raise SpecError(
            f"External $ref '{ref}': external references are not supported"
        )
    target = document
    pointer = ref[1:].lstrip("/")
    for token in pointer.split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(target, dict) or token not in target:
            raise SpecError(f"Unresolvable $ref '{ref}'")
        target = target[token]
    return target


def _check_internal_refs(spec: dict) -> None:
    """Every $ref must point inside the same document and actually resolve."""
    for location, ref in _iter_ref_locations(spec, ""):
        try:
            _resolve_pointer(spec, ref)
        except SpecError as exc:
            raise SpecError(f"{exc} at {location}") from exc


def _validate_with_openapi_validator(spec: dict) -> None:
    """Run openapi-spec-validator 0.7.x and shrink its error to one line."""
    try:
        osv_validate(spec)
    except Exception as exc:  # any validator error becomes a SpecError
        short = ""
        try:
            from openapi_spec_validator.utils import best_match

            short = str(best_match(exc).message)
        except Exception:
            short = ""
        if not short:
            short = str(getattr(exc, "message", None) or exc)
        raise SpecError(short.strip().splitlines()[0]) from exc


def validate_spec(spec: dict) -> None:
    """Raise SpecError unless spec is a valid OpenAPI 3.0.x document."""
    version = str(spec.get("openapi", ""))
    if not version.startswith("3.0."):
        raise SpecError(
            f"Unsupported OpenAPI version '{version or 'missing'}': only 3.0.x is supported"
        )
    if not str((spec.get("info") or {}).get("version", "")):
        raise SpecError("info.version is missing")
    _check_internal_refs(spec)
    _validate_with_openapi_validator(spec)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def _resolve_ref(spec: dict, ref: str) -> Any:
    """Follow a local $ref; refs were already validated, but stay defensive."""
    try:
        return _resolve_pointer(spec, ref)
    except SpecError as exc:
        raise SpecError(f"{exc} (while normalizing)") from exc


def _path_template(path: str) -> str:
    """'/quotes/{quoteId}' -> '/quotes/{}': identity ignores param names."""
    return _PLACEHOLDER.sub("{}", path)


def _sorted_enum(schema: dict) -> list[str] | None:
    enum = schema.get("enum")
    if not isinstance(enum, list) or not enum:
        return None
    return sorted(str(value) for value in enum)


def _content_schema(container: dict, spec: dict) -> dict | None:
    """The JSON schema of a requestBody/response, or None if there is none.

    Only application/json is treated as the contract; if a body has no json
    media type at all, fall back to the first one.
    """
    content = container.get("content")
    if not isinstance(content, dict) or not content:
        return None
    media = content.get("application/json")
    if not isinstance(media, dict):
        media = next(iter(content.values()))
    if not isinstance(media, dict):
        return None
    schema = media.get("schema")
    if isinstance(schema, dict) and "$ref" in schema:
        resolved = _resolve_ref(spec, schema["$ref"])
        return resolved if isinstance(resolved, dict) else None
    return schema if isinstance(schema, dict) else None


def _entry(
    fields: dict[str, dict],
    base: str,
    entry_type: str,
    required: bool,
    schema: dict,
) -> None:
    """Add one field entry, unless it is an object slot with no path."""
    key = base if base else ("[]" if entry_type == "array" else "")
    if not key:
        return
    format_value = schema.get("format")
    sunset = schema.get("x-sunset")
    fields[key] = {
        "type": entry_type,
        "format": str(format_value) if format_value is not None else None,
        "required": required,
        "enum": _sorted_enum(schema),
        "deprecated": schema.get("deprecated") is True,
        "sunset": str(sunset) if sunset is not None else None,
        "internal": schema.get("x-internal") is True,
    }


def _collect_fields(
    schema: dict,
    base: str,
    required: bool,
    fields: dict[str, dict],
    spec: dict,
    active: set[str],
) -> None:
    """Walk a schema and add entries for scalars and arrays under `base`.

    `required` says whether this field is required inside its parent object.
    `active` holds the $refs on the current path so recursive schemas stop
    with type "recursive" instead of looping forever.
    """
    if not isinstance(schema, dict):
        return

    if "$ref" in schema:
        ref = schema["$ref"]
        if ref in active:
            _entry(fields, base, "recursive", required, {})
            return
        resolved = _resolve_ref(spec, ref)
        if not isinstance(resolved, dict):
            return
        return _collect_fields(
            resolved, base, required, fields, spec, active | {ref}
        )

    if isinstance(schema.get("oneOf"), list):
        return _entry(fields, base, "oneOf", required, schema)
    if isinstance(schema.get("anyOf"), list):
        return _entry(fields, base, "anyOf", required, schema)

    if isinstance(schema.get("allOf"), list):
        return _collect_fields(
            _merge_all_of(schema, spec), base, required, fields, spec, active
        )

    schema_type = schema.get("type")
    schema_type = str(schema_type) if schema_type is not None else None
    if schema_type is None:
        if isinstance(schema.get("properties"), dict) or "additionalProperties" in schema:
            schema_type = "object"
        elif isinstance(schema.get("items"), dict):
            schema_type = "array"

    if schema_type == "object" or isinstance(schema.get("properties"), dict):
        # Objects are represented by their children only.
        required_names = {
            str(name) for name in schema.get("required") or [] if isinstance(name, str)
        }
        for name, child in (schema.get("properties") or {}).items():
            child = child if isinstance(child, dict) else {}
            child_base = f"{base}.{name}" if base else str(name)
            _collect_fields(
                child, child_base, str(name) in required_names, fields, spec, active
            )
        return

    if schema_type == "array" or isinstance(schema.get("items"), dict):
        _entry(fields, base, "array", required, schema)
        items = schema.get("items")
        if isinstance(items, dict):
            _collect_fields(
                items, f"{base}[]", required, fields, spec, active
            )
        return

    # Scalar (or unknown type): the leaf entry itself.
    _entry(fields, base, schema_type or "unknown", required, schema)


def _merge_all_of(schema: dict, spec: dict) -> dict:
    """Merge the parts of an allOf (resolving $ref parts) into one schema."""
    merged: dict[str, Any] = {}
    for part in schema["allOf"]:
        if not isinstance(part, dict):
            continue
        if "$ref" in part:
            resolved = _resolve_ref(spec, part["$ref"])
            part = resolved if isinstance(resolved, dict) else {}
        for key, value in part.items():
            if key == "required" and isinstance(value, list) and isinstance(merged.get("required"), list):
                merged["required"] = merged["required"] + [
                    name for name in value if name not in merged["required"]
                ]
            elif key == "properties" and isinstance(value, dict) and isinstance(merged.get("properties"), dict):
                merged["properties"] = {**merged["properties"], **value}
            else:
                merged[key] = value
    extras = {key: value for key, value in schema.items() if key != "allOf"}
    return {**merged, **extras}


def _normalize_parameters(
    shared: list[dict], local: list[dict], raw_path: str, spec: dict
) -> dict[str, dict]:
    """Params keyed 'path:0' (by position) or 'in:name'; cookie is ignored."""
    placeholders = _PATH_PARAM.findall(raw_path)
    params: dict[str, dict] = {}
    for parameter in [*shared, *local]:
        if not isinstance(parameter, dict):
            continue
        if "$ref" in parameter:
            resolved = _resolve_ref(spec, parameter["$ref"])
            if not isinstance(resolved, dict):
                continue
            parameter = resolved
        location = str(parameter.get("in") or "")
        name = str(parameter.get("name") or "")
        if location == "cookie" or not name:
            continue
        schema = parameter.get("schema") if isinstance(parameter.get("schema"), dict) else {}
        if location == "path":
            position = placeholders.index(name) if name in placeholders else len(placeholders)
            key = f"path:{position}"
            # Path params carry no name: identity is the position, so a
            # rename like {id} -> {quoteId} never registers as a change.
            # The display spelling lives on the endpoint's "path".
            params[key] = {
                "required": parameter.get("required") is True,
                "type": str(schema.get("type") or "string"),
                "enum": _sorted_enum(schema),
            }
        else:
            params[f"{location}:{name}"] = {
                "name": name,
                "required": parameter.get("required") is True,
                "type": str(schema.get("type") or "string"),
                "enum": _sorted_enum(schema),
            }
    return params


def _normalize_request(request_body: Any, spec: dict) -> dict | None:
    if not isinstance(request_body, dict):
        return None
    if "$ref" in request_body:
        resolved = _resolve_ref(spec, request_body["$ref"])
        request_body = resolved if isinstance(resolved, dict) else {}
    required = request_body.get("required") is True
    fields: dict[str, dict] = {}
    schema = _content_schema(request_body, spec)
    if schema is not None:
        _collect_fields(schema, "", True, fields, spec, set())
    return {"required": required, "fields": fields}


def _normalize_responses(responses: Any, spec: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not isinstance(responses, dict):
        return out
    for code, response in responses.items():
        if not isinstance(response, dict):
            out[str(code)] = {"fields": {}}
            continue
        if "$ref" in response:
            resolved = _resolve_ref(spec, response["$ref"])
            response = resolved if isinstance(resolved, dict) else {}
        fields: dict[str, dict] = {}
        schema = _content_schema(response, spec)
        if schema is not None:
            _collect_fields(schema, "", True, fields, spec, set())
        out[str(code)] = {"fields": fields}
    return out


def normalize_spec(spec: dict) -> dict:
    """Flatten a validated spec into endpoints and kit-notation field paths."""
    endpoints: dict[str, dict] = {}
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        paths = {}
    for raw_path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        if "$ref" in path_item:
            resolved = _resolve_ref(spec, path_item["$ref"])
            path_item = resolved if isinstance(resolved, dict) else {}
        shared = [p for p in path_item.get("parameters") or [] if isinstance(p, dict)]
        for method, operation in path_item.items():
            method_upper = str(method).upper()
            if method_upper not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            local = [p for p in operation.get("parameters") or [] if isinstance(p, dict)]
            fields_of_request = _normalize_request(operation.get("requestBody"), spec)
            endpoints[f"{method_upper} {_path_template(str(raw_path))}"] = {
                "method": method_upper,
                "path": str(raw_path),
                "internal": operation.get("x-internal") is True,
                "deprecated": operation.get("deprecated") is True,
                "params": _normalize_parameters(shared, local, str(raw_path), spec),
                "request": fields_of_request,
                "responses": _normalize_responses(operation.get("responses"), spec),
            }
    version = str((spec.get("info") or {}).get("version", ""))
    return {"version": version, "endpoints": endpoints}
