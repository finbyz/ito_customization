import frappe

def set_school_code(doc, method=None):
    if not doc.erpnext_customer:
        return

    customer = frappe.db.get_value(
        "Customer",
        doc.erpnext_customer,
        ["custom_school_code", "custom_ito_school_code", "custom_is_little_champ"],
        as_dict=True,
    )
    if not customer:
        return

    preferred_code = (
        customer.custom_school_code
        if customer.custom_is_little_champ
        else customer.custom_ito_school_code
    )
    doc.custom_school_code = (
        preferred_code
        or customer.custom_school_code
        or customer.custom_ito_school_code
        or doc.erpnext_customer
    )

def sync_school_code_to_hd(doc, method=None):
    if frappe.db.exists("HD Customer", doc.name):
        frappe.db.set_value(
            "HD Customer", doc.name,
            "custom_school_code", doc.get("custom_ito_school_code"),
            update_modified=False,
        )
