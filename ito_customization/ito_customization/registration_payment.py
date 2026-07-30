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

PAGE = "ITO Registration"
FEE_ITEM_NAME = "ITO Registration Fee"
FEE_ITEM_GROUP = "Fee Component"
RATE_PER_STUDENT_INR = 150


def _get_session_customer():
    if frappe.session.user == "Guest":
        frappe.throw(_("Please login to continue."), frappe.PermissionError)

    customer = frappe.cache().get_value(f"ito_customer_{frappe.session.user}")
    if not customer:
        customer = frappe.db.get_value("Portal User", {"user": frappe.session.user}, "parent")
    if not customer:
        frappe.throw(_("Please save School Information first."))
    return customer


def _get_total_students(customer):
    # Matches the lookup used by save_registration_step/save_ito_registration:
    # one Exams Summary per customer, its rows replaced wholesale on every step-3 save.
    exams_summary = frappe.db.get_value("Exams Summary", {"customer": customer}, "name")
    if not exams_summary:
        return 0
    total = frappe.db.sql(
        "select sum(no_of_students) from `tabExam Summary CT` where parent=%s",
        exams_summary,
    )[0][0]
    return cint(total)


def _get_or_create_fee_item(company):
    # Item.autoname (ito_customization's CustomItem) rewrites item_code/name to a
    # company-prefixed code, so the real primary key is not FEE_ITEM_NAME itself -
    # look it up (and dedupe) by item_name, which autoname leaves untouched.
    existing = frappe.db.get_value("Item", {"item_name": FEE_ITEM_NAME}, "name")
    if existing:
        item = frappe.get_doc("Item", existing)
        if not any(u.uom == "Nos" for u in item.uoms or []):
            item.append("uoms", {"uom": "Nos", "conversion_factor": 1})
            item.save(ignore_permissions=True)
        return existing
    item = frappe.get_doc(
        {
            "doctype": "Item",
            "item_name": FEE_ITEM_NAME,
            "item_group": FEE_ITEM_GROUP,
            "stock_uom": "Nos",
            "is_stock_item": 0,
            # 999295: "services involving conduct of examination for admission to
            # educational institutions" - required by india_compliance for all Items.
            "gst_hsn_code": "999295",
            "custom_company": company,
            # ito_customization's CustomItem.validate() doesn't call super().validate(),
            # so ERPNext never auto-populates the UOM Conversion Detail row for items
            # created via API (only the Desk form's JS does that client-side) - without
            # it, the first Item Price ever auto-inserted for this item fails with
            # "UOM Nos not found in Item ...". Add it explicitly.
            "uoms": [{"uom": "Nos", "conversion_factor": 1}],
        }
    ).insert(ignore_permissions=True)
    return item.name


def _find_registration_fee_invoice(customer, company):
    fee_item = _get_or_create_fee_item(company)
    rows = frappe.get_all(
        "Sales Invoice",
        filters={
            "customer": customer,
            "company": company,
            "docstatus": ["!=", 2],
        },
        fields=["name", "docstatus", "outstanding_amount", "grand_total"],
        order_by="creation desc",
    )
    for row in rows:
        if frappe.db.exists(
            "Sales Invoice Item", {"parent": row.name, "item_code": fee_item}
        ):
            return row
    return None


@frappe.whitelist(allow_guest=True)
def initiate_registration_fee_payment(free_registrations=0):
    customer = _get_session_customer()

    # The admin's page routing (Multi Company Razorpay Settings' "Used For"
    # tag) decides both which Razorpay account AND which company this
    # registration fee is invoiced under - not a hardcoded company.
    settings = get_settings_for_page(PAGE)
    company = settings.company

    total_students = _get_total_students(customer)
    free_registrations = max(0, min(cint(free_registrations), total_students))
    paid_students = total_students - free_registrations
    amount = flt(paid_students * RATE_PER_STUDENT_INR)

    if amount <= 0:
        frappe.throw(_("No outstanding registration fee to pay."))

    existing = _find_registration_fee_invoice(customer, company)
    if existing and existing.docstatus == 1 and flt(existing.outstanding_amount) <= 0:
        frappe.throw(_("Registration fee has already been paid."))

    # The fee can change between two "Pay Now" clicks (e.g. more students added
    # to Exams Summary after an earlier unpaid invoice was created) - an unpaid
    # invoice whose total no longer matches the current fee is stale, so cancel
    # it and start fresh rather than silently charging the old amount.
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
        invoice = frappe.get_doc(
            {
                "doctype": "Sales Invoice",
                "customer": customer,
                "company": company,
                "items": [
                    {
                        "item_code": item,
                        "qty": 1,
                        "rate": amount,
                    }
                ],
            }
        )
        invoice.insert(ignore_permissions=True)
        
        # submit() doesn't accept ignore_permissions - use frappe.flags instead
        original_ignore_permissions = frappe.flags.ignore_permissions
        try:
            frappe.flags.ignore_permissions = True
            invoice.submit()
        finally:
            frappe.flags.ignore_permissions = original_ignore_permissions
        
        invoice_name = invoice.name
        pay_amount = flt(invoice.outstanding_amount)

    # create_payment_for_sales_invoice enforces a real Sales Invoice read-permission
    # check, which portal Customer users don't have directly. _get_session_customer()
    # above already established that the current session legitimately owns `customer`,
    # and invoice_name is always scoped to that same customer, so it's safe to bypass
    # permissions just for this one call.
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

    checkout_context = get_checkout_context(token)
    checkout_context["sales_invoice"] = invoice_name
    return checkout_context


@frappe.whitelist(allow_guest=True)
def confirm_registration_fee_payment(
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
def get_registration_fee_payment_status():
    customer = _get_session_customer()
    company = get_settings_for_page(PAGE).company
    invoice = _find_registration_fee_invoice(customer, company)
    if not invoice or invoice.docstatus != 1:
        return {"paid": False}

    payment_entry = frappe.db.get_value(
        "Payment Entry Reference",
        {"reference_doctype": "Sales Invoice", "reference_name": invoice.name},
        "parent",
        order_by="creation desc",
    )
    return {
        "paid": flt(invoice.outstanding_amount) <= 0,
        "sales_invoice": invoice.name,
        "payment_entry": payment_entry,
    }
