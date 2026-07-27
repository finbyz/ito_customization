# Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
import re
from frappe.model.document import Document
from frappe.model.naming import make_autoname

class ParentConsent(Document):
    def autoname(self):
        prefix = (self.customer)
        self.name = make_autoname(f"{prefix}-.#####")