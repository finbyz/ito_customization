"""Bulk import of ITO school registrations from the registration sheet.

One sheet row is one school. A row creates or updates:
- the Customer (matched by school code), its Address,
- the Principal contact and the staff as Teachers on the school,
- the Exams Summary of the chosen exam schedule (class-wise student counts and slot dates),
- the subject slots (A/B/C) on the Customer,
- the open (not submitted) Books Selection of the school, with the book counts of the sheet.

Book counts go to the 5 book fields of Books Selection. The sheet's column group says the book type:
	WORKBOOK ...                 -> Practice Workbook (ITO) or Work Book (Little Champ)
	... STUDENT GUIDE ... / OSG  -> Student Guide
	PREVIOUS YEAR ... / PYQP     -> Previous Year Paper
	TEXT BOOK ...                -> Text Book (Little Champ)
A count is saved only when the School Subject has a book item for that class in the matching table.
For a workbook the ITO table is tried first, then the Little Champ one.

Payment and dispatch columns are not imported.

Checks give errors and warnings. An error skips the school. A warning only drops the one value (a
bad email, a book with no item ...) and the school is still imported. Each issue names its cell
(for example "AF7 Principal Email") and, where it can, suggests a fix that the user can apply.
Values the user corrects on the import form are kept per sheet row ("corrections") and used
instead of the sheet value, so the Excel file does not have to be uploaded again.

Masters (State, School Subject, Class, book items) are never created by the import itself. A
School Subject can be created, or added to the exam schedule, from the import form by a user who
may create School Subjects.

Each imported row keeps what it created and the values it replaced, so an import can be reverted.

The sheet has a three-row header (group / sub-group / leaf, with merged cells), the same layout as
the downloaded template. Columns are read by their header text, not by position.
"""

import datetime
import difflib
import io
import json
import re

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, getdate, now_datetime, strip_html, validate_email_address

from ito_customization.ito_customization.doc_events.customer import STATE_NAME_TO_CODE

CLASSES = [str(n) for n in range(1, 13)]
SLOT_LETTERS = {"A": 1, "B": 2, "C": 3}  # slot letter -> Yearly Exam Date CT tg_date_<n>
SEP = "|"  # separator in cell paths, e.g. "staff|PRINCIPAL|email"

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

# Book column groups: (words in the group header, book type). Checked in this order.
BOOK_GROUPS = [
	(("STUDENT GUIDE", "OSG"), "sg"),
	(("PREVIOUS YEAR", "PYQP"), "yp"),
	(("TEXT BOOK", "TEXTBOOK"), "tb"),
	(("WORKBOOK", "WORK BOOK"), "wb"),
]

# Book type -> Books Selection CT / School Subject book table fields, in the order they are tried.
BOOK_FIELDS = {
	"wb": ("practice_workbook_110", "work_book"),
	"sg": ("student_guide_220",),
	"yp": ("prev_year_paper_160",),
	"tb": ("text_book",),
}
BOOK_LABELS = {"wb": "Workbook", "sg": "Student Guide", "yp": "Previous Year Paper", "tb": "Text Book"}
BOOK_QTY_FIELDS = ("practice_workbook_110", "student_guide_220", "prev_year_paper_160", "text_book", "work_book")
BOOK_FIELD_LABELS = {
	"practice_workbook_110": "Practice Workbook",
	"student_guide_220": "Student Guide",
	"prev_year_paper_160": "Previous Year Paper",
	"text_book": "Text Book",
	"work_book": "Work Book",
}

# Template group header per book type.
BOOK_TEMPLATE_GROUPS = {
	"wb": "WORKBOOK ORDER COUNT",
	"yp": "PREVIOUS YEAR QUESTION PAPER ORDER COUNT",
	"sg": "OLYMPIAD STUDENT GUIDE ORDER COUNT",
	"tb": "TEXT BOOK ORDER COUNT",
}
BOOK_TEMPLATE_SUFFIX = {"wb": "WB", "yp": "PYQP", "sg": "OSG", "tb": "TB"}

CLASS_ALIASES = {"LKG": "Junior KG", "JR KG": "Junior KG", "UKG": "Senior KG", "SR KG": "Senior KG"}

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

SCHOOL_LABELS = {
	"registration_date": "Registration Date",
	"school_code": "School Code",
	"school_name": "School Name",
	"mobile": "School Mobile",
	"email": "School Email",
	"address": "Address",
	"city": "City",
	"taluka": "Taluka",
	"district": "District",
	"state": "State",
	"pincode": "PIN Code",
}

# Customer fields shown in the preview: (customer field, label, clean value key)
CUSTOMER_PREVIEW = [
	("customer_name", "School Name", "school_name"),
	("custom_state", "State", "state"),
	("custom_district", "District", "district"),
	("custom_taluka", "Taluka", "taluka"),
	("mobile_no", "Mobile", "mobile"),
	("email_id", "Email", "email"),
	("custom_registration_date", "Registration Date", "registration_date"),
]

EMAIL_DOMAINS = ("gmail", "yahoo", "hotmail", "outlook", "rediffmail", "ymail", "icloud", "live")

ISSUE_CATEGORIES = {
	"email": "email",
	"mobile": "mobile number",
	"state": "state",
	"code": "school code",
	"subject": "subject",
	"schedule": "exam schedule",
	"class": "class",
	"count": "count",
	"duplicate": "duplicate",
	"address": "address",
	"date": "date",
	"slot": "slot",
	"book": "book",
	"staff": "staff",
	"customer": "existing customer",
	"missing": "missing value",
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
	if isinstance(value, datetime.datetime):
		value = value.date()
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


def class_from_header(header: str, classes=()) -> str | None:
	"""'1ST' -> '1'; 'NURSERY' / 'LKG' / 'JUNIOR KG' -> the Class name (matched without case)."""
	header = _header(header)
	match = re.search(r"\d+", header)
	if match:
		return str(int(match.group()))
	header = CLASS_ALIASES.get(header, header)
	return next((c for c in classes if c.upper() == header.upper()), header or None)


def _column_spec(group, sub, leaf):
	if not group:
		return None
	if group in TOP_FIELDS and not sub:
		return ("school", TOP_FIELDS[group])
	if "ADDRESS" in group and sub in ADDRESS_FIELDS:
		return ("school", ADDRESS_FIELDS[sub])
	if "STAFF" in group and sub and leaf in ("NAME", "MOBILE", "EMAIL"):
		return ("staff", sub, leaf.lower())
	for words, book_type in BOOK_GROUPS:
		if any(word in group for word in words):
			return ("book", book_type, sub, leaf) if sub and leaf else None
	if "ENROL" in group and sub and leaf:
		return ("enrollment", sub, leaf)
	if group.startswith("SLOT") and sub:
		return ("slot", sub)
	return None


def spec_path(spec) -> str:
	return SEP.join(str(part) for part in spec)


def spec_label(spec) -> str:
	kind = spec[0]
	if kind == "school":
		return SCHOOL_LABELS.get(spec[1], spec[1].title())
	if kind == "staff":
		return f"{spec[1].title()} {spec[2].title()}"
	if kind == "enrollment":
		return f"{spec[1].title()}, class {spec[2]}"
	if kind == "slot":
		return f"Slot {spec[1].title()}"
	if kind == "book":
		return f"{BOOK_LABELS[spec[1]]} {spec[2].title()}, class {spec[3]}"
	return spec_path(spec)


def _load_sheet(file_url: str):
	from openpyxl import load_workbook

	file_doc = frappe.get_doc("File", {"file_url": file_url})
	workbook = load_workbook(io.BytesIO(file_doc.get_content()), data_only=True)
	sheet = next(
		(ws for ws in workbook.worksheets if "REGISTRATION" in ws.title.upper()), workbook.worksheets[0]
	)
	return workbook, sheet


def _header_layout(rows):
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
	return header_at, columns


def read_records(file_url: str) -> list[dict]:
	"""Parse the registration sheet into one record per school row."""
	from openpyxl.utils import get_column_letter

	_wb, sheet = _load_sheet(file_url)
	rows = [list(row) for row in sheet.iter_rows(values_only=True)]
	header_at, columns = _header_layout(rows)

	letters = [get_column_letter(i + 1) for i in range(len(columns))]
	records = []
	for offset, row in enumerate(rows[header_at + 3 :]):
		row_no = header_at + 4 + offset
		record = _empty_record(row_no)
		for col, (spec, value) in enumerate(zip(columns, row)):
			if spec is None:
				continue
			path = spec_path(spec)
			record["cells"][path] = f"{letters[col]}{row_no}"
			record["labels"][path] = spec_label(spec)
			if value is None or _text(value) == "":
				continue
			_put_value(record, spec, value)
		if record["school"].get("school_code") or record["school"].get("school_name"):
			records.append(record)
	return records


def _empty_record(row_no):
	return {
		"row_no": row_no,
		"school": {},
		"staff": {},
		"enrollment": {},
		"slots": {},
		"books": [],
		"cells": {},
		"labels": {},
	}


def _put_value(record, spec, value):
	kind = spec[0]
	if kind == "school":
		record["school"][spec[1]] = value
	elif kind == "staff":
		record["staff"].setdefault(spec[1], {})[spec[2]] = value
	elif kind == "enrollment":
		record["enrollment"].setdefault(spec[1], {})[spec[2]] = value
	elif kind == "slot":
		record["slots"][spec[1]] = value
	elif kind == "book":
		record["books"] = [
			b for b in record["books"] if not (b["path"] == spec_path(spec))
		]  # a correction replaces the sheet value
		record["books"].append(
			{"type": spec[1], "subject": spec[2], "class": spec[3], "value": value, "path": spec_path(spec)}
		)


def _drop_value(record, spec):
	kind = spec[0]
	if kind == "school":
		record["school"].pop(spec[1], None)
	elif kind == "staff":
		record["staff"].get(spec[1], {}).pop(spec[2], None)
	elif kind == "enrollment":
		record["enrollment"].get(spec[1], {}).pop(spec[2], None)
	elif kind == "slot":
		record["slots"].pop(spec[1], None)
	elif kind == "book":
		record["books"] = [b for b in record["books"] if b["path"] != spec_path(spec)]


def apply_corrections(record: dict, corrections: dict | None) -> None:
	"""Put the values corrected on the import form in place of the sheet values."""
	for path, value in (corrections or {}).items():
		spec = tuple(path.split(SEP))
		if not spec or spec[0] not in ("school", "staff", "enrollment", "slot", "book"):
			continue
		if value is None or _text(value) == "":
			_drop_value(record, spec)
		else:
			_put_value(record, spec, value)
		record["labels"].setdefault(path, spec_label(spec))


# --------------------------------------------------------------------------------------------------
# Checks
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

	# Book items per School Subject: {subject: {field: {class}}}, Little Champ subjects included.
	book_classes = {}
	for row in frappe.get_all(
		"School Subject CT",
		filters={"parenttype": "School Subject", "parentfield": ("in", BOOK_QTY_FIELDS), "item": ("is", "set")},
		fields=["parent", "parentfield", "class"],
	):
		book_classes.setdefault(row.parent, {}).setdefault(row.parentfield, set()).add(str(row.get("class")))

	return frappe._dict(
		exam_detail=ed.name,
		academic_year=ed.academic_year,
		subject_by_key=by_key,
		schedule=schedule,
		book_classes=book_classes,
		book_subjects={
			row.name: (row.abbr or "").upper()
			for row in frappe.get_all("School Subject", fields=["name", "abbr"])
		},
		little_champ_subjects=set(frappe.get_all("School Subject", {"little_champ": 1}, pluck="name")),
		classes=set(frappe.get_all("Class", pluck="name")),
		states={name.lower(): name for name in STATE_NAME_TO_CODE},
		codes_seen=set(),
		slot_use={},  # (subject, letter) -> number of schools, for the schedule gap check
	)


class Issues:
	"""Errors and warnings of one row, each tied to a sheet cell where possible."""

	def __init__(self, record):
		self.record = record
		self.items = []

	def add(self, level, category, text, path=None, fix=None, action=None):
		self.items.append(
			{
				"level": level,
				"category": category,
				"text": text,
				"path": path,
				"cell": self.record["cells"].get(path) if path else None,
				"label": self.record["labels"].get(path) if path else None,
				"fix": fix,
				"action": action,
			}
		)

	def error(self, *args, **kwargs):
		self.add("error", *args, **kwargs)

	def warning(self, *args, **kwargs):
		self.add("warning", *args, **kwargs)

	@property
	def errors(self):
		return [i for i in self.items if i["level"] == "error"]

	@property
	def warnings(self):
		return [i for i in self.items if i["level"] == "warning"]


def issue_text(issue) -> str:
	where = " ".join(p for p in (issue.get("cell"), issue.get("label")) if p)
	text = f"{where}: {issue['text']}" if where else issue["text"]
	if issue.get("fix"):
		text += " " + _("(suggested: {0})").format(issue["fix"])
	return text


def suggest_email(value) -> str | None:
	"""A likely intended email for a mistyped one, or None."""
	email = _text(value).lower().replace(" ", "").replace(",", ".").replace("..", ".")
	email = email.strip(".")
	if "@" not in email:
		match = re.match(rf"^(.+?)({'|'.join(EMAIL_DOMAINS)})\.?(com|in|co\.in)?$", email)
		if not match or match.group(1).endswith("."):
			return None
		email = f"{match.group(1)}@{match.group(2)}.{match.group(3) or 'com'}"
	local, _sep, domain = email.partition("@")
	for name in EMAIL_DOMAINS:
		if re.fullmatch(rf"{name}\.?(com|in|co\.?in)?", domain):
			tld = re.sub(rf"^{name}\.?", "", domain) or "com"
			tld = "co.in" if tld in ("coin", "co.in") else tld
			domain = f"{name}.{tld}"
			break
	else:
		domain = re.sub(r"(?<=[a-z0-9])(com|in|org|net|edu)$", r".\1", domain) if "." not in domain else domain
	email = f"{local}@{domain}"
	if email != _text(value).lower() and validate_email_address(email):
		return email
	return None


def suggest_state(value, ctx) -> str | None:
	text = _text(value).lower().replace("&", "and")
	match = difflib.get_close_matches(text, list(ctx.states), n=1, cutoff=0.75)
	return ctx.states[match[0]] if match else None


def _mobile(value):
	"""(normalized mobile, error text) for a mobile cell."""
	digits = re.sub(r"\D", "", _text(value))
	if len(digits) == 12 and digits.startswith("91"):
		digits = digits[2:]
	elif len(digits) == 11 and digits.startswith("0"):
		digits = digits[1:]
	if len(digits) != 10:
		return None, _("{0} is not a 10-digit mobile number").format(_text(value))
	return f"+91 {digits}", None  # Phone fields need the country code, stored as the portal does


def _date(value):
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	try:
		return getdate(_text(value), parse_day_first=True)
	except Exception:
		return None


def _whole_number(value):
	count = value if isinstance(value, (int, float)) else _text(value)
	if isinstance(count, str) and count.isdigit():
		count = int(count)
	if not isinstance(count, (int, float)) or count < 0 or int(count) != count:
		return None
	return int(count)


def check_record(record: dict, ctx) -> frappe._dict:
	"""Validate a record and build the clean values to import. Adds 'issues', 'errors', 'warnings'."""
	issues = Issues(record)
	src = record["school"]
	clean = frappe._dict(row_no=record["row_no"])
	sp = lambda field: spec_path(("school", field))  # noqa: E731

	clean.school_code = _text(src.get("school_code")).upper()
	clean.school_name = _text(src.get("school_name"))
	if not clean.school_code:
		issues.error("missing", _("School Code is missing"), sp("school_code"))
	if not clean.school_name:
		issues.error("missing", _("School Name is missing"), sp("school_name"))

	if clean.school_code:
		if clean.school_code in ctx.codes_seen:
			issues.error("duplicate", _("this School Code appears more than once in the file"), sp("school_code"))
		ctx.codes_seen.add(clean.school_code)

	# State is a master: it must be one of the known states, and the school code must use its prefix.
	clean.state = None
	if src.get("state"):
		clean.state = ctx.states.get(_text(src["state"]).lower().replace("&", "and"))
		if not clean.state:
			issues.error(
				"state",
				_("{0} is not a known state").format(_text(src["state"])),
				sp("state"),
				fix=suggest_state(src["state"], ctx),
			)
	elif clean.school_code:
		issues.error("state", _("State is missing"), sp("state"))
	clean.existing = None
	if clean.school_code:
		clean.existing = frappe.db.exists("Customer", clean.school_code) or frappe.db.get_value(
			"Customer", {"custom_ito_school_code": clean.school_code}, "name"
		)
	# A new school's code must fit its state. An existing school keeps its code, so only warn.
	code_issue = issues.warning if clean.existing else issues.error
	if clean.state and clean.school_code:
		prefix = STATE_NAME_TO_CODE[clean.state]
		match = re.match(r"^([A-Z]+)\d+$", clean.school_code)
		if not match:
			code_issue("code", _("must be letters followed by digits"), sp("school_code"))
		elif match.group(1) != prefix:
			code_issue(
				"code",
				_("{0} does not start with {1}, the code for {2}").format(clean.school_code, prefix, clean.state),
				sp("school_code"),
			)

	for field in ("city", "taluka", "district", "address"):
		clean[field] = _text(src.get(field)) or None
	if clean.district:
		clean.district = clean.district.title()

	clean.pincode = _text(src.get("pincode")) or None
	if clean.pincode and not re.fullmatch(r"\d{6}", clean.pincode):
		digits = re.sub(r"\D", "", clean.pincode)
		issues.warning(
			"address",
			_("{0} is not a 6-digit PIN code, it is not saved").format(clean.pincode),
			sp("pincode"),
			fix=digits if len(digits) == 6 else None,
		)
		clean.pincode = None
	if any(clean.get(f) for f in ("address", "city", "pincode")) and not (clean.address and clean.city):
		missing = "city" if clean.address else "address"
		issues.warning("address", _("Address and City are both needed, the address is not saved"), sp(missing))
		clean.address = None

	clean.mobile = clean.email = None
	if src.get("mobile"):
		clean.mobile, error = _mobile(src["mobile"])
		if error:
			issues.warning("mobile", _("{0}, it is not saved").format(error), sp("mobile"))
	if src.get("email"):
		email = _text(src["email"]).lower()
		if validate_email_address(email):
			clean.email = email
		else:
			issues.warning(
				"email", _("{0} is not a valid email, it is not saved").format(email), sp("email"),
				fix=suggest_email(email),
			)

	clean.registration_date = None
	if src.get("registration_date"):
		clean.registration_date = _date(src["registration_date"])
		if not clean.registration_date:
			issues.warning(
				"date", _("{0} is not a date, it is not saved").format(_text(src["registration_date"])),
				sp("registration_date"),
			)

	_check_staff(record, ctx, clean, issues)
	slot_by_subject_key = _check_slots(record, clean, issues)
	_check_enrollment(record, ctx, clean, issues, slot_by_subject_key)
	clean.books = _check_books(record["books"], ctx, issues)

	if clean.existing:
		category, is_lc = frappe.db.get_value(
			"Customer", clean.existing, ["custom_customer_category", "custom_is_little_champ"]
		)
		if category and category != "School Customer" or is_lc:
			issues.error(
				"customer",
				_("Customer {0} exists but is not an ITO school").format(clean.existing),
				sp("school_code"),
			)

	clean.issues = issues.items
	clean.errors = issues.errors
	clean.warnings = issues.warnings
	clean.fields = _editable_fields(record)
	return clean


def _check_staff(record, ctx, clean, issues):
	"""Each staff role must map to a School Subject (Principal and Overall Coordinator included)."""
	clean.staff = {}
	clean.principal = None
	for role, person in record["staff"].items():
		if not any(_text(v) for v in person.values()):
			continue
		path = lambda leaf: spec_path(("staff", role, leaf))  # noqa: E731
		if "PRINCIPAL" in role:
			subject = "Principal"
		elif "OVERALL" in role or role.startswith("COORDINATOR"):
			subject = "Overall Coordinator"
		else:
			subject = ctx.subject_by_key.get(subject_key(role) or "")
		if not subject or not frappe.db.exists("School Subject", subject):
			issues.warning(
				"staff", _("no School Subject matches this role, the teacher is not saved"), path("name"),
				action={"type": "create_subject", "header": role, "key": subject_key(role) or ""},
			)
			continue
		name = _text(person.get("name")).title()
		if not name:
			issues.warning("staff", _("name is missing, this person is not saved"), path("name"))
			continue
		entry = {"name": name, "role": subject, "mobile": None, "email": None}
		if person.get("mobile"):
			entry["mobile"], error = _mobile(person["mobile"])
			if error:
				issues.warning("mobile", _("{0}, it is not saved").format(error), path("mobile"))
		if person.get("email"):
			email = _text(person["email"]).lower()
			if validate_email_address(email):
				entry["email"] = email
			else:
				issues.warning(
					"email", _("{0} is not a valid email, it is not saved").format(email), path("email"),
					fix=suggest_email(email),
				)
		if subject == "Principal":
			clean.principal = entry
		clean.staff[subject] = entry


def _check_slots(record, clean, issues):
	"""Slots: letters A/B/C, stored on the Customer and used for the slot date of the exam rows."""
	clean.slots = {}
	slot_by_subject_key = {}
	for header, value in record["slots"].items():
		key = subject_key(header)
		letter = _text(value).upper()
		if key not in SLOT_FIELDS:
			continue  # e.g. EVS: no exam and no slot field on the school
		if letter not in SLOT_LETTERS:
			issues.error("slot", _("must be A, B or C, not {0}").format(letter), spec_path(("slot", header)))
			continue
		clean.slots[SLOT_FIELDS[key]] = letter
		slot_by_subject_key[key] = letter
	return slot_by_subject_key


def _check_enrollment(record, ctx, clean, issues, slot_by_subject_key):
	"""Subject must exist and be in the exam schedule; counts are whole numbers."""
	clean.enrollment = []
	clean.slot_gaps = []
	for header, by_class in record["enrollment"].items():
		key = subject_key(header)
		subject = ctx.subject_by_key.get(key or "")
		first_path = spec_path(("enrollment", header, next(iter(by_class), "")))
		if not subject:
			issues.error(
				"subject", _("{0} has no matching School Subject").format(header.title()), first_path,
				action={"type": "create_subject", "header": header, "key": key or ""},
			)
			continue
		if subject not in ctx.schedule:
			issues.error(
				"schedule", _("{0} is not in exam schedule {1}").format(subject, ctx.exam_detail), first_path,
				action={"type": "add_to_schedule", "subject": subject},
			)
			continue
		letter = slot_by_subject_key.get(key)
		slot_date = ctx.schedule[subject].get(SLOT_LETTERS[letter]) if letter else None
		added = False
		for class_header, value in by_class.items():
			path = spec_path(("enrollment", header, class_header))
			class_name = class_from_header(class_header, ctx.classes)
			if class_name not in ctx.classes:
				issues.error("class", _("{0} is not a known Class").format(class_header), path)
				continue
			count = _whole_number(value)
			if count is None:
				issues.error("count", _("{0} is not a whole number").format(_text(value)), path)
				continue
			if count:
				added = True
				clean.enrollment.append(
					{"subject": subject, "class": class_name, "no_of_students": count, "slot_date": slot_date}
				)
		if added and letter and not slot_date:
			clean.slot_gaps.append((subject, letter))


def _book_subject_candidates(header: str, ctx) -> list[str]:
	"""School Subjects a book column header may mean: by abbreviation or by name."""
	key = subject_key(header) or ""
	header_up = _header(header)
	found = []
	for name, abbr in ctx.book_subjects.items():
		paren = re.search(r"\(([A-Za-z]+)\)\s*$", name)
		keys = {abbr, paren.group(1).upper() if paren else ""} - {""}
		plain = re.sub(r"\s*\([^)]*\)\s*$", "", name).upper()
		if key in keys or (key and re.search(rf"\b{re.escape(key)}\b", name.upper())) or plain in header_up:
			found.append(name)
	return found


def _resolve_book(book_type, header, class_name, ctx):
	"""(subject, Books Selection field) a book column refers to, or None when no item is set."""
	for subject in _book_subject_candidates(header, ctx):
		for field in BOOK_FIELDS[book_type]:
			if class_name in ctx.book_classes.get(subject, {}).get(field, ()):
				return subject, field
	return None


def _check_books(cells: list[dict], ctx, issues) -> list[dict]:
	"""Book counts as Books Selection CT rows, one per subject and class.

	Each count goes to the first book field whose School Subject table has an item for that class.
	A count with no book item is a warning: that count is not saved.
	"""
	rows = {}
	seen = set()
	for cell in cells:
		path = cell["path"]
		count = _whole_number(cell["value"])
		if count is None:
			issues.error("count", _("{0} is not a whole number").format(_text(cell["value"])), path)
			continue
		if not count:
			continue

		class_name = class_from_header(cell["class"], ctx.classes)
		if class_name not in ctx.classes:
			issues.error("class", _("{0} is not a known Class").format(cell["class"]), path)
			continue

		target = _resolve_book(cell["type"], cell["subject"], class_name, ctx)
		if not target:
			issues.warning("book", _("no book item is set for this subject and class, {0} not saved").format(count), path)
			continue

		if (target, class_name) in seen:
			issues.error("duplicate", _("the same book has more than one column in this row"), path)
			continue
		seen.add((target, class_name))

		subject, field = target
		row = rows.setdefault((subject, class_name), {"subject": subject, "class_grade": class_name})
		row[field] = int(count)
	return list(rows.values())


def _editable_fields(record) -> list[dict]:
	"""School and staff values of a row, for the "Edit values" dialog."""
	fields = []
	for field, label in SCHOOL_LABELS.items():
		path = spec_path(("school", field))
		fields.append({"path": path, "label": label, "value": _text(record["school"].get(field))})
	for role, person in record["staff"].items():
		for leaf in ("name", "mobile", "email"):
			path = spec_path(("staff", role, leaf))
			fields.append({"path": path, "label": f"{role.title()} {leaf.title()}", "value": _text(person.get(leaf))})
	return fields


def _preview(clean, ctx) -> dict:
	"""What the row will change: field changes (old -> new), exam rows and books."""
	changes = []
	if clean.existing:
		old = frappe.db.get_value("Customer", clean.existing, [f for f, _l, _k in CUSTOMER_PREVIEW], as_dict=True) or {}
		for field, label, key in CUSTOMER_PREVIEW:
			new = clean.get(key)
			if new and _text(new) != _text(old.get(field)):
				changes.append({"label": label, "old": _text(old.get(field)), "new": _text(new)})
		old_slots = frappe.db.get_value("Customer", clean.existing, list(clean.slots), as_dict=True) or {} if clean.slots else {}
		for field, letter in clean.slots.items():
			if _text(old_slots.get(field)) != letter:
				label = next((k for k, v in SLOT_FIELDS.items() if v == field), field)
				changes.append({"label": f"Slot {label}", "old": _text(old_slots.get(field)), "new": letter})
		address = frappe.db.get_value(
			"Address", {"address_title": clean.existing}, ["address_line1", "city", "pincode"], as_dict=True
		) or {}
		for field, label, key in (("address_line1", "Address", "address"), ("city", "City", "city"), ("pincode", "PIN Code", "pincode")):
			if clean.address and clean.get(key) and _text(clean.get(key)) != _text(address.get(field)):
				changes.append({"label": label, "old": _text(address.get(field)), "new": _text(clean.get(key))})
	else:
		for _field, label, key in CUSTOMER_PREVIEW:
			if clean.get(key):
				changes.append({"label": label, "old": "", "new": _text(clean.get(key))})

	return {
		"is_new": not clean.existing,
		"changes": changes,
		"staff": [{"role": k, **v} for k, v in clean.staff.items()],
		"exams": clean.enrollment,
		"books": [
			{
				"subject": b["subject"],
				"class": b["class_grade"],
				"books": {BOOK_FIELD_LABELS[f]: b[f] for f in BOOK_QTY_FIELDS if f in b},
			}
			for b in clean.books
		],
	}


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
	for field, value in _customer_values(clean, ctx).items():
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

	if clean.books:
		_update_books_selection(customer, clean, ctx)

	return customer.name


def _customer_values(clean, ctx) -> dict:
	return {
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


def _exams_summary_name(customer, ctx):
	return frappe.db.get_value(
		"Exams Summary",
		{"customer": customer, "exam_detail": ctx.exam_detail, "is_wof": 0, "is_little_champ": 0},
	)


def _update_exams_summary(customer: str, clean, ctx) -> None:
	name = _exams_summary_name(customer, ctx)
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


def _open_books_selection(customer):
	return frappe.db.get_value("Books Selection", {"customer": customer, "is_submitted": 0})


def _update_books_selection(customer, clean, ctx) -> None:
	"""Put the sheet's book counts on the school's open Books Selection (the one the Book Order page
	edits), or a new one. Submitted selections are orders already placed and are never changed.

	A subject and class in the sheet gets the sheet's counts for the book types the sheet fills; its
	other book types and the rows the sheet does not mention are kept.
	"""
	name = _open_books_selection(customer.name)
	if name:
		doc = frappe.get_doc("Books Selection", name)
	else:
		doc = frappe.new_doc("Books Selection")
		doc.customer = customer.name
		doc.order_date = clean.registration_date or frappe.utils.nowdate()
	doc.customer_name = customer.customer_name
	doc.academic_year = ctx.academic_year

	existing = {(row.subject, str(row.class_grade)): row for row in doc.select_books}
	for entry in clean.books:
		row = existing.get((entry["subject"], entry["class_grade"]))
		if not row:
			row = doc.append("select_books", {"subject": entry["subject"], "class_grade": entry["class_grade"]})
		for field in BOOK_QTY_FIELDS:
			if field in entry:
				row.set(field, entry[field])

	if doc.is_new():
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)


# --------------------------------------------------------------------------------------------------
# Undo: what a row created and the values it replaced
# --------------------------------------------------------------------------------------------------


def _principal_contact(customer):
	rows = frappe.db.sql(
		"""SELECT c.name FROM `tabContact` c JOIN `tabDynamic Link` l ON l.parent = c.name
		WHERE c.designation = 'Principal' AND l.link_doctype = 'Customer' AND l.link_name = %s LIMIT 1""",
		(customer,),
	)
	return rows[0][0] if rows else None


def _child_rows(doc, field):
	return [row.as_dict(no_default_fields=True) for row in doc.get(field)]


def _snapshot(clean, ctx) -> dict:
	"""The state of what a row is going to touch, before it is imported."""
	snap = {"customer": None, "address": None, "contact": None, "exams_summary": None, "books_selection": None}
	customer = clean.existing
	if not customer:
		return snap
	doc = frappe.get_doc("Customer", customer)
	fields = list(_customer_values(clean, ctx)) + list(SLOT_FIELDS.values())
	snap["customer"] = {
		"name": customer,
		"values": {f: doc.get(f) for f in fields},
		"teachers": _child_rows(doc, "custom_school_teacher_details"),
		"teacher_ids": frappe.get_all("Teacher", {"customer_reference": customer}, pluck="name"),
	}
	address = frappe.db.exists("Address", {"address_title": customer})
	if address:
		a = frappe.get_doc("Address", address)
		snap["address"] = {
			"name": address,
			"values": {f: a.get(f) for f in ("address_line1", "city", "state", "pincode", "custom_taluka", "custom_district")},
		}
	contact = _principal_contact(customer)
	if contact:
		c = frappe.get_doc("Contact", contact)
		snap["contact"] = {
			"name": contact,
			"values": {"first_name": c.first_name},
			"email_ids": _child_rows(c, "email_ids"),
			"phone_nos": _child_rows(c, "phone_nos"),
		}
	es = _exams_summary_name(customer, ctx)
	if es:
		snap["exams_summary"] = {"name": es, "rows": _child_rows(frappe.get_doc("Exams Summary", es), "exam_summary")}
	bs = _open_books_selection(customer)
	if bs:
		snap["books_selection"] = {"name": bs, "rows": _child_rows(frappe.get_doc("Books Selection", bs), "select_books")}
	return snap


def _undo_record(snap, customer, ctx) -> dict:
	"""After import: what was created, what was updated (with old values) and each doc's modified time."""
	created, updated = [], []

	def add(doctype, name, before):
		if not name:
			return
		entry = {"doctype": doctype, "name": name, "modified": str(frappe.db.get_value(doctype, name, "modified"))}
		if before:
			entry["before"] = before
			updated.append(entry)
		else:
			created.append(entry)

	add("Customer", customer, snap["customer"])
	add("Address", frappe.db.exists("Address", {"address_title": customer}), snap["address"])
	add("Contact", _principal_contact(customer), snap["contact"])
	add("Exams Summary", _exams_summary_name(customer, ctx), snap["exams_summary"])
	add("Books Selection", _open_books_selection(customer), snap["books_selection"])
	old_teachers = set((snap["customer"] or {}).get("teacher_ids") or [])
	for teacher in frappe.get_all("Teacher", {"customer_reference": customer}, pluck="name"):
		if teacher not in old_teachers:
			add("Teacher", teacher, None)
	return {"created": created, "updated": updated}


def revert_row(undo: dict) -> list[str]:
	"""Put back what one imported row changed. Returns notes about what could not be reverted."""
	notes = []

	# Decide up front: restoring one document (the Customer) can touch others (its Contact).
	changed = set()
	for entry in undo.get("updated", []) + undo.get("created", []):
		current = frappe.db.get_value(entry["doctype"], entry["name"], "modified")
		if current and str(current) != entry["modified"]:
			changed.add((entry["doctype"], entry["name"]))

	def changed_since(entry):
		return (entry["doctype"], entry["name"]) in changed

	# Updated documents: restore the old values, unless someone changed them after the import.
	for entry in undo.get("updated", []):
		doctype, name, before = entry["doctype"], entry["name"], entry["before"]
		if not frappe.db.exists(doctype, name):
			continue
		if changed_since(entry):
			notes.append(_("{0} {1} was changed after the import and was left as it is").format(doctype, name))
			continue
		doc = frappe.get_doc(doctype, name)
		if doctype == "Books Selection" and doc.is_submitted:
			notes.append(_("Books Selection {0} is submitted and was left as it is").format(name))
			continue
		doc.update(before.get("values") or {})
		if doctype == "Customer":
			doc.set("custom_school_teacher_details", before.get("teachers") or [])
		elif doctype == "Contact":
			doc.set("email_ids", before.get("email_ids") or [])
			doc.set("phone_nos", before.get("phone_nos") or [])
		elif doctype == "Exams Summary":
			doc.set("exam_summary", before.get("rows") or [])
		elif doctype == "Books Selection":
			doc.set("select_books", before.get("rows") or [])
		doc.flags.ignore_validate_update_after_submit = True
		doc.save(ignore_permissions=True)

	# Created documents: delete them. Teachers are linked from the Customer's teacher table, so a
	# new Customer's table is emptied first.
	created = {}
	for entry in undo.get("created", []):
		created.setdefault(entry["doctype"], []).append(entry)
	new_customer = (created.get("Customer") or [None])[0]
	if new_customer and frappe.db.exists("Customer", new_customer["name"]):
		frappe.db.delete("Teachers Details", {"parent": new_customer["name"], "parenttype": "Customer"})

	# The Customer links its primary Contact and Address, so it goes before them (ERPNext also
	# removes contacts and addresses linked only to a deleted Customer).
	for doctype in ("Books Selection", "Exams Summary", "Teacher", "Customer", "Contact", "Address"):
		for entry in created.get(doctype, []):
			if not frappe.db.exists(doctype, entry["name"]):
				continue
			if doctype == "Books Selection" and frappe.db.get_value(doctype, entry["name"], "is_submitted"):
				notes.append(_("Books Selection {0} is submitted and was kept").format(entry["name"]))
				continue
			if doctype != "Teacher" and changed_since(entry):
				notes.append(_("{0} {1} was changed after the import and was kept").format(doctype, entry["name"]))
				continue
			try:
				frappe.delete_doc(doctype, entry["name"], ignore_permissions=True)
			except Exception as e:
				notes.append(
					_("{0} {1} could not be deleted: {2}").format(doctype, entry["name"], strip_html(str(e)))
				)
	return notes


# --------------------------------------------------------------------------------------------------
# Running the checks and the import
# --------------------------------------------------------------------------------------------------


def _corrections_by_row(doc) -> dict:
	out = {}
	for row in doc.get("rows") or []:
		if row.corrections:
			try:
				out[cint(row.row_no)] = json.loads(row.corrections)
			except ValueError:
				pass
	return out


def _school_filter(doc):
	"""School codes to keep when the import is for one school only."""
	if not doc.get("customer"):
		return None
	code, name = frappe.db.get_value("Customer", doc.customer, ["custom_ito_school_code", "name"])
	return {c.upper() for c in (code, name) if c}


def _imported_rows(doc) -> dict:
	"""Log rows already imported by this document. They are kept as they are (with their undo data)
	when the file is checked or imported again; only the other rows are worked on."""
	return {cint(r.row_no): r.as_dict(no_default_fields=True) for r in doc.get("rows") or [] if r.status == "Imported"}


def _checked_rows(doc, skip=()):
	"""(record, clean) for every sheet row of this import, with the form's corrections applied."""
	ctx = build_context(doc.exam_detail)
	corrections = _corrections_by_row(doc)
	only = _school_filter(doc)
	result = []
	for record in read_records(doc.import_file):
		apply_corrections(record, corrections.get(record["row_no"]))
		if only is not None and _text(record["school"].get("school_code")).upper() not in only:
			continue
		if record["row_no"] in skip:
			ctx.codes_seen.add(_text(record["school"].get("school_code")).upper())
			continue
		result.append((record, check_record(record, ctx)))
	return ctx, result, corrections


def _ordered(rows: list[dict]) -> list[dict]:
	return sorted(rows, key=lambda r: cint(r["row_no"]))


def _summary_counts(statuses_and_issues) -> str:
	"""'5 ready (2 with warnings), 2 need fixing: 1 email, 1 subject.'"""
	ready = sum(1 for status, _i in statuses_and_issues if status == "Valid")
	with_warnings = sum(
		1 for status, issues in statuses_and_issues if status == "Valid" and any(i["level"] == "warning" for i in issues)
	)
	failing = [issues for status, issues in statuses_and_issues if status == "Error"]
	text = _("{0} ready").format(ready)
	if with_warnings:
		text += " " + _("({0} with warnings)").format(with_warnings)
	if failing:
		cats = {}
		for issues in failing:
			for cat in {i["category"] for i in issues if i["level"] == "error"}:
				cats[cat] = cats.get(cat, 0) + 1
		detail = ", ".join(f"{n} {ISSUE_CATEGORIES.get(c, c)}" for c, n in sorted(cats.items(), key=lambda x: -x[1]))
		text += ", " + _("{0} need fixing: {1}").format(len(failing), detail)
	return text + "."


def _file_notes(doc, ctx, cleans) -> str:
	notes = []
	# Schedule gaps: a slot used in the file has no date in the exam schedule. Said once per subject.
	gaps = {}
	for clean in cleans:
		for subject, letter in clean.slot_gaps:
			gaps.setdefault((subject, letter), 0)
			gaps[(subject, letter)] += 1
	for (subject, letter), count in sorted(gaps.items()):
		notes.append(
			_("{0} has no slot {1} date in {2}; {3} school(s) use it, their exam rows get no date.").format(
				subject, letter, ctx.exam_detail, count
			)
		)
	duplicate = _same_file_imports(doc)
	if duplicate:
		notes.append(
			_("This file was already imported in {0}.").format(
				", ".join(f"{d.name} ({frappe.utils.format_datetime(d.modified, 'dd MMM yyyy')})" for d in duplicate)
			)
		)
	return "\n".join(notes)


def _same_file_imports(doc):
	content_hash = frappe.db.get_value("File", {"file_url": doc.import_file}, "content_hash")
	if not content_hash:
		return []
	urls = frappe.get_all("File", {"content_hash": content_hash}, pluck="file_url")
	return frappe.get_all(
		"ITO Bulk Import",
		filters={
			"name": ("!=", doc.name),
			"import_file": ("in", urls),
			"status": ("in", ("Completed", "Partially Completed")),
		},
		fields=["name", "modified"],
	)


def _log_run(doc, action, summary):
	doc.append(
		"runs",
		{"run_on": now_datetime(), "action": action, "user": frappe.session.user, "file": doc.import_file, "summary": summary},
	)


def validate_import(doc) -> None:
	"""Check the file and fill the log. Nothing is saved except the import document."""
	kept = _imported_rows(doc)
	ctx, checked, corrections = _checked_rows(doc, skip=kept)
	if not checked and not kept:
		frappe.throw(_("No school rows were found under the header rows."))

	rows = list(kept.values())
	for record, clean in checked:
		status = "Error" if clean.errors else "Valid"
		rows.append(_log_row(record, clean, ctx, status, corrections.get(record["row_no"])))
	doc.set("rows", [])
	for row in _ordered(rows):
		doc.append("rows", row)
	doc.summary = _summary_counts(
		[(r.status, json.loads(r.details or "{}").get("issues", [])) for r in doc.rows if r.status != "Imported"]
	)
	if kept:
		doc.summary += " " + _("{0} already imported.").format(len(kept))
	doc.file_notes = _file_notes(doc, ctx, [c for _r, c in checked])
	doc.status = "Validated"
	_log_run(doc, "Validate", doc.summary)


def _log_row(record, clean, ctx, status, corrections=None, message=None, undo=None, customer=None):
	details = {"issues": clean.issues, "fields": clean.fields, "preview": _preview(clean, ctx)}
	text = message or _plan_text(clean)
	lines = [text] + [issue_text(i) for i in clean.errors] + [_("Warning: {0}").format(issue_text(i)) for i in clean.warnings]
	customer = customer or clean.existing
	return {
		"row_no": clean.row_no,
		"school_code": clean.school_code,
		"school_name": clean.school_name,
		"status": status,
		"customer": customer,
		"exams_summary": _exams_summary_name(customer, ctx) if customer and frappe.db.exists("Customer", customer) else None,
		"books_selection": _open_books_selection(customer) if customer and frappe.db.exists("Customer", customer) else None,
		"message": "\n".join(lines),
		"details": json.dumps(details, default=str),
		"corrections": json.dumps(corrections) if corrections else None,
		"undo_data": json.dumps(undo, default=str) if undo else None,
	}


def _plan_text(clean) -> str:
	what = _("Updates {0}").format(clean.existing) if clean.existing else _("New school")
	counts = sum(e["no_of_students"] for e in clean.enrollment)
	books = sum(row.get(f, 0) for row in clean.books for f in BOOK_QTY_FIELDS)
	return _("{0}: {1} staff, {2} exam rows, {3} students, {4} books").format(
		what, len(clean.staff), len(clean.enrollment), counts, books
	)


def run_import(import_name: str) -> None:
	"""Background job: import every valid row on its own, so one bad school does not stop the rest."""
	doc = frappe.get_doc("ITO Bulk Import", import_name)
	doc.db_set("status", "In Progress", commit=True)

	kept = _imported_rows(doc)
	try:
		ctx, checked, corrections = _checked_rows(doc, skip=kept)
	except Exception as e:
		frappe.db.rollback()
		doc.reload()
		doc.db_set({"status": "Failed", "summary": strip_html(str(e))}, commit=True)
		_notify(doc, _("ITO Bulk Import {0} failed").format(doc.name))
		doc.notify_update()
		return

	results = list(kept.values())
	for record, clean in checked:
		fixes = corrections.get(record["row_no"])
		if clean.errors:
			results.append(_log_row(record, clean, ctx, "Error", fixes))
			continue

		frappe.db.savepoint("ito_bulk_import_row")
		try:
			snap = _snapshot(clean, ctx)
			customer = import_record(clean, ctx)
			undo = _undo_record(snap, customer, ctx)
			frappe.db.release_savepoint("ito_bulk_import_row")
			frappe.db.commit()
			done = _("Updated") if clean.existing else _("Created")
			results.append(
				_log_row(record, clean, ctx, "Imported", fixes, f"{done}. {_plan_text(clean)}", undo, customer)
			)
		except Exception as e:
			frappe.db.rollback(save_point="ito_bulk_import_row")
			frappe.log_error(frappe.get_traceback(), f"ITO Bulk Import {import_name} row {clean.row_no}")
			row = _log_row(record, clean, ctx, "Failed", fixes)
			row["message"] = (strip_html(str(e)) or type(e).__name__) + "\n" + row["message"]
			results.append(row)
		frappe.local.message_log = []

	doc.reload()
	doc.set("rows", [])
	for result in _ordered(results):
		doc.append("rows", result)
	imported = sum(1 for r in results if r["status"] == "Imported")
	doc.status = "Completed" if imported == len(results) else ("Partially Completed" if imported else "Failed")
	doc.summary = _("{0} of {1} schools imported. {2} skipped with errors, {3} failed while saving.").format(
		imported,
		len(results),
		sum(1 for r in results if r["status"] == "Error"),
		sum(1 for r in results if r["status"] == "Failed"),
	)
	_log_run(doc, "Import", doc.summary)
	doc.flags.from_import_job = True
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	_notify(doc, _("ITO Bulk Import {0}: {1}").format(doc.name, doc.summary))
	doc.notify_update()


def run_revert(import_name: str) -> None:
	"""Background job: put back what each imported row changed."""
	doc = frappe.get_doc("ITO Bulk Import", import_name)
	doc.db_set("status", "In Progress", commit=True)
	reverted = kept = 0
	for row in doc.rows:
		if row.status != "Imported" or not row.undo_data:
			continue
		frappe.db.savepoint("ito_bulk_revert_row")
		try:
			undo = json.loads(row.undo_data)
			created = {(e["doctype"], e["name"]) for e in undo.get("created", [])}
			for field, doctype in (("customer", "Customer"), ("exams_summary", "Exams Summary"), ("books_selection", "Books Selection")):
				if (doctype, row.get(field)) in created:
					frappe.db.set_value("ITO Bulk Import Log", row.name, field, None)
					row.set(field, None)
			notes = revert_row(undo)
			frappe.db.release_savepoint("ito_bulk_revert_row")
			frappe.db.commit()
			row.status = "Reverted"
			row.message = "\n".join([_("Reverted.")] + notes)
			reverted += 1
			kept += 1 if notes else 0
		except Exception as e:
			frappe.db.rollback(save_point="ito_bulk_revert_row")
			frappe.log_error(frappe.get_traceback(), f"ITO Bulk Import {import_name} revert row {row.row_no}")
			row.message = _("Revert failed: {0}").format(strip_html(str(e))) + "\n" + (row.message or "")
		frappe.local.message_log = []

	left = sum(1 for r in doc.rows if r.status == "Imported")
	doc.status = "Reverted" if not left else "Partially Reverted"
	doc.summary = _("{0} schools reverted, {1} with parts kept (see each row). {2} could not be reverted.").format(
		reverted, kept, left
	)
	_log_run(doc, "Revert", doc.summary)
	doc.flags.from_import_job = True
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	_notify(doc, _("ITO Bulk Import {0}: {1}").format(doc.name, doc.summary))
	doc.notify_update()


def _notify(doc, subject):
	"""Bell notification for the person who ran the import (and the owner of the import)."""
	from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification

	users = {u for u in (doc.owner, frappe.session.user) if u and u not in ("Guest",)}
	try:
		enqueue_create_notification(
			list(users),
			{
				"type": "Alert",
				"document_type": doc.doctype,
				"document_name": doc.name,
				"subject": subject,
				"from_user": frappe.session.user,
			},
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "ITO Bulk Import notification")


# --------------------------------------------------------------------------------------------------
# Masters from the import form
# --------------------------------------------------------------------------------------------------


def create_subject(subject_name: str, abbr: str, exam_detail: str | None = None) -> str:
	"""Create an ITO School Subject and, when an exam schedule is given, add it there."""
	frappe.has_permission("School Subject", "create", throw=True)
	subject_name = _text(subject_name)
	abbr = _text(abbr).upper()
	if not subject_name or not abbr:
		frappe.throw(_("Subject name and abbreviation are both needed."))
	if not frappe.db.exists("School Subject", subject_name):
		doc = frappe.new_doc("School Subject")
		doc.name = subject_name
		doc.abbr = abbr
		doc.little_champ = 0
		doc.insert()
	if exam_detail:
		add_to_schedule(exam_detail, subject_name)
	return subject_name


def add_to_schedule(exam_detail: str, subject: str) -> None:
	"""Add a subject row (dates empty) to a submitted exam schedule."""
	frappe.has_permission("Yearly Exam Date", "write", exam_detail, throw=True)
	ed = frappe.get_doc("Yearly Exam Date", exam_detail)
	if any(r.subject == subject for r in ed.target_dates):
		return
	row = frappe.get_doc(
		{
			"doctype": "Yearly Exam Date CT",
			"parent": ed.name,
			"parenttype": "Yearly Exam Date",
			"parentfield": "target_dates",
			"idx": len(ed.target_dates) + 1,
			"docstatus": ed.docstatus,
			"subject": subject,
			"school_subject": frappe.db.get_value("School Subject", subject, "abbr"),
			"is_little_champ": frappe.db.get_value("School Subject", subject, "little_champ") or 0,
			"for_chitrakala": 0,
		}
	)
	row.db_insert()
	ed.add_comment("Info", _("Added {0} to the schedule from an ITO Bulk Import. Fill its slot dates.").format(subject))


# --------------------------------------------------------------------------------------------------
# Excel downloads: template, prefilled sheet, rows with errors
# --------------------------------------------------------------------------------------------------


def _template_headers(ctx) -> list[tuple]:
	"""(group, sub, leaf) of every template column."""
	subjects = list(ctx.schedule)
	headers = []
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
	for book_type in ("wb", "yp", "sg", "tb"):
		for subject, fields in sorted(ctx.book_classes.items()):
			classes = set()
			for field in BOOK_FIELDS[book_type]:
				classes |= fields.get(field, set())
			# Subjects of this exam schedule, and the Little Champ books (Text Book / Work Book).
			if not classes or not (subject in ctx.schedule or subject in ctx.little_champ_subjects):
				continue
			label = f"{subject.upper()} {BOOK_TEMPLATE_SUFFIX[book_type]}"
			for class_name in sorted(classes, key=_class_order):
				headers.append((BOOK_TEMPLATE_GROUPS[book_type], label, class_name))
	for subject in subjects:
		headers.append(("SLOT (A/B/C)", subject.upper(), None))
	return headers


def _class_order(class_name: str):
	order = {"Nursery": -3, "Junior KG": -2, "Senior KG": -1}
	if class_name in order:
		return (0, order[class_name])
	return (1, int(class_name)) if class_name.isdigit() else (2, class_name)


def _build_workbook(ctx, headers, data_rows: list[list], editable_rows: int):
	"""The registration sheet: 3 locked header rows, dropdowns for State and slots, an instructions tab."""
	from openpyxl import Workbook
	from openpyxl.styles import Alignment, Font, PatternFill, Protection
	from openpyxl.utils import get_column_letter
	from openpyxl.worksheet.datavalidation import DataValidation

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
	for col in range(1, len(headers) + 1):
		ws.column_dimensions[get_column_letter(col)].width = 14

	for r, values in enumerate(data_rows, start=4):
		for c, value in enumerate(values, start=1):
			if value not in (None, ""):
				ws.cell(row=r, column=c, value=value)

	# Header rows locked, data rows open (sheet protection without a password).
	last_row = 3 + max(editable_rows, len(data_rows))
	unlocked = Protection(locked=False)
	for row in ws.iter_rows(min_row=4, max_row=last_row, max_col=len(headers)):
		for cell in row:
			cell.protection = unlocked
	ws.protection.sheet = True
	ws.protection.formatColumns = False
	ws.protection.formatCells = False
	ws.protection.insertRows = False
	ws.protection.deleteRows = False
	ws.protection.sort = False
	ws.protection.autoFilter = False

	# Dropdowns: State from a list sheet, slots A/B/C.
	lists = wb.create_sheet("LISTS")
	for i, state in enumerate(sorted(STATE_NAME_TO_CODE), start=1):
		lists.cell(row=i, column=1, value=state.upper())
	lists.sheet_state = "hidden"
	state_dv = DataValidation(type="list", formula1=f"=LISTS!$A$1:$A${len(STATE_NAME_TO_CODE)}", allow_blank=True)
	slot_dv = DataValidation(type="list", formula1='"A,B,C"', allow_blank=True)
	ws.add_data_validation(state_dv)
	ws.add_data_validation(slot_dv)
	for col, (group, sub, _leaf) in enumerate(headers, start=1):
		letter = get_column_letter(col)
		if group == "SCHOOL ADDRESS DETAILS" and sub == "STATE":
			state_dv.add(f"{letter}4:{letter}{last_row}")
		elif group.startswith("SLOT"):
			slot_dv.add(f"{letter}4:{letter}{last_row}")

	_instructions_sheet(wb, ctx)
	return wb


def _instructions_sheet(wb, ctx):
	from openpyxl.styles import Font

	ws = wb.create_sheet("INSTRUCTIONS")
	lines = [
		("How to fill this sheet", True),
		(f"Exam schedule: {ctx.exam_detail} ({ctx.academic_year}). One row per school, starting at row 4.", False),
		("Do not change the 3 header rows. Columns are read by their header text.", False),
		("", False),
		("Column groups", True),
		("REGISTRATION DATE, SCHOOL CODE, SCHOOL NAME, MOBILE, EMAIL: the school. School Code must start with the state code (KA for Karnataka).", False),
		("SCHOOL ADDRESS DETAILS: address, city, taluka, district, state (pick from the list) and 6-digit PIN code. Address and City go together.", False),
		("STAFF DETAILS: name, 10-digit mobile and email for the principal, the overall coordinator and each subject teacher.", False),
		("EXAM ENROLLMENT: number of students per subject and class (whole numbers).", False),
		("ORDER COUNT groups: number of books per subject and class. Workbook, Previous Year Paper, Student Guide and Text Book.", False),
		("SLOT (A/B/C): the exam slot per subject. The slot dates come from the exam schedule.", False),
		("", False),
		("Example row", True),
		("SCHOOL CODE: KA1729   SCHOOL NAME: Example Public School   STATE: KARNATAKA   PIN CODE: 572101", False),
		("PRINCIPAL NAME: A Kumar   MOBILE: 9876543210   EMAIL: principal@example.com", False),
		("EXAM ENROLLMENT, SCIENCE OLYMPIAD (ISO), class 5: 30   SLOT, SCIENCE OLYMPIAD (ISO): A", False),
		("", False),
		("Empty cells never clear data already saved for a school. Re-importing the same school updates it.", False),
	]
	for i, (text, heading) in enumerate(lines, start=1):
		cell = ws.cell(row=i, column=1, value=text)
		if heading:
			cell.font = Font(bold=True, size=12)
	ws.column_dimensions["A"].width = 140


def _send_workbook(wb, filename):
	out = io.BytesIO()
	wb.save(out)
	frappe.response["filename"] = filename
	frappe.response["filecontent"] = out.getvalue()
	frappe.response["type"] = "binary"


@frappe.whitelist()
def download_template(exam_detail: str):
	"""An empty registration sheet with one column group per subject of the exam schedule."""
	frappe.has_permission("ITO Bulk Import", "create", throw=True)
	ctx = build_context(exam_detail)
	wb = _build_workbook(ctx, _template_headers(ctx), [], editable_rows=300)
	_send_workbook(wb, f"ITO Registration Import - {ctx.academic_year}.xlsx")


@frappe.whitelist()
def download_prefilled(exam_detail: str, customer: str | None = None):
	"""The template filled with the schools of this exam schedule (or one school), to edit and re-upload."""
	frappe.has_permission("ITO Bulk Import", "create", throw=True)
	ctx = build_context(exam_detail)
	headers = _template_headers(ctx)
	if customer:
		customers = [customer]
	else:
		customers = frappe.get_all(
			"Exams Summary",
			filters={"exam_detail": exam_detail, "is_wof": 0, "is_little_champ": 0},
			pluck="customer",
			distinct=True,
		)
	data = [_prefilled_row(c, ctx, headers) for c in sorted(set(filter(None, customers)))]
	wb = _build_workbook(ctx, headers, data, editable_rows=len(data) + 100)
	name = f"ITO Registrations {customer}" if customer else f"ITO Registrations - {ctx.academic_year}"
	_send_workbook(wb, f"{name}.xlsx")


def _prefilled_row(customer: str, ctx, headers) -> list:
	c = frappe.get_doc("Customer", customer)
	address = frappe.db.get_value(
		"Address",
		{"address_title": customer},
		["address_line1", "city", "custom_taluka", "custom_district", "state", "pincode"],
		as_dict=True,
	) or {}
	school = {
		"registration_date": c.get("custom_registration_date"),
		"school_code": c.get("custom_ito_school_code") or c.name,
		"school_name": c.customer_name,
		"mobile": _plain_mobile(c.get("mobile_no")),
		"email": c.get("email_id"),
		"address": address.get("address_line1"),
		"city": address.get("city"),
		"taluka": address.get("custom_taluka") or c.get("custom_taluka"),
		"district": address.get("custom_district") or c.get("custom_district"),
		"state": (c.get("custom_state") or address.get("state") or "").upper() or None,
		"pincode": address.get("pincode"),
	}
	staff = {}
	for row in c.get("custom_school_teacher_details") or []:
		if row.subject and row.name1:
			staff[row.subject] = {
				"name": frappe.db.get_value("Teacher", row.name1, "name1") or row.name1,
				"mobile": _plain_mobile(row.phone_number),
				"email": row.email_id,
			}
	enrollment = {}
	es = _exams_summary_name(customer, ctx)
	if es:
		for row in frappe.get_doc("Exams Summary", es).exam_summary:
			if (row.form_type or "ITO Registration") == "ITO Registration":
				enrollment[(row.subject, str(row.get("class")))] = row.no_of_students
	books = {}
	bs = _open_books_selection(customer)
	if bs:
		for row in frappe.get_doc("Books Selection", bs).select_books:
			for field in BOOK_QTY_FIELDS:
				if row.get(field):
					books[(row.subject, str(row.class_grade), field)] = row.get(field)

	values = []
	for group, sub, leaf in headers:
		spec = _column_spec(_header(group), _header(sub), _header(leaf))
		values.append(_prefill_value(spec, c, ctx, school, staff, enrollment, books))
	return values


def _plain_mobile(value):
	digits = re.sub(r"\D", "", _text(value))
	return digits[-10:] if len(digits) >= 10 else (value or None)


def _prefill_value(spec, customer, ctx, school, staff, enrollment, books):
	if not spec:
		return None
	kind = spec[0]
	if kind == "school":
		return school.get(spec[1])
	if kind == "staff":
		role = spec[1]
		if "PRINCIPAL" in role:
			subject = "Principal"
		elif "OVERALL" in role:
			subject = "Overall Coordinator"
		else:
			subject = ctx.subject_by_key.get(subject_key(role) or "")
		return (staff.get(subject) or {}).get(spec[2])
	if kind == "enrollment":
		subject = ctx.subject_by_key.get(subject_key(spec[1]) or "")
		return enrollment.get((subject, class_from_header(spec[2], ctx.classes)))
	if kind == "slot":
		field = SLOT_FIELDS.get(subject_key(spec[1]) or "")
		return customer.get(field) if field else None
	if kind == "book":
		class_name = class_from_header(spec[3], ctx.classes)
		target = _resolve_book(spec[1], spec[2], class_name, ctx)
		return books.get((target[0], class_name, target[1])) if target else None
	return None


@frappe.whitelist()
def download_errors(import_name: str):
	"""The uploaded sheet with only the rows that were not imported, corrections filled in and an
	ERRORS column, to fix and upload again."""
	from openpyxl import load_workbook
	from openpyxl.utils import column_index_from_string

	doc = frappe.get_doc("ITO Bulk Import", import_name)
	doc.check_permission("read")
	failing = {cint(r.row_no): r for r in doc.rows if r.status in ("Error", "Failed")}
	if not failing:
		frappe.throw(_("There are no rows with errors."))

	file_doc = frappe.get_doc("File", {"file_url": doc.import_file})
	wb = load_workbook(io.BytesIO(file_doc.get_content()))
	ws = next((s for s in wb.worksheets if "REGISTRATION" in s.title.upper()), wb.worksheets[0])
	if ws.protection.sheet:
		ws.protection.sheet = False

	records = {r["row_no"]: r for r in read_records(doc.import_file)}
	for row_no, log in failing.items():
		record = records.get(row_no)
		if not record or not log.corrections:
			continue
		for path, value in json.loads(log.corrections).items():
			cell = record["cells"].get(path)
			if cell:
				ws[cell] = value

	error_col = ws.max_column + 1
	ws.cell(row=1, column=error_col, value="ERRORS")
	for row_no, log in failing.items():
		ws.cell(row=row_no, column=error_col, value=log.message)

	header_end = min(records) - 1 if records else 3
	for row_no in range(ws.max_row, header_end, -1):
		if row_no not in failing:
			ws.delete_rows(row_no)

	out = io.BytesIO()
	wb.save(out)
	frappe.response["filename"] = f"{doc.name} - rows to fix.xlsx"
	frappe.response["filecontent"] = out.getvalue()
	frappe.response["type"] = "binary"
