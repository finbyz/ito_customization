"""Tax policy for registration invoices."""

import frappe

from ito_customization.ito_customization.registration_payment import PAGE

REGISTRATION_FORMS = {
    PAGE,
    "Little Champ Registration",
    "WOF Olympiad",
    "WOF Registration",
    "Chitrakala Registration",
}

PAYMENT_MODES = {
    "Demand Draft (DD)",
    "NEFT/RTGS",
    "Cash Deposit",
    "QR Code / UPI",
    "Pay Online (Razorpay)",
}


def keep_registration_invoice_tax_free(doc, method=None):
    if doc.custom_payment_mode not in PAYMENT_MODES:
        return
    if not any(
        frappe.db.get_value("Item", row.item_code, "custom_form_name") in REGISTRATION_FORMS
        for row in doc.items
        if row.item_code
    ):
        return

    doc.flags.dont_auto_add_taxes = True
    doc.taxes_and_charges = None
    doc.tax_category = None
    doc.set("taxes", [])
    if method == "validate":
        doc.calculate_taxes_and_totals()
