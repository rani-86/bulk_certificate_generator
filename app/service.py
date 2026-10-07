"""Business logic: creating jobs, validating recipients and generating certificates."""
import logging
import os
import re
import threading
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.certificate import CertificateData, render_certificate
from app.models import (
    FINAL_JOB_STATUSES,
    Certificate,
    CertificateStatus,
    Job,
    JobStatus,
    utcnow,
)
from app.schemas import CertificateInfo, RecipientData

logger = logging.getLogger(__name__)


# ------------------------------------------------------------- validation
def validate_recipient(raw: Any) -> RecipientData:
    """Validate one raw recipient; raises ValueError with a readable message."""
    if not isinstance(raw, dict):
        raise ValueError("recipient must be a JSON object")
    try:
        return RecipientData.model_validate(raw)
    except ValidationError as exc:
        problems = []
        for err in exc.errors():
            field = ".".join(str(p) for p in err["loc"]) or "recipient"
            problems.append(f"{field}: {err['msg'].removeprefix('Value error, ')}")
        raise ValueError("; ".join(problems)) from None


# --------------------------------------------------------------- creating
def create_job(db: Session, info: CertificateInfo, recipients: list[Any]) -> Job:
    """Persist a job plus one Certificate row per recipient.

    Invalid recipients are stored immediately as FAILED (with the reason), so the
    client can see exactly which entries were rejected; valid ones stay PENDING.
    """
    now = utcnow()
    job = Job(
        course_name=info.course_name,
        organization=info.organization,
        signatory=info.signatory,
        issued_on=info.issued_on or date.today(),
    )

    pending = 0
    for position, raw in enumerate(recipients):
        cert = Certificate(position=position, raw_input=raw)
        try:
            data = validate_recipient(raw)
        except ValueError as exc:
            cert.status = CertificateStatus.FAILED
            cert.error = f"Invalid recipient: {exc}"
            cert.completed_at = now
            if isinstance(raw, dict) and isinstance(raw.get("name"), str):
                cert.recipient_name = raw["name"][:200]
        else:
            cert.recipient_name = data.name
            cert.recipient_email = data.email
            cert.achievement = data.achievement
            pending += 1
        job.certificates.append(cert)

    if pending == 0:
        job.status = JobStatus.FAILED
        job.error = "No valid recipients in request"
        job.completed_at = now

    db.add(job)
    db.commit()
    return job


# --------------------------------------------------------------- progress
def get_progress(db: Session, job_id: str) -> dict:
    rows = db.execute(
        select(Certificate.status, func.count())
        .where(Certificate.job_id == job_id)
        .group_by(Certificate.status)
    ).all()
    counts = {status: n for status, n in rows}
    completed = counts.get(CertificateStatus.COMPLETED, 0)
    failed = counts.get(CertificateStatus.FAILED, 0)
    pending = counts.get(CertificateStatus.PENDING, 0)
    total = completed + failed + pending
    percent = round((completed + failed) / total * 100, 1) if total else 100.0
    return {"total": total, "pending": pending, "completed": completed, "failed": failed, "percent_done": percent}


# ------------------------------------------------------------- processing
def process_job(session_factory: sessionmaker, storage_dir: Path, job_id: str) -> None:
    """Generate every PENDING certificate of a job. Safe to call more than once:
    it only touches certificates that are still pending, so it can also resume a
    job that was interrupted by a restart."""
    with session_factory() as db:
        job = db.get(Job, job_id)
        if job is None or job.status in FINAL_JOB_STATUSES:
            return

        try:
            job.status = JobStatus.PROCESSING
            job.started_at = job.started_at or utcnow()
            db.commit()

            pending_ids = db.scalars(
                select(Certificate.id)
                .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.PENDING)
                .order_by(Certificate.position)
            ).all()

            for cert_id in pending_ids:
                _generate_one(db, job, db.get(Certificate, cert_id), storage_dir)

            _finalize(db, job)
        except Exception as exc:  # unexpected, job-level failure (e.g. database went away)
            logger.exception("Job %s crashed", job_id)
            db.rollback()
            job.status = JobStatus.FAILED
            job.error = f"{type(exc).__name__}: {exc}"[:500]
            job.completed_at = utcnow()
            db.commit()


def _generate_one(db: Session, job: Job, cert: Certificate, storage_dir: Path) -> None:
    """Generate one certificate. Any error is recorded on that certificate only,
    so one bad recipient never stops the rest of the job."""
    try:
        pdf_bytes = render_certificate(
            CertificateData(
                certificate_id=cert.id,
                recipient_name=cert.recipient_name,
                course_name=job.course_name,
                organization=job.organization,
                issued_on=job.issued_on,
                achievement=cert.achievement,
                signatory=job.signatory,
            )
        )
        relative_path = f"{job.id}/{cert.id}.pdf"
        _write_atomically(storage_dir / relative_path, pdf_bytes)
        cert.status = CertificateStatus.COMPLETED
        cert.file_path = relative_path
        cert.error = None
    except Exception as exc:
        logger.warning("Certificate %s (job %s) failed: %s", cert.id, job.id, exc)
        cert.status = CertificateStatus.FAILED
        cert.error = f"{type(exc).__name__}: {exc}"[:500]
    cert.completed_at = utcnow()
    db.commit()  # commit per certificate so progress is visible while the job runs


def _write_atomically(path: Path, content: bytes) -> None:
    """Write to a temp file then rename, so a crash never leaves a half-written PDF."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(content)
    os.replace(tmp, path)


def _finalize(db: Session, job: Job) -> None:
    progress = get_progress(db, job.id)
    if progress["failed"] == 0:
        job.status = JobStatus.COMPLETED
    elif progress["completed"] == 0:
        job.status = JobStatus.FAILED
        job.error = "Every certificate in this job failed"
    else:
        job.status = JobStatus.COMPLETED_WITH_ERRORS
    job.completed_at = utcnow()
    db.commit()


# ---------------------------------------------------------------- restart
def resume_unfinished_jobs(session_factory: sessionmaker, storage_dir: Path) -> list[str]:
    """Called on startup: jobs interrupted by a crash/restart are picked up again."""
    with session_factory() as db:
        job_ids = db.scalars(
            select(Job.id).where(Job.status.in_([JobStatus.PENDING, JobStatus.PROCESSING]))
        ).all()
    for job_id in job_ids:
        threading.Thread(
            target=process_job, args=(session_factory, storage_dir, job_id), daemon=True
        ).start()
    return list(job_ids)


# ---------------------------------------------------------------- helpers
def download_filename(cert: Certificate) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", cert.recipient_name or "").strip("-").lower() or "certificate"
    return f"{slug}-{cert.id[:8]}.pdf"
