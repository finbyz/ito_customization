"""Excel template + upload for the Co-ordinators step of the ITO registration form.

Nothing is saved here. The upload is parsed and returned row by row so the form can
fill its table; the school can then correct anything by hand before saving the step.
"""

import base64
import csv
import io
import re
from datetime import date, datetime

import frappe
from frappe import _
from frappe.utils import validate_email_address

MAX_FILE_BYTES = 2 * 1024 * 1024
HEADERS = ["Role / Title", "Name", "Mobile", "D.O.B (DD-MM-YYYY)", "Email ID"]
FIELD_KEYS = ("name", "mobile", "dob", "email")
DATE_FORMATS = ("%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d", "%Y/%m/%d", "%d-%b-%Y", "%d %b %Y", "%d-%m-%y", "%d/%m/%y")


def _norm(value) -> str:
	return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _text(value) -> str:
	if value is None:
		return ""
	if isinstance(value, float) and value.is_integer():
		value = int(value)
	return str(value).strip()


def _parse_roles(roles) -> list[dict]:
	roles = frappe.parse_json(roles) if isinstance(roles, str) else roles
	out = [{"key": r.get("key"), "role": r.get("role")} for r in (roles or []) if r.get("key") and r.get("role")]
	if not out:
		frappe.throw(_("No roles were provided."))
	return out


@frappe.whitelist()
def download_coordinator_template(roles=None, include_email=1):
	"""Excel template with one pre-filled row per role the form asks for."""
	from openpyxl import Workbook
	from openpyxl.styles import Alignment, Font, PatternFill
	from openpyxl.worksheet.datavalidation import DataValidation

	roles = _parse_roles(roles)
	include_email = frappe.utils.cint(include_email)
	wb = Workbook()
	ws = wb.active
	ws.title = "Co-ordinators"

	head_fill = PatternFill("solid", fgColor="EA580C")
	headers = HEADERS if include_email else HEADERS[:4]
	for col, title in enumerate(headers, 1):
		cell = ws.cell(row=1, column=col, value=title)
		cell.font = Font(bold=True, color="FFFFFF")
		cell.fill = head_fill
		cell.alignment = Alignment(vertical="center")
	ws.row_dimensions[1].height = 22

	lock_fill = PatternFill("solid", fgColor="FFEDD5")
	for i, r in enumerate(roles, 2):
		ws.cell(row=i, column=1, value=r["role"]).fill = lock_fill
		ws.cell(row=i, column=1).font = Font(bold=True)
		ws.cell(row=i, column=3).number_format = "@"  # keep +91 / leading zeros
		ws.cell(row=i, column=4).number_format = "DD-MM-YYYY"
	last = len(roles) + 1

	# Example row, clearly marked, sits below the real rows and is ignored on upload.
	ex = last + 2
	ws.cell(row=ex, column=1, value="EXAMPLE (ignored) →").font = Font(italic=True, color="9A3412")
	example = ["Asha Sharma", "+91 9876543210", "15-08-1985", "asha@school.edu"]
	for col, v in enumerate(example if include_email else example[:3], 2):
		ws.cell(row=ex, column=col, value=v).font = Font(italic=True, color="9A3412")

	ws.cell(row=ex + 2, column=1, value="How to fill").font = Font(bold=True)
	for n, line in enumerate(
		[
			"1. Keep the Role / Title column as it is and fill the other columns in the same row.",
			"2. Mobile: include the country code, e.g. +91 9876543210 (10-digit numbers get your school's code).",
			("3. D.O.B: DD-MM-YYYY. Email ID: a valid address." if include_email else "3. D.O.B: DD-MM-YYYY."),
			("4. If a person has any detail filled, Mobile, D.O.B and Email are all required. Leave a row blank to skip it." if include_email else "4. If a person has any detail filled, Mobile and D.O.B are required. Leave a row blank to skip it."),
			"5. Save as .xlsx and upload on the form. You can still edit everything on screen before saving.",
		],
		ex + 3,
	):
		ws.cell(row=n, column=1, value=line)

	if include_email:
		dv = DataValidation(type="custom", formula1='ISNUMBER(SEARCH("@",E2))', allow_blank=True, showErrorMessage=True,
			errorTitle="Email", error="Enter a valid email address")
		ws.add_data_validation(dv)
		dv.add(f"E2:E{last}")

	for col, width in zip("ABCDE", (30, 28, 20, 20, 32)):
		ws.column_dimensions[col].width = width
	ws.freeze_panes = "A2"

	buf = io.BytesIO()
	wb.save(buf)
	return {
		"filename": f"ITO_Coordinators_Template_{frappe.utils.today()}.xlsx",
		"content": base64.b64encode(buf.getvalue()).decode(),
	}


def _read_rows(filedata: str, filename: str) -> list[list]:
	if not filedata:
		frappe.throw(_("No file data provided."))
	if filedata.startswith("data:") and "," in filedata[:200]:
		filedata = filedata.split(",", 1)[1]
	try:
		content = base64.b64decode(filedata)
	except Exception:
		frappe.throw(_("The uploaded file could not be read."))
	if not content:
		frappe.throw(_("The uploaded file is empty."))
	if len(content) > MAX_FILE_BYTES:
		frappe.throw(_("The file is larger than 2 MB."))

	ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
	if ext in ("csv", "txt"):
		try:
			text = content.decode("utf-8-sig")
		except UnicodeDecodeError:
			text = content.decode("latin-1")
		return [list(r) for r in csv.reader(io.StringIO(text))]
	if ext == "xlsx":
		from openpyxl import load_workbook

		try:
			wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
			rows = [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
			wb.close()
			return rows
		except Exception:
			frappe.throw(_("The Excel file could not be read. Save it as .xlsx and upload it again."))
	frappe.throw(_("Upload an Excel (.xlsx) file."))


def _parse_dob(value) -> tuple[str, str | None]:
	"""Return (YYYY-MM-DD, error)."""
	if value in (None, ""):
		return "", None
	if isinstance(value, datetime):
		value = value.date()
	if isinstance(value, date):
		return value.isoformat(), None
	s = _text(value)
	for fmt in DATE_FORMATS:
		try:
			d = datetime.strptime(s, fmt).date()
			if d.year < 1900 or d > date.today():
				return "", "D.O.B is not a valid date"
			return d.isoformat(), None
		except ValueError:
			continue
	return "", "D.O.B must be DD-MM-YYYY"


def _parse_mobile(value, country_code: str) -> tuple[str, str | None]:
	s = _text(value)
	if not s:
		return "", None
	digits = re.sub(r"\D", "", s)
	if s.startswith("+"):
		# already has a dial code: keep what the user typed
		if len(digits) < 8 or len(digits) > 15:
			return s, "Mobile number looks incorrect"
		return s, None
	if len(digits) < 6 or len(digits) > 12:
		return s, "Mobile number looks incorrect"
	if len(digits) == 11 and digits.startswith("0"):
		digits = digits[1:]
	return f"{_text(country_code) or '+91'} {digits}", None


def _match_role(label: str, roles: list[dict]) -> dict | None:
	n = _norm(label)
	if not n:
		return None
	for r in roles:
		if n in (_norm(r["role"]), _norm(r["key"])):
			return r
	for r in roles:  # forgiving match: "Principal", "Head Master", "Overall"
		rn = _norm(r["role"])
		if n in rn or rn in n:
			return r
	if "principal" in n or "headmaster" in n:
		return next((r for r in roles if "principal" in _norm(r["role"])), None)
	if "overall" in n:
		return next((r for r in roles if "overall" in _norm(r["role"])), None)
	return None


@frappe.whitelist()
def parse_coordinator_sheet(filedata=None, filename=None, roles=None, country_code="+91", include_email=1):
	"""Read an uploaded sheet and return the coordinators it contains. Saves nothing."""
	roles = _parse_roles(roles)
	include_email = frappe.utils.cint(include_email)
	rows = _read_rows(filedata, filename)

	# Find the header row (first row that looks like one) so a title row above it is harmless.
	header_idx = next(
		(i for i, r in enumerate(rows[:10]) if sum(1 for c in r if _norm(c) in ("name", "mobile", "email", "emailid")) >= 2),
		None,
	)
	if header_idx is None:
		frappe.throw(_("Headers not found. Please use the downloaded template."))

	cols = {}
	for i, h in enumerate(rows[header_idx]):
		n = _norm(h)
		if n.startswith("role"):
			cols["role"] = i
		elif n == "name" or n.endswith("name"):
			cols.setdefault("name", i)
		elif n.startswith("mobile") or n.startswith("phone"):
			cols["mobile"] = i
		elif n.startswith("dob") or n.startswith("dateofbirth"):
			cols["dob"] = i
		elif n.startswith("email"):
			cols["email"] = i
	missing = [k for k in ("role", "name", "mobile", "dob") + (("email",) if include_email else ()) if k not in cols]
	if missing:
		frappe.throw(_("Missing column(s): {0}. Please use the downloaded template.").format(", ".join(missing)))

	def cell(row, key):
		i = cols.get(key)
		if i is None:
			return None
		return row[i] if i < len(row) else None

	result, warnings, seen = [], [], set()
	for offset, row in enumerate(rows[header_idx + 1 :], header_idx + 2):
		label = _text(cell(row, "role"))
		if not label or label.upper().startswith(("EXAMPLE", "HOW TO FILL")) or re.match(r"^\d\.", label):
			continue
		has_data = any(_text(cell(row, k)) or isinstance(cell(row, k), (date, datetime)) for k in FIELD_KEYS)
		if not has_data:
			continue
		role = _match_role(label, roles)
		if not role:
			warnings.append(_("Row {0}: role “{1}” is not part of this form and was skipped.").format(offset, label))
			continue
		if role["key"] in seen:
			warnings.append(_("Row {0}: {1} appears more than once. The first entry was used.").format(offset, role["role"]))
			continue
		seen.add(role["key"])

		errors = []
		name = _text(cell(row, "name"))
		mobile, e = _parse_mobile(cell(row, "mobile"), country_code)
		e and errors.append(e)
		dob, e = _parse_dob(cell(row, "dob"))
		e and errors.append(e)
		email = _text(cell(row, "email")).lower()
		if include_email and email and not validate_email_address(email):
			errors.append("Email is not valid")
		for field, val in (("Name", name), ("Mobile", mobile), ("D.O.B", dob or _text(cell(row, "dob"))), ("Email", email)):
			if field == "Email" and not include_email:
				continue
			if not val:
				errors.append(f"{field} is missing")

		result.append(
			{"key": role["key"], "role": role["role"], "name": name, "mobile": mobile, "dob": dob, "email": email,
			 "row": offset, "errors": list(dict.fromkeys(errors))}
		)

	if not result:
		frappe.throw(_("No co-ordinator details were found in the file."))
	return {"rows": result, "warnings": warnings}
