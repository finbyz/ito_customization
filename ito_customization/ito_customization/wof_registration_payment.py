# Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import frappe
import frappe.share
from frappe import _
from frappe.utils import cint, flt, nowdate

from multi_company_razorpay.api import (
    checkout_success,
    create_payment_for_sales_invoice,
    get_checkout_context,
    get_settings_for_company,
    get_settings_for_page,
)

PAGE = "ITO Registration"
WOF_PAGE = "WOF Registration"
FEE_ITEM_NAME = "WOF Registration Fee"
FEE_ITEM_GROUP = "Fee Component"
RETENTION_PERCENT = 0.20  # 20% retention by school


def _get_customer(customer=None):
    session_customer = frappe.cache().get_value(f"ito_customer_{frappe.session.user}")
    if not session_customer:
        session_customer = frappe.db.get_value("Portal User", {"user": frappe.session.user}, "parent")
    customer = session_customer or customer
    if session_customer and customer and session_customer != customer:
        frappe.throw(_("You are not allowed to pay for this customer."), frappe.PermissionError)
    if not customer or not frappe.db.exists("Customer", customer):
        frappe.throw(_("Please save School Information first."))
    return customer


def _count_matrix_students(entry_matrix):
    if not entry_matrix:
        return 0
    if isinstance(entry_matrix, str):
        try:
            entry_matrix = json.loads(entry_matrix)
        except Exception:
            return 0

    total = 0
    if isinstance(entry_matrix, dict):
        alias_map = {
            "colouring": "Colouring Competition",
            "colouring_competition": "Colouring Competition",
            "handwriting": "Handwriting Competition",
            "handwriting_competition": "Handwriting Competition",
            "sketching": "Sketching Competition",
            "sketching_competition": "Sketching Competition",
            "cartoon": "Cartoon Making",
            "cartoon_making": "Cartoon Making",
            "caricature": "Caricature (Cartoon)",
            "caricature_cartoon": "Caricature (Cartoon)",
            "greeting": "Greeting Card Making",
            "greeting_card_making": "Greeting Card Making",
        }
        for group_key, comps in entry_matrix.items():
            if isinstance(comps, dict):
                group_totals = {}
                for comp_name, count in comps.items():
                    canonical = alias_map.get(comp_name.lower().replace(" ", "_"), comp_name)
                    group_totals[canonical] = max(group_totals.get(canonical, 0), cint(count))
                total += sum(group_totals.values())
    return total


def _get_total_students(customer, entry_matrix=None):
    if entry_matrix:
        count = _count_matrix_students(entry_matrix)
        if count > 0:
            frappe.cache().set_value(f"wof_entry_matrix_{customer}", entry_matrix)
            return count

    cached_matrix = frappe.cache().get_value(f"wof_entry_matrix_{customer}")
    if cached_matrix:
        count = _count_matrix_students(cached_matrix)
        if count > 0:
            return count

    # Fallback to Exams Summary if populated
    exams_summary = frappe.db.get_value("Exams Summary", {"customer": customer}, "name")
    if exams_summary:
        total = frappe.db.sql(
            "select sum(no_of_students) from `tabExam Summary CT` where parent=%s",
            exams_summary,
        )[0][0]
        if total:
            return cint(total)

    return 0


def _get_razorpay_settings():
    try:
        return get_settings_for_page(WOF_PAGE)
    except Exception:
        pass

    try:
        return get_settings_for_company("World Olympiad Foundation")
    except Exception:
        pass

    return get_settings_for_page(PAGE)


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


def _get_registration_fee_item(company=None):
    item = frappe.db.get_value(
        "Item",
        {"custom_is_registration_item": 1, "custom_form_name": WOF_PAGE, "disabled": 0},
        "name",
    )
    if item:
        return item
    existing = frappe.db.get_value("Item", {"item_name": FEE_ITEM_NAME, "disabled": 0}, "name")
    if existing:
        return existing
    if company:
        return _get_or_create_fee_item(company)
    return None


def _get_registration_fee_rate(currency="INR"):
    item_code = _get_registration_fee_item()
    if not item_code:
        return 0.0

    prices = frappe.get_all(
        "Item Price",
        filters={"item_code": item_code, "selling": 1},
        fields=["price_list_rate", "currency"],
        order_by="modified desc",
    )
    for p in prices:
        if p.currency == currency and flt(p.price_list_rate) > 0:
            return flt(p.price_list_rate)

    inr_price = frappe.db.get_value(
        "Item Price",
        {"item_code": item_code, "currency": currency},
        "price_list_rate",
        order_by="modified desc",
    )
    if not inr_price and currency == "INR":
        inr_price = frappe.db.get_value(
            "Item Price",
            {"item_code": item_code},
            "price_list_rate",
            order_by="modified desc",
        )
    if not inr_price:
        std_rate = frappe.db.get_value("Item", item_code, "standard_rate")
        if std_rate and flt(std_rate) > 0:
            inr_price = flt(std_rate)

    return flt(inr_price) if inr_price and flt(inr_price) > 0 else 0.0


def _get_retention_rate(customer=None, item_code=None):
    retention_val = None

    if not item_code:
        item_code = _get_registration_fee_item()

    if item_code:
        try:
            val = frappe.db.get_value("Item", item_code, "custom_retention")
            if val is not None and val != "":
                retention_val = flt(val)
        except Exception:
            pass

    if (retention_val is None or retention_val == 0) and customer:
        try:
            val = frappe.db.get_value("Customer", customer, "custom_retention")
            if val is not None and val != "":
                retention_val = flt(val)
        except Exception:
            pass

    if retention_val is not None and retention_val > 0:
        if retention_val > 1.0:
            return flt(retention_val / 100.0)
        return flt(retention_val)

    return 0.0


def _find_fee_invoice(customer, company):
    fee_item = _get_registration_fee_item(company)
    if not fee_item:
        return None
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
def initiate_wof_registration_payment(customer=None, entry_matrix=None, currency="INR", total_students=None):
    try:
        customer = _get_customer(customer)

        settings = _get_razorpay_settings()
        company = settings.company

        num_students = cint(total_students) if total_students else _get_total_students(customer, entry_matrix)
        if num_students <= 0:
            frappe.throw(_("Please enter at least one participating student in the Entry Matrix."))

        rate_per_student = _get_registration_fee_rate(currency)
        if rate_per_student <= 0:
            frappe.throw(
                _("Registration fee rate is not configured for {0}.").format(WOF_PAGE)
            )

        retention_rate = _get_retention_rate(customer, _get_registration_fee_item())
        gross_amount = flt(num_students * rate_per_student)
        school_retention = flt(gross_amount * retention_rate)
        net_payable_amount = flt(gross_amount - school_retention)
        effective_rate = flt(net_payable_amount / num_students) if num_students else rate_per_student

        if net_payable_amount <= 0:
            frappe.throw(_("No outstanding registration fee to pay."))

        existing = _find_fee_invoice(customer, company)

        if (
            existing
            and existing.docstatus == 1
            and abs(flt(existing.grand_total) - net_payable_amount) > 0.01
        ):
            if frappe.db.exists("Sales Invoice", existing.name):
                invoice = frappe.get_doc("Sales Invoice", existing.name)
                if invoice.status != "Paid":
                    invoice.flags.ignore_permissions = True
                    invoice.flags.ignore_user_permissions = True
                    invoice.cancel()
            existing = None

        if existing and existing.docstatus == 1:
            invoice_name = existing.name
            pay_amount = flt(existing.outstanding_amount)
            if pay_amount <= 0:
                frappe.throw(_("WOF registration fee is already paid."))
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
                debit_to = frappe.db.get_value("Company", company, "default_receivable_account")
            if not debit_to:
                frappe.throw(
                    _("No receivable account found for Customer {0} and Company {1}.").format(
                        customer, company
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
                invoice = frappe.get_doc("Sales Invoice", existing.name)
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
                if company_address and invoice.company_address != company_address:
                    invoice.company_address = company_address
                    needs_update = True

                if len(invoice.items) != 1:
                    invoice.set("items", [])
                    invoice.append("items", {
                        "item_code": item,
                        "item_name": frappe.db.get_value("Item", item, "item_name"),
                        "uom": frappe.db.get_value("Item", item, "stock_uom") or "Nos",
                        "qty": num_students,
                        "rate": effective_rate,
                        "income_account": frappe.db.get_value("Company", company, "default_income_account"),
                        "expense_account": frappe.db.get_value("Company", company, "default_expense_account"),
                        "cost_center": frappe.db.get_value("Company", company, "cost_center"),
                    })
                    frappe.share.add_docshare(
                        "Item", item, user=frappe.session.user, read=1, write=1,
                        flags={"ignore_share_permission": True}
                    )
                    needs_update = True
                else:
                    row = invoice.items[0]
                    if row.item_code != item or flt(row.qty) != flt(num_students) or abs(flt(row.rate) - effective_rate) > 0.01:
                        row.item_code = item
                        row.qty = num_students
                        row.rate = effective_rate
                        row.income_account = frappe.db.get_value("Company", company, "default_income_account")
                        row.expense_account = frappe.db.get_value("Company", company, "default_expense_account")
                        row.cost_center = frappe.db.get_value("Company", company, "cost_center")
                        needs_update = True
                        frappe.share.add_docshare(
                            "Item", item, user=frappe.session.user, read=1, write=1,
                            flags={"ignore_share_permission": True}
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

                needs_update = True
                if needs_update:
                    _orig = frappe.flags.ignore_permissions
                    try:
                        frappe.flags.ignore_permissions = True
                        invoice.save(ignore_permissions=True)
                    finally:
                        frappe.flags.ignore_permissions = _orig

                invoice_name = invoice.name
                pay_amount = net_payable_amount
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

                invoice.due_date = nowdate()
                invoice.currency = currency

                invoice.append("items", {
                    "item_code": item,
                    "item_name": frappe.db.get_value("Item", item, "item_name"),
                    "uom": frappe.db.get_value("Item", item, "stock_uom") or "Nos",
                    "qty": num_students,
                    "rate": effective_rate,
                    "income_account": frappe.db.get_value("Company", company, "default_income_account"),
                    "expense_account": frappe.db.get_value("Company", company, "default_expense_account"),
                    "cost_center": frappe.db.get_value("Company", company, "cost_center"),
                })

                frappe.share.add_docshare(
                    "Item", item, user=frappe.session.user, read=1, write=1,
                    flags={"ignore_share_permission": True}
                )

                invoice.set("taxes", [])
                invoice.tax_category = None
                invoice.calculate_taxes_and_totals()
                invoice.total_taxes_and_charges = 0
                invoice.base_total_taxes_and_charges = 0
                invoice.grand_total = invoice.net_total
                invoice.base_grand_total = invoice.base_net_total

                _orig = frappe.flags.ignore_permissions
                try:
                    frappe.flags.ignore_permissions = True
                    invoice.insert(ignore_permissions=True)
                finally:
                    frappe.flags.ignore_permissions = _orig

                invoice_name = invoice.name
                pay_amount = net_payable_amount

        if pay_amount <= 0:
            frappe.throw(_("Invalid outstanding amount for payment."))

        _orig = frappe.flags.ignore_permissions
        try:
            frappe.flags.ignore_permissions = True
            result = create_payment_for_sales_invoice(
                sales_invoice=invoice_name,
                amount=pay_amount,
                page=WOF_PAGE if frappe.db.exists("Multi Company Razorpay Settings Page", {"page": WOF_PAGE}) else PAGE,
            )
        finally:
            frappe.flags.ignore_permissions = _orig

        if not result or not result.get("checkout_url"):
            frappe.throw(_("Unable to create Razorpay checkout."))

        token = parse_qs(urlparse(result["checkout_url"]).query).get("token", [None])[0]
        if not token:
            frappe.throw(_("Unable to start Razorpay checkout."))

        checkout_context = get_checkout_context(token)
        checkout_context["sales_invoice"] = invoice_name
        return checkout_context

    except Exception:
        frappe.log_error(title="WOF Registration Payment Failed", message=frappe.get_traceback())
        frappe.throw(_("Unable to initiate WOF registration payment. Please try again or contact support."))


@frappe.whitelist(allow_guest=True)
def confirm_wof_registration_fee_payment(
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
        frappe.log_error(title="Error in confirm_wof_registration_fee_payment", message=frappe.get_traceback())
        raise


@frappe.whitelist(allow_guest=True)
def get_wof_registration_fee_payment_status(customer=None):
    try:
        customer = _get_customer(customer)
    except Exception:
        return {"paid": False}

    try:
        settings = _get_razorpay_settings()
        company = settings.company if settings else None
    except Exception:
        company = None

    filters = {
        "customer": customer,
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
        fee_item = _get_registration_fee_item(company or "")
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
