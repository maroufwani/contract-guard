"""Seed the registry from the kit: services, consumers, then spec versions."""
from datetime import date
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.consumers import parse_consumer
from app.core.normalize import load_spec
from app.db.models import Consumer, ConsumerUsage, Service
from app.services.publish import publish_version

OWNERS = {
    "quote-api": "Quotes team",
    "policy-api": "Policy team",
    "claims-api": "Claims team",
}
SERVICE_ORDER = ["quote-api", "policy-api", "claims-api"]


def _seed_consumer(db: Session, path: Path) -> None:
    parsed = parse_consumer(load_spec(path.read_text(encoding="utf-8")))
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


def seed_kit(db: Session, kit_dir: Path | str, today: date) -> dict:
    """Import the kit once; skip everything if any service already exists."""
    kit = Path(kit_dir)
    if db.scalar(select(Service).limit(1)) is not None:
        return {"seeded": False}

    services: dict[str, Service] = {}
    for name in SERVICE_ORDER:
        service = Service(name=name, owner_team=OWNERS[name])
        db.add(service)
        services[name] = service
    db.commit()

    for consumer_path in sorted((kit / "consumers").glob("*.yaml")):
        _seed_consumer(db, consumer_path)
    db.commit()

    attempts = 0
    for name in SERVICE_ORDER:
        specs_dir = kit / "specs" / name
        for spec_path in sorted(specs_dir.glob("*.yaml")):
            publish_version(
                db,
                services[name],
                spec_path.read_text(encoding="utf-8"),
                today,
            )
            attempts += 1

    return {"seeded": True, "services": len(services), "attempts": attempts}


def main() -> None:
    from app.db.session import get_session_factory, init_db

    init_db()
    kit = Path(__file__).resolve().parents[1] / "kit"
    with get_session_factory()() as db:
        result = seed_kit(db, kit, date.today())
    print(result)


if __name__ == "__main__":
    main()
