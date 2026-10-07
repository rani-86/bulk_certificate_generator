"""Database models: a Job owns many Certificates (one per submitted recipient)."""
import enum
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import JSON, Date, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class UTCDateTime(TypeDecorator):
    """Always hand back timezone-aware UTC datetimes (SQLite drops tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetimes are not allowed; use timezone-aware UTC.")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


def _enum(enum_cls):
    # Store the lowercase values ("pending") rather than member names, as plain strings.
    return Enum(enum_cls, native_enum=False, length=32, values_callable=lambda e: [m.value for m in e])


class JobStatus(str, enum.Enum):
    PENDING = "pending"                          # accepted, not started
    PROCESSING = "processing"                    # worker is generating certificates
    COMPLETED = "completed"                      # every certificate succeeded
    COMPLETED_WITH_ERRORS = "completed_with_errors"  # some succeeded, some failed
    FAILED = "failed"                            # nothing succeeded (or job crashed)


FINAL_JOB_STATUSES = {JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_ERRORS, JobStatus.FAILED}


class CertificateStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus), default=JobStatus.PENDING, index=True)

    # Certificate information shared by every recipient in the job.
    course_name: Mapped[str] = mapped_column(String(200))
    organization: Mapped[str] = mapped_column(String(200))
    signatory: Mapped[str | None] = mapped_column(String(200), nullable=True)
    issued_on: Mapped[date] = mapped_column(Date)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)  # job-level failure reason
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    certificates: Mapped[list["Certificate"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="Certificate.position"
    )


class Certificate(Base):
    __tablename__ = "certificates"
    __table_args__ = (
        UniqueConstraint("job_id", "position"),
        Index("ix_certificates_job_status", "job_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)  # index in the submitted recipients list

    recipient_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    recipient_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    achievement: Mapped[str | None] = mapped_column(String(100), nullable=True)
    raw_input: Mapped[Any] = mapped_column(JSON)  # exactly what the client sent, for debugging

    status: Mapped[CertificateStatus] = mapped_column(_enum(CertificateStatus), default=CertificateStatus.PENDING)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(300), nullable=True)  # relative to certificates_dir
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    job: Mapped[Job] = relationship(back_populates="certificates")
