# Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from ito_customization.ito_customization import bulk_registration_import as importer

RUNNING = ("Queued", "In Progress")


class ITOBulkImport(Document):
	def validate(self):
		if self.flags.from_import_job or self.is_new():
			return
		if self.has_value_changed("import_file") or self.has_value_changed("exam_detail"):
			if self.status in RUNNING:
				frappe.throw(_("The import is running. Wait for it to finish before changing the file."))
			if any(r.status == "Imported" for r in self.rows):
				frappe.throw(
					_("Schools were already imported from this file. Start a new import for another file.")
				)
			self.status = "Draft"
			self.summary = None
			self.file_notes = None
			self.set("rows", [])

	def _not_running(self):
		self.check_permission("write")
		if self.status in RUNNING:
			frappe.throw(_("The import is already running."))

	def _recheck(self):
		importer.validate_import(self)
		self.flags.from_import_job = True
		self.save()

	@frappe.whitelist()
	def validate_file(self):
		self._not_running()
		self._recheck()

	@frappe.whitelist()
	def save_corrections(self, row_no, corrections):
		"""Store the values corrected for one sheet row, then check the file again."""
		self._not_running()
		if isinstance(corrections, str):
			corrections = json.loads(corrections or "{}")
		row = next((r for r in self.rows if cint(r.row_no) == cint(row_no)), None)
		if not row:
			frappe.throw(_("Row {0} is not in this import.").format(row_no))
		if row.status == "Imported":
			frappe.throw(_("Row {0} is already imported.").format(row_no))
		current = json.loads(row.corrections or "{}")
		current.update(corrections or {})
		row.corrections = json.dumps(current)
		self._recheck()

	@frappe.whitelist()
	def apply_all_fixes(self):
		"""Apply every suggested fix of the rows not imported yet, then check the file again."""
		self._not_running()
		applied = 0
		for row in self.rows:
			if row.status == "Imported":
				continue
			issues = json.loads(row.details or "{}").get("issues", [])
			fixes = {i["path"]: i["fix"] for i in issues if i.get("fix") and i.get("path")}
			if fixes:
				current = json.loads(row.corrections or "{}")
				current.update(fixes)
				row.corrections = json.dumps(current)
				applied += len(fixes)
		if not applied:
			frappe.throw(_("There are no suggested fixes to apply."))
		self._recheck()
		return applied

	@frappe.whitelist()
	def create_subject(self, subject_name, abbr, add_to_schedule=1):
		self._not_running()
		importer.create_subject(subject_name, abbr, self.exam_detail if cint(add_to_schedule) else None)
		self._recheck()

	@frappe.whitelist()
	def add_subject_to_schedule(self, subject):
		self._not_running()
		importer.add_to_schedule(self.exam_detail, subject)
		self._recheck()

	@frappe.whitelist()
	def start_import(self):
		self._not_running()
		if self.status == "Draft":
			frappe.throw(_("Validate the file first."))
		self._enqueue("run_import")

	@frappe.whitelist()
	def start_revert(self):
		self._not_running()
		if not any(r.status == "Imported" and r.undo_data for r in self.rows):
			frappe.throw(_("There is nothing to revert."))
		self._enqueue("run_revert")

	def _enqueue(self, method):
		self.db_set("status", "Queued")
		frappe.enqueue(
			f"ito_customization.ito_customization.bulk_registration_import.{method}",
			queue="long",
			timeout=3600,
			import_name=self.name,
			job_id=f"ito_bulk_import::{self.name}",
			deduplicate=True,
			enqueue_after_commit=True,
		)
