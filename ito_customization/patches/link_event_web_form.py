import frappe

WEB_FORM = "award-ceremony-registration"


def execute():
	"""List the award ceremony web form on the event that was created for it."""
	if not frappe.db.exists("Web Form", WEB_FORM):
		return
	if frappe.db.exists("ITO Event Web Form", {"web_form": WEB_FORM}):
		return
	event = frappe.db.get_value("ITO Event", {"status": "Active"}, "name")
	if not event:
		return
	doc = frappe.get_doc("ITO Event", event)
	doc.append("web_forms", {"web_form": WEB_FORM})
	doc.flags.ignore_permissions = True
	doc.save()
