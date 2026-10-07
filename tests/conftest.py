import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.schemas import CertificateInfo


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        certificates_dir=tmp_path / "certs",
        max_recipients_per_job=50,
        resume_unfinished_jobs=False,
    )


@pytest.fixture
def client(settings):
    """Note: TestClient runs FastAPI background tasks before returning the response
    to the test, so after `client.post(...)` the job has already been processed."""
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def db(client):
    with client.app.state.session_factory() as session:
        yield session


@pytest.fixture
def info():
    return CertificateInfo(course_name="Intro to Data Science", organization="Acme Academy")


def make_payload(names=("Asha Verma", "Rahul Singh", "Meera Nair"), **certificate_overrides):
    certificate = {"course_name": "Intro to Data Science", "organization": "Acme Academy"}
    certificate.update(certificate_overrides)
    return {"certificate": certificate, "recipients": [{"name": n} for n in names]}


@pytest.fixture
def payload_factory():
    return make_payload
