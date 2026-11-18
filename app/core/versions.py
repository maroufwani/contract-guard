"""Version label comparison (semver X.Y.Z, as used in the kit's info.version)."""
from app.core.errors import SpecError


def parse_version(label: str) -> tuple[int, ...]:
    """'1.1.0' -> (1, 1, 0). Missing parts count as 0; non-numeric is an error."""
    parts = str(label).strip().split(".")
    numbers: list[int] = []
    for part in parts:
        if not part.isdigit():
            raise SpecError(
                f"Invalid version '{label}': expected numbers separated by dots, e.g. 1.1.0"
            )
        numbers.append(int(part))
    if not numbers:
        raise SpecError("Version label is missing")
    return tuple(numbers)


def compare_versions(a: str, b: str) -> int:
    """Return -1, 0 or 1 comparing version labels a and b (missing parts = 0)."""
    left, right = parse_version(a), parse_version(b)
    width = max(len(left), len(right))
    left += (0,) * (width - len(left))
    right += (0,) * (width - len(right))
    if left < right:
        return -1
    if left > right:
        return 1
    return 0
