import frappe


def execute():
	"""Runs before doctypes sync: ITO Event's permissions refer to this role."""
	if not frappe.db.exists("Role", "ITO Event Manager"):
		frappe.get_doc({"doctype": "Role", "role_name": "ITO Event Manager", "desk_access": 1}).insert(
			ignore_permissions=True
		)
