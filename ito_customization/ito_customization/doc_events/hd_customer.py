import frappe

def set_school_code(doc, method=None):
    if frappe.db.exists("Customer", doc.name):
        doc.custom_school_code = frappe.db.get_value(
            "Customer", doc.name, "custom_ito_school_code"
        )

def sync_school_code_to_hd(doc, method=None):
    if frappe.db.exists("HD Customer", doc.name):
        frappe.db.set_value(
            "HD Customer", doc.name,
            "custom_school_code", doc.get("custom_ito_school_code"),
            update_modified=False,
        )