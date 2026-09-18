import frappe
from frappe.model.document import Document


class RegisteredStudents(Document):

    def autoname(self):
        if not self.roll_no:
            frappe.throw("Roll No is required")

        self.name = self.roll_no