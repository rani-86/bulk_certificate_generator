"""Pydantic models: request validation and response shapes."""
import re
from datetime import date, datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.certificate import is_renderable
from app.models import CertificateStatus, JobStatus

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


# ---------------------------------------------------------------- requests
class RecipientData(BaseModel):
    """One recipient. Validated per item (not by FastAPI), so a single bad
    recipient is reported as a failed certificate instead of rejecting the whole job."""

    model_config = ConfigDict(extra="forbid")  # typos like "emial" are reported, not ignored

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    email: str | None = None
    achievement: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None

    @field_validator("name", "achievement")
    @classmethod
    def _printable_and_renderable(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value.isprintable():
            raise ValueError("must not contain control characters or line breaks")
        if not is_renderable(value):
            raise ValueError("contains characters the certificate font cannot render (Latin characters only)")
        return value

    @field_validator("achievement")
    @classmethod
    def _empty_achievement_is_none(cls, value: str | None) -> str | None:
        return value or None

    @field_validator("email")
    @classmethod
    def _valid_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if len(value) > 254 or not _EMAIL_RE.match(value):
            raise ValueError("is not a valid email address")
        return value


class CertificateInfo(BaseModel):
    """Certificate details shared by every recipient in the job."""

    course_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    organization: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
    issued_on: date | None = Field(default=None, description="Defaults to today's date")
    signatory: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)] | None = None


class JobCreate(BaseModel):
    certificate: CertificateInfo
    # Items are validated individually in the service layer (see RecipientData).
    recipients: list[Any] = Field(min_length=1)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "certificate": {
                    "course_name": "Introduction to Data Science",
                    "organization": "Acme Academy",
                    "issued_on": "2026-10-06",
                    "signatory": "Dr. A. Kumar",
                },
                "recipients": [
                    {"name": "Asha Verma", "email": "asha@example.com", "achievement": "With Distinction"},
                    {"name": "Rahul Singh"},
                ],
            }
        }
    )


# --------------------------------------------------------------- responses
class ProgressOut(BaseModel):
    total: int
    pending: int
    completed: int
    failed: int
    percent_done: float  # (completed + failed) / total * 100


class JobOut(BaseModel):
    id: str
    status: JobStatus
    course_name: str
    organization: str
    signatory: str | None
    issued_on: date
    error: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    progress: ProgressOut
    links: dict[str, str]


class CertificateOut(BaseModel):
    id: str
    position: int
    recipient_name: str | None
    recipient_email: str | None
    achievement: str | None
    status: CertificateStatus
    error: str | None
    download_url: str | None  # only set once the certificate has been generated
    created_at: datetime
    completed_at: datetime | None


class CertificateListOut(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CertificateOut]
