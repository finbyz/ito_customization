"""Extract and import Registered Students from a private image or PDF."""

import base64
import io
import json
import re
import time
from collections import Counter
from pathlib import Path

import frappe
from frappe import _

MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_PAGES = 10
MAX_STUDENTS = 200
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
EXTRACTION_RESULT_TTL = 60 * 60
EXTRACTION_TIMEOUT = 1800
AGENT_ATTEMPTS = 2
# Provider errors that a retry cannot fix: a wrong model name, endpoint or key.
CONFIG_ERRORS = {"BadRequestError", "AuthenticationError", "NotFoundError", "PermissionDeniedError"}
MIN_IMAGE_SIDE = 2800
HEADER_NAMES = {
	"name", "names", "student", "students", "student name", "students name", "name of the student",
	"name of student", "student's name", "parent's name", "parents name", "sr no", "serial no", "class",
}


@frappe.whitelist(methods=["POST"])
def extract_students(file_name: str, school: str, academic_year: str) -> dict:
	"""Start a background extraction and return its job id; extraction never writes student records."""
	check_import_access(school, academic_year)
	get_private_file(file_name)
	job_id = frappe.generate_hash(length=32)
	queue_job_id = f"registered-student-extraction::{job_id}"
	frappe.cache().set_value(
		extraction_cache_key(job_id),
		{"status": "queued", "current_page": 0, "queue_job_id": queue_job_id},
		expires_in_sec=EXTRACTION_RESULT_TTL,
	)
	frappe.enqueue(
		"ito_customization.ito_customization.doctype.registered_students.student_import.run_extraction_job",
		queue="long",
		timeout=EXTRACTION_TIMEOUT,
		job_id=queue_job_id,
		extraction_id=job_id,
		file_name=file_name,
		school=school,
		academic_year=academic_year,
		user=frappe.session.user,
	)
	return {"job_id": job_id}


@frappe.whitelist()
def get_student_extraction_status(job_id: str) -> dict:
	"""Progress or result of an extraction started by the current user."""
	state = frappe.cache().get_value(extraction_cache_key(job_id))
	if not state:
		return {"status": "failed", "message": _("The extraction expired or was not found. Please extract again.")}
	return state


def extraction_cache_key(job_id: str) -> str:
	return f"registered_students_extraction::{frappe.session.user}::{job_id}"


def run_extraction_job(extraction_id, file_name, school, academic_year, user=None, **kwargs):
	"""Background worker: read the file page by page and store the reviewable rows for the dialog."""
	key = extraction_cache_key(extraction_id)
	cache = frappe.cache()
	state = cache.get_value(key) or {}

	def save(**values):
		state.update(values)
		cache.set_value(key, dict(state), expires_in_sec=EXTRACTION_RESULT_TTL)

	try:
		check_import_access(school, academic_year)
		file_doc = get_private_file(file_name)
		save(status="processing", current_page=1)
		outcome = run_extraction(
			file_doc.get_full_path(), Path(file_doc.file_name or "").suffix.lower(), school, academic_year,
			on_page=lambda page: save(status="processing", current_page=page),
		)
		ensure_db_connection()
		subjects, classes = get_subject_master(), get_class_master()
		rows = review_rows(outcome["students"], school, academic_year, subjects, classes)
		save(
			status="completed",
			result={
				"rows": rows,
				"warnings": outcome["warnings"] + page_total_warnings(outcome),
				"file_name": file_doc.name,
				"file_url": file_doc.file_url,
				"usage": outcome["usage"],
			},
		)
	except Exception as exc:
		message = extraction_error_message(exc)
		if not isinstance(exc, frappe.ValidationError):
			frappe.log_error(frappe.get_traceback(), "Registered Students AI Extraction")
		save(status="failed", message=message)


def ensure_db_connection() -> None:
	"""Slow AI calls leave the DB connection idle long enough for MariaDB to drop it; reconnect."""
	try:
		frappe.db.sql("select 1")
	except Exception:
		frappe.db.connect()


def extraction_error_message(exc: Exception) -> str:
	if is_provider_budget_error(exc):
		return _(
			"The AI provider cannot process this student list because its available credits or "
			"in-flight request budget are exhausted. Wait for active requests to finish and try again, "
			"or add credits to the configured provider account. No students were created."
		)
	if isinstance(exc, frappe.ValidationError):
		return str(exc)
	return _("The AI could not read this file ({0}). No students were created. Please try again.").format(
		type(exc).__name__
	)


def run_extraction(path, extension, school, academic_year, llm=None, on_page=None) -> dict:
	"""Read every page and return raw student rows, page warnings and token usage.

	Shared by the background job and the offline benchmark so both exercise the same logic.
	`llm` overrides the model for this run; otherwise ITO AI Settings > Student List LLM Override,
	then the model saved on the Student List agent, is used."""
	from langchain_core.callbacks import UsageMetadataCallbackHandler

	from finbyzai.ai.agent.agent_service import AgentService

	subjects = get_subject_master()
	classes = get_class_master()
	agent_name = frappe.db.get_single_value("ITO AI Settings", "student_list_agent")
	if not agent_name or not frappe.db.exists("AI Agent", agent_name):
		frappe.throw(_("Set Student List Agent in ITO AI Settings before importing."))
	service = AgentService(agent_name)
	llm = llm or frappe.db.get_single_value("ITO AI Settings", "student_list_llm")
	if llm:
		llm_row = frappe.db.get_value("LLM", llm, ["enabled", "supports_vision"], as_dict=True)
		if not llm_row or not llm_row.enabled or not llm_row.supports_vision:
			frappe.throw(_("LLM {0} must exist, be enabled and support vision to read student lists.").format(llm))
		service.agent_doc.llm = llm

	usage = UsageMetadataCallbackHandler()
	student_rows, warnings, page_info = [], [], []
	previous_class = ""
	for page_no, (image, grid_image, typed_students) in enumerate(file_pages(path, extension, subjects, classes), start=1):
		if on_page:
			on_page(page_no)
		if typed_students:
			# Typed PDF with a readable table: names, classes and ticks come straight from the text layer.
			data = {"students": typed_students, "printed_totals": []}
			resolve_typed_subjects(data, subjects)
		else:
			variables = dict(
				school=school,
				academic_year=academic_year,
				valid_classes=json.dumps(classes),
				previous_class=previous_class,
				valid_subjects=json.dumps(subjects),
			)
			# Scans are often sideways or upside down, and a model reading a rotated page invents names.
			angle = detect_rotation(service, usage, image)
			if angle:
				image, grid_image = rotate_image(image, angle, "JPEG"), rotate_image(grid_image, angle, "PNG")
			data = invoke_page(service, usage, page_no, warnings, **page_images(image), **variables)
			verify_subject_grid(data, grid_image, subjects)
		for row in data["students"]:
			row["page"] = page_no
		student_rows.extend(data["students"])
		page_info.append({"page": page_no, "students": len(data["students"]), "printed_totals": data.get("printed_totals") or []})
		for row in reversed(data["students"]):
			found_class = resolve_class(row.get("class"), classes)
			if found_class:
				previous_class = found_class
				break
		if len(student_rows) > MAX_STUDENTS:
			frappe.throw(_("The file contains more than {0} students.").format(MAX_STUDENTS))
	return {
		"students": student_rows,
		"warnings": warnings,
		"pages": page_info,
		"usage": summarise_usage(usage.usage_metadata),
	}


def page_images(image: bytes) -> dict:
	return {"student_image": base64.b64encode(image).decode("ascii")}


def detect_rotation(service, usage, image: bytes) -> int:
	"""Clockwise degrees that make the page upright. Shows the model the page at all four turns on
	one small sheet and asks which one reads upright; a cheap, much easier question than reading
	rotated text. Any failure returns 0 so a bad answer can never block an extraction."""
	from langchain_core.messages import HumanMessage
	from PIL import Image, ImageDraw

	angles = (0, 90, 180, 270)
	try:
		with Image.open(io.BytesIO(image)) as source:
			page = source.convert("RGB")
		page.thumbnail((700, 700))
		cell = 720
		sheet = Image.new("RGB", (cell * 2, cell * 2), "white")
		draw = ImageDraw.Draw(sheet)
		for index, angle in enumerate(angles):
			tile = page.rotate(-angle, expand=True)
			left, top = (index % 2) * cell, (index // 2) * cell
			sheet.paste(tile, (left + 10, top + 10))
			draw.rectangle((left, top, left + 44, top + 44), fill="black")
			draw.text((left + 14, top + 12), str(index + 1), fill="white", font_size=26)
		buffer = io.BytesIO()
		sheet.save(buffer, format="JPEG", quality=80)
		message = HumanMessage(content=[
			{"type": "text", "text": (
				"These four tiles show the same document page turned four different ways and numbered 1 to 4. "
				"Exactly one tile shows the page upright, with printed text reading normally left to right. "
				"Reply with only that tile's number."
			)},
			{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")}},
		])
		reply = service.get_llm().invoke([message], config={"callbacks": [usage]}).content
		text = reply if isinstance(reply, str) else json.dumps(reply)
		match = re.search(r"[1-4]", text)
		return angles[int(match.group()) - 1] if match else 0
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Registered Students Page Orientation")
		return 0


def rotate_image(data: bytes, clockwise_degrees: int, image_format: str) -> bytes:
	from PIL import Image

	with Image.open(io.BytesIO(data)) as source:
		buffer = io.BytesIO()
		source.rotate(-clockwise_degrees, expand=True).save(buffer, format=image_format, quality=90)
		return buffer.getvalue()


def invoke_page(service, usage, page_no, warnings, **variables) -> dict:
	"""Call the agent for one page; retry empty, malformed, cut-off or failed responses."""
	problem = None
	for attempt in range(1, AGENT_ATTEMPTS + 1):
		try:
			response = service.invoke(callbacks=[usage], **variables)
		except Exception as exc:
			if is_provider_budget_error(exc):
				raise
			problem = _("the AI request failed ({0}: {1})").format(type(exc).__name__, provider_message(exc))
			frappe.log_error(frappe.get_traceback(), "Registered Students AI Extraction")
			if type(exc).__name__ in CONFIG_ERRORS:
				# Retrying a rejected model name, endpoint or key only wastes time; check the LLM settings.
				frappe.throw(_("Page {0} could not be read: {1}. Check the LLM and LLM Provider (model name, API base, API key) used by the Student List agent.").format(page_no, problem))
			time.sleep(2 * attempt)
			continue
		data = response.model_dump() if hasattr(response, "model_dump") else response
		if not isinstance(data, dict) or not isinstance(data.get("students"), list):
			problem = _("the AI returned an empty or malformed response")
			continue
		students = []
		for item in data["students"]:
			if isinstance(item, dict):
				students.append(item)
			else:
				warnings.append(_("Page {0}: a malformed row in the AI response was skipped.").format(page_no))
		data["students"] = students
		counted = data.get("rows_counted")
		if isinstance(counted, int) and counted > len(students) and attempt < AGENT_ATTEMPTS:
			problem = _("the AI counted {0} rows but returned {1}; the response may have been cut off").format(
				counted, len(students))
			continue
		if isinstance(counted, int) and counted != len(students):
			warnings.append(_("Page {0}: the AI counted {1} student rows but returned {2}. Compare with the original file.").format(
				page_no, counted, len(students)))
		return data
	frappe.throw(_("Page {0} could not be read: {1}. No students were created.").format(page_no, problem))


def provider_message(exc: Exception) -> str:
	"""First line of the provider's explanation, without anything that looks like an API key."""
	line = (str(exc).strip().splitlines() or [""])[0]
	line = re.sub(r"\b(sk|key|AIza)[-_A-Za-z0-9]{8,}", "[hidden]", line)
	return line[:300]


def summarise_usage(usage_metadata: dict | None) -> dict:
	totals = Counter()
	for values in (usage_metadata or {}).values():
		for field in ("input_tokens", "output_tokens", "total_tokens"):
			totals[field] += values.get(field) or 0
	return dict(totals)


def page_total_warnings(outcome: dict) -> list[str]:
	"""Compare the TOTAL row printed under the subject columns with the subjects we assigned."""
	subjects = get_subject_master()
	lookup = subject_index(subjects)
	warnings = []
	for page in outcome["pages"]:
		counted = Counter()
		for row in outcome["students"]:
			if row.get("page") == page["page"]:
				counted.update(set(row.get("subjects") or []))
		for item in page["printed_totals"]:
			matched = resolve_subject(item.get("abbr"), lookup) if isinstance(item, dict) else None
			if matched and counted[matched["abbr"]] != item.get("total"):
				warnings.append(_("Page {0}: the sheet's TOTAL for {1} is {2} but {3} students are marked. Check the subject ticks.").format(
					page["page"], matched["abbr"], item.get("total"), counted[matched["abbr"]]))
	return warnings


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
			doc = frappe.get_doc({
				"doctype": "Registered Students",
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
		"in_flight_budget_exhausted", "payment required", "requires more credits",
		"can only afford", "insufficient credits", "error code: 402",
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


def file_pages(path, extension, subjects, classes):
	"""Yield (page image, grid image, typed students). typed students is None unless the page
	is a typed PDF table that could be read exactly from its text layer."""
	from PIL import Image, ImageOps

	if extension in IMAGE_EXTENSIONS:
		with Image.open(path) as source:
			image = ImageOps.exif_transpose(source)
			yield jpeg_bytes(image), png_bytes(image), None
		return
	if extension != ".pdf":
		frappe.throw(_("Upload a PDF, JPG, PNG, or WebP file."))

	import fitz

	from ito_customization.ito_customization.doctype.registered_students.student_pdf_table import (
		read_typed_pdf_page,
	)

	abbr_names = {s["abbr"].upper(): s["abbr"] for s in subjects if s["abbr"]}
	with fitz.open(path) as pdf:
		if not 0 < len(pdf) <= MAX_PAGES:
			frappe.throw(_("PDFs must contain between 1 and {0} pages.").format(MAX_PAGES))
		for page in pdf:
			typed = None
			try:
				typed = read_typed_pdf_page(page, abbr_names, lambda value: resolve_class(value, classes))
			except Exception:
				frappe.log_error(frappe.get_traceback(), "Registered Students PDF Table Read")
			if typed:
				yield b"", b"", typed
				continue
			pixmap = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
			image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
			grid_pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
			grid_image = Image.frombytes(
				"RGB", (grid_pixmap.width, grid_pixmap.height), grid_pixmap.samples
			)
			yield jpeg_bytes(image), png_bytes(grid_image), None


def jpeg_bytes(image) -> bytes:
	from PIL import Image

	image = image.convert("RGB")
	longest = max(image.size)
	if longest < MIN_IMAGE_SIDE:
		# Screenshots of dense tables carry 8px print; enlarging helps the vision encoder resolve it.
		factor = MIN_IMAGE_SIDE / longest
		image = image.resize((round(image.width * factor), round(image.height * factor)), Image.LANCZOS)
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


STANDARD_COLUMNS = ["ISO", "IMO", "EIO", "GKIO", "NSSO", "ICO", "IDO", "NESO", "NHO", "NLRO"]


def resolve_typed_subjects(data, subjects) -> None:
	"""Rows read from a PDF text layer already carry exact marked subjects."""
	lookup = subject_index(subjects)
	for row in data["students"]:
		row["subjects"] = provisional_subjects(row, lookup)
		row["subject_review_required"] = 0
		for value in row.get("unclear_subjects") or []:
			row.setdefault("uncertain_fields", []).append(_("Unclear subject cell: {0}").format(value))


def verify_subject_grid(data, grid_image, subjects) -> None:
	"""Handwritten ITO sheets: measure the ruled grid. Any other layout: use the AI's per-student reading."""
	from ito_customization.ito_customization.doctype.registered_students.student_subject_grid import (
		read_grid,
	)

	lookup = subject_index(subjects)
	students = data["students"]
	declared = []
	for value in data.get("subject_columns") or []:
		# Count the columns as printed, including ones missing from the subject master.
		matched = resolve_subject(value, lookup)
		code = matched["abbr"] if matched and matched["abbr"] else str(value or "").strip().upper()
		if code and code not in declared:
			declared.append(code)
	# The grid reader only fits the printed 10-column ITO template. Typed sheets with 13 or 26
	# columns would be misread by it, so it runs only when the AI saw exactly those 10 columns.
	template = (
		set(declared) == set(STANDARD_COLUMNS)
		and len(declared) == len(STANDARD_COLUMNS)
		and all(resolve_subject(code, lookup) for code in STANDARD_COLUMNS)
	)
	result = None
	if template and students:
		try:
			result = read_grid(grid_image, list(STANDARD_COLUMNS), len(students))
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
		row["subject_review_required"] = 0
		if grid_rows is not None:
			grid = grid_rows[index]
			grid_subjects = [resolve_subject(code, lookup)["abbr"] for code in grid.subjects]
			row["subjects"] = grid_subjects
			issues = [issue for issue in issues if "subject" not in str(issue).casefold()]
			if set(grid_subjects) != set(model_subjects):
				issues.append(_("Subject scan and AI reading differ; check against the image"))
			if grid.needs_review:
				issues.append(_("Cancelled or borderline tick; check subjects against the image"))
		else:
			row["subjects"] = model_subjects
			if template:
				if result and not result.rows:
					issues.append(_("Sheet has {0} ticked rows but {1} students were read; a student row may be missing").format(
						result.tick_rows, len(students)))
				else:
					issues.append(_("Subject grid could not be verified; check subjects against the image"))
			for value in row.get("unclear_subjects") or []:
				issues.append(_("Unclear subject cell: {0}").format(value))
		row["uncertain_fields"] = issues


def provisional_subjects(row: dict, lookup: dict) -> list[str]:
	selected = []
	codes = list(row.get("marked_subjects") or [])
	# Agents saved with the older schema returned one status per cell instead of a list.
	codes += [
		cell.get("abbr") for cell in row.get("subject_cells") or []
		if isinstance(cell, dict) and cell.get("status") == "marked"
	]
	for code in codes:
		matched = resolve_subject(code, lookup)
		if matched and matched["abbr"] not in selected:
			selected.append(matched["abbr"])
	return selected


def review_rows(rows, school, academic_year, subjects, classes) -> list[dict]:
	lookup = subject_index(subjects)
	reviewed = []
	seen_names, serial_names = {}, {}
	for index, row in enumerate(rows, start=1):
		if not isinstance(row, dict):
			continue
		issues = [str(issue) for issue in (row.get("uncertain_fields") or [])]
		# A name that wrapped onto a second line can carry a line break; keep the words, drop the break.
		name = re.sub(r"\s+", " ", str(row.get("student_name") or "")).strip()
		raw_class = str(row.get("class") or "").strip()
		student_class = resolve_class(raw_class, classes)
		serial_no = str(row.get("serial_no") or "").strip()
		mobile = str(row.get("mobile") or "").strip()
		matched_subjects = []
		for subject in row.get("subjects") or []:
			match = resolve_subject(subject, lookup)
			if not match:
				issues.append(_("Unknown subject: {0}").format(subject))
				continue
			label = match["abbr"] if match["abbr"] and resolve_subject(match["abbr"], lookup) == match else match["name"]
			if label not in matched_subjects:
				matched_subjects.append(label)
		if not name:
			issues.append(_("Student name is missing"))
		elif re.search(r"\d", name) or name.casefold().strip(" .:") in HEADER_NAMES:
			issues.append(_("Name looks like a number or a column header; check it against the file"))
		if name and student_class:
			key = (student_class, name.casefold())
			if key in seen_names:
				issues.append(_("Possible duplicate of row {0} in this file").format(seen_names[key]))
			else:
				seen_names[key] = index
		if serial_no and student_class:
			previous = serial_names.setdefault((student_class, serial_no), name.casefold())
			if previous != name.casefold():
				issues.append(_("Serial {0} is used by another student in this class; a row may be merged or split").format(serial_no))
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