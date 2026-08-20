import frappe
from frappe.model.document import Document
from frappe.model.naming import make_autoname


class BooksSelection(Document):
    def autoname(self):
        if not self.customer:
            frappe.throw("Customer is required before creating Books Selection")

        self.name = make_autoname(f"{self.customer}-.####")