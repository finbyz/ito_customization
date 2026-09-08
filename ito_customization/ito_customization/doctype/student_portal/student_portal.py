# Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors
# For license information, please see license.txt

import secrets
import frappe
from frappe.model.document import Document
from frappe.utils import flt, cint


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

	customer_doc = frappe.get_doc("Customer", customer)
	code = customer_doc.get("custom_school_code") or customer_doc.get("custom_ito_school_code") or customer_doc.name

	token = secrets.token_urlsafe(16)
	base_url = frappe.utils.get_url()
	full_url = f"{base_url}/student-portal?token={token}&school_code={code}"

	try:
		for field in ["custom_consent_token", "custom_student_registration_link", "custom_url"]:
			if hasattr(customer_doc, field) or frappe.db.has_column("Customer", field):
				val = token if field == "custom_consent_token" else full_url
				frappe.db.set_value("Customer", customer, field, val, update_modified=False)
		frappe.db.commit()
	except Exception as e:
		frappe.log_error(f"Error saving student portal token for {customer}: {e}")

	return {
		"token": token,
		"url": full_url
	}


def _get_customer_full_address(customer_doc):
	address_parts = []

	def _add_part(val):
		if val and str(val).strip():
			s = str(val).strip()
			if s not in address_parts and s != "-":
				address_parts.append(s)

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
			_add_part(addr_doc.get(fld))

	# 2. Check Customer direct custom fields
	for fld in ["custom_addreess", "custom_ito_address_detail", "custom_taluka", "custom_district", "custom_city", "custom_state", "custom_stateprovince", "custom_pincode", "custom_country"]:
		_add_part(customer_doc.get(fld))

	# 3. Check child tables custom_ito_address_details / custom_address_details
	child_tables = customer_doc.get("custom_ito_address_details") or customer_doc.get("custom_address_details") or []
	for row in child_tables:
		for fld in ["address_line1", "address_line2", "city", "state", "pincode"]:
			_add_part(getattr(row, fld, None))

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
		customer = (
			frappe.db.get_value("Customer", {"custom_consent_token": token}, "name")
			or frappe.db.get_value("Customer", {"custom_url": ["like", f"%{token}%"]}, "name")
			or frappe.db.get_value("Customer", {"custom_student_registration_link": ["like", f"%{token}%"]}, "name")
		)

	if not customer and school_code:
		customer = (
			frappe.db.get_value("Customer", {"custom_school_code": school_code}, "name")
			or frappe.db.get_value("Customer", {"custom_ito_school_code": school_code}, "name")
			or frappe.db.get_value("Customer", {"name": school_code}, "name")
		)

	if not customer and token:
		customer = (
			frappe.db.get_value("Customer", {"custom_school_code": token}, "name")
			or frappe.db.get_value("Customer", {"custom_ito_school_code": token}, "name")
			or frappe.db.get_value("Customer", {"name": token}, "name")
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

	from ito_customization.ito_customization.api import get_registration_fee
	fee_info = get_registration_fee("Student Portal Registration")
	registration_fee_rate = flt(fee_info.get("rate_inr") or fee_info.get("rate_usd") or 150) if fee_info and fee_info.get("success") else 150.0

	return {
		"success": True,
		"customer": customer_doc.name,
		"customer_name": customer_doc.customer_name,
		"school_code": code,
		"address": address,
		"subjects": subject_details,
		"registration_fee_rate": registration_fee_rate
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


class IgnorePermissionsContext:
	def __enter__(self):
		self.orig_has_permission = frappe.has_permission
		self.orig_permissions_has_permission = getattr(frappe, "permissions", None) and getattr(frappe.permissions, "has_permission", None)
		self.orig_ignore = frappe.flags.ignore_permissions
		self.orig_ignore_account = getattr(frappe.flags, "ignore_account_permission", False)

		def custom_has_permission(doctype, ptype="read", doc=None, user=None, **kwargs):
			if doctype in ("Account", "Sales Invoice", "Payment Entry", "Customer", "Item", "Company", "Student Portal", "Books Selection"):
				return True
			return self.orig_has_permission(doctype, ptype=ptype, doc=doc, user=user, **kwargs)

		frappe.has_permission = custom_has_permission
		if hasattr(frappe, "permissions"):
			frappe.permissions.has_permission = custom_has_permission

		frappe.flags.ignore_permissions = True
		frappe.flags.ignore_account_permission = True
		return self

	def __exit__(self, exc_type, exc_val, exc_tb):
		frappe.has_permission = self.orig_has_permission
		if self.orig_permissions_has_permission and hasattr(frappe, "permissions"):
			frappe.permissions.has_permission = self.orig_permissions_has_permission

		frappe.flags.ignore_permissions = self.orig_ignore
		frappe.flags.ignore_account_permission = self.orig_ignore_account


def create_payment_entry_for_student_portal_registration(doc, exam_count):
	with IgnorePermissionsContext():
		try:
			if not exam_count or exam_count <= 0:
				return None, None

			customer = doc.school_name
			if not customer and doc.school_code:
				customer = frappe.db.get_value("Customer", {"custom_school_code": doc.school_code}) or frappe.db.get_value("Customer", {"custom_ito_school_code": doc.school_code}) or frappe.db.get_value("Customer", {"name": doc.school_code})

			if not customer or not frappe.db.exists("Customer", customer):
				return None, None

			# Fetch dynamic fee rate for "Student Portal Registration"
			from ito_customization.ito_customization.api import get_registration_fee
			fee_info = get_registration_fee("Student Portal Registration")
			item_code = fee_info.get("item_code") if fee_info and fee_info.get("success") else None
			rate = flt(fee_info.get("rate_inr") or fee_info.get("rate_usd") or 150) if fee_info and fee_info.get("success") else 150.0

			if not item_code:
				item_code = frappe.db.get_value("Item", {"custom_is_registration_item": 1, "custom_form_name": "Student Portal Registration", "disabled": 0}, "name")

			if not item_code:
				item_code = frappe.db.get_value("Item", {"name": "Student Portal Registration Fee"}, "name")
				if not item_code:
					item_doc = frappe.get_doc({
						"doctype": "Item",
						"item_code": "Student Portal Registration Fee",
						"item_name": "Student Portal Registration Fee",
						"item_group": "Fee Component",
						"stock_uom": "Nos",
						"is_stock_item": 0,
						"standard_rate": rate,
						"custom_is_registration_item": 1,
						"custom_form_name": "Student Portal Registration",
						"uoms": [{"uom": "Nos", "conversion_factor": 1}]
					})
					item_doc.flags.ignore_permissions = True
					item_doc.insert(ignore_permissions=True)
					item_code = item_doc.name

			company = frappe.db.get_single_value("Global Defaults", "default_company") or "Indian Talent Olympiad"
			if not frappe.db.exists("Company", company):
				companies = frappe.get_all("Company", limit=1)
				if companies:
					company = companies[0].name

			# 1. Create Sales Invoice
			si = frappe.new_doc("Sales Invoice")
			si.customer = customer
			si.company = company
			si.append("items", {
				"item_code": item_code,
				"qty": exam_count,
				"rate": rate
			})
			si.flags.ignore_permissions = True
			si.flags.ignore_mandatory = True
			si.insert(ignore_permissions=True)
			si.submit()

			# 2. Create Payment Entry
			from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
			pe = get_payment_entry("Sales Invoice", si.name)
			pe.reference_no = doc.name
			pe.reference_date = frappe.utils.today()
			pe.remarks = f"Payment Entry for Student Portal Registration: {doc.student_full_name} ({doc.name})"
			pe.flags.ignore_permissions = True
			pe.flags.ignore_mandatory = True
			pe.insert(ignore_permissions=True)
			pe.submit()

			return si.name, pe.name
		except Exception as e:
			frappe.log_error(frappe.get_traceback(), "Student Portal Payment Entry Error")
			return None, None


@frappe.whitelist(allow_guest=True)
def create_student_portal_entry(data):
	with IgnorePermissionsContext():
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

		exam_count = 0
		for item in data.get("selected_exams", []):
			is_sel = 1 if item.get("selected") or item.get("check_ptxn") else 0
			if is_sel:
				exam_count += 1
			doc.append("exam_list", {
				"school_subject": item.get("school_subject"),
				"abbr": item.get("abbr"),
				"check_ptxn": is_sel
			})

		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)

		si_name, pe_name = create_payment_entry_for_student_portal_registration(doc, exam_count)

		return {"success": True, "name": doc.name, "sales_invoice": si_name, "payment_entry": pe_name}


@frappe.whitelist(allow_guest=True)
def save_student_portal_book_order(data):
	with IgnorePermissionsContext():
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
					sdoc.insert(ignore_permissions=True)
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
			doc.insert(ignore_permissions=True)
		else:
			doc.save(ignore_permissions=True)

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
				bs_doc.insert(ignore_permissions=True)
			else:
				bs_doc.save(ignore_permissions=True)

		return {"success": True, "name": doc.name, "message": "Book order saved successfully!"}
