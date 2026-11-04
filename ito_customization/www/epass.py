from urllib.parse import quote

import frappe

from ito_customization.ito_customization.doc_events.epass import (
	EVENT_NAME,
	DOCTYPE,
	attendance_info,
	is_approved,
	is_staff,
	photo_data_uri,
)


def get_context(context):
	context.no_cache = 1
	context.event_name = EVENT_NAME
	context.valid = False
	context.reason = "This E-Pass is invalid or could not be found."
	context.photo = ""
	context.d = None
	context.is_staff = False
	context.token = ""
	context.attendance = None
	context.can_mark = False
	context.login_url = ""

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
	context.token = token
	context.login_url = f"/login?redirect-to=/epass%3Ft%3D{quote(token)}"
	context.is_staff = is_staff()
	if context.is_staff:
		context.attendance = attendance_info(registration)
		declined = str(registration.attendance_confirmation or "").strip().lower().startswith("no")
		context.can_mark = not declined
	context.d = registration
	context.photo = photo_data_uri(registration.photo)
	return context
