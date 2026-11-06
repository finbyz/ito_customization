"""Read ruled, typed student-list PDFs (Excel exports) exactly from their text layer.

Only used when the PDF page has a real text layer and a recognisable ruled table. Anything
unrecognised returns None so the caller falls back to the AI agent. No guessing happens here.
"""

import re

MARK_VALUES = {"yes", "y", "true", "1", "selected", "participating", "✓", "✔", "√", "✅"}


def read_typed_pdf_page(page, abbr_names: dict, resolve_class) -> list[dict] | None:
	"""abbr_names maps an upper-case master abbreviation to itself (or its master abbr)."""
	if not hasattr(page, "find_tables"):
		return None
	words = page.get_text("words")
	if len(words) < 30:
		return None  # scanned or photographed page: no text layer
	try:
		tables = page.find_tables().tables
	except Exception:
		return None
	students = []
	for table in tables:
		parsed = parse_table(table, words, abbr_names, resolve_class)
		if parsed:
			students.extend(parsed)
	return students or None


def cell_text(bbox, words) -> str:
	if not bbox:
		return ""
	found = [
		word for word in words
		if bbox[0] <= (word[0] + word[2]) / 2 <= bbox[2] and bbox[1] <= (word[1] + word[3]) / 2 <= bbox[3]
	]
	found.sort(key=lambda word: (round((word[1] + word[3]) / 2 / 3), word[0]))
	return " ".join(word[4] for word in found).strip()


def parse_table(table, words, abbr_names, resolve_class):
	rows = [[cell_text(cell, words) for cell in row.cells] for row in table.rows]
	bboxes = [row.cells for row in table.rows]
	if len(rows) < 3:
		return None
	width = max(len(row) for row in rows)

	def abbr_in(text):
		match = re.match(r"\s*([A-Za-z]{2,10})\b", text or "")
		return abbr_names.get(match.group(1).upper()) if match else None

	students = []
	layout = None
	for index, row in enumerate(rows):
		found = {col: abbr_in(text) for col, text in enumerate(row) if abbr_in(text)}
		if len(found) >= 3:  # an abbreviation row starts (or repeats) a header block
			layout = build_layout(rows, bboxes, index, found, words)
			continue
		if not layout:
			continue
		name = clean(row[layout["name"]]) if layout["name"] < len(row) else ""
		if not name or not re.search(r"[A-Za-z]", name) or looks_like_header(name):
			continue
		marked, unclear = [], []
		for col, abbr in layout["subjects"]:
			value = clean(row[col]).casefold() if col < len(row) else ""
			if value in MARK_VALUES:
				if abbr not in marked:
					marked.append(abbr)
			elif value and value not in {"no", "n", "-", "–", "nil", "0"}:
				unclear.append(abbr)
		raw_class = clean(row[layout["class"]]) if layout["class"] is not None and layout["class"] < len(row) else ""
		serial = clean(row[layout["serial"]]) if layout["serial"] is not None and layout["serial"] < len(row) else ""
		mobile = re.sub(r"\D", "", row[layout["mobile"]]) if layout["mobile"] is not None and layout["mobile"] < len(row) else ""
		issues = []
		if not resolve_class(raw_class):
			issues.append("class")
		students.append({
			"class": raw_class, "serial_no": serial, "student_name": name, "mobile": mobile,
			"marked_subjects": marked, "unclear_subjects": unclear, "uncertain_fields": issues,
		})
	return students or None


def build_layout(rows, bboxes, abbr_row, found, words):
	"""Find name/class/mobile/serial columns and the participation subject columns."""
	header_rows = rows[max(0, abbr_row - 3):abbr_row + 1]

	def find(pattern, exclude=None):
		for row in header_rows:
			for col, text in enumerate(row):
				if re.search(pattern, text, re.I) and not (exclude and re.search(exclude, text, re.I)):
					return col
		return None

	name = find(r"student", r"parent|guardian")
	if name is None:
		name = find(r"\bname\b", r"parent|guardian")
	cols = sorted(found)
	groups, current, seen = [], [], set()
	for col in cols:
		if found[col] in seen:
			groups.append(current)
			current, seen = [], set()
		current.append(col)
		seen.add(found[col])
	groups.append(current)
	chosen = None
	for group in groups:
		span = (min(group), max(group))
		heading = " ".join(
			text for row in header_rows for col, text in enumerate(row) if span[0] <= col <= span[1]
		)
		heading += " " + heading_above(bboxes[abbr_row], span, words)
		if re.search(r"purchas|workbook|printed|book|payment|fee", heading, re.I):
			continue
		chosen = group
		break
	if chosen is None or name is None:
		return {"name": 0, "class": None, "serial": None, "mobile": None, "subjects": []}
	return {
		"name": name,
		"class": find(r"^\s*class\b|^\s*std\b|^\s*grade\b"),
		"serial": find(r"^\s*(sr|s)\.?\s*no|serial|^\s*no\.?\s*$"),
		"mobile": find(r"mobile|phone|contact", r"parent|guardian"),
		"subjects": [(col, found[col]) for col in chosen],
	}


def heading_above(cells, span, words):
	"""Merged headings such as 'Participating' can sit outside the cell grid; read the words above the group."""
	boxes = [cells[col] for col in range(span[0], span[1] + 1) if col < len(cells) and cells[col]]
	if not boxes:
		return ""
	left, right = min(box[0] for box in boxes), max(box[2] for box in boxes)
	top = min(box[1] for box in boxes)
	return " ".join(
		word[4] for word in words
		if left <= (word[0] + word[2]) / 2 <= right and top - 45 <= word[3] <= top + 1
	)


def looks_like_header(text) -> bool:
	return bool(re.fullmatch(r"(student'?s?\s*name|name|rs\.?.*)", text.strip(), re.I))


def clean(text) -> str:
	return re.sub(r"\s+", " ", text or "").strip()