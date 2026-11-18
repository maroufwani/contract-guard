"""JSON API: dashboard metrics across every service and consumer."""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.consumers import _published_specs, _usage_dict
from app.core.impact import find_stale_references
from app.core.metrics import compute_metrics
from app.db.models import Consumer, ConsumerUsage, PublishAttempt
from app.db.session import get_session

router = APIRouter(prefix="/api")


def _attempt_dict(attempt: PublishAttempt) -> dict:
    return {
        "outcome": attempt.outcome,
        "changes": attempt.changes or [],
        "affected_consumers": attempt.affected_consumers or [],
        "unused_breaking": attempt.unused_breaking or [],
    }


def _stale_count(db: Session) -> int:
    """Stale references over every consumer, same rules as the consumer page."""
    total = 0
    consumers = list(db.scalars(select(Consumer).order_by(Consumer.name)))
    for consumer in consumers:
        usages = list(
            db.scalars(
                select(ConsumerUsage).where(
                    ConsumerUsage.consumer_id == consumer.id
                )
            )
        )
        by_provider: dict[str, list[dict]] = {}
        for usage in usages:
            item = _usage_dict(usage)
            by_provider.setdefault(item["provider"], []).append(
                {**item, "consumer": consumer.name, "team": consumer.team}
            )
        for provider, provider_usages in by_provider.items():
            total += len(
                find_stale_references(provider_usages, _published_specs(db, provider))
            )
    return total


@router.get("/metrics")
def get_metrics(db: Session = Depends(get_session)) -> dict:
    attempts = list(db.scalars(select(PublishAttempt).order_by(PublishAttempt.id)))
    return compute_metrics(
        [_attempt_dict(attempt) for attempt in attempts], _stale_count(db)
    )
