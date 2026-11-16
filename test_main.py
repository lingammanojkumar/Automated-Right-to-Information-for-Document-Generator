import base64
import os
import unittest
from unittest.mock import MagicMock, patch

import main


COMPLAINT = {
    "complaintId": "GR-20261115-ABCD",
    "userEmail": "citizen@example.com",
    "userName": "Citizen Name",
    "userPhone": "9876543210",
    "userAddress": "Ward 2, Town",
    "title": "Water issue",
    "location": "Ward 2",
    "assignedTo": "Water Supply Department",
    "description": "No water supply.",
    "officialEmail": main.DEPARTMENT_EMAIL,
    "officialPhone": "1234567890",
}


class SendComplaintTest(unittest.TestCase):
    def setUp(self):
        self.client = main.app.test_client()

    def _request(
        self,
        complaint=None,
        pdf=b"%PDF-test",
        photo=None,
        origin="http://localhost:62045",
    ):
        data = {
            "complaint": complaint or COMPLAINT,
            "pdfBase64": base64.b64encode(pdf).decode("ascii"),
        }
        if photo is not None:
            data["photo"] = photo
        return self.client.post(
            "/sendComplaintEmail",
            headers={"Origin": origin},
            json=data,
        )

    def test_sends_complaint_without_firebase_auth_or_firestore(self):
        with patch.object(main, "send_complaint_email") as send_email:
            response = self._request()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {
                "success": True,
                "complaintId": COMPLAINT["complaintId"],
                "recipient": main.DEPARTMENT_EMAIL,
            },
        )
        send_email.assert_called_once_with(COMPLAINT, b"%PDF-test", None)

    def test_rejects_non_local_origin(self):
        with patch.object(main, "send_complaint_email") as send_email:
            response = self._request(origin="https://attacker.example")

        self.assertEqual(response.status_code, 403)
        send_email.assert_not_called()

    def test_allows_firebase_hosting_preflight(self):
        origin = "https://civicform-12e28.web.app"
        response = self.client.options(
            "/sendComplaintEmail",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], origin)
        self.assertIn("POST", response.headers["Access-Control-Allow-Methods"])
        self.assertEqual(response.headers["Access-Control-Allow-Headers"], "Content-Type")

    def test_allows_configured_custom_hosting_origin(self):
        origin = "https://civicform.example.com"
        with patch.dict(os.environ, {"CORS_ALLOWED_ORIGINS": origin}):
            response = self.client.options(
                "/sendComplaintEmail",
                headers={"Origin": origin},
            )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], origin)

    def test_rejects_invalid_pdf_before_sending(self):
        with patch.object(main, "send_complaint_email") as send_email:
            response = self.client.post(
                "/sendComplaintEmail",
                headers={"Origin": "http://localhost:62045"},
                json={"complaint": COMPLAINT, "pdfBase64": "not a pdf"},
            )

        self.assertEqual(response.status_code, 400)
        send_email.assert_not_called()

    def test_rejects_unapproved_department_address(self):
        complaint = dict(COMPLAINT, officialEmail="someone@example.com")
        with patch.object(main, "send_complaint_email") as send_email:
            response = self._request(complaint=complaint)

        self.assertEqual(response.status_code, 400)
        send_email.assert_not_called()

    def test_sends_formatted_message_to_approved_department_with_pdf(self):
        smtp = MagicMock()
        smtp_context = MagicMock()
        smtp_context.__enter__.return_value = smtp

        with patch.dict(os.environ, {"SMTP_APP_PASSWORD": "test-app-password"}):
            with patch.object(main.smtplib, "SMTP_SSL", return_value=smtp_context):
                main.send_complaint_email(COMPLAINT, b"%PDF-test")

        message = smtp.send_message.call_args.args[0]
        self.assertEqual(message["From"], f"CivicForm Portal <{main.SENDER_EMAIL}>")
        self.assertEqual(message["To"], main.DEPARTMENT_EMAIL)
        self.assertEqual(message["Reply-To"], COMPLAINT["userEmail"])
        self.assertIn(COMPLAINT["complaintId"], message["Subject"])
        self.assertIn("Complaint Details:", message.get_body().get_content())
        self.assertIn("Citizen Name", message.get_body().get_content())
        self.assertIn("9876543210", message.get_body().get_content())
        attachment = list(message.iter_attachments())[0]
        self.assertEqual(attachment.get_content_type(), "application/pdf")
        self.assertEqual(attachment.get_payload(decode=True), b"%PDF-test")

    def test_sends_evidence_photo_as_email_attachment(self):
        photo_bytes = b"\xff\xd8\xffproof"
        photo = {
            "base64": base64.b64encode(photo_bytes).decode("ascii"),
            "contentType": "image/jpeg",
            "name": "issue.jpg",
        }
        smtp = MagicMock()
        smtp_context = MagicMock()
        smtp_context.__enter__.return_value = smtp

        with patch.dict(os.environ, {"SMTP_APP_PASSWORD": "test-app-password"}):
            with patch.object(main.smtplib, "SMTP_SSL", return_value=smtp_context):
                response = self._request(photo=photo)

        self.assertEqual(response.status_code, 200)
        message = smtp.send_message.call_args.args[0]
        self.assertIn("Photo evidence is attached.", message.get_body().get_content())
        attachments = list(message.iter_attachments())
        self.assertEqual(len(attachments), 2)
        self.assertEqual(attachments[1].get_content_type(), "image/jpeg")
        self.assertEqual(
            attachments[1].get_payload(decode=True),
            photo_bytes,
        )

    def test_rejects_mismatched_evidence_photo_type(self):
        photo = {
            "base64": base64.b64encode(b"not an image").decode("ascii"),
            "contentType": "image/jpeg",
            "name": "issue.jpg",
        }
        with patch.object(main, "send_complaint_email") as send_email:
            response = self._request(photo=photo)

        self.assertEqual(response.status_code, 400)
        send_email.assert_not_called()

    def test_sends_legacy_complaint_when_profile_contact_fields_are_missing(self):
        complaint = dict(COMPLAINT)
        complaint.pop("userName")
        complaint.pop("userPhone")
        complaint.pop("userAddress")
        smtp = MagicMock()
        smtp_context = MagicMock()
        smtp_context.__enter__.return_value = smtp

        with patch.dict(os.environ, {"SMTP_APP_PASSWORD": "test-app-password"}):
            with patch.object(main.smtplib, "SMTP_SSL", return_value=smtp_context):
                main.send_complaint_email(complaint, b"%PDF-test")

        body = smtp.send_message.call_args.args[0].get_body().get_content()
        self.assertIn("Name: Not provided", body)
        self.assertIn("Phone: Not provided", body)
        self.assertIn("Address: Not provided", body)


if __name__ == "__main__":
    unittest.main()
