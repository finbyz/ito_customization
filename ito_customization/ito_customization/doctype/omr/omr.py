# Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class OMR(Document):
	def autoname(self):
		if not self.school:
			frappe.throw("School is required")

		self.name = self.school