import frappe

# Shown when neither the Customer nor the caller supplies a year.
DEFAULT_ACADEMIC_YEAR = "2026-27"


def _variants(year: str):
	"""The same year in both spellings, legacy 'AY-2026/27' first, then '2026-27'.

	Existing records (Yearly Exam Date, Registered Students ...) are stored under the legacy
	name, so it wins whenever both masters exist.
	"""
	plain = year[3:].replace("/", "-") if year.startswith("AY-") else year
	yield "AY-" + plain.replace("-", "/")
	yield plain


def normalize_academic_year(raw_year=None, default=DEFAULT_ACADEMIC_YEAR):
	"""Return the School Academic Year name for `raw_year`, whichever format it is written in.

	Both '2026-27' and 'AY-2026/27' are accepted. If neither exists as a School Academic Year,
	the value is returned as given (no 'AY-' prefix is forced on it).
	"""
	year = (raw_year or default or "").strip()
	if not year:
		return year
	for candidate in _variants(year):
		if frappe.db.exists("School Academic Year", candidate):
			return candidate
	return year
