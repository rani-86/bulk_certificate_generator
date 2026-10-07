import os
from datetime import date
import tempfile
import zipfile

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app import service
from app.certificate import CertificateData, CertificateGenerationError, render_certificate
from app.models import FINAL_JOB_STATUSES, Certificate, CertificateStatus, Job, JobStatus
from app.schemas import CertificateListOut, CertificateOut, JobCreate, JobOut, ProgressOut

router = APIRouter(prefix="/api/v1")


def get_db(request: Request):
    with request.app.state.session_factory() as db:
        yield db


# ---------------------------------------------------------------- helpers
def _job_or_404(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _certificate_or_404(db: Session, certificate_id: str) -> Certificate:
    cert = db.get(Certificate, certificate_id)
    if cert is None:
        raise HTTPException(status_code=404, detail="Certificate not found")
    return cert


def _job_out(db: Session, job: Job) -> JobOut:
    base = f"/api/v1/jobs/{job.id}"
    return JobOut(
        id=job.id,
        status=job.status,
        course_name=job.course_name,
        organization=job.organization,
        signatory=job.signatory,
        issued_on=job.issued_on,
        error=job.error,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        progress=ProgressOut(**service.get_progress(db, job.id)),
        links={"self": base, "certificates": f"{base}/certificates", "download": f"{base}/download"},
    )


def _certificate_out(cert: Certificate) -> CertificateOut:
    return CertificateOut(
        id=cert.id,
        position=cert.position,
        recipient_name=cert.recipient_name,
        recipient_email=cert.recipient_email,
        achievement=cert.achievement,
        status=cert.status,
        error=cert.error,
        download_url=(
            f"/api/v1/certificates/{cert.id}/download" if cert.status == CertificateStatus.COMPLETED else None
        ),
        created_at=cert.created_at,
        completed_at=cert.completed_at,
    )


# ------------------------------------------------------------------- jobs
@router.post("/jobs", response_model=JobOut, status_code=202, summary="Submit a bulk generation job")
def create_generation_job(
    payload: JobCreate,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    settings = request.app.state.settings
    if len(payload.recipients) > settings.max_recipients_per_job:
        raise HTTPException(
            status_code=422,
            detail=f"Too many recipients ({len(payload.recipients)}); the limit is "
            f"{settings.max_recipients_per_job} per job. Split the list into several jobs.",
        )

    # Fail fast if the shared certificate details can't be rendered at all, instead of
    # accepting the job and then failing every single certificate in it.
    try:
        render_certificate(
            CertificateData(
                certificate_id="preview",
                recipient_name="Sample Recipient",
                course_name=payload.certificate.course_name,
                organization=payload.certificate.organization,
                issued_on=payload.certificate.issued_on or date.today(),
                signatory=payload.certificate.signatory,
            )
        )
    except CertificateGenerationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid certificate details: {exc}")

    job = service.create_job(db, payload.certificate, payload.recipients)

    if job.status == JobStatus.PENDING:  # nothing to schedule if every recipient was invalid
        background_tasks.add_task(
            service.process_job, request.app.state.session_factory, settings.certificates_dir, job.id
        )

    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    return _job_out(db, job)


@router.get("/jobs/{job_id}", response_model=JobOut, summary="Job status and progress")
def get_job(job_id: str, db: Session = Depends(get_db)):
    return _job_out(db, _job_or_404(db, job_id))


@router.get(
    "/jobs/{job_id}/certificates",
    response_model=CertificateListOut,
    summary="Per-recipient results (filter by status to find failures)",
)
def list_job_certificates(
    job_id: str,
    status: CertificateStatus | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    _job_or_404(db, job_id)
    query = select(Certificate).where(Certificate.job_id == job_id)
    if status is not None:
        query = query.where(Certificate.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(Certificate.position).limit(limit).offset(offset)).all()
    return CertificateListOut(total=total, limit=limit, offset=offset, items=[_certificate_out(c) for c in rows])


@router.get("/jobs/{job_id}/download", summary="Download all generated certificates as a ZIP")
def download_job_zip(job_id: str, request: Request, db: Session = Depends(get_db)):
    job = _job_or_404(db, job_id)
    if job.status not in FINAL_JOB_STATUSES:
        raise HTTPException(status_code=409, detail=f"Job is still {job.status.value}; try again when it has finished")

    certs = db.scalars(
        select(Certificate)
        .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.COMPLETED)
        .order_by(Certificate.position)
    ).all()
    if not certs:
        raise HTTPException(status_code=409, detail="This job has no generated certificates")

    storage_dir = request.app.state.settings.certificates_dir
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()
    with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as archive:
        for cert in certs:
            archive.write(storage_dir / cert.file_path, arcname=service.download_filename(cert))

    return FileResponse(
        tmp.name,
        media_type="application/zip",
        filename=f"certificates-{job.id}.zip",
        background=BackgroundTask(os.unlink, tmp.name),  # clean up the temp file after sending
    )


# ----------------------------------------------------------- certificates
@router.get("/certificates/{certificate_id}", response_model=CertificateOut, summary="Certificate status")
def get_certificate(certificate_id: str, db: Session = Depends(get_db)):
    return _certificate_out(_certificate_or_404(db, certificate_id))


@router.get("/certificates/{certificate_id}/download", summary="Download one certificate (PDF)")
def download_certificate(certificate_id: str, request: Request, db: Session = Depends(get_db)):
    cert = _certificate_or_404(db, certificate_id)
    if cert.status != CertificateStatus.COMPLETED:
        detail = f"Certificate is {cert.status.value}"
        if cert.error:
            detail += f": {cert.error}"
        raise HTTPException(status_code=409, detail=detail)

    path = request.app.state.settings.certificates_dir / cert.file_path
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Certificate file is missing from storage")
    return FileResponse(path, media_type="application/pdf", filename=service.download_filename(cert))
