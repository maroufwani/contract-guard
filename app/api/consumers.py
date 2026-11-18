"""JSON API: register consumers and show their usages and stale references."""
from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.core.consumers import parse_consumer
from app.core.impact import find_stale_references
from app.core.normalize import load_spec
from app.db.models import Consumer, ConsumerUsage, Service, SpecVersion
from app.db.session import get_session

router = APIRouter(prefix="/api")


def _usage_dict(usage: ConsumerUsage) -> dict:
    return {
        "provider": usage.provider,
        "method": usage.method,
        "path": usage.path,
        "sends": usage.sends or [],
        "reads": usage.reads or [],
    }


def _usages(db: Session, consumer: Consumer) -> list[ConsumerUsage]:
    return list(
        db.scalars(
            select(ConsumerUsage)
            .where(ConsumerUsage.consumer_id == consumer.id)
            .order_by(ConsumerUsage.id)
        )
    )


def _consumer_dict(db: Session, consumer: Consumer) -> dict:
    return {
        "name": consumer.name,
        "consumer": consumer.name,
        "team": consumer.team,
        "usages": [_usage_dict(usage) for usage in _usages(db, consumer)],
    }


def _get_consumer(db: Session, name: str) -> Consumer:
    consumer = db.scalar(select(Consumer).where(Consumer.name == name))
    if consumer is None:
        raise ApiError(404, f"Consumer '{name}' is not registered")
    return consumer


async def _read_upload(request: Request, file: UploadFile | None) -> str:
    if file is not None:
        return (await file.read()).decode("utf-8", "replace")
    return (await request.body()).decode("utf-8", "replace")


@router.post("/consumers", status_code=201)
async def upsert_consumer(
    request: Request,
    file: UploadFile | None = File(default=None),
    db: Session = Depends(get_session),
) -> dict:
    """Create a consumer, or replace an existing consumer's usages."""
    text = await _read_upload(request, file)
    try:
        parsed = parse_consumer(load_spec(text))
    except ValueError as exc:
        raise ApiError(422, str(exc)) from exc

    consumer = db.scalar(select(Consumer).where(Consumer.name == parsed["consumer"]))
    if consumer is None:
        consumer = Consumer(name=parsed["consumer"], team=parsed["team"])
        db.add(consumer)
        db.flush()
    else:
        consumer.team = parsed["team"]
        db.execute(
            delete(ConsumerUsage).where(ConsumerUsage.consumer_id == consumer.id)
        )

    for use in parsed["uses"]:
        db.add(
            ConsumerUsage(
                consumer_id=consumer.id,
                provider=use["provider"],
                method=use["method"],
                path=use["path"],
                sends=use["sends"],
                reads=use["reads"],
            )
        )
    db.commit()
    return _consumer_dict(db, consumer)


@router.get("/consumers")
def list_consumers(db: Session = Depends(get_session)) -> list[dict]:
    consumers = list(db.scalars(select(Consumer).order_by(Consumer.name)))
    return [_consumer_dict(db, consumer) for consumer in consumers]


@router.get("/consumers/{name}")
def get_consumer(name: str, db: Session = Depends(get_session)) -> dict:
    consumer = _get_consumer(db, name)
    usages = [_usage_dict(usage) for usage in _usages(db, consumer)]

    by_provider: dict[str, list[dict]] = {}
    for usage in usages:
        by_provider.setdefault(usage["provider"], []).append(
            {**usage, "consumer": consumer.name, "team": consumer.team}
        )

    stale: list[dict] = []
    for provider, provider_usages in sorted(by_provider.items()):
        published = _published_specs(db, provider)
        stale.extend(find_stale_references(provider_usages, published))

    return {
        "name": consumer.name,
        "consumer": consumer.name,
        "team": consumer.team,
        "usages": usages,
        "stale_references": stale,
    }


def _published_specs(db: Session, provider: str) -> list[tuple[str, dict]]:
    service = db.scalar(select(Service).where(Service.name == provider))
    if service is None:
        return []
    versions = db.scalars(
        select(SpecVersion)
        .where(
            SpecVersion.service_id == service.id,
            SpecVersion.status.in_(("published", "overridden")),
            SpecVersion.normalized_spec.is_not(None),
        )
        .order_by(SpecVersion.id)
    )
    return [
        (version.version_label, version.normalized_spec)
        for version in versions
        if version.normalized_spec is not None
    ]
