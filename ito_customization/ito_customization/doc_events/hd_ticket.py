import frappe
from frappe.model.mapper import get_mapped_doc


@frappe.whitelist(methods=["POST"])
def make_issue(source_name: str, target_doc=None):
    return get_mapped_doc(
        "HD Ticket",
        source_name,
        {
            "HD Ticket": {
                "doctype": "Issue",
                "field_no_map": [
                    "status",
                    "priority",
                    "customer",
                    "via_customer_portal",
                    "response_by",
                    "agreement_status",
                    "service_level_agreement_creation",
                    "first_response_time",
                    "first_responded_on",
                    "avg_response_time",
                    "resolution_details",
                    "resolution_time",
                    "user_resolution_time",
                    "opening_date",
                    "opening_time",
                ],
                "field_map": {"name": "custom_hd_ticket"},
            }
        },
        target_doc,
        set_matching_links,
    )


def set_matching_links(source, target):
    for source_field, target_field, doctype in (
        ("priority", "priority", "Issue Priority"),
        ("ticket_type", "issue_type", "Issue Type"),
        ("customer", "customer", "Customer"),
    ):
        value = source.get(source_field)
        if value and frappe.db.exists(doctype, value):
            target.set(target_field, value)
