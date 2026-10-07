"""One failing certificate must not stop the rest of the job."""
import pytest

from app import service


@pytest.fixture
def flaky_renderer(monkeypatch):
    """Make the renderer blow up for recipients named 'Boom'."""
    original = service.render_certificate

    def render(data):
        if data.recipient_name == "Boom":
            raise RuntimeError("renderer exploded")
        return original(data)

    monkeypatch.setattr(service, "render_certificate", render)


def test_failure_in_the_middle_does_not_block_other_certificates(client, payload_factory, flaky_renderer, settings):
    job_id = client.post("/api/v1/jobs", json=payload_factory(["Alice", "Boom", "Carol"])).json()["id"]

    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "completed_with_errors"
    assert job["progress"] == {"total": 3, "pending": 0, "completed": 2, "failed": 1, "percent_done": 100.0}

    items = client.get(f"/api/v1/jobs/{job_id}/certificates").json()["items"]
    alice, boom, carol = items  # ordered by position
    assert alice["status"] == carol["status"] == "completed"
    assert boom["status"] == "failed"
    assert "renderer exploded" in boom["error"]
    assert boom["download_url"] is None

    # Only the successful certificates exist on disk.
    assert len(list((settings.certificates_dir / job_id).glob("*.pdf"))) == 2

    # And the successful ones are retrievable.
    assert client.get(alice["download_url"]).status_code == 200
    assert client.get(carol["download_url"]).status_code == 200


def test_job_is_failed_when_every_certificate_fails(client, payload_factory, flaky_renderer):
    job_id = client.post("/api/v1/jobs", json=payload_factory(["Boom", "Boom"])).json()["id"]
    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "failed"
    assert job["progress"]["failed"] == 2
    assert job["error"] == "Every certificate in this job failed"


def test_failed_certificates_can_be_listed_with_their_reasons(client, payload_factory, flaky_renderer):
    job_id = client.post("/api/v1/jobs", json=payload_factory(["Alice", "Boom"])).json()["id"]
    failed = client.get(f"/api/v1/jobs/{job_id}/certificates", params={"status": "failed"}).json()
    assert failed["total"] == 1
    assert failed["items"][0]["recipient_name"] == "Boom"
    assert failed["items"][0]["error"].startswith("RuntimeError")


def test_unexpected_job_level_error_marks_job_failed(client, db, info, monkeypatch):
    job = service.create_job(db, info, [{"name": "Alice"}])

    def explode(*_args, **_kwargs):
        raise RuntimeError("database went away")

    monkeypatch.setattr(service, "_finalize", explode)
    service.process_job(client.app.state.session_factory, client.app.state.settings.certificates_dir, job.id)

    body = client.get(f"/api/v1/jobs/{job.id}").json()
    assert body["status"] == "failed"
    assert "database went away" in body["error"]
