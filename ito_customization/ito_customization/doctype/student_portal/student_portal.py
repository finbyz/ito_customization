# Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class StudentPortal(Document):
	def before_save(self):
		self.update_customer_details()

	def update_customer_details(self):
		if not self.school_name:
			return

		if not self.school_code:
			code = frappe.db.get_value(
				"Customer", self.school_name, "custom_school_code"
			) or frappe.db.get_value(
				"Customer", self.school_name, "custom_ito_school_code"
			)
			if code:
				self.school_code = code

		if not self.exam_list:
			details = get_customer_exam_details(self.school_name)
			for item in details.get("subjects", []):
				self.append("exam_list", {
					"school_subject": item.get("school_subject"),
					"abbr": item.get("abbr")
				})


@frappe.whitelist()
def get_customer_exam_details(customer):
	if not customer:
		return {"school_code": "", "subjects": []}

	school_code = frappe.db.get_value(
		"Customer", customer, ["custom_school_code", "custom_ito_school_code"], as_dict=True
	) or {}
	code = school_code.get("custom_school_code") or school_code.get("custom_ito_school_code") or ""

	subjects = []
	seen = set()

	# 1. Fetch from Exams Summary linked to customer
	exams_summaries = frappe.get_all(
		"Exams Summary",
		filters={"customer": customer},
		fields=["name", "exam_detail"]
	)

	for es in exams_summaries:
		es_doc = frappe.get_doc("Exams Summary", es.name)
		for row in getattr(es_doc, "exam_summary", []):
			if getattr(row, "subject", None) and row.subject not in seen:
				seen.add(row.subject)
				subjects.append(row.subject)

		if es.exam_detail and frappe.db.exists("Yearly Exam Date", es.exam_detail):
			yed_doc = frappe.get_doc("Yearly Exam Date", es.exam_detail)
			for row in getattr(yed_doc, "target_dates", []):
				if getattr(row, "subject", None) and row.subject not in seen:
					seen.add(row.subject)
					subjects.append(row.subject)

	# 2. Fallback to active/latest Yearly Exam Date if no subjects found in Exams Summary
	if not subjects:
		ay = frappe.db.get_value("Customer", customer, "custom_current_academic_year")
		yed_filters = {"docstatus": 1}
		if ay:
			normalized_ay = ay
			if not normalized_ay.startswith("AY-"):
				parts = normalized_ay.replace("-", "/").split("/")
				if len(parts) == 2:
					normalized_ay = f"AY-{parts[0]}/{parts[1]}"
			yed_filters["academic_year"] = normalized_ay

		yearly_exams = frappe.get_all(
			"Yearly Exam Date",
			filters=yed_filters,
			fields=["name"],
			order_by="creation desc",
			limit=1
		)
		if not yearly_exams:
			yearly_exams = frappe.get_all(
				"Yearly Exam Date",
				filters={"docstatus": 1},
				fields=["name"],
				order_by="creation desc",
				limit=1
			)
		if yearly_exams:
			yed_doc = frappe.get_doc("Yearly Exam Date", yearly_exams[0].name)
			for row in getattr(yed_doc, "target_dates", []):
				if getattr(row, "subject", None) and row.subject not in seen:
					seen.add(row.subject)
					subjects.append(row.subject)

	subject_details = []
	for subj in subjects:
		abbr = frappe.db.get_value("School Subject", subj, "abbr") or ""
		subject_details.append({
			"school_subject": subj,
			"abbr": abbr
		})

	return {
		"school_code": code,
		"subjects": subject_details
	}

