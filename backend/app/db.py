from datetime import datetime, timezone
from sqlalchemy import create_engine, ForeignKey, String, Text, DateTime, Integer, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from .config import DATABASE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(engine, expire_on_commit=False)

def now():
    return datetime.now(timezone.utc)

class Base(DeclarativeBase):
    pass

class Application(Base):
    __tablename__ = "applications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    category: Mapped[str] = mapped_column(String(150))
    hint: Mapped[str] = mapped_column(Text)

class ResearchRun(Base):
    __tablename__ = "research_runs"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("applications.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    stage: Mapped[str] = mapped_column(String(80), default="queued")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str] = mapped_column(String(100))
    buildability: Mapped[str] = mapped_column(String(40), default="Unclear")
    blocker: Mapped[str | None] = mapped_column(Text, nullable=True)
    sources: Mapped[list["Source"]] = relationship(back_populates="run")
    claims: Mapped[list["Claim"]] = relationship(back_populates="run")

class Source(Base):
    __tablename__ = "sources"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("research_runs.id"), index=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    run: Mapped[ResearchRun] = relationship(back_populates="sources")
    __table_args__ = (UniqueConstraint("run_id", "url"),)

class Claim(Base):
    __tablename__ = "claims"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("research_runs.id"), index=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"), nullable=True)
    dimension: Mapped[str] = mapped_column(String(30))
    tag: Mapped[str] = mapped_column(String(50), default="other")
    text: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="uncertain")
    reason: Mapped[str] = mapped_column(Text, default="")
    run: Mapped[ResearchRun] = relationship(back_populates="claims")
    source: Mapped[Source | None] = relationship()
    reviews: Mapped[list["Review"]] = relationship(back_populates="claim")

class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), index=True)
    verdict: Mapped[str] = mapped_column(String(30))
    note: Mapped[str] = mapped_column(Text)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    claim: Mapped[Claim] = relationship(back_populates="reviews")

class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    active_app_id: Mapped[int | None] = mapped_column(ForeignKey("applications.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

def init_db():
    from .dataset import APPS
    Base.metadata.create_all(engine)
    with SessionLocal.begin() as session:
        for app in APPS:
            if not session.get(Application, app["id"]):
                session.add(Application(**app))
