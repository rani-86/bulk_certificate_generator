"""Retrieving generated certificates."""
import io
import zipfile

from pypdf import PdfReader

from app import service


def create_job(client, payload_factory, names):
    return client.post("/api/v1/jobs", json=payload_factory(names)).json()["id"]


def test_download_single_certificate_as_pdf(client, payload_factory):
    job_id = create_job(client, payload_factory, ["Asha Verma"])
    item = client.get(f"/api/v1/jobs/{job_id}/certificates").json()["items"][0]

    response = client.get(item["download_url"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    disposition = response.headers["content-disposition"]
    assert "attachment" in disposition and "asha-verma-" in disposition and ".pdf" in disposition
    assert response.content.startswith(b"%PDF")
    assert "Asha Verma" in PdfReader(io.BytesIO(response.content)).pages[0].extract_text()


def test_each_recipient_gets_their_own_certificate(client, payload_factory):
    job_id = create_job(client, payload_factory, ["Asha Verma", "Rahul Singh"])
    items = client.get(f"/api/v1/jobs/{job_id}/certificates").json()["items"]
    texts = [PdfReader(io.BytesIO(client.get(i["download_url"]).content)).pages[0].extract_text() for i in items]
    assert "Asha Verma" in texts[0] and "Rahul Singh" not in texts[0]
    assert "Rahul Singh" in texts[1] and "Asha Verma" not in texts[1]


def test_certificate_metadata_endpoint(client, payload_factory):
    job_id = create_job(client, payload_factory, ["Asha Verma"])
    item = client.get(f"/api/v1/jobs/{job_id}/certificates").json()["items"][0]
    body = client.get(f"/api/v1/certificates/{item['id']}").json()
    assert body["status"] == "completed" and body["recipient_name"] == "Asha Verma"


def test_cannot_download_failed_or_pending_or_unknown_certificates(client, db, info, payload_factory):
    payload = payload_factory(["Ok"])
    payload["recipients"].append({"name": ""})
    job_id = client.post("/api/v1/jobs", json=payload).json()["id"]
    failed = client.get(f"/api/v1/jobs/{job_id}/certificates", params={"status": "failed"}).json()["items"][0]
    response = client.get(f"/api/v1/certificates/{failed['id']}/download")
    assert response.status_code == 409
    assert "failed" in response.json()["detail"]

    pending_job = service.create_job(db, info, [{"name": "Later"}])
    pending_cert = pending_job.certificates[0]
    assert client.get(f"/api/v1/certificates/{pending_cert.id}/download").status_code == 409

    assert client.get("/api/v1/certificates/nope/download").status_code == 404
    assert client.get("/api/v1/certificates/nope").status_code == 404


def test_download_all_certificates_of_a_job_as_zip(client, payload_factory):
    payload = payload_factory(["Asha Verma", "Rahul Singh"])
    payload["recipients"].append({"name": ""})  # one failure that must not be in the ZIP
    job_id = client.post("/api/v1/jobs", json=payload).json()["id"]

    response = client.get(f"/api/v1/jobs/{job_id}/download")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert len(names) == 2
        assert names[0].startswith("asha-verma-") and names[1].startswith("rahul-singh-")
        assert all(archive.read(n).startswith(b"%PDF") for n in names)


def test_zip_is_not_available_until_job_finishes_or_when_nothing_was_generated(client, db, info, payload_factory):
    pending = service.create_job(db, info, [{"name": "Later"}])
    assert client.get(f"/api/v1/jobs/{pending.id}/download").status_code == 409

    payload = payload_factory()
    payload["recipients"] = [{"name": ""}]
    failed_job = client.post("/api/v1/jobs", json=payload).json()["id"]
    assert client.get(f"/api/v1/jobs/{failed_job}/download").status_code == 409


def test_certificate_list_pagination_and_status_filter(client, payload_factory):
    names = [f"Person {i}" for i in range(7)]
    payload = payload_factory(names)
    payload["recipients"][2] = {"name": ""}
    job_id = client.post("/api/v1/jobs", json=payload).json()["id"]

    page = client.get(f"/api/v1/jobs/{job_id}/certificates", params={"limit": 3, "offset": 3}).json()
    assert page["total"] == 7 and len(page["items"]) == 3
    assert [i["position"] for i in page["items"]] == [3, 4, 5]

    completed = client.get(f"/api/v1/jobs/{job_id}/certificates", params={"status": "completed"}).json()
    assert completed["total"] == 6
    assert client.get(f"/api/v1/jobs/{job_id}/certificates", params={"limit": 0}).status_code == 422
