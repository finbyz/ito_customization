# Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
import json
from urllib.parse import parse_qs, urlparse
from frappe.utils import cint, today
from frappe.utils import flt
from multi_company_razorpay.api import (
    checkout_success,
    create_payment_for_sales_invoice,
    get_checkout_context,
    get_settings_for_page,
)

RAZORPAY_PAGE = 'Little Champ Book Order'
from frappe.utils import cint, today, add_days
from frappe.utils import flt

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
def save_little_champ_book_order(order_data):
    original_flag = frappe.flags.ignore_permissions

    try:
        frappe.flags.ignore_permissions = True
        data = (
            json.loads(order_data)
            if isinstance(order_data, str)
            else order_data
        )

        frappe.log_error(
            "Little Champ Book Order Data",
            json.dumps(data, indent=2)
        )

        school_info = data.get("school_info", {})
        books_selection = data.get("books_selection", [])

        # ----------------------------------
        # Customer
        # ----------------------------------

        # FIX: Handle both 'school_code' and 'ito_school_code' from frontend
        if not school_info.get("school_code") and school_info.get("ito_school_code"):
            school_info["school_code"] = school_info.get("ito_school_code")

        customer = create_or_update_customer_book_order(school_info)
        
        customer_doc = frappe.get_doc("Customer", customer)

        customer_doc.custom_is_little_champ = 1
        customer_doc.custom_school_code = school_info.get("school_code")
        customer_doc.custom_taluka = school_info.get("taluka")
        customer_doc.custom_district = school_info.get("district")

        customer_doc.save(ignore_permissions=True)

        # ----------------------------------
        # Contact
        # ----------------------------------

        if customer_doc.customer_primary_contact:
            contact = frappe.get_doc("Contact", customer_doc.customer_primary_contact)
        else:
            contact = frappe.new_doc("Contact")
            contact.first_name = school_info.get("school_name") or customer_doc.customer_name
            contact.append("links", {
                "link_doctype": "Customer",
                "link_name": customer_doc.name
            })

        contact.email_id = school_info.get("school_email")
        contact.mobile_no = school_info.get("phone1") or school_info.get("phone_no")

        if hasattr(contact, "custom_whatsapp_no"):
            contact.custom_whatsapp_no = school_info.get("whatsapp_no")

        if contact.is_new():
            contact.insert(ignore_permissions=True)
            customer_doc.customer_primary_contact = contact.name
            customer_doc.save(ignore_permissions=True)
        else:
            contact.save(ignore_permissions=True)

        # ----------------------------------
        # Address
        # ----------------------------------

        create_or_update_address(customer_doc.name, school_info)

        # ----------------------------------
        # Coordinator
        # ----------------------------------

        principal = data.get("principal", {})
        coordinator = data.get("coordinator", {})

        if coordinator:
            create_or_update_school_contact(principal, customer, "Principal")
            create_or_update_school_contact(coordinator, customer, "Overall Coordinator")

        # ----------------------------------
        # Books Selection
        # ----------------------------------

        books_selection = data.get("books_selection", [])
        if books_selection:
            create_or_update_books_selection(customer_doc.name, books_selection)

        # NO explicit commit - let Frappe handle it
        return {
            "success": True,
            "customer": customer_doc.name
        }

    except Exception as e:
        # NO explicit rollback - let Frappe handle exceptions
        frappe.log_error(
            frappe.get_traceback(),
            "Little Champ Book Order Save Error"
        )
        return {
            "success": False,
            "message": str(e)
        }

    finally:
        frappe.flags.ignore_permissions = original_flag



def _get_delivery_warehouse(company):
    """Return a usable leaf warehouse for Little Champ orders in `company`."""
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
        frappe.throw(_("No active delivery warehouse is configured for {0}.").format(frappe.bold(company)))
    return warehouse


def create_or_update_customer_book_order(
    school_info
):

    school_name = school_info.get(
        "school_name"
    )

    if not school_name:
        frappe.throw(
            "School Name is mandatory"
        )

    existing_customer = frappe.db.exists(
        "Customer",
        {
            "customer_name":
                school_name
        }
    )

    if existing_customer:

        customer_doc = frappe.get_doc(
            "Customer",
            existing_customer
        )

    else:

        customer_doc = frappe.new_doc(
            "Customer"
        )

        customer_doc.customer_name = (
            school_name
        )

    customer_doc.customer_type = (
        "Company"
    )

    # ----------------------------------
    # Customer Category
    # ----------------------------------

    if hasattr(
        customer_doc,
        "custom_customer_category"
    ):

        customer_doc.custom_customer_category = (
            "School Customer"
        )

    # ----------------------------------
    # School Code
    # ----------------------------------

    if hasattr(
        customer_doc,
        "custom_school_code"
    ):

        customer_doc.custom_school_code = (
            school_info.get(
                "school_code"
            )
        )

    # ----------------------------------
    # Email
    # ----------------------------------

    if hasattr(
        customer_doc,
        "email_id"
    ):

        customer_doc.email_id = (
            school_info.get(
                "school_email"
            )
        )

    # ----------------------------------
    # Mobile
    # ----------------------------------

    if hasattr(
        customer_doc,
        "mobile_no"
    ):

        customer_doc.mobile_no = (
            school_info.get(
                "phone1"
            )
        )

    # ----------------------------------
    # Taluka
    # ----------------------------------

    if hasattr(
        customer_doc,
        "custom_taluka"
    ):

        customer_doc.custom_taluka = (
            school_info.get(
                "taluka"
            )
        )

    # ----------------------------------
    # District
    # ----------------------------------

    if hasattr(
        customer_doc,
        "custom_district"
    ):

        customer_doc.custom_district = (
            school_info.get(
                "district"
            )
        )

    # ----------------------------------
    # Little Champ Flags
    # ----------------------------------

    if hasattr(
        customer_doc,
        "custom_is_little_champ"
    ):

        customer_doc.custom_is_little_champ = 1

    # ----------------------------------
    # Save
    # ----------------------------------

    if customer_doc.is_new():

        customer_doc.insert(
            ignore_permissions=True
        )

    else:

        customer_doc.save(
            ignore_permissions=True
        )

    return customer_doc.name

def create_or_update_address(customer_name, school_info):

    address_name = frappe.db.exists(
        "Address",
        {
            "address_title": customer_name
        }
    )

    if address_name:
        address = frappe.get_doc("Address", address_name)
    else:
        address = frappe.new_doc("Address")

        address.address_title = customer_name
        address.address_type = "Billing"

        address.append(
            "links",
            {
                "link_doctype": "Customer",
                "link_name": customer_name
            }
        )

    address.address_line1 = school_info.get("school_address")
    address.city = school_info.get("city")
    address.state = school_info.get("state")
    address.pincode = school_info.get("pincode")
    address.country = "India"

    if hasattr(address, "custom_taluka"):
        address.custom_taluka = school_info.get("taluka")
        
    if hasattr(address, "custom_district"):
        address.custom_district = school_info.get("district")

    if address.is_new():
        address.insert(ignore_permissions=True)
    else:
        address.save(ignore_permissions=True)

def create_or_update_principal(principal, customer_name):

    if not principal.get("name"):
        return

    contact_name = frappe.db.exists(
        "Contact",
        {
            "first_name": principal.get("name")
        }
    )

    if contact_name:
        contact = frappe.get_doc("Contact", contact_name)
    else:
        contact = frappe.new_doc("Contact")

    contact.first_name = principal.get("name")
    contact.designation = "Principal"

    contact.email_ids = []
    contact.phone_nos = []

    if principal.get("email"):
        contact.append(
            "email_ids",
            {
                "email_id": principal.get("email"),
                "is_primary": 1
            }
        )

    if principal.get("mobile"):
        contact.append(
            "phone_nos",
            {
                "phone": principal.get("mobile"),
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
        contact.insert(ignore_permissions=True)
    else:
        contact.save(ignore_permissions=True)


def create_or_update_address(
    customer_name,
    school_info
):

    address_name = frappe.db.exists(
        "Address",
        {
            "address_title": customer_name
        }
    )

    if address_name:

        address = frappe.get_doc(
            "Address",
            address_name
        )

    else:

        address = frappe.new_doc(
            "Address"
        )

        address.address_title = (
            customer_name
        )

        address.address_type = (
            "Billing"
        )

        address.append(
            "links",
            {
                "link_doctype": "Customer",
                "link_name": customer_name
            }
        )

    address.address_line1 = school_info.get(
        "school_address"
    )

    address.city = school_info.get(
        "city"
    )

    address.state = school_info.get(
        "state"
    )

    address.pincode = school_info.get(
        "pincode"
    )

    address.country = "India"

    if hasattr(
        address,
        "custom_taluka"
    ):
        address.custom_taluka = (
            school_info.get(
                "taluka"
            )
        )

    if hasattr(
        address,
        "custom_district"
    ):
        address.custom_district = (
            school_info.get(
                "district"
            )
        )

    if hasattr(
        address,
        "custom_gst_no"
    ):
        address.custom_gst_no = (
            school_info.get(
                "gst_no"
            )
        )

    if hasattr(
        address,
        "custom_whatsapp_no"
    ):
        address.custom_whatsapp_no = (
            school_info.get(
                "whatsapp_no"
            )
        )

    if hasattr(
        address,
        "email_id"
    ):
        address.email_id = (
            school_info.get(
                "school_email"
            )
        )

    if hasattr(
        address,
        "phone"
    ):
        address.phone = (
            school_info.get(
                "phone1"
            )
        )

    if address.is_new():

        address.insert(
            ignore_permissions=True
        )

    else:

        address.save(
            ignore_permissions=True
        )

    return address.name


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

            "text_book": [
                {
                    "class": d.get("class"),
                    "item": d.get("item"),
                    "item_price" : d.get("item_price")
                }
                for d in subject_doc.text_book
            ],

            "work_book": [
                {
                    "class": d.get("class"),
                    "item": d.get("item"),
                    "item_price" : d.get("item_price")
                }
                for d in subject_doc.work_book
            ],
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


def create_or_update_books_selection(
    customer_name,
    books_selection
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
            cint(row.get("text_book"))
            or
            cint(row.get("work_book"))
        ):
            continue

        books_doc.append(
            "select_books",
            {
                "subject":
                    row.get("subject"),

                "class_grade":
                    row.get("class_grade"),

                "text_book":
                    cint(
                        row.get(
                            "text_book"
                        )
                    ),

                "work_book":
                    cint(
                        row.get(
                            "work_book"
                        )
                    )
            }
        )

    if books_doc.is_new():

        books_doc.insert(
            ignore_permissions=True
        )

    else:

        books_doc.save(
            ignore_permissions=True
        )

    return books_doc.name


@frappe.whitelist(allow_guest=True)
def get_customer_from_session_user():

    if frappe.session.user == "Guest":
        return {}

    customer_name = frappe.db.get_value(
        "Portal User",
        {"user": frappe.session.user},
        "parent"
    )

    if not customer_name:
        return {}

    if not frappe.db.exists("Customer", customer_name):
        # Delete stale Portal User link
        frappe.db.delete("Portal User", {"user": frappe.session.user, "parent": customer_name})
        # NO explicit commit - let Frappe handle it
        return {}

    customer = frappe.get_doc(
        "Customer",
        customer_name
    )
    

    forms_data = {
        "registration": None,
        "little_champ": None,
        "books_order": None,
        "form4": None,
    }

    # --------------------------------------------------
    # Address Details
    # --------------------------------------------------

    address = {}

    address_names = frappe.get_all(
        "Dynamic Link",
        filters={
            "link_doctype": "Customer",
            "link_name": customer.name,
            "parenttype": "Address"
        },
        pluck="parent"
    )

    if address_names:

        addr = frappe.get_doc(
            "Address",
            address_names[0]
        )

        address = {
            "address_line1": addr.address_line1,
            "address_line2": addr.address_line2,
            "city": addr.city,
            "county": getattr(addr, "county", ""),
            "state": addr.state,
            "custom_taluka": getattr(addr, "custom_taluka", ""),
            "custom_district": getattr(addr, "custom_district", ""),
            "country": addr.country,
            "pincode": addr.pincode
        }

    # --------------------------------------------------
    # Primary Contact Details
    # --------------------------------------------------

    contact_name = ""
    contact_email = ""
    contact_mobile = ""
    contact_whatsapp = ""

    if customer.customer_primary_contact:

        contact = frappe.get_doc(
            "Contact",
            customer.customer_primary_contact
        )

        contact_name = (
            contact.get_full_name()
            if hasattr(contact, "get_full_name")
            else contact.first_name
        )

        if contact.email_ids:

            primary_email = next(
                (
                    row.email_id
                    for row in contact.email_ids
                    if row.is_primary
                ),
                None
            )

            if not primary_email and contact.email_ids:
                primary_email = contact.email_ids[0].email_id

            contact_email = primary_email or ""

        if contact.phone_nos:

            primary_mobile = next(
                (
                    row.phone
                    for row in contact.phone_nos
                    if row.is_primary_mobile_no
                ),
                None
            )

            if not primary_mobile and contact.phone_nos:
                primary_mobile = contact.phone_nos[0].phone

            contact_mobile = primary_mobile or ""

        if not contact_email:
            contact_email = contact.email_id or ""

        if not contact_mobile:
            contact_mobile = (
                contact.mobile_no
                or contact.phone
                or ""
            )

        contact_whatsapp = (
            contact.custom_whatsapp_no
            or ""
        )

    # --------------------------------------------------
    # Teacher / Coordinator Data (Step 2)
    # --------------------------------------------------

    coordinators_data = {}

    for row in customer.custom_school_teacher_details:

        if row.subject == "Principal":
            role_text = "👑 Head Master / Principal"
        elif row.subject == "Overall Coordinator":
            role_text = "⭐ Overall Co-ordinator"
        else:
            role_text = f"{row.subject} In-charge"

        coordinators_data[row.subject] = {
            "role": role_text,
            "name": row.name1,
            "mobile": row.phone_number,
            "email": row.email_id,
            "dob": str(row.date_of_birth) if row.date_of_birth else ""
        }

    
        # --------------------------------------------------
    # Exam Summary Data (Step 3)
    # --------------------------------------------------

    exam_summaries_data = []

    exams = frappe.get_all(
        "Exams Summary",
        filters={
            "customer": customer.name
        },
        pluck="name"
    )

    for exam_name in exams:

        es_doc = frappe.get_doc(
            "Exams Summary",
            exam_name
        )

        if not es_doc.exam_detail:
            subject_map = {}
            for exam_row in es_doc.exam_summary:
                subject = exam_row.subject
                if subject not in subject_map:
                    subject_map[subject] = {
                        "exam_summary_name": es_doc.name,
                        "yearly_exam_date": "",
                        "academic_year": "",
                        "subject": subject,
                        "target_dates": [],
                        "rows": []
                    }
                subject_map[subject]["rows"].append({
                    "class": getattr(exam_row, "class", ""),
                    "teacher_name": getattr(exam_row, "teacher_name", ""),
                    "whatsapp_no": getattr(exam_row, "whatsapp_no", ""),
                    "no_of_students": getattr(exam_row, "no_of_students", 0),
                    "slot_date": getattr(exam_row, "slot_date", "")
                })
            exam_summaries_data.extend(subject_map.values())
            continue

        yearly_exam = frappe.get_doc(
            "Yearly Exam Date",
            es_doc.exam_detail
        )

        subject_map = {}

        for row in yearly_exam.target_dates:

            subject_map[row.subject] = {
                "exam_summary_name": es_doc.name,
                "yearly_exam_date": yearly_exam.name,
                "academic_year": yearly_exam.academic_year,
                "subject": row.subject,
                "target_dates": [
                    row.tg_date_1,
                    row.tg_date_2,
                    row.tg_date_3
                ],
                "rows": []
            }

        for exam_row in es_doc.exam_summary:

            subject = exam_row.subject

            if subject not in subject_map:

                frappe.log_error(
                    title="Subject Not Found",
                    message=f"""
                    Exam Summary Subject: {subject}

                    Available Subjects:
                    {list(subject_map.keys())}
                    """
                )

                continue

            subject_map[subject]["rows"].append({
                "class": getattr(
                    exam_row,
                    "class",
                    ""
                ),
                "teacher_name": getattr(
                    exam_row,
                    "teacher_name",
                    ""
                ),
                "whatsapp_no": getattr(
                    exam_row,
                    "whatsapp_no",
                    ""
                ),
                "no_of_students": getattr(
                    exam_row,
                    "no_of_students",
                    0
                ),
                "slot_date": getattr(exam_row, "slot_date", "")
            })

        exam_summaries_data.extend(
            subject_map.values()
        )
        
    teachers_data = []

    es_name = frappe.db.get_value(
        "Exams Summary",
        {
            "customer": customer.name
        }
    )

    if es_name:

        es_doc = frappe.get_doc(
            "Exams Summary",
            es_name
        )

        for row in es_doc.exam_summary:

            teachers_data.append({
                "subject": row.subject,
                "class": getattr(row, "class", ""),
                "teacher_name": row.teacher_name,
                "whatsapp_no": row.whatsapp_no,
                "no_of_students": row.no_of_students,
                "slot_date": (exam_row, "slot_date", "")
                
            })

    # --------------------------------------------------
    # Books Selection Data
    # --------------------------------------------------

    books_selection = []

    bs_name = frappe.db.get_value(
        "Books Selection",
        {
            "customer": customer.name
        }
    )

    frappe.log_error(
        "BOOKS DEBUG",
        f"Customer={customer.name}\nBooks Selection={bs_name}"
    )

    if bs_name:

        bs_doc = frappe.get_doc(
            "Books Selection",
            bs_name
        )

        for row in bs_doc.select_books:

            frappe.log_error(
                "BOOK ROW DEBUG",
                frappe.as_json(row.as_dict())
            )


            books_selection.append({
                "subject": row.subject,
                "class_grade": row.class_grade,
                "text_book" : row.text_book,
                "work_book" : row.work_book
            })

    return {
        "customer": customer.as_dict(),
        "address": address,
        "contact_name": contact_name,
        "contact_email": contact_email,
        "contact_mobile": contact_mobile,
        "contact_whatsapp": contact_whatsapp,
        "application_deadline": customer.custom_application_deadline,
        "school_code": customer.custom_school_code,
        "registration_date_1": customer.custom_last_date_of_reg,
        "registration_date_2": customer.custom_last_date_of_reg_2,
        "coordinators": coordinators_data,
        "exam_summaries": exam_summaries_data,
        "session_user": frappe.session.user,
        "teachers": teachers_data,
        "books_selection": books_selection
    }

def create_or_update_school_contact(
    person_data,
    customer_name,
    role
):

    if not person_data.get("name"):
        return

    customer = frappe.get_doc(
        "Customer",
        customer_name
    )

    ensure_subject_exists(role)

    # ----------------------------------
    # Find Existing Teacher
    # ----------------------------------

    teacher_name = frappe.db.get_value(
        "Teacher",
        {
            "customer_reference":
                customer.name,
            "subject":
                role
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

    teacher.name1 = (
        person_data.get("name")
    )

    teacher.phone_number = (
        person_data.get("mobile")
    )

    teacher.email_id = (
        person_data.get("email")
    )

    teacher.date_of_birth = (
        teacher.date_of_birth
        or "2000-01-01"
    )

    teacher.subject = role

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

    for row in (
        customer.custom_school_teacher_details
    ):

        if row.subject == role:

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

        existing_row.subject = role

        existing_row.status = (
            teacher.status
        )

        existing_row.experience = (
            teacher.experience
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
                    role,

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
    # No items selected — return friendly alert
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
    # FIX: Use party_name (the actual link field) instead of customer_name
    # Also check customer_name as fallback for backwards compatibility

    quotation_name = frappe.db.get_value(
        "Quotation",
        {
            "party_name": customer_name,
            "docstatus": 0
        },
        "name",
        order_by="creation desc"
    )

    # Fallback: try customer_name if party_name didn't work
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
    
    
    
@frappe.whitelist(allow_guest=True)
def create_sales_order_from_books_order(order_data, customer=None):
    data = (
        json.loads(order_data)
        if isinstance(order_data, str)
        else order_data
    )

    session_customer = frappe.db.get_value(
        "Portal User",
        {"user": frappe.session.user},
        "parent"
    )
    customer_name = session_customer or customer

    if session_customer and customer and session_customer != customer:
        frappe.throw(_("You are not allowed to create an order for this customer."), frappe.PermissionError)
    if not customer_name or not frappe.db.exists("Customer", customer_name):
        return {
            "success": False,
            "message": "Customer not found",
            "alert": True
        }

    # -----------------------------------
    # Filter valid items
    # -----------------------------------

    delivery_date = add_days(today(), 7)  # adjust default lead time as needed
    payment_company = get_settings_for_page(RAZORPAY_PAGE).company
    delivery_warehouse = _get_delivery_warehouse(payment_company)

    valid_items = []
    for row in data:
        item_code = row.get("item")
        qty = flt(row.get("qty"))
        rate = flt(row.get("rate"))

        if item_code and qty > 0:
            valid_items.append({
                "item_code": item_code,
                "qty": qty,
                "rate": rate,
                "delivery_date": delivery_date,
                "warehouse": delivery_warehouse,
            })

    # -----------------------------------
    # No items selected — return friendly alert
    # -----------------------------------

    if not valid_items:
        return {
            "success": True,
            "sales_order": None,
            "message": "No sales order created. Please select items if you want to create a sales order.",
            "alert": True
        }

    # The sales invoice must use the same company as the Razorpay settings
    # tagged for this Little Champ payment page; otherwise checkout rejects it.
    # -----------------------------------
    # Find existing Draft Sales Order
    # -----------------------------------

    sales_order_name = frappe.db.get_value(
        "Sales Order",
        {
            "customer": customer_name,
            "company": payment_company,
            "docstatus": 0
        },
        "name",
        order_by="creation desc"
    )

    # -----------------------------------
    # Update Existing or Create New
    # -----------------------------------

    if sales_order_name:
        sales_order = frappe.get_doc("Sales Order", sales_order_name)
        sales_order.set("items", [])
        is_update = True
    else:
        sales_order = frappe.new_doc("Sales Order")
        sales_order.customer = customer_name
        sales_order.company = payment_company
        sales_order.transaction_date = today()
        sales_order.delivery_date = delivery_date
        is_update = False

    # -----------------------------------
    # Append Items
    # -----------------------------------

    for item in valid_items:
        sales_order.append("items", item)

    # -----------------------------------
    # Save
    # -----------------------------------

    if sales_order.is_new():
        sales_order.insert(ignore_permissions=True)
    else:
        sales_order.save(ignore_permissions=True)

    return {
        "success": True,
        "sales_order": sales_order.name,
        "message": "Sales Order Updated" if is_update else "Sales Order Created"
    }

@frappe.whitelist(allow_guest=True)
def initiate_little_champ_books_order_payment(sales_order, customer=None):
    session_customer = frappe.db.get_value('Portal User', {'user': frappe.session.user}, 'parent')
    customer = session_customer or customer
    if session_customer and customer and session_customer != customer:
        frappe.throw(_('You are not allowed to pay for this customer.'), frappe.PermissionError)
    if not customer:
        frappe.throw(_('Please save School Information first.'))

    original_ignore_permissions = frappe.flags.ignore_permissions
    try:
        frappe.flags.ignore_permissions = True

        sales_order_doc = frappe.get_doc('Sales Order', sales_order)
        if sales_order_doc.customer != customer:
            frappe.throw(_('You are not allowed to pay for this order.'), frappe.PermissionError)
        if sales_order_doc.docstatus == 0:
            sales_order_doc.submit()
        elif sales_order_doc.docstatus == 2:
            frappe.throw(_('This Sales Order has been cancelled.'))

        existing_invoice = frappe.db.sql(
            """
            select si.name, si.docstatus, si.outstanding_amount
            from `tabSales Invoice` si
            inner join `tabSales Invoice Item` sii on sii.parent = si.name
            where sii.sales_order = %s and si.docstatus != 2
            order by si.creation desc limit 1
            """,
            sales_order_doc.name,
            as_dict=True,
        )

        if existing_invoice:
            invoice = existing_invoice[0]
            if invoice.docstatus == 1 and flt(invoice.outstanding_amount) <= 0:
                frappe.throw(_('This book order has already been paid.'))
            if invoice.docstatus == 1:
                invoice_name = invoice.name
                pay_amount = flt(invoice.outstanding_amount)
            else:
                frappe.delete_doc('Sales Invoice', invoice.name, ignore_permissions=True)
                invoice_name = None
        else:
            invoice_name = None

        if not invoice_name:
            from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

            sales_invoice = make_sales_invoice(sales_order_doc.name)
            sales_invoice.set_posting_time = 1
            sales_invoice.insert(ignore_permissions=True)
            sales_invoice.submit()
            invoice_name = sales_invoice.name
            pay_amount = flt(sales_invoice.outstanding_amount)

        result = create_payment_for_sales_invoice(
            sales_invoice=invoice_name, amount=pay_amount, page=RAZORPAY_PAGE
        )
    finally:
        frappe.flags.ignore_permissions = original_ignore_permissions

    token = parse_qs(urlparse(result['checkout_url']).query).get('token', [None])[0]
    if not token:
        frappe.throw(_('Unable to start Razorpay checkout.'))

    checkout_context = get_checkout_context(token)
    checkout_context['sales_invoice'] = invoice_name
    return checkout_context


@frappe.whitelist(allow_guest=True)
def confirm_little_champ_books_order_payment(
    integration_request, razorpay_payment_id, razorpay_order_id, razorpay_signature
):
    result = checkout_success(
        integration_request=integration_request,
        razorpay_payment_id=razorpay_payment_id,
        razorpay_order_id=razorpay_order_id,
        razorpay_signature=razorpay_signature,
    )

    integration = frappe.get_doc('Integration Request', integration_request)
    data = frappe.parse_json(integration.data or '{}')
    transaction = frappe.get_doc('Razorpay Transaction', data['razorpay_transaction'])
    return {
        'paid': transaction.status == 'Completed',
        'payment_entry': transaction.payment_entry,
        'sales_invoice': transaction.reference_docname,
        'redirect_to': result.get('redirect_to'),
    }
