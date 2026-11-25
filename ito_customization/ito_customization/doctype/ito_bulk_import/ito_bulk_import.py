# Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from ito_customization.ito_customization import bulk_registration_import as importer

RUNNING = ("Queued", "In Progress")


class ITOBulkImport(Document):
	def validate(self):
		if self.flags.from_import_job or self.is_new():
			return
		if self.has_value_changed("import_file") or self.has_value_changed("exam_detail"):
			if self.status in RUNNING:
				frappe.throw(_("The import is running. Wait for it to finish before changing the file."))
			self.status = "Draft"
			self.summary = None
			self.set("rows", [])

	@frappe.whitelist()
	def validate_file(self):
		self.check_permission("write")
		if self.status in RUNNING:
			frappe.throw(_("The import is already running."))
		importer.validate_import(self)
		self.flags.from_import_job = True
		self.save()

	@frappe.whitelist()
	def start_import(self):
		self.check_permission("write")
		if self.status in RUNNING:
			frappe.throw(_("The import is already running."))
		if self.status == "Draft":
			frappe.throw(_("Validate the file first."))
		self.db_set("status", "Queued")
		frappe.enqueue(
			"ito_customization.ito_customization.bulk_registration_import.run_import",
			queue="long",
			timeout=3600,
			import_name=self.name,
			job_id=f"ito_bulk_import::{self.name}",
			deduplicate=True,
			enqueue_after_commit=True,
		)
