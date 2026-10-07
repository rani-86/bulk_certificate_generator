"""Certificate generation (the PDF renderer on its own)."""
import io
from datetime import date

import pytest
from pypdf import PdfReader

from app.certificate import CertificateData, CertificateGenerationError, render_certificate


def data(**overrides):
    base = dict(
        certificate_id="abc123",
        recipient_name="Asha Verma",
        course_name="Intro to Data Science",
        organization="Acme Academy",
        issued_on=date(2026, 10, 6),
    )
    base.update(overrides)
    return CertificateData(**base)


def pdf_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    assert len(reader.pages) == 1
    return reader.pages[0].extract_text()


def test_generates_valid_pdf_with_recipient_specific_content():
    pdf = render_certificate(data())
    assert pdf.startswith(b"%PDF")
    text = pdf_text(pdf)
    for expected in ("Asha Verma", "Intro to Data Science", "Acme Academy", "06 October 2026", "abc123"):
        assert expected in text


def test_optional_fields_are_rendered_when_given():
    text = pdf_text(render_certificate(data(achievement="With Distinction", signatory="Dr. A. Kumar")))
    assert "With Distinction" in text
    assert "Dr. A. Kumar" in text


def test_different_recipients_produce_different_certificates():
    assert pdf_text(render_certificate(data(recipient_name="Rahul Singh"))) != pdf_text(render_certificate(data()))


def test_latin_accents_are_supported():
    assert "José Müller" in pdf_text(render_certificate(data(recipient_name="José Müller")))


def test_unsupported_characters_raise():
    with pytest.raises(CertificateGenerationError, match="cannot render"):
        render_certificate(data(recipient_name="राहुल"))


def test_name_too_wide_for_the_page_raises():
    with pytest.raises(CertificateGenerationError, match="too long"):
        render_certificate(data(recipient_name="W" * 80))
