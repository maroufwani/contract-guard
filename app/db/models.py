"""SQLAlchemy models for the registry (data model in docs/PLAN.md)."""
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Service(Base):
    """A provider API owned by one team, for example quote-api."""

    __tablename__ = "services"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    owner_team: Mapped[str] = mapped_column(String(100))
    contact: Mapped[str | None] = mapped_column(String(200), default=None)

    spec_versions: Mapped[list["SpecVersion"]] = relationship(back_populates="service")
    publish_attempts: Mapped[list["PublishAttempt"]] = relationship(back_populates="service")


class SpecVersion(Base):
    """One uploaded version of a service's OpenAPI spec.

    status is set when a publish attempt is decided: published, blocked,
    rejected or overridden.
    """

    __tablename__ = "spec_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    version_label: Mapped[str] = mapped_column(String(50))
    raw_spec: Mapped[str] = mapped_column(Text)
    normalized_spec: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    status: Mapped[str | None] = mapped_column(String(20), default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    service: Mapped["Service"] = relationship(back_populates="spec_versions")


class PublishAttempt(Base):
    """One attempt to publish a spec version and what came out of the diff.

    outcome is one of: published, blocked, rejected, overridden.
    compared_against is the version label this spec was diffed with (None for
    the first version of a service). changes is a JSON list of entries like
    {path, change, severity, reason}.
    """

    __tablename__ = "publish_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    version_label: Mapped[str] = mapped_column(String(50))
    outcome: Mapped[str] = mapped_column(String(20))
    compared_against: Mapped[str | None] = mapped_column(String(50), default=None)
    changes: Mapped[list[Any]] = mapped_column(JSON, default=list)
    affected_consumers: Mapped[list[Any]] = mapped_column(JSON, default=list)
    override_reason: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    service: Mapped["Service"] = relationship(back_populates="publish_attempts")


class Consumer(Base):
    """A service that calls other services' APIs, for example mobile-app."""

    __tablename__ = "consumers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    team: Mapped[str] = mapped_column(String(100))

    usages: Mapped[list["ConsumerUsage"]] = relationship(back_populates="consumer")


class ConsumerUsage(Base):
    """One endpoint a consumer calls, and the fields it sends and reads.

    provider is the provider service name from the consumer YAML (for example
    quote-api). sends and reads are field path lists in the kit notation.
    """

    __tablename__ = "consumer_usages"

    id: Mapped[int] = mapped_column(primary_key=True)
    consumer_id: Mapped[int] = mapped_column(ForeignKey("consumers.id"))
    provider: Mapped[str] = mapped_column(String(100))
    method: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(String(200))
    sends: Mapped[list[Any]] = mapped_column(JSON, default=list)
    reads: Mapped[list[Any]] = mapped_column(JSON, default=list)

    consumer: Mapped["Consumer"] = relationship(back_populates="usages")
