"""Job status / progress reporting."""
from app import service
from app.models import JobStatus


def test_new_job_is_pending_with_zero_progress(client, db, info):
    job = service.create_job(db, info, [{"name": "A"}, {"name": "B"}, {"name": "C"}])  # not processed

    body = client.get(f"/api/v1/jobs/{job.id}").json()
    assert body["status"] == "pending"
    assert body["started_at"] is None and body["completed_at"] is None
    assert body["progress"] == {"total": 3, "pending": 3, "completed": 0, "failed": 0, "percent_done": 0.0}


def test_progress_is_visible_while_the_job_is_running(client, db, info, monkeypatch):
    job = service.create_job(db, info, [{"name": "A"}, {"name": "B"}, {"name": "C"}, {"name": "D"}])
    original = service.render_certificate
    session_factory = client.app.state.session_factory
    seen = []

    def spying_render(data):
        # Look at the job from a *separate* session, like a polling client would.
        with session_factory() as other:
            seen.append((other.get(type(job), job.id).status, service.get_progress(other, job.id)["completed"]))
        return original(data)

    monkeypatch.setattr(service, "render_certificate", spying_render)
    service.process_job(session_factory, client.app.state.settings.certificates_dir, job.id)

    assert seen == [(JobStatus.PROCESSING, 0), (JobStatus.PROCESSING, 1), (JobStatus.PROCESSING, 2), (JobStatus.PROCESSING, 3)]
    final = client.get(f"/api/v1/jobs/{job.id}").json()
    assert final["status"] == "completed"
    assert final["progress"]["percent_done"] == 100.0
    assert final["started_at"] and final["completed_at"]


def test_percent_done_counts_failed_certificates_as_processed(client, payload_factory):
    payload = payload_factory(["Ok One", "Ok Two"])
    payload["recipients"].append({"name": ""})
    payload["recipients"].append({"name": ""})
    job = client.post("/api/v1/jobs", json=payload).json()
    assert client.get(f"/api/v1/jobs/{job['id']}").json()["progress"]["percent_done"] == 100.0


def test_unknown_job_returns_404(client):
    assert client.get("/api/v1/jobs/does-not-exist").status_code == 404
    assert client.get("/api/v1/jobs/does-not-exist/certificates").status_code == 404


def test_processing_is_idempotent_and_resumable(client, db, info, settings):
    job = service.create_job(db, info, [{"name": "A"}, {"name": "B"}])
    factory = client.app.state.session_factory

    assert service.resume_unfinished_jobs(factory, settings.certificates_dir) == [job.id]  # picked up on restart
    service.process_job(factory, settings.certificates_dir, job.id)
    service.process_job(factory, settings.certificates_dir, job.id)  # second run is a no-op

    body = client.get(f"/api/v1/jobs/{job.id}").json()
    assert body["status"] == "completed"
    assert body["progress"]["completed"] == 2
    assert len(list((settings.certificates_dir / job.id).glob("*.pdf"))) == 2
    assert service.resume_unfinished_jobs(factory, settings.certificates_dir) == []  # nothing left to resume
