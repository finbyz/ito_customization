# -*- coding: utf-8 -*-
# Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors

from functools import reduce
import frappe
from frappe import _, scrub
import json
import csv
import base64
from frappe.utils import cint, flt
import io

def ensure_subject_exists(subject_name):
	"""Create a School Subject if it doesn't exist"""
	if not frappe.db.exists("School Subject", subject_name):
		subject = frappe.new_doc("School Subject")
		subject.name = subject_name
		try:
			subject.insert(ignore_permissions=True)
		except frappe.DuplicateEntryError:
			pass


@frappe.whitelist(allow_guest=True)
def save_ito_registration(registration_data):
	try:
		data = json.loads(registration_data) if isinstance(registration_data, str) else registration_data
		frappe.log_error("Registration Data", json.dumps(data, indent=2))
		
		original_flag = frappe.flags.ignore_permissions
		frappe.flags.ignore_permissions = True
		
		try:
			school_info = data.get("school_info", {})
			gstin = school_info.get("gst_no", "").strip().upper()
			if gstin and len(gstin) != 15:
				gstin = ""
			school_info["gst_no"] = gstin

			# FIX: Normalize school_code from both possible field names
			# Frontend might send 'ito_school_code' instead of 'school_code'
			if not school_info.get("school_code") and school_info.get("ito_school_code"):
				school_info["school_code"] = school_info.get("ito_school_code")

			# Create/update customer - saves to DB session
			customer = create_or_update_customer(school_info)
			customer_doc = frappe.get_doc("Customer", customer)
			if hasattr(customer_doc, "custom_registration_date"):
				customer_doc.custom_registration_date = frappe.utils.nowdate()
				customer_doc.save(ignore_permissions=True)
			
			# Create/update address - saves to DB session
			create_or_update_address(customer, school_info)

			# Create/update principal if provided
			if data.get("coordinators", {}).get("principal"):
				create_or_update_principal(data["coordinators"]["principal"], customer)

			# Create/update teachers - saves to DB session
			create_or_update_teachers(data.get("coordinators", {}), customer)

			# Save exams data
			exams_data = data.get("exams", {})
			if exams_data:
				es_name = frappe.db.get_value("Exams Summary", {"customer": customer})
				if es_name:
					es_doc = frappe.get_doc("Exams Summary", es_name)
					es_doc.exam_summary = []

					subject_map = {
						"IDO": "Drawing Olympiad (IDO)",
						"NESO": "Essay Olympiad (NESO)",
						"EIO": "English Olympiad (EIO)",
						"IMO": "Maths Olympiad (IMO)",
						"ISO": "Science Olympiad (ISO)",
						"GKIO": "General Knowledge (GKIO)",
						"ICO": "Computer Olympiad (ICO)",
						"NSSO": "Social Studies (NSSO)",
						"NHO": "Hindi Olympiad (NHO)",
						"NLRO": "Logical Reasoning (NLRO)",
						"CIO": "Commerce Olympiad (CIO)"
					}

					for subject_code, rows in exams_data.items():
						subject_name = subject_map.get(subject_code, subject_code)
						for row in rows:
							es_doc.append("exam_summary", {
								"subject": subject_name,
								"class": row.get("class"),
								"teacher_name": row.get("teacher_name"),
								"whatsapp_no": row.get("whatsapp"),
								"no_of_students": row.get("students")
							})

					# Save exams - persists to DB session
					es_doc.save(ignore_permissions=True)

			# NO explicit commit - let Frappe handle it
			return {"success": True, "customer": customer}

		finally:
			frappe.flags.ignore_permissions = original_flag
			
	except Exception as e:
		# Reset flag but don't call rollback
		frappe.flags.ignore_permissions = False        
		frappe.log_error(frappe.get_traceback(), "ITO Registration Error")
		return {"success": False, "message": str(e)}

@frappe.whitelist(allow_guest=True)
def save_registration_step():
	if frappe.session.user == "Guest":
		data = frappe.request.get_json() or {}
		return {"success": True, "step": cint(data.get("step"))}

	try:
		data = frappe.request.get_json() or {}
		step = cint(data.get("step"))
		original_flag = frappe.flags.ignore_permissions
		frappe.flags.ignore_permissions = True

		try:
			if step == 1:
				school_info = data.get("school_info", {})
				gstin = school_info.get("gst_no", "").strip().upper()
				if gstin and len(gstin) != 15:
					gstin = ""
				school_info["gst_no"] = gstin

				customer = frappe.cache().get_value(f"ito_customer_{frappe.session.user}")
				if not customer:
					customer = frappe.db.get_value("Portal User", {"user": frappe.session.user}, "parent")
				
				if not customer:
					frappe.throw("Please save School Information first.")

				customer_doc = frappe.get_doc("Customer", customer)
				customer_doc.customer_name = school_info.get("school_name", customer_doc.customer_name)
				customer_doc.custom_ito_school_code = school_info.get("school_code", customer_doc.custom_ito_school_code)
				customer_doc.custom_board = school_info.get("board", customer_doc.custom_board)
				customer_doc.custom_student_strength = school_info.get("student_strength", customer_doc.custom_student_strength)
				customer_doc.mobile_no = school_info.get("school_phone1", customer_doc.mobile_no)
				customer_doc.email_id = school_info.get("school_email", customer_doc.email_id)
				customer_doc.gstin = school_info.get("gst_no", customer_doc.gstin)
				
				# Save customer - this persists to DB session
				customer_doc.save(ignore_permissions=True)

				# Save address - this persists to DB session
				create_or_update_address(customer, school_info)
				
				# Cache the customer name
				frappe.cache().set_value(f"ito_customer_{frappe.session.user}", customer)

			elif step == 2:
				customer = frappe.cache().get_value(f"ito_customer_{frappe.session.user}")
				if not customer and frappe.session.user != "Guest":
					customer = frappe.db.get_value("Portal User", {"user": frappe.session.user}, "parent")
				if not customer:
					frappe.throw("Please save School Information first.")

				coordinators = data.get("coordinators", {})
				frappe.log_error(title="STEP 2 COORDINATORS", message=frappe.as_json(coordinators))
				
				# Save teachers - persists to DB session
				create_or_update_teachers(coordinators, customer)

			elif step == 3:
				customer = frappe.cache().get_value(f"ito_customer_{frappe.session.user}")
				if not customer and frappe.session.user != "Guest":
					customer = frappe.db.get_value("Portal User", {"user": frappe.session.user}, "parent")
				if not customer:
					frappe.throw("Please save School Information first.")

				exams_data = data.get("exams", {})
				frappe.log_error(title="STEP 3 EXAMS DATA", message=frappe.as_json(exams_data))

				if exams_data:
					es_name = frappe.db.get_value("Exams Summary", {"customer": customer})
					if es_name:
						es_doc = frappe.get_doc("Exams Summary", es_name)
						es_doc.exam_summary = []

						subject_map = {
							"IDO": "Drawing Olympiad (IDO)",
							"NESO": "Essay Olympiad (NESO)",
							"EIO": "English Olympiad (EIO)",
							"IMO": "Maths Olympiad (IMO)",
							"ISO": "Science Olympiad (ISO)",
							"GKIO": "General Knowledge (GKIO)",
							"ICO": "Computer Olympiad (ICO)",
							"NSSO": "Social Studies (NSSO)",
							"NHO": "Hindi Olympiad (NHO)",
							"NLRO": "Logical Reasoning (NLRO)",
							"CIO": "Commerce Olympiad (CIO)"
						}

						for subject_code, rows in exams_data.items():
							subject_name = subject_map.get(subject_code, subject_code)
							for row in rows:
								es_doc.append("exam_summary", {
									"subject": subject_name,
									"class": row.get("class"),
									"teacher_name": row.get("teacher_name"),
									"whatsapp_no": row.get("whatsapp"),
									"no_of_students": row.get("students")
								})

						# Save exams - persists to DB session
						es_doc.save(ignore_permissions=True)

			elif step == 4:
				customer = frappe.cache().get_value(f"ito_customer_{frappe.session.user}")
				if not customer and frappe.session.user != "Guest":
					customer = frappe.db.get_value("Portal User", {"user": frappe.session.user}, "parent")
				if not customer:
					frappe.throw("Please save School Information first.")

				payment = data.get("payment", {})
				mode = payment.get("mode")
				# Razorpay payments are recorded via the Sales Invoice/Payment Entry
				# created by registration_payment.py - this is just an audit trail for
				# the self-declared manual modes (DD/NEFT/Cash/UPI).
				if mode and mode != "razorpay":
					frappe.get_doc("Customer", customer).add_comment(
						"Info",
						f"ITO Registration fee reported as paid via {mode}: "
						f"{frappe.as_json(payment.get('payment_details', {}))}",
					)

			else:
				frappe.throw(f"Invalid step: {step}")

			# NO explicit commit - let Frappe handle it
			# NO explicit rollback - let Frappe handle exceptions
			return {"success": True, "step": step}

		finally:
			frappe.flags.ignore_permissions = original_flag

	except Exception as e:
		# Reset flag but don't call rollback
		frappe.flags.ignore_permissions = False
		frappe.log_error(frappe.get_traceback(), "Save Registration Step Error")
		return {"success": False, "message": str(e)}


def create_or_update_customer(school_info):
	school_name = school_info.get("school_name")
	if not school_name:
		frappe.throw("School Name is mandatory")

	existing = frappe.db.exists("Customer", {"customer_name": school_name})
	if existing:
		customer = frappe.get_doc("Customer", existing)
	else:
		customer = frappe.new_doc("Customer")
		customer.customer_name = school_name

	customer.customer_type = "Company"

	if hasattr(customer, "email_id"):
		customer.email_id = school_info.get("school_email")

	# FIX: Handle both 'school_code' and 'ito_school_code'
	school_code = school_info.get("school_code") or school_info.get("ito_school_code")
	if school_code:
		customer.custom_ito_school_code = school_code

	if hasattr(customer, "mobile_no"):
		customer.mobile_no = school_info.get("school_phone1")

	if hasattr(customer, "gstin"):
		customer.gstin = school_info.get("gst_no")

	if hasattr(customer, "custom_board"):
		customer.custom_board = school_info.get("board")

	customer.custom_student_strength = cint(school_info.get("student_strength") or 0)
	
	# Remove this duplicate line since we handle it above
	# if hasattr(customer, "custom_ito_school_code"):
	#     customer.custom_ito_school_code = school_info.get("ito_school_code")

	if hasattr(customer, "custom_customer_category"):
		customer.custom_customer_category = "School Customer"

	if hasattr(customer, "custom_taluka"):
		customer.custom_taluka = school_info.get("taluka")

	if hasattr(customer, "custom_district"):
		customer.custom_district = school_info.get("district")

	if customer.is_new():
		customer.insert(ignore_permissions=True)
	else:
		customer.save(ignore_permissions=True)

	return customer.name


def create_or_update_address(customer_name, school_info):
	address_name = frappe.db.exists("Address", {"address_title": customer_name})
	
	if address_name:
		address = frappe.get_doc("Address", address_name)
	else:
		address = frappe.new_doc("Address")
		address.address_title = customer_name
		address.address_type = "Billing"
		address.append("links", {
			"link_doctype": "Customer",
			"link_name": customer_name
		})

	address.address_line1 = school_info.get("school_address")
	address.city = school_info.get("city")
	address.state = school_info.get("state")
	address.pincode = school_info.get("pincode")
	address.country = "India"

	if hasattr(address, "custom_taluka"):
		address.custom_taluka = school_info.get("taluka")
	if hasattr(address, "custom_district"):
		address.custom_district = school_info.get("district")

	if address.is_new():
		address.insert(ignore_permissions=True)
	else:
		address.save(ignore_permissions=True)


def create_or_update_principal(principal, customer_name):
	"""
	Keeps history: appends new email/phone rows instead of replacing.
	Marks new ones as primary, old ones as non-primary.
	"""
	if not principal.get("name"):
		return

	# Step 1: Find existing Principal contact for this customer
	existing = frappe.db.sql("""
		SELECT c.name 
		FROM `tabContact` c
		JOIN `tabDynamic Link` l ON l.parent = c.name
		WHERE c.designation = 'Principal'
		AND l.link_doctype = 'Customer'
		AND l.link_name = %s
		LIMIT 1
	""", (customer_name,), as_dict=True)
	
	contact_name = existing[0].name if existing else None

	if contact_name:
		contact = frappe.get_doc("Contact", contact_name)
	else:
		contact = frappe.new_doc("Contact")

	contact.first_name = principal.get("name")
	contact.designation = "Principal"
	contact.company_name = customer_name
	contact.is_primary_contact = 1

	# Step 2: Handle Email — add NEW row, mark old as non-primary
	email = principal.get("email")
	if email:
		email_exists = any(row.email_id == email for row in contact.email_ids)
		if not email_exists:
			for row in contact.email_ids:
				row.is_primary = 0
			contact.append("email_ids", {
				"email_id": email,
				"is_primary": 1
			})

	# Step 3: Handle Mobile — add NEW row, mark old as non-primary
	mobile = principal.get("mobile")
	if mobile:
		phone_exists = any(row.phone == mobile for row in contact.phone_nos)
		if not phone_exists:
			for row in contact.phone_nos:
				row.is_primary_mobile_no = 0
			contact.append("phone_nos", {
				"phone": mobile,
				"is_primary_mobile_no": 1
			})

	# Step 4: Ensure customer link
	has_link = any(
		link.link_doctype == "Customer" and link.link_name == customer_name
		for link in contact.get("links", [])
	)
	if not has_link:
		contact.append("links", {
			"link_doctype": "Customer",
			"link_name": customer_name
		})

	# Step 5: Save contact
	if contact.is_new():
		contact.insert(ignore_permissions=True)
	else:
		contact.save(ignore_permissions=True)

	# Step 6: Set as customer's primary contact using doc method instead of db.set_value
	customer = frappe.get_doc("Customer", customer_name)
	customer.customer_primary_contact = contact.name
	customer.save(ignore_permissions=True)

	return contact.name


def create_or_update_teachers(coordinators, customer_name):
	customer = frappe.get_doc("Customer", customer_name)

	subject_map = {
		"iso": "Science Olympiad (ISO)",
		"imo": "Maths Olympiad (IMO)",
		"eio": "English Olympiad (EIO)",
		"gkio": "General Knowledge Olympiad (GKIO)",
		"ico": "Computer Olympiad (ICO)",
		"ido": "Drawing Olympiad (IDO)",
		"neso": "Essay Olympiad (NESO)",
		"nsso": "Social Studies Olympiad (NSSO)",
		"nho": "Hindi Olympiad (NHO)",
		"nlro": "Logical Reasoning Olympiad (NLRO)",
		"cio": "Commerce Olympiad (CIO)"
	}

	ensure_subject_exists("Principal")
	for subject_name in subject_map.values():
		ensure_subject_exists(subject_name)

	# Clean existing child table rows in database for this customer
	frappe.db.delete("Teachers Details", {"parent": customer.name})
	customer.set("custom_school_teacher_details", [])

	processed_teacher_names = set()

	for role_key, coordinator in coordinators.items():
		if not coordinator.get("name"):
			continue

		role_text = coordinator.get("role", "").strip()
		subject = None

		if "principal" in role_text.lower() or "principal" in role_key.lower():
			subject = "Principal"
		elif "overall" in role_text.lower() or "overall" in role_key.lower():
			subject = "Overall Coordinator"
		else:
			for code, sub_name in subject_map.items():
				if f"({code.upper()})" in role_text:
					subject = sub_name
					break
			if not subject and role_text.endswith(" In-charge"):
				subject = role_text[:-10].strip()
			if not subject:
				subject = subject_map.get(role_key)

		if not subject:
			subject = role_text or "Other"

		ensure_subject_exists(subject)

		teacher_name = frappe.db.get_value("Teacher", {
			"name1": coordinator.get("name"),
			"customer_reference": customer.name
		})

		if teacher_name:
			teacher = frappe.get_doc("Teacher", teacher_name)
		else:
			teacher = frappe.new_doc("Teacher")

		teacher.name1 = coordinator.get("name")
		teacher.phone_number = coordinator.get("mobile")
		teacher.email_id = coordinator.get("email")
		teacher.date_of_birth = coordinator.get("dob") or "2000-01-01"
		teacher.subject = subject
		teacher.status = "Active"
		teacher.experience = "Experienced"
		teacher.customer_reference = customer.name

		if teacher.is_new():
			teacher.insert(ignore_permissions=True)
		else:
			teacher.save(ignore_permissions=True)

		if teacher.name in processed_teacher_names:
			continue
		processed_teacher_names.add(teacher.name)

		# Ensure no orphaned Teachers Details record exists with this name before append
		if frappe.db.exists("Teachers Details", teacher.name):
			frappe.db.delete("Teachers Details", {"name": teacher.name})

		customer.append("custom_school_teacher_details", {
			"name1": teacher.name,
			"date_of_birth": teacher.date_of_birth,
			"phone_number": teacher.phone_number,
			"status": teacher.status,
			"email_id": teacher.email_id,
			"subject": teacher.subject,
			"experience": teacher.experience
		})

	customer.save(ignore_permissions=True)


@frappe.whitelist(allow_guest=True)
def get_customer_from_session_user():
	if frappe.session.user == "Guest":
		return {}

	customer_name = frappe.db.get_value("Portal User", {"user": frappe.session.user}, "parent")
	if not customer_name:
		return {}

	customer = frappe.get_doc("Customer", customer_name)

	# Address
	address = {}
	address_names = frappe.get_all("Dynamic Link",
		filters={"link_doctype": "Customer", "link_name": customer.name, "parenttype": "Address"},
		pluck="parent")

	if address_names:
		addr = frappe.get_doc("Address", address_names[0])
		address = {
			"address_line1": addr.address_line1,
			"address_line2": addr.address_line2,
			"city": addr.city,
			"county": getattr(addr, "county", ""),
			"state": addr.state,
			"custom_taluka": addr.custom_taluka,
			"custom_district": addr.custom_district,
			"country": addr.country,
			"pincode": addr.pincode,
			
		}

	# Contact
	contact_name = ""
	contact_email = ""
	contact_mobile = ""

	contact_doc = None
	if customer.customer_primary_contact and frappe.db.exists("Contact", customer.customer_primary_contact):
		contact_doc = frappe.get_doc("Contact", customer.customer_primary_contact)
	else:
		contact_names = frappe.get_all(
			"Dynamic Link",
			filters={"link_doctype": "Customer", "link_name": customer.name, "parenttype": "Contact"},
			pluck="parent"
		)
		if contact_names:
			contact_doc = frappe.get_doc("Contact", contact_names[0])

	if contact_doc:
		contact_name = contact_doc.get_full_name() if hasattr(contact_doc, "get_full_name") else getattr(contact_doc, "first_name", "")

		if getattr(contact_doc, "email_ids", None):
			primary_email = next((row.email_id for row in contact_doc.email_ids if getattr(row, "is_primary", False)), None)
			if not primary_email and len(contact_doc.email_ids) > 0:
				primary_email = contact_doc.email_ids[0].email_id
			contact_email = primary_email or ""

		if not contact_email:
			contact_email = getattr(contact_doc, "email_id", "") or ""

		if getattr(contact_doc, "phone_nos", None):
			primary_mobile = next((row.phone for row in contact_doc.phone_nos if getattr(row, "is_primary_mobile_no", False)), None)
			if not primary_mobile and len(contact_doc.phone_nos) > 0:
				primary_mobile = contact_doc.phone_nos[0].phone
			contact_mobile = primary_mobile or ""

		if not contact_mobile:
			contact_mobile = getattr(contact_doc, "mobile_no", "") or getattr(contact_doc, "phone", "") or ""

	# Check Customer fields for email fallback
	if not contact_email:
		contact_email = (
			customer.get("email_id")
			or customer.get("custom_school_email")
			or customer.get("custom_email")
			or customer.get("custom_email_id")
			or ""
		)

	# Check User session email fallback
	if not contact_email and frappe.session.user and frappe.session.user != "Guest":
		user_email = frappe.db.get_value("User", frappe.session.user, "email")
		if user_email and "@" in user_email:
			contact_email = user_email
		elif "@" in frappe.session.user:
			contact_email = frappe.session.user

	coordinators_data = {}
	for row in customer.custom_school_teacher_details:
		subject_key = frappe.scrub(row.subject or "")  # canonical snake_case key

		if subject_key == "principal":
			role_text = "👑 Head Master / Principal"
		elif subject_key == "overall_coordinator":
			role_text = "⭐ Overall Co-ordinator"
		else:
			role_text = f"{(row.subject or '').strip()} In-charge"

		coordinators_data[subject_key] = {
			"role": role_text,
			"name": row.name1,
			"mobile": row.phone_number,
			"email": row.email_id,
			"dob": str(row.date_of_birth) if row.date_of_birth else ""
		}

	# Exam Summaries
	exam_summaries_data = []
	exams = frappe.get_all("Exams Summary", filters={"customer": customer.name}, pluck="name")

	for exam_name in exams:
		es_doc = frappe.get_doc("Exams Summary", exam_name)

		if not es_doc.exam_detail:
			subject_map = {}
			for exam_row in es_doc.exam_summary:
				subject = exam_row.subject
				if subject not in subject_map:
					subject_map[subject] = {
						"exam_summary_name": es_doc.name,
						"yearly_exam_date": "",
						"academic_year": "",
						"subject": subject,
						"target_dates": [],
						"rows": []
					}
				subject_map[subject]["rows"].append({
					"class": getattr(exam_row, "class", ""),
					"teacher_name": getattr(exam_row, "teacher_name", ""),
					"whatsapp_no": getattr(exam_row, "whatsapp_no", ""),
					"no_of_students": getattr(exam_row, "no_of_students", 0),
					"slot_date": getattr(exam_row, "slot_date", "")
				})
			exam_summaries_data.extend(subject_map.values())
			continue

		yearly_exam = frappe.get_doc("Yearly Exam Date", es_doc.exam_detail)
		subject_map = {}

		for row in yearly_exam.target_dates:
			subject_map[row.subject] = {
				"exam_summary_name": es_doc.name,
				"yearly_exam_date": yearly_exam.name,
				"academic_year": yearly_exam.academic_year,
				"subject": row.subject,
				"target_dates": [row.tg_date_1, row.tg_date_2, row.tg_date_3],
				"rows": []
			}

		for exam_row in es_doc.exam_summary:
			subject = exam_row.subject
			if subject not in subject_map:
				continue
			subject_map[subject]["rows"].append({
				"class": getattr(exam_row, "class", ""),
				"teacher_name": getattr(exam_row, "teacher_name", ""),
				"whatsapp_no": getattr(exam_row, "whatsapp_no", ""),
				"no_of_students": getattr(exam_row, "no_of_students", 0),
				"slot_date": getattr(exam_row, "slot_date", "")
			})

		exam_summaries_data.extend(subject_map.values())

	# If no specific Exams Summary assigned yet, fetch from active Yearly Exam Date
	if not exam_summaries_data:
		ay = customer.get("custom_current_academic_year")
		yed_filters = {"docstatus": 1}
		if ay:
			normalized_ay = ay
			if not normalized_ay.startswith("AY-"):
				parts = normalized_ay.replace("-", "/").split("/")
				if len(parts) == 2:
					normalized_ay = f"AY-{parts[0]}/{parts[1]}"
			yed_filters["academic_year"] = normalized_ay

		yearly_exams = frappe.get_all("Yearly Exam Date", filters=yed_filters, fields=["name", "academic_year"], order_by="creation desc", limit=1)
		if not yearly_exams:
			# Fallback to any latest submitted Yearly Exam Date
			yearly_exams = frappe.get_all("Yearly Exam Date", filters={"docstatus": 1}, fields=["name", "academic_year"], order_by="creation desc", limit=1)

		if yearly_exams:
			yearly_exam = frappe.get_doc("Yearly Exam Date", yearly_exams[0].name)
			for row in yearly_exam.target_dates:
				exam_summaries_data.append({
					"exam_summary_name": "",
					"yearly_exam_date": yearly_exam.name,
					"academic_year": yearly_exam.academic_year,
					"subject": row.subject,
					"target_dates": [row.tg_date_1, row.tg_date_2, row.tg_date_3],
					"rows": []
				})

	# Teachers
	teachers_data = []
	es_name = frappe.db.get_value("Exams Summary", {"customer": customer.name})
	if es_name:
		es_doc = frappe.get_doc("Exams Summary", es_name)
		for row in es_doc.exam_summary:
			teachers_data.append({
				"subject": row.subject,
				"class": getattr(row, "class", ""),
				"teacher_name": row.teacher_name,
				"whatsapp_no": row.whatsapp_no,
				"no_of_students": row.no_of_students,
				"slot_date": row.slot_date
			})

	# Books Selection
	books_selection = []
	books_order_submitted = False
	bs_name = frappe.db.get_value("Books Selection", {"customer": customer.name})
	if bs_name:
		bs_doc = frappe.get_doc("Books Selection", bs_name)
		books_order_submitted = bool(bs_doc.is_submitted)
		for row in bs_doc.select_books:
			books_selection.append({
				"subject": row.subject,
				"class_grade": row.class_grade,
				"practice_workbook_110": row.practice_workbook_110,
				"student_guide_220": row.student_guide_220,
				"prev_year_paper_160": row.prev_year_paper_160
			})

	# Competition Entry Matrix: Fetch latest record for customer
	latest_cem_name = frappe.db.get_value(
		"Competition Entry Matrix",
		{"customer": customer.name},
		"name",
		order_by="creation desc"
	)
	competition_entry_matrix_rows = []
	entry_matrix = None
	if latest_cem_name:
		cem_doc = frappe.get_doc("Competition Entry Matrix", latest_cem_name)
		entry_matrix = {
			"group_a": {},
			"group_b": {},
			"group_c": {},
			"group_d": {},
			"group_e": {},
		}
		for row in cem_doc.entry_matrix:
			competition_entry_matrix_rows.append({
				"subject": row.subject,
				"group_a": row.group_a or 0,
				"group_b": row.group_b or 0,
				"group_c": row.group_c or 0,
				"group_d": row.group_d or 0,
				"group_e": row.group_e or 0,
			})
			subj_key = frappe.scrub(row.subject)
			entry_matrix["group_a"][subj_key] = row.group_a or 0
			entry_matrix["group_b"][subj_key] = row.group_b or 0
			entry_matrix["group_c"][subj_key] = row.group_c or 0
			entry_matrix["group_d"][subj_key] = row.group_d or 0
			entry_matrix["group_e"][subj_key] = row.group_e or 0
	else:
		entry_matrix = frappe.cache().get_value(f"wof_entry_matrix_{customer.name}")

	lc_reg_fee = get_registration_fee("Little Champ Registration")
	ito_reg_fee = get_registration_fee("ITO Registration")
	wof_reg_fee = get_registration_fee("WOF Registration")
	if customer.get("custom_retention") is not None and flt(customer.get("custom_retention")) > 0:
		wof_reg_fee["custom_retention"] = flt(customer.get("custom_retention"))

	return {
		"customer": customer.as_dict(),
		"address": address,
		"contact_name": contact_name,
		"contact_email": contact_email,
		"contact_mobile": contact_mobile,
		"application_deadline": customer.custom_application_deadline,
		"school_code": customer.custom_school_code,
		"registration_date_1": customer.custom_last_date_of_reg,
		"registration_date_2": customer.custom_last_date_of_reg_2,
		"coordinators": coordinators_data,
		"exam_summaries": exam_summaries_data,
		"entry_matrix": entry_matrix,
		"competition_entry_matrix_rows": competition_entry_matrix_rows,
		"latest_competition_entry_matrix": latest_cem_name,
		"session_user": frappe.session.user,
		"teachers": teachers_data,
		"books_selection": books_selection,
		"books_order_submitted": books_order_submitted,
		"custom_is_little_champ": customer.get("custom_is_little_champ") or 0,
		"registration_fee": lc_reg_fee if customer.get("custom_is_little_champ") else ito_reg_fee,
		"little_champ_registration_fee": lc_reg_fee,
		"ito_registration_fee": ito_reg_fee,
		"wof_registration_fee": wof_reg_fee,
	}


# ==================== LITTLE CHAMP ====================

@frappe.whitelist(allow_guest=True)
def save_little_champ_registration(registration_data):
	original_ignore_permissions = frappe.flags.ignore_permissions
	try:
		# This portal route uses Guest API access. Its Customer/contact/address
		# persistence is scoped to the submitted Little Champ registration, so
		# bypass permissions only while persisting the records.
		frappe.flags.ignore_permissions = True
		data = json.loads(registration_data) if isinstance(registration_data, str) else registration_data

		# Create/update customer - saves to DB session
		customer = create_or_update_customer(data.get("school_info", {}))
		
		# Update customer with Little Champ specific fields
		customer_doc = frappe.get_doc("Customer", customer)
		customer_doc.custom_is_little_champ = 1
		
		# FIX: Handle both 'school_code' and 'ito_school_code' from frontend
		school_code = data.get("school_info", {}).get("school_code") or data.get("school_info", {}).get("ito_school_code")
		if school_code:
			customer_doc.custom_school_code = school_code
			
		customer_doc.custom_taluka = data.get("school_info", {}).get("taluka")
		customer_doc.custom_district = data.get("school_info", {}).get("district")
		customer_doc.save(ignore_permissions=True)

		# Update contact if exists
		if customer_doc.customer_primary_contact:
			contact = frappe.get_doc("Contact", customer_doc.customer_primary_contact)
			school_info = data.get("school_info", {})
			contact.mobile_no = school_info.get("phone_no")
			contact.custom_whatsapp_no = school_info.get("whatsapp_no")
			contact.save(ignore_permissions=True)

		# Create/update address - saves to DB session
		create_or_update_address(customer_doc.name, data.get("school_info", {}))

		# Process coordinators
		coordinators_data = data.get("coordinators", {})
		for role_key, coord_data in coordinators_data.items():
			role_text = coord_data.get("role", role_key).strip().lower()
			if "principal" in role_text or "head master" in role_text:
				create_or_update_principal(coord_data, customer_doc.name)

		# Create/update teachers - saves to DB session
		create_or_update_little_champ_teachers(coordinators_data, customer_doc.name)

		# Save exams data
		exams_data = data.get("exams", {})
		if exams_data:
			es_name = frappe.db.get_value("Exams Summary", {"customer": customer_doc.name})
			if es_name:
				es_doc = frappe.get_doc("Exams Summary", es_name)
			else:
				es_doc = frappe.new_doc("Exams Summary")
				es_doc.customer = customer_doc.name

			es_doc.exam_summary = []
			for subject_key, subject_data in exams_data.items():
				subject_name = subject_data.get("subject") or subject_data.get("subject_name") or subject_key
				registrations = subject_data.get("registrations", [])
				for row in registrations:
					es_doc.append("exam_summary", {
						"subject": subject_name,
						"class": row.get("class"),
						"teacher_name": row.get("teacher_name"),
						"whatsapp_no": row.get("whatsapp"),
						"no_of_students": row.get("students")
					})

			if es_doc.is_new():
				es_doc.insert(ignore_permissions=True)
			else:
				es_doc.save(ignore_permissions=True)

		# NO explicit commit - let Frappe handle it
		# NO explicit rollback - let Frappe handle exceptions
		return {"success": True, "customer": customer_doc.name}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Little Champ Registration Error")
		return {"success": False, "message": str(e)}
	finally:
		frappe.flags.ignore_permissions = original_ignore_permissions


def create_or_update_little_champ_teachers(coordinators, customer_name):
	customer = frappe.get_doc("Customer", customer_name)

	# Clean existing child table rows in database for this customer
	frappe.db.delete("Teachers Details", {"parent": customer.name})
	customer.set("custom_school_teacher_details", [])

	processed_teacher_names = set()

	for role, coordinator in coordinators.items():
		if not coordinator.get("name"):
			continue

		role_lower = role.strip().lower()
		if role_lower in ["head master / principal", "principal"]:
			subject = "Principal"
		elif role_lower in ["overall co-ordinator", "overall coordinator"]:
			subject = "Overall Coordinator"
		else:
			subject = role.replace(" In-charge", "").strip()

		teacher_name = frappe.db.get_value("Teacher", {
			"name1": coordinator.get("name"),
			"customer_reference": customer.name
		})

		if teacher_name:
			teacher = frappe.get_doc("Teacher", teacher_name)
		else:
			teacher = frappe.new_doc("Teacher")

		teacher.name1 = coordinator.get("name")
		teacher.phone_number = coordinator.get("mobile")
		teacher.email_id = coordinator.get("email")
		teacher.date_of_birth = coordinator.get("dob") or "2000-01-01"
		teacher.subject = subject
		teacher.status = "Active"
		teacher.experience = "Experienced"
		teacher.customer_reference = customer.name

		if teacher.is_new():
			teacher.insert(ignore_permissions=True)
		else:
			teacher.save(ignore_permissions=True)

		if teacher.name in processed_teacher_names:
			continue
		processed_teacher_names.add(teacher.name)

		if frappe.db.exists("Teachers Details", teacher.name):
			frappe.db.delete("Teachers Details", {"name": teacher.name})

		customer.append("custom_school_teacher_details", {
			"name1": teacher.name,
			"date_of_birth": teacher.date_of_birth,
			"phone_number": teacher.phone_number,
			"status": teacher.status,
			"email_id": teacher.email_id,
			"subject": teacher.subject,
			"experience": teacher.experience
		})

	customer.save(ignore_permissions=True)


@frappe.whitelist()
def save_little_champ_step():
	data = frappe.request.get_json() or {}
	step = cint(data.get("step"))

	try:
		original_flag = frappe.flags.ignore_permissions
		frappe.flags.ignore_permissions = True

		try:
			if step == 1:
				school_info = data.get("school_info", {})
				
				# FIX: Handle both 'school_code' and 'ito_school_code' from frontend
				if not school_info.get("school_code") and school_info.get("ito_school_code"):
					school_info["school_code"] = school_info.get("ito_school_code")
				
				customer = create_or_update_customer(school_info)
				customer_doc = frappe.get_doc("Customer", customer)
				customer_doc.custom_is_little_champ = 1
				customer_doc.custom_school_code = school_info.get("school_code")
				customer_doc.custom_taluka = school_info.get("taluka")
				customer_doc.custom_district = school_info.get("district")
				customer_doc.save(ignore_permissions=True)

				if customer_doc.customer_primary_contact:
					contact = frappe.get_doc("Contact", customer_doc.customer_primary_contact)
					contact.mobile_no = school_info.get("phone_no")
					contact.custom_whatsapp_no = school_info.get("whatsapp_no")
					contact.save(ignore_permissions=True)

				create_or_update_address(customer_doc.name, school_info)
				frappe.cache().set_value(f"little_champ_customer_{frappe.session.user}", customer_doc.name)

				# NO explicit commit - let Frappe handle it
				return {"success": True, "step": 1, "customer": customer_doc.name}

			elif step == 2:
				customer = frappe.cache().get_value(f"little_champ_customer_{frappe.session.user}")
				if not customer:
					customer = frappe.db.get_value("Portal User", {"user": frappe.session.user}, "parent")
				if not customer:
					frappe.throw("Please save School Information first.")

				coordinators_data = data.get("coordinators", {})
				for role_key, coord_data in coordinators_data.items():
					role_text = coord_data.get("role", role_key).strip().lower()
					if "principal" in role_text or "head master" in role_text:
						create_or_update_principal(coord_data, customer)

				create_or_update_little_champ_teachers(coordinators_data, customer)

				# NO explicit commit - let Frappe handle it
				return {"success": True, "step": 2}

			elif step == 3:
				customer = frappe.cache().get_value(f"little_champ_customer_{frappe.session.user}")
				if not customer:
					customer = frappe.db.get_value("Portal User", {"user": frappe.session.user}, "parent")
				if not customer:
					frappe.throw("Please save School Information first.")

				exams_data = data.get("exams", {})
				if exams_data:
					es_name = frappe.db.get_value("Exams Summary", {"customer": customer})
					if es_name:
						es_doc = frappe.get_doc("Exams Summary", es_name)
					else:
						es_doc = frappe.new_doc("Exams Summary")
						es_doc.customer = customer

					es_doc.exam_summary = []
					for subject_key, subject_data in exams_data.items():
						subject_name = subject_data.get("subject") or subject_data.get("subject_name") or subject_key
						registrations = subject_data.get("registrations", [])
						for row in registrations:
							es_doc.append("exam_summary", {
								"subject": subject_name,
								"class": row.get("class"),
								"teacher_name": row.get("teacher_name"),
								"whatsapp_no": row.get("whatsapp"),
								"no_of_students": row.get("students"),
								"slot_date": row.get("slot_date")
							})

					if es_doc.is_new():
						es_doc.insert(ignore_permissions=True)
					else:
						es_doc.save(ignore_permissions=True)

				# NO explicit commit - let Frappe handle it
				return {"success": True, "step": 3}

			else:
				frappe.throw(f"Invalid step {step}")

		finally:
			frappe.flags.ignore_permissions = original_flag

	except Exception as e:
		# NO explicit rollback - let Frappe handle exceptions
		frappe.log_error(frappe.get_traceback(), "Little Champ Step Save Error")
		return {"success": False, "message": str(e)}


@frappe.whitelist(allow_guest=True)
def save_wof_registration(registration_data):
	original_ignore_permissions = frappe.flags.ignore_permissions
	try:
		frappe.flags.ignore_permissions = True
		data = json.loads(registration_data) if isinstance(registration_data, str) else registration_data

		school_info = data.get("school_info", {})
		gstin = school_info.get("gst_no", "").strip().upper()
		if gstin and len(gstin) != 15:
			gstin = ""
		school_info["gst_no"] = gstin

		if not school_info.get("school_code") and school_info.get("ito_school_code"):
			school_info["school_code"] = school_info.get("ito_school_code")

		customer = create_or_update_customer(school_info)
		customer_doc = frappe.get_doc("Customer", customer)
		if hasattr(customer_doc, "custom_is_wof"):
			customer_doc.custom_is_wof = 1
		if hasattr(customer_doc, "custom_registration_date"):
			customer_doc.custom_registration_date = frappe.utils.nowdate()
		customer_doc.save(ignore_permissions=True)

		create_or_update_address(customer, school_info)

		coordinators_data = data.get("coordinators", {})
		principal = coordinators_data.get("principal") or coordinators_data.get("head_master_principal")
		if principal:
			create_or_update_principal(principal, customer)

		create_or_update_teachers(coordinators_data, customer)

		entry_matrix = data.get("entry_matrix")
		if entry_matrix:
			save_competition_entry_matrix_record(customer, entry_matrix, is_submitted=True)
			sync_wof_entry_matrix_to_exams_summary(customer, entry_matrix)

		payment = data.get("payment", {})
		mode = payment.get("mode")
		if mode and mode != "razorpay":
			customer_doc.add_comment(
				"Info",
				f"WOF Registration fee reported as paid via {mode}: {frappe.as_json(payment.get('payment_details', {}))}",
			)

		return {"success": True, "customer": customer}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "WOF Registration Error")
		return {"success": False, "message": str(e)}
	finally:
		frappe.flags.ignore_permissions = original_ignore_permissions


def save_competition_entry_matrix_record(customer, entry_matrix, is_submitted=False):
	if not customer or not entry_matrix:
		return None

	frappe.cache().set_value(f"wof_entry_matrix_{customer}", entry_matrix)

	# Fetch all school subjects for exact matching
	all_school_subjects = frappe.get_all("School Subject", pluck="name")
	subject_lookup = {frappe.scrub(s): s for s in all_school_subjects}
	for s in all_school_subjects:
		subject_lookup[s.lower()] = s
		subject_lookup[s] = s

	alias_map = {
		"colouring": "Colouring Competition",
		"colouring_competition": "Colouring Competition",
		"handwriting": "Handwriting Competition",
		"handwriting_competition": "Handwriting Competition",
		"sketching": "Sketching Competition",
		"sketching_competition": "Sketching Competition",
		"cartoon": "Cartoon Making",
		"cartoon_making": "Cartoon Making",
		"caricature": "Caricature (Cartoon)",
		"caricature_cartoon": "Caricature (Cartoon)",
		"greeting": "Greeting Card Making",
		"greeting_card_making": "Greeting Card Making",
	}

	# Collect all distinct subject keys from entry_matrix
	subjects_keys = []
	for g_data in entry_matrix.values():
		if isinstance(g_data, dict):
			for k in g_data.keys():
				if k not in subjects_keys:
					subjects_keys.append(k)

	resolved_subjects = {}
	for key in subjects_keys:
		target_subject = alias_map.get(key) or subject_lookup.get(key) or subject_lookup.get(key.lower()) or subject_lookup.get(frappe.scrub(key)) or key

		# Ensure School Subject exists in DB
		if target_subject not in all_school_subjects and not frappe.db.exists("School Subject", target_subject):
			try:
				subj_doc = frappe.get_doc({"doctype": "School Subject", "subject_name": target_subject})
				subj_doc.insert(ignore_permissions=True)
				all_school_subjects.append(target_subject)
			except Exception:
				pass

		ga = cint(entry_matrix.get("group_a", {}).get(key, 0))
		gb = cint(entry_matrix.get("group_b", {}).get(key, 0))
		gc = cint(entry_matrix.get("group_c", {}).get(key, 0))
		gd = cint(entry_matrix.get("group_d", {}).get(key, 0))
		ge = cint(entry_matrix.get("group_e", {}).get(key, 0))

		if target_subject not in resolved_subjects:
			resolved_subjects[target_subject] = {
				"subject": target_subject,
				"group_a": ga,
				"group_b": gb,
				"group_c": gc,
				"group_d": gd,
				"group_e": ge,
			}
		else:
			resolved_subjects[target_subject]["group_a"] = max(resolved_subjects[target_subject]["group_a"], ga)
			resolved_subjects[target_subject]["group_b"] = max(resolved_subjects[target_subject]["group_b"], gb)
			resolved_subjects[target_subject]["group_c"] = max(resolved_subjects[target_subject]["group_c"], gc)
			resolved_subjects[target_subject]["group_d"] = max(resolved_subjects[target_subject]["group_d"], gd)
			resolved_subjects[target_subject]["group_e"] = max(resolved_subjects[target_subject]["group_e"], ge)

	# Find existing unsubmitted record for this customer
	existing_cem_name = frappe.db.get_value(
		"Competition Entry Matrix",
		{"customer": customer, "is_submitted": 0},
		"name",
		order_by="creation desc"
	)

	if existing_cem_name:
		cem_doc = frappe.get_doc("Competition Entry Matrix", existing_cem_name)
		cem_doc.set("entry_matrix", [])
	else:
		cem_doc = frappe.new_doc("Competition Entry Matrix")
		cem_doc.customer = customer

	if hasattr(cem_doc, "is_submitted"):
		cem_doc.is_submitted = 1 if is_submitted else 0

	for row_data in resolved_subjects.values():
		cem_doc.append("entry_matrix", row_data)

	if existing_cem_name:
		cem_doc.save(ignore_permissions=True)
	else:
		cem_doc.insert(ignore_permissions=True)

	return cem_doc.name


def sync_wof_entry_matrix_to_exams_summary(customer, entry_matrix):
	if not customer or not entry_matrix:
		return

	es_name = frappe.db.get_value("Exams Summary", {"customer": customer})
	if not es_name:
		yed_name = frappe.db.get_value("Yearly Exam Date", {"docstatus": 1}, "name", order_by="creation desc")
		if yed_name:
			es_doc = frappe.new_doc("Exams Summary")
			es_doc.customer = customer
			es_doc.exam_detail = yed_name
			es_doc.insert(ignore_permissions=True)
			es_name = es_doc.name

	if es_name:
		es_doc = frappe.get_doc("Exams Summary", es_name)
		es_doc.exam_summary = []

		group_labels = {
			"group_a": "Group A",
			"group_b": "Group B",
			"group_c": "Group C",
			"group_d": "Group D",
			"group_e": "Group E",
		}

		for group_key, comp_dict in entry_matrix.items():
			if not isinstance(comp_dict, dict):
				continue
			group_label = group_labels.get(group_key, group_key)
			for comp_key, count in comp_dict.items():
				if count and cint(count) > 0:
					es_doc.append("exam_summary", {
						"subject": comp_key,
						"class": group_label,
						"no_of_students": cint(count),
						"form_type": "WOF Registration"
					})
		es_doc.save(ignore_permissions=True)


@frappe.whitelist(allow_guest=True)
def save_wof_step():
	data = frappe.request.get_json() or {}
	step = cint(data.get("step"))

	try:
		original_flag = frappe.flags.ignore_permissions
		frappe.flags.ignore_permissions = True

		try:
			if step == 1:
				school_info = data.get("school_info", {})
				if not school_info.get("school_code") and school_info.get("ito_school_code"):
					school_info["school_code"] = school_info.get("ito_school_code")

				customer = create_or_update_customer(school_info)
				customer_doc = frappe.get_doc("Customer", customer)
				if hasattr(customer_doc, "custom_is_wof"):
					customer_doc.custom_is_wof = 1
				customer_doc.save(ignore_permissions=True)

				create_or_update_address(customer_doc.name, school_info)
				frappe.cache().set_value(f"wof_customer_{frappe.session.user}", customer_doc.name)
				frappe.cache().set_value(f"ito_customer_{frappe.session.user}", customer_doc.name)
				return {"success": True, "step": 1, "customer": customer_doc.name}

			elif step == 2:
				customer = frappe.cache().get_value(f"wof_customer_{frappe.session.user}") or frappe.cache().get_value(f"ito_customer_{frappe.session.user}")
				if not customer:
					customer = frappe.db.get_value("Portal User", {"user": frappe.session.user}, "parent")
				if not customer:
					frappe.throw("Please save School Information first.")

				coordinators_data = data.get("coordinators", {})
				principal = coordinators_data.get("principal") or coordinators_data.get("head_master_principal")
				if principal:
					create_or_update_principal(principal, customer)

				create_or_update_teachers(coordinators_data, customer)
				return {"success": True, "step": 2}

			elif step == 3:
				customer = frappe.cache().get_value(f"wof_customer_{frappe.session.user}") or frappe.cache().get_value(f"ito_customer_{frappe.session.user}")
				if not customer:
					customer = frappe.db.get_value("Portal User", {"user": frappe.session.user}, "parent")
				if not customer:
					frappe.throw("Please save School Information first.")

				entry_matrix = data.get("entry_matrix")
				if entry_matrix:
					save_competition_entry_matrix_record(customer, entry_matrix)
					sync_wof_entry_matrix_to_exams_summary(customer, entry_matrix)

				return {"success": True, "step": 3}

			elif step == 4 or step == 5:
				customer = frappe.cache().get_value(f"wof_customer_{frappe.session.user}") or frappe.cache().get_value(f"ito_customer_{frappe.session.user}")
				if not customer:
					customer = frappe.db.get_value("Portal User", {"user": frappe.session.user}, "parent")
				if not customer:
					frappe.throw("Please save School Information first.")

				payment = data.get("payment", {})
				mode = payment.get("mode")
				if mode and mode != "razorpay":
					frappe.get_doc("Customer", customer).add_comment(
						"Info",
						f"WOF Registration fee reported as paid via {mode}: {frappe.as_json(payment.get('payment_details', {}))}",
					)
				return {"success": True, "step": step}

			else:
				frappe.throw(f"Invalid step {step}")

		finally:
			frappe.flags.ignore_permissions = original_flag

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "WOF Step Save Error")
		return {"success": False, "message": str(e)}


# ==================== DYNAMIC SUBJECT HELPER ====================

def get_subjects_for_year(academic_year, is_little_champ=0):
	"""
	Fetch subjects from Yearly Exam Date for given academic year.
	Filters by is_little_champ flag on parent Yearly Exam Date document.
	Only fetches SUBMITTED documents (docstatus=1).
	"""
	if not academic_year:
		return []

	# Normalize academic year format
	if not academic_year.startswith("AY-"):
		parts = academic_year.replace("-", "/").split("/")
		if len(parts) == 2:
			academic_year = f"AY-{parts[0]}/{parts[1]}"

	# CRITICAL: Convert to integer
	is_little_champ = int(is_little_champ or 0)

	# Check if parent doctype has is_little_champ field
	has_parent_lc_field = frappe.db.has_column("Yearly Exam Date", "is_little_champ")

	# Build filters for parent document
	filters = {
		"academic_year": academic_year,
		"docstatus": 1  # ← ONLY fetch SUBMITTED documents
	}
	if has_parent_lc_field:
		filters["is_little_champ"] = is_little_champ

	# Find Yearly Exam Date for this academic year AND type
	# Order by creation date to get latest if multiple exist
	yed = frappe.get_all("Yearly Exam Date",
		filters=filters,
		fields=["name", "creation"],
		order_by="creation desc",  # ← Get latest first
		limit=1)

	if not yed:
		frappe.log_error(
			f"No Yearly Exam Date found for {academic_year} (is_little_champ={is_little_champ})", 
			"Subject Fetch"
		)
		return []

	yed_name = yed[0].name

	# Fetch ALL subjects from this document's child table
	target_dates = frappe.get_all("Yearly Exam Date CT",
		filters={"parent": yed_name},
		fields=["subject", "school_subject", "idx"],
		order_by="idx asc")

	subjects = []
	for row in target_dates:
		if not row.subject:
			continue

		short_name = row.school_subject or derive_short_name(row.subject)
		safe_code = (short_name or row.subject).lower().replace(" ", "_").replace("(", "").replace(")", "")[:20]

		subjects.append({
			"code": safe_code,
			"name": row.subject,
			"shortName": short_name or safe_code[:10].upper(),
			"title": row.subject,
			"isDefaultFree": "Logical Reasoning" in row.subject or "NLRO" in (short_name or "")
		})

	return subjects


def derive_short_name(subject_name):
	"""
	Fallback: Derive short name from subject name if school_subject is empty.
	"""
	if not subject_name:
		return ""
	
	# Extract text in parentheses
	import re
	match = re.search(r'\(([^)]+)\)', subject_name)
	if match:
		return match.group(1)
	
	# Or take first letters of each word
	words = subject_name.split()
	if len(words) > 1:
		return ''.join(w[0].upper() for w in words if w)
	
	return subject_name[:10].upper()


# ==================== 1. FETCH SUBJECTS API (Regular) ====================

@frappe.whitelist(allow_guest=True)
def get_bulk_subjects_for_year(academic_year=None, is_little_champ=0):
	try:
		if not academic_year:
			customer_name = frappe.db.get_value("Portal User",
				{"user": frappe.session.user}, "parent")
			if customer_name:
				academic_year = frappe.db.get_value("Customer", customer_name,
					"custom_current_academic_year") or "AY-2026/27"
			else:
				academic_year = "AY-2026/27"

		subjects = get_subjects_for_year(academic_year, is_little_champ)

		return {
			"success": True,
			"subjects": subjects,
			"academic_year": academic_year
		}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Get Bulk Subjects Error")
		return {"success": False, "message": str(e), "subjects": []}


# ==================== 2. FETCH SUBJECTS API (Little Champ) ====================

@frappe.whitelist(allow_guest=True)
def get_little_champ_subjects_for_year(academic_year=None):
	try:
		if not academic_year:
			customer_name = frappe.db.get_value("Portal User",
				{"user": frappe.session.user}, "parent")
			if customer_name:
				academic_year = frappe.db.get_value("Customer", customer_name,
					"custom_current_academic_year") or "AY-2026/27"
			else:
				academic_year = "AY-2026/27"

		subjects = get_subjects_for_year(academic_year, is_little_champ=1)

		return {
			"success": True,
			"subjects": subjects,
			"academic_year": academic_year
		}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Get Little Champ Subjects Error")
		return {"success": False, "message": str(e), "subjects": []}


# ==================== 3. DOWNLOAD TEMPLATE (Regular) ====================

@frappe.whitelist(allow_guest=True)
def download_bulk_student_template():
	try:
		data = frappe.request.args or {}
		academic_year = data.get("academic_year", "")
		is_little_champ = int(data.get("is_little_champ", 0))

		if not academic_year:
			customer_name = frappe.db.get_value("Portal User",
				{"user": frappe.session.user}, "parent")
			if customer_name:
				academic_year = frappe.db.get_value("Customer", customer_name,
					"custom_current_academic_year") or "AY-2026/27"
			else:
				academic_year = "AY-2026/27"

		subjects = get_subjects_for_year(academic_year, is_little_champ)

		if not subjects:
			return {"success": False, "message": "No subjects configured for this academic year"}

		buffer = io.StringIO()
		writer = csv.writer(buffer)

		headers = ["Sr. No.", "Student Name", "Mobile No."]
		for sub in subjects:
			headers.append(sub["shortName"])

		writer.writerow(headers)

		demo_row = ["001", "RAHUL SHARMA", "+91 9876543210"]
		for sub in subjects:
			demo_row.append("1")
		writer.writerow(demo_row)

		for i in range(2, 11):
			empty_row = [str(i).zfill(3), "", ""]
			for _ in subjects:
				empty_row.append("0")
			writer.writerow(empty_row)

		csv_content = buffer.getvalue()
		buffer.close()

		filename = f"ITO_BulkStudent_Template_{frappe.utils.today()}.csv"

		frappe.local.response.filename = filename
		frappe.local.response.filecontent = csv_content
		frappe.local.response.type = "download"
		frappe.local.response.content_type = "text/csv; charset=utf-8"

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Bulk Student Template Download Error")
		return {"success": False, "message": str(e)}


# ==================== 4. DOWNLOAD TEMPLATE (Little Champ) ====================

@frappe.whitelist(allow_guest=True)
def download_little_champ_template():
	try:
		data = frappe.request.args or {}
		academic_year = data.get("academic_year", "")

		if not academic_year:
			customer_name = frappe.db.get_value("Portal User",
				{"user": frappe.session.user}, "parent")
			if customer_name:
				academic_year = frappe.db.get_value("Customer", customer_name,
					"custom_current_academic_year") or "AY-2026/27"
			else:
				academic_year = "AY-2026/27"

		subjects = get_subjects_for_year(academic_year, is_little_champ=1)

		if not subjects:
			return {"success": False, "message": "No subjects configured for this academic year"}

		buffer = io.StringIO()
		writer = csv.writer(buffer)

		headers = ["Sr. No.", "Student Name", "Mobile No."]
		for sub in subjects:
			headers.append(sub["shortName"])

		writer.writerow(headers)

		demo_row = ["001", "RAHUL SHARMA", "+91 9876543210"]
		for sub in subjects:
			demo_row.append("1")
		writer.writerow(demo_row)

		for i in range(2, 11):
			empty_row = [str(i).zfill(3), "", ""]
			for _ in subjects:
				empty_row.append("0")
			writer.writerow(empty_row)

		csv_content = buffer.getvalue()
		buffer.close()

		filename = f"ITO_LittleChamp_Template_{frappe.utils.today()}.csv"

		frappe.local.response.filename = filename
		frappe.local.response.filecontent = csv_content
		frappe.local.response.type = "download"
		frappe.local.response.content_type = "text/csv; charset=utf-8"

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Little Champ Template Download Error")
		return {"success": False, "message": str(e)}


# ==================== 5. UPLOAD TEMPLATE (Regular) ====================

@frappe.whitelist(allow_guest=True)
def upload_bulk_student_template():
	try:
		data = frappe.request.get_json() or {}
		filedata = data.get('filedata', '')
		filename = data.get('filename', 'template.csv')

		customer_name = frappe.db.get_value("Portal User",
			{"user": frappe.session.user}, "parent")
		if customer_name:
			academic_year = frappe.db.get_value("Customer", customer_name,
				"custom_current_academic_year") or "AY-2026/27"
		else:
			academic_year = "AY-2026/27"

		if not filedata:
			return {"success": False, "message": "No file data provided"}

		if ',' in filedata:
			filedata = filedata.split(',')[1]

		try:
			decoded = base64.b64decode(filedata)
			file_content = decoded.decode('utf-8')
		except Exception:
			return {"success": False, "message": "Invalid file format. Please upload a valid CSV file."}

		subjects = get_subjects_for_year(academic_year, is_little_champ=0)
		if not subjects:
			return {"success": False, "message": "No subjects configured for this academic year"}

		buffer = io.StringIO(file_content)
		reader = csv.reader(buffer)

		rows = list(reader)

		if len(rows) < 2:
			return {"success": False, "message": "Invalid CSV format. File is too short."}

		header = rows[0]

		subject_cols = []
		for i, h in enumerate(header):
			if i >= 3:
				for sub in subjects:
					if h.strip().upper() == sub["shortName"].upper():
						subject_cols.append({
							"col_index": i,
							"code": sub["code"],
							"shortName": sub["shortName"]
						})
						break

		students = []

		for idx, row in enumerate(rows[1:], start=2):
			if not row or len(row) < 3:
				continue

			if not any(cell.strip() for cell in row):
				continue

			sr_no = row[0].strip()
			student_name = row[1].strip() if len(row) > 1 else ""

			if not student_name or sr_no.lower() in ['sr. no.', 's.no', 'serial', '']:
				continue

			def parse_subject(val):
				if val is None or val == "":
					return False
				return str(val).strip() == "1"

			subjects_dict = {}
			for sc in subject_cols:
				val = row[sc["col_index"]] if len(row) > sc["col_index"] else "0"
				subjects_dict[sc["code"]] = parse_subject(val)

			checked_count = sum(subjects_dict.values())
			free_slots = checked_count // 4
			paid_count = checked_count - free_slots

			student = {
				"serialNum": sr_no.zfill(3) if sr_no.isdigit() else str(len(students) + 1).zfill(3),
				"student_name": student_name.upper(),
				"mobile": row[2].strip() if len(row) > 2 else "",
				"subjects": subjects_dict,
				"paidCount": paid_count,
				"freeCount": free_slots,
			}

			students.append(student)

		buffer.close()

		if not students:
			return {"success": False, "message": "No valid student data found in the uploaded file"}

		return {
			"success": True,
			"message": f"Successfully parsed {len(students)} students",
			"students": students,
		}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Bulk Student Template Upload Error")
		return {"success": False, "message": str(e)}


# ==================== 6. UPLOAD TEMPLATE (Little Champ) ====================

@frappe.whitelist(allow_guest=True)
def upload_little_champ_template():
	try:
		data = frappe.request.get_json() or {}
		filedata = data.get('filedata', '')
		filename = data.get('filename', 'template.csv')

		customer_name = frappe.db.get_value("Portal User",
			{"user": frappe.session.user}, "parent")
		if customer_name:
			academic_year = frappe.db.get_value("Customer", customer_name,
				"custom_current_academic_year") or "AY-2026/27"
		else:
			academic_year = "AY-2026/27"

		if not filedata:
			return {"success": False, "message": "No file data provided"}

		if ',' in filedata:
			filedata = filedata.split(',')[1]

		try:
			decoded = base64.b64decode(filedata)
			file_content = decoded.decode('utf-8')
		except Exception:
			return {"success": False, "message": "Invalid file format. Please upload a valid CSV file."}

		subjects = get_subjects_for_year(academic_year, is_little_champ=1)
		if not subjects:
			return {"success": False, "message": "No subjects configured for this academic year"}

		buffer = io.StringIO(file_content)
		reader = csv.reader(buffer)

		rows = list(reader)

		if len(rows) < 2:
			return {"success": False, "message": "Invalid CSV format. File is too short."}

		header = rows[0]

		subject_cols = []
		for i, h in enumerate(header):
			if i >= 3:
				for sub in subjects:
					if h.strip().upper() == sub["shortName"].upper():
						subject_cols.append({
							"col_index": i,
							"code": sub["code"],
							"shortName": sub["shortName"]
						})
						break

		students = []

		for idx, row in enumerate(rows[1:], start=2):
			if not row or len(row) < 3:
				continue

			if not any(cell.strip() for cell in row):
				continue

			sr_no = row[0].strip()
			student_name = row[1].strip() if len(row) > 1 else ""

			if not student_name or sr_no.lower() in ['sr. no.', 's.no', 'serial', '']:
				continue

			def parse_subject(val):
				if val is None or val == "":
					return False
				return str(val).strip() == "1"

			subjects_dict = {}
			for sc in subject_cols:
				val = row[sc["col_index"]] if len(row) > sc["col_index"] else "0"
				subjects_dict[sc["code"]] = parse_subject(val)

			checked_count = sum(subjects_dict.values())
			free_slots = checked_count // 4
			paid_count = checked_count - free_slots

			student = {
				"serialNum": sr_no.zfill(3) if sr_no.isdigit() else str(len(students) + 1).zfill(3),
				"student_name": student_name.upper(),
				"mobile": row[2].strip() if len(row) > 2 else "",
				"subjects": subjects_dict,
				"paidCount": paid_count,
				"freeCount": free_slots,
			}

			students.append(student)

		buffer.close()

		if not students:
			return {"success": False, "message": "No valid student data found in the uploaded file"}

		return {
			"success": True,
			"message": f"Successfully parsed {len(students)} students",
			"students": students,
		}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Little Champ Template Upload Error")
		return {"success": False, "message": str(e)}


# ==================== 7. SAVE BULK STUDENT LIST (Regular) ====================

@frappe.whitelist(allow_guest=True)
def save_bulk_student_list():
	try:
		data = frappe.request.get_json() or {}
		payload = json.loads(data.get("data", "{}")) if isinstance(data.get("data"), str) else data.get("data", {})

		school_info = payload.get("school_info", {})
		students = payload.get("students", [])

		if not students:
			return {"success": False, "message": "No students provided"}

		customer_name = frappe.db.get_value("Portal User", {"user": frappe.session.user}, "parent")
		if not customer_name:
			return {"success": False, "message": "Customer not found"}

		raw_year = school_info.get("academic_year", "")
		if not raw_year:
			raw_year = frappe.db.get_value("Customer", customer_name, "custom_current_academic_year") or "2026-27"

		if raw_year.startswith("AY-"):
			academic_year = raw_year
		else:
			parts = raw_year.replace("-", "/").split("/")
			if len(parts) == 2:
				academic_year = f"AY-{parts[0]}/{parts[1]}"
			else:
				academic_year = f"AY-{raw_year}"

		if not frappe.db.exists("Academic Years", academic_year):
			existing = frappe.db.get_value("Academic Years", {}, "name", order_by="creation desc")
			if existing:
				academic_year = existing

		dynamic_subjects = get_subjects_for_year(academic_year, 0)
		valid_subject_codes = [s["code"] for s in dynamic_subjects]

		original_flag = frappe.flags.ignore_permissions
		frappe.flags.ignore_permissions = True

		try:
			bsl_name = frappe.db.get_value("Bulk Student List", {
				"customer": customer_name,
				"academic_year": academic_year
			})

			if bsl_name:
				bsl = frappe.get_doc("Bulk Student List", bsl_name)
				bsl.student_list = []
			else:
				bsl = frappe.new_doc("Bulk Student List")
				bsl.customer = customer_name
				bsl.academic_year = academic_year

			bsl.for_little_champ = 0
			bsl.for_student = 1

			for student in students:
				subjects = student.get("subjects", {})

				row_data = {
					"student_name": student.get("student_name", ""),
					"mobile_no": student.get("mobile", ""),
				}

				for code in valid_subject_codes:
					row_data[code] = 1 if subjects.get(code, False) else 0

				bsl.append("student_list", row_data)

			if bsl.is_new():
				bsl.insert(ignore_permissions=True)
			else:
				bsl.save(ignore_permissions=True)

			# NO explicit commit - let Frappe handle it
			return {"success": True, "name": bsl.name}

		finally:
			frappe.flags.ignore_permissions = original_flag

	except Exception as e:
		# NO explicit rollback - let Frappe handle exceptions
		frappe.log_error(frappe.get_traceback(), "Bulk Student List Save Error")
		return {"success": False, "message": str(e)}


# ==================== 8. SAVE LITTLE CHAMP BULK ====================

@frappe.whitelist(allow_guest=True)
def save_little_champ_bulk():
	try:
		data = frappe.request.get_json() or {}
		payload = json.loads(data.get("data", "{}")) if isinstance(data.get("data"), str) else data.get("data", {})

		school_info = payload.get("school_info", {})
		students = payload.get("students", [])

		if not students:
			return {"success": False, "message": "No students provided"}

		customer_name = frappe.db.get_value("Portal User", {"user": frappe.session.user}, "parent")
		if not customer_name:
			return {"success": False, "message": "Customer not found"}

		raw_year = school_info.get("academic_year", "")
		if not raw_year:
			raw_year = frappe.db.get_value("Customer", customer_name, "custom_current_academic_year") or "2026-27"

		if raw_year.startswith("AY-"):
			academic_year = raw_year
		else:
			parts = raw_year.replace("-", "/").split("/")
			if len(parts) == 2:
				academic_year = f"AY-{parts[0]}/{parts[1]}"
			else:
				academic_year = f"AY-{raw_year}"

		if not frappe.db.exists("Academic Years", academic_year):
			existing = frappe.db.get_value("Academic Years", {}, "name", order_by="creation desc")
			if existing:
				academic_year = existing

		dynamic_subjects = get_subjects_for_year(academic_year, is_little_champ=1)
		valid_subject_codes = [s["code"] for s in dynamic_subjects]

		original_flag = frappe.flags.ignore_permissions
		frappe.flags.ignore_permissions = True

		try:
			bsl_name = frappe.db.get_value("Bulk Student List", {
				"customer": customer_name,
				"academic_year": academic_year,
				"for_little_champ": 1
			})

			if bsl_name:
				bsl = frappe.get_doc("Bulk Student List", bsl_name)
				bsl.student_list = []
			else:
				bsl = frappe.new_doc("Bulk Student List")
				bsl.customer = customer_name
				bsl.academic_year = academic_year

			bsl.for_little_champ = 1
			bsl.for_student = 0

			for student in students:
				subjects = student.get("subjects", {})

				row_data = {
					"student_name": student.get("student_name", ""),
					"mobile_no": student.get("mobile", ""),
				}

				for code in valid_subject_codes:
					row_data[code] = 1 if subjects.get(code, False) else 0

				bsl.append("student_list", row_data)

			if bsl.is_new():
				bsl.insert(ignore_permissions=True)
			else:
				bsl.save(ignore_permissions=True)

			# NO explicit commit - let Frappe handle it
			return {"success": True, "name": bsl.name}

		finally:
			frappe.flags.ignore_permissions = original_flag

	except Exception as e:
		# NO explicit rollback - let Frappe handle exceptions
		frappe.log_error(frappe.get_traceback(), "Little Champ Bulk Save Error")
		return {"success": False, "message": str(e)}


@frappe.whitelist(allow_guest=True)
def debug_subjects():
	"""
	Debug endpoint to check what's happening with subject fetching.
	"""
	import inspect
	
	# Get the source of get_subjects_for_year
	try:
		source = inspect.getsource(get_subjects_for_year)
	except:
		source = "Could not get source"
	
	# Check database directly
	yed = frappe.get_all("Yearly Exam Date",
		filters={"academic_year": "AY-2026/27"},
		fields=["name"],
		limit=1)
	
	yed_name = yed[0].name if yed else None
	
	children = []
	if yed_name:
		children = frappe.get_all("Yearly Exam Date CT",
			filters={"parent": yed_name},
			fields=["subject", "school_subject", "idx", "is_little_champ"],
			order_by="idx")
	
	return {
		"success": True,
		"yed_found": yed_name,
		"yed_name": yed_name,
		"children_count": len(children),
		"children": children,
		"function_source_preview": source[:500] if source else "N/A",
		"has_lc_column": frappe.db.has_column("Yearly Exam Date CT", "is_little_champ")
	}


@frappe.whitelist(allow_guest=True)
def save_parent_consent(data):
	try:
		payload = json.loads(data) if isinstance(data, str) else data
		
		student_profile = payload.get("student_profile", {})
		is_little_champ = payload.get("is_little_champ", False)
		selections = payload.get("selections", [])
		payment = payload.get("payment", {})
		token = payload.get("token")
		
		import uuid
		book_order_token = str(uuid.uuid4())

		# 1. Map fields
		# Resolve via the consent token first (same lookup as get_school_by_token) -
		# more reliable than matching on customer_name, which can collide/mismatch
		# on case or whitespace.
		school_name = student_profile.get("school_name")
		customer = None
		if token:
			customer = frappe.db.get_value("Customer", {"custom_consent_token": token}, "name")
		if not customer and school_name:
			customer = frappe.db.get_value("Customer", {"customer_name": school_name})

		# 1a. Recompute the fee server-side - never trust the client's
		# total_amount, which can be stale (e.g. per-subject pricing hadn't
		# finished loading in the browser yet) as well as manipulated. This
		# mirrors initiate_parent_consent_payment's calculation exactly.
		from ito_customization.ito_customization.parent_consent_payment import (
			_compute_grand_total,
		)

		selected_class = payload.get("selected_class") or student_profile.get("class_grade")
		total_amount = _compute_grand_total(school_name, selected_class, selections)
		sales_invoice = payment.get("sales_invoice")
		payment_entry = payment.get("payment_entry")

		# 1b. If fees are due, require a confirmed payment covering that amount
		# before finalizing the consent - the frontend already gates the submit
		# button on this, this is the server-side backstop.
		if total_amount > 0:
			if not sales_invoice:
				frappe.throw(_("Please complete the payment before submitting."))
			invoice = frappe.db.get_value(
				"Sales Invoice", sales_invoice, ["outstanding_amount", "grand_total"], as_dict=True
			)
			if not invoice or flt(invoice.outstanding_amount) > 0:
				frappe.throw(_("Payment has not been confirmed for this consent yet."))
			if flt(invoice.grand_total) < total_amount - 1:
				frappe.throw(
					_("The paid amount does not cover the current fee total. Please retry payment.")
				)

		gender_map = {
			"male": "Boy",
			"female": "Girl"
		}
		gender = gender_map.get(student_profile.get("gender"), student_profile.get("gender"))
		
		# 2. Create Parent Consent doc
		pc_doc = frappe.new_doc("Parent Consent Registration")
		pc_doc.customer = customer
		pc_doc.is_little_champ = 1 if is_little_champ else 0
		pc_doc.student_name = student_profile.get("student_name")
		pc_doc.gender = gender
		# Fieldname in parent_consent.json is "class" (reserved word in Python),
		# so it must be set via pc_doc.set(...) rather than attribute access.
		pc_doc.set("class", student_profile.get("class_grade"))
		pc_doc.section = student_profile.get("section")
		pc_doc.parent_name = student_profile.get("parent_name")
		pc_doc.email = student_profile.get("parent_email")
		pc_doc.city = student_profile.get("city")
		pc_doc.mobile_no = student_profile.get("parent_mobile")
		pc_doc.total_amount = total_amount
		pc_doc.sales_invoice = sales_invoice
		pc_doc.payment_entry = payment_entry
		pc_doc.book_order_token = book_order_token

		# 3. Populate Book Order Selection child table
		for sel in selections:
			sub_code = sel.get("subject_code")
			subject_name = sub_code

			# Resolve School Subject
			if not frappe.db.exists("School Subject", sub_code):
				found_sub = frappe.db.get_value(
					"School Subject",
					{"name": ["like", f"%({sub_code})%"]},
					"name"
				)
				if found_sub:
					subject_name = found_sub

			# pc_doc.append("book_order_selection", {
			# 	"subject": subject_name,
			# 	"workbook": 1 if sel.get("wb") else 0,
			# 	"student_guide": 1 if sel.get("sg") else 0,
			# 	"past_paper": 1 if sel.get("yp") else 0,
			# 	"text_book": 1 if sel.get("tb") else 0,
			# })

		pc_doc.insert(ignore_permissions=True)
		
		return {"success": True, "message": "Parent Consent registered successfully", "docname": pc_doc.name, "book_order_token": book_order_token}
		
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Save Parent Consent Error")
		return {"success": False, "message": str(e)}


# ==================== WOF STUDENT LIST APIS ====================

@frappe.whitelist(allow_guest=True)
def save_wof_student_list():
	original_flag = frappe.flags.ignore_permissions
	try:
		frappe.flags.ignore_permissions = True
		data = frappe.request.get_json() or {}
		payload = json.loads(data.get("data", "{}")) if isinstance(data.get("data"), str) else data.get("data", {})

		school_info = payload.get("school_info", {})
		rosters = payload.get("rosters", {})
		totals = payload.get("totals", {})

		customer_name = frappe.db.get_value("Portal User", {"user": frappe.session.user}, "parent")
		if not customer_name:
			school_code = school_info.get("school_code")
			if school_code:
				customer_name = frappe.db.get_value("Customer", {"custom_ito_school_code": school_code})
		if not customer_name:
			customer_name = frappe.db.get_value("Customer", {"custom_is_wof": 1}, "name")

		if not customer_name:
			return {"success": False, "message": "Customer not found"}

		raw_year = school_info.get("academic_year", "")
		if not raw_year:
			raw_year = frappe.db.get_value("Customer", customer_name, "custom_current_academic_year") or "AY-2026/27"

		if raw_year.startswith("AY-"):
			academic_year = raw_year
		else:
			parts = raw_year.replace("-", "/").split("/")
			if len(parts) == 2:
				academic_year = f"AY-{parts[0]}/{parts[1]}"
			else:
				academic_year = f"AY-{raw_year}"

		if not frappe.db.exists("School Academic Year", academic_year):
			existing = frappe.db.get_value("School Academic Year", {}, "name", order_by="creation desc")
			if existing:
				academic_year = existing

		# Find existing unsubmitted Bulk Student List for this customer or create new
		bsl_name = frappe.db.get_value("Bulk Student List", {
			"customer": customer_name,
			"for_wof_list": 1,
			"is_submitted": 0
		}, "name", order_by="creation desc")

		if bsl_name:
			bsl = frappe.get_doc("Bulk Student List", bsl_name)
			bsl.table_xxdu = []
		else:
			bsl = frappe.new_doc("Bulk Student List")
			bsl.customer = customer_name

		bsl.academic_year = academic_year
		bsl.for_wof_list = 1
		bsl.for_student = 0
		bsl.for_little_champ = 0
		bsl.is_submitted = 1

		# Append students to table_xxdu (WOF List child table)
		for batch_key, batch_data in rosters.items():
			students = []
			if isinstance(batch_data, dict):
				students = batch_data.get("students", [])
			elif isinstance(batch_data, list):
				students = batch_data

			for st in students:
				student_name = (st.get("student_name") or st.get("name") or "").strip().upper()
				parent_name = (st.get("parent_name") or st.get("parentName") or "").strip()
				subs = st.get("subjects", {})

				if not student_name and not parent_name and not any(subs.values()):
					continue

				row_data = {
					"student_name": student_name,
					"parent_name": parent_name,
					"colouring": 1 if subs.get("colouring") else 0,
					"handwriting": 1 if subs.get("handwriting") else 0,
					"sketching": 1 if subs.get("sketching") else 0,
					"cartoon": 1 if subs.get("cartoon") else 0,
					"caricature": 1 if subs.get("caricature") else 0,
					"greeting_card": 1 if (subs.get("greeting_card") or subs.get("greeting")) else 0,
				}
				bsl.append("table_xxdu", row_data)

		if bsl.is_new():
			bsl.insert(ignore_permissions=True)
		else:
			bsl.save(ignore_permissions=True)

		# Clear draft cache on final submission
		try:
			frappe.cache().delete_value(f"wof_student_draft_{customer_name}_{academic_year}")
			frappe.cache().delete_value(f"wof_student_draft_{customer_name}_{raw_year}")
			frappe.cache().delete_value(f"wof_student_draft_{customer_name}_AY-2026/27")
		except Exception:
			pass

		# Log comment on customer
		try:
			customer_doc = frappe.get_doc("Customer", customer_name)
			total_students = totals.get("total_students", len(bsl.table_xxdu))
			total_entries = totals.get("total_entries", 0)
			total_batches = totals.get("total_batches", len(rosters))
			customer_doc.add_comment(
				"Info",
				f"WOF Student List submitted: {total_students} students across {total_batches} batches with {total_entries} total competition entries ({academic_year})."
			)
		except Exception:
			pass

		return {
			"success": True,
			"name": bsl.name,
			"message": "WOF Student list submitted and saved to Bulk Student List successfully",
			"totals": totals
		}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Save WOF Student List Error")
		return {"success": False, "message": str(e)}
	finally:
		frappe.flags.ignore_permissions = original_flag


@frappe.whitelist(allow_guest=True)
def save_wof_student_draft():
	original_flag = frappe.flags.ignore_permissions
	try:
		frappe.flags.ignore_permissions = True
		data = frappe.request.get_json() or {}
		payload = json.loads(data.get("data", "{}")) if isinstance(data.get("data"), str) else data.get("data", {})

		school_info = payload.get("school_info", {})
		rosters = payload.get("rosters", {})
		customer_name = frappe.db.get_value("Portal User", {"user": frappe.session.user}, "parent")
		if not customer_name:
			school_code = school_info.get("school_code")
			if school_code:
				customer_name = frappe.db.get_value("Customer", {"custom_ito_school_code": school_code})
		if not customer_name:
			customer_name = frappe.db.get_value("Customer", {"custom_is_wof": 1}, "name")

		if not customer_name:
			return {"success": False, "message": "Customer not found"}

		raw_year = school_info.get("academic_year", "")
		if not raw_year:
			raw_year = frappe.db.get_value("Customer", customer_name, "custom_current_academic_year") or "AY-2026/27"

		if raw_year.startswith("AY-"):
			academic_year = raw_year
		else:
			parts = raw_year.replace("-", "/").split("/")
			if len(parts) == 2:
				academic_year = f"AY-{parts[0]}/{parts[1]}"
			else:
				academic_year = f"AY-{raw_year}"

		if not frappe.db.exists("School Academic Year", academic_year):
			existing = frappe.db.get_value("School Academic Year", {}, "name", order_by="creation desc")
			if existing:
				academic_year = existing

		# Save or update unsubmitted draft in Bulk Student List doctype
		bsl_name = frappe.db.get_value("Bulk Student List", {
			"customer": customer_name,
			"for_wof_list": 1,
			"is_submitted": 0
		}, "name", order_by="creation desc")

		if bsl_name:
			bsl = frappe.get_doc("Bulk Student List", bsl_name)
			bsl.table_xxdu = []
		else:
			bsl = frappe.new_doc("Bulk Student List")
			bsl.customer = customer_name

		bsl.academic_year = academic_year
		bsl.for_wof_list = 1
		bsl.for_student = 0
		bsl.for_little_champ = 0
		bsl.is_submitted = 0

		# Append students to table_xxdu (WOF List child table)
		for batch_key, batch_data in rosters.items():
			students = []
			if isinstance(batch_data, dict):
				students = batch_data.get("students", [])
			elif isinstance(batch_data, list):
				students = batch_data

			for st in students:
				student_name = (st.get("student_name") or st.get("name") or "").strip().upper()
				parent_name = (st.get("parent_name") or st.get("parentName") or "").strip()
				subs = st.get("subjects", {})

				if not student_name and not parent_name and not any(subs.values()):
					continue

				row_data = {
					"student_name": student_name,
					"parent_name": parent_name,
					"colouring": 1 if subs.get("colouring") else 0,
					"handwriting": 1 if subs.get("handwriting") else 0,
					"sketching": 1 if subs.get("sketching") else 0,
					"cartoon": 1 if subs.get("cartoon") else 0,
					"caricature": 1 if subs.get("caricature") else 0,
					"greeting_card": 1 if (subs.get("greeting_card") or subs.get("greeting")) else 0,
				}
				bsl.append("table_xxdu", row_data)

		if bsl.is_new():
			bsl.insert(ignore_permissions=True)
		else:
			bsl.save(ignore_permissions=True)

		frappe.cache().set_value(f"wof_student_draft_{customer_name}_{academic_year}", payload)
		frappe.cache().set_value(f"wof_student_draft_{customer_name}_{raw_year}", payload)

		return {
			"success": True,
			"name": bsl.name,
			"message": "Draft saved to Bulk Student List successfully"
		}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Save WOF Student Draft Error")
		return {"success": False, "message": str(e)}
	finally:
		frappe.flags.ignore_permissions = original_flag


@frappe.whitelist(allow_guest=True)
def get_wof_student_draft(academic_year="AY-2026/27"):
	try:
		customer_name = frappe.db.get_value("Portal User", {"user": frappe.session.user}, "parent")
		if not customer_name:
			return {"success": False, "message": "Customer not found", "data": None}

		saved_data = frappe.cache().get_value(f"wof_student_draft_{customer_name}_{academic_year}")
		return {
			"success": True,
			"data": saved_data or None
		}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Get WOF Student Draft Error")
		return {"success": False, "message": str(e), "data": None}


# ==================== WOF TEACHER ENTRY APIS ====================

@frappe.whitelist(allow_guest=True)
def save_wof_teacher_entry():
	original_flag = frappe.flags.ignore_permissions
	try:
		frappe.flags.ignore_permissions = True
		data = frappe.request.get_json() or {}
		payload = json.loads(data.get("data", "{}")) if isinstance(data.get("data"), str) else data.get("data", {})

		teacher_info = payload.get("teacher_info", {})
		school_info = payload.get("school_info", {})
		financial_ledger = payload.get("financial_ledger", {})
		honorarium_slab = payload.get("honorarium_slab", {})
		form_date = payload.get("form_date") or frappe.utils.today()

		# Resolve Customer
		customer_name = frappe.db.get_value("Portal User", {"user": frappe.session.user}, "parent")
		if not customer_name:
			school_code = school_info.get("school_code")
			if school_code:
				customer_name = frappe.db.get_value("Customer", {"custom_ito_school_code": school_code})

		if not customer_name:
			return {"success": False, "message": "Customer/School account not found for current session."}

		customer_doc = frappe.get_doc("Customer", customer_name)

		# Ensure School Subject exists
		ensure_subject_exists("Art Teacher / Coordinator")

		teacher_name = (teacher_info.get("name") or "").strip().upper()
		if not teacher_name:
			return {"success": False, "message": "Art Teacher / Coordinator Name is required."}

		# Check existing Teacher by name1 & customer_reference
		existing_teacher_name = frappe.db.get_value("Teacher", {
			"name1": teacher_name,
			"customer_reference": customer_doc.name
		})

		if existing_teacher_name:
			teacher_doc = frappe.get_doc("Teacher", existing_teacher_name)
		else:
			teacher_doc = frappe.new_doc("Teacher")

		full_address = ", ".join(filter(None, [
			(teacher_info.get("address") or "").strip(),
			(teacher_info.get("city") or "").strip(),
			(teacher_info.get("taluka") or "").strip(),
			(teacher_info.get("district") or "").strip(),
			(teacher_info.get("state") or "").strip(),
			(teacher_info.get("pincode") or "").strip()
		]))

		teacher_doc.name1 = teacher_name
		teacher_doc.date_of_birth = teacher_info.get("dob") or "1990-01-01"
		teacher_doc.phone_number = (teacher_info.get("mobile") or "").strip()
		teacher_doc.email_id = (teacher_info.get("email") or "").strip()
		teacher_doc.school_name = customer_doc.name
		teacher_doc.customer_reference = customer_doc.name
		teacher_doc.status = "Active"
		teacher_doc.experience = "Experienced"
		teacher_doc.subject = "Art Teacher / Coordinator"
		teacher_doc.address = full_address

		if teacher_doc.is_new():
			teacher_doc.insert(ignore_permissions=True)
		else:
			teacher_doc.save(ignore_permissions=True)

		# Sync with Customer custom_school_teacher_details table if not present
		existing_row = None
		for row in customer_doc.custom_school_teacher_details:
			if row.name1 == teacher_doc.name:
				existing_row = row
				break

		if not existing_row:
			customer_doc.append("custom_school_teacher_details", {
				"name1": teacher_doc.name,
				"date_of_birth": teacher_doc.date_of_birth,
				"phone_number": teacher_doc.phone_number,
				"status": teacher_doc.status,
				"email_id": teacher_doc.email_id,
				"subject": teacher_doc.subject,
				"experience": teacher_doc.experience
			})
			customer_doc.save(ignore_permissions=True)

		# Clear teacher draft cache
		raw_year = school_info.get("academic_year", "AY-2026/27")
		frappe.cache().delete_value(f"wof_teacher_draft_{customer_name}_{raw_year}")

		return {
			"success": True,
			"message": "Art Teacher & Coordinator Entry Form submitted successfully!",
			"teacher_name": teacher_doc.name
		}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Save WOF Teacher Entry Error")
		return {"success": False, "message": str(e)}
	finally:
		frappe.flags.ignore_permissions = original_flag


@frappe.whitelist(allow_guest=True)
def save_wof_teacher_draft():
	try:
		data = frappe.request.get_json() or {}
		payload = json.loads(data.get("data", "{}")) if isinstance(data.get("data"), str) else data.get("data", {})

		school_info = payload.get("school_info", {})
		customer_name = frappe.db.get_value("Portal User", {"user": frappe.session.user}, "parent")
		if not customer_name:
			school_code = school_info.get("school_code")
			if school_code:
				customer_name = frappe.db.get_value("Customer", {"custom_ito_school_code": school_code})

		raw_year = school_info.get("academic_year", "AY-2026/27")
		if customer_name:
			frappe.cache().set_value(f"wof_teacher_draft_{customer_name}_{raw_year}", payload)

		return {"success": True, "message": "Teacher entry draft saved successfully"}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Save WOF Teacher Draft Error")
		return {"success": False, "message": str(e)}


@frappe.whitelist(allow_guest=True)
def get_wof_teacher_draft(academic_year="AY-2026/27"):
	try:
		customer_name = frappe.db.get_value("Portal User", {"user": frappe.session.user}, "parent")
		if not customer_name:
			return {"success": False, "message": "Customer not found", "data": None}

		saved_data = frappe.cache().get_value(f"wof_teacher_draft_{customer_name}_{academic_year}")
		return {
			"success": True,
			"data": saved_data or None
		}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Get WOF Teacher Draft Error")
		return {"success": False, "message": str(e), "data": None}


# ==================== DYNAMIC REGISTRATION FEE API ====================

@frappe.whitelist(allow_guest=True)
def get_registration_fee(form_name="Little Champ Registration"):
	"""
	Fetch dynamic registration fee strictly from Item where custom_is_registration_item=1 and custom_form_name=form_name.
	Returns rate_inr and rate_usd from the corresponding Item Price or Item standard_rate.
	"""
	try:
		item = frappe.db.get_value(
			"Item",
			{"custom_is_registration_item": 1, "custom_form_name": form_name, "disabled": 0},
			["name", "item_code", "item_name", "standard_rate"],
			as_dict=True
		)

		if not item and form_name == "WOF Registration":
			item = frappe.db.get_value(
				"Item",
				{"item_name": "WOF Registration Fee", "disabled": 0},
				["name", "item_code", "item_name", "standard_rate"],
				as_dict=True
			)

		rate_inr = None
		rate_usd = None
		custom_retention = 0.0
		item_code = ""
		item_name = ""

		if item:
			item_code = item.get("name") or item.get("item_code")
			item_name = item.get("item_name") or item_code

			try:
				ret_val = frappe.db.get_value("Item", item_code, "custom_retention")
				if ret_val is not None and ret_val != "":
					custom_retention = flt(ret_val)
			except Exception:
				pass

			# Fetch active Item Price for INR and USD
			prices = frappe.get_all(
				"Item Price",
				filters={"item_code": item_code, "selling": 1},
				fields=["price_list_rate", "currency"],
				order_by="modified desc"
			)
			for p in prices:
				if p.currency == "INR" and rate_inr is None:
					rate_inr = flt(p.price_list_rate)
				elif p.currency == "USD" and rate_usd is None:
					rate_usd = flt(p.price_list_rate)

			if rate_inr is None:
				any_inr = frappe.db.get_value("Item Price", {"item_code": item_code, "currency": "INR"}, "price_list_rate", order_by="modified desc")
				if any_inr is not None:
					rate_inr = flt(any_inr)

			if rate_inr is None:
				any_price = frappe.db.get_value("Item Price", {"item_code": item_code}, "price_list_rate", order_by="modified desc")
				if any_price is not None:
					rate_inr = flt(any_price)

			if rate_usd is None:
				any_usd = frappe.db.get_value("Item Price", {"item_code": item_code, "currency": "USD"}, "price_list_rate", order_by="modified desc")
				if any_usd is not None:
					rate_usd = flt(any_usd)

			if rate_inr is None and item.get("standard_rate") and flt(item.get("standard_rate")) > 0:
				rate_inr = flt(item.get("standard_rate"))

		return {
			"success": True,
			"item_code": item_code,
			"item_name": item_name,
			"rate_inr": flt(rate_inr) if rate_inr is not None else 0.0,
			"rate_usd": flt(rate_usd) if rate_usd is not None else 0.0,
			"custom_retention": flt(custom_retention),
		}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Get Registration Fee Error")
		return {
			"success": False,
			"message": str(e),
			"rate_inr": 0.0,
			"rate_usd": 0.0,
			"custom_retention": 0.0
		}