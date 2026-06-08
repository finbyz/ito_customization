# Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ExamsSummary(Document):

	def validate(self):
		if not self.customer or not self.exam_detail:
			return

		existing = frappe.db.exists(
			"Exams Summary",
			{
				"customer": self.customer,
				"exam_detail": self.exam_detail,
				"name": ["!=", self.name]
			}
		)

		if existing:
			frappe.throw(
				f"Exam Detail <b>{self.exam_detail}</b> is already assigned to Customer <b>{self.customer}</b>. "
				"The same Exam Detail cannot be added twice for the same customer."
			)