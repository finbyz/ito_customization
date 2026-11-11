import frappe


def execute():
	"""Make the Web Forms Registration link fields regular custom fields, not system generated."""
	frappe.db.set_value(
		"Custom Field",
		{"fieldname": "web_forms_registration", "dt": ["in", ["Customer", "Lead", "Contact", "Teacher"]]},
		"is_system_generated",
		0,
	)
