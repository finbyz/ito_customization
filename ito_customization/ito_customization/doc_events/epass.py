import base64
import io
import re

import frappe
from frappe.utils import get_url
from frappe.utils.data import cint

DOCTYPE = "Web Forms Registration"
PRINT_FORMAT = "ITO E-Pass"
STAFF_ROLES = {"ITO Event Staff", "System Manager"}
ATTENDANCE_STATUSES = ("Present", "Absent")
EVENT_NAME = "ITO Excellence Award Felicitation Ceremony 2026\u201327"
VENUE_NAME = "Dr. B. R. Ambedkar Bhavana"
VENUE_ADDRESS = (
	"9c, 2nd A Cross Rd, Sector A, Yelahanka Satellite Town, Yelahanka New Town, "
	"Bengaluru, Karnataka 560064"
)
VENUE_MAP_URL = "https://maps.app.goo.gl/7Ca2HyThYC82fR9L8"


def no_autolink(text):
	"""HTML-escape text and put an invisible zero-width non-joiner after the first digit of each
	number, e.g. "9c" -> "9&zwnj;c". It looks and copies the same, but Gmail, Apple Mail and iOS
	no longer recognise it as a street address and stop turning it into a blue maps link."""
	from markupsafe import Markup, escape

	return Markup(re.sub(r"(\d)(?=\w)", r"\1&zwnj;", str(escape(text))))

EMAIL_HTML = """
<div style="font-family:Arial,Helvetica,sans-serif;max-width:560px;margin:auto;color:#202124">
  <div style="background:#2f7f73;color:#fff;padding:18px 22px;border-radius:8px 8px 0 0">
    <div style="font-size:12px;letter-spacing:2px;text-transform:uppercase;opacity:.85">Indian Talent Olympiad</div>
    <div style="font-size:20px;font-weight:bold;margin-top:4px">{{ event }}</div>
  </div>
  <div style="border:1px solid #dadce0;border-top:0;padding:22px;border-radius:0 0 8px 8px">
    <p>Dear {{ name }},</p>
    <p>Thank you for registering. Your <b>E-Pass</b> is confirmed. Please show the QR code below
       (or the attached E-Pass PDF) at the venue entrance.</p>
    <p style="text-align:center;margin:22px 0">
      <img src="{{ qr_url }}" width="200" height="200" alt="E-Pass QR code"
           style="border:1px solid #dadce0;padding:8px;border-radius:8px">
      <br><span style="font-size:12px;color:#5f6368">If the image is hidden, open the attached QR / PDF.</span>
    </p>
    <table style="font-size:14px;line-height:1.7">
      <tr><td style="color:#5f6368;padding-right:14px;vertical-align:top">Date</td><td><b>23rd November 2026</b></td></tr>
      <tr><td style="color:#5f6368;padding-right:14px;vertical-align:top">Time</td><td><b>10:00 AM to 1:00 PM</b></td></tr>
      <tr><td style="color:#5f6368;padding-right:14px;vertical-align:top">Venue</td><td><b>{{ venue_name }}</b><br>
        <a href="{{ venue_map_url }}" style="color:#202124 !important;text-decoration:none !important;cursor:text"><span style="color:#202124 !important;text-decoration:none !important">{{ venue_address }}</span></a><br>
        <a href="{{ venue_map_url }}">Open in Google Maps</a></td></tr>
    </table>
    <p style="font-size:13px;color:#5f6368;margin-top:18px">
      Entry is strictly for the invited individual. Accompanying persons are not permitted.
      Entry is subject to verification at the venue.</p>
    <p style="font-size:13px"><a href="{{ page_url }}">View your E-Pass online</a></p>
  </div>
</div>
"""


def epass_url(token):
	return f"{get_url()}/epass?t={token}"


def qr_png_bytes(data, box_size=8, border=2):
	try:
		import qrcode

		qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=box_size, border=border)
		qr.add_data(data)
		qr.make(fit=True)
		img = qr.make_image(fill_color="black", back_color="white")
		buf = io.BytesIO()
		img.save(buf, format="PNG")
		return buf.getvalue()
	except ImportError:
		import pyqrcode

		buf = io.BytesIO()
		pyqrcode.create(data, error="M").png(buf, scale=box_size, quiet_zone=border)
		return buf.getvalue()


def qr_data_uri(token):
	"""Jinja helper for the print format: QR that points to the public E-Pass page."""
	if not token:
		return ""
	return "data:image/png;base64," + base64.b64encode(qr_png_bytes(epass_url(token))).decode()


def photo_data_uri(file_url, max_px=500):
	"""Read the (private) uploaded photo and return a small inline JPEG data URI."""
	if not file_url:
		return ""
	try:
		from PIL import Image, ImageOps

		name = frappe.db.get_value("File", {"file_url": file_url}, "name")
		if not name:
			return ""
		content = frappe.get_doc("File", name).get_content()
		if isinstance(content, str):
			content = content.encode()
		img = ImageOps.exif_transpose(Image.open(io.BytesIO(content))).convert("RGB")
		img.thumbnail((max_px, max_px))
		buf = io.BytesIO()
		img.save(buf, "JPEG", quality=85)
		return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
	except Exception:
		frappe.log_error(title="E-Pass photo could not be read")
		return ""


def is_approved(doc):
	return str(doc.status or "").strip().lower() == "approve"


def queue_epass(doc):
	if (
		not is_approved(doc)
		or not doc.email
		or not doc.epass_token
		or cint(doc.epass_email_sent)
		or cint(doc.mail_sent)
	):
		return
	frappe.enqueue(
		"ito_customization.ito_customization.doc_events.epass.send_epass_email",
		queue="short",
		docname=doc.name,
		enqueue_after_commit=True,
	)


def send_epass_email(docname, force=False):
	doc = frappe.get_doc(DOCTYPE, docname)
	if not is_approved(doc) or not doc.email or not doc.epass_token:
		return
	if not force and (cint(doc.epass_email_sent) or cint(doc.mail_sent)):
		return

	page_url = epass_url(doc.epass_token)
	attachments = [{"fname": f"E-Pass-QR-{doc.name}.png", "fcontent": qr_png_bytes(page_url)}]
	try:
		attachments.insert(
			0,
			frappe.attach_print(DOCTYPE, doc.name, print_format=PRINT_FORMAT, file_name=f"E-Pass-{doc.name}"),
		)
	except Exception:
		frappe.log_error(title="E-Pass PDF could not be generated")

	message = frappe.render_template(
		EMAIL_HTML,
		{
			"event": EVENT_NAME,
			"name": doc.full_name,
			"page_url": page_url,
			# Plain text: the address sits inside our own <a> (see EMAIL_HTML), so mail apps
			# don't add their own maps link, and copying it gives clean text.
			"venue_name": VENUE_NAME,
			"venue_address": VENUE_ADDRESS,
			"venue_map_url": VENUE_MAP_URL,
			"qr_url": f"{get_url()}/api/method/ito_customization.ito_customization.doc_events.epass.qr_image?t={doc.epass_token}",
		},
	)
	frappe.sendmail(
		recipients=[doc.email],
		subject=f"Your E-Pass \u2013 {EVENT_NAME}",
		message=message,
		attachments=attachments,
		reference_doctype=DOCTYPE,
		reference_name=doc.name,
	)
	frappe.db.set_value(
		DOCTYPE,
		doc.name,
		{"epass_email_sent": 1, "mail_sent": 1},
		update_modified=False,
	)


def _resend_block_reason(doc):
	"""Why the E-Pass email cannot be resent for this registration, or None if it can."""
	if not doc.has_permission("write"):
		return "Not permitted"
	if not is_approved(doc):
		return "Registration is not approved"
	if not doc.email:
		return "No email address"
	return None


def _queue_resend(doc):
	if not doc.epass_token:
		frappe.db.set_value(DOCTYPE, doc.name, "epass_token", frappe.generate_hash(length=24), update_modified=False)
	frappe.enqueue(
		"ito_customization.ito_customization.doc_events.epass.send_epass_email",
		queue="short",
		docname=doc.name,
		force=True,
		enqueue_after_commit=True,
	)


@frappe.whitelist()
def resend_epass_email(docname):
	"""Re-queue the E-Pass email for an approved registration (button on the form)."""
	doc = frappe.get_doc(DOCTYPE, docname)
	doc.check_permission("write")
	reason = _resend_block_reason(doc)
	if reason:
		frappe.throw(f"E-Pass email cannot be resent: {reason}.")
	_queue_resend(doc)
	return True


@frappe.whitelist()
def bulk_resend_epass_email(names):
	"""Re-queue the E-Pass email for each selected registration (list view).
	Records that fail the same checks as the form button are skipped and reported back."""
	if isinstance(names, str):
		names = frappe.parse_json(names)

	queued, skipped = [], []
	for name in names or []:
		if not frappe.db.exists(DOCTYPE, name):
			skipped.append({"name": name, "reason": "Not found"})
			continue
		doc = frappe.get_doc(DOCTYPE, name)
		reason = _resend_block_reason(doc)
		if reason:
			skipped.append({"name": name, "full_name": doc.full_name, "email": doc.email, "status": doc.status, "reason": reason})
			continue
		_queue_resend(doc)
		queued.append(name)

	return {"queued": queued, "skipped": skipped}


@frappe.whitelist(allow_guest=True)
def qr_image(t=None):
	"""Serves the QR PNG for the e-mail body. Only works with the unguessable token."""
	name = frappe.db.get_value(DOCTYPE, {"epass_token": (t or "").strip()}, "name") if t else None
	if not name:
		frappe.throw("Not found", frappe.DoesNotExistError)
	token = frappe.db.get_value(DOCTYPE, name, "epass_token")
	frappe.response["filename"] = "e-pass-qr.png"
	frappe.response["filecontent"] = qr_png_bytes(epass_url(token))
	frappe.response["type"] = "download"
	frappe.response["display_content_as"] = "inline"


def backfill_tokens_and_queue(send_emails=False):
	"""One-off, from bench console: give existing registrations a token (and optionally e-mail them)."""
	for name in frappe.get_all(DOCTYPE, pluck="name"):
		doc = frappe.get_doc(DOCTYPE, name)
		if not doc.epass_token:
			frappe.db.set_value(DOCTYPE, name, "epass_token", frappe.generate_hash(length=24), update_modified=False)
		if send_emails and not doc.epass_email_sent:
			queue_epass(frappe.get_doc(DOCTYPE, name))


def name_by_token(token):
	token = (token or "").strip()
	return frappe.db.get_value(DOCTYPE, {"epass_token": token}, "name") if token else None


def is_staff(user=None):
	user = user or frappe.session.user
	if user == "Guest":
		return False
	return bool(STAFF_ROLES & set(frappe.get_roles(user)))


def attendance_info(doc):
	by = doc.attendance_marked_by
	return {
		"status": doc.event_attendance or "Not Marked",
		"marked_on": doc.attendance_marked_on,
		"marked_on_display": frappe.utils.format_datetime(doc.attendance_marked_on, "dd MMM, hh:mm a")
		if doc.attendance_marked_on
		else "",
		"marked_by": frappe.db.get_value("User", by, "full_name") if by else None,
	}


def _declined(doc):
	return str(doc.attendance_confirmation or "").strip().lower().startswith("no")


@frappe.whitelist()
def mark_attendance(t, status):
	"""Staff-only: record Present/Absent for the E-Pass identified by token `t`."""
	if frappe.session.user == "Guest":
		frappe.throw("Please log in to mark attendance.", frappe.PermissionError)
	if not is_staff():
		frappe.throw("You are not allowed to mark attendance.", frappe.PermissionError)
	if status not in ATTENDANCE_STATUSES:
		frappe.throw("Invalid attendance status.")

	name = name_by_token(t)
	if not name:
		frappe.throw("This E-Pass is invalid or could not be found.", frappe.DoesNotExistError)

	# Row lock: a concurrent scan of the same pass waits here, then sees the committed status.
	frappe.db.get_value(DOCTYPE, name, "name", for_update=True)
	doc = frappe.get_doc(DOCTYPE, name)

	if not is_approved(doc):
		frappe.throw("This registration has not been approved for an E-Pass.")
	if _declined(doc):
		frappe.throw("This person responded No to the RSVP and cannot be marked.")

	current = doc.event_attendance or "Not Marked"
	if current in ATTENDANCE_STATUSES:
		return {
			"changed": False,
			"message": f"Already marked {current}, no change made.",
			**attendance_info(doc),
		}

	doc.event_attendance = status
	doc.attendance_marked_on = frappe.utils.now_datetime()
	doc.attendance_marked_by = frappe.session.user
	doc.flags.ignore_permissions = True
	doc.flags.ignore_validate = True
	doc.save()
	return {"changed": True, "message": f"Marked {status}.", **attendance_info(doc)}


def mark_remaining_absent():
	"""After the event: approved, RSVP-confirmed registrations never scanned -> Absent. Returns count."""
	names = frappe.get_all(
		DOCTYPE,
		filters={
			"status": "Approve",
			"attendance_confirmation": ["like", "Yes%"],
			"event_attendance": ["in", ["", "Not Marked"]],
		},
		pluck="name",
	)
	now = frappe.utils.now_datetime()
	for name in names:
		frappe.db.set_value(
			DOCTYPE,
			name,
			{"event_attendance": "Absent", "attendance_marked_on": now},
			update_modified=False,
		)
	return len(names)