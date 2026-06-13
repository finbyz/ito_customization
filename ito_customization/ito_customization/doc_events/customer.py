import frappe

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