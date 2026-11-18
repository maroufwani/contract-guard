"""HTML pages. They call the same Python functions as the JSON API.

No page makes an HTTP call back into our own API: the routes use the domain
functions and the API helpers directly, with the request's own session.
"""
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.consumers import get_consumer, upsert_consumer
from app.api.errors import ApiError
from app.api.metrics import _attempt_dict, _stale_count
from app.api.services import _get_service, _get_version, create_service
from app.core.changelog import build_changelog
from app.core.classify import classify_all
from app.core.diff import diff_specs
from app.core.errors import SpecError
from app.core.impact import compute_impact
from app.core.metrics import compute_metrics
from app.core.normalize import load_spec, normalize_spec, validate_spec
from app.db.models import Consumer, ConsumerUsage, PublishAttempt, Service, SpecVersion
from app.db.session import get_session
from app.services.publish import latest_published, override_attempt, publish_version

TEMPLATES = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent / "templates")
)
router = APIRouter()

SEVERITIES = ("breaking", "warning", "safe")


def _render(
    request: Request, template: str, context: dict, status_code: int = 200
) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request=request, name=template, context=context, status_code=status_code
    )


def _counts(changes: list[dict] | None) -> dict[str, int]:
    changes = changes or []
    return {
        severity: sum(1 for change in changes if change.get("severity") == severity)
        for severity in SEVERITIES
    }


def _service_rows(db: Session) -> list[dict]:
    rows: list[dict] = []
    for service in db.scalars(select(Service).order_by(Service.name)):
        last = db.scalar(
            select(PublishAttempt)
            .where(PublishAttempt.service_id == service.id)
            .order_by(PublishAttempt.id.desc())
        )
        rows.append(
            {
                "service": service,
                "latest": latest_published(db, service.id),
                "last_attempt": last,
            }
        )
    return rows


def _consumer_rows(db: Session) -> list[dict]:
    rows: list[dict] = []
    for consumer in db.scalars(select(Consumer).order_by(Consumer.name)):
        detail = get_consumer(consumer.name, db)
        rows.append(
            {
                "consumer": consumer,
                "endpoint_count": len(detail["usages"]),
                "stale_count": len(detail["stale_references"]),
            }
        )
    return rows


def _versions(db: Session, service: Service) -> list[SpecVersion]:
    return list(
        db.scalars(
            select(SpecVersion)
            .where(SpecVersion.service_id == service.id)
            .order_by(SpecVersion.id.desc())
        )
    )


def _impact_usages(db: Session) -> list[dict]:
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


def _classify_changes(old: SpecVersion, new: SpecVersion) -> list[dict]:
    if old.normalized_spec is None or new.normalized_spec is None:
        raise ApiError(404, "One of the versions has no normalized spec")
    classified = classify_all(
        diff_specs(old.normalized_spec, new.normalized_spec), date.today()
    )
    return [
        {
            **change.to_dict(),
            "severity": classification.severity,
            "rule": classification.rule,
            "reason": classification.reason,
        }
        for change, classification in classified
    ]


EMPTY_SPEC_MESSAGE = "Choose a spec file or paste the YAML"


async def _form_text(request: Request) -> str:
    """Pasted YAML from the textarea, or the contents of the chosen file."""
    form = await request.form()
    upload = form.get("file")
    if upload is not None and getattr(upload, "filename", ""):
        return (await upload.read()).decode("utf-8", "replace")
    return str(form.get("spec_text") or "")


def _empty_spec_error(
    request: Request, service: Service
) -> HTMLResponse:
    return _render(
        request,
        "partials/impact_result.html",
        {"service": service, "error": EMPTY_SPEC_MESSAGE, "result": None},
    )


@router.get("/", response_class=HTMLResponse)
def services_page(request: Request, db: Session = Depends(get_session)):
    return _render(
        request,
        "index.html",
        {"rows": _service_rows(db), "error": None},
    )


@router.post("/services")
async def create_service_page(request: Request, db: Session = Depends(get_session)):
    form = await request.form()
    name = str(form.get("name") or "").strip()
    payload = {
        "name": name,
        "owner_team": str(form.get("owner_team") or "").strip(),
        "contact": str(form.get("contact") or "").strip() or None,
    }
    try:
        create_service(payload, db)
    except ApiError as exc:
        return _render(
            request,
            "index.html",
            {"rows": _service_rows(db), "error": exc.message},
            status_code=exc.status_code,
        )
    return RedirectResponse(f"/services/{name}", status_code=303)


@router.get("/services/{name}", response_class=HTMLResponse)
def service_page(
    name: str, request: Request, db: Session = Depends(get_session)
):
    service = _get_service(db, name)
    attempts = list(
        db.scalars(
            select(PublishAttempt)
            .where(PublishAttempt.service_id == service.id)
            .order_by(PublishAttempt.id.desc())
        )
    )
    history = [
        {"attempt": attempt, "counts": _counts(attempt.changes)} for attempt in attempts
    ]
    overridden = {
        attempt.version_label for attempt in attempts if attempt.outcome == "overridden"
    }
    return _render(
        request,
        "service.html",
        {
            "service": service,
            "versions": _versions(db, service),
            "history": history,
            "overridden": overridden,
            "error": None,
        },
    )


@router.post("/services/{name}/upload", response_class=HTMLResponse)
async def upload_page(
    name: str, request: Request, db: Session = Depends(get_session)
):
    service = _get_service(db, name)
    text = await _form_text(request)
    if not text.strip():
        return _empty_spec_error(request, service)
    result = publish_version(db, service, text, date.today())
    return _render(
        request,
        "partials/upload_result.html",
        {
            "service": service,
            "attempt": result.attempt,
            "endpoints": result.endpoints,
            "counts": _counts(result.attempt.changes),
        },
    )


@router.post("/services/{name}/impact", response_class=HTMLResponse)
async def impact_page(
    name: str, request: Request, db: Session = Depends(get_session)
):
    service = _get_service(db, name)
    text = await _form_text(request)
    if not text.strip():
        return _empty_spec_error(request, service)

    try:
        spec = load_spec(text)
        validate_spec(spec)
    except SpecError as exc:
        return _render(
            request,
            "partials/impact_result.html",
            {"service": service, "error": str(exc), "result": None},
        )

    normalized = normalize_spec(spec)
    last = latest_published(db, service.id)
    changes: list[dict] = []
    summary = {severity: 0 for severity in SEVERITIES}
    impact: dict = {"affected_consumers": [], "unused_breaking": []}

    if last is not None and last.normalized_spec is not None:
        classified = classify_all(
            diff_specs(last.normalized_spec, normalized), date.today()
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
        summary = _counts(changes)
        impact = compute_impact(classified, _impact_usages(db), service.name)

    return _render(
        request,
        "partials/impact_result.html",
        {
            "service": service,
            "error": None,
            "result": {
                "version": str(normalized.get("version") or ""),
                "compared_against": last.version_label if last else None,
                "changes": changes,
                "summary": summary,
                "affected_consumers": impact["affected_consumers"],
                "unused_breaking": impact["unused_breaking"],
            },
        },
    )


@router.get("/services/{name}/compare", response_class=HTMLResponse)
def compare_page(
    name: str,
    request: Request,
    from_version: str = Query(alias="from"),
    to_version: str = Query(alias="to"),
    db: Session = Depends(get_session),
):
    service = _get_service(db, name)
    old = _get_version(db, service, from_version)
    new = _get_version(db, service, to_version)
    changelog = build_changelog(
        _classify_changes(old, new), old.version_label, new.version_label
    )
    return _render(
        request,
        "compare.html",
        {
            "service": service,
            "versions": _versions(db, service),
            "changelog": changelog,
            "error": None,
        },
    )


@router.get("/services/{name}/attempts/{attempt_id}", response_class=HTMLResponse)
def attempt_page(
    name: str,
    attempt_id: int,
    request: Request,
    db: Session = Depends(get_session),
):
    service = _get_service(db, name)
    attempt = db.scalar(
        select(PublishAttempt).where(
            PublishAttempt.id == attempt_id,
            PublishAttempt.service_id == service.id,
        )
    )
    if attempt is None:
        raise ApiError(404, f"Attempt {attempt_id} not found for '{name}'")

    override = db.scalar(
        select(PublishAttempt)
        .where(
            PublishAttempt.service_id == service.id,
            PublishAttempt.version_label == attempt.version_label,
            PublishAttempt.outcome == "overridden",
        )
        .order_by(PublishAttempt.id.desc())
    )
    warnings = [
        change
        for change in attempt.changes or []
        if change.get("severity") == "warning"
    ]
    return _render(
        request,
        "attempt.html",
        {
            "service": service,
            "attempt": attempt,
            "override": override,
            "warnings": warnings,
            "counts": _counts(attempt.changes),
        },
    )


@router.post("/services/{name}/attempts/{attempt_id}/override")
def override_page(
    name: str,
    attempt_id: int,
    request: Request,
    approved_by: str = Form(default=""),
    reason: str = Form(default=""),
    db: Session = Depends(get_session),
):
    service = _get_service(db, name)
    try:
        override_attempt(db, service, attempt_id, approved_by, reason)
    except ApiError as exc:
        attempts = list(
            db.scalars(
                select(PublishAttempt)
                .where(PublishAttempt.service_id == service.id)
                .order_by(PublishAttempt.id.desc())
            )
        )
        return _render(
            request,
            "service.html",
            {
                "service": service,
                "versions": _versions(db, service),
                "history": [
                    {"attempt": attempt, "counts": _counts(attempt.changes)}
                    for attempt in attempts
                ],
                "overridden": {
                    attempt.version_label
                    for attempt in attempts
                    if attempt.outcome == "overridden"
                },
                "error": exc.message,
            },
            status_code=exc.status_code,
        )
    return RedirectResponse(f"/services/{name}", status_code=303)


@router.get("/consumers", response_class=HTMLResponse)
def consumers_page(request: Request, db: Session = Depends(get_session)):
    return _render(
        request,
        "consumers.html",
        {"rows": _consumer_rows(db), "error": None},
    )


@router.post("/consumers")
async def consumer_upload_page(
    request: Request, db: Session = Depends(get_session)
):
    form = await request.form()
    upload = form.get("file")
    if upload is None or not getattr(upload, "filename", ""):
        return _render(
            request,
            "consumers.html",
            {"rows": _consumer_rows(db), "error": "Choose a consumer YAML file."},
            status_code=422,
        )
    try:
        await upsert_consumer(request, file=upload, db=db)
    except ApiError as exc:
        return _render(
            request,
            "consumers.html",
            {"rows": _consumer_rows(db), "error": exc.message},
            status_code=exc.status_code,
        )
    return RedirectResponse("/consumers", status_code=303)


@router.get("/consumers/{name}", response_class=HTMLResponse)
def consumer_page(
    name: str, request: Request, db: Session = Depends(get_session)
):
    return _render(request, "consumer.html", {"detail": get_consumer(name, db)})


@router.get("/metrics", response_class=HTMLResponse)
def metrics_page(request: Request, db: Session = Depends(get_session)):
    attempts = list(db.scalars(select(PublishAttempt).order_by(PublishAttempt.id)))
    metrics = compute_metrics(
        [_attempt_dict(attempt) for attempt in attempts], _stale_count(db)
    )
    return _render(request, "metrics.html", {"metrics": metrics})
