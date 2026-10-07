# Bulk Certificate Generator

A backend API that accepts **one request containing many recipients**, generates a PDF certificate for each valid recipient from a single predefined template, tracks progress, and lets the client retrieve the results (individually or as a ZIP).

**Stack:** Python 3.10+, FastAPI, SQLAlchemy 2 (SQLite by default, any SQL database via `DATABASE_URL`), ReportLab (PDF), pytest.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt                    # requirements.txt for runtime only
```

## Run

```bash
uvicorn app.main:app --reload
```

- API: http://127.0.0.1:8000  ·  Interactive docs (Swagger): http://127.0.0.1:8000/docs
- Data is stored in `./data/` (SQLite file + generated PDFs).

| Env var | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/certificates.db` | Any SQLAlchemy URL (e.g. PostgreSQL) |
| `CERTIFICATES_DIR` | `./data/certificates` | Where PDFs are written |
| `MAX_RECIPIENTS_PER_JOB` | `1000` | Request size limit |
| `RESUME_UNFINISHED_JOBS` | `true` | Re-queue interrupted jobs on startup |

## Run tests

```bash
pytest
```

Tests use a temporary database and directory per test, so they never touch `./data`.

## Using the API

### 1. Submit a job — `POST /api/v1/jobs`

```bash
curl -X POST http://127.0.0.1:8000/api/v1/jobs \
  -H "Content-Type: application/json" \
  -d @examples/sample_request.json
```

Returns `202 Accepted` immediately (plus a `Location` header) with the job id:

```json
{
  "id": "9f1c...", "status": "pending", "course_name": "Introduction to Data Science",
  "progress": {"total": 4, "pending": 2, "completed": 0, "failed": 2, "percent_done": 50.0},
  "links": {"self": "/api/v1/jobs/9f1c...", "certificates": "/api/v1/jobs/9f1c.../certificates", "download": "/api/v1/jobs/9f1c.../download"}
}
```

Request fields:

| Field | Required | Rules |
|---|---|---|
| `certificate.course_name` | yes | 1–80 chars |
| `certificate.organization` | yes | 1–60 chars |
| `certificate.issued_on` | no | `YYYY-MM-DD`, defaults to today |
| `certificate.signatory` | no | ≤ 40 chars |
| `recipients[]` | yes | 1 to `MAX_RECIPIENTS_PER_JOB` items |
| `recipients[].name` | yes | 1–80 chars, printable, Latin characters only |
| `recipients[].email` | no | valid email format |
| `recipients[].achievement` | no | ≤ 100 chars (e.g. "With Distinction") |

### 2. Track progress — `GET /api/v1/jobs/{job_id}`

```bash
curl http://127.0.0.1:8000/api/v1/jobs/<job_id>
```

`status` is one of `pending`, `processing`, `completed`, `completed_with_errors`, `failed`. `progress` has `total / pending / completed / failed / percent_done`.

### 3. See who succeeded or failed — `GET /api/v1/jobs/{job_id}/certificates`

```bash
curl "http://127.0.0.1:8000/api/v1/jobs/<job_id>/certificates?status=failed"
```

Query params: `status` (`pending|completed|failed`), `limit` (1–500, default 100), `offset`. Each item has `position` (index in your request), `recipient_name`, `status`, `error` (why it failed) and `download_url` (when completed).

### 4. Retrieve certificates

```bash
# one certificate (PDF)
curl -OJ http://127.0.0.1:8000/api/v1/certificates/<certificate_id>/download
# every successful certificate in a job (ZIP, only once the job has finished)
curl -OJ http://127.0.0.1:8000/api/v1/jobs/<job_id>/download
```

Other endpoints: `GET /api/v1/certificates/{id}` (single certificate status), `GET /health`.

Error codes: `422` invalid request, `404` unknown job/certificate, `409` certificate not ready/failed or job not finished.

## Design decisions

**Background processing (not synchronous).** `POST /jobs` validates, stores the job, returns `202` and generates certificates after the response (FastAPI `BackgroundTasks`, in a worker thread). A synchronous design would hold the HTTP connection open for the whole batch (≈3 s for 1000 certificates here, but minutes for heavier templates), risk client/proxy timeouts, and give no progress. The cost is the status endpoint. I chose in-process background tasks over Celery/RQ because the task asks for a self-contained service with no extra infrastructure; the processing function (`service.process_job`) takes only a job id, so moving it to a real queue later is a small change.

**Two-level validation.**
1. *Request level* (FastAPI/Pydantic, `422`): malformed body, missing/blank course or organization, bad date, empty list, too many recipients. Certificate details are also test-rendered once so unrenderable shared details fail fast instead of failing every certificate.
2. *Recipient level* (stored per item): an invalid recipient does **not** reject the request. It is saved as a `failed` certificate with a readable reason (e.g. `Invalid recipient: email: is not a valid email address`) and the valid ones proceed. Unknown fields are rejected so typos like `emial` are visible.

**Failure isolation.** Each certificate is generated inside its own `try/except` and committed individually. A failure is recorded on that certificate only. A job ends as `completed` (all ok), `completed_with_errors` (mixed) or `failed` (nothing succeeded, or an unexpected job-level crash, recorded in `error`).

**Data model.** `jobs` (shared certificate info, status, timestamps) 1—N `certificates` (one per submitted recipient, with `position`, `status`, `error`, `file_path`, and the raw input). Progress is computed with a `GROUP BY` on certificate statuses rather than stored counters, so it cannot drift out of sync.

**Storage.** PDFs are files under `CERTIFICATES_DIR/<job_id>/<certificate_id>.pdf`; the DB stores the relative path. Files are written to a temp name then renamed, so a crash never leaves a half-written PDF. (For production, swap for object storage such as S3.)

**Restart safety.** Processing only touches `pending` certificates and ignores finished jobs, so it is idempotent. On startup, jobs left `pending`/`processing` by a crash are resumed.

**Progress visibility.** One commit per certificate so clients polling the status endpoint see live progress. SQLite runs in WAL mode so reads are not blocked by the worker's writes. Batching commits would be faster for very large jobs at the cost of coarser progress.

**Template.** A single hard-coded A4 landscape design in `app/certificate.py` (`render_certificate` is a pure function: data in, PDF bytes out). Names/course text shrink to fit; if they still don't fit, that certificate fails with a clear error.

## Known limitations / possible extensions

- Built-in PDF fonts only support Latin characters (accents like é/ü are fine). Names in other scripts (e.g. Devanagari) are reported as failed with a clear reason. Supporting them means registering a Unicode TTF font in `app/certificate.py`.
- In-process workers don't scale across multiple server instances; use Celery/RQ + Postgres for that.
- No authentication, rate limiting, retries of failed items, or idempotency keys.

## Project layout

```
app/
  main.py         app factory, startup (resume unfinished jobs)
  api.py          HTTP endpoints
  service.py      validation, job creation, processing, progress
  certificate.py  PDF template/rendering
  models.py       SQLAlchemy models (Job, Certificate)
  schemas.py      request/response models
  config.py, database.py
tests/            pytest suite (certificate, API, failures, progress, retrieval)
examples/         sample_request.json
```
