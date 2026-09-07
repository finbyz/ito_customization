# Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors
# For license information, please see license.txt

import secrets
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
def generate_student_portal_token(customer):
	if not customer:
		frappe.throw("Customer is required")

	token = secrets.token_urlsafe(16)
	base_url = frappe.utils.get_url()
	full_url = f"{base_url}/student-portal?token={token}"

	frappe.db.set_value("Customer", customer, {
		"custom_consent_token": token,
		"custom_student_registration_link": full_url,
		"custom_url": full_url
	})

	return {
		"token": token,
		"url": full_url
	}


def _get_customer_full_address(customer_doc):
	address_parts = []

	# 1. Check primary_address / customer_primary_address / Dynamic Link
	addr_name = customer_doc.get("customer_primary_address") or customer_doc.get("primary_address")
	if not addr_name:
		addr_links = frappe.get_all(
			"Dynamic Link",
			filters={"link_doctype": "Customer", "link_name": customer_doc.name, "parenttype": "Address"},
			pluck="parent"
		)
		if addr_links:
			addr_name = addr_links[0]

	if addr_name and frappe.db.exists("Address", addr_name):
		addr_doc = frappe.get_doc("Address", addr_name)
		for fld in ["address_line1", "address_line2", "custom_taluka", "custom_district", "city", "state", "pincode", "country"]:
			val = addr_doc.get(fld)
			if val and str(val).strip() and str(val).strip() not in address_parts:
				address_parts.append(str(val).strip())

	# 2. Check Customer direct custom fields
	if not address_parts:
		for fld in ["custom_addreess", "custom_ito_address_detail", "custom_taluka", "custom_district", "custom_city", "custom_state", "custom_stateprovince", "custom_pincode", "custom_country"]:
			val = customer_doc.get(fld)
			if val and str(val).strip() and str(val).strip() not in address_parts:
				address_parts.append(str(val).strip())

	# 3. Check child tables custom_ito_address_details / custom_address_details
	if not address_parts:
		child_tables = customer_doc.get("custom_ito_address_details") or customer_doc.get("custom_address_details") or []
		for row in child_tables:
			for fld in ["address_line1", "address_line2", "city", "state", "pincode"]:
				val = getattr(row, fld, None)
				if val and str(val).strip() and str(val).strip() not in address_parts:
					address_parts.append(str(val).strip())

	return ", ".join(address_parts) if address_parts else "-"


@frappe.whitelist(allow_guest=True)
def get_customer_info(query=None):
	"""Search customer by name or school code for public registration page"""
	if not query:
		return []

	customers = frappe.get_all(
		"Customer",
		filters=[
			["docstatus", "=", 0],
			["disabled", "=", 0],
		],
		or_filters=[
			["name", "like", f"%{query}%"],
			["customer_name", "like", f"%{query}%"],
			["custom_school_code", "like", f"%{query}%"],
			["custom_ito_school_code", "like", f"%{query}%"]
		],
		fields=["name", "customer_name", "custom_school_code", "custom_ito_school_code", "custom_city", "custom_addreess", "customer_primary_address"],
		limit=20
	)
	for cust in customers:
		cust_doc = frappe.get_doc("Customer", cust.name)
		cust["address"] = _get_customer_full_address(cust_doc)

	return customers


@frappe.whitelist(allow_guest=True)
def get_customer_exam_details(customer=None, school_code=None, token=None):
	if not customer and token:
		customer = frappe.db.get_value("Customer", {"custom_consent_token": token}, "name")

	if not customer and school_code:
		customer = frappe.db.get_value(
			"Customer",
			{"custom_school_code": school_code},
			"name"
		) or frappe.db.get_value(
			"Customer",
			{"custom_ito_school_code": school_code},
			"name"
		) or frappe.db.get_value(
			"Customer",
			{"name": school_code},
			"name"
		)

	if not customer:
		return {"success": False, "message": "Customer not found", "subjects": []}

	customer_doc = frappe.get_doc("Customer", customer)
	code = customer_doc.get("custom_school_code") or customer_doc.get("custom_ito_school_code") or customer_doc.name
	address = _get_customer_full_address(customer_doc)

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
		ay = customer_doc.get("custom_current_academic_year")
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
		"success": True,
		"customer": customer_doc.name,
		"customer_name": customer_doc.customer_name,
		"school_code": code,
		"address": address,
		"subjects": subject_details
	}


@frappe.whitelist(allow_guest=True)
def check_student_already_registered(email_id):
	if not email_id or not str(email_id).strip():
		return {"registered": False}

	clean_email = str(email_id).strip().lower()
	exists = frappe.db.sql(
		"SELECT name FROM `tabStudent Portal` WHERE LOWER(email_id) = %s LIMIT 1",
		(clean_email,)
	)

	return {
		"registered": bool(exists),
		"message": "Registration is only allowed once! You can order books. Contact support if you have any queries." if exists else ""
	}


@frappe.whitelist(allow_guest=True)
def create_student_portal_entry(data):
	if isinstance(data, str):
		data = frappe.parse_json(data)

	email = (data.get("email_id") or "").strip().lower()
	if email:
		exists = frappe.db.sql(
			"SELECT name FROM `tabStudent Portal` WHERE LOWER(email_id) = %s LIMIT 1",
			(email,)
		)
		if exists:
			return {
				"success": False,
				"already_registered": True,
				"message": "Registration is only allowed once! You can order books. Contact support if you have any queries."
			}

	doc = frappe.new_doc("Student Portal")
	doc.school_name = data.get("school_name") or data.get("customer")
	doc.school_code = data.get("school_code")
	doc.student_full_name = data.get("student_full_name")
	doc.parent_full_name = data.get("parent_full_name")
	doc.email_id = data.get("email_id")
	doc.mobile_number = data.get("mobile_number")
	doc.set("class", str(data.get("class") or data.get("class_grade") or ""))

	for item in data.get("selected_exams", []):
		doc.append("exam_list", {
			"school_subject": item.get("school_subject"),
			"abbr": item.get("abbr"),
			"check_ptxn": 1 if item.get("selected") or item.get("check_ptxn") else 0
		})

	doc.flags.ignore_permissions = True
	doc.insert()
	return {"success": True, "name": doc.name}


@frappe.whitelist(allow_guest=True)
def save_student_portal_book_order(data):
	if isinstance(data, str):
		data = frappe.parse_json(data)

	customer = data.get("customer") or data.get("school_name")
	school_code = data.get("school_code")
	student_full_name = data.get("student_full_name")
	parent_full_name = data.get("parent_full_name")
	email_id = data.get("email_id")
	mobile_number = data.get("mobile_number")
	class_grade = str(data.get("class") or data.get("class_grade") or "")
	book_items = data.get("book_items", [])

	if not customer and school_code:
		customer = frappe.db.get_value("Customer", {"custom_school_code": school_code}) or frappe.db.get_value("Customer", {"custom_ito_school_code": school_code}) or frappe.db.get_value("Customer", {"name": school_code})

	if not customer and not school_code and email_id:
		existing_sp = frappe.db.get_value("Student Portal", {"email_id": email_id}, ["school_name", "school_code"], as_dict=1)
		if existing_sp:
			customer = existing_sp.get("school_name")
			school_code = existing_sp.get("school_code")

	# Ensure School Subjects exist
	for item in book_items:
		subj = item.get("subject")
		if subj and not frappe.db.exists("School Subject", subj):
			try:
				sdoc = frappe.new_doc("School Subject")
				sdoc.name = subj
				sdoc.flags.ignore_permissions = True
				sdoc.insert()
			except Exception:
				pass

	# 1. Update or Create Student Portal Doc
	doc = None
	if email_id and str(email_id).strip():
		sp_name = frappe.db.sql(
			"SELECT name FROM `tabStudent Portal` WHERE LOWER(email_id) = %s ORDER BY creation DESC LIMIT 1",
			(str(email_id).strip().lower(),),
			as_dict=1
		)
		if sp_name:
			doc = frappe.get_doc("Student Portal", sp_name[0].name)

	if not doc:
		doc = frappe.new_doc("Student Portal")
		doc.school_name = customer
		doc.school_code = school_code
		doc.student_full_name = student_full_name
		doc.parent_full_name = parent_full_name
		doc.email_id = email_id
		doc.mobile_number = mobile_number
		doc.set("class", class_grade)

	# Set Books Selected in child table Books Selection CT
	if book_items:
		doc.set("books_selected", [])
		for item in book_items:
			wb = int(item.get("practice_workbook_110") or item.get("wb") or 0)
			guide = int(item.get("student_guide_220") or item.get("guide") or 0)
			pyqp = int(item.get("prev_year_paper_160") or item.get("pyqp") or 0)
			if wb or guide or pyqp:
				doc.append("books_selected", {
					"subject": item.get("subject"),
					"class_grade": item.get("class_grade") or class_grade,
					"practice_workbook_110": wb,
					"student_guide_220": guide,
					"prev_year_paper_160": pyqp,
				})

	doc.flags.ignore_permissions = True
	if doc.is_new():
		doc.insert()
	else:
		doc.save()

	# 2. ALSO Update or Create Books Selection Doc for Customer in ERPNext backend
	if customer and frappe.db.exists("Customer", customer):
		bs_name = frappe.db.get_value("Books Selection", {"customer": customer, "is_submitted": 0}) or frappe.db.get_value("Books Selection", {"customer": customer})
		if bs_name:
			bs_doc = frappe.get_doc("Books Selection", bs_name)
		else:
			bs_doc = frappe.new_doc("Books Selection")
			bs_doc.customer = customer
			bs_doc.order_date = frappe.utils.today()

		for item in book_items:
			wb = int(item.get("practice_workbook_110") or item.get("wb") or 0)
			guide = int(item.get("student_guide_220") or item.get("guide") or 0)
			pyqp = int(item.get("prev_year_paper_160") or item.get("pyqp") or 0)
			if wb or guide or pyqp:
				bs_doc.append("select_books", {
					"subject": item.get("subject"),
					"class_grade": item.get("class_grade") or class_grade,
					"practice_workbook_110": wb,
					"student_guide_220": guide,
					"prev_year_paper_160": pyqp,
				})

		bs_doc.flags.ignore_permissions = True
		if bs_doc.is_new():
			bs_doc.insert()
		else:
			bs_doc.save()

	return {"success": True, "name": doc.name, "message": "Book order saved successfully!"}




