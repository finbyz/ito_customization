# Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
import json
from urllib.parse import parse_qs, urlparse
from frappe.utils import cint, flt, today, add_days, getdate
from multi_company_razorpay.api import (
    get_checkout_context,
    get_settings_for_company,
    get_settings_for_page,
)
from frappe.desk.form import assign_to
from frappe.model.base_document import get_controller

RAZORPAY_PAGE = "WOF Book Order"


def _wof_get_razorpay_settings():
    """Resolve Razorpay settings for WOF Book Order with fallback to WOF company/page."""
    try:
        settings = get_settings_for_page(RAZORPAY_PAGE)
        if settings and settings.company == "World Olympiad Foundation":
            return settings
    except Exception:
        pass

    try:
        return get_settings_for_company("World Olympiad Foundation")
    except Exception:
        pass

    try:
        return get_settings_for_page("WOF Registration")
    except Exception:
        pass

    return get_settings_for_page(RAZORPAY_PAGE)


def _wof_ensure_subject_exists(subject_name):
    """Create a School Subject if it doesn't exist."""
    if not frappe.db.exists("School Subject", subject_name):
        subject = frappe.new_doc("School Subject")
        subject.name = subject_name
        try:
            subject.insert(ignore_permissions=True)
        except frappe.DuplicateEntryError:
            pass


def _wof_get_delivery_warehouse(company):
    """Return a usable leaf warehouse for WOF orders in `company`."""
    warehouse = frappe.db.get_value(
        "Warehouse",
        {"company": company, "warehouse_name": "Stores", "disabled": 0, "is_group": 0},
        "name",
    )
    if not warehouse:
        warehouse = frappe.db.get_value(
            "Warehouse",
            {"company": company, "disabled": 0, "is_group": 0},
            "name",
            order_by="name",
        )
    if not warehouse:
        frappe.throw(
            _("No active delivery warehouse is configured for {0}.").format(
                frappe.bold(company)
            )
        )
    return warehouse


# ══════════════════════════════════════════════════════════════════════
# 1. SAVE WOF BOOKS ORDER
# ══════════════════════════════════════════════════════════════════════

def _wof_create_or_update_customer(school_info):
    school_name = school_info.get("school_name")
    if not school_name:
        frappe.throw("School Name is mandatory")

    existing = frappe.db.exists("Customer", {"customer_name": school_name})

    if existing:
        customer = frappe.get_doc("Customer", existing)
    else:
        customer = frappe.new_doc("Customer")
        customer.customer_name = school_name

    customer.customer_type = "Company"

    if hasattr(customer, "custom_ito_school_code"):
        customer.custom_ito_school_code = school_info.get("ito_school_code")
    if hasattr(customer, "email_id"):
        customer.email_id = school_info.get("contact_email")
    if hasattr(customer, "mobile_no"):
        customer.mobile_no = school_info.get("contact_mobile")
    if hasattr(customer, "custom_taluka"):
        customer.custom_taluka = school_info.get("taluka")
    if hasattr(customer, "custom_district"):
        customer.custom_district = school_info.get("district")
    if hasattr(customer, "custom_customer_category"):
        customer.custom_customer_category = "School Customer"

    if customer.is_new():
        customer.insert(ignore_permissions=True)
    else:
        customer.save(ignore_permissions=True)

    return customer.name


def _wof_create_or_update_address(customer_name, school_info):
    address_name = frappe.db.exists("Address", {"address_title": customer_name})

    if address_name:
        address = frappe.get_doc("Address", address_name)
    else:
        address = frappe.new_doc("Address")
        address.address_title = customer_name
        address.address_type = "Billing"
        address.append("links", {
            "link_doctype": "Customer",
            "link_name": customer_name,
        })

    address.address_line1 = school_info.get("school_address", "")
    address.city = school_info.get("city", "")
    address.state = school_info.get("state", "")
    address.pincode = school_info.get("pincode", "")
    address.country = "India"

    if hasattr(address, "custom_taluka"):
        address.custom_taluka = school_info.get("taluka", "")
    if hasattr(address, "custom_district"):
        address.custom_district = school_info.get("district", "")

    if address.is_new():
        address.insert(ignore_permissions=True)
    else:
        address.save(ignore_permissions=True)

    return address.name


def _wof_create_or_update_primary_contact(primary_contact, customer_name):
    if not primary_contact.get("name"):
        return

    contact_name = frappe.db.exists("Contact", {"first_name": primary_contact.get("name")})

    if contact_name:
        contact = frappe.get_doc("Contact", contact_name)
    else:
        contact = frappe.new_doc("Contact")

    contact.first_name = primary_contact.get("name")
    contact.designation = "Primary Contact"
    contact.email_ids = []
    contact.phone_nos = []

    if primary_contact.get("email"):
        contact.append("email_ids", {
            "email_id": primary_contact.get("email"),
            "is_primary": 1,
        })

    if primary_contact.get("mobile"):
        contact.append("phone_nos", {
            "phone": primary_contact.get("mobile"),
            "is_primary_mobile_no": 1,
        })

    if not contact.links:
        contact.append("links", {
            "link_doctype": "Customer",
            "link_name": customer_name,
        })

    if contact.is_new():
        contact.insert(ignore_permissions=True)
    else:
        contact.save(ignore_permissions=True)

    customer = frappe.get_doc("Customer", customer_name)
    customer.customer_primary_contact = contact.name
    if hasattr(customer, "mobile_no"):
        customer.mobile_no = primary_contact.get("mobile")
    if hasattr(customer, "email_id"):
        customer.email_id = primary_contact.get("email")
    customer.save(ignore_permissions=True)

    return contact.name


def _wof_create_or_update_olympiad_coordinator(coordinator, customer_name):
    if not coordinator.get("name"):
        return

    customer = frappe.get_doc("Customer", customer_name)
    _wof_ensure_subject_exists("Overall Coordinator")

    teacher_name = frappe.db.get_value(
        "Teacher",
        {"customer_reference": customer.name, "subject": "Overall Coordinator"},
    )

    if teacher_name:
        teacher = frappe.get_doc("Teacher", teacher_name)
    else:
        teacher = frappe.new_doc("Teacher")

    teacher.name1 = coordinator.get("name")
    teacher.phone_number = coordinator.get("mobile")
    teacher.email_id = coordinator.get("email")
    teacher.date_of_birth = "2000-01-01"
    teacher.subject = "Overall Coordinator"
    teacher.status = "Active"
    teacher.experience = "Experienced"
    teacher.customer_reference = customer.name

    if teacher.is_new():
        teacher.insert(ignore_permissions=True)
    else:
        teacher.save(ignore_permissions=True)

    existing_row = None
    for row in customer.custom_school_teacher_details:
        if row.subject == "Overall Coordinator":
            existing_row = row
            break

    if existing_row:
        existing_row.name1 = teacher.name
        existing_row.phone_number = teacher.phone_number
        existing_row.email_id = teacher.email_id
    else:
        customer.append("custom_school_teacher_details", {
            "name1": teacher.name,
            "phone_number": teacher.phone_number,
            "email_id": teacher.email_id,
            "subject": "Overall Coordinator",
            "status": teacher.status,
            "experience": teacher.experience,
        })

    customer.save(ignore_permissions=True)
    return teacher.name


def _wof_create_or_update_books_selection(customer_name, books_selection, is_submitted=False):
    books_name = frappe.db.get_value(
        "Books Selection",
        {"customer": customer_name, "is_submitted": 0},
    )

    if books_name:
        books_doc = frappe.get_doc("Books Selection", books_name)
        books_doc.select_books = []
    else:
        books_doc = frappe.new_doc("Books Selection")
        books_doc.customer = customer_name
        books_doc.order_date = frappe.utils.nowdate()

    for row in books_selection:
        if not (
            cint(row.get("practice_workbook_110"))
            or cint(row.get("student_guide_220"))
            or cint(row.get("prev_year_paper_160"))
        ):
            continue

        books_doc.append("select_books", {
            "subject": row.get("subject"),
            "class_grade": row.get("class_grade"),
            "practice_workbook_110": cint(row.get("practice_workbook_110")),
            "student_guide_220": cint(row.get("student_guide_220")),
            "prev_year_paper_160": cint(row.get("prev_year_paper_160")),
        })

    if is_submitted:
        books_doc.is_submitted = 1

    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            if books_doc.is_new():
                books_doc.insert(ignore_permissions=True)
            else:
                books_doc.save(ignore_permissions=True)
            break
        except frappe.DuplicateEntryError:
            if attempt == max_attempts - 1:
                raise
            books_doc.name = None
            frappe.db.rollback()

    return books_doc.name


@frappe.whitelist(allow_guest=True)
def save_wof_books_order(registration_data):
    """Save/update WOF book order — school info, contacts, coordinator, book selections."""
    try:
        data = (
            json.loads(registration_data)
            if isinstance(registration_data, str)
            else registration_data
        )

        frappe.log_error("WOF Books Order Data", json.dumps(data, indent=2))

        school_info = data.get("school_info", {})

        state = school_info.get("state", "")
        city = school_info.get("city", "")
        pincode = school_info.get("pincode", "")

        if not state:
            return {"success": False, "message": "State is required.", "field": "state"}
        if not city:
            return {"success": False, "message": "City is required.", "field": "city"}

        valid_state = frappe.db.exists("Address", {"state": state})
        if not valid_state:
            return {
                "success": False,
                "message": f"Invalid state '{state}'. Please select a valid state.",
                "field": "state",
            }

        valid_city = frappe.db.exists("Address", {"state": state, "city": city})
        if not valid_city:
            return {
                "success": False,
                "message": f"Invalid city '{city}' for state '{state}'.",
                "field": "city",
            }

        if pincode and not pincode.isdigit():
            return {"success": False, "message": "Pincode must contain only numbers.", "field": "pincode"}

        if not school_info.get("school_code") and school_info.get("ito_school_code"):
            school_info["school_code"] = school_info.get("ito_school_code")

        original_flag = frappe.flags.ignore_permissions
        frappe.flags.ignore_permissions = True

        try:
            customer = _wof_create_or_update_customer(school_info)
            _wof_create_or_update_address(customer, school_info)

            if school_info.get("primary_contact"):
                _wof_create_or_update_primary_contact(school_info.get("primary_contact"), customer)

            if school_info.get("olympiad_coordinator"):
                _wof_create_or_update_olympiad_coordinator(school_info.get("olympiad_coordinator"), customer)

            books_selection = data.get("books_selection", [])
            is_submitted = bool(data.get("submitted", False))

            if books_selection:
                _wof_create_or_update_books_selection(customer, books_selection, is_submitted=is_submitted)

            return {"success": True, "customer": customer, "message": "WOF books order saved successfully!"}

        finally:
            frappe.flags.ignore_permissions = original_flag

    except Exception as e:
        frappe.flags.ignore_permissions = False
        frappe.log_error(frappe.get_traceback(), "WOF Books Order Save Error")
        return {"success": False, "message": str(e)}


# ══════════════════════════════════════════════════════════════════════
# 2. GET WOF BOOK ORDER SUBJECTS
# ══════════════════════════════════════════════════════════════════════

@frappe.whitelist(allow_guest=True)
def get_wof_books_order_subjects():
    """Return practice-material subject configs for the logged-in WOF customer."""
    if frappe.session.user == "Guest":
        return []

    customer_name = frappe.db.get_value(
        "Portal User", {"user": frappe.session.user}, "parent"
    )
    if not customer_name:
        return []

    es_name = frappe.db.get_value("Exams Summary", {"customer": customer_name})
    if not es_name:
        return []

    es_doc = frappe.get_doc("Exams Summary", es_name)
    if not es_doc.exam_detail:
        return []

    yearly_exam = frappe.get_doc("Yearly Exam Date", es_doc.exam_detail)
    subjects_data = []

    for row in yearly_exam.target_dates:
        if not row.subject:
            continue
        if not frappe.db.exists("School Subject", row.subject):
            continue

        subject_doc = frappe.get_doc("School Subject", row.subject)

        subjects_data.append({
            "subject": subject_doc.name,
            "practice_workbook_110": [
                {"class": d.get("class"), "item": d.get("item"), "item_price": d.get("item_price")}
                for d in subject_doc.practice_workbook_110
            ],
            "student_guide_220": [
                {"class": d.get("class"), "item": d.get("item"), "item_price": d.get("item_price")}
                for d in subject_doc.student_guide_220
            ],
            "prev_year_paper_160": [
                {"class": d.get("class"), "item": d.get("item"), "item_price": d.get("item_price")}
                for d in subject_doc.prev_year_paper_160
            ],
        })

    frappe.log_error(
        title="WOF BOOK SUBJECTS",
        message=json.dumps(subjects_data, indent=2, default=str),
    )

    return subjects_data


# ══════════════════════════════════════════════════════════════════════
# 3. CREATE WOF SALES ORDER
# ══════════════════════════════════════════════════════════════════════

@frappe.whitelist(allow_guest=True)
def create_wof_sales_order(order_data, customer=None):
    """Create or update a draft Sales Order for WOF book items."""
    try:
        data = json.loads(order_data) if isinstance(order_data, str) else order_data

        session_customer = frappe.db.get_value(
            "Portal User", {"user": frappe.session.user}, "parent"
        )
        customer_name = session_customer or customer

        if session_customer and customer and session_customer != customer:
            frappe.throw(
                _("You are not allowed to create an order for this customer."),
                frappe.PermissionError,
            )

        if not customer_name or not frappe.db.exists("Customer", customer_name):
            return {"success": False, "message": "Customer not found", "alert": True}

        delivery_date = add_days(today(), 7)
        payment_company = _wof_get_razorpay_settings().company
        delivery_warehouse = _wof_get_delivery_warehouse(payment_company)

        valid_items = []

        original_ignore_permissions = frappe.flags.ignore_permissions
        frappe.flags.ignore_permissions = True

        try:
            for row in data:
                item_code = row.get("item")
                qty = flt(row.get("qty"))
                rate = flt(row.get("rate"))

                if item_code and qty > 0:
                    valid_items.append({
                        "item_code": item_code,
                        "item_name": frappe.db.get_value("Item", item_code, "item_name"),
                        "uom": frappe.db.get_value("Item", item_code, "stock_uom"),
                        "qty": qty,
                        "rate": rate,
                        "delivery_date": delivery_date,
                        "warehouse": delivery_warehouse,
                    })
                    frappe.share.add_docshare(
                        "Item", item_code,
                        user=frappe.session.user, read=1, write=1,
                        flags={"ignore_share_permission": True},
                    )

            if not valid_items:
                return {
                    "success": True,
                    "sales_order": None,
                    "message": "No sales order created. Please select items.",
                    "alert": True,
                }

            sales_order_name = frappe.db.get_value(
                "Sales Order",
                {"customer": customer_name, "company": payment_company, "docstatus": 0},
                "name",
                order_by="creation desc",
            )

            if sales_order_name:
                sales_order = frappe.get_doc("Sales Order", sales_order_name)
                sales_order.set("items", [])
                is_update = True
            else:
                sales_order = frappe.new_doc("Sales Order")
                sales_order.customer = customer_name
                sales_order.company = payment_company
                sales_order.currency = frappe.db.get_value("Customer", customer_name, "default_currency")
                sales_order.transaction_date = today()
                sales_order.delivery_date = delivery_date
                is_update = False

            for item in valid_items:
                sales_order.append("items", item)

            if sales_order.is_new():
                sales_order.save(ignore_permissions=True)
            else:
                sales_order.save(ignore_permissions=True)
        finally:
            frappe.flags.ignore_permissions = original_ignore_permissions

        return {
            "success": True,
            "sales_order": sales_order.name,
            "message": "Sales Order Updated" if is_update else "Sales Order Created",
        }

    except Exception as e:
        frappe.log_error(
            title="WOF Create Sales Order Error",
            message=frappe.get_traceback(),
        )
        return {"success": False, "sales_order": None, "message": str(e), "alert": True}


# ══════════════════════════════════════════════════════════════════════
# 4. INITIATE WOF BOOKS ORDER PAYMENT
# ══════════════════════════════════════════════════════════════════════

@frappe.whitelist(allow_guest=True)
def initiate_wof_books_order_payment(sales_order, customer=None, trans_item_json=None):
    """Initiate Razorpay payment for WOF book order without submitting the SO."""
    session_customer = frappe.db.get_value(
        "Portal User", {"user": frappe.session.user}, "parent"
    )
    customer = session_customer or customer

    if session_customer and customer and session_customer != customer:
        frappe.throw(_("You are not allowed to pay for this customer."), frappe.PermissionError)
    if not customer:
        frappe.throw(_("Please save School Information first."))

    try:
        trans_items = (
            json.loads(trans_item_json) if isinstance(trans_item_json, str) else trans_item_json
        ) if trans_item_json else None

        if not trans_items:
            frappe.throw(_("No items selected for payment."))

        total_amount = sum(flt(row.get("qty")) * flt(row.get("rate")) for row in trans_items)
        if total_amount <= 0:
            frappe.throw(_("Total amount must be greater than zero."))

        payment_company = _wof_get_razorpay_settings().company
        delivery_warehouse = _wof_get_delivery_warehouse(payment_company)
        delivery_date = add_days(today(), 7)

        sales_order_doc = frappe.get_doc("Sales Order", sales_order)
        if sales_order_doc.customer != customer:
            frappe.throw(_("You are not allowed to pay for this order."), frappe.PermissionError)
        if sales_order_doc.docstatus == 2:
            frappe.throw(_("This Sales Order has been cancelled."))

        # Check already paid
        existing_invoice = frappe.db.sql(
            """
            SELECT si.name, si.docstatus, si.outstanding_amount
            FROM `tabSales Invoice` si
            INNER JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
            WHERE sii.sales_order = %s AND si.docstatus = 1
            ORDER BY si.creation DESC LIMIT 1
            """,
            sales_order_doc.name,
            as_dict=True,
        )
        if existing_invoice and flt(existing_invoice[0].outstanding_amount) <= 0:
            payment_entry = frappe.db.get_value(
                "Payment Entry Reference",
                {"reference_doctype": "Sales Invoice", "reference_name": existing_invoice[0].name},
                "parent",
                order_by="creation desc",
            )
            return {
                "already_paid": True,
                "sales_invoice": existing_invoice[0].name,
                "payment_entry": payment_entry,
            }

        # Rebuild SO items
        sales_order_doc.set("items", [])
        for row in trans_items:
            item_code = row.get("item")
            qty = flt(row.get("qty"))
            rate = flt(row.get("rate"))
            if item_code and qty > 0:
                sales_order_doc.append("items", {
                    "item_code": item_code,
                    "qty": qty,
                    "rate": rate,
                    "delivery_date": delivery_date,
                    "warehouse": delivery_warehouse,
                })

        sales_order_doc.flags.ignore_permissions = True
        if sales_order_doc.is_new():
            sales_order_doc.insert(ignore_permissions=True)
        else:
            sales_order_doc.save(ignore_permissions=True)

        # Create Razorpay order
        settings = _wof_get_razorpay_settings()
        gateway_account = settings.get_payment_gateway_account()

        kwargs = {
            "amount": total_amount,
            "currency": settings.account_currency,
            "title": settings.company,
            "description": f"WOF Book Order against {sales_order_doc.name}",
            "reference_doctype": "Sales Order",
            "reference_docname": sales_order_doc.name,
            "payer_email": frappe.session.user,
            "payer_name": customer,
            "payment_gateway": settings.payment_gateway,
            "payment_gateway_account": gateway_account,
            "company": settings.company,
            "book_order_customer": customer,
            "book_order_items": json.dumps(trans_items),
            "book_order_warehouse": delivery_warehouse,
            "book_order_delivery_date": str(delivery_date),
            "redirect_to": "/portal/wof-books-order?payment=success",
        }

        checkout_url = settings.get_payment_url(**kwargs)
        token = parse_qs(urlparse(checkout_url).query).get("token", [None])[0]
        if not token:
            frappe.throw(_("Unable to start Razorpay checkout."))

        checkout_context = get_checkout_context(token)
        checkout_context["sales_order"] = sales_order_doc.name
        return checkout_context

    except Exception as e:
        error_msg = str(e)

        if "Amount exceeds maximum amount allowed" in error_msg:
            human_msg = _(
                "The payment amount exceeds Razorpay's maximum limit. "
                "Please reduce the quantity in your order and try again."
            )
        elif "Authentication failed" in error_msg:
            human_msg = _(
                "Payment gateway is temporarily unavailable. "
                "Please try again later or contact support."
            )
        else:
            human_msg = _("Payment initiation failed: {0}").format(error_msg)

        frappe.log_error(frappe.get_traceback(), "WOF Books Order Payment Initiation Error")
        return {"success": False, "message": human_msg, "error": error_msg}


# ══════════════════════════════════════════════════════════════════════
# 5. CONFIRM WOF BOOKS ORDER PAYMENT
# ══════════════════════════════════════════════════════════════════════

@frappe.whitelist(allow_guest=True)
def confirm_wof_books_order_payment(
    integration_request,
    razorpay_payment_id,
    razorpay_order_id,
    razorpay_signature,
):
    """
    Called after Razorpay checkout completes.
    1. Verify signature & capture payment
    2. Submit Sales Order
    3. Create & submit Sales Invoice
    4. Create & submit Payment Entry
    5. Update Razorpay Transaction
    """
    original_ignore_permissions = frappe.flags.ignore_permissions

    integration = None
    transaction = None
    payment_entry = None

    actual_pe_class = get_controller("Payment Entry")
    original_set_missing_ref_details = actual_pe_class.set_missing_ref_details

    def fake_set_missing_ref_details(self, *args, **kwargs):
        frappe.logger().info("Skipping set_missing_ref_details for Razorpay Payment Entry")
        return

    try:
        frappe.flags.ignore_permissions = True

        # Get Integration Request
        integration = frappe.get_doc("Integration Request", integration_request)
        data = frappe.parse_json(integration.data or "{}")

        # Get Razorpay Transaction
        transaction_name = data.get("razorpay_transaction")
        if not transaction_name:
            frappe.throw(_("Razorpay Transaction is missing from Integration Request."))

        transaction = frappe.get_doc("Razorpay Transaction", transaction_name)
        settings = frappe.get_doc("Multi Company Razorpay Settings", transaction.settings)

        # Verify Order ID
        if razorpay_order_id != transaction.razorpay_order_id:
            frappe.throw(_("Razorpay Order ID mismatch."), frappe.PermissionError)

        # Verify Signature
        settings.verify_checkout_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature)

        transaction.db_set({
            "razorpay_payment_id": razorpay_payment_id,
            "razorpay_signature": razorpay_signature,
            "status": "Authorized",
        })

        integration.update_status(
            {
                "razorpay_payment_id": razorpay_payment_id,
                "razorpay_order_id": razorpay_order_id,
                "razorpay_signature": razorpay_signature,
            },
            "Authorized",
        )

        # Fetch & Capture Payment
        payment = settings.fetch_payment(razorpay_payment_id)

        if payment.get("status") == "authorized":
            try:
                payment = settings.capture_payment(
                    payment.get("id"), transaction.amount_in_smallest_unit
                )
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    f"Razorpay capture failed for {transaction.name}",
                )
                return {"paid": False, "message": _("Payment was authorized but could not be captured.")}

        if payment.get("status") != "captured":
            return {
                "paid": False,
                "message": _("Payment status is '{0}', not captured.").format(payment.get("status")),
            }

        # Get Sales Order
        sales_order_name = data.get("reference_docname")
        if not sales_order_name:
            frappe.throw(_("Sales Order reference is missing."))
        if not frappe.db.exists("Sales Order", sales_order_name):
            frappe.throw(_("Sales Order {0} not found.").format(sales_order_name))

        so_doc = frappe.get_doc("Sales Order", sales_order_name)

        # Rebuild SO items
        items_json = data.get("book_order_items")
        if items_json:
            trans_items = json.loads(items_json) if isinstance(items_json, str) else items_json
            delivery_warehouse = data.get("book_order_warehouse") or _wof_get_delivery_warehouse(settings.company)
            delivery_date = data.get("book_order_delivery_date") or str(add_days(today(), 7))

            so_doc.set("items", [])
            for row in trans_items:
                item_code = row.get("item")
                qty = flt(row.get("qty"))
                rate = flt(row.get("rate"))
                if item_code and qty > 0:
                    so_doc.append("items", {
                        "item_code": item_code,
                        "qty": qty,
                        "rate": rate,
                        "delivery_date": delivery_date,
                        "warehouse": delivery_warehouse,
                    })

            so_doc.flags.ignore_permissions = True
            so_doc.save(ignore_permissions=True)

        # Submit SO
        if so_doc.docstatus == 0:
            so_doc.flags.ignore_permissions = True
            so_doc.submit()

        # Create & Submit Sales Invoice
        from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

        sales_invoice = make_sales_invoice(so_doc.name, ignore_permissions=True)
        sales_invoice.set_posting_time = 1
        sales_invoice.flags.ignore_permissions = True
        sales_invoice.set("taxes", [])
        sales_invoice.tax_category = None
        sales_invoice.calculate_taxes_and_totals()
        sales_invoice.total_taxes_and_charges = 0
        sales_invoice.base_total_taxes_and_charges = 0
        sales_invoice.grand_total = sales_invoice.net_total
        sales_invoice.base_grand_total = sales_invoice.base_net_total

        if sales_invoice.disable_rounded_total:
            sales_invoice.rounded_total = sales_invoice.grand_total
            sales_invoice.base_rounded_total = sales_invoice.base_grand_total

        sales_invoice.set_posting_time = 1
        sales_invoice.flags.ignore_permissions = True
        sales_invoice.insert(ignore_permissions=True)
        sales_invoice.submit()

        # Create Payment Entry
        actual_pe_class.set_missing_ref_details = fake_set_missing_ref_details

        from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

        payment_entry = get_payment_entry(
            "Sales Invoice",
            sales_invoice.name,
            party_amount=transaction.amount,
            bank_account=settings.payment_account,
        )
        payment_entry.reference_no = razorpay_payment_id or razorpay_order_id
        payment_entry.reference_date = today()
        payment_entry.remarks = (
            f"WOF Book Order Payment for {so_doc.name} via Razorpay ({razorpay_order_id})"
        )
        payment_entry.flags.ignore_permissions = True

        if payment_entry.payment_type in ("Receive", "Pay"):
            payment_entry.title = payment_entry.party
        else:
            payment_entry.title = payment_entry.paid_from + " - " + payment_entry.paid_to

        payment_entry.insert(ignore_permissions=True)

        # Share documents
        frappe.share.add_docshare(
            "Payment Entry", payment_entry.name,
            user=frappe.session.user, read=1,
            flags={"ignore_share_permission": True},
        )
        frappe.share.add_docshare(
            "Sales Invoice", sales_invoice.name,
            user=frappe.session.user, read=1,
            flags={"ignore_share_permission": True},
        )

        assign_to.add({
            "assign_to": [frappe.session.user],
            "doctype": "Payment Entry",
            "name": payment_entry.name,
            "description": "Share Payment Entry with user for reference",
        })
        assign_to.add({
            "assign_to": [frappe.session.user],
            "doctype": "Sales Invoice",
            "name": sales_invoice.name,
            "description": "Share Sales Invoice with user for reference",
        })

        # Submit PE
        frappe.flags.ignore_permissions = True
        payment_entry.submit()

        # Update Transaction
        transaction.db_set({
            "status": "Completed",
            "sales_invoice": sales_invoice.name,
            "reference_doctype": "Sales Invoice",
            "reference_docname": sales_invoice.name,
            "payment_entry": payment_entry.name,
            "gateway_response": json.dumps(payment, default=str),
            "processed_on": frappe.utils.now_datetime(),
        })

        frappe.db.set_value("Integration Request", integration.name, "status", "Completed")

        return {
            "paid": True,
            "sales_invoice": sales_invoice.name,
            "payment_entry": payment_entry.name,
            "sales_order": so_doc.name,
            "redirect_to": "/portal/wof-books-order?payment=success",
        }

    except Exception as e:
        error_message = str(e)
        frappe.log_error(frappe.get_traceback(), "WOF Book Order Payment Failed")

        if transaction:
            try:
                transaction.db_set({
                    "status": "Failed",
                    "gateway_response": json.dumps(
                        {"error": error_message, "payment_id": razorpay_payment_id, "order_id": razorpay_order_id},
                        default=str,
                    ),
                })
            except Exception:
                frappe.log_error(frappe.get_traceback(), "Failed to update WOF Razorpay Transaction status")

        if integration:
            try:
                frappe.db.set_value("Integration Request", integration.name, "status", "Failed")
                if frappe.get_meta("Integration Request").has_field("error"):
                    frappe.db.set_value("Integration Request", integration.name, "error", error_message)
            except Exception:
                frappe.log_error(frappe.get_traceback(), "Failed to update WOF Integration Request status")

        frappe.db.rollback()

        return {
            "paid": False,
            "success": False,
            "message": _("Payment processing failed. Please contact support."),
            "error": error_message,
        }

    finally:
        actual_pe_class.set_missing_ref_details = original_set_missing_ref_details
        frappe.flags.ignore_permissions = original_ignore_permissions

        if payment_entry:
            try:
                assign_to.set_status(
                    "Payment Entry", payment_entry.name,
                    assign_to=frappe.session.user, status="Cancelled",
                    ignore_permissions=True,
                )
            except Exception:
                pass

        if "sales_invoice" in locals():
            try:
                assign_to.set_status(
                    "Sales Invoice", sales_invoice.name,
                    assign_to=frappe.session.user, status="Cancelled",
                    ignore_permissions=True,
                )
            except Exception:
                pass

        if payment_entry:
            try:
                frappe.db.delete("DocShare", {
                    "user": frappe.session.user,
                    "share_name": payment_entry.name,
                    "share_doctype": "Payment Entry",
                })
            except Exception:
                pass

        if "sales_invoice" in locals():
            try:
                frappe.db.delete("DocShare", {
                    "user": frappe.session.user,
                    "share_name": sales_invoice.name,
                    "share_doctype": "Sales Invoice",
                })
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════
# 6. GET WOF BOOKS ORDER PAYMENT STATUS
# ══════════════════════════════════════════════════════════════════════

@frappe.whitelist(allow_guest=True)
def get_wof_books_order_payment_status(sales_order=None):
    """Check if the WOF book order has been paid."""
    if not sales_order:
        session_customer = frappe.db.get_value(
            "Portal User", {"user": frappe.session.user}, "parent"
        )
        if not session_customer:
            return {"paid": False}

        sales_order_doc = frappe.db.sql(
            """
            SELECT so.name
            FROM `tabSales Order` so
            WHERE so.customer = %s
            ORDER BY so.creation DESC LIMIT 1
            """,
            session_customer,
            as_dict=True,
        )
        if not sales_order_doc:
            return {"paid": False}

        sales_order = sales_order_doc[0].name

    existing_invoice = frappe.db.sql(
        """
        SELECT si.name, si.docstatus, si.outstanding_amount
        FROM `tabSales Invoice` si
        INNER JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
        WHERE sii.sales_order = %s AND si.docstatus = 1
        ORDER BY si.creation DESC LIMIT 1
        """,
        sales_order,
        as_dict=True,
    )

    if existing_invoice:
        invoice = existing_invoice[0]
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
