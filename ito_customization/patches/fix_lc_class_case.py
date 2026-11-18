import frappe

CANONICAL = {"nursery": "Nursery", "junior kg": "Junior KG", "senior kg": "Senior KG"}


def execute():
	"""Exam Summary rows saved as "Junior Kg" / "Senior Kg" (re-cased by Case Format Settings)
	get the real class name back."""
	rows = frappe.get_all("Exam Summary CT", filters={"class": ["is", "set"]}, fields=["name", "class"])
	for row in rows:
		fixed = CANONICAL.get(row["class"].strip().lower())
		if fixed and fixed != row["class"]:
			frappe.db.set_value("Exam Summary CT", row.name, "class", fixed, update_modified=False)
