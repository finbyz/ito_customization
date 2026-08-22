# Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors

from __future__ import annotations

from urllib.parse import parse_qs, urlparse
# from contextlib import contextmanager
from frappe.desk.form import assign_to
from frappe.desk.form import assign_to
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
    try:
        customer = _get_session_customer()

        # The admin's page routing (Multi Company Razorpay Settings
        # "Used For" tag) decides both Razorpay account and company.
        settings = get_settings_for_page(PAGE)
        company = settings.company

        total_students = _get_total_students(customer)

        free_registrations = max(
            0,
            min(cint(free_registrations), total_students),
        )

        paid_students = total_students - free_registrations
        amount = flt(paid_students * RATE_PER_STUDENT_INR)

        if amount <= 0:
            frappe.throw(
                _("No outstanding registration fee to pay.")
            )

        # ---------------------------------------------------------
        # Find existing registration fee invoice
        # ---------------------------------------------------------
        existing = _find_registration_fee_invoice(
            customer,
            company,
        )

        # ---------------------------------------------------------
        # If submitted invoice exists but amount has changed,
        # cancel the old invoice and create a new one.
        # ---------------------------------------------------------
        if (
            existing
            and existing.docstatus == 1
            and abs(flt(existing.grand_total) - amount) > 0.01
        ):
            if frappe.db.exists("Sales Invoice", existing.name):
                invoice = frappe.get_doc(
                    "Sales Invoice",
                    existing.name,
                )

                if invoice.status != "Paid":
                    invoice.flags.ignore_permissions = True
                    invoice.flags.ignore_user_permissions = True
                    invoice.cancel()

            existing = None

        # ---------------------------------------------------------
        # Existing submitted invoice
        # ---------------------------------------------------------
        if existing and existing.docstatus == 1:
            invoice_name = existing.name
            pay_amount = flt(existing.outstanding_amount)

            if pay_amount <= 0:
                frappe.throw(
                    _("There is no outstanding amount on the registration invoice.")
                )

        else:
            # -----------------------------------------------------
            # Get/Create Registration Fee Item
            # -----------------------------------------------------
            item = _get_or_create_fee_item(company)

            # -----------------------------------------------------
            # Get Customer Receivable Account
            #
            # Priority:
            # 1. Customer's Party Account for this Company
            # 2. Company's default_receivable_account
            # -----------------------------------------------------
            debit_to = frappe.db.get_value(
                "Party Account",
                {
                    "parent": customer,
                    "parenttype": "Customer",
                    "company": company,
                },
                "account",
            )

            # -----------------------------------------------------
            # Fallback to Company's Default Receivable Account
            # -----------------------------------------------------
            if not debit_to:
                debit_to = frappe.db.get_value(
                    "Company",
                    company,
                    "default_receivable_account",
                )

            if not debit_to:
                frappe.throw(
                    _(
                        "No receivable account found for Customer {0} "
                        "and no Default Receivable Account is configured "
                        "for Company {1}."
                    ).format(
                        customer,
                        company,
                    )
                )

            # -----------------------------------------------------
            # Get Company's Default Address
            # -----------------------------------------------------
            company_address = frappe.db.get_value(
                "Address",
                {
                    "is_your_company_address": 1,
                    "link_doctype": "Company",
                    "link_name": company,
                },
                "name",
            )

            # -----------------------------------------------------
            # Existing Draft Invoice
            # -----------------------------------------------------
            if existing and existing.docstatus == 0:
                invoice = frappe.get_doc(
                    "Sales Invoice",
                    existing.name,
                )

                invoice.flags.ignore_permissions = True
                invoice.flags.ignore_user_permissions = True

                needs_update = False

                # -------------------------------------------------
                # Ensure Customer
                # -------------------------------------------------
                if invoice.customer != customer:
                    invoice.customer = customer
                    needs_update = True

                # -------------------------------------------------
                # Ensure Company
                # -------------------------------------------------
                if invoice.company != company:
                    invoice.company = company
                    needs_update = True

                # -------------------------------------------------
                # Ensure Receivable Account
                # -------------------------------------------------
                if invoice.debit_to != debit_to:
                    invoice.debit_to = debit_to
                    needs_update = True

                # -------------------------------------------------
                # Ensure Company Address
                # -------------------------------------------------
                if (
                    company_address
                    and invoice.company_address != company_address
                ):
                    invoice.company_address = company_address
                    needs_update = True

                # -------------------------------------------------
                # Ensure exactly one item row
                # -------------------------------------------------
                if len(invoice.items) != 1:
                    invoice.set("items", [])

                    invoice.append(
                        "items",
                        {
                            "item_code": item,
                            "qty": paid_students,
                            "rate": RATE_PER_STUDENT_INR,
                        },
                    )

                    needs_update = True

                else:
                    row = invoice.items[0]

                    if (
                        row.item_code != item
                        or flt(row.qty) != flt(paid_students)
                        or abs(flt(row.rate) - RATE_PER_STUDENT_INR) > 0.01
                    ):
                        row.item_code = item
                        row.qty = paid_students
                        row.rate = RATE_PER_STUDENT_INR

                        needs_update = True

                # -------------------------------------------------
                # Save only when something changed
                # -------------------------------------------------
                if needs_update:
                    _original = frappe.flags.ignore_permissions
                    try:
                        frappe.flags.ignore_permissions = True
                        invoice.save(ignore_permissions=True)
                    finally:
                        frappe.flags.ignore_permissions = _original

                invoice_name = invoice.name
                pay_amount = amount

            # -----------------------------------------------------
            # No Existing Invoice
            # -----------------------------------------------------
            else:
                invoice = frappe.new_doc(
                    "Sales Invoice"
                )

                invoice.flags.ignore_permissions = True
                invoice.flags.ignore_user_permissions = True

                invoice.customer = customer
                invoice.company = company
                invoice.debit_to = debit_to

                # -------------------------------------------------
                # Set Company Address
                # -------------------------------------------------
                if company_address:
                    invoice.company_address = company_address

                # -------------------------------------------------
                # Add Registration Fee Item
                # -------------------------------------------------
                invoice.append(
                    "items",
                    {
                        "item_code": item,
                        "qty": paid_students,
                        "rate": RATE_PER_STUDENT_INR,
                    },
                )

                # -------------------------------------------------
                # Insert new Sales Invoice
                # -------------------------------------------------
                _original = frappe.flags.ignore_permissions
                try:
                    frappe.flags.ignore_permissions = True
                    invoice.insert(ignore_permissions=True)
                finally:
                    frappe.flags.ignore_permissions = _original

                invoice_name = invoice.name
                pay_amount = amount

        # ---------------------------------------------------------
        # Create Razorpay Payment
        # ---------------------------------------------------------
        original_ignore_permissions = (
            frappe.flags.ignore_permissions
        )

        try:
            frappe.flags.ignore_permissions = True

            result = create_payment_for_sales_invoice(
                sales_invoice=invoice_name,
                amount=pay_amount,
                page=PAGE,
            )

        finally:
            frappe.flags.ignore_permissions = (
                original_ignore_permissions
            )

        # ---------------------------------------------------------
        # Extract Razorpay Checkout Token
        # ---------------------------------------------------------
        token = parse_qs(
            urlparse(result["checkout_url"]).query
        ).get("token", [None])[0]

        if not token:
            frappe.throw(
                _("Unable to start Razorpay checkout.")
            )

        # ---------------------------------------------------------
        # Get Checkout Context
        # ---------------------------------------------------------
        checkout_context = get_checkout_context(token)

        checkout_context["sales_invoice"] = invoice_name

        return checkout_context

    except Exception:
        frappe.log_error(
            title="Registration Fee Payment Failed",
            message=frappe.get_traceback(),
        )

        frappe.throw(
            _(
                "Unable to initiate registration fee payment. "
                "Please try again or contact support."
            )
        )

@frappe.whitelist(allow_guest=True)
def confirm_registration_fee_payment(
    integration_request, razorpay_payment_id, razorpay_order_id, razorpay_signature
):  
    try:
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
    except Exception:
        frappe.log_error(
            title="Error in confirm_registration_fee_payment",
            message=frappe.get_traceback()
        )
        raise



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
