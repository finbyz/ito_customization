import frappe


def link_to_hd_ticket(doc, method=None):
    if doc.custom_hd_ticket:
        frappe.db.set_value(
            "HD Ticket", doc.custom_hd_ticket, "custom_issue", doc.name,
            update_modified=False,
        )


def unlink_from_hd_ticket(doc, method=None):
    if not doc.custom_hd_ticket:
        return

    if frappe.db.get_value("HD Ticket", doc.custom_hd_ticket, "custom_issue") == doc.name:
        frappe.db.set_value(
            "HD Ticket", doc.custom_hd_ticket, "custom_issue", None,
            update_modified=False,
        )
