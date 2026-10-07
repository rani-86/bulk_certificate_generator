"""Creating a job, input validation, and end-to-end generation."""
import pytest

from app.models import JobStatus


def test_create_job_returns_202_with_job_id_and_location(client, payload_factory):
    response = client.post("/api/v1/jobs", json=payload_factory())
    assert response.status_code == 202
    body = response.json()
    assert body["id"]
    assert response.headers["location"] == f"/api/v1/jobs/{body['id']}"
    assert body["progress"]["total"] == 3
    assert body["course_name"] == "Intro to Data Science"
    assert body["links"]["certificates"].endswith("/certificates")


def test_all_certificates_are_generated_for_valid_recipients(client, payload_factory, settings):
    job_id = client.post("/api/v1/jobs", json=payload_factory()).json()["id"]

    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "completed"
    assert job["progress"] == {"total": 3, "pending": 0, "completed": 3, "failed": 0, "percent_done": 100.0}
    assert job["completed_at"] is not None

    items = client.get(f"/api/v1/jobs/{job_id}/certificates").json()["items"]
    assert [i["recipient_name"] for i in items] == ["Asha Verma", "Rahul Singh", "Meera Nair"]
    assert all(i["status"] == "completed" and i["download_url"] for i in items)
    assert len(list((settings.certificates_dir / job_id).glob("*.pdf"))) == 3


def test_issue_date_defaults_to_today_and_can_be_set(client, payload_factory):
    explicit = client.post("/api/v1/jobs", json=payload_factory(issued_on="2025-01-31")).json()
    assert explicit["issued_on"] == "2025-01-31"
    assert client.post("/api/v1/jobs", json=payload_factory()).json()["issued_on"]


# ------------------------------------------------ request-level validation
@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.pop("certificate"),                          # missing certificate info
        lambda p: p.pop("recipients"),                           # missing recipients
        lambda p: p.update(recipients=[]),                       # empty list
        lambda p: p.update(recipients="Asha"),                   # wrong type
        lambda p: p["certificate"].pop("course_name"),           # missing course
        lambda p: p["certificate"].update(course_name="   "),    # blank course
        lambda p: p["certificate"].update(organization="x" * 61),
        lambda p: p["certificate"].update(issued_on="not-a-date"),
    ],
)
def test_invalid_request_is_rejected_with_422(client, payload_factory, mutate):
    payload = payload_factory()
    mutate(payload)
    assert client.post("/api/v1/jobs", json=payload).status_code == 422


def test_too_many_recipients_is_rejected(client, payload_factory):
    names = [f"Person {i}" for i in range(51)]  # test limit is 50
    response = client.post("/api/v1/jobs", json=payload_factory(names))
    assert response.status_code == 422
    assert "Too many recipients" in response.json()["detail"]


def test_certificate_details_that_cannot_be_rendered_fail_fast(client, payload_factory):
    response = client.post("/api/v1/jobs", json=payload_factory(course_name="数据科学"))
    assert response.status_code == 422
    assert "Invalid certificate details" in response.json()["detail"]


# ---------------------------------------------- per-recipient validation
def test_invalid_recipients_are_failed_individually_while_valid_ones_succeed(client, payload_factory):
    payload = payload_factory()
    payload["recipients"] = [
        {"name": "Good Person", "email": "good@example.com", "achievement": "Top of class"},  # 0 ok
        {"name": ""},                                    # 1 blank name
        {"email": "no-name@example.com"},                # 2 missing name
        {"name": "Bad Email", "email": "not-an-email"},  # 3 bad email
        "just a string",                                 # 4 not an object
        {"name": "Typo Field", "emial": "x@y.com"},      # 5 unknown field
        {"name": "राहुल"},                                # 6 unsupported characters
        {"name": "Line\nBreak"},                         # 7 control characters
        {"name": "Second Good"},                         # 8 ok
    ]
    job_id = client.post("/api/v1/jobs", json=payload).json()["id"]

    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "completed_with_errors"
    assert job["progress"]["completed"] == 2
    assert job["progress"]["failed"] == 7

    failed = client.get(f"/api/v1/jobs/{job_id}/certificates", params={"status": "failed"}).json()
    assert failed["total"] == 7
    by_position = {i["position"]: i["error"] for i in failed["items"]}
    assert sorted(by_position) == [1, 2, 3, 4, 5, 6, 7]
    assert "name" in by_position[1]
    assert "name: Field required" in by_position[2]
    assert "email" in by_position[3]
    assert "JSON object" in by_position[4]
    assert "emial" in by_position[5]
    assert "cannot render" in by_position[6]
    assert "control characters" in by_position[7]
    assert all(e.startswith("Invalid recipient") for e in by_position.values())


def test_job_where_every_recipient_is_invalid_is_failed_immediately(client, payload_factory):
    payload = payload_factory()
    payload["recipients"] = [{"name": ""}, {}, 42]
    body = client.post("/api/v1/jobs", json=payload).json()
    assert body["status"] == JobStatus.FAILED.value
    assert body["error"] == "No valid recipients in request"
    assert body["progress"]["failed"] == 3
    assert body["completed_at"] is not None


def test_surrounding_whitespace_is_trimmed(client, payload_factory):
    job_id = client.post("/api/v1/jobs", json=payload_factory(["  Asha Verma  "])).json()["id"]
    item = client.get(f"/api/v1/jobs/{job_id}/certificates").json()["items"][0]
    assert item["recipient_name"] == "Asha Verma"
