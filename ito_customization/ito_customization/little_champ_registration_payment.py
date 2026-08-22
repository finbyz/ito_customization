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
    try:
        customer = _get_customer(customer)

        settings = get_settings_for_page(PAGE)
        company = settings.company

        total_students = _get_total_students(customer)
        amount = flt(total_students * RATE_PER_STUDENT_INR)

        if amount <= 0:
            frappe.throw(
                _("No Little Champ registration fee is due.")
            )

        existing = _find_fee_invoice(
            customer,
            company,
        )

        if (
            existing
            and existing.docstatus == 1
            and abs(flt(existing.grand_total) - amount) > 0.01
        ):
            if frappe.db.exists(
                "Sales Invoice",
                existing.name,
            ):
                invoice = frappe.get_doc(
                    "Sales Invoice",
                    existing.name,
                )

                if invoice.status != "Paid":
                    invoice.flags.ignore_permissions = True
                    invoice.flags.ignore_user_permissions = True
                    invoice.cancel()

            existing = None

        if existing and existing.docstatus == 1:
            invoice_name = existing.name
            pay_amount = flt(existing.outstanding_amount)

            if pay_amount <= 0:
                frappe.throw(
                    _("Little Champ registration fee is already paid.")
                )

        else:
            item = _get_or_create_fee_item(company)

            debit_to = frappe.db.get_value(
                "Party Account",
                {
                    "parent": customer,
                    "parenttype": "Customer",
                    "company": company,
                },
                "account",
            )

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

            company_address = frappe.db.get_value(
                "Address",
                {
                    "is_your_company_address": 1,
                    "link_doctype": "Company",
                    "link_name": company,
                },
                "name",
            )

            if existing and existing.docstatus == 0:
                invoice = frappe.get_doc(
                    "Sales Invoice",
                    existing.name,
                )

                invoice.flags.ignore_permissions = True
                invoice.flags.ignore_user_permissions = True

                needs_update = False

                if invoice.customer != customer:
                    invoice.customer = customer
                    needs_update = True

                if invoice.company != company:
                    invoice.company = company
                    needs_update = True

                if invoice.debit_to != debit_to:
                    invoice.debit_to = debit_to
                    needs_update = True

                if (
                    company_address
                    and invoice.company_address != company_address
                ):
                    invoice.company_address = company_address
                    needs_update = True

                if len(invoice.items) != 1:
                    invoice.set("items", [])

                    invoice.append(
                        "items",
                        {
                            "item_code": item,
                            "item_name": frappe.db.get_value(
                                "Item",
                                item,
                                "item_name",
                            ),
                            "uom": frappe.db.get_value(
                                "Item",
                                item,
                                "stock_uom",
                            ),
                            "qty": total_students,
                            "rate": RATE_PER_STUDENT_INR,
                            "income_account": frappe.db.get_value(
                                "Company",
                                company,
                                "default_income_account",
                            ),
                            "expense_account": frappe.db.get_value(
                                "Company",
                                company,
                                "default_expense_account",
                            ),
                            "cost_center": frappe.db.get_value(
                                "Company",
                                company,
                                "cost_center",
                            ),
                        },
                    )

                    frappe.share.add_docshare(
                        "Item",
                        item,
                        user=frappe.session.user,
                        read=1,
                        write=1,
                        flags={"ignore_share_permission": True},
                    )

                    needs_update = True

                else:
                    row = invoice.items[0]

                    if (
                        row.item_code != item
                        or flt(row.qty) != flt(total_students)
                        or abs(flt(row.rate) - RATE_PER_STUDENT_INR) > 0.01
                    ):
                        row.item_code = item
                        row.qty = total_students
                        row.rate = RATE_PER_STUDENT_INR
                        row.income_account = frappe.db.get_value(
                            "Company",
                            company,
                            "default_income_account",
                        )
                        row.expense_account = frappe.db.get_value(
                            "Company",
                            company,
                            "default_expense_account",
                        )
                        row.cost_center = frappe.db.get_value(
                            "Company",
                            company,
                            "cost_center",
                        )
                        needs_update = True

                        frappe.share.add_docshare(
                            "Item",
                            item,
                            user=frappe.session.user,
                            read=1,
                            write=1,
                            flags={"ignore_share_permission": True},
                        )

                invoice.set("taxes", [])
                invoice.tax_category = None
                invoice.total_taxes_and_charges = 0
                invoice.base_total_taxes_and_charges = 0

                invoice.calculate_taxes_and_totals()

                invoice.total_taxes_and_charges = 0
                invoice.base_total_taxes_and_charges = 0
                invoice.grand_total = invoice.net_total
                invoice.base_grand_total = invoice.base_net_total

                if invoice.disable_rounded_total:
                    invoice.rounded_total = invoice.grand_total
                    invoice.base_rounded_total = invoice.base_grand_total

                needs_update = True

                if needs_update:
                    original_ignore_permissions = (
                        frappe.flags.ignore_permissions
                    )

                    try:
                        frappe.flags.ignore_permissions = True
                        invoice.save(
                            ignore_permissions=True
                        )
                    finally:
                        frappe.flags.ignore_permissions = (
                            original_ignore_permissions
                        )

                invoice_name = invoice.name
                pay_amount = amount

            else:
                invoice = frappe.new_doc("Sales Invoice")

                invoice.flags.ignore_permissions = True
                invoice.flags.ignore_user_permissions = True

                invoice.customer = customer
                invoice.company = company
                invoice.debit_to = debit_to
                invoice.selling_price_list = "Standard Selling"

                if company_address:
                    invoice.company_address = company_address

                invoice.due_date = frappe.utils.today()
                invoice.currency = frappe.db.get_value(
                    "Customer",
                    customer,
                    "default_currency",
                )

                invoice.append(
                    "items",
                    {
                        "item_code": item,
                        "item_name": frappe.db.get_value(
                            "Item",
                            item,
                            "item_name",
                        ),
                        "uom": frappe.db.get_value(
                            "Item",
                            item,
                            "stock_uom",
                        ),
                        "qty": total_students,
                        "rate": RATE_PER_STUDENT_INR,
                        "income_account": frappe.db.get_value(
                            "Company",
                            company,
                            "default_income_account",
                        ),
                        "expense_account": frappe.db.get_value(
                            "Company",
                            company,
                            "default_expense_account",
                        ),
                        "cost_center": frappe.db.get_value(
                            "Company",
                            company,
                            "cost_center",
                        ),
                        "price_list_rate": frappe.db.get_value(
                            "Item Price",
                            {
                                "item_code": item,
                                "price_list": "Standard Selling",
                            },
                            "price_list_rate",
                        ),
                        "base_price_list_rate": frappe.db.get_value(
                            "Item Price",
                            {
                                "item_code": item,
                                "price_list": "Standard Selling",
                            },
                            "price_list_rate",
                        ),
                    },
                )

                frappe.share.add_docshare(
                    "Item",
                    item,
                    user=frappe.session.user,
                    read=1,
                    write=1,
                    flags={"ignore_share_permission": True},
                )

                invoice.set("taxes", [])
                invoice.tax_category = None

                invoice.calculate_taxes_and_totals()

                invoice.total_taxes_and_charges = 0
                invoice.base_total_taxes_and_charges = 0
                invoice.grand_total = invoice.net_total
                invoice.base_grand_total = invoice.base_net_total

                if invoice.disable_rounded_total:
                    invoice.rounded_total = invoice.grand_total
                    invoice.base_rounded_total = invoice.base_grand_total

                original_ignore_permissions = (
                    frappe.flags.ignore_permissions
                )

                try:
                    frappe.flags.ignore_permissions = True
                    invoice.due_date = frappe.utils.today()
                    invoice.insert(
                        ignore_permissions=True
                    )
                finally:
                    frappe.flags.ignore_permissions = (
                        original_ignore_permissions
                    )

                invoice_name = invoice.name
                pay_amount = amount

        if pay_amount <= 0:
            frappe.throw(
                _("Invalid outstanding amount for payment.")
            )

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

        if not result or not result.get("checkout_url"):
            frappe.throw(
                _("Unable to create Razorpay checkout.")
            )

        token = parse_qs(
            urlparse(
                result["checkout_url"]
            ).query
        ).get("token", [None])[0]

        if not token:
            frappe.throw(
                _("Unable to start Razorpay checkout.")
            )

        checkout_context = get_checkout_context(token)
        checkout_context["sales_invoice"] = invoice_name

        return checkout_context

    except Exception:
        frappe.log_error(
            title="Little Champ Registration Payment Failed",
            message=frappe.get_traceback(),
        )

        frappe.throw(
            _(
                "Unable to initiate Little Champ registration payment. "
                "Please try again or contact support."
            )
        )

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
