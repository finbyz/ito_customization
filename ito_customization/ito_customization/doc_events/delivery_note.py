import frappe
from frappe import _
from frappe.utils import flt

REQUIRED_BOOK_PERCENT = 60


def validate(doc, method=None):
    validate_book_order_requirement(doc)


def get_customer_totals(customer):
    """Return registration amount and book order amount for a customer."""
    invoices = frappe.get_all(
        "Sales Invoice",
        filters={"customer": customer, "docstatus": 1},
        pluck="name",
    )

    registration_amount = 0.0
    if invoices:
        registration_rows = frappe.get_all(
            "Sales Invoice Item",
            filters={
                "parent": ["in", invoices],
                "parenttype": "Sales Invoice",
                "item_group": "Fee component",
            },
            fields=[{"SUM": "amount", "as": "total_amount"}],
            parent_doctype="Sales Invoice",
        )
        if registration_rows:
            registration_amount = flt(registration_rows[0].total_amount)

    book_groups = frappe.get_all(
        "Item Group",
        filters={"custom_is_book": 1},
        pluck="name",
    )
    if not book_groups:
        return registration_amount, 0.0

    orders = frappe.get_all(
        "Sales Order",
        filters={"customer": customer, "docstatus": 1},
        pluck="name",
    )
    if not orders:
        return registration_amount, 0.0

    rows = frappe.get_all(
        "Sales Order Item",
        filters={
            "parent": ["in", orders],
            "parenttype": "Sales Order",
            "item_group": ["in", book_groups],
        },
        fields=[{"SUM": "amount", "as": "total_amount"}],
        parent_doctype="Sales Order",
    )
    book_order_amount = flt(rows[0].total_amount) if rows else 0.0

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