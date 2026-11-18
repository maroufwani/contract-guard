"""The publish gate, blocking, and overrides, using the database.

app/core stays pure; this module is where a validated spec becomes a stored
SpecVersion and a PublishAttempt.
"""
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.core.classify import Classification, check_version_bump, classify_all
from app.core.diff import diff_specs
from app.core.errors import SpecError
from app.core.gate import decide
from app.core.impact import compute_impact
from app.core.normalize import load_spec, normalize_spec, validate_spec
from app.core.versions import compare_versions
from app.db.models import ConsumerUsage, PublishAttempt, Service, SpecVersion


@dataclass(frozen=True)
class PublishResult:
    status_code: int
    attempt: PublishAttempt
    endpoints: list[str] | None = None


def latest_published(db: Session, service_id: int) -> SpecVersion | None:
    """Newest version that counts as published: published or overridden.

    Blocked and rejected versions are never a comparison point.
    """
    return db.scalar(
        select(SpecVersion)
        .where(
            SpecVersion.service_id == service_id,
            SpecVersion.status.in_(("published", "overridden")),
        )
        .order_by(SpecVersion.id.desc())
    )


def _reject(
    db: Session,
    service: Service,
    version_label: str | None,
    compared_against: str | None,
    message: str,
) -> PublishResult:
    attempt = PublishAttempt(
        service_id=service.id,
        version_label=version_label or "",
        outcome="rejected",
        compared_against=compared_against,
        changes=[],
        affected_consumers=[],
        unused_breaking=[],
        reasons=[message],
        error=message,
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return PublishResult(status_code=422, attempt=attempt)


def _usages(db: Session) -> list[dict]:
    usages = list(db.scalars(select(ConsumerUsage).order_by(ConsumerUsage.id)))
    return [
        {
            "consumer": usage.consumer.name,
            "team": usage.consumer.team,
            "provider": usage.provider,
            "method": usage.method,
            "path": usage.path,
            "sends": usage.sends or [],
            "reads": usage.reads or [],
        }
        for usage in usages
    ]


def publish_version(
    db: Session,
    service: Service,
    spec_text: str,
    today: date,
) -> PublishResult:
    """Load, validate, gate and store one spec upload.

    Returns the stored attempt with the HTTP status the API should use:
    201 published, 409 blocked, 422 rejected.
    """
    try:
        spec = load_spec(spec_text)
    except SpecError as exc:
        return _reject(db, service, None, None, str(exc))

    try:
        validate_spec(spec)
    except SpecError as exc:
        version = str((spec.get("info") or {}).get("version", "")) or None
        return _reject(db, service, version, None, str(exc))

    version_label = str(spec["info"]["version"])
    last = latest_published(db, service.id)

    if last is not None:
        try:
            ordering = compare_versions(version_label, last.version_label)
        except SpecError as exc:
            return _reject(db, service, version_label, last.version_label, str(exc))
        if ordering <= 0:
            return _reject(
                db,
                service,
                version_label,
                last.version_label,
                f"Version {version_label} must be higher than the last published "
                f"version {last.version_label}",
            )

    normalized = normalize_spec(spec)

    if last is None:
        version = SpecVersion(
            service_id=service.id,
            version_label=version_label,
            raw_spec=spec_text,
            normalized_spec=normalized,
            status="published",
        )
        db.add(version)
        attempt = PublishAttempt(
            service_id=service.id,
            version_label=version_label,
            outcome="published",
            compared_against=None,
            changes=[],
            affected_consumers=[],
            unused_breaking=[],
            reasons=["First published version; there was nothing to compare against."],
        )
        db.add(attempt)
        db.commit()
        db.refresh(attempt)
        return PublishResult(
            status_code=201,
            attempt=attempt,
            endpoints=sorted(normalized["endpoints"]),
        )

    classified = classify_all(
        diff_specs(last.normalized_spec or {}, normalized),
        today,
    )
    changes = [
        {
            **change.to_dict(),
            "severity": classification.severity,
            "rule": classification.rule,
            "reason": classification.reason,
        }
        for change, classification in classified
    ]
    has_breaking = any(
        classification.severity == "breaking" for _change, classification in classified
    )
    version_warning: Classification | None = check_version_bump(
        last.version_label, version_label, has_breaking
    )
    impact = compute_impact(classified, _usages(db), service.name)
    decision = decide(classified, impact, version_warning)

    version = SpecVersion(
        service_id=service.id,
        version_label=version_label,
        raw_spec=spec_text,
        normalized_spec=normalized,
        status=decision.outcome,
    )
    db.add(version)
    attempt = PublishAttempt(
        service_id=service.id,
        version_label=version_label,
        outcome=decision.outcome,
        compared_against=last.version_label,
        changes=changes,
        affected_consumers=impact["affected_consumers"],
        unused_breaking=impact["unused_breaking"],
        reasons=decision.reasons,
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return PublishResult(
        status_code=201 if decision.outcome == "published" else 409,
        attempt=attempt,
        endpoints=sorted(normalized["endpoints"]),
    )


def override_attempt(
    db: Session,
    service: Service,
    attempt_id: int,
    approved_by: str,
    reason: str,
) -> PublishResult:
    """Turn a blocked attempt into an overridden version, kept in history."""
    attempt = db.scalar(
        select(PublishAttempt).where(
            PublishAttempt.id == attempt_id,
            PublishAttempt.service_id == service.id,
        )
    )
    if attempt is None:
        raise ApiError(404, f"Attempt {attempt_id} not found for '{service.name}'")
    if attempt.outcome != "blocked":
        raise ApiError(409, f"Attempt {attempt_id} is not blocked, so it cannot be overridden")

    if not approved_by or approved_by.strip().casefold() != service.owner_team.strip().casefold():
        raise ApiError(
            403,
            f"Only the owning team '{service.owner_team}' can override this publish",
        )

    if not reason or not reason.strip():
        raise ApiError(422, "An override reason is required")

    version = db.scalar(
        select(SpecVersion)
        .where(
            SpecVersion.service_id == service.id,
            SpecVersion.version_label == attempt.version_label,
            SpecVersion.status == "blocked",
        )
        .order_by(SpecVersion.id.desc())
    )
    if version is None:
        raise ApiError(409, "The blocked version for this attempt no longer exists")

    last = latest_published(db, service.id)
    if last is not None:
        try:
            ordering = compare_versions(version.version_label, last.version_label)
        except SpecError as exc:
            raise ApiError(409, str(exc)) from exc
        if ordering <= 0:
            raise ApiError(
                409,
                f"Version {version.version_label} is no longer higher than the last "
                f"published version {last.version_label}",
            )

    version.status = "overridden"
    override = PublishAttempt(
        service_id=service.id,
        version_label=attempt.version_label,
        outcome="overridden",
        compared_against=attempt.compared_against,
        changes=attempt.changes or [],
        affected_consumers=attempt.affected_consumers or [],
        unused_breaking=attempt.unused_breaking or [],
        reasons=[
            f"Overridden by {approved_by.strip()}: {reason.strip()}",
            *(attempt.reasons or []),
        ],
        approved_by=approved_by.strip(),
        override_reason=reason.strip(),
    )
    db.add(override)
    db.commit()
    db.refresh(override)
    return PublishResult(status_code=201, attempt=override)
