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
		"registration_fee_rate": registration_fee_rate,
		"is_little_champ": bool(customer_doc.get("custom_is_little_champ"))
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
                customer = (
                    frappe.db.get_value("Customer", {"custom_school_code": doc.school_code}, "name")
                    or frappe.db.get_value("Customer", {"custom_ito_school_code": doc.school_code}, "name")
                    or frappe.db.get_value("Customer", {"name": doc.school_code}, "name")
                )

            if not customer or not frappe.db.exists("Customer", customer):
                return None, None

            from ito_customization.ito_customization.api import get_registration_fee
            fee_info = get_registration_fee("Student Portal Registration")
            item_code = fee_info.get("item_code") if fee_info and fee_info.get("success") else None
            rate = flt(fee_info.get("rate_inr") or fee_info.get("rate_usd") or 150) if fee_info and fee_info.get("success") else 150.0

            # --- item_code resolution unchanged ---

            company = frappe.db.get_single_value("Global Defaults", "default_company") or "Indian Talent Olympiad"
            if not frappe.db.exists("Company", company):
                companies = frappe.get_all("Company", limit=1)
                if companies:
                    company = companies[0].name

            # 1. Sales Invoice (unchanged)
            si = frappe.new_doc("Sales Invoice")
            si.customer = customer
            si.company = company
            si.append("items", {"item_code": item_code, "qty": exam_count, "rate": rate})
            si.flags.ignore_permissions = True
            si.flags.ignore_mandatory = True
            si.insert(ignore_permissions=True)
            si.submit()

            # 2. Resolve accounts via direct DB — no permission check
            from erpnext.accounts.party import get_party_account
            receivable_account = get_party_account("Customer", customer, company)
            paid_to_account = (
                frappe.db.get_value("Company", company, "default_cash_account")
                or frappe.db.get_value("Company", company, "default_bank_account")
            )

            if not receivable_account or not paid_to_account:
                frappe.log_error(
                    f"Missing accounts for company {company} "
                    f"(receivable={receivable_account}, paid_to={paid_to_account})",
                    "Student Portal Payment Entry Error"
                )
                return si.name, None

            # ── KEY FIX: fetch account meta via db.get_value, not get_account_details ──
            paid_from_currency, paid_from_type = frappe.db.get_value(
                "Account", receivable_account, ["account_currency", "account_type"]
            )
            paid_to_currency, paid_to_type = frappe.db.get_value(
                "Account", paid_to_account, ["account_currency", "account_type"]
            )
            company_currency = frappe.db.get_value("Company", company, "default_currency")

            # 3. Build Payment Entry with all account fields pre-populated
            pe = frappe.new_doc("Payment Entry")
            pe.payment_type = "Receive"
            pe.party_type = "Customer"
            pe.party = customer
            pe.company = company
            pe.posting_date = frappe.utils.today()
            pe.reference_date = frappe.utils.today()
            pe.reference_no = doc.name

            pe.paid_from = receivable_account
            pe.paid_from_account_currency = paid_from_currency or company_currency
            pe.paid_from_account_type = paid_from_type

            pe.paid_to = paid_to_account
            pe.paid_to_account_currency = paid_to_currency or company_currency
            pe.paid_to_account_type = paid_to_type

            pe.paid_amount = si.grand_total
            pe.received_amount = si.grand_total
            pe.source_exchange_rate = 1
            pe.target_exchange_rate = 1

            pe.remarks = (
                f"Payment for Student Portal Registration: "
                f"{doc.student_full_name} ({doc.name})"
            )

            pe.append("references", {
                "reference_doctype": "Sales Invoice",
                "reference_name": si.name,
                "total_amount": si.grand_total,
                "outstanding_amount": si.outstanding_amount,
                "allocated_amount": si.grand_total,
            })

            pe.flags.ignore_permissions = True
            pe.flags.ignore_mandatory = True
            pe.insert(ignore_permissions=True)
            pe.submit()

            return si.name, pe.name

        except Exception:
            frappe.log_error(frappe.get_traceback(), "Student Portal Payment Entry Error")
            return None, None


@frappe.whitelist(allow_guest=True)
def confirm_student_portal_payment(integration_request, razorpay_payment_id, razorpay_order_id, razorpay_signature):
	with IgnorePermissionsContext():
		from multi_company_razorpay.api import checkout_success
		result = checkout_success(
			integration_request=integration_request,
			razorpay_payment_id=razorpay_payment_id,
			razorpay_order_id=razorpay_order_id,
			razorpay_signature=razorpay_signature,
		)
		integration = frappe.get_doc("Integration Request", integration_request)
		data = frappe.parse_json(integration.data or "{}")
		transaction = frappe.get_doc("Razorpay Transaction", data["razorpay_transaction"])
		return {
			"success": True,
			"paid": transaction.status == "Completed",
			"payment_entry": transaction.payment_entry,
			"sales_invoice": transaction.reference_docname,
			"redirect_to": result.get("redirect_to"),
		}


def initiate_student_portal_registration_payment(doc, exam_count):
	with IgnorePermissionsContext():
		try:
			if not exam_count or exam_count <= 0:
				return None, None

			customer = doc.school_name
			if not customer and doc.school_code:
				customer = (
					frappe.db.get_value("Customer", {"custom_school_code": doc.school_code}, "name")
					or frappe.db.get_value("Customer", {"custom_ito_school_code": doc.school_code}, "name")
					or frappe.db.get_value("Customer", {"name": doc.school_code}, "name")
				)

			if not customer or not frappe.db.exists("Customer", customer):
				return None, None

			from ito_customization.ito_customization.api import get_registration_fee
			fee_info = get_registration_fee("Student Portal Registration")
			item_code = fee_info.get("item_code") if fee_info and fee_info.get("success") else None
			rate = flt(fee_info.get("rate_inr") or fee_info.get("rate_usd") or 150) if fee_info and fee_info.get("success") else 150.0

			if not item_code:
				item_code = frappe.db.get_value(
					"Item",
					{"custom_is_registration_item": 1, "disabled": 0},
					"name"
				) or "ITO-Student Portal Registration"

			company = "Indian Talent Olympiad"
			if not frappe.db.exists("Company", company):
				company = frappe.db.get_single_value("Global Defaults", "default_company") or "Indian Talent Olympiad"
				if not frappe.db.exists("Company", company):
					companies = frappe.get_all("Company", limit=1)
					if companies:
						company = companies[0].name

			# 1. Sales Invoice
			si = frappe.new_doc("Sales Invoice")
			si.customer = customer
			si.company = company
			si.append("items", {"item_code": item_code, "qty": exam_count, "rate": rate})
			si.flags.ignore_permissions = True
			si.flags.ignore_mandatory = True
			si.insert(ignore_permissions=True)
			si.submit()

			# 2. Initiate Razorpay Checkout Payload (page=None so default company Razorpay settings are used)
			from multi_company_razorpay.api import create_payment_for_sales_invoice, get_checkout_context
			from urllib.parse import parse_qs, urlparse

			original_ignore = frappe.flags.ignore_permissions
			try:
				frappe.flags.ignore_permissions = True
				res = create_payment_for_sales_invoice(
					sales_invoice=si.name,
					amount=si.grand_total,
					page=None
				)
			finally:
				frappe.flags.ignore_permissions = original_ignore

			if not res or not res.get("checkout_url"):
				return si.name, None

			checkout_token = parse_qs(urlparse(res["checkout_url"]).query).get("token", [None])[0]
			if not checkout_token:
				return si.name, None

			checkout_context = get_checkout_context(checkout_token)
			checkout_context["sales_invoice"] = si.name
			return si.name, checkout_context

		except Exception:
			frappe.log_error(frappe.get_traceback(), "Student Portal Payment Initiation Error")
			return None, None


def _get_valid_class_name(cval):
	if not cval:
		return None
	sval = str(cval).strip()
	if frappe.db.exists("Class", sval):
		return sval
	clean_val = sval.replace("Class", "").replace("class", "").strip()
	if frappe.db.exists("Class", clean_val):
		return clean_val
	all_cls = frappe.db.get_value("Class", {}, "name")
	return all_cls or sval


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
		doc.set("class", _get_valid_class_name(data.get("class") or data.get("class_grade")) or "")

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

		si_name = None
		checkout_context = None
		if exam_count > 0:
			si_name, checkout_context = initiate_student_portal_registration_payment(doc, exam_count)

		return {
			"success": True,
			"name": doc.name,
			"sales_invoice": si_name,
			"checkout": checkout_context
		}


def _get_or_create_book_order_item(company="Olympiad Books"):
	item_code = frappe.db.get_value("Item", {"item_name": "Olympiad Books Order"}, "name")
	if not item_code:
		item_code = frappe.db.get_value("Item", {"item_group": "Products", "disabled": 0}, "name")
	if not item_code:
		item_code = frappe.db.get_value("Item", {"disabled": 0}, "name")
	if not item_code:
		item_code = "ITO-Book Order"
		if not frappe.db.exists("Item", item_code):
			try:
				idoc = frappe.new_doc("Item")
				idoc.item_code = item_code
				idoc.item_name = "Olympiad Books Order"
				idoc.item_group = "Products"
				idoc.stock_uom = "Nos"
				idoc.is_stock_item = 0
				idoc.flags.ignore_permissions = True
				idoc.insert(ignore_permissions=True)
			except Exception:
				pass
	return item_code


def initiate_student_portal_book_order_payment(customer, grand_total, doc_name=None):
	with IgnorePermissionsContext():
		try:
			if not grand_total or grand_total <= 0:
				return None, None

			# Customer resolution logic to ensure si.customer is a valid Customer in ERPNext
			cust_name = None
			if customer:
				if frappe.db.exists("Customer", customer):
					cust_name = customer
				else:
					cust_name = (
						frappe.db.get_value("Customer", {"custom_school_code": customer}, "name")
						or frappe.db.get_value("Customer", {"custom_ito_school_code": customer}, "name")
						or frappe.db.get_value("Customer", {"customer_name": customer}, "name")
					)

			if not cust_name and doc_name and frappe.db.exists("Student Portal", doc_name):
				sp = frappe.get_doc("Student Portal", doc_name)
				if sp.school_name and frappe.db.exists("Customer", sp.school_name):
					cust_name = sp.school_name
				elif sp.school_code:
					cust_name = (
						frappe.db.get_value("Customer", {"custom_school_code": sp.school_code}, "name")
						or frappe.db.get_value("Customer", {"custom_ito_school_code": sp.school_code}, "name")
					)

			if not cust_name:
				cust_name = frappe.db.get_value("Customer", {"disabled": 0}, "name")

			if not cust_name:
				# Create fallback customer if no customer doc exists in DB
				try:
					cdoc = frappe.new_doc("Customer")
					cdoc.customer_name = customer or "Student Portal Direct Customer"
					cdoc.customer_group = "Individual"
					cdoc.territory = "India"
					cdoc.flags.ignore_permissions = True
					cdoc.insert(ignore_permissions=True)
					cust_name = cdoc.name
				except Exception:
					pass

			customer = cust_name

			company = "Olympiad Books"
			if not frappe.db.exists("Company", company):
				company = frappe.db.get_single_value("Global Defaults", "default_company") or "Indian Talent Olympiad"

			item_code = _get_or_create_book_order_item(company)

			# 1. Sales Invoice
			si = frappe.new_doc("Sales Invoice")
			si.customer = customer
			si.company = company
			si.append("items", {"item_code": item_code, "qty": 1, "rate": flt(grand_total)})
			si.flags.ignore_permissions = True
			si.flags.ignore_mandatory = True
			si.insert(ignore_permissions=True)
			si.submit()

			# 2. Initiate Razorpay Checkout Payload for Olympiad Books
			from multi_company_razorpay.api import create_payment_for_sales_invoice, get_checkout_context
			from urllib.parse import parse_qs, urlparse

			original_ignore = frappe.flags.ignore_permissions
			try:
				frappe.flags.ignore_permissions = True
				res = create_payment_for_sales_invoice(
					sales_invoice=si.name,
					amount=si.grand_total,
					page=None
				)
			finally:
				frappe.flags.ignore_permissions = original_ignore

			if not res or not res.get("checkout_url"):
				return si.name, None

			checkout_token = parse_qs(urlparse(res["checkout_url"]).query).get("token", [None])[0]
			if not checkout_token:
				return si.name, None

			checkout_context = get_checkout_context(checkout_token)
			checkout_context["sales_invoice"] = si.name
			return si.name, checkout_context

		except Exception:
			frappe.log_error(frappe.get_traceback(), "Student Portal Book Order Payment Initiation Error")
			return None, None


@frappe.whitelist(allow_guest=True)
def confirm_student_portal_book_order_payment(integration_request, razorpay_payment_id, razorpay_order_id, razorpay_signature):
	with IgnorePermissionsContext():
		from multi_company_razorpay.api import checkout_success
		result = checkout_success(
			integration_request=integration_request,
			razorpay_payment_id=razorpay_payment_id,
			razorpay_order_id=razorpay_order_id,
			razorpay_signature=razorpay_signature,
		)
		integration = frappe.get_doc("Integration Request", integration_request)
		data = frappe.parse_json(integration.data or "{}")
		transaction = frappe.get_doc("Razorpay Transaction", data["razorpay_transaction"])
		return {
			"success": True,
			"paid": transaction.status == "Completed",
			"payment_entry": transaction.payment_entry,
			"sales_invoice": transaction.reference_docname,
			"redirect_to": result.get("redirect_to"),
		}


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
		class_grade = _get_valid_class_name(data.get("class") or data.get("class_grade")) or ""
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
				tb = int(item.get("text_book") or item.get("tb") or 0)
				wb = int(item.get("work_book") or item.get("practice_workbook_110") or item.get("wb") or 0)
				guide = int(item.get("student_guide_220") or item.get("guide") or 0)
				pyqp = int(item.get("prev_year_paper_160") or item.get("pyqp") or 0)
				if tb or wb or guide or pyqp:
					doc.append("books_selected", {
						"subject": item.get("subject"),
						"class_grade": _get_valid_class_name(item.get("class_grade") or class_grade) or class_grade,
						"text_book": tb,
						"work_book": wb,
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
				tb = int(item.get("text_book") or item.get("tb") or 0)
				wb = int(item.get("work_book") or item.get("practice_workbook_110") or item.get("wb") or 0)
				guide = int(item.get("student_guide_220") or item.get("guide") or 0)
				pyqp = int(item.get("prev_year_paper_160") or item.get("pyqp") or 0)
				if tb or wb or guide or pyqp:
					bs_doc.append("select_books", {
						"subject": item.get("subject"),
						"class_grade": _get_valid_class_name(item.get("class_grade") or class_grade) or class_grade,
						"text_book": tb,
						"work_book": wb,
						"practice_workbook_110": wb,
						"student_guide_220": guide,
						"prev_year_paper_160": pyqp,
					})

			bs_doc.flags.ignore_permissions = True
			if bs_doc.is_new():
				bs_doc.insert(ignore_permissions=True)
			else:
				bs_doc.save(ignore_permissions=True)

		# 3. Calculate total amount & initiate Razorpay Payment for Olympiad Books
		grand_total = flt(data.get("grand_total") or 0)
		if not grand_total and book_items:
			for b in book_items:
				tb = flt(b.get("text_book") or b.get("tb") or 0)
				wb = flt(b.get("work_book") or b.get("practice_workbook_110") or b.get("wb") or 0)
				guide = flt(b.get("student_guide_220") or b.get("guide") or 0)
				pyqp = flt(b.get("prev_year_paper_160") or b.get("pyqp") or 0)
				grand_total += (tb * 100) + (wb * 100) + (guide * 200) + (pyqp * 150)

		si_name = None
		checkout_context = None
		if grand_total > 0:
			si_name, checkout_context = initiate_student_portal_book_order_payment(customer, grand_total, doc.name)

		return {
			"success": True,
			"name": doc.name,
			"sales_invoice": si_name,
			"checkout": checkout_context,
			"message": "Book order saved successfully!"
		}
