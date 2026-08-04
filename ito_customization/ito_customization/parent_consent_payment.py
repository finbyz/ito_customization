# Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from ito_customization.ito_customization.doc_events.web_page.olympiad_book_order import (
    get_dynamic_subjects,
)
from multi_company_razorpay.api import (
    checkout_success,
    create_payment_for_sales_invoice,
    get_checkout_context,
    get_settings_for_page,
)

FEE_ITEM_NAME = "ITO Consent & Books Fee"
FEE_ITEM_GROUP = "Fee Component"
PAGE = "Parent Consent"
REGISTRATION_FEE = 175


def _resolve_customer_by_token(token):
    # Same lookup/expiry rules as get_school_by_token - this is a guest-facing
    # link, so the token is the only thing standing in for authentication.
    result = frappe.db.get_value(
        "Customer",
        {"custom_consent_token": token},
        ["name", "customer_name", "custom_concern_form_last_date", "custom_is_little_champ"],
    )
    if not result:
        frappe.throw(_("Invalid or expired link."))
    customer, school_name, expiry_date, is_little_champ = result
    if expiry_date and getdate(nowdate()) > getdate(expiry_date):
        frappe.throw(_("This link has expired. Please contact the school for a new link."))
    return customer, school_name, bool(is_little_champ)


def _compute_grand_total(school_name, selected_class, selections):
    # Recompute server-side from the same pricing source the frontend reads
    # (get_dynamic_subjects), instead of trusting a client-calculated total -
    # mirrors the "3+1" promo logic in parent-consent.tsx's useMemo.
    subjects = {
        s["code"]: s
        for s in get_dynamic_subjects(school_name, selected_class).get("subjects", [])
    }

    exam_fee = wb_fee = tb_fee = 0.0
    exam_count = 0
    selected_exam_fees = []

    # We need to know if it's little champ to know whether to include tb/wb
    # We can get this from the customer record since we have school_name
    is_little_champ = False
    customer_doc = frappe.db.get_value("Customer", {"customer_name": school_name}, ["custom_is_little_champ"], as_dict=True)
    if customer_doc and customer_doc.custom_is_little_champ:
        is_little_champ = True

    for sel in selections or []:
        subj = subjects.get(sel.get("subject_code"))
        if not subj:
            continue
        if sel.get("exam") and subj.get("exam_available"):
            exam_count += 1
            fee = flt(subj.get("exam_fee"))
            exam_fee += fee
            selected_exam_fees.append(fee)
        
        # Little Champ only — keep tb/wb fees
        if is_little_champ:
            if sel.get("tb") and subj.get("tb", {}).get("price") is not None:
                tb_fee += flt(subj["tb"]["price"])
            if sel.get("wb") and subj.get("wb", {}).get("price") is not None:
                wb_fee += flt(subj["wb"]["price"])

    discount = min(selected_exam_fees) if exam_count >= 4 and selected_exam_fees else 0.0
    
    if is_little_champ:
        return flt(REGISTRATION_FEE + tb_fee + wb_fee)
    else:
        return flt(REGISTRATION_FEE + exam_fee - discount)


def _get_or_create_fee_item(company):
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
            # Item.validate() in this app doesn't populate the UOM Conversion table
            # itself (see registration_payment.py for the full explanation) - add
            # it explicitly so the first auto Item Price insert doesn't fail.
            "uoms": [{"uom": "Nos", "conversion_factor": 1}],
        }
    ).insert(ignore_permissions=True)
    return item.name


def _find_consent_fee_invoice(customer, company):
    fee_item = _get_or_create_fee_item(company)
    rows = frappe.get_all(
        "Sales Invoice",
        filters={"customer": customer, "company": company, "docstatus": ["!=", 2]},
        fields=["name", "docstatus", "outstanding_amount", "grand_total"],
        order_by="creation desc",
    )
    for row in rows:
        if frappe.db.exists("Sales Invoice Item", {"parent": row.name, "item_code": fee_item}):
            return row
    return None


@frappe.whitelist(allow_guest=True)
def initiate_parent_consent_payment(data):
    payload = frappe.parse_json(data) if isinstance(data, str) else data
    token = payload.get("token")
    selected_class = payload.get("selected_class")
    selections = payload.get("selections") or []

    # The admin's page routing (Multi Company Razorpay Settings' "Used For"
    # tag) decides both which Razorpay account AND which company this
    # consent fee is invoiced under - not a hardcoded company.
    settings = get_settings_for_page(PAGE)
    company = settings.company

    customer, school_name, _is_little_champ = _resolve_customer_by_token(token)
    amount = _compute_grand_total(school_name, selected_class, selections)

    if amount <= 0:
        # Nothing owed for this selection - no invoice/payment needed,
        # frontend should treat this as consent-complete.
        return {"no_payment_required": True}

    existing = _find_consent_fee_invoice(customer, company)
    
    if existing and existing.docstatus == 1 and flt(existing.outstanding_amount) <= 0:
        frappe.throw(_("Fees have already been paid for this consent."))

    # The fee can change between two "Pay Now" clicks (e.g. selections changed
    # after an earlier unpaid invoice was created) - an unpaid invoice whose
    # total no longer matches the current fee is stale, so cancel it and start
    # fresh rather than silently charging the old amount.
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
                "items": [{"item_code": item, "qty": 1, "rate": amount}],
            }
        )
        invoice.insert(ignore_permissions=True)
        invoice.submit()
        invoice_name = invoice.name
        pay_amount = flt(invoice.outstanding_amount)

    # Guest (allow_guest=True) endpoint - create_payment_for_sales_invoice enforces
    # a real Sales Invoice read-permission check that no guest/session has. The
    # invoice above was just created for the token-resolved customer, so it's
    # safe to bypass permissions only for this one call.
    original_ignore_permissions = frappe.flags.ignore_permissions
    try:
        frappe.flags.ignore_permissions = True
        result = create_payment_for_sales_invoice(
            sales_invoice=invoice_name, amount=pay_amount, page=PAGE
        )
    finally:
        frappe.flags.ignore_permissions = original_ignore_permissions

    checkout_token = parse_qs(urlparse(result["checkout_url"]).query).get("token", [None])[0]
    if not checkout_token:
        frappe.throw(_("Unable to start Razorpay checkout."))

    checkout_context = get_checkout_context(checkout_token)
    checkout_context["sales_invoice"] = invoice_name
    return checkout_context


@frappe.whitelist(allow_guest=True)
def confirm_parent_consent_payment(
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
def get_parent_consent_payment_status(token):
    customer, _school_name, _is_little_champ = _resolve_customer_by_token(token)
    company = get_settings_for_page(PAGE).company
    invoice = _find_consent_fee_invoice(customer, company)
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
