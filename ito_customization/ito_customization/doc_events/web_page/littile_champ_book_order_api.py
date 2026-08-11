# Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
import json
from urllib.parse import parse_qs, urlparse
from frappe.utils import cint, today
from frappe.utils import flt
from multi_company_razorpay.api import (
    get_checkout_context,
    get_settings_for_page,
)
from frappe.desk.form import assign_to
from frappe.model.base_document import get_controller
from frappe.utils import cint, today, add_days
from frappe.utils import flt

RAZORPAY_PAGE = 'Little Champ Book Order'
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
def initiate_little_champ_books_order_payment(sales_order, customer=None, trans_item_json=None):
    """Initiate Razorpay payment WITHOUT submitting the Sales Order or creating
    a Sales Invoice.  Accounting documents (SO submit → SI → Payment Entry) are
    created ONLY after payment succeeds in the *confirm* handler.

    This eliminates the cleanup headache when Razorpay rejects the order
    (e.g. "Amount exceeds maximum amount allowed") — there's nothing to
    roll back because nothing was committed in ERPNext.
    """
    session_customer = frappe.db.get_value('Portal User', {'user': frappe.session.user}, 'parent')
    customer = session_customer or customer
    if session_customer and customer and session_customer != customer:
        frappe.throw(_('You are not allowed to pay for this customer.'), frappe.PermissionError)
    if not customer:
        frappe.throw(_('Please save School Information first.'))

    try:
        # ------------------------------------------------------------
        # Parse & validate trans_item_json
        # ------------------------------------------------------------
        frappe.log_error(
            title="Trans Item JSON",
            message=json.dumps(trans_item_json, indent=2, default=str) )
        trans_items = (
            json.loads(trans_item_json)
            if isinstance(trans_item_json, str)
            else trans_item_json
        ) if trans_item_json else None

        if not trans_items:
            frappe.throw(_('No items selected for payment.'))

        total_amount = sum(
            flt(row.get("qty")) * flt(row.get("rate")) for row in trans_items
        )
        if total_amount <= 0:
            frappe.throw(_('Total amount must be greater than zero.'))

        # ------------------------------------------------------------
        # Update draft Sales Order items to match frontend (DON'T submit)
        # ------------------------------------------------------------
        payment_company = get_settings_for_page(RAZORPAY_PAGE).company
        delivery_warehouse = _get_delivery_warehouse(payment_company)
        delivery_date = add_days(today(), 7)

        sales_order_doc = frappe.get_doc('Sales Order', sales_order)
        if sales_order_doc.customer != customer:
            frappe.throw(_('You are not allowed to pay for this order.'), frappe.PermissionError)
        if sales_order_doc.docstatus == 2:
            frappe.throw(_('This Sales Order has been cancelled.'))

        # Check for already-paid status
        existing_invoice = frappe.db.sql(
            """
            select si.name, si.docstatus, si.outstanding_amount
            from `tabSales Invoice` si
            inner join `tabSales Invoice Item` sii on sii.parent = si.name
            where sii.sales_order = %s and si.docstatus = 1
            order by si.creation desc limit 1
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

        # Rebuild SO items to exactly match what the frontend sent
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

        # ------------------------------------------------------------
        # Create Razorpay order directly (NO SO submit, NO SI creation)
        # ------------------------------------------------------------
        settings = get_settings_for_page(RAZORPAY_PAGE)
        gateway_account = settings.get_payment_gateway_account()

        # Build exactly the kwargs that get_payment_url/create_order expect,
        # PLUS extra fields that the confirm handler will need.
        kwargs = {
            "amount": total_amount,
            "currency": settings.account_currency,
            "title": settings.company,
            "description": f"Book Order against {sales_order_doc.name}",
            "reference_doctype": "Sales Order",
            "reference_docname": sales_order_doc.name,
            "payer_email": frappe.session.user,
            "payer_name": customer,
            "payment_gateway": settings.payment_gateway,
            "payment_gateway_account": gateway_account,
            "company": settings.company,
            # These are NOT passed to create_order but stored on Integration Request
            # so the confirm handler can create the accounting documents.
            "book_order_customer": customer,
            "book_order_items": json.dumps(trans_items),
            "book_order_warehouse": delivery_warehouse,
            "book_order_delivery_date": str(delivery_date),
            "redirect_to": f"/portal/little-champ-book?payment=success",
        }

        checkout_url = settings.get_payment_url(**kwargs)
        token = parse_qs(urlparse(checkout_url).query).get('token', [None])[0]
        if not token:
            frappe.throw(_('Unable to start Razorpay checkout.'))

        checkout_context = get_checkout_context(token)
        checkout_context['sales_order'] = sales_order_doc.name
        return checkout_context

    except Exception as e:
        error_msg = str(e)

        # Friendly messages for known Razorpay errors
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

        frappe.log_error(
            frappe.get_traceback(),
            "error in initiate_little_champ_books_order_payment"
        )
        return {
            "success": False,
            "message": human_msg,
            "error": error_msg,
        }


@frappe.whitelist(allow_guest=True)
def confirm_little_champ_books_order_payment(
    integration_request,
    razorpay_payment_id,
    razorpay_order_id,
    razorpay_signature,
):
    """
    Called after the user completes checkout on the Razorpay page.

    Flow:
    1. Verify Razorpay signature & capture payment
    2. Submit the draft Sales Order
    3. Create & submit Sales Invoice
    4. Create & submit Payment Entry
    5. Update Razorpay Transaction
    """

    original_ignore_permissions = frappe.flags.ignore_permissions

    integration = None
    transaction = None
    payment_entry = None

    # Payment Entry controller monkey patch
    actual_pe_class = get_controller("Payment Entry")
    original_set_missing_ref_details = (
        actual_pe_class.set_missing_ref_details
    )

    def fake_set_missing_ref_details(self, *args, **kwargs):
        frappe.logger().info(
            "Skipping set_missing_ref_details for Razorpay Payment Entry"
        )
        return

    try:
        frappe.flags.ignore_permissions = True

        # =========================================================
        # Get Integration Request
        # =========================================================
        integration = frappe.get_doc(
            "Integration Request",
            integration_request,
        )

        data = frappe.parse_json(
            integration.data or "{}"
        )

        # =========================================================
        # Get Razorpay Transaction
        # =========================================================
        transaction_name = data.get(
            "razorpay_transaction"
        )

        if not transaction_name:
            frappe.throw(
                _("Razorpay Transaction is missing from Integration Request.")
            )

        transaction = frappe.get_doc(
            "Razorpay Transaction",
            transaction_name,
        )

        settings = frappe.get_doc(
            "Multi Company Razorpay Settings",
            transaction.settings,
        )

        # =========================================================
        # Step 1: Verify Razorpay Order ID
        # =========================================================
        if razorpay_order_id != transaction.razorpay_order_id:
            frappe.throw(
                _("Razorpay Order ID mismatch."),
                frappe.PermissionError,
            )

        # =========================================================
        # Verify Razorpay Signature
        # =========================================================
        settings.verify_checkout_signature(
            razorpay_order_id,
            razorpay_payment_id,
            razorpay_signature,
        )

        # Record payment information
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

        # =========================================================
        # Step 2: Fetch & Capture Payment
        # =========================================================
        payment = settings.fetch_payment(
            razorpay_payment_id
        )

        if payment.get("status") == "authorized":
            try:
                payment = settings.capture_payment(
                    payment.get("id"),
                    transaction.amount_in_smallest_unit,
                )
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    f"Razorpay capture failed for {transaction.name}",
                )

                return {
                    "paid": False,
                    "message": _(
                        "Payment was authorized but could not be captured."
                    ),
                }

        if payment.get("status") != "captured":
            return {
                "paid": False,
                "message": _(
                    "Payment status is '{0}', not captured."
                ).format(
                    payment.get("status")
                ),
            }

        # =========================================================
        # Step 3: Get Sales Order
        # =========================================================
        sales_order_name = data.get(
            "reference_docname"
        )

        if not sales_order_name:
            frappe.throw(
                _("Sales Order reference is missing.")
            )

        if not frappe.db.exists(
            "Sales Order",
            sales_order_name,
        ):
            frappe.throw(
                _(
                    "Sales Order {0} not found. Please recreate your order."
                ).format(
                    sales_order_name
                )
            )

        so_doc = frappe.get_doc(
            "Sales Order",
            sales_order_name,
        )

        # =========================================================
        # Rebuild Sales Order Items
        # =========================================================
        items_json = data.get(
            "book_order_items"
        )

        if items_json:
            trans_items = (
                json.loads(items_json)
                if isinstance(items_json, str)
                else items_json
            )

            delivery_warehouse = (
                data.get("book_order_warehouse")
                or _get_delivery_warehouse(settings.company)
            )

            delivery_date = (
                data.get("book_order_delivery_date")
                or str(add_days(today(), 7))
            )

            so_doc.set("items", [])

            for row in trans_items:
                item_code = row.get("item")
                qty = flt(row.get("qty"))
                rate = flt(row.get("rate"))

                if item_code and qty > 0:
                    so_doc.append(
                        "items",
                        {
                            "item_code": item_code,
                            "qty": qty,
                            "rate": rate,
                            "delivery_date": delivery_date,
                            "warehouse": delivery_warehouse,
                        },
                    )

            so_doc.flags.ignore_permissions = True

            so_doc.save(
                ignore_permissions=True
            )

        # =========================================================
        # Submit Sales Order
        # =========================================================
        if so_doc.docstatus == 0:
            so_doc.flags.ignore_permissions = True
            so_doc.submit()

        # =========================================================
        # Step 4: Create & Submit Sales Invoice
        # =========================================================
        from erpnext.selling.doctype.sales_order.sales_order import (
            make_sales_invoice,
        )

        sales_invoice = make_sales_invoice(
            so_doc.name,
            ignore_permissions=True,
        )

        sales_invoice.set_posting_time = 1
        sales_invoice.flags.ignore_permissions = True
        # Remove all tax rows copied from Sales Order
        sales_invoice.set("taxes", [])

        # Remove tax category / tax-related configuration
        sales_invoice.tax_category = None

        # Recalculate invoice totals without taxes
        sales_invoice.calculate_taxes_and_totals()

        # Explicitly ensure tax amount is zero
        sales_invoice.total_taxes_and_charges = 0
        sales_invoice.base_total_taxes_and_charges = 0

        # Grand total = net total
        sales_invoice.grand_total = sales_invoice.net_total
        sales_invoice.base_grand_total = sales_invoice.base_net_total

        if sales_invoice.disable_rounded_total:
            sales_invoice.rounded_total = sales_invoice.grand_total
            sales_invoice.base_rounded_total = sales_invoice.base_grand_total

        sales_invoice.set_posting_time = 1
        sales_invoice.flags.ignore_permissions = True
        sales_invoice.insert(
            ignore_permissions=True
        )

        sales_invoice.submit()

        # =========================================================
        # Step 5: Create Payment Entry
        # =========================================================

        # Get actual Payment Entry controller.
        # This is important when HRMS or another app overrides it.
        actual_pe_class.set_missing_ref_details = (
            fake_set_missing_ref_details
        )

        from erpnext.accounts.doctype.payment_entry.payment_entry import (
            get_payment_entry,
        )

        payment_entry = get_payment_entry(
            "Sales Invoice",
            sales_invoice.name,
            party_amount=transaction.amount,
            bank_account=settings.payment_account,
        )

        payment_entry.reference_no = (
            razorpay_payment_id
            or razorpay_order_id
        )

        payment_entry.reference_date = today()

        payment_entry.remarks = (
            f"Book Order Payment for {so_doc.name} "
            f"via Razorpay ({razorpay_order_id})"
        )

        # Same logic as existing create_payment_entry()
        payment_entry.flags.ignore_validate = True
        payment_entry.flags.ignore_permissions = True

        # validate() is skipped, therefore set title manually
        if payment_entry.payment_type in ("Receive", "Pay"):
            payment_entry.title = payment_entry.party
        else:
            payment_entry.title = (
                payment_entry.paid_from
                + " - "
                + payment_entry.paid_to
            )

        # =========================================================
        # Insert Payment Entry
        # =========================================================
        payment_entry.insert(
            ignore_permissions=True
        )

        # =========================================================
        # Temporary Share - Payment Entry
        # =========================================================
        frappe.share.add_docshare(
            "Payment Entry",
            payment_entry.name,
            user=frappe.session.user,
            read=1,
            flags={
                "ignore_share_permission": True,
            },
        )

        # =========================================================
        # Temporary Share - Sales Invoice
        # =========================================================
        frappe.share.add_docshare(
            "Sales Invoice",
            sales_invoice.name,
            user=frappe.session.user,
            read=1,
            flags={
                "ignore_share_permission": True,
            },
        )

        # =========================================================
        # Temporary Assignment - Payment Entry
        # =========================================================
        assign_to.add(
            {
                "assign_to": [frappe.session.user],
                "doctype": "Payment Entry",
                "name": payment_entry.name,
                "description": (
                    "Share Payment Entry with user for reference"
                ),
            }
        )

        # =========================================================
        # Temporary Assignment - Sales Invoice
        # =========================================================
        assign_to.add(
            {
                "assign_to": [frappe.session.user],
                "doctype": "Sales Invoice",
                "name": sales_invoice.name,
                "description": (
                    "Share Sales Invoice with user for reference"
                ),
            }
        )

        # =========================================================
        # Submit Payment Entry
        # =========================================================
        frappe.flags.ignore_permissions = True

        payment_entry.submit()

        # =========================================================
        # Step 6: Update Razorpay Transaction
        # =========================================================
        transaction.db_set({
            "status": "Completed",
            "sales_invoice": sales_invoice.name,
            "reference_doctype": "Sales Invoice",
            "reference_docname": sales_invoice.name,
            "payment_entry": payment_entry.name,
            "gateway_response": json.dumps(
                payment,
                default=str,
            ),
            "processed_on": frappe.utils.now_datetime(),
        })

        # =========================================================
        # Update Integration Request
        # =========================================================
        frappe.db.set_value(
            "Integration Request",
            integration.name,
            "status",
            "Completed",
        )

        # =========================================================
        # Success Response
        # =========================================================
        return {
            "paid": True,
            "sales_invoice": sales_invoice.name,
            "payment_entry": payment_entry.name,
            "sales_order": so_doc.name,
            "redirect_to": (
                "/portal/little-champ-book?payment=success"
            ),
        }

    except Exception as e:
        # =========================================================
        # Exception Handling
        # =========================================================
        error_message = str(e)

        frappe.log_error(
            frappe.get_traceback(),
            "Little Champ Book Order Payment Failed",
        )

        # ---------------------------------------------------------
        # Update Razorpay Transaction
        # ---------------------------------------------------------
        if transaction:
            try:
                transaction.db_set({
                    "status": "Failed",
                    "gateway_response": json.dumps(
                        {
                            "error": error_message,
                            "payment_id": razorpay_payment_id,
                            "order_id": razorpay_order_id,
                        },
                        default=str,
                    ),
                })
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    "Failed to update Razorpay Transaction status",
                )

        # ---------------------------------------------------------
        # Update Integration Request
        # ---------------------------------------------------------
        if integration:
            try:
                frappe.db.set_value(
                    "Integration Request",
                    integration.name,
                    "status",
                    "Failed",
                )

                # Only if "error" field exists on Integration Request
                if frappe.get_meta(
                    "Integration Request"
                ).has_field("error"):
                    frappe.db.set_value(
                        "Integration Request",
                        integration.name,
                        "error",
                        error_message,
                    )

            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    "Failed to update Integration Request status",
                )

        # ---------------------------------------------------------
        # Rollback Current Transaction
        # ---------------------------------------------------------
        frappe.db.rollback()

        return {
            "paid": False,
            "success": False,
            "message": _(
                "Payment processing failed. Please contact support."
            ),
            "error": error_message,
        }

    finally:
        # =========================================================
        # Restore Payment Entry Controller
        # =========================================================
        actual_pe_class.set_missing_ref_details = (
            original_set_missing_ref_details
        )

        # =========================================================
        # Restore Permission Flag
        # =========================================================
        frappe.flags.ignore_permissions = (
            original_ignore_permissions
        )

        # =========================================================
        # Remove Payment Entry Assignment
        # =========================================================
        if payment_entry:
            try:
                assign_to.set_status(
                    "Payment Entry",
                    payment_entry.name,
                    assign_to=frappe.session.user,
                    status="Cancelled",
                    ignore_permissions=True,
                )
            except Exception:
                pass

        # =========================================================
        # Remove Sales Invoice Assignment
        # =========================================================
        if "sales_invoice" in locals():
            try:
                assign_to.set_status(
                    "Sales Invoice",
                    sales_invoice.name,
                    assign_to=frappe.session.user,
                    status="Cancelled",
                    ignore_permissions=True,
                )
            except Exception:
                pass

        # =========================================================
        # Remove Payment Entry Share
        # =========================================================
        if payment_entry:
            try:
                frappe.db.delete(
                    "DocShare",
                    {
                        "user": frappe.session.user,
                        "share_name": payment_entry.name,
                        "share_doctype": "Payment Entry",
                    },
                )
            except Exception:
                pass

        # =========================================================
        # Remove Sales Invoice Share
        # =========================================================
        if "sales_invoice" in locals():
            try:
                frappe.db.delete(
                    "DocShare",
                    {
                        "user": frappe.session.user,
                        "share_name": sales_invoice.name,
                        "share_doctype": "Sales Invoice",
                    },
                )
            except Exception:
                pass



@frappe.whitelist(allow_guest=True)
def get_little_champ_books_order_payment_status(sales_order=None):
    if not sales_order:
        session_customer = frappe.db.get_value('Portal User', {'user': frappe.session.user}, 'parent')
        if not session_customer:
            return {"paid": False}
        
        # Find latest Sales Order for this customer
        sales_order_doc = frappe.db.sql(
            """
            select so.name
            from `tabSales Order` so
            where so.customer = %s
            order by so.creation desc limit 1
            """,
            session_customer,
            as_dict=True
        )
        if not sales_order_doc:
            return {"paid": False}
            
        sales_order = sales_order_doc[0].name

    existing_invoice = frappe.db.sql(
        """
        select si.name, si.docstatus, si.outstanding_amount
        from `tabSales Invoice` si
        inner join `tabSales Invoice Item` sii on sii.parent = si.name
        where sii.sales_order = %s and si.docstatus = 1
        order by si.creation desc limit 1
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