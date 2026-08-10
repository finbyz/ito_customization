# import frappe
# from frappe import _



# def on_update(doc, method=None):

#     # Create Principal Contact for School Lead
#     if (
#         doc.custom_lead_category == "School Lead"
#         and doc.custom_principal_name
#     ):

#         existing = frappe.db.exists(
#             "Contact",
#             {
#                 "first_name": doc.custom_principal_name,
#                 "mobile_no": doc.custom_principal_phone_number
#             }
#         )

#         if not existing:

#             contact = frappe.new_doc("Contact")

#             # Principal Details
#             contact.first_name = doc.custom_principal_name
#             contact.company_name = doc.company_name

#             # Designation
#             contact.designation = "Principal"

#             # Email
#             if doc.custom_principal_email_id:

#                 contact.append("email_ids", {
#                     "email_id": doc.custom_principal_email_id,
#                     "is_primary": 1
#                 })

#                 contact.email_id = doc.custom_principal_email_id

#             # Phone
#             if doc.custom_principal_phone_number:

#                 contact.append("phone_nos", {
#                     "phone": doc.custom_principal_phone_number,
#                     "is_primary_mobile_no": 1
#                 })

#                 contact.mobile_no = doc.custom_principal_phone_number

#             # Link with Lead
#             contact.append("links", {
#                 "link_doctype": "Lead",
#                 "link_name": doc.name,
#                 "link_title": doc.company_name
#             })

#             contact.insert(ignore_permissions=True)
            
#             frappe.msgprint(
#                 frappe._("Principal Contact created successfully for {0}").format(doc.custom_principal_name),
#                 alert=True,
#                 indicator="green"
#             )

#     # Create Parent Contact for Online Student Lead
#     if (
#         doc.custom_lead_category == "Online Student Lead"
#         and doc.custom_parent_name  # Parent name
#     ):

#         # Check if contact already exists
#         existing = frappe.db.exists(
#             "Contact",
#             {
#                 "first_name": doc.custom_parent_name,
#                 "mobile_no": doc.mobile_no
#             }
#         )

#         if not existing:

#             contact = frappe.new_doc("Contact")

#             # Parent Details
#             contact.first_name = doc.custom_parent_name
            
#             # Use school name as company name if available
#             if doc.custom_school_name:
#                 contact.company_name = doc.custom_school_name

#             # Designation
#             contact.designation = "Student Parent"

#             # Email
#             if doc.custom_parent_email_id or doc.email_id:

#                 email = doc.custom_parent_email_id or doc.email_id

#                 contact.append("email_ids", {
#                     "email_id": email,
#                     "is_primary": 1
#                 })

#                 contact.email_id = email

#             # Phone
#             if doc.mobile_no:

#                 contact.append("phone_nos", {
#                     "phone": doc.mobile_no,
#                     "is_primary_mobile_no": 1
#                 })

#                 contact.mobile_no = doc.mobile_no

#             # Link with Lead
#             contact.append("links", {
#                 "link_doctype": "Lead",
#                 "link_name": doc.name,
#                 "link_title": doc.lead_name
#             })

#             contact.insert(ignore_permissions=True)
            
#             frappe.msgprint(
#                 frappe._("Parent Contact created successfully for {0}").format(doc.custom_parent_name),
#                 alert=True,
#                 indicator="green"
#             )
#         else:
#             frappe.msgprint(
#                 frappe._("Parent Contact already exists for {0}").format(doc.custom_parent_name),
#                 alert=True,
#                 indicator="blue"
#             )


import frappe
from frappe import _
from frappe.model.mapper import get_mapped_doc

def on_update(doc, method=None):
    create_principal_contact(doc)
    create_parent_contact(doc)

def create_principal_contact(doc):
    if (
        doc.custom_lead_category != "School Lead"
        or not doc.custom_principal_name
    ):
        return

    principal_email = (
        doc.custom_principal_email_id or ""
    ).strip().lower()

    lead_email = (
        doc.email_id or ""
    ).strip().lower()

    # -----------------------------------
    # SAME EMAIL → UPDATE EXISTING CONTACT
    # -----------------------------------

    if principal_email and principal_email == lead_email:
        existing_contact = frappe.db.get_value(
            "Contact Email",
            {
                "email_id": principal_email
            },
            "parent"
        )

        if existing_contact:
            contact = frappe.get_doc(
                "Contact",
                existing_contact
            )

            contact.first_name = doc.custom_principal_name
            contact.designation = "Principal"

            if doc.custom_principal_phone_number:
                contact.mobile_no = doc.custom_principal_phone_number

            add_link_if_missing(contact, doc)

            contact.save(ignore_permissions=True)

            frappe.msgprint(
                _("Existing Contact updated as Principal"),
                alert=True,
                indicator="green"
            )

            return

    # -----------------------------------
    # SEPARATE CONTACT CREATION
    # -----------------------------------

    existing = frappe.db.exists(
        "Contact",
        {
            "first_name": doc.custom_principal_name,
            "mobile_no": doc.custom_principal_phone_number
        }
    )

    if existing:
        return

    contact = frappe.new_doc("Contact")

    contact.first_name = doc.custom_principal_name
    contact.company_name = doc.company_name
    contact.designation = "Principal"

    if principal_email:
        contact.append("email_ids", {
            "email_id": principal_email,
            "is_primary": 1
        })
        contact.email_id = principal_email

    if doc.custom_principal_phone_number:
        contact.append("phone_nos", {
            "phone": doc.custom_principal_phone_number,
            "is_primary_mobile_no": 1
        })
        contact.mobile_no = doc.custom_principal_phone_number

    contact.append("links", {
        "link_doctype": "Lead",
        "link_name": doc.name,
        "link_title": doc.company_name
    })

    contact.insert(ignore_permissions=True)

    frappe.msgprint(
        _("Principal Contact created successfully"),
        alert=True,
        indicator="green"
    )

def create_parent_contact(doc):
    if (
        doc.custom_lead_category != "Online Student Lead"
        or not doc.custom_parent_name
    ):
        return

    parent_email = (
        doc.custom_parent_email_id or ""
    ).strip().lower()

    student_email = (
        doc.custom_student_email_id or ""
    ).strip().lower()

    # -----------------------------------
    # SAME EMAIL → UPDATE EXISTING CONTACT
    # -----------------------------------

    if parent_email and parent_email == student_email:
        existing_contact = frappe.db.get_value(
            "Contact Email",
            {
                "email_id": parent_email
            },
            "parent"
        )

        if existing_contact:
            contact = frappe.get_doc(
                "Contact",
                existing_contact
            )

            contact.designation = "Student Parent"

            if doc.custom_parent_name:
                contact.last_name = doc.custom_parent_name

            add_link_if_missing(contact, doc)

            contact.save(ignore_permissions=True)

            frappe.msgprint(
                _("Existing Contact updated as Parent"),
                alert=True,
                indicator="green"
            )

            return

    # -----------------------------------
    # SEPARATE CONTACT CREATION
    # -----------------------------------

    existing = frappe.db.exists(
        "Contact",
        {
            "first_name": doc.custom_parent_name,
            "mobile_no": doc.mobile_no
        }
    )

    if existing:
        return

    contact = frappe.new_doc("Contact")

    contact.first_name = doc.custom_parent_name

    if doc.custom_school_name:
        contact.company_name = doc.custom_school_name

    contact.designation = "Student Parent"

    if parent_email:
        contact.append("email_ids", {
            "email_id": parent_email,
            "is_primary": 1
        })
        contact.email_id = parent_email

    if doc.mobile_no:
        contact.append("phone_nos", {
            "phone": doc.mobile_no,
            "is_primary_mobile_no": 1
        })
        contact.mobile_no = doc.mobile_no

    contact.append("links", {
        "link_doctype": "Lead",
        "link_name": doc.name,
        "link_title": doc.lead_name
    })

    contact.insert(ignore_permissions=True)

    frappe.msgprint(
        _("Parent Contact created successfully"),
        alert=True,
        indicator="green"
    )

def add_link_if_missing(contact, doc):
    exists = False

    for d in contact.links:
        if (
            d.link_doctype == "Lead"
            and d.link_name == doc.name
        ):
            exists = True
            break

    if not exists:
        contact.append("links", {
            "link_doctype": "Lead",
            "link_name": doc.name,
            "link_title": doc.lead_name or doc.company_name
        })


def validate_duplicate_lead(doc, method=None):
    fields_to_check = [
        {
            "field": "email_id",
            "label": "Email ID"
        },
        {
            "field": "custom_student_email_id",
            "label": "Student Email ID"
        },
        {
            "field": "mobile_no",
            "label": "Mobile Number"
        }
    ]

    for row in fields_to_check:

        fieldname = row["field"]
        label = row["label"]

        value = doc.get(fieldname)

        if not value:
            continue

        existing = frappe.db.get_value(
            "Lead",
            {
                fieldname: value,
                "name": ["!=", doc.name]
            },
            ["name", "lead_name"],
            as_dict=True
        )

        if existing:

            frappe.throw(
                _("{0} already exists in Lead: <b>{1}</b>")
                .format(label, existing.name),
                title=_("Duplicate Lead")
            )

@frappe.whitelist()
def make_customer(source_name, target_doc=None):
    def set_missing_values(source, target):
        if source.company_name:
            target.customer_type = "Company"
            target.customer_name = source.company_name
        else:
            target.customer_type = "Individual"
            target.customer_name = source.lead_name

        target.customer_group = source.market_segment or frappe.db.get_single_value(
            "Selling Settings", "customer_group"
        )

        lead_category_map = {
            "School Lead": "School Customer",
            "Online Student Lead": "Online Student Customer",
        }
        target.custom_customer_category = lead_category_map.get(
            source.custom_lead_category
        )

    doclist = get_mapped_doc(
        "Lead",
        source_name,
        {
            "Lead": {
                "doctype": "Customer",
                "field_map": {
                    "name": "lead_name",
                    "company_name": "customer_name",
                    "custom_school_name": "custom_school_name",
                    "custom_no_of_students": "custom_student_strength",
                    "custom_district": "custom_district",
                    "custom_taluka": "custom_taluka",
                    "custom_pincode": "custom_pincode",
                    "custom_stateprovince": "custom_state",
                    "custom_city2": "custom_city",
                    "country": "custom_country",
                },
            }
        },
        target_doc,
        set_missing_values,
    )

    return doclist