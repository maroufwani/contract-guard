"""Parse a consumer YAML definition into the shape impact checks use.

The kit format is a consumer name and team plus a list of uses. Every use says
which provider endpoint is called and which fields the consumer sends and
reads. This module is pure: the API turns the text into a dict with load_spec.
"""
from app.core.normalize import HTTP_METHODS


class ConsumerError(ValueError):
    """A consumer file is missing information or uses an unsupported method."""


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConsumerError(f"{where} must be a non-empty string")
    return value.strip()


def _field_list(value: object, where: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConsumerError(f"{where} must be a list of field paths")
    fields: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ConsumerError(f"{where} must contain non-empty field paths")
        fields.append(item.strip())
    return fields


def parse_consumer(data: dict) -> dict:
    """Return {consumer, team, uses} with upper-case methods and field lists."""
    if not isinstance(data, dict):
        raise ConsumerError("Consumer file must be a mapping")

    consumer = _text(data.get("consumer"), "consumer")
    team = _text(data.get("team"), f"team for consumer '{consumer}'")

    uses = data.get("uses")
    if not isinstance(uses, list):
        raise ConsumerError(f"Consumer '{consumer}' is missing a 'uses' list")

    parsed_uses: list[dict] = []
    for index, use in enumerate(uses, start=1):
        where = f"consumer '{consumer}' usage {index}"
        if not isinstance(use, dict):
            raise ConsumerError(f"{where} must be a mapping")

        provider = _text(use.get("provider"), f"{where}: provider")
        method = _text(use.get("method"), f"{where}: method").upper()
        if method not in HTTP_METHODS:
            raise ConsumerError(
                f"{where} has unsupported method '{use.get('method')}'; "
                f"expected one of {', '.join(sorted(HTTP_METHODS))}"
            )
        path = _text(use.get("path"), f"{where}: path")

        parsed_uses.append(
            {
                "provider": provider,
                "method": method,
                "path": path,
                "sends": _field_list(use.get("sends"), f"{where}: sends"),
                "reads": _field_list(use.get("reads"), f"{where}: reads"),
            }
        )

    return {"consumer": consumer, "team": team, "uses": parsed_uses}
