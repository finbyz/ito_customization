# Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors

from __future__ import annotations

from urllib.parse import parse_qs, urlparse
import frappe
import frappe.share
from frappe import _
from frappe.utils import cint, flt

from multi_company_razorpay.api import (
    checkout_success,
    create_payment_for_sales_invoice,
    get_checkout_context,
    get_settings_for_page,
)

PAGE = "ITO Registration"


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


def _get_registration_fee_item(company=None, form_name=None, customer=None):
    if not form_name and customer:
        if frappe.db.get_value("Customer", customer, "custom_is_wof"):
            form_name = "WOF Olympiad"
    if not form_name:
        form_name = PAGE

    from ito_customization.ito_customization import api
    fee_info = api.get_registration_fee(form_name=form_name)
    if fee_info and fee_info.get("success") and fee_info.get("item_code"):
        return fee_info.get("item_code")

    item = frappe.db.get_value(
        "Item",
        {"custom_is_registration_item": 1, "custom_form_name": form_name, "disabled": 0},
        "name",
    )
    if not item and ("WOF" in str(form_name) or "Olympiad" in str(form_name)):
        item = frappe.db.get_value(
            "Item",
            {"custom_is_registration_item": 1, "custom_form_name": ["in", ["WOF Olympiad", "WOF Registration", "WOF Olympiad Registration"]], "disabled": 0},
            "name",
        )
    if not item and ("WOF" in str(form_name) or "Olympiad" in str(form_name)):
        item = frappe.db.get_value(
            "Item",
            {"item_name": ["like", "%WOF%"], "disabled": 0},
            "name",
        )
    if not item:
        item = frappe.db.get_value(
            "Item",
            {"custom_is_registration_item": 1, "custom_form_name": PAGE, "disabled": 0},
            "name",
        )
    return item


def _get_registration_fee_rate(currency="INR", form_name=None, customer=None):
    from ito_customization.ito_customization import api
    target_form = form_name
    if not target_form and customer:
        if frappe.db.get_value("Customer", customer, "custom_is_wof"):
            target_form = "WOF Olympiad"
    if not target_form:
        target_form = PAGE

    fee_info = api.get_registration_fee(form_name=target_form)
    if fee_info and fee_info.get("success"):
        rate = fee_info.get("rate_usd") if currency == "USD" else fee_info.get("rate_inr")
        if rate and flt(rate) > 0:
            return flt(rate)

    item_code = _get_registration_fee_item(form_name=target_form, customer=customer)
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


def _get_razorpay_settings(form_name=None, customer=None):
    if form_name:
        try:
            return get_settings_for_page(form_name), form_name
        except Exception:
            pass

    for candidate in ["WOF Registration", "WOF Olympiad", "WOF Olympiad Registration"]:
        try:
            return get_settings_for_page(candidate), candidate
        except Exception:
            pass

    try:
        settings = get_settings_for_page(PAGE)
        return settings, PAGE
    except Exception:
        pass

    enabled_settings = frappe.get_all(
        "Multi Company Razorpay Settings",
        filters={"enabled": 1},
        fields=["name"],
        order_by="name",
    )
    if enabled_settings:
        s = frappe.get_doc("Multi Company Razorpay Settings", enabled_settings[0].name)
        pages = frappe.get_all(
            "Multi Company Razorpay Settings Page",
            filters={"parent": s.name},
            fields=["page"],
        )
        matched_page = pages[0].page if pages else None
        return s, matched_page

    frappe.throw(_("No enabled Razorpay settings configured in the system."))


def _find_registration_fee_invoice(customer, company, fee_item=None):
    if not fee_item:
        fee_item = _get_registration_fee_item(company, customer=customer)
    if not fee_item:
        return None
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


@frappe.whitelist()
def initiate_registration_fee_payment(customer=None, free_registrations=0, form_name=None, total_students=None):
    try:
        customer = _get_customer(customer)
        if not form_name:
            if frappe.db.get_value("Customer", customer, "custom_is_wof"):
                form_name = "WOF Olympiad"
            else:
                form_name = PAGE

        settings, resolved_page = _get_razorpay_settings(form_name=form_name, customer=customer)
        company = settings.company

        if total_students is not None and cint(total_students) > 0:
            total_students_val = cint(total_students)
        else:
            total_students_val = _get_total_students(customer)

        free_registrations = max(
            0,
            min(cint(free_registrations), total_students_val),
        )

        rate = _get_registration_fee_rate("INR", form_name=form_name, customer=customer)
        if rate <= 0:
            frappe.throw(
                _("Registration fee rate is not configured for {0}.").format(form_name)
            )

        paid_students = total_students_val - free_registrations
        amount = flt(paid_students * rate)

        if amount <= 0:
            frappe.throw(
                _("No outstanding registration fee to pay.")
            )

        fee_item = _get_registration_fee_item(company=company, form_name=form_name, customer=customer)
        if not fee_item:
            frappe.throw(
                _("Registration fee item is not configured for {0}.").format(form_name or PAGE)
            )

        # ---------------------------------------------------------
        # Find existing registration fee invoice
        # ---------------------------------------------------------
        existing = _find_registration_fee_invoice(
            customer,
            company,
            fee_item=fee_item,
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
            # Get Registration Fee Item
            # -----------------------------------------------------
            item = fee_item

            # -----------------------------------------------------
            # Get Customer Receivable Account
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
                            "qty": paid_students,
                            "rate": rate,
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
                        or flt(row.qty) != flt(paid_students)
                        or abs(flt(row.rate) - rate) > 0.01
                    ):
                        row.item_code = item
                        row.qty = paid_students
                        row.rate = rate
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
                invoice.selling_price_list = "Standard Selling"

                # -------------------------------------------------
                # Set Company Address
                # -------------------------------------------------
                if company_address:
                    invoice.company_address = company_address

                invoice.due_date = frappe.utils.today()
                invoice.currency = frappe.db.get_value(
                    "Customer",
                    customer,
                    "default_currency",
                )

                # -------------------------------------------------
                # Add Registration Fee Item
                # -------------------------------------------------
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
                        "qty": paid_students,
                        "rate": rate,
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

                # -------------------------------------------------
                # Insert new Sales Invoice
                # -------------------------------------------------
                _original = frappe.flags.ignore_permissions
                try:
                    frappe.flags.ignore_permissions = True
                    invoice.due_date = frappe.utils.today()
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
                page=resolved_page,
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

@frappe.whitelist()
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



@frappe.whitelist()
def get_registration_fee_payment_status(customer=None, form_name=None):
    customer = _get_customer(customer)
    if not form_name and customer:
        if frappe.db.get_value("Customer", customer, "custom_is_wof"):
            form_name = "WOF Olympiad"
    if not form_name:
        form_name = PAGE

    page_name = form_name if form_name in ["WOF Olympiad", "WOF Registration"] else PAGE
    try:
        settings = get_settings_for_page(page_name)
    except Exception:
        try:
            settings = get_settings_for_page("WOF Registration")
        except Exception:
            settings = get_settings_for_page(PAGE)

    company = settings.company
    fee_item = _get_registration_fee_item(company, form_name=form_name, customer=customer)
    invoice = _find_registration_fee_invoice(customer, company, fee_item=fee_item)
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
