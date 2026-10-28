
import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import make_autoname


class RegisteredStudents(Document):
    def autoname(self):
        if not self.student_name or not self.student_name.strip():
            frappe.throw(_("Student Name is required."))

        self.name = self.student_name.strip()


@frappe.whitelist(methods=["POST"])
def generate_roll_numbers_for_hall_tickets(school, student_class, names):
    if not frappe.has_permission("Registered Students", "write"):
        frappe.throw(
            _("You do not have permission to update Registered Students."),
            frappe.PermissionError,
        )

    if not isinstance(school, str) or not school.strip():
        frappe.throw(_("Select a valid School."))

    if not isinstance(student_class, str) or not student_class.strip():
        frappe.throw(_("Select both School and Class before generating roll numbers."))

    school = school.strip()
    student_class = student_class.strip()

    if isinstance(names, str):
        try:
            names = json.loads(names)
        except json.JSONDecodeError:
            frappe.throw(_("The selected student list is invalid."))

    if not isinstance(names, list) or not names:
        frappe.throw(_("Select at least one student."))

    if any(not isinstance(name, str) or not name for name in names):
        frappe.throw(_("The selected student list contains an invalid record."))

    school_doc = frappe.get_doc("Customer", school)
    school_doc.check_permission("read")

    ito_code = (school_doc.get("custom_ito_school_code") or "").strip().upper()
    if not ito_code:
        frappe.throw(
            _("The selected school needs an ITO School Code to generate roll numbers.")
        )

    student_docs = []

    for name in dict.fromkeys(names):
        student = frappe.get_doc("Registered Students", name)
        student.check_permission("write")

        if student.school != school or student.get("class") != student_class:
            frappe.throw(
                _("All selected students must match the filtered School and Class.")
            )

        if student.get("not_registered"):
            frappe.throw(_("Roll numbers cannot be generated for inactive students."))

        student_docs.append(student)

    student_docs.sort(
        key=lambda student: (
            (student.student_name or "").strip().casefold(),
            (student.serial_number or "").strip().casefold(),
            student.name.casefold(),
        )
    )

    class_code = student_class.lower().replace("class", "").strip()
    if class_code.isdigit():
        class_code = class_code.zfill(2)

    series = f"{ito_code}{class_code}.###"

    for student in student_docs:
        if not student.roll_no:
            student.roll_no = make_autoname(series)
            student.save()

    return {"names": [student.name for student in student_docs]}
