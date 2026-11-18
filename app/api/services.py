"""JSON API: register services, upload spec versions, read history."""
from datetime import date

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.core.changelog import build_changelog, to_markdown
from app.core.classify import classify_all
from app.core.diff import diff_specs
from app.core.errors import SpecError
from app.core.impact import compute_impact
from app.core.normalize import load_spec, normalize_spec, validate_spec
from app.db.models import ConsumerUsage, PublishAttempt, Service, SpecVersion
from app.db.session import get_session
from app.services.publish import override_attempt, publish_version

router = APIRouter(prefix="/api")


def _get_service(db: Session, name: str) -> Service:
    service = db.scalar(select(Service).where(Service.name == name))
    if service is None:
        raise ApiError(404, f"Service '{name}' is not registered")
    return service


def _latest_published(db: Session, service_id: int) -> SpecVersion | None:
    return db.scalar(
        select(SpecVersion)
        .where(
            SpecVersion.service_id == service_id,
            SpecVersion.status.in_(("published", "overridden")),
        )
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
        "unused_breaking": attempt.unused_breaking,
        "reasons": attempt.reasons,
        "approved_by": attempt.approved_by,
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
    today: date | None = Query(default=None),
    db: Session = Depends(get_session),
) -> JSONResponse:
    """Upload a spec version as a multipart file or as a raw YAML/JSON body."""
    service = _get_service(db, name)

    if file is not None:
        text = (await file.read()).decode("utf-8", "replace")
    else:
        text = (await request.body()).decode("utf-8", "replace")

    if not text.strip():
        raise ApiError(422, "Choose a spec file or paste the YAML")

    result = publish_version(db, service, text, today or date.today())
    body = _attempt_dict(result.attempt)
    if result.endpoints is not None:
        body["endpoints"] = result.endpoints
    return JSONResponse(status_code=result.status_code, content=body)


@router.post("/services/{name}/impact")
async def spec_impact(
    name: str,
    request: Request,
    file: UploadFile | None = File(default=None),
    today: date | None = Query(default=None),
    db: Session = Depends(get_session),
) -> dict:
    """Diff a proposed spec against the last published one; save nothing."""
    service = _get_service(db, name)

    if file is not None:
        text = (await file.read()).decode("utf-8", "replace")
    else:
        text = (await request.body()).decode("utf-8", "replace")

    try:
        spec = load_spec(text)
        validate_spec(spec)
    except SpecError as exc:
        raise ApiError(422, str(exc)) from exc

    normalized = normalize_spec(spec)
    version_label = str(normalized.get("version") or "")
    last_published = _latest_published(db, service.id)

    changes: list[dict] = []
    summary = {"breaking": 0, "warning": 0, "safe": 0}
    impact = {"affected_consumers": [], "unused_breaking": []}

    if last_published is not None and last_published.normalized_spec is not None:
        classified = classify_all(
            diff_specs(last_published.normalized_spec, normalized),
            today or date.today(),
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
        summary = {
            severity: sum(1 for item in changes if item["severity"] == severity)
            for severity in ("breaking", "warning", "safe")
        }

        usages = list(db.scalars(select(ConsumerUsage).order_by(ConsumerUsage.id)))
        impact = compute_impact(
            classified,
            [
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
            ],
            service.name,
        )

    return {
        "version": version_label,
        "compared_against": (
            last_published.version_label if last_published is not None else None
        ),
        "changes": changes,
        "summary": summary,
        **impact,
    }


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
    today: date | None = Query(default=None),
    db: Session = Depends(get_session),
) -> dict:
    service = _get_service(db, name)
    old = _get_version(db, service, from_version)
    new = _get_version(db, service, to_version)
    if old.normalized_spec is None or new.normalized_spec is None:
        raise ApiError(404, "One of the versions has no normalized spec")

    classified = classify_all(
        diff_specs(old.normalized_spec, new.normalized_spec),
        today or date.today(),
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
    summary = {
        severity: sum(1 for item in changes if item["severity"] == severity)
        for severity in ("breaking", "warning", "safe")
    }
    return {"changes": changes, "summary": summary}


@router.get("/services/{name}/changelog")
def changelog(
    name: str,
    from_version: str = Query(alias="from"),
    to_version: str = Query(alias="to"),
    format: str = Query(default="json"),
    today: date | None = Query(default=None),
    db: Session = Depends(get_session),
):
    """Changelog between any two stored versions, in the order asked for."""
    if format not in {"json", "markdown"}:
        raise ApiError(422, f"Unknown format '{format}'; use json or markdown")

    service = _get_service(db, name)
    old = _get_version(db, service, from_version)
    new = _get_version(db, service, to_version)
    if old.normalized_spec is None or new.normalized_spec is None:
        raise ApiError(404, "One of the versions has no normalized spec")

    classified = classify_all(
        diff_specs(old.normalized_spec, new.normalized_spec),
        today or date.today(),
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
    result = build_changelog(changes, old.version_label, new.version_label)
    if format == "markdown":
        return PlainTextResponse(to_markdown(result), media_type="text/markdown")
    return result


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


@router.post("/services/{name}/attempts/{attempt_id}/override", status_code=201)
def override_publish(
    name: str,
    attempt_id: int,
    payload: dict,
    db: Session = Depends(get_session),
) -> dict:
    service = _get_service(db, name)
    result = override_attempt(
        db,
        service,
        attempt_id,
        str(payload.get("approved_by") or ""),
        str(payload.get("reason") or ""),
    )
    return _attempt_dict(result.attempt)
