import frappe


def execute():
	if not frappe.db.exists("Role", "ITO Event Staff"):
		frappe.get_doc({"doctype": "Role", "role_name": "ITO Event Staff", "desk_access": 0}).insert(
			ignore_permissions=True
		)
