import frappe
from frappe.model.document import Document

class YearlyExamDate(Document):
    pass

    # def validate(self):
    #     self.validate_unique_subject_per_year()

    # def validate_unique_subject_per_year(self):

    #     existing = frappe.db.exists(
    #         "Yearly Exam Date",
    #         {
    #             "subject": self.subject,
    #             "academic_year": self.academic_year,
    #             "name": ["!=", self.name]
    #         }
    #     )

    #     if existing:
    #         frappe.throw(
    #             f"""
    #             Subject <b>{self.subject}</b> is already configured
    #             for Academic Year <b>{self.academic_year}</b>.

    #             Existing Record: <b>{existing}</b>
    #             """,
    #             title="Duplicate Subject Configuration"
    #         )