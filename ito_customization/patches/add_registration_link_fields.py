import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

FIELD = {
	"fieldname": "web_forms_registration",
	"label": "Web Forms Registration",
	"fieldtype": "Link",
	"options": "Web Forms Registration",
	"read_only": 1,
	"no_copy": 1,
	"print_hide": 1,
	"module": "ITO Customization",
}


def execute():
	create_custom_fields({dt: [FIELD] for dt in ("Customer", "Lead", "Contact", "Teacher")}, update=True)
	# create_custom_fields flags them system generated; these are regular, editable custom fields
	frappe.db.set_value("Custom Field", {"fieldname": FIELD["fieldname"]}, "is_system_generated", 0)
