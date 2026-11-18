import frappe


def execute():
	"""Registrations whose E-Pass was already emailed get the E-Pass PDF saved on the record.
	Runs as a background job, as making PDFs is slow."""
	frappe.enqueue(
		"ito_customization.patches.backfill_epass_pdf.run",
		queue="long",
		timeout=3000,
		enqueue_after_commit=True,
	)


def run():
	from ito_customization.ito_customization.doc_events.event_outputs import (
		DOCTYPE,
		LEGACY_OUTPUT,
		generate_pdf_for,
		output_sent,
		event_for,
	)

	for name in frappe.get_all(DOCTYPE, filters={"e_pass_pdf": ["is", "not set"]}, pluck="name"):
		doc = frappe.get_doc(DOCTYPE, name)
		event = event_for(doc)
		if not output_sent(doc, LEGACY_OUTPUT, event):
			continue
		# A savepoint per record: a failure undoes only that record, and the job commits the rest at the end.
		frappe.db.savepoint("epass_pdf")
		try:
			generate_pdf_for(doc, LEGACY_OUTPUT, event)
		except Exception:
			frappe.db.rollback(save_point="epass_pdf")
			frappe.log_error(title=f"E-Pass PDF backfill failed for {name}")
		else:
			frappe.db.release_savepoint("epass_pdf")
