function set_case_format_field_options(frm, row) {
	if (!row.document_type) return;

	frappe.model.with_doctype(row.document_type, () => {
		const fields = frappe.get_meta(row.document_type).fields
			.filter((field) => ["Data", "Small Text"].includes(field.fieldtype))
			.map((field) => field.fieldname);

		frm.fields_dict.exclusions.grid.update_docfield_property(
			"field_name",
			"options",
			[""].concat(fields).join("\n")
		);
		frm.refresh_field("exclusions");
	});
}

frappe.ui.form.on("Case Format Exclusion", {
	document_type(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		frappe.model.set_value(cdt, cdn, "field_name", "");
		set_case_format_field_options(frm, row);
	},
});

frappe.ui.form.on("Case Format Settings", {
	refresh(frm) {
		(frm.doc.exclusions || []).forEach((row) => set_case_format_field_options(frm, row));
	},
});
