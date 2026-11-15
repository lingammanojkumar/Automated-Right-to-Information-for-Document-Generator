import base64
import binascii
import logging
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import urlparse

from flask import Flask, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SENDER_EMAIL = "mk4829779@gmail.com"
DEPARTMENT_EMAIL = "lingammanojkumar178@gmail.com"
MAX_PDF_BYTES = 1024 * 1024
MAX_PHOTO_BYTES = 640 * 1024
MAX_PHOTO_BASE64_CHARS = ((MAX_PHOTO_BYTES + 2) // 3) * 4
MAX_REQUEST_BYTES = 3 * 1024 * 1024
PHOTO_TYPES = {
    "image/gif": ("gif", (b"GIF87a", b"GIF89a")),
    "image/jpeg": ("jpg", (b"\xff\xd8\xff",)),
    "image/png": ("png", (b"\x89PNG\r\n\x1a\n",)),
    "image/webp": ("webp", (b"RIFF",)),
}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES


class RequestError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def is_local_origin(origin):
    if not origin:
        return True
    try:
        parsed = urlparse(origin)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and parsed.hostname in {
        "localhost",
        "127.0.0.1",
    }


@app.before_request
def reject_non_local_email_origins():
    if request.path == "/sendComplaintEmail" and not is_local_origin(
        request.headers.get("Origin")
    ):
        return jsonify(error="The local email API only accepts requests from localhost."), 403


@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin")
    if origin and is_local_origin(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    return response


def read_text(data, field, max_length):
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RequestError(f"Missing {field}.")
    value = value.strip()
    if len(value) > max_length:
        raise RequestError(f"{field} is too long.")
    return value


def read_optional_text(data, field, max_length):
    value = data.get(field, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise RequestError(f"{field} must be text.")
    value = value.strip()
    if len(value) > max_length:
        raise RequestError(f"{field} is too long.")
    return value


def decode_pdf(value):
    if len(value) > 1_400_000:
        raise RequestError("The complaint letter PDF is invalid or too large.")
    try:
        pdf_bytes = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise RequestError("The complaint letter PDF is invalid.") from error
    if len(pdf_bytes) > MAX_PDF_BYTES or not pdf_bytes.startswith(b"%PDF-"):
        raise RequestError("The complaint letter PDF is invalid or too large.")
    return pdf_bytes


def decode_photo(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise RequestError("The evidence photo is invalid.")

    encoded = read_text(value, "base64", MAX_PHOTO_BASE64_CHARS)
    content_type = read_text(value, "contentType", 40).lower()
    photo_type = PHOTO_TYPES.get(content_type)
    if photo_type is None:
        raise RequestError("The evidence photo type is not supported.")

    try:
        photo_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise RequestError("The evidence photo is invalid.") from error
    if len(photo_bytes) > MAX_PHOTO_BYTES:
        raise RequestError("The evidence photo is too large.")

    extension, signatures = photo_type
    if content_type == "image/webp":
        valid_signature = (
            len(photo_bytes) >= 12
            and photo_bytes.startswith(b"RIFF")
            and photo_bytes[8:12] == b"WEBP"
        )
    else:
        valid_signature = any(photo_bytes.startswith(signature) for signature in signatures)
    if not valid_signature:
        raise RequestError("The evidence photo content does not match its type.")

    return photo_bytes, content_type, extension


def send_complaint_email(grievance, pdf_bytes, photo=None):
    app_password = os.environ.get("SMTP_APP_PASSWORD", "").strip().replace(" ", "")
    if not app_password:
        raise RuntimeError("SMTP_APP_PASSWORD is not configured.")

    complaint_id = read_text(grievance, "complaintId", 80)
    user_email = read_text(grievance, "userEmail", 254)
    user_name = read_optional_text(grievance, "userName", 200)
    user_phone = read_optional_text(grievance, "userPhone", 40)
    user_address = read_optional_text(grievance, "userAddress", 500)
    title = read_text(grievance, "title", 200)
    location = read_text(grievance, "location", 500)
    assigned_to = read_text(grievance, "assignedTo", 200)
    description = read_text(grievance, "description", 8000)
    official_email = read_text(grievance, "officialEmail", 254)
    official_phone = read_text(grievance, "officialPhone", 80)

    if not re.fullmatch(r"[A-Za-z0-9-]+", complaint_id):
        raise RequestError("The complaint ID is invalid.")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", user_email):
        raise RequestError("The complainant email address is invalid.")
    if official_email.lower() != DEPARTMENT_EMAIL.lower():
        raise RequestError("The department email is not an approved recipient.")

    message = EmailMessage()
    message["From"] = f"CivicForm Portal <{SENDER_EMAIL}>"
    message["To"] = official_email
    message["Reply-To"] = user_email
    message["Subject"] = f"[CivicForm Portal] New Complaint - {complaint_id}"
    message.set_content(
        "\n".join(
            [
                f"To: {assigned_to}",
                f"Email: {official_email}",
                f"Phone: {official_phone}",
                "",
                f"Subject: {title}",
                "",
                "Respected Sir/Madam,",
                "",
                f"I am writing to bring to your attention the following grievance related to {assigned_to}.",
                f"Complaint ID: {complaint_id}",
                f"Name: {user_name or 'Not provided'}",
                f"Filed By: {user_email}",
                f"Phone: {user_phone or 'Not provided'}",
                f"Address: {user_address or 'Not provided'}",
                f"Location: {location}",
                "",
                "Complaint Details:",
                description,
                "",
                (
                    "Photo evidence is attached."
                    if photo is not None
                    else "No photo evidence was attached."
                ),
                "",
                "I kindly request you to take immediate action and resolve this issue at the earliest.",
                "Please find the formal complaint letter attached as a PDF.",
                "",
                "Yours faithfully,",
                user_email,
            ]
        )
    )
    message.add_attachment(
        pdf_bytes,
        maintype="application",
        subtype="pdf",
        filename=f"{complaint_id}.pdf",
    )
    if photo is not None:
        photo_bytes, content_type, extension = photo
        maintype, subtype = content_type.split("/", 1)
        message.add_attachment(
            photo_bytes,
            maintype=maintype,
            subtype=subtype,
            filename=f"{complaint_id}-evidence.{extension}",
        )

    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465,
        context=ssl.create_default_context(),
        timeout=30,
    ) as smtp:
        smtp.login(SENDER_EMAIL, app_password)
        smtp.send_message(message)


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.errorhandler(RequestEntityTooLarge)
def request_too_large(_error):
    return jsonify(error="The request is too large."), 413


@app.route("/sendComplaintEmail", methods=["POST", "OPTIONS"])
def send_complaint():
    if request.method == "OPTIONS":
        return "", 204

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="A JSON request body is required."), 400

    try:
        grievance = data.get("complaint")
        if not isinstance(grievance, dict):
            raise RequestError("A complaint object is required.")
        complaint_id = read_text(grievance, "complaintId", 80)
        official_email = read_text(grievance, "officialEmail", 254)
        if not re.fullmatch(r"[A-Za-z0-9-]+", complaint_id):
            raise RequestError("The complaint ID is invalid.")
        if official_email.lower() != DEPARTMENT_EMAIL.lower():
            raise RequestError("The department email is not an approved recipient.")
        pdf_bytes = decode_pdf(read_text(data, "pdfBase64", 1_400_000))
        photo = decode_photo(data.get("photo"))
    except RequestError as error:
        return jsonify(error=str(error)), error.status_code

    try:
        send_complaint_email(grievance, pdf_bytes, photo)
    except RequestError as error:
        return jsonify(error=str(error)), error.status_code
    except RuntimeError as error:
        logger.exception("Email configuration is incomplete.")
        return jsonify(error=str(error)), 503
    except (smtplib.SMTPException, OSError):
        logger.exception("Complaint email delivery failed.")
        return jsonify(
            error="Gmail could not send the message. Check the sender App Password."
        ), 502

    logger.info(
        "Gmail accepted complaint %s for %s.",
        complaint_id,
        official_email,
    )

    return jsonify(
        success=True,
        complaintId=complaint_id,
        recipient=official_email,
    ), 200


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "8080")))
