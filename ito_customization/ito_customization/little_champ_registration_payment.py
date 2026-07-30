# Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import frappe
from frappe import _
from frappe.utils import cint, flt

from multi_company_razorpay.api import (
    checkout_success,
    create_payment_for_sales_invoice,
    get_checkout_context,
    get_settings_for_page,
)

PAGE = "Little Champ Registration"
FEE_ITEM_NAME = "Little Champ Registration Fee"
FEE_ITEM_GROUP = "Fee Component"
RATE_PER_STUDENT_INR = 150


def _get_customer(customer=None):
    session_customer = frappe.db.get_value("Portal User", {"user": frappe.session.user}, "parent")
    customer = session_customer or customer
    if session_customer and customer and session_customer != customer:
        frappe.throw(_("You are not allowed to pay for this customer."), frappe.PermissionError)
    if not customer or not frappe.db.exists("Customer", customer):
        frappe.throw(_("Please save School Information first."))
    if not frappe.db.get_value("Customer", customer, "custom_is_little_champ"):
        frappe.throw(_("This payment is only available for Little Champ registrations."), frappe.PermissionError)
    return customer


def _get_total_students(customer):
    exams_summary = frappe.db.get_value("Exams Summary", {"customer": customer}, "name")
    if not exams_summary:
        return 0
    total = frappe.db.sql(
        "select sum(no_of_students) from `tabExam Summary CT` where parent=%s",
        exams_summary,
    )[0][0]
    return cint(total)


def _get_or_create_fee_item(company):
    existing = frappe.db.get_value("Item", {"item_name": FEE_ITEM_NAME}, "name")
    if existing:
        item = frappe.get_doc("Item", existing)
        if not any(row.uom == "Nos" for row in item.uoms or []):
            item.append("uoms", {"uom": "Nos", "conversion_factor": 1})
            item.save(ignore_permissions=True)
        return item.name

    item = frappe.get_doc({
        "doctype": "Item",
        "item_name": FEE_ITEM_NAME,
        "item_group": FEE_ITEM_GROUP,
        "stock_uom": "Nos",
        "is_stock_item": 0,
        "gst_hsn_code": "999295",
        "custom_company": company,
        "uoms": [{"uom": "Nos", "conversion_factor": 1}],
    }).insert(ignore_permissions=True)
    return item.name


def _find_fee_invoice(customer, company):
    fee_item = _get_or_create_fee_item(company)
    invoices = frappe.get_all(
        "Sales Invoice",
        filters={"customer": customer, "company": company, "docstatus": ["!=", 2]},
        fields=["name", "docstatus", "outstanding_amount", "grand_total"],
        order_by="creation desc",
    )
    for invoice in invoices:
        if frappe.db.exists("Sales Invoice Item", {"parent": invoice.name, "item_code": fee_item}):
            return invoice
    return None


@frappe.whitelist(allow_guest=True)
def initiate_little_champ_registration_payment(customer=None):
    customer = _get_customer(customer)
    settings = get_settings_for_page(PAGE)
    company = settings.company
    total_students = _get_total_students(customer)
    amount = flt(total_students * RATE_PER_STUDENT_INR)
    if amount <= 0:
        frappe.throw(_("No Little Champ registration fee is due."))

    existing = _find_fee_invoice(customer, company)
    if existing and existing.docstatus == 1 and flt(existing.outstanding_amount) <= 0:
        # Return friendly signal instead of throwing — frontend will show "already paid" message
        payment_entry = frappe.db.get_value(
            "Payment Entry Reference",
            {"reference_doctype": "Sales Invoice", "reference_name": existing.name},
            "parent",
            order_by="creation desc",
        )
        return {
            "already_paid": True,
            "sales_invoice": existing.name,
            "payment_entry": payment_entry,
        }
    if existing and existing.docstatus == 1 and abs(flt(existing.grand_total) - amount) > 0.01:
        frappe.get_doc("Sales Invoice", existing.name).cancel()
        existing = None

    if existing and existing.docstatus == 1:
        invoice_name = existing.name
        pay_amount = flt(existing.outstanding_amount)
    else:
        if existing and existing.docstatus == 0:
            frappe.delete_doc("Sales Invoice", existing.name, ignore_permissions=True)
        item = _get_or_create_fee_item(company)
        invoice = frappe.get_doc({
            "doctype": "Sales Invoice",
            "customer": customer,
            "company": company,
            "items": [{"item_code": item, "qty": 1, "rate": amount}],
        })
        invoice.insert(ignore_permissions=True)
        invoice.submit()
        invoice_name = invoice.name
        pay_amount = flt(invoice.outstanding_amount)

    original_ignore_permissions = frappe.flags.ignore_permissions
    try:
        frappe.flags.ignore_permissions = True
        result = create_payment_for_sales_invoice(
            sales_invoice=invoice_name, amount=pay_amount, page=PAGE
        )
    finally:
        frappe.flags.ignore_permissions = original_ignore_permissions

    token = parse_qs(urlparse(result["checkout_url"]).query).get("token", [None])[0]
    if not token:
        frappe.throw(_("Unable to start Razorpay checkout."))
    context = get_checkout_context(token)
    context["sales_invoice"] = invoice_name
    return context


@frappe.whitelist(allow_guest=True)
def confirm_little_champ_registration_payment(
    integration_request, razorpay_payment_id, razorpay_order_id, razorpay_signature
):
    result = checkout_success(
        integration_request=integration_request,
        razorpay_payment_id=razorpay_payment_id,
        razorpay_order_id=razorpay_order_id,
        razorpay_signature=razorpay_signature,
    )
    integration = frappe.get_doc("Integration Request", integration_request)
    data = frappe.parse_json(integration.data or "{}")
    transaction = frappe.get_doc("Razorpay Transaction", data["razorpay_transaction"])
    return {
        "paid": transaction.status == "Completed",
        "payment_entry": transaction.payment_entry,
        "sales_invoice": transaction.reference_docname,
        "redirect_to": result.get("redirect_to"),
    }


@frappe.whitelist(allow_guest=True)
def get_little_champ_payment_status():
    """Check if the current session customer's Little Champ fee has been paid."""
    session_customer = frappe.db.get_value(
        "Portal User", {"user": frappe.session.user}, "parent"
    )
    if not session_customer:
        return {"paid": False}

    try:
        company = get_settings_for_page(PAGE).company
    except Exception:
        company = None

    filters = {
        "customer": session_customer,
        "docstatus": 1,
    }
    if company:
        filters["company"] = company

    invoices = frappe.get_all(
        "Sales Invoice",
        filters=filters,
        fields=["name", "outstanding_amount"],
        order_by="creation desc",
    )

    try:
        fee_item = _get_or_create_fee_item(company or "")
    except Exception:
        fee_item = None

    for invoice in invoices:
        if fee_item and not frappe.db.exists("Sales Invoice Item", {"parent": invoice.name, "item_code": fee_item}):
            continue
        if flt(invoice.outstanding_amount) <= 0:
            payment_entry = frappe.db.get_value(
                "Payment Entry Reference",
                {"reference_doctype": "Sales Invoice", "reference_name": invoice.name},
                "parent",
                order_by="creation desc",
            )
            return {
                "paid": True,
                "sales_invoice": invoice.name,
                "payment_entry": payment_entry,
            }

    return {"paid": False}
