"""Chitrakala student list: the upload in the Entry Matrix step of the Chitrakala registration form
and the read-only Chitrakala Student List page.

The list is saved (api._save_chitrakala_rosters) into a Bulk Student List (for_wof_list = 1) with one
Exam List CT row per student and competition, and synced to Registered Students. That document
carries the academic year, so it is the source for the read-only page. Roll numbers live on
Registered Students and are matched by student name and class (and mobile number when the name
repeats in a class).

The Chitrakala sheet is not the WOF Olympiad sheet (school_student_list): it has Parent Name and
Section columns, the Chitrakala competitions of the school's Entry Matrix, pre-primary classes
(Nursery, LKG, UKG), and pre-primary students (Group A) may only take the colouring competition.
A saved upload fills both the student list and the Entry Matrix counts.
"""

import re
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint

from ito_customization.ito_customization.academic_year import academic_year_variants
from ito_customization.ito_customization.school_student_list import (
	CLASS_PATTERN,
	LC_CLASS_ALIASES,
	MAX_ROWS,
	MOBILE_HEADERS,
	NAME_HEADERS,
	NO_VALUES,
	SERIAL_HEADERS,
	YES_VALUES,
	_normalise_header,
	class_sort_key,
	get_session_school,
	is_locked,
	read_sheet,
	registration_deadline,
)

PRE_PRIMARY_CLASSES = ("Nursery", "Junior KG", "Senior KG")
CLASS_HEADERS = {"class", "std", "standard", "classstd", "grade"}
PARENT_HEADERS = {"parentname", "parent", "fathername", "mothername", "guardianname", "parentsname"}
SECTION_HEADERS = {"section", "sec", "div", "division"}
SECTION_PATTERN = re.compile(r"^[A-Z0-9]{1,3}$")
DEMO_MARKERS = ("DEMO STUDENT", "SAMPLE - REPLACE")


def _latest_chitrakala_list(customer: str, academic_year: str | None) -> frappe._dict | None:
	filters = {"customer": customer, "for_wof_list": 1}
	if academic_year:
		filters["academic_year"] = ["in", academic_year_variants(academic_year)]
	rows = frappe.get_all(
		"Bulk Student List",
		filters=filters,
		fields=["name", "academic_year", "modified"],
		order_by="modified desc",
		limit=1,
	)
	return rows[0] if rows else None


def _roll_numbers(customer: str) -> dict:
	"""(student name, class) -> list of (mobile, roll no) for the school's active students."""
	found = defaultdict(list)
	for row in frappe.get_all(
		"Registered Students",
		filters={"school": customer, "not_registered": 0, "docstatus": ["<", 2]},
		fields=["student_name", "class", "mobile_number", "roll_no"],
	):
		if not row.roll_no:
			continue
		key = ((row.student_name or "").strip().upper(), str(row["class"] or ""))
		found[key].append(((row.mobile_number or "").strip(), row.roll_no))
	return found


def _match_roll_no(candidates: list, mobile: str) -> str:
	if not candidates:
		return ""
	if len(candidates) == 1:
		return candidates[0][1]
	for candidate_mobile, roll_no in candidates:
		if mobile and candidate_mobile == mobile:
			return roll_no
	# The same name twice in one class with no mobile to tell them apart: do not guess.
	return ""


@frappe.whitelist()
def get_chitrakala_student_list(filters=None):
	"""Every Chitrakala student saved by the logged-in school for its current academic year."""
	school = get_session_school()
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	class_filter = str(filters.get("class") or "").strip()
	subject_filter = str(filters.get("subject") or "").strip().upper()

	empty = {
		"students": [],
		"totals": {"students": 0, "entries": 0, "classes": 0, "with_roll_no": 0},
		"by_subject": {},
		"shown": 0,
		"filters": {"classes": [], "subjects": []},
		"academic_year": school.academic_year,
		"last_saved": None,
	}

	bulk_list = _latest_chitrakala_list(school.name, school.academic_year)
	if not bulk_list:
		return empty

	entries = frappe.get_all(
		"Exam List CT",
		filters={"parent": bulk_list.name, "parenttype": "Bulk Student List", "parentfield": "table_xxdu"},
		fields=["student_name", "class", "mobile_number", "school_subject", "abbr"],
		order_by="idx asc",
	)

	roll_numbers = _roll_numbers(school.name)
	students = {}
	for entry in entries:
		name = (entry.student_name or "").strip().upper()
		if not name:
			continue
		class_name = str(entry["class"] or "")
		mobile = (entry.mobile_number or "").strip()
		key = (name, class_name, mobile)
		student = students.get(key)
		if not student:
			student = students[key] = {
				"key": f"{len(students) + 1}",
				"student_name": name,
				"class": class_name,
				"mobile": mobile,
				"roll_no": _match_roll_no(roll_numbers.get((name, class_name), []), mobile),
				"subjects": [],
			}
		subject = (entry.abbr or entry.school_subject or "").strip().upper()
		if subject and subject not in student["subjects"]:
			student["subjects"].append(subject)

	rows = list(students.values())
	# Totals are for the whole list so the filters narrow the table, not the headline numbers.
	by_subject = defaultdict(int)
	for student in rows:
		for subject in student["subjects"]:
			by_subject[subject] += 1

	classes = sorted({s["class"] for s in rows if s["class"]}, key=class_sort_key)
	totals = {
		"students": len(rows),
		"entries": sum(len(s["subjects"]) for s in rows),
		"classes": len(classes),
		"with_roll_no": sum(1 for s in rows if s["roll_no"]),
	}

	if class_filter:
		rows = [s for s in rows if s["class"] == class_filter]
	if subject_filter:
		rows = [s for s in rows if subject_filter in s["subjects"]]
	rows.sort(key=lambda s: (class_sort_key(s["class"]), s["student_name"]))

	return {
		"students": rows,
		"totals": totals,
		"by_subject": dict(sorted(by_subject.items())),
		"shown": len(rows),
		"filters": {"classes": classes, "subjects": sorted(by_subject)},
		"academic_year": bulk_list.academic_year or school.academic_year,
		"last_saved": str(bulk_list.modified) if bulk_list.modified else None,
	}


# --------------------------------------------------------------------------------------------------
# Upload (in the Entry Matrix step of the Chitrakala registration form)
# --------------------------------------------------------------------------------------------------

# The Entry Matrix groups (Step3EntryMatrix.tsx): A Nursery-UKG, B 1-2, C 3-5, D 6-8, E 9-12.
GROUPS = ("group_a", "group_b", "group_c", "group_d", "group_e")


def class_group(class_name: str) -> str | None:
	if class_name in PRE_PRIMARY_CLASSES:
		return "group_a"
	if not str(class_name).isdigit():
		return None
	number = int(class_name)
	if 1 <= number <= 2:
		return "group_b"
	if 3 <= number <= 5:
		return "group_c"
	if 6 <= number <= 8:
		return "group_d"
	if 9 <= number <= 12:
		return "group_e"
	return None


def is_colouring(subject: str) -> bool:
	lower = (subject or "").lower()
	return "colour" in lower or "color" in lower


def get_competitions(school=None) -> list[dict]:
	"""The competitions the school's Entry Matrix shows, as {name, abbr, code}, in the same order.

	The Chitrakala form builds its Entry Matrix from get_customer_from_session_user's exam summaries
	marked for_chitrakala, so the upload reads the same list: every column in the template is a row in
	the matrix and nothing is uploaded that the matrix cannot show.
	"""
	from ito_customization.ito_customization.api import derive_short_name, get_customer_from_session_user

	summaries = (get_customer_from_session_user() or {}).get("exam_summaries") or []
	names = []
	for summary in summaries:
		subject = summary.get("subject")
		if subject and cint(summary.get("for_chitrakala")) and subject not in names:
			names.append(subject)
	abbrs = dict(frappe.get_all("School Subject", filters={"name": ["in", names]}, fields=["name", "abbr"], as_list=True)) if names else {}
	return [
		{
			"name": name,
			"abbr": ((abbrs.get(name) or "").strip() or derive_short_name(name)).upper(),
			"code": frappe.scrub(name),
		}
		for name in names
	]


def pre_primary_competitions(competitions: list[dict]) -> set[str]:
	"""Competitions open to Nursery, LKG and UKG (Group A): only colouring, as in the Entry Matrix."""
	return {c["name"] for c in competitions if is_colouring(c["name"])}


def matrix_counts(students) -> dict:
	"""{competition name: {group_a..group_e: students}} for rows of (class, [competition names])."""
	counts = {}
	for class_name, subject_names in students:
		group = class_group(class_name)
		if not group:
			continue
		for name in subject_names:
			counts.setdefault(name, dict.fromkeys(GROUPS, 0))[group] += 1
	return counts


def _parse_class(raw_class: str):
	"""Return (ERP class name, error). Nursery, LKG (Junior KG), UKG (Senior KG) and 1 to 12."""
	class_name = LC_CLASS_ALIASES.get(_normalise_header(raw_class))
	if not class_name:
		match = CLASS_PATTERN.match(raw_class.strip()) if raw_class else None
		if match and 1 <= int(match.group(1)) <= 12:
			class_name = str(int(match.group(1)))
	if not class_name:
		return None, _("Class must be Nursery, LKG, UKG or 1 to 12 (found '{0}').").format(raw_class)
	if not frappe.db.exists("Class", class_name):
		return None, _("Class {0} is not set up.").format(class_name)
	return class_name, None


def parse_chitrakala_sheet(rows: list[list[str]], competitions: list[dict]) -> dict:
	"""Validate a Chitrakala sheet. Nothing is saved; every row carries its own errors."""
	result = {"rows": [], "errors": [], "warnings": [], "subject_columns": []}

	header_index = next((i for i, row in enumerate(rows) if any(row)), None)
	if header_index is None:
		result["errors"].append(_("The file has no rows."))
		return result

	lookup = {}
	for competition in competitions:
		for key in (competition["abbr"], competition["name"], competition["code"]):
			if key:
				lookup.setdefault(_normalise_header(key), competition)

	columns, competition_by_column, unknown = {}, {}, []
	for index, title in enumerate(rows[header_index]):
		key = _normalise_header(title)
		if not key:
			continue
		if key in NAME_HEADERS or key == "studentsname":
			columns.setdefault("name", index)
		elif key in CLASS_HEADERS:
			columns.setdefault("class", index)
		elif key in SECTION_HEADERS:
			columns.setdefault("section", index)
		elif key in PARENT_HEADERS:
			columns.setdefault("parent", index)
		elif key in MOBILE_HEADERS:
			columns.setdefault("mobile", index)
		elif key in SERIAL_HEADERS:
			columns.setdefault("serial", index)
		elif key in lookup:
			competition_by_column.setdefault(index, lookup[key])
		else:
			unknown.append(title)

	for required, label in (("name", "Student Name"), ("class", "Class")):
		if required not in columns:
			result["errors"].append(_("Required column missing: {0}.").format(label))
	if not competition_by_column:
		result["errors"].append(
			_("No competition columns found. Use these headers: {0}.").format(
				", ".join(c["abbr"] for c in competitions)
			)
		)
	if unknown:
		result["warnings"].append(
			_("These columns are not recognised and were ignored: {0}.").format(", ".join(unknown))
		)
	if result["errors"]:
		return result

	result["subject_columns"] = [c["abbr"] for c in competition_by_column.values()]
	pre_primary_allowed = pre_primary_competitions(competitions)
	pre_primary_labels = ", ".join(c["abbr"] for c in competitions if c["name"] in pre_primary_allowed)
	seen_students, demo_rows = {}, 0

	for offset, row in enumerate(rows[header_index + 1 :], start=header_index + 2):

		def cell(key, row=row):
			index = columns.get(key)
			return row[index] if index is not None and index < len(row) else ""

		marks = {
			index: (row[index].strip().lower() if index < len(row) else "") for index in competition_by_column
		}
		if not any(row) or not (cell("name") or any(m not in NO_VALUES for m in marks.values())):
			continue

		name = " ".join(cell("name").split()).upper()
		# The downloaded template carries sample rows; they are skipped, as on the old Student List page.
		if any(marker in name for marker in DEMO_MARKERS):
			demo_rows += 1
			continue
		if len(result["rows"]) >= MAX_ROWS:
			result["errors"].append(
				_("The sheet has more than {0} students. Split it and upload in parts.").format(MAX_ROWS)
			)
			break

		errors = []
		if not name:
			errors.append(_("Student name is missing."))
		elif len(name) > 100:
			errors.append(_("Student name is longer than 100 characters."))

		raw_class = cell("class")
		class_name, class_error = _parse_class(raw_class)
		if class_error:
			errors.append(class_error)

		section = cell("section").strip().upper() or "A"
		if not SECTION_PATTERN.match(section):
			errors.append(_("Section '{0}' is not valid. Use a letter such as A or B.").format(cell("section")))

		mobile = re.sub(r"[\s\-()]", "", cell("mobile"))
		digits = mobile.lstrip("+")
		if mobile and not (digits.isdigit() and 7 <= len(digits) <= 15):
			errors.append(_("Mobile number '{0}' is not valid.").format(cell("mobile")))

		parent = " ".join(cell("parent").split())

		chosen = []
		for index, mark in marks.items():
			if mark in YES_VALUES:
				chosen.append(competition_by_column[index])
			elif mark not in NO_VALUES:
				errors.append(
					_("{0}: use Yes/No or 1/0 (found '{1}').").format(competition_by_column[index]["abbr"], row[index])
				)
		if not chosen:
			errors.append(_("Select at least one competition."))
		if class_name in PRE_PRIMARY_CLASSES:
			blocked = [c["abbr"] for c in chosen if c["name"] not in pre_primary_allowed]
			if blocked:
				errors.append(
					_("Nursery, LKG and UKG can only take {0} (found {1}).").format(
						pre_primary_labels, ", ".join(blocked)
					)
					if pre_primary_labels
					else _("Nursery, LKG and UKG can only take the colouring competition, which is not offered to your school (found {0}).").format(
						", ".join(blocked)
					)
				)

		if name and class_name:
			key = (name, class_name, section, digits)
			if key in seen_students:
				errors.append(
					_("Duplicate of row {0} (same name, class, section and mobile).").format(seen_students[key])
				)
			else:
				seen_students[key] = offset

		result["rows"].append(
			{
				"row": offset,
				"serial_no": cell("serial"),
				"student_name": name,
				"parent_name": parent,
				"mobile": mobile,
				"class": class_name or raw_class,
				"section": section,
				"subjects": [c["abbr"] for c in chosen],
				"subject_names": [c["name"] for c in chosen],
				"errors": errors,
			}
		)

	if demo_rows:
		result["warnings"].append(_("{0} sample row(s) from the template were skipped.").format(demo_rows))
	if not result["rows"] and not result["errors"]:
		result["errors"].append(_("The file has no student rows."))
	return result


def _summarise_preview(parsed: dict, school, existing_students: int) -> dict:
	invalid = [row for row in parsed["rows"] if row["errors"]]
	by_class = defaultdict(int)
	for row in parsed["rows"]:
		if not row["errors"]:
			by_class[row["class"]] += 1
	locked = is_locked(school)
	return {
		"rows": parsed["rows"],
		"errors": parsed["errors"],
		"warnings": parsed["warnings"],
		"subject_columns": parsed["subject_columns"],
		"total_rows": len(parsed["rows"]),
		"valid_rows": len(parsed["rows"]) - len(invalid),
		"invalid_rows": len(invalid),
		"entries": sum(len(row["subjects"]) for row in parsed["rows"] if not row["errors"]),
		"by_class": dict(sorted(by_class.items(), key=lambda item: class_sort_key(item[0]))),
		"can_save": bool(parsed["rows"]) and not invalid and not parsed["errors"] and not locked,
		"locked": locked,
		"existing_students": existing_students,
		# What the Entry Matrix will show once this file is saved.
		"matrix_counts": matrix_counts(
			(row["class"], row["subject_names"]) for row in parsed["rows"] if not row["errors"]
		),
	}


def _saved_counts(school) -> dict:
	"""Students, competition entries and Entry Matrix counts of the school's saved Chitrakala list."""
	bulk_list = _latest_chitrakala_list(school.name, school.academic_year)
	if not bulk_list:
		return {"students": 0, "entries": 0, "last_saved": None, "matrix_counts": {}}
	rows = frappe.get_all(
		"Exam List CT",
		filters={"parent": bulk_list.name, "parenttype": "Bulk Student List", "parentfield": "table_xxdu"},
		fields=["student_name", "class", "mobile_number", "school_subject"],
	)
	students = {
		((r.student_name or "").strip().upper(), str(r["class"] or ""), (r.mobile_number or "").strip())
		for r in rows
		if (r.student_name or "").strip()
	}
	return {
		"students": len(students),
		"entries": len(rows),
		"last_saved": str(bulk_list.modified),
		"matrix_counts": matrix_counts((str(r["class"] or ""), [r.school_subject]) for r in rows if r.school_subject),
	}


@frappe.whitelist()
def get_chitrakala_upload_state():
	"""What the upload step needs: whether a list is saved, its size and whether it is locked."""
	school = get_session_school()
	counts = _saved_counts(school)
	competitions = get_competitions(school)
	pre_primary = pre_primary_competitions(competitions)
	return {
		"has_list": counts["students"] > 0,
		"students": counts["students"],
		"entries": counts["entries"],
		"last_saved": counts["last_saved"],
		"matrix_counts": counts["matrix_counts"],
		"locked": is_locked(school),
		"deadline": str(registration_deadline(school) or "") or None,
		"academic_year": school.academic_year,
		"competitions": [c["abbr"] for c in competitions],
		"pre_primary_competitions": [c["abbr"] for c in competitions if c["name"] in pre_primary],
	}


@frappe.whitelist()
def preview_chitrakala_student_list(filedata=None, filename=None):
	"""Validate an upload and return it row by row. Saves nothing."""
	school = get_session_school()
	parsed = parse_chitrakala_sheet(read_sheet(filedata, filename), get_competitions(school))
	return _summarise_preview(parsed, school, _saved_counts(school)["students"])


@frappe.whitelist()
def save_chitrakala_student_list(filedata=None, filename=None, replace=0):
	"""Validate the upload again on the server and save it as the school's Chitrakala student list.

	The file is re-read here rather than trusting a preview from the browser. An existing list is
	replaced only when the school confirms (replace=1).
	"""
	from ito_customization.ito_customization.api import _save_chitrakala_rosters

	school = get_session_school()
	if is_locked(school):
		frappe.throw(_("The registration deadline has passed, so the student list can no longer be changed."))

	competitions = get_competitions(school)
	parsed = parse_chitrakala_sheet(read_sheet(filedata, filename), competitions)
	existing = _saved_counts(school)["students"]
	preview = _summarise_preview(parsed, school, existing)
	if not preview["can_save"]:
		return {"success": False, "message": _("Fix the errors in the file before saving."), "preview": preview}
	if existing and not cint(replace):
		return {"success": False, "needs_confirmation": True, "existing_students": existing}

	# Same shape as the old Student List page sends, so both save the same way.
	rosters = {}
	for row in parsed["rows"]:
		batch = rosters.setdefault(
			f"{row['class']}_{row['section']}",
			{"class_grade": row["class"], "section": row["section"], "students": []},
		)
		batch["students"].append(
			{
				"id": f"upload_row_{row['row']}",
				"serialNum": row["serial_no"] or str(len(batch["students"]) + 1).zfill(3),
				"roll_no": "",
				"name": row["student_name"],
				"mobile": row["mobile"],
				"parentName": row["parent_name"],
				"subjects": {name: True for name in row["subject_names"]},
			}
		)
	payload = {
		"school_info": {"academic_year": school.academic_year, "school_name": school.customer_name},
		"rosters": rosters,
		"totals": {
			"total_students": len(parsed["rows"]),
			"total_entries": preview["entries"],
			"total_batches": len(rosters),
		},
	}

	flags = frappe.flags.ignore_permissions
	frappe.flags.ignore_permissions = True
	try:
		_save_chitrakala_rosters(school.name, payload, competitions=competitions)
	finally:
		frappe.flags.ignore_permissions = flags

	return {
		"success": True,
		"students": len(parsed["rows"]),
		"matrix_counts": preview["matrix_counts"],
		"state": get_chitrakala_upload_state(),
	}
