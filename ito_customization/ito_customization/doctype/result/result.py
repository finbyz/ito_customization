# Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class Result(Document):
	def autoname(self):
		if not self.student_name:
			frappe.throw("Student Name is required")

		self.name = self.student_name
