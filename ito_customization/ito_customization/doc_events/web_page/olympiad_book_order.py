import frappe
from frappe.utils import flt
import json
import secrets
from frappe.utils import getdate, nowdate, add_days, today
from frappe.utils import cint

def ensure_subject_exists(subject_name):
    """Create a School Subject if it doesn't exist"""
    if not frappe.db.exists("School Subject", subject_name):
        subject = frappe.new_doc("School Subject")
        subject.name = subject_name
        try:
            subject.insert(ignore_permissions=True)
        except frappe.DuplicateEntryError:
            pass  # Subject was created by another process




@frappe.whitelist(allow_guest=True)
def save_books_order(registration_data):
    try:
        data = (
            json.loads(registration_data)
            if isinstance(registration_data, str)
            else registration_data
        )

        frappe.log_error(
            "Books Order Data",
            json.dumps(data, indent=2)
        )

        school_info = data.get("school_info", {})

        # Validate state and city before saving
        state = school_info.get("state", "")
        city = school_info.get("city", "")
        pincode = school_info.get("pincode", "")

        if not state:
            return {
                "success": False,
                "message": "State is required. Please select from the dropdown.",
                "field": "state"
            }

        if not city:
            return {
                "success": False,
                "message": "City is required. Please select from the dropdown.",
                "field": "city"
            }

        # Validate state exists in Address doctype
        valid_state = frappe.db.exists("Address", {"state": state})
        if not valid_state:
            return {
                "success": False,
                "message": f"Invalid state '{state}'. Please select a valid state from the dropdown.",
                "field": "state"
            }

        # Validate city exists for this state
        valid_city = frappe.db.exists("Address", {"state": state, "city": city})
        if not valid_city:
            return {
                "success": False,
                "message": f"Invalid city '{city}' for state '{state}'. Please select a valid city from the dropdown.",
                "field": "city"
            }

        # Validate pincode format (optional)
        if pincode and not pincode.isdigit():
            return {
                "success": False,
                "message": "Pincode must contain only numbers.",
                "field": "pincode"
            }

        # FIX: Handle both 'school_code' and 'ito_school_code' from frontend
        if not school_info.get("school_code") and school_info.get("ito_school_code"):
            school_info["school_code"] = school_info.get("ito_school_code")

        original_flag = frappe.flags.ignore_permissions
        frappe.flags.ignore_permissions = True

        try:
            customer = create_or_update_customer_books_order(school_info)

            create_or_update_address(customer, school_info)

            if school_info.get("primary_contact"):
                create_or_update_primary_contact(
                    school_info.get("primary_contact"),
                    customer
                )

            if school_info.get("olympiad_coordinator"):
                create_or_update_olympiad_coordinator(
                    school_info.get("olympiad_coordinator"),
                    customer
                )

            books_selection = data.get("books_selection", [])
            is_submitted = bool(data.get("submitted", False))

            if books_selection:
                create_or_update_books_selection(customer, books_selection, is_submitted=is_submitted)

            # NO explicit commit - let Frappe handle it
            return {
                "success": True,
                "customer": customer,
                "message": "Books order saved successfully!"
            }

        finally:
            frappe.flags.ignore_permissions = original_flag

    except Exception as e:
        # NO explicit rollback - let Frappe handle exceptions
        frappe.flags.ignore_permissions = False
        frappe.log_error(
            frappe.get_traceback(),
            "Books Order Save Error"
        )
        return {
            "success": False,
            "message": str(e)
        }



def create_or_update_books_selection(
    customer_name,
    books_selection,
    is_submitted=False
):
    books_name = frappe.db.get_value(
        "Books Selection",
        {
            "customer": customer_name
        }
    )

    if books_name:
        books_doc = frappe.get_doc(
            "Books Selection",
            books_name
        )
        books_doc.select_books = []
    else:
        books_doc = frappe.new_doc(
            "Books Selection"
        )
        books_doc.customer = (
            customer_name
        )

    if not books_doc.order_date:
        books_doc.order_date = frappe.utils.nowdate()

    for row in books_selection:
        if not (
            cint(
                row.get(
                    "practice_workbook_110"
                )
            )
            or
            cint(
                row.get(
                    "student_guide_220"
                )
            )
            or
            cint(
                row.get(
                    "prev_year_paper_160"
                )
            )
        ):
            continue

        books_doc.append(
            "select_books",
            {
                "subject":
                    row.get("subject"),
                "class_grade":
                    row.get("class_grade"),
                "practice_workbook_110":
                    cint(
                        row.get(
                            "practice_workbook_110"
                        )
                    ),
                "student_guide_220":
                    cint(
                        row.get(
                            "student_guide_220"
                        )
                    ),
                "prev_year_paper_160":
                    cint(
                        row.get(
                            "prev_year_paper_160"
                        )
                    )
            }
        )

    if is_submitted:
        books_doc.is_submitted = 1

    if books_doc.is_new():
        books_doc.insert(
            ignore_permissions=True
        )
    else:
        books_doc.save(
            ignore_permissions=True
        )

    return books_doc.name


def create_or_update_customer_books_order(school_info):
    school_name = school_info.get("school_name")

    if not school_name:
        frappe.throw("School Name is mandatory")

    existing = frappe.db.exists(
        "Customer",
        {"customer_name": school_name}
    )

    if existing:
        customer = frappe.get_doc(
            "Customer",
            existing
        )
    else:
        customer = frappe.new_doc("Customer")
        customer.customer_name = school_name

    customer.customer_type = "Company"

    # Existing ITO School Code
    if hasattr(customer, "custom_ito_school_code"):
        customer.custom_ito_school_code = school_info.get(
            "ito_school_code"
        )

    # Contact Details
    if hasattr(customer, "email_id"):
        customer.email_id = school_info.get(
            "contact_email"
        )

    if hasattr(customer, "mobile_no"):
        customer.mobile_no = school_info.get(
            "contact_mobile"
        )

    # Location Details
    if hasattr(customer, "custom_taluka"):
        customer.custom_taluka = school_info.get(
            "taluka"
        )

    if hasattr(customer, "custom_district"):
        customer.custom_district = school_info.get(
            "district"
        )

    if hasattr(customer, "custom_customer_category"):
        customer.custom_customer_category = (
            "School Customer"
        )

    if customer.is_new():
        customer.insert(
            ignore_permissions=True
        )
    else:
        customer.save(
            ignore_permissions=True
        )

    return customer.name


def create_or_update_address(customer_name, school_info):
    address_name = frappe.db.exists("Address", {"address_title": customer_name})
    
    if address_name:
        address = frappe.get_doc("Address", address_name)
    else:
        address = frappe.new_doc("Address")
        address.address_title = customer_name
        address.address_type = "Billing"
        address.append("links", {
            "link_doctype": "Customer",
            "link_name": customer_name
        })

    address.address_line1 = school_info.get("school_address", "")
    address.city = school_info.get("city", "")
    address.state = school_info.get("state", "")  # Now validated - will be "Tamil Nadu" not "Tamilnadu"
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


def create_or_update_primary_contact(
    primary_contact,
    customer_name
):
    if not primary_contact.get("name"):
        return

    contact_name = frappe.db.exists(
        "Contact",
        {
            "first_name": primary_contact.get("name")
        }
    )

    if contact_name:
        contact = frappe.get_doc(
            "Contact",
            contact_name
        )
    else:
        contact = frappe.new_doc(
            "Contact"
        )

    contact.first_name = primary_contact.get(
        "name"
    )

    contact.designation = (
        "Primary Contact"
    )

    contact.email_ids = []
    contact.phone_nos = []

    if primary_contact.get("email"):
        contact.append(
            "email_ids",
            {
                "email_id": primary_contact.get(
                    "email"
                ),
                "is_primary": 1
            }
        )

    if primary_contact.get("mobile"):
        contact.append(
            "phone_nos",
            {
                "phone": primary_contact.get(
                    "mobile"
                ),
                "is_primary_mobile_no": 1
            }
        )

    if not contact.links:
        contact.append(
            "links",
            {
                "link_doctype": "Customer",
                "link_name": customer_name
            }
        )

    if contact.is_new():
        contact.insert(
            ignore_permissions=True
        )
    else:
        contact.save(
            ignore_permissions=True
        )

    # ----------------------------------
    # Update Customer Primary Contact
    # ----------------------------------

    customer = frappe.get_doc(
        "Customer",
        customer_name
    )

    customer.customer_primary_contact = (
        contact.name
    )

    if hasattr(customer, "mobile_no"):
        customer.mobile_no = primary_contact.get(
            "mobile"
        )

    if hasattr(customer, "email_id"):
        customer.email_id = primary_contact.get(
            "email"
        )

    customer.save(
        ignore_permissions=True
    )

    return contact.name


def create_or_update_olympiad_coordinator(
    coordinator,
    customer_name
):
    if not coordinator.get("name"):
        return

    customer = frappe.get_doc(
        "Customer",
        customer_name
    )

    ensure_subject_exists(
        "Overall Coordinator"
    )

    # ----------------------------------
    # Find Existing Coordinator
    # ----------------------------------

    teacher_name = frappe.db.get_value(
        "Teacher",
        {
            "customer_reference":
                customer.name,
            "subject":
                "Overall Coordinator"
        }
    )

    if teacher_name:
        teacher = frappe.get_doc(
            "Teacher",
            teacher_name
        )
    else:
        teacher = frappe.new_doc(
            "Teacher"
        )

    # ----------------------------------
    # Teacher Master
    # ----------------------------------

    teacher.name1 = coordinator.get(
        "name"
    )

    teacher.phone_number = (
        coordinator.get("mobile")
    )

    teacher.email_id = (
        coordinator.get("email")
    )

    teacher.date_of_birth = "2000-01-01"

    teacher.subject = (
        "Overall Coordinator"
    )

    teacher.status = "Active"

    teacher.experience = (
        "Experienced"
    )

    teacher.customer_reference = (
        customer.name
    )

    if teacher.is_new():
        teacher.insert(
            ignore_permissions=True
        )
    else:
        teacher.save(
            ignore_permissions=True
        )

    # ----------------------------------
    # Customer Child Table Sync
    # ----------------------------------

    existing_row = None

    for row in customer.custom_school_teacher_details:
        if (
            row.subject ==
            "Overall Coordinator"
        ):
            existing_row = row
            break

    if existing_row:
        existing_row.name1 = (
            teacher.name
        )

        existing_row.phone_number = (
            teacher.phone_number
        )

        existing_row.email_id = (
            teacher.email_id
        )
    else:
        customer.append(
            "custom_school_teacher_details",
            {
                "name1":
                    teacher.name,
                "phone_number":
                    teacher.phone_number,
                "email_id":
                    teacher.email_id,
                "subject":
                    "Overall Coordinator",
                "status":
                    teacher.status,
                "experience":
                    teacher.experience
            }
        )

    customer.save(
        ignore_permissions=True
    )

    return teacher.name


@frappe.whitelist(allow_guest=True)
def get_books_order_subjects():
    if frappe.session.user == "Guest":
        return []

    customer_name = frappe.db.get_value(
        "Portal User",
        {
            "user": frappe.session.user
        },
        "parent"
    )

    if not customer_name:
        return []

    es_name = frappe.db.get_value(
        "Exams Summary",
        {
            "customer": customer_name
        }
    )

    if not es_name:
        return []

    es_doc = frappe.get_doc(
        "Exams Summary",
        es_name
    )

    if not es_doc.exam_detail:
        return []

    yearly_exam = frappe.get_doc(
        "Yearly Exam Date",
        es_doc.exam_detail
    )

    subjects_data = []

    for row in yearly_exam.target_dates:
        if not row.subject:
            continue

        if not frappe.db.exists(
            "School Subject",
            row.subject
        ):
            continue

        subject_doc = frappe.get_doc(
            "School Subject",
            row.subject
        )

        subjects_data.append({
            "subject": subject_doc.name,
            "practice_workbook_110": [
                {
                    "class": d.get("class"),
                    "item": d.get("item"),
                    "item_price" : d.get("item_price")
                }
                for d in subject_doc.practice_workbook_110
            ],
            "student_guide_220": [
                {
                    "class": d.get("class"),
                    "item": d.get("item"),
                    "item_price" : d.get("item_price")
                }
                for d in subject_doc.student_guide_220
            ],
            "prev_year_paper_160": [
                {
                    "class": d.get("class"),
                    "item": d.get("item"),
                    "item_price" : d.get("item_price")
                }
                for d in subject_doc.prev_year_paper_160
            ]
        })

    frappe.log_error(
        title="BOOK SUBJECTS",
        message=json.dumps(
            subjects_data,
            indent=2,
            default=str
        )
    )

    return subjects_data


def get_customer_from_user():
    user = frappe.session.user

    customer = frappe.db.get_value(
        "Customer",
        {
            "email_id": user
        }
    )

    return customer


@frappe.whitelist()
def create_quotation_from_books_order(order_data):
    data = (
        json.loads(order_data)
        if isinstance(order_data, str)
        else order_data
    )

    customer_name = frappe.db.get_value(
        "Portal User",
        {"user": frappe.session.user},
        "parent"
    )

    if not customer_name:
        return {
            "success": False,
            "message": "Customer not found",
            "alert": True
        }

    # -----------------------------------
    # Filter valid items
    # -----------------------------------

    valid_items = []
    for row in data:
        item_code = row.get("item")
        qty = flt(row.get("qty"))
        rate = flt(row.get("rate"))

        if item_code and qty > 0:
            valid_items.append({
                "item_code": item_code,
                "qty": qty,
                "rate": rate
            })

    # -----------------------------------
    # No items selected — return friendly message
    # -----------------------------------

    if not valid_items:
        return {
            "success": True,
            "quotation": None,
            "message": "No quotation created. Please select items if you want to create a quotation.",
            "alert": True
        }

    # -----------------------------------
    # Find existing Draft Quotation
    # -----------------------------------
    # FIX: Search by party_name first (actual link field), fallback to customer_name

    quotation_name = frappe.db.get_value(
        "Quotation",
        {
            "party_name": customer_name,
            "docstatus": 0
        },
        "name",
        order_by="creation desc"
    )

    # Fallback: try customer_name for backwards compatibility
    if not quotation_name:
        quotation_name = frappe.db.get_value(
            "Quotation",
            {
                "customer_name": customer_name,
                "docstatus": 0
            },
            "name",
            order_by="creation desc"
        )

    # -----------------------------------
    # Update Existing or Create New
    # -----------------------------------

    if quotation_name:
        quotation = frappe.get_doc("Quotation", quotation_name)
        quotation.set("items", [])
        is_update = True
    else:
        quotation = frappe.new_doc("Quotation")
        quotation.quotation_to = "Customer"
        quotation.party_name = customer_name
        quotation.customer_name = customer_name
        is_update = False

    # -----------------------------------
    # Append Items
    # -----------------------------------

    for item in valid_items:
        quotation.append("items", item)

    # -----------------------------------
    # Save
    # -----------------------------------

    if quotation.is_new():
        quotation.insert(ignore_permissions=True)
    else:
        quotation.save(ignore_permissions=True)

    # NO explicit commit - let Frappe handle it

    return {
        "success": True,
        "quotation": quotation.name,
        "message": "Quotation Updated" if is_update else "Quotation Created"
    }

import re
import secrets
from frappe.utils import formatdate, getdate, nowdate

LITTLE_CHAMP_CLASS_ORDER = ["Nursery", "Junior", "Senior"]


@frappe.whitelist()
def generate_consent_token(customer):
    token = secrets.token_urlsafe(16)
    base_url = frappe.utils.get_url()
    full_url = f'{base_url}/parent-consent?token={token}'

    frappe.db.set_value('Customer', customer, {
        'custom_consent_token': token,
        'custom_url': full_url
    })

    return {'token': token, 'url': full_url}

from urllib.parse import parse_qs, urlparse
from multi_company_razorpay.api import (
    checkout_success,
    create_payment_for_sales_invoice,
    get_checkout_context,
    get_settings_for_page,
)

PAGE = "Book Order"
BOOKS_ORDER_INVOICE_REMARK = "Books Order Portal"


def _get_valid_books_invoice_items(order_data):
    data = json.loads(order_data) if isinstance(order_data, str) else order_data
    valid_items = []

    for row in data or []:
        item_code = row.get("item")
        qty = flt(row.get("qty"))
        rate = flt(row.get("rate"))

        if item_code and qty > 0:
            valid_items.append({
                "item_code": item_code,
                "qty": qty,
                "rate": rate,
            })

    return valid_items


def _normalize_books_invoice_items(items):
    return sorted(
        (row.get("item_code"), flt(row.get("qty")), flt(row.get("rate")))
        for row in items
    )


def _invoice_matches_books_order(invoice_name, valid_items):
    invoice_items = frappe.get_all(
        "Sales Invoice Item",
        filters={"parent": invoice_name},
        fields=["item_code", "qty", "rate"],
        order_by="idx asc",
    )
    return _normalize_books_invoice_items(invoice_items) == _normalize_books_invoice_items(valid_items)


def _find_existing_books_order_invoice(customer, company):
    invoices = frappe.get_all(
        "Sales Invoice",
        filters={
            "customer": customer,
            "company": company,
            "remarks": BOOKS_ORDER_INVOICE_REMARK,
            "docstatus": ["!=", 2],
        },
        fields=["name", "docstatus", "outstanding_amount", "grand_total"],
        order_by="creation desc",
    )
    return invoices[0] if invoices else None


def _get_or_create_books_order_invoice(order_data, customer_name):
    valid_items = _get_valid_books_invoice_items(order_data)
    if not valid_items:
        frappe.throw("Please select items before proceeding to payment")

    company = get_settings_for_page(PAGE).company
    existing = _find_existing_books_order_invoice(customer_name, company)

    # ═══════════════════════════════════════════════════════════════
    # FIXED: Fully paid invoice → create a fresh one instead of throwing
    # ═══════════════════════════════════════════════════════════════
    if existing and existing.docstatus == 1 and flt(existing.outstanding_amount) <= 0:
        existing = None

    if existing and existing.docstatus == 1 and not _invoice_matches_books_order(existing.name, valid_items):
        if flt(existing.outstanding_amount) < flt(existing.grand_total):
            frappe.throw("An earlier invoice for this book order is partially paid. Please contact support.")
            
        inv = frappe.get_doc("Sales Invoice", existing.name); 
        if inv.status != "Paid":
            inv.flags.ignore_permissions = True
            inv.cancel()
        existing = None

    if existing and existing.docstatus == 0:
        frappe.delete_doc("Sales Invoice", existing.name, ignore_permissions=True)
        existing = None

    if existing and existing.docstatus == 1:
        return existing.name, flt(existing.outstanding_amount)

    invoice = frappe.get_doc(
        {
            "doctype": "Sales Invoice",
            "customer": customer_name,
            "company": company,
            "remarks": BOOKS_ORDER_INVOICE_REMARK,
            "items": valid_items,
        }
    )
    invoice.insert(ignore_permissions=True)
    # invoice.flags.ignore_permissions = True; invoice.submit()
    return invoice.name, flt(invoice.outstanding_amount)


@frappe.whitelist(allow_guest=True)
def initiate_books_order_payment(order_data):
    session_customer = frappe.db.get_value(
        "Portal User", {"user": frappe.session.user}, "parent"
    )
    if not session_customer:
        frappe.throw("Please save School Information first.")

    original_ignore_permissions = frappe.flags.ignore_permissions
    try:
        frappe.flags.ignore_permissions = True

        invoice_name, pay_amount = _get_or_create_books_order_invoice(
            order_data, session_customer
        )
        result = create_payment_for_sales_invoice(
            sales_invoice=invoice_name,
            amount=pay_amount,
        )
    finally:
        frappe.flags.ignore_permissions = original_ignore_permissions

    checkout_token = parse_qs(urlparse(result["checkout_url"]).query).get("token", [None])[0]
    if not checkout_token:
        frappe.throw("Unable to start Razorpay checkout.")

    checkout_context = get_checkout_context(checkout_token)
    checkout_context["sales_invoice"] = invoice_name
    return checkout_context

@frappe.whitelist(allow_guest=True)
def confirm_books_order_payment(
    integration_request, razorpay_payment_id, razorpay_order_id, razorpay_signature
):
    original_ignore_permissions = frappe.flags.ignore_permissions
    try:
        frappe.flags.ignore_permissions = True

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
    finally:
        frappe.flags.ignore_permissions = original_ignore_permissions


@frappe.whitelist(allow_guest=True)
def get_books_order_payment_status():
    """Check if the current session customer's books order invoice has been paid."""
    session_customer = frappe.db.get_value(
        "Portal User", {"user": frappe.session.user}, "parent"
    )
    if not session_customer:
        return {"paid": False}

    # Build filters — company is optional (fall back gracefully if settings missing)
    filters = {
        "customer": session_customer,
        "remarks": BOOKS_ORDER_INVOICE_REMARK,
        "docstatus": 1,
    }
    try:
        company = get_settings_for_page(PAGE).company
        if company:
            filters["company"] = company
    except Exception:
        pass  # No Razorpay page settings configured — search across all companies

    invoices = frappe.get_all(
        "Sales Invoice",
        filters=filters,
        fields=["name", "outstanding_amount"],
        order_by="creation desc",
        limit=1,
    )

    if not invoices:
        return {"paid": False}

    invoice = invoices[0]
    if flt(invoice.outstanding_amount) > 0:
        return {"paid": False, "sales_invoice": invoice.name}

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

@frappe.whitelist(allow_guest=True)
def get_school_by_token(token):
    result = frappe.db.get_value(
        'Customer',
        {'custom_consent_token': token},
        ['name', 'customer_name', 'custom_concern_form_last_date', 'custom_is_little_champ']
    )
    if not result:
        frappe.throw('Invalid or expired link')

    customer, school_name, expiry_date, is_little_champ = result

    if expiry_date and getdate(nowdate()) > getdate(expiry_date):
        frappe.throw('This link has expired. Please contact the school for a new link.')

    return {
        'customer': customer,
        'school_name': school_name,
        'is_little_champ': bool(is_little_champ),
    }


def extract_class_number(value):
    """Extract a numeric class from strings like 'Class 12' -> 12"""
    if not value:
        return None
    match = re.search(r'(\d+)', str(value))
    return int(match.group(1)) if match else None

@frappe.whitelist(allow_guest=True)
def get_class_options(token, is_little_champ=0):
    """Returns the ordered list of Class names actually configured
    for this school's assigned subjects."""
    is_little_champ = int(is_little_champ)

    customer = frappe.db.get_value('Customer', {'custom_consent_token': token}, 'name')
    if not customer:
        frappe.throw('Invalid or expired link')

    es_name = frappe.db.get_value('Exams Summary', {'customer': customer})
    if not es_name:
        return {'classes': []}

    es_doc = frappe.get_doc('Exams Summary', es_name)
    if not es_doc.exam_detail:
        return {'classes': []}

    yearly_exam = frappe.get_doc('Yearly Exam Date', es_doc.exam_detail)

    found = set()

    for row in yearly_exam.target_dates:
        if not row.subject or not frappe.db.exists('School Subject', row.subject):
            continue

        subject_doc = frappe.get_doc('School Subject', row.subject)

        if bool(subject_doc.little_champ) != bool(is_little_champ):
            continue

        if is_little_champ:
            child_tables = [subject_doc.text_book, subject_doc.work_book]
            for table in child_tables:
                for d in table:
                    if d.get('class'):
                        found.add(str(d.get('class')))
        else:
            child_tables = [
                subject_doc.practice_workbook_110,
                subject_doc.student_guide_220,
                subject_doc.prev_year_paper_160,
            ]
            for table in child_tables:
                for d in table:
                    cls = extract_class_number(d.get('class'))
                    if cls is not None:
                        found.add(str(cls))

    names = list(found)

    if is_little_champ:
        names.sort(
            key=lambda n: LITTLE_CHAMP_CLASS_ORDER.index(n)
            if n in LITTLE_CHAMP_CLASS_ORDER else len(LITTLE_CHAMP_CLASS_ORDER)
        )
    else:
        names.sort(key=lambda n: extract_class_number(n) or 0)

    return {'classes': names}


@frappe.whitelist(allow_guest=True)
def get_dynamic_subjects(school_name, selected_class=None):
    """Returns fully dynamic subject config for the Exams & Books table.
    Branches on the Customer's custom_is_little_champ flag:
      - Normal: Exam registration + Workbook/Guide/Past Papers, priced by class number.
      - Little Champ: Text Book + Work Book only, no exam, priced by exact class name.
    """

    customer_row = frappe.db.get_value(
        'Customer',
        {'customer_name': school_name},
        ['name', 'custom_is_little_champ'],
        as_dict=True
    )

    if not customer_row:
        return {'subjects': [], 'found_customer': False}

    customer = customer_row.name
    is_little_champ = bool(customer_row.custom_is_little_champ)

    es_name = frappe.db.get_value('Exams Summary', {'customer': customer})

    if not es_name:
        return {'subjects': [], 'found_customer': True, 'is_little_champ': is_little_champ}

    es_doc = frappe.get_doc('Exams Summary', es_name)

    if not es_doc.exam_detail:
        return {'subjects': [], 'found_customer': True, 'is_little_champ': is_little_champ}

    yearly_exam = frappe.get_doc('Yearly Exam Date', es_doc.exam_detail)

    selected_class_num = extract_class_number(selected_class) if selected_class else None
    subjects = []

    def find_item_by_number(items):
        if selected_class_num is None:
            return None
        for d in items:
            if extract_class_number(d.get('class')) == selected_class_num:
                return d
        return None

    def find_item_by_exact_class(items):
        if not selected_class:
            return None
        for d in items:
            if str(d.get('class')) == str(selected_class):
                return d
        return None

    for row in yearly_exam.target_dates:
        if not row.subject:
            continue

        if not frappe.db.exists('School Subject', row.subject):
            continue

        subject_doc = frappe.get_doc('School Subject', row.subject)

        # Only show subjects matching the customer's category
        if bool(subject_doc.little_champ) != is_little_champ:
            continue

        match = re.search(r'\(([^)]+)\)', subject_doc.name)
        code = match.group(1) if match else subject_doc.name

        if is_little_champ:
            tb_item = find_item_by_exact_class(subject_doc.text_book)
            wb_item = find_item_by_exact_class(subject_doc.work_book)

            subjects.append({
                'code': code,
                'name': subject_doc.name,
                'exam_available': False,
                'exam_fee': 0,
                'tb': {
                    'available': bool(tb_item),
                    'price': tb_item.get('item_price') if tb_item else None
                },
                'wb': {
                    'available': bool(wb_item),
                    'price': wb_item.get('item_price') if wb_item else None
                },
                'sg': {'available': False, 'price': None},
                'yp': {'available': False, 'price': None},
                'date_a': None,
                'date_b': None,
                'date_c': None,
            })
        else:
            wb_item = find_item_by_number(subject_doc.practice_workbook_110)
            sg_item = find_item_by_number(subject_doc.student_guide_220)
            yp_item = find_item_by_number(subject_doc.prev_year_paper_160)

            subjects.append({
                'code': code,
                'name': subject_doc.name,
                'exam_available': True,
                'exam_fee': 175,  # TODO: replace with per-subject fee once that field exists
                'wb': {
                    'available': bool(wb_item),
                    'price': wb_item.get('item_price') if wb_item else None
                },
                'sg': {
                    'available': bool(sg_item),
                    'price': sg_item.get('item_price') if sg_item else None
                },
                'yp': {
                    'available': bool(yp_item),
                    'price': yp_item.get('item_price') if yp_item else None
                },
                'date_a': formatdate(row.tg_date_1, 'dd MMM, yyyy') if row.tg_date_1 else None,
                'date_b': formatdate(row.tg_date_2, 'dd MMM, yyyy') if row.tg_date_2 else None,
                'date_c': formatdate(row.tg_date_3, 'dd MMM, yyyy') if row.tg_date_3 else None,
            })

    return {'subjects': subjects, 'found_customer': True, 'is_little_champ': is_little_champ}