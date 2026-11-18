import frappe


def execute():
	"""Leave one PDF per output on each registration: the newest.
	Earlier, saving an open form cleared the field, so later sends added extra PDFs."""
	from ito_customization.ito_customization.doc_events.event_outputs import (
		DOCTYPE,
		LEGACY_OUTPUT,
		delete_old_pdfs,
		pdf_field,
	)

	outputs = {LEGACY_OUTPUT} | set(frappe.get_all("ITO Event Output", pluck="output_name", distinct=True))
	meta = frappe.get_meta(DOCTYPE)
	for output in outputs:
		field = pdf_field(output)
		has_field = meta.has_field(field)
		files = frappe.get_all(
			"File",
			filters={"attached_to_doctype": DOCTYPE, "file_name": ["like", f"{output}-%.pdf"]},
			fields=["name", "attached_to_name", "file_url", "creation"],
			order_by="creation desc",
		)
		by_doc = {}
		for f in files:
			by_doc.setdefault(f.attached_to_name, []).append(f)
		for docname, doc_files in by_doc.items():
			keep = doc_files[0]  # the newest
			current = frappe.db.get_value(DOCTYPE, docname, field) if has_field else None
			if has_field and current != keep.file_url:
				frappe.db.set_value(DOCTYPE, docname, field, keep.file_url, update_modified=False)
			if has_field:
				frappe.db.set_value("File", keep.name, "attached_to_field", field, update_modified=False)
			delete_old_pdfs(docname, output, keep=keep.name)
