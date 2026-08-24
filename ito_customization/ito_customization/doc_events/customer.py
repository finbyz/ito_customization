

import frappe
import re


def sync_teachers(doc, method=None):

    # Teachers currently selected in child table
    current_teachers = {
        row.name1
        for row in doc.custom_school_teacher_details
        if row.name1
    }

    # Teachers already linked to this customer
    linked_teachers = frappe.get_all(
        "Teacher",
        filters={"school_name": doc.name},
        pluck="name"
    )

    # Remove school_name from deleted teachers
    for teacher_name in linked_teachers:
        if teacher_name not in current_teachers:
            teacher_doc = frappe.get_doc(
                "Teacher",
                teacher_name
            )
            teacher_doc.school_name = ""
            teacher_doc.save(
                ignore_permissions=True
            )

    # Update school_name for current teachers
    for teacher_name in current_teachers:
        if frappe.db.exists(
            "Teacher",
            teacher_name
        ):
            teacher_doc = frappe.get_doc(
                "Teacher",
                teacher_name
            )
            teacher_doc.school_name = doc.name
            teacher_doc.save(
                ignore_permissions=True
            )


# custom_state stores a plain state name (from frappe.boot.india_state_options).
# Used only for domestic (non-Overseas) School Customers.
STATE_NAME_TO_CODE = {
    "Andhra Pradesh": "AP",
    "Arunachal Pradesh": "AR",
    "Assam": "AS",
    "Bihar": "BR",
    "Chhattisgarh": "CT",
    "Goa": "GA",
    "Gujarat": "GJ",
    "Haryana": "HR",
    "Himachal Pradesh": "HP",
    "Jharkhand": "JH",
    "Karnataka": "KA",
    "Kerala": "KL",
    "Madhya Pradesh": "MP",
    "Maharashtra": "MH",
    "Manipur": "MN",
    "Meghalaya": "ML",
    "Mizoram": "MZ",
    "Nagaland": "NL",
    "Odisha": "OD",
    "Punjab": "PB",
    "Rajasthan": "RJ",
    "Sikkim": "SK",
    "Tamil Nadu": "TN",
    "Telangana": "TG",
    "Tripura": "TR",
    "Uttar Pradesh": "UP",
    "Uttarakhand": "UK",
    "West Bengal": "WB",
    "Andaman and Nicobar Islands": "AN",
    "Chandigarh": "CH",
    "Dadra and Nagar Haveli and Daman and Diu": "DN",
    "Delhi": "DL",
    "Jammu and Kashmir": "JK",
    "Ladakh": "LA",
    "Lakshadweep Islands": "LD",
    "Puducherry": "PY",
    "Other Territory": "OT",
}


def get_prefix(doc):
    """Resolve the naming prefix: country code for Overseas, state code otherwise."""

    if doc.gst_category == "Overseas":
        if not doc.custom_country:
            return None

        country_code = frappe.db.get_value("Country", doc.custom_country, "code")
        return country_code.upper() if country_code else None

    if not doc.custom_state:
        return None

    return STATE_NAME_TO_CODE.get(doc.custom_state)


def _target_field(doc):
    return "custom_school_code" if doc.custom_is_little_champ else "custom_ito_school_code"


# def _get_next_number(prefix):
#     """
#     Look at existing Customer names for this prefix and return the next
#     number in sequence (max + 1). This works regardless of whether the
#     previous record was created via the form, autoname(), or Data Import
#     with an explicit ID — because it reads the actual data, not a
#     separate naming-series counter. `for update` locks matching rows so
#     concurrent inserts for the same prefix don't generate the same number.
#     """
#     existing = frappe.db.sql(
#         """
#         select name from `tabCustomer`
#         where name like %s
#         for update
#         """,
#         (f"{prefix}-%",),
#     )

#     pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
#     max_num = 0

#     for (name,) in existing:
#         m = pattern.match(name)
#         if m:
#             num = int(m.group(1))
#             if num > max_num:
#                 max_num = num

#     return max_num + 1

def _get_next_number(prefix):
    """
    Look at existing Customer names for this prefix and return the next
    number in sequence (max + 1).
    """
    existing = frappe.db.sql(
        """
        select name from `tabCustomer`
        where name like %s
        for update
        """,
        (f"{prefix}%",),
    )

    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    max_num = 0

    for (name,) in existing:
        m = pattern.match(name)
        if m:
            num = int(m.group(1))
            if num > max_num:
                max_num = num

    return max_num + 1


# def _generate_code(prefix):
#     # Numbering is derived live from the max existing Customer name for
#     # this prefix (see _get_next_number) — not from a separate naming
#     # series counter — so it stays correct regardless of whether records
#     # were created via the form, autoname(), or Data Import.
#     next_num = _get_next_number(prefix)
#     # keeps the existing 5-digit zero-padded style: WB-01218, WB-01219...
#     return f"{prefix}-{next_num:05d}"

def _generate_code(prefix):
    # Numbering is derived live from the max existing Customer name for
    # this prefix (see _get_next_number) — not from a separate naming
    # series counter — so it stays correct regardless of whether records
    # were created via the form, autoname(), or Data Import.
    next_num = _get_next_number(prefix)
    # 4-digit zero-padded, no separator: AP0001, AP0002...
    return f"{prefix}{next_num:04d}"

def autoname(doc, method):
    """
    Runs on NEW Customers only, before validate/insert. Sets the document's
    actual ID (doc.name) to the generated code, and mirrors it into the
    display field (custom_school_code or custom_ito_school_code).

    Only applies to School Customers — State/Country and the school code
    they generate are irrelevant for Online Student Customers, who use
    Frappe's default naming instead.
    """
    if doc.custom_customer_category != "School Customer":
        return  # fall through to default Customer naming (e.g. by Customer Name)

    prefix = get_prefix(doc)

    if not prefix:
        frappe.throw(
            "Please set State (for domestic customers) or Country + GST "
            "Category = Overseas (for international customers) before "
            "saving — the Customer ID is generated from this."
        )

    code = _generate_code(prefix)
    doc.name = code
    doc.set(_target_field(doc), code)


def sync_school_code(doc, method):
    if doc.custom_customer_category != "School Customer":
        return

    if doc.is_new():
        return

    prefix = get_prefix(doc)
    if not prefix:
        return

    match = re.match(r"^([A-Za-z]+)\d+$", doc.name)
    current_prefix = match.group(1) if match else None

    if current_prefix == prefix:
        return  # ID already matches current state/category — nothing to do

    # ... rest unchanged

    relevant_change = (
        doc.has_value_changed("custom_state")
        or doc.has_value_changed("custom_country")
        or doc.has_value_changed("gst_category")
        or doc.has_value_changed("custom_is_little_champ")
    )

    if not relevant_change:
        return

    new_name = _generate_code(prefix)

    # ERPNext's Customer.after_rename() controller method automatically
    # overwrites customer_name to match the new document name whenever
    # Selling Settings > Customer Naming By = "Customer Name". That fires
    # as part of frappe.rename_doc() below, regardless of our own naming
    # scheme — so capture the real customer_name first and restore it
    # immediately after the rename completes.
    original_customer_name = doc.customer_name

    frappe.rename_doc(
        doc.doctype,
        doc.name,
        new_name,
        force=True,
    )

    frappe.db.set_value(doc.doctype, new_name, "customer_name", original_customer_name)
    doc.customer_name = original_customer_name

    target_field = _target_field(doc)
    frappe.db.set_value(doc.doctype, new_name, target_field, new_name)

    doc.name = new_name
    doc.set(target_field, new_name)


def validate(doc, method):
    """
    Server-side guarantee that custom_country stays consistent with
    gst_category — but only for School Customers, since State/Country
    are irrelevant and hidden for Online Student Customers (who store
    location on the Address instead). No manual commit here — Frappe
    commits automatically at the end of the request/transaction.
    """
    if doc.custom_customer_category != "School Customer":
        return

    if doc.gst_category == "Overseas":
        if doc.custom_country == "India":
            doc.custom_country = ""
    else:
        if doc.custom_country != "India":
            doc.custom_country = "India"