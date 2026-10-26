"""Extract and import Registered Students from a private image or PDF."""

import base64
import io
import json
import re
from pathlib import Path

import frappe
from frappe import _

from ito_customization.ito_customization.api import allocate_roll_number

MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_PAGES = 10
MAX_STUDENTS = 200
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


@frappe.whitelist(methods=["POST"])
def extract_students(file_name: str, school: str, academic_year: str) -> dict:
	"""Return editable student rows; extraction never writes student records."""
	check_import_access(school, academic_year)
	file_doc = get_private_file(file_name)
	subjects = get_subject_master()
	classes = get_class_master()
	agent_name = frappe.db.get_single_value("ITO AI Settings", "student_list_agent")
	if not agent_name or not frappe.db.exists("AI Agent", agent_name):
		frappe.throw(_("Set Student List Agent in ITO AI Settings before importing."))

	from finbyzai.ai.agent.agent_service import AgentService

	student_rows = []
	previous_class = ""
	try:
		for image, grid_image in file_images(file_doc):
			response = AgentService(agent_name).invoke(
				student_image=base64.b64encode(image).decode("ascii"),
				school=school,
				academic_year=academic_year,
				valid_classes=json.dumps(classes),
				previous_class=previous_class,
				valid_subjects=json.dumps(subjects),
			)
			data = response.model_dump() if hasattr(response, "model_dump") else response
			if not isinstance(data, dict) or not isinstance(data.get("students"), list):
				frappe.throw(_("The AI Agent did not return a student list."))
			verify_subject_grid(data, grid_image, subjects)
			student_rows.extend(data["students"])
			for row in reversed(data["students"]):
				found_class = resolve_class(row.get("class"), classes)
				if found_class:
					previous_class = found_class
					break
			if len(student_rows) > MAX_STUDENTS:
				frappe.throw(_("The file contains more than {0} students.").format(MAX_STUDENTS))
	except Exception as exc:
		if is_provider_budget_error(exc):
			frappe.throw(_(
				"The AI provider cannot process this student list because its available credits or "
				"in-flight request budget are exhausted. Wait for active requests to finish and try again, "
				"or add credits to the configured provider account. "
				"No students were created."
			))
		frappe.log_error(frappe.get_traceback(), "Registered Students AI Extraction")
		raise

	return {
		"rows": review_rows(student_rows, school, academic_year, subjects, classes),
		"file_name": file_doc.name,
	}


@frappe.whitelist(methods=["POST"])
def import_students(
	rows: str, school: str, academic_year: str, file_name: str
) -> dict:
	"""Create approved rows without retiring students missing from the upload."""
	check_import_access(school, academic_year)
	get_private_file(file_name)
	try:
		rows = json.loads(rows) if isinstance(rows, str) else rows
	except (TypeError, json.JSONDecodeError):
		frappe.throw(_("Student rows must be valid JSON."))
	if not isinstance(rows, list) or not 0 < len(rows) <= MAX_STUDENTS:
		frappe.throw(_("Select between 1 and {0} student rows.").format(MAX_STUDENTS))

	subjects = get_subject_master()
	subject_lookup = subject_index(subjects)
	classes = get_class_master()
	school_doc = frappe.get_doc("Customer", school)
	ito_code = (school_doc.get("custom_ito_school_code") or "").strip().upper()
	if not ito_code:
		frappe.throw(_("The selected school needs an ITO School Code."))
	if frappe.db.exists("Customer", {
		"custom_ito_school_code": ito_code,
		"name": ["!=", school],
	}):
		frappe.throw(_("This ITO School Code belongs to another school too."))
	results = [None] * len(rows)
	prepared = []
	seen = set()
	for index, row in enumerate(rows, start=1):
		if not isinstance(row, dict):
			results[index - 1] = {"row_id": index, "error": _("Invalid row.")}
			continue
		row_id = row.get("row_id") or index
		try:
			student = validated_student(row, subject_lookup, classes)
			key = (student["class"], student["student_name"].casefold())
			if key in seen:
				frappe.throw(_("This student appears twice in the selected class."))
			seen.add(key)
			# check_duplicate(student, school, academic_year)
			prepared.append((index, row_id, student))
		except frappe.ValidationError as exc:
			results[index - 1] = {"row_id": row_id, "error": str(exc)}

	for index, row_id, student in sorted(
		prepared, key=lambda item: (item[2]["class"], item[2]["student_name"].casefold(), item[0])
	):
		frappe.db.savepoint(f"student_import_{index}")
		try:
			roll_no = allocate_roll_number(ito_code, class_code(student["class"]))
			doc = frappe.get_doc({
				"doctype": "Registered Students",
				"roll_no": roll_no,
				"school": school,
				"full_name": school_doc.customer_name,
				"ito_school_code": ito_code,
				"student_name": student["student_name"],
				"serial_number": student["serial_no"],
				"class": student["class"],
				"academic_year": academic_year,
				"mobile_number": student["mobile"],
				"subjects_registered": [
					{"subject": subject["name"], "abbr": subject["abbr"]}
					for subject in student["subjects"]
				],
			})
			doc.insert()
			results[index - 1] = {"row_id": row_id, "name": doc.name}
		except (frappe.ValidationError, frappe.DuplicateEntryError, frappe.LinkValidationError) as exc:
			frappe.db.rollback(save_point=f"student_import_{index}")
			results[index - 1] = {"row_id": row_id, "error": str(exc)}
	return {"results": results}


def check_import_access(school: str, academic_year: str) -> None:
	if not frappe.has_permission("Registered Students", "create"):
		frappe.throw(_("You cannot create Registered Students."), frappe.PermissionError)
	for doctype, name in (
		("Customer", school),
		("School Academic Year", academic_year),
	):
		if not isinstance(name, str) or not name or not frappe.db.exists(doctype, name):
			frappe.throw(_("Select a valid {0}.").format(doctype))
		frappe.get_doc(doctype, name).check_permission("read")


def is_provider_budget_error(error: Exception) -> bool:
	markers = (
		"in_flight_budget_exhausted", "402", "payment required",
		"requires more credits", "can only afford", "insufficient credits",
	)
	seen = set()
	while error is not None and id(error) not in seen:
		seen.add(id(error))
		if getattr(error, "status_code", None) == 402:
			return True
		text = str(error).casefold()
		if any(marker in text for marker in markers):
			return True
		error = error.__cause__ or error.__context__
	return False


def get_private_file(file_name: str):
	if not isinstance(file_name, str) or not file_name:
		frappe.throw(_("Upload an image or PDF first."))
	file_doc = frappe.get_doc("File", file_name)
	file_doc.check_permission("read")
	if not file_doc.is_private:
		frappe.throw(_("Upload the file as a private attachment."))
	if file_doc.file_size and file_doc.file_size > MAX_FILE_SIZE:
		frappe.throw(_("The file must be 10 MB or smaller."))
	return file_doc


def file_images(file_doc):
	from PIL import Image, ImageOps

	path = file_doc.get_full_path()
	extension = Path(file_doc.file_name or "").suffix.lower()
	if extension in IMAGE_EXTENSIONS:
		with Image.open(path) as source:
			image = ImageOps.exif_transpose(source)
			yield jpeg_bytes(image), png_bytes(image)
		return
	if extension != ".pdf":
		frappe.throw(_("Upload a PDF, JPG, PNG, or WebP file."))

	import fitz

	with fitz.open(path) as pdf:
		if not 0 < len(pdf) <= MAX_PAGES:
			frappe.throw(_("PDFs must contain between 1 and {0} pages.").format(MAX_PAGES))
		for page in pdf:
			pixmap = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
			image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
			grid_pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
			grid_image = Image.frombytes(
				"RGB", (grid_pixmap.width, grid_pixmap.height), grid_pixmap.samples
			)
			yield jpeg_bytes(image), png_bytes(grid_image)


def jpeg_bytes(image) -> bytes:
	image = image.convert("RGB")
	image.thumbnail((3000, 3000))
	buffer = io.BytesIO()
	image.save(buffer, format="JPEG", quality=90)
	return buffer.getvalue()


def png_bytes(image) -> bytes:
	buffer = io.BytesIO()
	image.save(buffer, format="PNG")
	return buffer.getvalue()


def get_subject_master() -> list[dict]:
	"""Use the subject master as the source of link names and abbreviations."""
	subjects = frappe.get_all("School Subject", fields=["name", "abbr"])
	return [
		{
			"name": subject.name,
			"abbr": (subject.abbr or _code_in_name(subject.name) or "").strip(),
		}
		for subject in subjects
	]


def get_class_master() -> list[str]:
	return [row.name for row in frappe.get_all("Class", fields=["name"])]


def resolve_class(value, classes: list[str]) -> str | None:
	raw = re.sub(r"^(class|std|standard|grade)\s*[-.:]*\s*", "", str(value or "").strip(), flags=re.I)
	raw = raw.strip(" .-")
	roman = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6,
		"VII": 7, "VIII": 8, "IX": 9, "X": 10, "XI": 11, "XII": 12}
	if raw.upper() in roman:
		raw = str(roman[raw.upper()])
	elif raw.isdigit():
		raw = str(int(raw))
	return next((name for name in classes if name.casefold() == raw.casefold()), None)


def class_code(class_name: str) -> str:
	code = class_name.lower().replace("class", "").strip()
	return code.zfill(2) if code.isdigit() else code


def subject_index(subjects: list[dict]) -> dict:
	lookup = {}
	for subject in subjects:
		for value in (subject["name"], subject["abbr"], _name_without_code(subject["name"])):
			key = _subject_key(value)
			if not key:
				continue
			if key in lookup and lookup[key] != subject:
				lookup[key] = None
			else:
				lookup[key] = subject
	return lookup


def resolve_subject(value, lookup: dict) -> dict | None:
	if isinstance(value, dict):
		values = (value.get("abbr"), value.get("name"))
	else:
		values = (value,)
	matches = []
	for item in values:
		key = _subject_key(item)
		if key:
			matches.append(lookup.get(key))
	return matches[0] if matches and all(match == matches[0] for match in matches) else None


def verify_subject_grid(data, grid_image, subjects) -> None:
	from ito_customization.ito_customization.doctype.registered_students.student_subject_grid import (
		STANDARD_COLUMNS, read_grid,
	)

	lookup = subject_index(subjects)
	students = data["students"]
	# Every ITO sheet prints the same ten columns; the model's header reading is the weak part.
	columns = list(STANDARD_COLUMNS)
	if not all(resolve_subject(code, lookup) for code in columns):
		columns = []
		for value in data.get("subject_columns") or []:
			matched = resolve_subject(value, lookup)
			if matched and matched["abbr"] and matched["abbr"] not in columns:
				columns.append(matched["abbr"])
	result = None
	if columns and students:
		try:
			result = read_grid(grid_image, columns, len(students))
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Registered Students Subject Verification")
	grid_rows = result.rows if result and result.rows else None

	for index, row in enumerate(students):
		if not isinstance(row, dict):
			continue
		issues = row.get("uncertain_fields") or []
		if not isinstance(issues, list):
			issues = [str(issues)]
		model_subjects = provisional_subjects(row, lookup)
		if grid_rows is None:
			row["subjects"] = model_subjects
			row["subject_review_required"] = 1
			if result:
				issues.append(_("Sheet has {0} ticked rows but {1} students were read; a student row may be missing").format(
					result.tick_rows, len(students)))
			else:
				issues.append(_("Subject grid could not be verified; check every subject against the image"))
		else:
			grid = grid_rows[index]
			grid_subjects = [resolve_subject(code, lookup)["abbr"] for code in grid.subjects]
			agrees = set(grid_subjects) == set(model_subjects)
			row["subjects"] = grid_subjects
			row["subject_review_required"] = 0 if agrees and not grid.needs_review else 1
			issues = [issue for issue in issues if "subject" not in str(issue).casefold()]
			if not agrees:
				issues.append(_("Subject scan and AI reading differ; check against the image"))
			if grid.needs_review:
				issues.append(_("Cancelled or borderline tick; check subjects against the image"))
		row["uncertain_fields"] = issues


def provisional_subjects(row: dict, lookup: dict) -> list[str]:
	selected = []
	for cell in row.get("subject_cells") or []:
		if not isinstance(cell, dict) or cell.get("status") != "marked":
			continue
		matched = resolve_subject(cell.get("abbr"), lookup)
		if matched and matched["abbr"] not in selected:
			selected.append(matched["abbr"])
	return selected


def review_rows(rows, school, academic_year, subjects, classes) -> list[dict]:
	lookup = subject_index(subjects)
	reviewed = []
	for index, row in enumerate(rows, start=1):
		if not isinstance(row, dict):
			continue
		issues = [str(issue) for issue in (row.get("uncertain_fields") or [])]
		name = str(row.get("student_name") or "").strip()
		raw_class = str(row.get("class") or "").strip()
		student_class = resolve_class(raw_class, classes)
		serial_no = str(row.get("serial_no") or "").strip()
		mobile = str(row.get("mobile") or "").strip()
		matched_subjects = []
		for subject in row.get("subjects") or []:
			match = resolve_subject(subject, lookup)
			if not match:
				issues.append(_("Unknown subject: {0}").format(subject))
			elif match["name"] not in matched_subjects:
				matched_subjects.append(match["name"])
		if not name:
			issues.append(_("Student name is missing"))
		if not student_class:
			issues.append(_("Class is missing or does not match the Class master"))
		if not serial_no:
			issues.append(_("Serial number is missing"))
		if not matched_subjects:
			issues.append(_("Subjects are missing"))
		if name and student_class and frappe.db.exists("Registered Students", {
			"school": school, "class": student_class,
			"academic_year": academic_year, "student_name": name,
		}):
			issues.append(_("Possible existing student"))
		reviewed.append({
			"row_id": index,
			"subject_review_required": row.get("subject_review_required") or 0,
			"confirm_subjects": 0,
			"class": student_class or raw_class,
			"serial_no": serial_no,
			"student_name": name,
			"mobile": mobile,
			"subjects": ", ".join(matched_subjects),
			"issues": "; ".join(issues),
		})
	return reviewed


def validated_student(row: dict, subject_lookup: dict, classes: list[str]) -> dict:
	name = str(row.get("student_name") or "").strip().upper()
	student_class = resolve_class(row.get("class"), classes)
	serial_no = str(row.get("serial_no") or "").strip()
	mobile = str(row.get("mobile") or "").strip()
	if not name:
		frappe.throw(_("Student name is required."))
	if not student_class:
		frappe.throw(_("Choose a valid class from the Class master."))
	frappe.get_doc("Class", student_class).check_permission("read")
	if not serial_no:
		frappe.throw(_("Sheet serial number is required."))
	if len(serial_no) > 140:
		frappe.throw(_("Sheet serial number is too long."))
	if row.get("subject_review_required") and not row.get("confirm_subjects"):
		frappe.throw(_("Check this student's subjects against the image and tick Subjects Checked."))
	values = row.get("subjects") or []
	if isinstance(values, str):
		values = [part.strip() for part in values.split(",") if part.strip()]
	if not isinstance(values, list):
		frappe.throw(_("Subjects must be a list or comma-separated text."))
	subjects = []
	for value in values:
		matched = resolve_subject(value, subject_lookup)
		if not matched:
			frappe.throw(_("Unknown subject: {0}").format(value))
		if matched not in subjects:
			subjects.append(matched)
	if not subjects:
		frappe.throw(_("At least one subject is required."))
	return {"student_name": name, "class": student_class, "serial_no": serial_no, "mobile": mobile, "subjects": subjects}


# def check_duplicate(student: dict, school: str, academic_year: str) -> None:
# 	if frappe.db.exists("Registered Students", {
# 		"school": school,
# 		"class": student["class"],
# 		"academic_year": academic_year,
# 		"student_name": student["student_name"],
# 		"not_registered": 0,
# 	}):
# 		frappe.throw(_("A student with this name already exists for the selected class and year."))


def _subject_key(value) -> str:
	return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _code_in_name(name: str) -> str:
	match = re.search(r"\(([A-Z0-9]{2,8})\)\s*$", name or "")
	return match.group(1) if match else ""


def _name_without_code(name: str) -> str:
	return re.sub(r"\s*\([A-Z0-9]{2,8}\)\s*$", "", name or "")
