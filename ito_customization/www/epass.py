import frappe

from ito_customization.ito_customization.doc_events.epass import (
	EVENT_NAME,
	DOCTYPE,
	is_approved,
	photo_data_uri,
)


def get_context(context):
	context.no_cache = 1
	context.event_name = EVENT_NAME
	context.valid = False
	context.reason = "This E-Pass is invalid or could not be found."
	context.photo = ""
	context.d = None

	token = (frappe.form_dict.get("t") or "").strip()
	if not token:
		return context

	name = frappe.db.get_value(DOCTYPE, {"epass_token": token}, "name")
	if not name:
		return context

	registration = frappe.get_doc(DOCTYPE, name)
	if not is_approved(registration):
		context.reason = "This registration has not been approved for an E-Pass."
		return context

	context.valid = True
	context.d = registration
	context.photo = photo_data_uri(registration.photo)
	return context
