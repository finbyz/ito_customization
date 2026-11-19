import re

import frappe

# Shown when neither the Customer nor the caller supplies a year.
DEFAULT_ACADEMIC_YEAR = "2026-27"


def year_key(value):
	"""The starting year of an academic year written in any format, or None.

	'AY-2026/27', '2026-27', '2026-2027', 'AY 2026-27', '2026/27', '2026' and '26-27' all give 2026.
	"""
	match = re.search(r"\d{4}|\d{2}", str(value or ""))
	if not match:
		return None
	start = int(match.group())
	return start + 2000 if start < 100 else start


def _same_year_names(year):
	"""School Academic Year names that mean the same year as `year`, legacy 'AY-' names first.

	Existing records (Yearly Exam Date, Registered Students ...) are mostly stored under the legacy
	'AY-2026/27' name, so it wins whenever several masters exist for the same year.
	"""
	key = year_key(year)
	if key is None:
		return []
	names = [
		name
		for name in frappe.get_all("School Academic Year", pluck="name", order_by="name asc")
		if year_key(name) == key
	]
	return sorted(names, key=lambda name: (not name.upper().startswith("AY"), name))


def normalize_academic_year(raw_year=None, default=DEFAULT_ACADEMIC_YEAR):
	"""Return the School Academic Year name for `raw_year`, whichever format it is written in.

	Any spelling of the year is accepted ('2026-27', 'AY-2026/27', '2026-2027', 'AY 2026-27' ...).
	The School Academic Year for the same starting year is used, the legacy 'AY-' name first.
	If none exists, the value is returned as given.
	"""
	year = (raw_year or default or "").strip()
	if not year:
		return year
	matches = _same_year_names(year)
	return matches[0] if matches else year


def academic_year_variants(raw_year=None, default=DEFAULT_ACADEMIC_YEAR):
	"""Every spelling of the same academic year, for filters: records may be saved under any of them."""
	year = (raw_year or default or "").strip()
	if not year:
		return [year]
	variants = _same_year_names(year)
	for extra in (year, normalize_academic_year(year, default=None)):
		if extra and extra not in variants:
			variants.append(extra)
	return variants


def academic_year_filter(raw_year=None, default=DEFAULT_ACADEMIC_YEAR):
	"""A Frappe filter value that matches the year in any of its spellings."""
	return ["in", academic_year_variants(raw_year, default=default)]
