# apps/ito_customization/ito_customization/ito_customization/doctype/web_forms_registration/web_forms_registration.py
import frappe
from frappe.model.document import Document
from frappe.rate_limiter import rate_limit

ALREADY_REGISTERED_MSG = "Registration has already been done with this email ID."
MOBILE_ALREADY_REGISTERED_MSG = "Registration has already been done with this mobile number."


class WebFormsRegistration(Document):
	def validate(self):
		# normalise so "A@x.com " and "a@x.com" count as the same person
		if self.email:
			self.email = self.email.strip().lower()
			if _email_exists(self.email, exclude=self.name):
				frappe.throw(ALREADY_REGISTERED_MSG, title="Already Registered")
		if self.mobile_number:
			self.mobile_number = self.mobile_number.strip()
			if _mobile_exists(self.mobile_number, exclude=self.name):
				frappe.throw(MOBILE_ALREADY_REGISTERED_MSG, title="Already Registered")

	def before_insert(self):
		if not self.epass_token:
			self.epass_token = frappe.generate_hash(length=24)

	def on_update(self):
		previous = self.get_doc_before_save()
		if not previous:
			return

		from ito_customization.ito_customization.doc_events.epass import is_approved, queue_epass

		if is_approved(previous) or not is_approved(self):
			return
		queue_epass(self)


def _email_exists(email, exclude=None):
	filters = {"email": email.strip().lower()}
	if exclude:
		filters["name"] = ["!=", exclude]
	return bool(frappe.db.exists("Web Forms Registration", filters))


def _mobile_exists(mobile_number, exclude=None):
	filters = {"mobile_number": mobile_number.strip()}
	if exclude:
		filters["name"] = ["!=", exclude]
	return bool(frappe.db.exists("Web Forms Registration", filters))


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=30, seconds=60)
def email_registered(email=None):
	"""Used by the web form to warn early. Returns only True/False."""
	if not email:
		return False
	return _email_exists(email)


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=30, seconds=60)
def mobile_registered(mobile_number=None):
	"""Used by the web form to warn early. Returns only True/False."""
	if not mobile_number:
		return False
	return _mobile_exists(mobile_number)

@frappe.whitelist()
def send_bulk_email(names, email_template):
	"""Queue an Email Template to the email of each selected registration."""
	if isinstance(names, str):
		names = frappe.parse_json(names)

	if not frappe.has_permission("Web Forms Registration", "email"):
		frappe.throw("Not permitted to send emails", frappe.PermissionError)

	template = frappe.get_doc("Email Template", email_template)
	queued = skipped = 0

	for name in names:
		doc = frappe.get_doc("Web Forms Registration", name)
		doc.check_permission("read")
		if not doc.email:
			skipped += 1
			continue

		rendered = template.get_formatted_email({"doc": doc.as_dict()})
		subject, message = rendered["subject"], rendered["message"]

		frappe.sendmail(
			recipients=[doc.email],
			subject=subject,
			message=message,
			reference_doctype=doc.doctype,
			reference_name=doc.name,
		)
		queued += 1

	return {"queued": queued, "skipped": skipped}


# --------------------------------------------------------------------------
# Create linked records (Customer / Lead / Contact / Teacher) from a registration
# --------------------------------------------------------------------------
# To support another DocType: write a `_create_<x>(reg, ctx)` returning
# (docname, created: bool) and add it to CREATORS. Order matters: later
# creators may read earlier results from ctx["created"].


def _squash(text):
	return " ".join((text or "").split())


def _tidy_name(text):
	text = _squash(text)
	if text and (text.islower() or text.isupper()):
		# not str.title(): it turns "paul's" into "Paul'S"
		return " ".join(w[:1].upper() + w[1:].lower() for w in text.split(" "))
	return text


def _norm_key(text):
	return "".join(ch for ch in (text or "").lower() if ch.isalnum())


def _normalise(reg):
	"""Tidy the registration values in memory (not saved) before they are used for new records."""
	from frappe.utils import validate_email_address

	reg.full_name = _tidy_name(reg.full_name)
	reg.school_name = _tidy_name(reg.school_name)
	reg.school_city = _tidy_name(reg.school_city)
	reg.school_state = _squash(reg.school_state)
	reg.designation = _squash(reg.designation)
	reg.email = (reg.email or "").strip().lower()
	if reg.email and not validate_email_address(reg.email):
		frappe.throw(f"Email {reg.email} is not valid.", title="Invalid Email")
	reg.mobile_number = "".join(ch for ch in (reg.mobile_number or "") if ch.isdigit() or ch == "+")
	if reg.mobile_number and len([c for c in reg.mobile_number if c.isdigit()]) < 10:
		frappe.throw(f"Mobile number {reg.mobile_number} looks too short.", title="Invalid Mobile Number")
	return reg


def _similar_customers(reg, limit=3):
	"""Customers whose names look like the school name but do not match exactly (e.g. St Pauls vs St Paul's)."""
	import difflib

	key = _norm_key(reg.school_name)
	if not key:
		return []
	names = frappe.get_all(
		"Customer",
		filters={"custom_customer_category": "School Customer"},
		fields=["name", "customer_name"],
	)
	scored = []
	for row in names:
		other = _norm_key(row.customer_name)
		if other == key:
			continue
		ratio = difflib.SequenceMatcher(None, key, other).ratio()
		if ratio >= 0.88:
			scored.append((ratio, row))
	scored.sort(key=lambda x: -x[0])
	return [{"name": r.name, "customer_name": r.customer_name} for _, r in scored[:limit]]


def _find_customer(reg, ctx):
	"""Customer created in this run, else an existing one matching the school name."""
	if ctx["created"].get("Customer"):
		return ctx["created"]["Customer"]
	if (ctx.get("extras") or {}).get("use_customer"):
		return ctx["extras"]["use_customer"]
	if reg.school_name:
		return frappe.db.get_value("Customer", {"customer_name": reg.school_name.strip()}, "name")
	return None


def _existing_customer(reg):
	if not reg.school_name:
		return None
	name = frappe.db.get_value("Customer", {"customer_name": reg.school_name.strip()}, "name")
	if name:
		return name
	key = _norm_key(reg.school_name)
	for row in frappe.get_all("Customer", filters={"custom_customer_category": "School Customer"}, fields=["name", "customer_name"]):
		if _norm_key(row.customer_name) == key:
			return row.name
	return None


def _existing_lead(reg):
	return frappe.db.get_value("Lead", {"email_id": reg.email}, "name") if reg.email else None


def _existing_contact(reg):
	if not reg.email:
		return None
	return frappe.db.get_value("Contact Email", {"email_id": reg.email, "parenttype": "Contact"}, "parent")


def _existing_teacher(reg, customer):
	if not reg.full_name:
		return None
	filters = {"name1": reg.full_name}
	if customer:
		filters["school_name"] = customer
	if reg.email:
		filters["email_id"] = reg.email
	return frappe.db.get_value("Teacher", filters, "name")


def _create_customer(reg, ctx):
	if not reg.school_name:
		frappe.throw("School Name is required to create a Customer.")
	existing = (ctx.get("extras") or {}).get("use_customer") or _existing_customer(reg)
	if existing:
		if not frappe.db.exists("Customer", existing):
			frappe.throw(f"Customer {existing} does not exist.")
		return existing, False

	doc = frappe.new_doc("Customer")
	doc.web_forms_registration = reg.name
	doc.customer_name = reg.school_name.strip()
	doc.customer_type = "Company"
	doc.custom_customer_category = "School Customer"
	doc.custom_state = reg.school_state
	if reg.school_code:
		doc.custom_school_code = reg.school_code
	# School Customer IDs are generated from the state (or taken from the school code)
	if reg.school_state:
		doc.custom_state = reg.school_state.strip()
	if not reg.school_code and not doc.custom_state:
		frappe.throw("School Code or School State is required to create a Customer.")
	if reg.school_city:
		doc.custom_city = reg.school_city
	if reg.school_state:
		doc.custom_stateprovince = reg.school_state
	doc.insert()
	return doc.name, True


def _create_lead(reg, ctx):
	if not reg.full_name:
		frappe.throw("Full Name is required to create a Lead.")
	existing = _existing_lead(reg)
	if existing:
		return existing, False

	doc = frappe.new_doc("Lead")
	doc.custom_lead_category = "School Lead"
	doc.web_forms_registration = reg.name
	doc.first_name = reg.full_name
	doc.email_id = reg.email
	doc.mobile_no = reg.mobile_number
	doc.company_name = reg.school_name
	doc.job_title = reg.designation
	doc.city = reg.school_city
	doc.state = reg.school_state
	if frappe.get_meta("Lead").has_field("custom_school_name"):
		doc.custom_school_name = reg.school_name
	customer = _find_customer(reg, ctx)
	if customer:
		doc.customer = customer
	doc.insert()
	return doc.name, True


def _create_contact(reg, ctx):
	if not reg.full_name:
		frappe.throw("Full Name is required to create a Contact.")
	customer = _find_customer(reg, ctx)

	existing = _existing_contact(reg)
	if existing:
		doc = frappe.get_doc("Contact", existing)
		if customer and not any(l.link_doctype == "Customer" and l.link_name == customer for l in doc.links):
			doc.append("links", {"link_doctype": "Customer", "link_name": customer})
			doc.save()
		return doc.name, False

	doc = frappe.new_doc("Contact")
	doc.web_forms_registration = reg.name
	doc.first_name = reg.full_name
	doc.designation = reg.designation
	doc.company_name = reg.school_name
	if reg.email:
		doc.append("email_ids", {"email_id": reg.email, "is_primary": 1})
	if reg.mobile_number:
		doc.append("phone_nos", {"phone": reg.mobile_number, "is_primary_mobile_no": 1})
	if customer:
		doc.append("links", {"link_doctype": "Customer", "link_name": customer})
	doc.insert()
	return doc.name, True


def _phone_with_country_code(number):
	"""Teacher.phone_number is a Phone field that needs a country code; registrations are Indian."""
	number = (number or "").strip()
	if number and not number.startswith("+"):
		digits = "".join(ch for ch in number if ch.isdigit())
		number = "+91" + digits[-10:] if len(digits) >= 10 else number
	return number


def _sync_teacher_to_customer(customer, teacher_name):
	"""Show the teacher in the Customer's Teachers table (custom_school_teacher_details)."""
	if not customer:
		return
	# Teachers Details.name1 is unique, so never add a second row for the same teacher
	if frappe.db.exists("Teachers Details", {"name1": teacher_name}):
		return
	teacher = frappe.get_doc("Teacher", teacher_name)
	customer_doc = frappe.get_doc("Customer", customer)
	customer_doc.append(
		"custom_school_teacher_details",
		{
			"name1": teacher.name,
			"date_of_birth": teacher.date_of_birth,
			"phone_number": teacher.phone_number,
			"status": teacher.status,
			"email_id": teacher.email_id,
			"subject": teacher.subject,
			"experience": teacher.experience,
		},
	)
	# date of birth / subject are not collected at registration
	customer_doc.flags.ignore_mandatory = True
	customer_doc.save()


def _create_teacher(reg, ctx):
	if not reg.full_name:
		frappe.throw("Full Name is required to create a Teacher.")
	customer = _find_customer(reg, ctx)

	existing = _existing_teacher(reg, customer)
	if existing:
		_sync_teacher_to_customer(customer, existing)
		return existing, False

	doc = frappe.new_doc("Teacher")
	doc.web_forms_registration = reg.name
	doc.name1 = reg.full_name
	doc.email_id = reg.email
	doc.phone_number = _phone_with_country_code(reg.mobile_number)
	doc.status = "Active"
	doc.profile_image = reg.photo
	doc.experience = "Fresher"
	doc.school_name = customer
	doc.customer_reference = customer
	if frappe.get_meta("Teacher").has_field("specialization"):
		doc.specialization = reg.designation
	extras = ctx.get("extras") or {}
	doc.subject = extras.get("subject") or None
	doc.date_of_birth = extras.get("date_of_birth") or None
	# "Subject" is mandatory on Teacher; if it wasn't supplied we still create the record.
	doc.flags.ignore_mandatory = not doc.subject
	doc.insert()
	_sync_teacher_to_customer(customer, doc.name)
	return doc.name, True


# Creation order: Customer first so the others can link to it.
CREATORS = {
	"Customer": _create_customer,
	"Lead": _create_lead,
	"Contact": _create_contact,
	"Teacher": _create_teacher,
}


@frappe.whitelist()
def create_linked_records(name, doctypes, extras=None):
	"""Create the selected DocTypes from one registration in a single transaction."""
	doctypes = frappe.parse_json(doctypes) if isinstance(doctypes, str) else doctypes
	selected = [dt for dt in CREATORS if dt in (doctypes or [])]
	if not selected:
		frappe.throw("Select at least one DocType to create.")

	reg = frappe.get_doc("Web Forms Registration", name)
	reg.check_permission("read")
	_normalise(reg)
	for dt in selected:
		frappe.has_permission(dt, "create", throw=True)

	extras = frappe.parse_json(extras) if isinstance(extras, str) else extras
	ctx = {"created": {}, "extras": extras or {}}
	created, reused = {}, {}
	frappe.db.savepoint("create_linked_records")
	try:
		for dt in selected:
			docname, is_new = CREATORS[dt](reg, ctx)
			ctx["created"][dt] = docname
			(created if is_new else reused)[dt] = docname
			if not is_new:
				# make reused records show up under this registration's Connections too
				if not frappe.db.get_value(dt, docname, "web_forms_registration"):
					frappe.db.set_value(dt, docname, "web_forms_registration", reg.name, update_modified=False)
	except Exception:
		frappe.db.rollback(save_point="create_linked_records")
		raise

	summary = ", ".join(f"{dt} {n}" for dt, n in created.items()) or "nothing new"
	reused_txt = ", ".join(f"{dt} {n}" for dt, n in reused.items())
	reg.add_comment(
		"Info",
		f"{frappe.bold(frappe.session.user)} created: {summary}."
		+ (f" Reused and linked: {reused_txt}." if reused_txt else ""),
	)
	return {"created": created, "existing": reused}


FINDERS = {
	"Customer": lambda reg, customer: _existing_customer(reg),
	"Lead": lambda reg, customer: _existing_lead(reg),
	"Contact": lambda reg, customer: _existing_contact(reg),
	"Teacher": lambda reg, customer: _existing_teacher(reg, customer),
}


@frappe.whitelist()
def get_create_preview(name):
	"""What Create would do for each DocType, without changing anything.

	state: "new"    - will be created
	       "exists" - a matching record exists and will be reused and linked
	       "linked" - already linked to this registration
	"""
	reg = frappe.get_doc("Web Forms Registration", name)
	reg.check_permission("read")
	try:
		_normalise(reg)
	except frappe.ValidationError:
		frappe.local.message_log = []  # preview should still open; the error shows on Create
	customer = _existing_customer(reg)

	preview = {}
	for dt in CREATORS:
		linked = frappe.db.get_value(dt, {"web_forms_registration": name}, "name")
		if linked:
			preview[dt] = {"state": "linked", "name": linked}
			continue
		existing = FINDERS[dt](reg, customer)
		preview[dt] = {"state": "exists", "name": existing} if existing else {"state": "new", "name": None}
	if preview["Customer"]["state"] == "new":
		preview["Customer"]["similar"] = _similar_customers(reg)
	return preview


@frappe.whitelist()
def get_linked_records(name):
	"""Records linked to this registration, with who created them and when (for the form section)."""
	reg = frappe.get_doc("Web Forms Registration", name)
	reg.check_permission("read")

	summary_fields = {
		"Customer": ("customer_name", "custom_customer_category"),
		"Lead": ("lead_name", "email_id"),
		"Contact": ("full_name", "email_id"),
		"Teacher": ("name1", "email_id"),
	}
	out = []
	for dt, (title_f, sub_f) in summary_fields.items():
		if not frappe.has_permission(dt, "read"):
			out.append({"doctype": dt, "state": "restricted"})
			continue
		row = frappe.db.get_value(
			dt, {"web_forms_registration": name}, ["name", title_f, sub_f, "creation", "owner"], as_dict=True
		)
		if not row:
			out.append({"doctype": dt, "state": "missing"})
			continue
		out.append(
			{
				"doctype": dt,
				"state": "linked",
				"name": row.name,
				"title": row.get(title_f),
				"subtitle": row.get(sub_f),
				"created_on": frappe.utils.format_datetime(row.creation, "dd MMM yyyy, hh:mm a"),
				"created_iso": str(row.creation),
				"owner": row.owner,
				"created_by": frappe.db.get_value("User", row.owner, "full_name") or row.owner,
			}
		)
	return out
