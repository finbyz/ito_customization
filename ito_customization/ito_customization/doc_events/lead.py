import frappe


def on_update(doc, method=None):

    # Create Principal Contact for School Lead
    if (
        doc.custom_lead_category == "School Lead"
        and doc.custom_principal_name
    ):

        existing = frappe.db.exists(
            "Contact",
            {
                "first_name": doc.custom_principal_name,
                "mobile_no": doc.custom_principal_phone_number
            }
        )

        if not existing:

            contact = frappe.new_doc("Contact")

            # Principal Details
            contact.first_name = doc.custom_principal_name
            contact.company_name = doc.company_name

            # Designation
            contact.designation = "Principal"

            # Email
            if doc.custom_principal_email_id:

                contact.append("email_ids", {
                    "email_id": doc.custom_principal_email_id,
                    "is_primary": 1
                })

                contact.email_id = doc.custom_principal_email_id

            # Phone
            if doc.custom_principal_phone_number:

                contact.append("phone_nos", {
                    "phone": doc.custom_principal_phone_number,
                    "is_primary_mobile_no": 1
                })

                contact.mobile_no = doc.custom_principal_phone_number

            # Link with Lead
            contact.append("links", {
                "link_doctype": "Lead",
                "link_name": doc.name,
                "link_title": doc.company_name
            })

            contact.insert(ignore_permissions=True)
            
            frappe.msgprint(
                frappe._("Principal Contact created successfully for {0}").format(doc.custom_principal_name),
                alert=True,
                indicator="green"
            )

    # Create Parent Contact for Online Student Lead
    if (
        doc.custom_lead_category == "Online Student Lead"
        and doc.custom_parent_name  # Parent name
    ):

        # Check if contact already exists
        existing = frappe.db.exists(
            "Contact",
            {
                "first_name": doc.custom_parent_name,
                "mobile_no": doc.mobile_no
            }
        )

        if not existing:

            contact = frappe.new_doc("Contact")

            # Parent Details
            contact.first_name = doc.custom_parent_name
            
            # Use school name as company name if available
            if doc.custom_school_name:
                contact.company_name = doc.custom_school_name

            # Designation
            contact.designation = "Student Parent"

            # Email
            if doc.custom_parent_email_id or doc.email_id:

                email = doc.custom_parent_email_id or doc.email_id

                contact.append("email_ids", {
                    "email_id": email,
                    "is_primary": 1
                })

                contact.email_id = email

            # Phone
            if doc.mobile_no:

                contact.append("phone_nos", {
                    "phone": doc.mobile_no,
                    "is_primary_mobile_no": 1
                })

                contact.mobile_no = doc.mobile_no

            # Link with Lead
            contact.append("links", {
                "link_doctype": "Lead",
                "link_name": doc.name,
                "link_title": doc.lead_name
            })

            contact.insert(ignore_permissions=True)
            
            frappe.msgprint(
                frappe._("Parent Contact created successfully for {0}").format(doc.custom_parent_name),
                alert=True,
                indicator="green"
            )
        else:
            frappe.msgprint(
                frappe._("Parent Contact already exists for {0}").format(doc.custom_parent_name),
                alert=True,
                indicator="blue"
            )