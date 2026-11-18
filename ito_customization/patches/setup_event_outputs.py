"""Move the E-Pass from hard-coded values (and the short-lived E-Pass Settings) to the generic
ITO Event setup: an Active event, the "E-Pass Email" Email Template, and the "ITO E-Pass" Print
Format, all editable in the desk. Safe to run on a site that already has some of these."""

import frappe
from frappe.permissions import add_permission, update_permission_property

DOCTYPE = "Web Forms Registration"
EVENT_NAME = "ITO Excellence Award Felicitation Ceremony 2026–27"
PRINT_FORMAT = "ITO E-Pass"
EMAIL_TEMPLATE = "E-Pass Email"
ROLE = "ITO Event Manager"

FALLBACK = {
	"organiser_name": "Indian Talent Olympiad",
	"event_name": EVENT_NAME,
	"event_date": "2026-11-23",
	"start_time": "10:00:00",
	"end_time": "13:00:00",
	"venue_name": "Dr. B. R. Ambedkar Bhavana",
	"venue_address": (
		"9c, 2nd A Cross Rd, Sector A, Yelahanka Satellite Town, Yelahanka New Town, "
		"Bengaluru, Karnataka 560064"
	),
	"venue_map_url": "https://maps.app.goo.gl/7Ca2HyThYC82fR9L8",
	"entry_note": (
		"Entry is strictly for the invited individual. Accompanying persons, including family "
		"members, friends, colleagues or other guests, are not permitted. This pass is "
		"non-transferable and entry is subject to verification at the venue."
	),
}


def execute():
	values = _old_values()
	_email_template()
	_print_format()
	event = _event(values)
	frappe.db.sql(
		f"update `tab{DOCTYPE}` set event = %s where ifnull(event, '') = ''",
		event,
	)
	_permissions()
	_drop_epass_settings()


def _template(name):
	path = frappe.get_app_path("ito_customization", "templates", "event_outputs", name)
	with open(path) as f:
		return f.read()


def _old_values():
	"""Values saved in E-Pass Settings (if the client edited them), else the original ones."""
	saved = frappe.db.get_singles_dict("E-Pass Settings") or {}
	return {key: saved.get(key) or default for key, default in FALLBACK.items()}


def _email_template():
	if frappe.db.exists("Email Template", EMAIL_TEMPLATE):
		return
	frappe.get_doc(
		{
			"doctype": "Email Template",
			"name": EMAIL_TEMPLATE,
			"subject": "Your E-Pass – {{ event.event_name }}",
			"use_html": 1,
			"response_html": _template("epass_email.html"),
		}
	).insert(ignore_permissions=True)


def _print_format():
	"""The print format came from a fixture until now; switch it to the event-based template once.
	From here on it is not a fixture, so edits made in the desk are never overwritten."""
	html = _template("epass_print.html")
	if frappe.db.exists("Print Format", PRINT_FORMAT):
		frappe.db.set_value("Print Format", PRINT_FORMAT, "html", html)
		return
	frappe.get_doc(
		{
			"doctype": "Print Format",
			"name": PRINT_FORMAT,
			"doc_type": DOCTYPE,
			"module": "ITO Customization",
			"custom_format": 1,
			"print_format_type": "Jinja",
			"standard": "No",
			"font_size": 14,
			"margin_top": 15,
			"margin_bottom": 15,
			"margin_left": 15,
			"margin_right": 15,
			"page_number": "Hide",
			"pdf_generator": "wkhtmltopdf",
			"html": html,
		}
	).insert(ignore_permissions=True)


def _event(values):
	existing = frappe.db.get_value("ITO Event", {"event_name": values["event_name"]}, "name")
	if existing:
		return existing
	active = frappe.db.get_value("ITO Event", {"status": "Active"}, "name")
	doc = frappe.get_doc(
		{
			"doctype": "ITO Event",
			"status": "Draft" if active else "Active",
			"brand_colour": "#2f7f73",
			**{k: v for k, v in values.items() if k != "entry_note"},
			"details": [{"label": "Entry Note", "key": "entry_note", "value": values["entry_note"]}],
			"outputs": [
				{
					"output_name": "E-Pass",
					"enabled": 1,
					"send_on": "On Approval",
					"requires_approval": 1,
					"email_template": EMAIL_TEMPLATE,
					"print_format": PRINT_FORMAT,
					"attach_pdf": 1,
					"attach_qr": 1,
				}
			],
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def _permissions():
	"""Let ITO Event Managers edit the templates their events use."""
	for doctype in ("Print Format", "Email Template"):
		add_permission(doctype, ROLE, 0)
		for ptype in ("read", "write", "create"):
			update_permission_property(doctype, ROLE, 0, ptype, 1, validate=False)


def _drop_epass_settings():
	if frappe.db.exists("DocType", "E-Pass Settings"):
		frappe.delete_doc("DocType", "E-Pass Settings", force=True, ignore_permissions=True)
	frappe.db.delete("Singles", {"doctype": "E-Pass Settings"})
