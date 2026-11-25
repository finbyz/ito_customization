"""Bulk import of ITO school registrations from the registration sheet.

One sheet row is one school. A row creates or updates:
- the Customer (matched by school code), its Address,
- the Principal contact and the staff as Teachers on the school,
- the Exams Summary of the chosen exam schedule (class-wise student counts and slot dates),
- the subject slots (A/B/C) on the Customer.

Book orders, payment and dispatch columns are not imported.

Masters (State, School Subject, Class) are never created here. A value that does not match an
existing master is reported as an error on that row, and the row is skipped.

The sheet has a three-row header (group / sub-group / leaf, with merged cells), the same layout as
the downloaded template. Columns are read by their header text, not by position.
"""

import datetime
import io
import re

import frappe
from frappe import _
from frappe.utils import cint, getdate, strip_html, validate_email_address

from ito_customization.ito_customization.doc_events.customer import STATE_NAME_TO_CODE

CLASSES = [str(n) for n in range(1, 13)]
SLOT_LETTERS = {"A": 1, "B": 2, "C": 3}  # slot letter -> Yearly Exam Date CT tg_date_<n>

# Header words -> subject key. More specific words first ("SOCIAL" before "SCIENCE").
SUBJECT_KEYWORDS = [
	("GENERAL KNOWLEDGE", "GKIO"),
	("LOGICAL", "NLRO"),
	("REASONING", "NLRO"),
	("MATHEMATICS", "IMO"),
	("MATHS", "IMO"),
	("SOCIAL", "NSSO"),
	("SCIENCE", "ISO"),
	("ENGLISH", "EIO"),
	("GK", "GKIO"),
	("COMPUTER", "ICO"),
	("DRAWING", "IDO"),
	("ART", "IDO"),
	("ESSAY", "NESO"),
	("EASSY", "NESO"),
	("HINDI", "NHO"),
	("COMMERCE", "CIO"),
	("ABACUS", "ABACUS"),
	("ASTRONOMY", "ASTRONOMY"),
	("EVS", "EVS"),
]

# Subject key -> Customer slot field (existing custom fields).
SLOT_FIELDS = {
	"ISO": "custom_science_slot",
	"IMO": "custom_mathematic_slot",
	"EIO": "custom_english_slot",
	"GKIO": "custom_gk_slot",
	"ICO": "custom_computer_slot",
	"IDO": "custom_drawing_slot",
	"NESO": "custom_essay_slot",
	"NSSO": "custom_social_slot",
	"NHO": "custom_hindi_slot",
	"NLRO": "custom_reasoning_slot",
	"CIO": "custom_commerce_slot",
	"ABACUS": "custom_abacus_slot",
	"ASTRONOMY": "custom_astronomy_slot",
}

TOP_FIELDS = {
	"REGISTRATION DATE": "registration_date",
	"SCHOOL CODE": "school_code",
	"SCHOOL NAME": "school_name",
	"MOBILE": "mobile",
	"EMAIL": "email",
}

ADDRESS_FIELDS = {
	"ADDRESS": "address",
	"CITY": "city",
	"TALUKA": "taluka",
	"DISTRICT": "district",
	"DISTICT": "district",
	"STATE": "state",
	"PIN CODE": "pincode",
	"PINCODE": "pincode",
}


# --------------------------------------------------------------------------------------------------
# Reading the sheet
# --------------------------------------------------------------------------------------------------


def _text(value) -> str:
	"""A cell as clean text: numbers without '.0', spaces collapsed."""
	if value is None:
		return ""
	if isinstance(value, float) and value.is_integer():
		value = int(value)
	return re.sub(r"\s+", " ", str(value)).strip()


def _header(value) -> str:
	return _text(value).upper()


def subject_key(header: str) -> str | None:
	"""The subject key ('ISO', 'ABACUS' ...) a column header refers to, or None."""
	header = _header(header)
	paren = re.search(r"\(([A-Z]+)\)", header)
	if paren:
		return paren.group(1)
	for word, key in SUBJECT_KEYWORDS:
		if re.search(rf"\b{word}\b", header):
			return key
	return None


def class_from_header(header: str) -> str | None:
	match = re.search(r"\d+", _header(header))
	return str(int(match.group())) if match else None


def read_records(file_url: str) -> list[dict]:
	"""Parse the registration sheet into one record per school row."""
	from openpyxl import load_workbook

	file_doc = frappe.get_doc("File", {"file_url": file_url})
	workbook = load_workbook(io.BytesIO(file_doc.get_content()), data_only=True)
	sheet = next(
		(ws for ws in workbook.worksheets if "REGISTRATION" in ws.title.upper()), workbook.worksheets[0]
	)
	rows = [list(row) for row in sheet.iter_rows(values_only=True)]

	header_at = next(
		(i for i, row in enumerate(rows[:10]) if row and _header(row[0]) == "REGISTRATION DATE"), None
	)
	if header_at is None or len(rows) < header_at + 3:
		frappe.throw(
			_("The first sheet must start with the 3 header rows of the template (cell A1: REGISTRATION DATE).")
		)

	groups, subs, leaves = rows[header_at], rows[header_at + 1], rows[header_at + 2]
	width = max(len(groups), len(subs), len(leaves))
	columns = []
	group = sub = None
	for i in range(width):
		g = _header(groups[i] if i < len(groups) else None)
		s = _header(subs[i] if i < len(subs) else None)
		leaf = _header(leaves[i] if i < len(leaves) else None)
		if g:
			group, sub = g, None
		if s:
			sub = s
		columns.append(_column_spec(group, sub, leaf))

	records = []
	for offset, row in enumerate(rows[header_at + 3 :]):
		record = {"row_no": header_at + 4 + offset, "school": {}, "staff": {}, "enrollment": {}, "slots": {}}
		for spec, value in zip(columns, row):
			if spec is None or value is None or _text(value) == "":
				continue
			kind = spec[0]
			if kind == "school":
				record["school"][spec[1]] = value
			elif kind == "staff":
				record["staff"].setdefault(spec[1], {})[spec[2]] = value
			elif kind == "enrollment":
				record["enrollment"].setdefault(spec[1], {})[spec[2]] = value
			elif kind == "slot":
				record["slots"][spec[1]] = value
		if record["school"].get("school_code") or record["school"].get("school_name"):
			records.append(record)
	return records


def _column_spec(group, sub, leaf):
	if not group:
		return None
	if group in TOP_FIELDS and not sub:
		return ("school", TOP_FIELDS[group])
	if "ADDRESS" in group and sub in ADDRESS_FIELDS:
		return ("school", ADDRESS_FIELDS[sub])
	if "STAFF" in group and sub and leaf in ("NAME", "MOBILE", "EMAIL"):
		return ("staff", sub, leaf.lower())
	if "ENROL" in group and sub and leaf:
		return ("enrollment", sub, leaf)
	if group.startswith("SLOT") and sub:
		return ("slot", sub)
	return None


# --------------------------------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------------------------------


def build_context(exam_detail: str) -> frappe._dict:
	ed = frappe.get_doc("Yearly Exam Date", exam_detail)
	if ed.docstatus != 1 or ed.is_little_champ:
		frappe.throw(_("Choose a submitted ITO exam schedule (not Little Champ)."))

	by_key = {}
	for row in frappe.get_all(
		"School Subject", filters={"little_champ": 0}, fields=["name", "abbr"], order_by="name asc"
	):
		paren = re.search(r"\(([A-Za-z]+)\)\s*$", row.name)
		for key in {(row.abbr or "").upper(), paren.group(1).upper() if paren else ""}:
			if key:
				by_key.setdefault(key, row.name)

	schedule = {}
	for row in ed.target_dates:
		if row.subject and not row.for_chitrakala and not row.is_little_champ:
			schedule[row.subject] = {n: row.get(f"tg_date_{n}") for n in (1, 2, 3)}

	return frappe._dict(
		exam_detail=ed.name,
		academic_year=ed.academic_year,
		subject_by_key=by_key,
		schedule=schedule,
		classes=set(frappe.get_all("Class", pluck="name")),
		states={name.lower(): name for name in STATE_NAME_TO_CODE},
		codes_seen=set(),
	)


def _mobile(value):
	"""(normalized 10-digit mobile, error) for a mobile cell."""
	digits = re.sub(r"\D", "", _text(value))
	if len(digits) == 12 and digits.startswith("91"):
		digits = digits[2:]
	elif len(digits) == 11 and digits.startswith("0"):
		digits = digits[1:]
	if len(digits) != 10:
		return None, _("mobile {0} is not a 10-digit number").format(_text(value))
	return f"+91 {digits}", None  # Phone fields need the country code, stored as the portal does


def _email(value):
	email = _text(value).lower()
	if not validate_email_address(email):
		return None, _("email {0} is not valid").format(email)
	return email, None


def _date(value):
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	try:
		return getdate(_text(value), parse_day_first=True)
	except Exception:
		return None


def check_record(record: dict, ctx) -> dict:
	"""Validate a record and build the clean values to import. Adds 'errors' and 'warnings'."""
	errors, warnings = [], []
	src = record["school"]
	clean = frappe._dict(row_no=record["row_no"])

	clean.school_code = _text(src.get("school_code")).upper()
	clean.school_name = _text(src.get("school_name"))
	if not clean.school_code:
		errors.append(_("School Code is missing"))
	if not clean.school_name:
		errors.append(_("School Name is missing"))

	if clean.school_code:
		if clean.school_code in ctx.codes_seen:
			errors.append(_("School Code {0} appears more than once in the file").format(clean.school_code))
		ctx.codes_seen.add(clean.school_code)

	# State is a master: it must be one of the known states, and the school code must use its prefix.
	clean.state = None
	if src.get("state"):
		clean.state = ctx.states.get(_text(src["state"]).lower().replace("&", "and"))
		if not clean.state:
			errors.append(_("State {0} is not a known state").format(_text(src["state"])))
	elif clean.school_code:
		errors.append(_("State is missing"))
	if clean.state and clean.school_code:
		prefix = STATE_NAME_TO_CODE[clean.state]
		match = re.match(r"^([A-Z]+)\d+$", clean.school_code)
		if not match:
			errors.append(_("School Code {0} must be letters followed by digits").format(clean.school_code))
		elif match.group(1) != prefix:
			errors.append(
				_("School Code {0} does not start with {1}, the code for {2}").format(
					clean.school_code, prefix, clean.state
				)
			)

	for field in ("city", "taluka", "district", "address"):
		clean[field] = _text(src.get(field)) or None
	if clean.district:
		clean.district = clean.district.title()

	clean.pincode = _text(src.get("pincode")) or None
	if clean.pincode and not re.fullmatch(r"\d{6}", clean.pincode):
		errors.append(_("PIN code {0} is not 6 digits").format(clean.pincode))
	if any(clean.get(f) for f in ("address", "city", "pincode")) and not (clean.address and clean.city):
		errors.append(_("Address and City are both needed to save the school address"))

	clean.mobile = clean.email = None
	if src.get("mobile"):
		clean.mobile, error = _mobile(src["mobile"])
		if error:
			errors.append(_("School {0}").format(error))
	if src.get("email"):
		clean.email, error = _email(src["email"])
		if error:
			errors.append(_("School {0}").format(error))

	clean.registration_date = None
	if src.get("registration_date"):
		clean.registration_date = _date(src["registration_date"])
		if not clean.registration_date:
			errors.append(_("Registration Date {0} is not a date").format(_text(src["registration_date"])))

	# Staff: each role must map to an existing School Subject (Principal and Overall Coordinator included).
	clean.staff = {}
	clean.principal = None
	for role, person in record["staff"].items():
		label = role.title()
		if "PRINCIPAL" in role:
			subject = "Principal"
		elif "OVERALL" in role or role.startswith("COORDINATOR"):
			subject = "Overall Coordinator"
		else:
			subject = ctx.subject_by_key.get(subject_key(role) or "")
		if not subject or not frappe.db.exists("School Subject", subject):
			errors.append(_("Staff column {0} has no matching School Subject").format(label))
			continue
		name = _text(person.get("name")).title()
		if not name:
			errors.append(_("{0}: name is missing").format(label))
			continue
		entry = {"name": name, "role": subject, "mobile": None, "email": None}
		if person.get("mobile"):
			entry["mobile"], error = _mobile(person["mobile"])
			if error:
				errors.append(f"{label}: {error}")
		if person.get("email"):
			entry["email"], error = _email(person["email"])
			if error:
				errors.append(f"{label}: {error}")
		if subject == "Principal":
			clean.principal = entry
		clean.staff[subject] = entry

	# Slots: letters A/B/C, stored on the Customer and used for the slot date of the exam rows.
	clean.slots = {}
	slot_by_subject_key = {}
	for header, value in record["slots"].items():
		key = subject_key(header)
		letter = _text(value).upper()
		if key not in SLOT_FIELDS:
			continue  # e.g. EVS: no exam and no slot field on the school
		if letter not in SLOT_LETTERS:
			errors.append(_("Slot for {0} must be A, B or C, not {1}").format(header.title(), letter))
			continue
		clean.slots[SLOT_FIELDS[key]] = letter
		slot_by_subject_key[key] = letter

	# Enrollment: subject must exist and be in the exam schedule; counts are whole numbers.
	clean.enrollment = []
	for header, by_class in record["enrollment"].items():
		key = subject_key(header)
		subject = ctx.subject_by_key.get(key or "")
		if not subject:
			errors.append(_("Exam {0} has no matching School Subject").format(header.title()))
			continue
		if subject not in ctx.schedule:
			errors.append(_("{0} is not in exam schedule {1}").format(subject, ctx.exam_detail))
			continue
		letter = slot_by_subject_key.get(key)
		slot_date = ctx.schedule[subject].get(SLOT_LETTERS[letter]) if letter else None
		for class_header, value in by_class.items():
			class_name = class_from_header(class_header)
			if class_name not in ctx.classes:
				errors.append(_("{0}: class {1} is not a known Class").format(subject, class_header))
				continue
			count = value if isinstance(value, (int, float)) else _text(value)
			if isinstance(count, str) and count.isdigit():
				count = int(count)
			if not isinstance(count, (int, float)) or count < 0 or int(count) != count:
				errors.append(_("{0} class {1}: {2} is not a whole number").format(subject, class_name, _text(value)))
				continue
			if count:
				clean.enrollment.append(
					{"subject": subject, "class": class_name, "no_of_students": int(count), "slot_date": slot_date}
				)

	clean.existing = None
	if clean.school_code:
		clean.existing = frappe.db.exists("Customer", clean.school_code) or frappe.db.get_value(
			"Customer", {"custom_ito_school_code": clean.school_code}, "name"
		)
		if clean.existing:
			category, is_lc = frappe.db.get_value(
				"Customer", clean.existing, ["custom_customer_category", "custom_is_little_champ"]
			)
			if category and category != "School Customer" or is_lc:
				errors.append(_("Customer {0} exists but is not an ITO school").format(clean.existing))

	clean.errors, clean.warnings = errors, warnings
	return clean


# --------------------------------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------------------------------


def import_record(clean, ctx) -> str:
	"""Create or update everything for one school. Returns the Customer name."""
	from ito_customization.ito_customization import api

	customer = frappe.get_doc("Customer", clean.existing) if clean.existing else frappe.new_doc("Customer")
	if customer.is_new():
		customer.customer_type = "Company"
		customer.custom_customer_category = "School Customer"
		customer.custom_ito_school_code = clean.school_code
		customer.custom_country = "India"

	# Only fill what the sheet has: an empty cell never clears stored data.
	values = {
		"customer_name": clean.school_name,
		"custom_state": clean.state,
		"custom_district": clean.district,
		"custom_taluka": clean.taluka,
		"mobile_no": clean.mobile,
		"email_id": clean.email,
		"custom_registration_date": clean.registration_date,
		"custom_current_academic_year": ctx.academic_year,
		**clean.slots,
	}
	for field, value in values.items():
		if value:
			customer.set(field, value)
	if customer.is_new():
		customer.insert(ignore_permissions=True)
	else:
		customer.save(ignore_permissions=True)

	if clean.address and clean.city:
		api.create_or_update_address(
			customer.name,
			{
				"school_address": clean.address,
				"city": clean.city,
				"state": clean.state,
				"pincode": clean.pincode,
				"taluka": clean.taluka,
				"district": clean.district,
			},
		)

	if clean.principal:
		api.create_or_update_principal(clean.principal, customer.name)

	if clean.staff:
		api.create_or_update_teachers(_merge_staff(customer.name, clean.staff), customer.name)

	if clean.enrollment:
		_update_exams_summary(customer.name, clean, ctx)

	return customer.name


def _merge_staff(customer: str, sheet_staff: dict) -> dict:
	"""create_or_update_teachers replaces the school's whole teacher list, so keep the teachers already
	stored for roles the sheet does not fill."""
	coordinators = {}
	for row in frappe.get_all(
		"Teachers Details",
		filters={"parent": customer, "parenttype": "Customer", "parentfield": "custom_school_teacher_details"},
		fields=["name1", "phone_number", "email_id", "subject"],
	):
		if not row.name1 or row.subject in sheet_staff:
			continue
		coordinators[f"existing-{row.name1}"] = {
			"name": frappe.db.get_value("Teacher", row.name1, "name1") or row.name1,
			"mobile": row.phone_number,
			"email": row.email_id,
			"role": row.subject or "",
		}
	for subject, person in sheet_staff.items():
		coordinators[subject] = dict(person)
	return coordinators


def _update_exams_summary(customer: str, clean, ctx) -> None:
	name = frappe.db.get_value(
		"Exams Summary",
		{"customer": customer, "exam_detail": ctx.exam_detail, "is_wof": 0, "is_little_champ": 0},
	)
	if name:
		doc = frappe.get_doc("Exams Summary", name)
	else:
		doc = frappe.new_doc("Exams Summary")
		doc.update({"customer": customer, "exam_detail": ctx.exam_detail, "is_wof": 0, "is_little_champ": 0})

	# Keep rows of other forms (book orders ...) and the teacher details already typed on ITO rows.
	kept, previous = [], {}
	for row in doc.exam_summary:
		if (row.form_type or "ITO Registration") == "ITO Registration":
			previous[(str(row.get("class")), row.subject)] = row
		else:
			kept.append(row)

	doc.set("exam_summary", [])
	for row in kept:
		doc.append("exam_summary", row.as_dict(no_default_fields=True))

	for entry in clean.enrollment:
		old = previous.get((entry["class"], entry["subject"]))
		teacher = clean.staff.get(entry["subject"]) or {}
		doc.append(
			"exam_summary",
			{
				"form_type": "ITO Registration",
				"subject": entry["subject"],
				"class": entry["class"],
				"no_of_students": entry["no_of_students"],
				"teacher_name": (old and old.teacher_name) or teacher.get("name"),
				"whatsapp_no": (old and old.whatsapp_no) or teacher.get("mobile"),
				"slot_date": entry["slot_date"] or (old and old.slot_date) or None,
			},
		)

	if doc.is_new():
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)


def _row_message(clean, extra: str | None = None) -> str:
	parts = []
	if extra:
		parts.append(extra)
	parts += clean.errors
	parts += [_("Note: {0}").format(w) for w in clean.warnings]
	return "\n".join(parts)


def _plan_text(clean) -> str:
	what = _("Updates {0}").format(clean.existing) if clean.existing else _("New school")
	counts = sum(e["no_of_students"] for e in clean.enrollment)
	return _("{0}: {1} staff, {2} exam rows, {3} students").format(
		what, len(clean.staff), len(clean.enrollment), counts
	)


def validate_import(doc) -> None:
	"""Check the file and fill the log. Nothing is saved except the import document."""
	ctx = build_context(doc.exam_detail)
	records = read_records(doc.import_file)
	if not records:
		frappe.throw(_("No school rows were found under the header rows."))

	doc.set("rows", [])
	for record in records:
		clean = check_record(record, ctx)
		doc.append(
			"rows",
			{
				"row_no": clean.row_no,
				"school_code": clean.school_code,
				"school_name": clean.school_name,
				"status": "Error" if clean.errors else "Valid",
				"customer": clean.existing,
				"message": _row_message(clean, None if clean.errors else _plan_text(clean)),
			},
		)
	valid = sum(1 for row in doc.rows if row.status == "Valid")
	doc.summary = _("{0} of {1} rows are valid. Rows with errors are skipped on import.").format(
		valid, len(doc.rows)
	)
	doc.status = "Validated"


def run_import(import_name: str) -> None:
	"""Background job: import every valid row on its own, so one bad school does not stop the rest."""
	doc = frappe.get_doc("ITO Bulk Import", import_name)
	doc.db_set("status", "In Progress", commit=True)

	try:
		ctx = build_context(doc.exam_detail)
		records = read_records(doc.import_file)
	except Exception as e:
		frappe.db.rollback()
		doc.reload()
		doc.db_set({"status": "Failed", "summary": strip_html(str(e))}, commit=True)
		doc.notify_update()
		return

	results = []
	for record in records:
		clean = check_record(record, ctx)
		result = {"row_no": clean.row_no, "school_code": clean.school_code, "school_name": clean.school_name}
		if clean.errors:
			result.update(status="Error", customer=clean.existing, message=_row_message(clean))
			results.append(result)
			continue

		frappe.db.savepoint("ito_bulk_import_row")
		try:
			customer = import_record(clean, ctx)
			frappe.db.release_savepoint("ito_bulk_import_row")
			frappe.db.commit()
			done = _("Updated") if clean.existing else _("Created")
			result.update(status="Imported", customer=customer, message=_row_message(clean, f"{done}. {_plan_text(clean)}"))
		except Exception as e:
			frappe.db.rollback(save_point="ito_bulk_import_row")
			frappe.log_error(frappe.get_traceback(), f"ITO Bulk Import {import_name} row {clean.row_no}")
			result.update(status="Failed", customer=clean.existing, message=strip_html(str(e)) or type(e).__name__)
		frappe.local.message_log = []
		results.append(result)

	doc.reload()
	doc.set("rows", [])
	for result in results:
		doc.append("rows", result)
	imported = sum(1 for r in results if r["status"] == "Imported")
	doc.status = "Completed" if imported == len(results) else ("Partially Completed" if imported else "Failed")
	doc.summary = _("{0} of {1} schools imported. {2} skipped with errors, {3} failed while saving.").format(
		imported,
		len(results),
		sum(1 for r in results if r["status"] == "Error"),
		sum(1 for r in results if r["status"] == "Failed"),
	)
	doc.flags.from_import_job = True
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	doc.notify_update()


# --------------------------------------------------------------------------------------------------
# Template
# --------------------------------------------------------------------------------------------------


@frappe.whitelist()
def download_template(exam_detail: str):
	"""An empty registration sheet with one column group per subject of the exam schedule."""
	from openpyxl import Workbook
	from openpyxl.styles import Alignment, Font, PatternFill

	frappe.has_permission("ITO Bulk Import", "create", throw=True)
	ctx = build_context(exam_detail)
	subjects = list(ctx.schedule)

	headers = []  # (group, sub, leaf)
	for label in TOP_FIELDS:
		headers.append((label, None, None))
	for label in ("ADDRESS", "CITY", "TALUKA", "DISTRICT", "STATE", "PIN CODE"):
		headers.append(("SCHOOL ADDRESS DETAILS", label, None))
	for role in ["PRINCIPAL", "OVERALL COORDINATOR"] + [f"{s.upper()} TEACHER" for s in subjects]:
		for leaf in ("NAME", "MOBILE", "EMAIL"):
			headers.append(("STAFF DETAILS", role, leaf))
	for subject in subjects:
		for class_name in CLASSES:
			headers.append(("EXAM ENROLLMENT", subject.upper(), class_name))
	for subject in subjects:
		headers.append(("SLOT (A/B/C)", subject.upper(), None))

	wb = Workbook()
	ws = wb.active
	ws.title = "REGISTRATION DETAILS"
	bold, fill = Font(bold=True), PatternFill("solid", fgColor="DDEBF7")
	center = Alignment(horizontal="center", vertical="center", wrap_text=True)

	for col, (group, sub, leaf) in enumerate(headers, start=1):
		for row, value in ((1, group), (2, sub), (3, leaf)):
			cell = ws.cell(row=row, column=col, value=value)
			cell.font, cell.fill, cell.alignment = bold, fill, center

	def merge_runs(row, key):
		start = 1
		for col in range(2, len(headers) + 2):
			if col > len(headers) or key(headers[col - 1]) != key(headers[start - 1]):
				if col - 1 > start and key(headers[start - 1])[-1]:
					ws.merge_cells(start_row=row, start_column=start, end_row=row, end_column=col - 1)
				start = col

	merge_runs(1, lambda h: (h[0],))
	merge_runs(2, lambda h: (h[0], h[1]))
	for col, (group, sub, leaf) in enumerate(headers, start=1):
		if not sub and not leaf:
			ws.merge_cells(start_row=1, start_column=col, end_row=3, end_column=col)
		elif not leaf:
			ws.merge_cells(start_row=2, start_column=col, end_row=3, end_column=col)
	ws.freeze_panes = "E4"

	out = io.BytesIO()
	wb.save(out)
	frappe.response["filename"] = f"ITO Registration Import - {ctx.academic_year}.xlsx"
	frappe.response["filecontent"] = out.getvalue()
	frappe.response["type"] = "binary"
