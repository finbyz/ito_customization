# -*- coding: utf-8 -*-
# Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
import json
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


@frappe.whitelist()
def save_ito_registration(registration_data):

    try:

        data = (
            json.loads(registration_data)
            if isinstance(registration_data, str)
            else registration_data
        )
        
        customer = create_or_update_customer(
            data.get("school_info", {})
        )

        create_or_update_address(
            customer,
            data.get("school_info", {})
        )

        create_or_update_teachers(
            data.get("coordinators", {}),
            customer
        )

        # --------------------------------------------------
        # Update Exams Summary
        # --------------------------------------------------

        exams_data = data.get("exams", {})

        if exams_data:

            es_name = frappe.db.get_value(
                "Exams Summary",
                {
                    "customer": customer
                }
            )

            if es_name:

                es_doc = frappe.get_doc(
                    "Exams Summary",
                    es_name
                )

                # Clear existing rows

                es_doc.exam_summary = []

                subject_map = {
                    "IDO": "Drawing Olympiad (IDO)",
                    "NESO": "Essay Olympiad (NESO)",
                    "EIO": "English Olympiad (EIO)",
                    "IMO": "Maths Olympiad (IMO)",
                    "ISO": "Science Olympiad (ISO)",
                    "GKIO": "General Knowledge (GKIO)",
                    "ICO": "Computer Olympiad (ICO)",
                    "NSSO": "Social Studies (NSSO)",
                    "NHO": "Hindi Olympiad (NHO)",
                    "NLRO": "Logical Reasoning (NLRO)",
                    "CIO": "Commerce Olympiad (CIO)"
                }

                for subject_code, rows in exams_data.items():

                    subject_name = subject_map.get(
                        subject_code,
                        subject_code
                    )

                    for row in rows:

                        es_doc.append(
                            "exam_summary",
                            {
                                "subject": subject_name,
                                "class": row.get("class"),
                                "teacher_name": row.get("teacher_name"),
                                "whatsapp_no": row.get("whatsapp"),
                                "no_of_students": row.get("students")
                            }
                        )

                es_doc.save(
                    ignore_permissions=True
                )

        frappe.log_error(
            title="ITO Registration Payload",
            message=json.dumps(
                data,
                indent=2
            )
        )

        frappe.db.commit()

        return {
            "success": True,
            "customer": customer
        }

    except Exception:

        frappe.db.rollback()

        frappe.log_error(
            frappe.get_traceback(),
            "ITO Registration Error"
        )

        return {
            "success": False,
            "message": frappe.get_traceback()
        }



@frappe.whitelist()
def save_registration_step():

    try:

        data = frappe.request.get_json() or {}

        step = cint(data.get("step"))

        # --------------------------------------------------
        # STEP 1 : School Information
        # --------------------------------------------------

        if step == 1:

            school_info = data.get(
                "school_info",
                {}
            )

            customer = create_or_update_customer(
                school_info
            )

            create_or_update_address(
                customer,
                school_info
            )

            frappe.cache().set_value(
                f"ito_customer_{frappe.session.user}",
                customer
            )

            frappe.db.commit()

            return {
                "success": True,
                "step": 1,
                "customer": customer
            }

        # --------------------------------------------------
        # STEP 2 : Principal + Coordinators
        # --------------------------------------------------

        elif step == 2:

            customer = frappe.cache().get_value(
                f"ito_customer_{frappe.session.user}"
            )

            if not customer:

                frappe.throw(
                    "Please save School Information first."
                )

            coordinators = data.get(
                "coordinators",
                {}
            )

            create_or_update_teachers(
                coordinators,
                customer
            )

            frappe.db.commit()

            return {
                "success": True,
                "step": 2
            }

        # --------------------------------------------------
        # STEP 3 : Exams Summary
        # --------------------------------------------------

        elif step == 3:

            customer = frappe.cache().get_value(
                f"ito_customer_{frappe.session.user}"
            )

            if not customer:

                frappe.throw(
                    "Please save School Information first."
                )

            exams_data = data.get(
                "exams",
                {}
            )

            frappe.log_error(
                title="STEP 3 EXAMS DATA",
                message=frappe.as_json(exams_data)
            )

            if exams_data:

                es_name = frappe.db.get_value(
                    "Exams Summary",
                    {
                        "customer": customer
                    }
                )

                if es_name:

                    es_doc = frappe.get_doc(
                        "Exams Summary",
                        es_name
                    )

                    es_doc.exam_summary = []

                    subject_map = {
                        "IDO": "Drawing Olympiad (IDO)",
                        "NESO": "Essay Olympiad (NESO)",
                        "EIO": "English Olympiad (EIO)",
                        "IMO": "Maths Olympiad (IMO)",
                        "ISO": "Science Olympiad (ISO)",
                        "GKIO": "General Knowledge (GKIO)",
                        "ICO": "Computer Olympiad (ICO)",
                        "NSSO": "Social Studies (NSSO)",
                        "NHO": "Hindi Olympiad (NHO)",
                        "NLRO": "Logical Reasoning (NLRO)",
                        "CIO": "Commerce Olympiad (CIO)"
                    }

                    for subject_code, rows in exams_data.items():

                        subject_name = subject_map.get(
                            subject_code,
                            subject_code
                        )

                        for row in rows:

                            es_doc.append(
                                "exam_summary",
                                {
                                    "subject": subject_name,
                                    "class": row.get("class"),
                                    "teacher_name": row.get("teacher_name"),
                                    "whatsapp_no": row.get("whatsapp"),
                                    "no_of_students": row.get("students")
                                }
                            )

                    es_doc.save(
                        ignore_permissions=True
                    )

            frappe.db.commit()

            return {
                "success": True,
                "step": 3
            }

        else:

            frappe.throw(
                f"Invalid step: {step}"
            )

    except Exception:

        frappe.db.rollback()

        frappe.log_error(
            frappe.get_traceback(),
            "Save Registration Step Error"
        )

        return {
            "success": False,
            "message": frappe.get_traceback()
        }

def create_or_update_customer(school_info):

    school_name = school_info.get("school_name")

    if not school_name:
        frappe.throw("School Name is mandatory")

    existing = frappe.db.exists(
        "Customer",
        {"customer_name": school_name}
    )

    if existing:
        customer = frappe.get_doc("Customer", existing)
    else:
        customer = frappe.new_doc("Customer")
        customer.customer_name = school_name

    customer.customer_type = "Company"

    if hasattr(customer, "email_id"):
        customer.email_id = school_info.get("school_email")

    if school_info.get("school_code"):
        customer.custom_school_code = (
            school_info.get("school_code")
        )

    if hasattr(customer, "mobile_no"):
        customer.mobile_no = school_info.get("school_phone1")

    if hasattr(customer, "gstin"):
        customer.gstin = school_info.get("gst_no")

    if hasattr(customer, "custom_board"):
        customer.custom_board = school_info.get("board")
        
    frappe.log_error(
        title="FIELD CHECK",
        message=str(hasattr(customer, "custom_student_strength"))
    )

    customer.custom_student_strength = cint(
        school_info.get("student_strength") or 0
    )
    
    if hasattr(customer, "custom_ito_school_code"):
        customer.custom_ito_school_code = school_info.get("ito_school_code")

    if hasattr(customer, "custom_customer_category"):
        customer.custom_customer_category = "School Customer"
        
    if customer.is_new():
        customer.insert(ignore_permissions=True)
    else:
        customer.save(ignore_permissions=True)

    return customer.name


def create_or_update_address(customer_name, school_info):
    
    frappe.log_error(
        title="ADDRESS HIT",
        message=f"""
        customer_name = {customer_name}
        school_address = {school_info.get('school_address')}
        """
    )

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


def create_or_update_teachers(
    coordinators,
    customer_name
):

    customer = frappe.get_doc(
        "Customer",
        customer_name
    )

    subject_map = {
        "principal": "Principal",
        "overall_coordinator": "Overall Coordinator",
        "iso": "Science Olympiad (ISO)",
        "imo": "Maths Olympiad (IMO)",
        "eio": "English Olympiad (EIO)",
        "gkio": "General Knowledge (GKIO)",
        "ico": "Computer Olympiad (ICO)",
        "ido": "Drawing Olympiad (IDO)",
        "neso": "Essay Olympiad (NESO)",
        "nsso": "Social Studies (NSSO)",
        "nho": "Hindi Olympiad (NHO)",
        "nlro": "Logical Reasoning (NLRO)",
        "cio": "Commerce Olympiad (CIO)"
    }

    # ----------------------------------
    # Ensure Subjects Exist
    # ----------------------------------

    for subject_name in subject_map.values():

        ensure_subject_exists(
            subject_name
        )

    # ----------------------------------
    # Clear Customer Child Table
    # ----------------------------------

    customer.custom_school_teacher_details = []

    for role, coordinator in coordinators.items():

        if not coordinator.get("name"):
            continue

        subject = subject_map.get(role)

        if not subject:
            continue

        # ----------------------------------
        # Find Existing Teacher
        # ----------------------------------

        teacher_name = frappe.db.get_value(
            "Teacher",
            {
                "name1": coordinator.get("name")
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

        teacher.name1 = coordinator.get("name")

        teacher.phone_number = (
            coordinator.get("mobile")
        )

        teacher.email_id = (
            coordinator.get("email")
        )

        teacher.date_of_birth = (
            coordinator.get("dob")
            or "2000-01-01"
        )

        teacher.subject = subject

        teacher.status = "Active"

        teacher.experience = "Experienced"

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
        # Customer Child Table
        # ----------------------------------

        customer.append(
            "custom_school_teacher_details",
            {
                "name1": teacher.name,
                "date_of_birth":
                    teacher.date_of_birth,
                "phone_number":
                    teacher.phone_number,
                "status":
                    teacher.status,
                "email_id":
                    teacher.email_id,
                "subject":
                    teacher.subject,
                "experience":
                    teacher.experience
            }
        )

    customer.save(
        ignore_permissions=True
    )


@frappe.whitelist()
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

    customer = frappe.get_doc(
        "Customer",
        customer_name
    )

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
            "country": addr.country,
            "pincode": addr.pincode
        }

    # --------------------------------------------------
    # Primary Contact Details
    # --------------------------------------------------

    contact_email = ""
    contact_mobile = ""

    if customer.customer_primary_contact:

        contact = frappe.get_doc(
            "Contact",
            customer.customer_primary_contact
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

    # --------------------------------------------------
    # Teacher / Coordinator Data (Step 2)
    # --------------------------------------------------

    coordinators_data = {}

    subject_role_map = {
        "Science Olympiad (ISO)": "iso",
        "Maths Olympiad (IMO)": "imo",
        "English Olympiad (EIO)": "eio",
        "General Knowledge (GKIO)": "gkio",
        "Computer Olympiad (ICO)": "ico",
        "Drawing Olympiad (IDO)": "ido",
        "Essay Olympiad (NESO)": "neso",
        "Social Studies (NSSO)": "nsso",
        "Hindi Olympiad (NHO)": "nho",
        "Logical Reasoning (NLRO)": "nlro",
        "Commerce Olympiad (CIO)": "cio"
    }

    for row in customer.custom_school_teacher_details:

        role = subject_role_map.get(row.subject)

        if not role:
            continue

        coordinators_data[role] = {
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
                )
            })

        exam_summaries_data.extend(
            subject_map.values()
        )

    return {
        "customer": customer.as_dict(),
        "address": address,
        "contact_email": contact_email,
        "contact_mobile": contact_mobile,
        "application_deadline": customer.custom_application_deadline,
        "coordinators": coordinators_data,
        "exam_summaries": exam_summaries_data,
        "session_user": frappe.session.user
    }








    # --------------------------- Little Champ >>>>>>>>>>>>>>

@frappe.whitelist()
def save_little_champ_registration(registration_data):

    try:

        data = (
            json.loads(registration_data)
            if isinstance(registration_data, str)
            else registration_data
        )

        # ----------------------------------
        # Customer
        # ----------------------------------

        customer = create_or_update_customer(
            data.get("school_info", {})
        )

        customer_doc = frappe.get_doc(
            "Customer",
            customer
        )

        customer_doc.custom_is_little_champ = 1

        customer_doc.save(
            ignore_permissions=True
        )

        # ----------------------------------
        # Address
        # ----------------------------------

        create_or_update_address(
            customer_doc.name,
            data.get("school_info", {})
        )

        frappe.db.commit()

        return {
            "success": True,
            "customer": customer_doc.name
        }

    except Exception:

        frappe.db.rollback()

        frappe.log_error(
            frappe.get_traceback(),
            "Little Champ Registration Error"
        )

        return {
            "success": False,
            "message": frappe.get_traceback()
        }