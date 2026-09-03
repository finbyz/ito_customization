import frappe
from frappe import _

REGISTRATION_DOCTYPE = "Parent Consent Registration"
BOOK_ORDER_DOCTYPE = "Parent Consent"


def _resolve_registration_by_token(token):
    """
    `token` is the per-Registration `book_order_token` field on
    Parent Consent Registration (generated at Registration-save time).
    """
    reg_name = frappe.db.get_value(REGISTRATION_DOCTYPE, {"book_order_token": token}, "name")
    if not reg_name:
        frappe.throw(_("This link is invalid or has expired."))
    return frappe.get_doc(REGISTRATION_DOCTYPE, reg_name)


@frappe.whitelist()
def get_book_order_prefill(token):
    reg = _resolve_registration_by_token(token)
    existing = frappe.db.get_value(BOOK_ORDER_DOCTYPE, {"parent_consent_registration": reg.name}, "name")
    school_name = frappe.db.get_value("Customer", reg.customer, "customer_name") if reg.customer else None
    gender_map = {"Boy": "male", "Girl": "female"}

    return {
        "book_order_placed": bool(existing),
        "is_little_champ": bool(reg.is_little_champ),
        "selected_class": reg.get("class"),
        "student_profile": {
            "student_name": reg.student_name,
            "gender": gender_map.get(reg.gender, reg.gender),
            "school_name": school_name,
            "class_grade": reg.get("class"),
            "section": reg.section,
            "parent_name": reg.parent_name,
            "parent_mobile": reg.mobile_no,
            "city": reg.city,
            "parent_email": reg.email,  # StudentProfile contract unchanged
        },
    }


@frappe.whitelist()
def initiate_book_order_payment(token, selections):
    reg = _resolve_registration_by_token(token)

    if frappe.db.exists(BOOK_ORDER_DOCTYPE, {"parent_consent_registration": reg.name}):
        frappe.throw(_("A book order has already been placed for this registration."))

    # --- build amount from `selections`, create Razorpay order via the
    # "ITO Book Order" Multi Company Razorpay Settings entry, create the
    # pending Integration Request, return {key_id, order_id, amount,
    # currency, company_name, description, name/email, integration_request}
    # — mirror your existing exam-fee initiate function.


@frappe.whitelist()
def confirm_book_order_payment(integration_request, razorpay_payment_id, razorpay_order_id, razorpay_signature):
    # --- verify signature, mark Integration Request completed, create
    # Sales Invoice + Payment Entry as your existing confirm function does,
    # then on success:

    pending = frappe.get_doc("Integration Request", integration_request)  # adjust to your actual pending-state source
    pending = frappe.parse_json(pending.data)

    if not frappe.db.exists(BOOK_ORDER_DOCTYPE, {"parent_consent_registration": pending["parent_consent_registration"]}):
        reg = frappe.get_doc(REGISTRATION_DOCTYPE, pending["parent_consent_registration"])
        bo = frappe.new_doc(BOOK_ORDER_DOCTYPE)
        bo.parent_consent_registration = reg.name
        bo.customer = reg.customer
        bo.is_little_champ = reg.is_little_champ
        bo.student_name = reg.student_name
        bo.parent_name = reg.parent_name
        bo.email = reg.email            # note the field-name mismatch
        bo.city = reg.city
        bo.gender = reg.gender
        bo.mobile_no = reg.mobile_no
        bo.section = reg.section
        bo.set("class", reg.get("class"))
        bo.total_amount = pending["amount"]
        bo.sales_invoice = invoice_name          # set above where Sales Invoice is created
        bo.payment_entry = transaction.payment_entry  # set above where Payment Entry is created

        for sel in pending["selections"]:
            sub_code = sel.get("subject_code")
            subject_name = sub_code
            if not frappe.db.exists("School Subject", sub_code):
                found_sub = frappe.db.get_value("School Subject", {"name": ["like", f"%({sub_code})%"]}, "name")
                if found_sub:
                    subject_name = found_sub
            bo.append("book_order_selection", {
                "subject": subject_name,
                "workbook": 1 if sel.get("wb") else 0,
                "student_guide": 1 if sel.get("sg") else 0,
                "past_paper": 1 if sel.get("yp") else 0,
                "text_book": 1 if sel.get("tb") else 0,
            })
        bo.insert(ignore_permissions=True)
        bo.submit()
        book_order_name = bo.name

    return {"paid": True, "payment_entry": transaction.payment_entry}