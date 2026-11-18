"""JSON API: register services, upload spec versions, read history."""
from typing import NoReturn

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.core.diff import diff_specs
from app.core.errors import SpecError
from app.core.normalize import load_spec, normalize_spec, validate_spec
from app.core.versions import compare_versions
from app.db.models import PublishAttempt, Service, SpecVersion
from app.db.session import get_session

router = APIRouter(prefix="/api")


def _get_service(db: Session, name: str) -> Service:
    service = db.scalar(select(Service).where(Service.name == name))
    if service is None:
        raise ApiError(404, f"Service '{name}' is not registered")
    return service


def _latest_published(db: Session, service_id: int) -> SpecVersion | None:
    return db.scalar(
        select(SpecVersion)
        .where(SpecVersion.service_id == service_id, SpecVersion.status == "published")
        .order_by(SpecVersion.id.desc())
    )


def _get_version(db: Session, service: Service, label: str) -> SpecVersion:
    version = db.scalar(
        select(SpecVersion)
        .where(
            SpecVersion.service_id == service.id,
            SpecVersion.version_label == label,
        )
        .order_by(SpecVersion.id.desc())
    )
    if version is None:
        raise ApiError(404, f"Version '{label}' not found for service '{service.name}'")
    return version


def _service_dict(db: Session, service: Service) -> dict:
    latest = _latest_published(db, service.id)
    return {
        "id": service.id,
        "name": service.name,
        "owner_team": service.owner_team,
        "contact": service.contact,
        "latest_published": latest.version_label if latest else None,
    }


def _attempt_dict(attempt: PublishAttempt) -> dict:
    return {
        "id": attempt.id,
        "service": attempt.service.name if attempt.service else None,
        "version": attempt.version_label,
        "outcome": attempt.outcome,
        "compared_against": attempt.compared_against,
        "changes": attempt.changes,
        "affected_consumers": attempt.affected_consumers,
        "override_reason": attempt.override_reason,
        "error": attempt.error,
        "created_at": attempt.created_at.isoformat(),
    }


def _version_dict(version: SpecVersion) -> dict:
    return {
        "id": version.id,
        "service": version.service.name if version.service else None,
        "version": version.version_label,
        "status": version.status,
        "created_at": version.created_at.isoformat(),
    }


def _reject(
    db: Session,
    service: Service,
    version_label: str | None,
    compared_against: str | None,
    message: str,
) -> NoReturn:
    """Record a rejected attempt and answer 422 with the reason."""
    db.add(
        PublishAttempt(
            service_id=service.id,
            version_label=version_label or "",
            outcome="rejected",
            compared_against=compared_against,
            changes=[],
            affected_consumers=[],
            error=message,
        )
    )
    db.commit()
    raise ApiError(422, message)


@router.post("/services", status_code=201)
def create_service(payload: dict, db: Session = Depends(get_session)) -> dict:
    name = str(payload.get("name") or "").strip()
    owner_team = str(payload.get("owner_team") or "").strip()
    if not name or not owner_team:
        raise ApiError(422, "name and owner_team are required")
    if db.scalar(select(Service).where(Service.name == name)):
        raise ApiError(409, f"Service '{name}' already exists")
    service = Service(name=name, owner_team=owner_team, contact=payload.get("contact"))
    db.add(service)
    db.commit()
    return _service_dict(db, service)


@router.get("/services")
def list_services(db: Session = Depends(get_session)) -> list[dict]:
    services = list(db.scalars(select(Service).order_by(Service.name)))
    return [_service_dict(db, service) for service in services]


@router.get("/services/{name}")
def get_service(name: str, db: Session = Depends(get_session)) -> dict:
    return _service_dict(db, _get_service(db, name))


@router.post("/services/{name}/versions", status_code=201)
async def upload_version(
    name: str,
    request: Request,
    file: UploadFile | None = File(default=None),
    db: Session = Depends(get_session),
) -> dict:
    """Upload a spec version as a multipart file or as a raw YAML/JSON body."""
    service = _get_service(db, name)

    if file is not None:
        text = (await file.read()).decode("utf-8", "replace")
    else:
        text = (await request.body()).decode("utf-8", "replace")

    try:
        spec = load_spec(text)
    except SpecError as exc:
        _reject(db, service, None, None, str(exc))
    try:
        validate_spec(spec)
    except SpecError as exc:
        version = str((spec.get("info") or {}).get("version", "")) or None
        _reject(db, service, version, None, str(exc))

    version_label = str(spec["info"]["version"])
    last_published = _latest_published(db, service.id)
    if last_published is not None:
        try:
            ordering = compare_versions(version_label, last_published.version_label)
        except SpecError as exc:
            _reject(db, service, version_label, last_published.version_label, str(exc))
        if ordering <= 0:
            _reject(
                db,
                service,
                version_label,
                last_published.version_label,
                f"Version {version_label} must be higher than the last published "
                f"version {last_published.version_label}",
            )

    # The publish gate comes later; valid versions publish for now.
    normalized = normalize_spec(spec)
    db.add(
        SpecVersion(
            service_id=service.id,
            version_label=version_label,
            raw_spec=text,
            normalized_spec=normalized,
            status="published",
        )
    )
    db.add(
        PublishAttempt(
            service_id=service.id,
            version_label=version_label,
            outcome="published",
            compared_against=last_published.version_label if last_published else None,
            changes=[],
            affected_consumers=[],
        )
    )
    db.commit()
    return {"version": version_label, "endpoints": sorted(normalized["endpoints"])}


@router.get("/services/{name}/versions")
def list_versions(name: str, db: Session = Depends(get_session)) -> list[dict]:
    service = _get_service(db, name)
    versions = list(
        db.scalars(
            select(SpecVersion)
            .where(SpecVersion.service_id == service.id)
            .order_by(SpecVersion.id)
        )
    )
    return [_version_dict(version) for version in versions]


@router.get("/services/{name}/diff")
def diff_versions(
    name: str,
    from_version: str = Query(alias="from"),
    to_version: str = Query(alias="to"),
    db: Session = Depends(get_session),
) -> list[dict]:
    service = _get_service(db, name)
    old = _get_version(db, service, from_version)
    new = _get_version(db, service, to_version)
    if old.normalized_spec is None or new.normalized_spec is None:
        raise ApiError(404, "One of the versions has no normalized spec")
    return [change.to_dict() for change in diff_specs(old.normalized_spec, new.normalized_spec)]


@router.get("/services/{name}/attempts")
def list_attempts(name: str, db: Session = Depends(get_session)) -> list[dict]:
    service = _get_service(db, name)
    attempts = list(
        db.scalars(
            select(PublishAttempt)
            .where(PublishAttempt.service_id == service.id)
            .order_by(PublishAttempt.id)
        )
    )
    return [_attempt_dict(attempt) for attempt in attempts]
