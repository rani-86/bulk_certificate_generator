"""PDF rendering for the single predefined certificate template.

`render_certificate` is a pure function (data in, PDF bytes out). It does no
file or database I/O, which keeps it easy to test and easy to swap out.
"""
from dataclasses import dataclass
from datetime import date
from io import BytesIO

from reportlab.lib.colors import Color
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

FONT = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
FONT_ITALIC = "Helvetica-Oblique"

NAVY = Color(0.10, 0.20, 0.40)
GOLD = Color(0.72, 0.58, 0.20)
GREY = Color(0.35, 0.35, 0.35)


class CertificateGenerationError(Exception):
    """Raised when a certificate cannot be rendered from the given data."""


@dataclass(frozen=True)
class CertificateData:
    certificate_id: str
    recipient_name: str
    course_name: str
    organization: str
    issued_on: date
    achievement: str | None = None
    signatory: str | None = None


def is_renderable(text: str) -> bool:
    """The built-in PDF fonts only cover Latin-1/WinAnsi characters."""
    try:
        text.encode("cp1252")
    except UnicodeEncodeError:
        return False
    return True


def _fit_font_size(text: str, font: str, max_size: int, min_size: int, max_width: float, label: str) -> int:
    size = max_size
    while size > min_size and stringWidth(text, font, size) > max_width:
        size -= 1
    if stringWidth(text, font, size) > max_width:
        raise CertificateGenerationError(f"{label} is too long to fit on the certificate")
    return size


def render_certificate(data: CertificateData) -> bytes:
    for label, value in (
        ("recipient name", data.recipient_name),
        ("course name", data.course_name),
        ("organization", data.organization),
        ("achievement", data.achievement),
        ("signatory", data.signatory),
    ):
        if value and not is_renderable(value):
            raise CertificateGenerationError(
                f"The {label} contains characters the certificate font cannot render"
            )

    width, height = landscape(A4)
    cx = width / 2
    max_text_width = width - 160

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(width, height))
    pdf.setTitle(f"Certificate - {data.recipient_name}")
    pdf.setAuthor(data.organization)

    # Double border
    pdf.setStrokeColor(GOLD)
    pdf.setLineWidth(4)
    pdf.rect(20, 20, width - 40, height - 40)
    pdf.setStrokeColor(NAVY)
    pdf.setLineWidth(1)
    pdf.rect(30, 30, width - 60, height - 60)

    # Heading
    pdf.setFillColor(NAVY)
    pdf.setFont(FONT_BOLD, 40)
    pdf.drawCentredString(cx, height - 110, "CERTIFICATE")
    pdf.setFont(FONT, 16)
    pdf.setFillColor(GOLD)
    pdf.drawCentredString(cx, height - 138, "O F   C O M P L E T I O N")

    # Recipient
    pdf.setFillColor(GREY)
    pdf.setFont(FONT_ITALIC, 15)
    pdf.drawCentredString(cx, height - 190, "This is to certify that")

    name_size = _fit_font_size(data.recipient_name, FONT_BOLD, 34, 14, max_text_width, "Recipient name")
    pdf.setFillColor(NAVY)
    pdf.setFont(FONT_BOLD, name_size)
    pdf.drawCentredString(cx, height - 240, data.recipient_name)
    name_width = stringWidth(data.recipient_name, FONT_BOLD, name_size)
    pdf.setStrokeColor(GOLD)
    pdf.setLineWidth(1)
    pdf.line(cx - name_width / 2 - 20, height - 250, cx + name_width / 2 + 20, height - 250)

    # Course
    pdf.setFillColor(GREY)
    pdf.setFont(FONT_ITALIC, 15)
    pdf.drawCentredString(cx, height - 285, "has successfully completed")

    course_size = _fit_font_size(data.course_name, FONT_BOLD, 24, 12, max_text_width, "Course name")
    pdf.setFillColor(NAVY)
    pdf.setFont(FONT_BOLD, course_size)
    pdf.drawCentredString(cx, height - 320, data.course_name)

    if data.achievement:
        size = _fit_font_size(data.achievement, FONT_ITALIC, 16, 10, max_text_width, "Achievement")
        pdf.setFillColor(GOLD)
        pdf.setFont(FONT_ITALIC, size)
        pdf.drawCentredString(cx, height - 348, data.achievement)

    # Footer: date on the left, signatory on the right
    pdf.setFillColor(GREY)
    pdf.setFont(FONT, 12)
    pdf.drawCentredString(160, 100, data.issued_on.strftime("%d %B %Y"))
    pdf.setStrokeColor(GREY)
    pdf.setLineWidth(0.5)
    pdf.line(90, 115, 230, 115)
    pdf.setFont(FONT, 9)
    pdf.drawCentredString(160, 85, "Date of issue")

    if data.signatory:
        sig_x = width - 160
        sig_size = _fit_font_size(data.signatory, FONT, 12, 8, 200, "Signatory")
        pdf.line(sig_x - 80, 115, sig_x + 80, 115)
        pdf.setFont(FONT, sig_size)
        pdf.drawCentredString(sig_x, 100, data.signatory)
        pdf.setFont(FONT, 9)
        pdf.drawCentredString(sig_x, 85, "Authorised signatory")

    org_size = _fit_font_size(data.organization, FONT_BOLD, 14, 8, 330, "Organization name")
    pdf.setFont(FONT_BOLD, org_size)
    pdf.setFillColor(NAVY)
    pdf.drawCentredString(cx, 100, data.organization)
    pdf.setFont(FONT, 8)
    pdf.setFillColor(GREY)
    pdf.drawCentredString(cx, 48, f"Certificate ID: {data.certificate_id}")

    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
