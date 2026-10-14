"""Draft Sales Invoices for self-reported registration payments."""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from ito_customization.ito_customization.registration_payment import (
    PAGE as ITO_PAGE,
    _get_registration_fee_item,
    _get_registration_fee_rate,
    _get_razorpay_settings,
    _get_total_students,
)

PAYMENT_MODES = {
    "dd": ("Demand Draft (DD)", "dd_no", "custom_dd_no"),
    "neft": ("NEFT/RTGS", "utr_no", "custom_utr_no"),
    "cash": ("Cash Deposit", "transaction_id", "custom_transaction_id"),
    "upi": ("QR Code / UPI", "upi_ref", "custom_upi_transaction_id__ref_no"),
}


def create_or_update_manual_registration_invoice(
    customer: str, payment: dict, form_name: str = ITO_PAGE
) -> str:
    """Save one draft invoice for a self-reported registration payment."""
    mode = payment.get("mode")
    if mode not in PAYMENT_MODES:
        frappe.throw(_("Invalid manual payment mode."))

    details = payment.get("payment_details") or {}
    if not isinstance(details, dict):
        frappe.throw(_("Invalid payment details."))

    currency = "INR" if payment.get("region", "india") == "india" else "USD"
    if form_name == ITO_PAGE:
        settings, _ = _get_razorpay_settings(form_name=form_name, customer=customer)
        item_code = _get_registration_fee_item(company=settings.company, form_name=form_name, customer=customer)
        rate = _get_registration_fee_rate(currency, form_name=form_name, customer=customer)
        total_students = _get_total_students(customer)
    elif form_name == "Little Champ Registration":
        from ito_customization.ito_customization import little_champ_registration_payment as lc

        settings = lc.get_settings_for_page(lc.PAGE)
        item_code = lc._get_registration_fee_item(settings.company)
        rate = lc._get_registration_fee_rate(currency)
        total_students = lc._get_total_students(customer)
    else:
        frappe.throw(_("Unsupported registration form."))
    company = settings.company
    if not item_code or rate <= 0:
        frappe.throw(_("Registration fee item and rate must be configured."))

    free_registrations = cint(payment.get("free_registrations")) if form_name == ITO_PAGE else 0
    if free_registrations < 0 or free_registrations > total_students:
        frappe.throw(_("Invalid number of free registrations."))
    paid_students = total_students - free_registrations
    if paid_students <= 0:
        frappe.throw(_("No registration fee is due."))

    amount = flt(paid_students * rate, 2)
    _validate_payment(details, mode, amount)
    invoice = _find_manual_invoice(customer, company, item_code, mode, details)
    if not invoice:
        invoice = frappe.new_doc("Sales Invoice")

    invoice.customer = customer
    invoice.company = company
    invoice.currency = currency
    invoice.selling_price_list = "Standard Selling"
    invoice.due_date = nowdate()
    invoice.debit_to = _get_receivable_account(customer, company)
    invoice.set("items", [])
    invoice.append("items", {
        "item_code": item_code,
        "item_name": frappe.db.get_value("Item", item_code, "item_name"),
        "uom": frappe.db.get_value("Item", item_code, "stock_uom"),
        "qty": paid_students,
        "rate": rate,
        "price_list_rate": rate,
        "income_account": frappe.db.get_value("Company", company, "default_income_account"),
        "expense_account": frappe.db.get_value("Company", company, "default_expense_account"),
        "cost_center": frappe.db.get_value("Company", company, "cost_center"),
    })
    _set_payment_fields(invoice, mode, details, amount)
    invoice.flags.ignore_user_permissions = True
    if invoice.is_new():
        invoice.insert(ignore_permissions=True)
    else:
        invoice.save(ignore_permissions=True)
    return invoice.name


def _validate_payment(details: dict, mode: str, amount: float) -> None:
    reference_field = PAYMENT_MODES[mode][1]
    if not str(details.get(reference_field) or "").strip():
        frappe.throw(_("Payment reference is required."))
    if mode in ("dd", "neft") and not details.get("bank_name"):
        frappe.throw(_("Bank name is required."))
    if not details.get("date"):
        frappe.throw(_("Payment date is required."))
    try:
        getdate(details["date"])
        reported_amount = flt(details.get("amount"))
    except (TypeError, ValueError):
        frappe.throw(_("Invalid payment date or amount."))
    if abs(reported_amount - amount) > 0.01:
        frappe.throw(_("Payment amount must match the registration fee of {0}.").format(amount))


def _find_manual_invoice(customer: str, company: str, item_code: str, mode: str, details: dict):
    label, input_field, invoice_field = PAYMENT_MODES[mode]
    reference = str(details[input_field]).strip()
    matches = frappe.get_all(
        "Sales Invoice",
        filters={
            "custom_payment_mode": label,
            invoice_field: reference,
            "docstatus": ["!=", 2],
        },
        fields=["name", "customer", "company", "docstatus"],
        order_by="creation desc",
    )
    if not matches:
        return None

    if (
        len(matches) > 1
        or matches[0].customer != customer
        or matches[0].company != company
        or not frappe.db.exists(
            "Sales Invoice Item", {"parent": matches[0].name, "item_code": item_code}
        )
    ):
        frappe.throw(_("This payment reference is already used by another invoice."))
    if matches[0].docstatus != 0:
        frappe.throw(_("This payment reference is already used by a submitted invoice."))
    return frappe.get_doc("Sales Invoice", matches[0].name)


def _get_receivable_account(customer: str, company: str) -> str:
    account = frappe.db.get_value(
        "Party Account",
        {"parent": customer, "parenttype": "Customer", "company": company},
        "account",
    ) or frappe.db.get_value("Company", company, "default_receivable_account")
    if not account:
        frappe.throw(_("No receivable account is configured for company {0}.").format(company))
    return account


def _set_payment_fields(invoice, mode: str, details: dict, amount: float) -> None:
    invoice.custom_payment_mode = PAYMENT_MODES[mode][0]
    invoice.custom_bank_name = details.get("bank_name") if mode in ("dd", "neft") else None
    invoice.custom_dd_no = str(details["dd_no"]).strip() if mode == "dd" else None
    invoice.custom_utr_no = str(details["utr_no"]).strip() if mode == "neft" else None
    invoice.custom_bank_deposited_in = (details.get("bank_name") or "SBI") if mode == "cash" else None
    invoice.custom_transaction_id = str(details["transaction_id"]).strip() if mode == "cash" else None
    invoice.custom_upi_transaction_id__ref_no = str(details["upi_ref"]).strip() if mode == "upi" else None
    invoice.custom_date = getdate(details["date"])
    invoice.custom_amount = amount
