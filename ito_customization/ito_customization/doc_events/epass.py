import base64
import io
import re

import frappe
from frappe.utils import get_url

from ito_customization.ito_customization.doc_events.event_outputs import is_approved, queue_outputs_for_trigger

DOCTYPE = "Web Forms Registration"
STAFF_ROLES = {"ITO Event Staff", "System Manager"}
ATTENDANCE_STATUSES = ("Present", "Absent")


def no_autolink(text):
	"""HTML-escape text and put an invisible zero-width non-joiner after the first digit of each
	number, e.g. "9c" -> "9&zwnj;c". It looks and copies the same, but Gmail, Apple Mail and iOS
	no longer recognise it as a street address and stop turning it into a blue maps link."""
	from markupsafe import Markup, escape

	return Markup(re.sub(r"(\d)(?=\w)", r"\1&zwnj;", str(escape(text))))


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


# The E-Pass is now an output of the generic engine (event_outputs.py), set up on the ITO Event.
# These names are kept so queued jobs and old buttons keep working.


def queue_epass(doc):
	from ito_customization.ito_customization.doc_events.event_outputs import queue_outputs_for_trigger

	queue_outputs_for_trigger(doc, "On Approval")


def send_epass_email(docname, force=False):
	from ito_customization.ito_customization.doc_events.event_outputs import LEGACY_OUTPUT, send_output

	send_output(docname, LEGACY_OUTPUT, force=force)


@frappe.whitelist()
def resend_epass_email(docname):
	from ito_customization.ito_customization.doc_events.event_outputs import LEGACY_OUTPUT, send_now

	return send_now(docname, LEGACY_OUTPUT)


@frappe.whitelist()
def bulk_resend_epass_email(names):
	from ito_customization.ito_customization.doc_events.event_outputs import LEGACY_OUTPUT, bulk_send

	return bulk_send(names, LEGACY_OUTPUT)


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
		if send_emails:
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
	if status == "Present":
		queue_outputs_for_trigger(doc, "On Marked Present")
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