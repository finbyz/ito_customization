"""Generic engine for event outputs (E-Pass, E-Card, Attendance Certificate, ...).

Everything a client may want to change lives in the desk:
  - ITO Event:        event name, date, time, venue, branding, extra details, and the Outputs table
  - Email Template:   subject and body of each output's email (Jinja)
  - Print Format:     the PDF of each output (Jinja)

Templates get these variables:
  doc        the registration (doc.full_name, doc.school_name, ...)
  event      the event (event.event_name, event.date_long, event.venue_name, event.details.<key>, ...)
  page_url   link to the online E-Pass page for this registration
  qr_url     public link to the QR image (for emails)
  output     name of the output being sent, e.g. "E-Pass"
"""

import re
import unicodedata

import frappe
from frappe.utils import cint, get_url, getdate, now_datetime

DOCTYPE = "Web Forms Registration"
EVENT_DOCTYPE = "ITO Event"
TRIGGERS = ("Manual", "On Approval", "On Marked Present")
EVENT_FIELDS = (
	"event_name",
	"organiser_name",
	"status",
	"event_date",
	"start_time",
	"end_time",
	"venue_name",
	"venue_address",
	"venue_map_url",
	"logo",
	"brand_colour",
)
# Before the generic engine, the E-Pass used these two flags. Still honoured (and set) for "E-Pass".
LEGACY_OUTPUT = "E-Pass"


class Blank(dict):
	"""A dict whose missing keys read as "" in templates, so {{ event.details.typo }} prints nothing
	instead of "None"."""

	def __getattr__(self, key):
		if key.startswith("__"):
			raise AttributeError(key)
		value = self.get(key)
		return "" if value is None else value


def is_approved(doc):
	return str(doc.get("status") or "").strip().lower() == "approve"


def get_active_event():
	return frappe.db.get_value(EVENT_DOCTYPE, {"status": "Active"}, "name")


def get_event_for_web_form(web_form):
	"""The event a web form belongs to: the Active one listing it, else a Draft one, else the Active event."""
	if web_form:
		rows = frappe.get_all(
			"ITO Event Web Form",
			filters={"web_form": web_form, "parenttype": EVENT_DOCTYPE},
			pluck="parent",
		)
		if rows:
			events = frappe.get_all(
				EVENT_DOCTYPE,
				filters={"name": ["in", rows], "status": ["!=", "Closed"]},
				fields=["name", "status"],
				order_by="modified desc",
			)
			for status in ("Active", "Draft"):
				match = next((e.name for e in events if e.status == status), None)
				if match:
					return match
	return get_active_event()


def event_for(doc):
	return (doc.get("event") if doc else None) or get_active_event()


def _fmt_time(value):
	from frappe.utils import get_time

	return get_time(value).strftime("%I:%M %p").lstrip("0") if value else ""


def event_context(doc=None, event=None):
	"""Jinja method: the event of `doc` (or `event`, or the Active event) with display-ready values.

	Use in a Print Format as  {%- set event = event_context(doc) -%}
	"""
	name = event or event_for(doc)
	ctx = Blank(details=Blank())
	if not name or not frappe.db.exists(EVENT_DOCTYPE, name):
		return ctx

	ev = frappe.get_cached_doc(EVENT_DOCTYPE, name)
	ctx.update({field: ev.get(field) for field in EVENT_FIELDS})
	ctx["name"] = ev.name
	ctx["details"] = Blank({row.key: row.value for row in ev.details if row.key})
	ctx["logo_url"] = get_url(ev.logo) if ev.logo else ""

	if ev.event_date:
		date = getdate(ev.event_date)
		ctx["date_long"] = f"{date.day} {date.strftime('%B %Y')}"  # 23 November 2026
		ctx["date_short"] = f"{date.day} {date.strftime('%b %Y')}"  # 23 Nov 2026
		ctx["weekday"] = date.strftime("%A")
	start, end = _fmt_time(ev.start_time), _fmt_time(ev.end_time)
	ctx["time_display"] = f"{start} – {end}" if start and end else start or end
	ctx["when_short"] = ", ".join(x for x in (ctx.date_short, ctx.time_display) if x)
	return ctx


# ----------------------------------------------------------------------------
# Outputs
# ----------------------------------------------------------------------------


def get_outputs(event, enabled_only=True):
	if not event or not frappe.db.exists(EVENT_DOCTYPE, event):
		return []
	rows = frappe.get_cached_doc(EVENT_DOCTYPE, event).outputs
	return [row for row in rows if cint(row.enabled) or not enabled_only]


def find_output(event, output):
	output = (output or "").strip().lower()
	return next((row for row in get_outputs(event) if row.output_name.lower() == output), None)


def output_sent(doc, output, event):
	if output == LEGACY_OUTPUT and (cint(doc.get("epass_email_sent")) or cint(doc.get("mail_sent"))):
		return True
	return any(row.output == output and row.event == event for row in doc.get("output_log") or [])


def block_reason(doc, row, check_permission=True):
	"""Why `row` cannot be sent for this registration, or None if it can."""
	if check_permission and not doc.has_permission("write"):
		return "Not permitted"
	if not row:
		return "Output not set up for this event"
	if cint(row.requires_approval) and not is_approved(doc):
		return "Registration is not approved"
	if not doc.email:
		return "No email address"
	return None


def render_context(doc, event, output=None):
	from ito_customization.ito_customization.doc_events.epass import epass_url

	token = doc.epass_token or ""
	return {
		"doc": doc,
		"event": event_context(doc, event),
		"output": output,
		"page_url": epass_url(token) if token else "",
		"qr_url": f"{get_url()}/api/method/ito_customization.ito_customization.doc_events.epass.qr_image?t={token}"
		if token
		else "",
	}


def render_email(row, ctx):
	template = frappe.get_cached_doc("Email Template", row.email_template)
	body = template.response_html if cint(template.use_html) else template.response
	subject = frappe.render_template(template.subject or row.output_name, ctx)
	return subject, frappe.render_template(body or "", ctx)


def _ensure_token(doc):
	if not doc.epass_token:
		doc.epass_token = frappe.generate_hash(length=24)
		frappe.db.set_value(DOCTYPE, doc.name, "epass_token", doc.epass_token, update_modified=False)


def _enqueue(docname, output, event, force):
	frappe.enqueue(
		"ito_customization.ito_customization.doc_events.event_outputs.send_output",
		queue="short",
		docname=docname,
		output=output,
		event=event,
		force=force,
		enqueue_after_commit=True,
	)


def queue_outputs_for_trigger(doc, trigger):
	"""Queue every enabled output of the registration's event whose 'Send When' is `trigger`."""
	event = event_for(doc)
	queued = []
	for row in get_outputs(event):
		if row.send_on != trigger or block_reason(doc, row, check_permission=False):
			continue
		if output_sent(doc, row.output_name, event):
			continue
		_enqueue(doc.name, row.output_name, event, force=False)
		queued.append(row.output_name)
	return queued


def send_output(docname, output, event=None, force=False):
	"""Background job: email one output to one registration."""
	from ito_customization.ito_customization.doc_events.epass import qr_png_bytes

	doc = frappe.get_doc(DOCTYPE, docname)
	event = event or event_for(doc)
	row = find_output(event, output)
	if block_reason(doc, row, check_permission=False):
		return
	if not force and output_sent(doc, row.output_name, event):
		return

	_ensure_token(doc)
	ctx = render_context(doc, event, row.output_name)
	subject, message = render_email(row, ctx)

	attachments = []
	pdf = _make_pdf(doc, row)
	if pdf:
		_save_pdf(doc, row.output_name, pdf)
		if cint(row.attach_pdf):
			attachments.append(pdf)
	if cint(row.attach_qr) and ctx["page_url"]:
		attachments.append({"fname": f"{row.output_name}-QR-{doc.name}.png", "fcontent": qr_png_bytes(ctx["page_url"])})

	frappe.sendmail(
		recipients=[doc.email],
		subject=subject,
		message=message,
		attachments=attachments,
		reference_doctype=DOCTYPE,
		reference_name=doc.name,
	)
	_log_sent(doc, row.output_name, event)


def pdf_field(output):
	"""Registration field that keeps the latest PDF of an output: "E-Pass" -> e_pass_pdf.
	For a new output, add an Attach field with this name (Customize Form) and it is filled too."""
	return f"{frappe.scrub(output)}_pdf"


def pdf_file_name(doc, output):
	"""E-Pass-Ravi-Kumar-WF-0009: easy to recognise once shared. The record ID keeps it unique."""
	# keep letters, vowel signs and digits of any script (e.g. Hindi), everything else becomes "-"
	chars = (c if unicodedata.category(c)[0] in "LMN" else "-" for c in doc.get("full_name") or "")
	person = re.sub(r"-+", "-", "".join(chars)).strip("-")[:40]
	return "-".join(part for part in (output, person, doc.name) if part)


def _make_pdf(doc, row):
	"""{"fname", "fcontent"} of the output's PDF, or None if it has no print format or fails."""
	if not row.print_format:
		return None
	try:
		pdf = frappe.attach_print(
			DOCTYPE, doc.name, print_format=row.print_format, file_name=pdf_file_name(doc, row.output_name)
		)
	except Exception:
		frappe.log_error(title=f"{row.output_name} PDF could not be generated for {doc.name}")
		return None
	return pdf if pdf["fname"].endswith(".pdf") else None


def _save_pdf(doc, output, pdf):
	"""Keep the PDF as a private file on the registration, point the output's PDF field at it, and
	delete the output's older PDFs, so staff only ever see the latest one. Returns the file URL."""
	field = pdf_field(output)
	has_field = frappe.get_meta(DOCTYPE).has_field(field)
	file = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": pdf["fname"],
			"attached_to_doctype": DOCTYPE,
			"attached_to_name": doc.name,
			"attached_to_field": field if has_field else None,
			"is_private": 1,
			"content": pdf["fcontent"],
		}
	).insert(ignore_permissions=True)

	if has_field:
		frappe.db.set_value(DOCTYPE, doc.name, field, file.file_url, update_modified=False)
		doc.set(field, file.file_url)
	delete_old_pdfs(doc.name, output, keep=file.name)
	# Anyone with this registration open gets the new file without reloading (see web_forms_registration.js)
	frappe.publish_realtime(
		"output_pdf_updated",
		{"name": doc.name, "output": output, "field": field if has_field else None, "file_url": file.file_url},
		doctype=DOCTYPE,
		docname=doc.name,
		after_commit=True,
	)
	return file.file_url


def delete_old_pdfs(docname, output, keep):
	"""Delete the PDFs of `output` on this registration made before the File `keep`. A file on disk that
	the kept PDF shares (same content) stays, Frappe only removes it when nothing uses it."""
	# Only older ones: if two jobs save a PDF at the same moment, the newest still survives.
	created = frappe.db.get_value("File", keep, "creation")
	base = {"attached_to_doctype": DOCTYPE, "attached_to_name": docname, "name": ["!=", keep], "creation": ["<=", created]}
	old = set(frappe.get_all("File", filters={**base, "attached_to_field": pdf_field(output)}, pluck="name"))
	# Older PDFs may have no field set: match them by name, e.g. E-Pass-WF-0002.pdf, E-Pass-Ravi-Kumar-WF-0002.pdf
	old |= set(frappe.get_all("File", filters={**base, "file_name": ["like", f"{output}-%.pdf"]}, pluck="name"))
	for name in old:
		frappe.delete_doc("File", name, ignore_permissions=True, force=True)


def generate_pdf_for(doc, output, event=None):
	"""Make and save an output's PDF without emailing it. Returns the file URL."""
	event = event or event_for(doc)
	row = find_output(event, output)
	if not row:
		frappe.throw(f"{output} is not set up for this registration's event.")
	if not row.print_format:
		frappe.throw(f"{output} has no Print Format, so there is no PDF to make.")
	if cint(row.requires_approval) and not is_approved(doc):
		frappe.throw(f"{output} PDF cannot be made: the registration is not approved.")
	_ensure_token(doc)
	pdf = _make_pdf(doc, row)
	if not pdf:
		frappe.throw(f"The {output} PDF could not be made. See the Error Log for details.")
	return _save_pdf(doc, row.output_name, pdf)


def refresh_pdfs(docname, skip=None):
	"""Background job: make or remake the registration's PDFs from its current details. Sends nothing.
	`skip`: outputs an email job is already making the PDF for."""
	doc = frappe.get_doc(DOCTYPE, docname)
	for row in pdf_outputs(doc):
		if row.output_name in (skip or []):
			continue
		pdf = _make_pdf(doc, row)
		if pdf:
			_save_pdf(doc, row.output_name, pdf)


def pdf_outputs(doc):
	"""Outputs of the registration's event that have a PDF it may have now (approved, if required)."""
	return [
		row
		for row in get_outputs(event_for(doc))
		if row.print_format and (is_approved(doc) or not cint(row.requires_approval))
	]


def queue_pdf_refresh(doc, skip=None):
	if not [row for row in pdf_outputs(doc) if row.output_name not in (skip or [])]:
		return
	frappe.enqueue(
		"ito_customization.ito_customization.doc_events.event_outputs.refresh_pdfs",
		queue="short",
		docname=doc.name,
		skip=skip or [],
		enqueue_after_commit=True,
		job_id=f"refresh_pdfs::{doc.name}",
		deduplicate=True,
	)


def _log_sent(doc, output, event):
	log = frappe.get_doc(
		{
			"doctype": "ITO Output Log",
			"parent": doc.name,
			"parenttype": DOCTYPE,
			"parentfield": "output_log",
			"idx": len(doc.get("output_log") or []) + 1,
			"output": output,
			"event": event,
			"recipient": doc.email,
			"sent_on": now_datetime(),
			"sent_by": frappe.session.user,
		}
	)
	log.db_insert()
	if output == LEGACY_OUTPUT:
		frappe.db.set_value(DOCTYPE, doc.name, {"epass_email_sent": 1, "mail_sent": 1}, update_modified=False)


# ----------------------------------------------------------------------------
# Desk API (buttons on the registration form / list, and the event form)
# ----------------------------------------------------------------------------


@frappe.whitelist()
def get_send_options(docname=None):
	"""Enabled outputs for a registration's event (or the Active event, for the list view)."""
	if docname:
		doc = frappe.get_doc(DOCTYPE, docname)
		doc.check_permission("read")
		event = event_for(doc)
	else:
		frappe.has_permission(DOCTYPE, "read", throw=True)
		event = get_active_event()
	rows = get_outputs(event)
	meta = frappe.get_meta(DOCTYPE)
	return {
		"event": event,
		"outputs": [row.output_name for row in rows],
		# output -> its PDF field, for the Download buttons
		"pdf_fields": {
			row.output_name: pdf_field(row.output_name)
			for row in rows
			if row.print_format and meta.has_field(pdf_field(row.output_name))
		},
	}


@frappe.whitelist()
def send_now(docname, output):
	"""Queue one output for one registration, even if it was sent before."""
	doc = frappe.get_doc(DOCTYPE, docname)
	doc.check_permission("write")
	event = event_for(doc)
	row = find_output(event, output)
	reason = block_reason(doc, row)
	if reason:
		frappe.throw(f"{output} cannot be sent: {reason}.")
	_ensure_token(doc)
	_enqueue(doc.name, row.output_name, event, force=True)
	return True


@frappe.whitelist()
def bulk_send(names, output):
	"""Queue one output for each selected registration. Each uses its own event's setup.
	Records that cannot be sent are skipped and reported back."""
	if isinstance(names, str):
		names = frappe.parse_json(names)

	queued, skipped = [], []
	for name in names or []:
		if not frappe.db.exists(DOCTYPE, name):
			skipped.append({"name": name, "reason": "Not found"})
			continue
		doc = frappe.get_doc(DOCTYPE, name)
		event = event_for(doc)
		row = find_output(event, output)
		reason = block_reason(doc, row)
		if reason:
			skipped.append(
				{"name": name, "full_name": doc.full_name, "email": doc.email, "status": doc.status, "reason": reason}
			)
			continue
		_ensure_token(doc)
		_enqueue(doc.name, row.output_name, event, force=True)
		queued.append(name)

	return {"queued": queued, "skipped": skipped}


@frappe.whitelist()
def preview(event, output, docname=None):
	"""Render an output's email for one registration without sending anything."""
	frappe.has_permission(EVENT_DOCTYPE, "read", doc=event, throw=True)
	row = find_output(event, output)
	if not row:
		frappe.throw(f"{output} is not an enabled output of {event}.")

	if not docname:
		docname = frappe.db.get_value(DOCTYPE, {"event": event, "status": "Approve"}, "name") or frappe.db.get_value(
			DOCTYPE, {"event": event}, "name"
		)
	if not docname:
		frappe.throw("There is no registration for this event yet to preview with.")
	doc = frappe.get_doc(DOCTYPE, docname)
	doc.check_permission("read")

	subject, message = render_email(row, render_context(doc, event, row.output_name))
	return {
		"docname": doc.name,
		"subject": subject,
		"message": message,
		"print_format": row.print_format,
	}
