"""Epoch seconds preserve UTC ordering identically in SQLite and PostgreSQL."""
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import BigInteger, Float, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .config import DATABASE_URL
from .sources import SourceRow


class Base(DeclarativeBase):
    pass


class Actual(Base):
    __tablename__ = "wind_actuals"
    start_ts: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    publish_ts: Mapped[float] = mapped_column(Float, primary_key=True)
    generation_mw: Mapped[float] = mapped_column(Float)


class Forecast(Base):
    __tablename__ = "wind_forecasts"
    start_ts: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    publish_ts: Mapped[float] = mapped_column(Float, primary_key=True)
    generation_mw: Mapped[float] = mapped_column(Float)


class Ingestion(Base):
    __tablename__ = "ingestion_runs"
    source_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    ingested_at: Mapped[str] = mapped_column(String(40))
    metadata_json: Mapped[str] = mapped_column(Text)


class SourceSync(Base):
    """Freshness is separate from content identity: unchanged data can be checked again."""
    __tablename__ = "source_sync"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_attempt_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_success_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


def make_engine(url: str = DATABASE_URL):
    if url.startswith("sqlite:///") and ":memory:" not in url:
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
                         pool_pre_ping=True)


def persist(engine, rows: list[SourceRow], source_hash: str, metadata: dict) -> dict:
    """Atomic import; repeated identical records are harmless, conflicts fail."""
    import json
    Base.metadata.create_all(engine)
    counts = {"inserted": 0, "duplicates": 0}
    with Session(engine) as session, session.begin():
        existing = {model: {(r.start_ts, r.publish_ts): r.generation_mw
                           for r in session.scalars(select(model))} for model in (Actual, Forecast)}
        for row in rows:
            if row.dataset == "FUELHH" and row.fuel_type != "WIND":
                raise ValueError("Actual source row must explicitly identify WIND")
            model = Actual if row.dataset == "FUELHH" else Forecast
            key = (int(row.start_time.timestamp()), row.publish_time.timestamp())
            if key in existing[model]:
                if existing[model][key] != row.generation_mw:
                    raise ValueError(f"Conflicting source record for {model.__tablename__} {key}")
                counts["duplicates"] += 1
                continue
            session.add(model(start_ts=key[0], publish_ts=key[1], generation_mw=row.generation_mw))
            existing[model][key] = row.generation_mw
            counts["inserted"] += 1
        if session.get(Ingestion, source_hash) is None:
            session.add(Ingestion(source_hash=source_hash, ingested_at=datetime.now(UTC).isoformat(),
                                  metadata_json=json.dumps(metadata)))
    return counts
