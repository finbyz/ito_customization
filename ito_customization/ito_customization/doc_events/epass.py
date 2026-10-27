import base64
import io

import frappe
from frappe.utils import get_url
from frappe.utils.data import cint

DOCTYPE = "Web Forms Registration"
PRINT_FORMAT = "ITO E-Pass"
EVENT_NAME = "ITO Excellence Award Felicitation Ceremony 2026\u201327"

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
      <tr><td style="color:#5f6368;padding-right:14px">Date</td><td><b>23rd November 2026</b></td></tr>
      <tr><td style="color:#5f6368;padding-right:14px">Time</td><td><b>10:00 AM to 1:00 PM</b></td></tr>
      <tr><td style="color:#5f6368;padding-right:14px">Venue</td><td><b>Bangalore</b> (detailed address will be shared shortly)</td></tr>
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


def send_epass_email(docname):
	doc = frappe.get_doc(DOCTYPE, docname)
	if (
		not is_approved(doc)
		or not doc.email
		or not doc.epass_token
		or cint(doc.epass_email_sent)
		or cint(doc.mail_sent)
	):
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
	# frappe.db.commit()