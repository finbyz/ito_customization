// Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors

const ITO_IMPORT_COLORS = {
	Draft: "gray", Validated: "blue", Queued: "orange", "In Progress": "orange",
	Completed: "green", "Partially Completed": "yellow", Failed: "red",
};

frappe.ui.form.on("ITO Bulk Import", {
	setup(frm) {
		frm.set_query("exam_detail", () => ({ filters: { docstatus: 1, is_little_champ: 0 } }));
	},

	refresh(frm) {
		frm.page.set_indicator(__(frm.doc.status || "Draft"), ITO_IMPORT_COLORS[frm.doc.status] || "gray");

		if (frm.doc.exam_detail) {
			frm.add_custom_button(__("Download Template"), () => {
				window.open(
					"/api/method/ito_customization.ito_customization.bulk_registration_import.download_template?" +
						new URLSearchParams({ exam_detail: frm.doc.exam_detail })
				);
			});
		}

		if (frm.is_new() || frm.is_dirty() || !frm.doc.import_file) return;
		if (["Queued", "In Progress"].includes(frm.doc.status)) {
			frm.dashboard.set_headline(__("Import is running. This page updates when it finishes."));
			return;
		}

		frm.add_custom_button(__("Validate"), () =>
			frm.call({ doc: frm.doc, method: "validate_file", freeze: true, freeze_message: __("Checking the file...") })
				.then(() => frm.reload_doc())
		);

		const valid = (frm.doc.rows || []).filter((r) => r.status === "Valid").length;
		if (frm.doc.status === "Validated" && valid) {
			frm.add_custom_button(__("Start Import"), () =>
				frappe.confirm(
					__("Import {0} valid school rows? Rows with errors are skipped.", [valid]),
					() => frm.call({ doc: frm.doc, method: "start_import", freeze: true }).then(() => frm.reload_doc())
				)
			).addClass("btn-primary");
		}
	},
});
