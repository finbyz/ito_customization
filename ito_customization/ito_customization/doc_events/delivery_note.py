import frappe
from frappe import _
from frappe.utils import flt

REGISTRATION_GROUP = "fee component"
BOOK_GROUP = "olympiad books"
REQUIRED_BOOK_PERCENT = 60


def validate(doc, method=None):
    validate_book_order_requirement(doc)


def get_customer_totals(customer):
    """Return (registration_amount, book_order_amount) from submitted Sales Invoices."""
    invoices = frappe.get_all(
        "Sales Invoice",
        filters={"customer": customer, "docstatus": 1},
        pluck="name",
    )

    if not invoices:
        return 0.0, 0.0

    items = frappe.get_all(
        "Sales Invoice Item",
        filters={
            "parent": ["in", invoices],
            "parenttype": "Sales Invoice",
            "item_group": ["in", ["Fee component", "Olympiad Books"]],
        },
        fields=["item_group", "amount"],
        parent_doctype="Sales Invoice",
    )

    registration_amount = 0.0
    book_order_amount = 0.0

    for item in items:
        group = (item.item_group or "").strip().lower()

        if group == REGISTRATION_GROUP:
            registration_amount += flt(item.amount)
        elif group == BOOK_GROUP:
            book_order_amount += flt(item.amount)

    return registration_amount, book_order_amount


def validate_book_order_requirement(doc):
    if not doc.customer:
        return

    registration_amount, book_order_amount = get_customer_totals(doc.customer)

    # No registration amount means there is nothing to validate.
    if registration_amount <= 0:
        return

    required_book_order_amount = registration_amount * REQUIRED_BOOK_PERCENT / 100

    if book_order_amount < required_book_order_amount:
        frappe.throw(
            _(
                "Delivery Note cannot be created for customer "
                "<b>{0}</b>.<br><br>"
                "Registration Amount: <b>{1}</b><br>"
                "Required Book Order Amount ({2}%): <b>{3}</b><br>"
                "Current Book Order Amount: <b>{4}</b>"
            ).format(
                doc.customer,
                frappe.format_value(registration_amount, {"fieldtype": "Currency"}),
                REQUIRED_BOOK_PERCENT,
                frappe.format_value(required_book_order_amount, {"fieldtype": "Currency"}),
                frappe.format_value(book_order_amount, {"fieldtype": "Currency"}),
            )
        )