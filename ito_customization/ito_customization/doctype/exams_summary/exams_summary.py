# Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ExamsSummary(Document):

	def validate(self):
		if not self.customer or not self.exam_detail:
			return

		# A WOF school keeps a Chitrakala summary (is_wof = 0) and a WOF Olympiad summary (is_wof = 1),
		# often on the same Exam Detail, so a duplicate is only one of the same kind.
		existing = frappe.db.exists(
			"Exams Summary",
			{
				"customer": self.customer,
				"exam_detail": self.exam_detail,
				"is_wof": self.get("is_wof") or 0,
				"is_little_champ": self.get("is_little_champ") or 0,
				"name": ["!=", self.name]
			}
		)

		if existing:
			frappe.throw(
				f"Exam Detail <b>{self.exam_detail}</b> is already assigned to Customer <b>{self.customer}</b>. "
				"The same Exam Detail cannot be added twice for the same customer."
			)