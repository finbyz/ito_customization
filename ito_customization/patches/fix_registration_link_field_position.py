import json

import frappe

FIELDNAME = "web_forms_registration"

# DocType -> field (inside the first tab/section) to place the link after
ANCHORS = {
	"Customer": "customer_group",
	"Lead": "customer",
	"Contact": "status",
	"Teacher": "customer_reference",
}


def execute():
	for dt, anchor in ANCHORS.items():
		cf = frappe.db.exists("Custom Field", {"dt": dt, "fieldname": FIELDNAME})
		if cf:
			frappe.db.set_value("Custom Field", cf, "insert_after", anchor, update_modified=False)

		ps = frappe.db.get_value("Property Setter", {"doc_type": dt, "property": "field_order"}, ["name", "value"], as_dict=True)
		if not ps:
			continue
		order = [f for f in json.loads(ps.value) if f != FIELDNAME]
		if anchor in order:
			order.insert(order.index(anchor) + 1, FIELDNAME)
		else:
			# anchor missing from the saved order: append inside the form rather than at the very top
			order.append(FIELDNAME)
		frappe.db.set_value("Property Setter", ps.name, "value", json.dumps(order), update_modified=False)
		frappe.clear_cache(doctype=dt)
