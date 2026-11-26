// Customer form: download this school's registration sheet, or import it from a sheet.
frappe.ui.form.on("Customer", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.custom_customer_category !== "School Customer") return;
		if (!frappe.model.can_create("ITO Bulk Import")) return;
		const group = __("Registration Sheet");
		const exam_field = {
			fieldtype: "Link", fieldname: "exam_detail", label: __("Exam Schedule"), options: "Yearly Exam Date", reqd: 1,
			get_query: () => ({ filters: { docstatus: 1, is_little_champ: 0 } }),
		};

		frm.add_custom_button(__("Download This School"), () => {
			frappe.prompt([exam_field], (v) => {
				window.open("/api/method/ito_customization.ito_customization.bulk_registration_import.download_prefilled?" +
					new URLSearchParams({ exam_detail: v.exam_detail, customer: frm.doc.name }));
			}, __("Download Registration Sheet"), __("Download"));
		}, group);

		frm.add_custom_button(__("Import from Sheet"), () => {
			const d = new frappe.ui.Dialog({
				title: __("Import this school from a sheet"),
				fields: [
					exam_field,
					{ fieldtype: "Attach", fieldname: "import_file", label: __("Registration Sheet (.xlsx)"), reqd: 1 },
					{ fieldtype: "HTML", options: `<div class="text-muted small">${__("Only this school's row is imported. You can check it before importing.")}</div>` },
				],
				primary_action_label: __("Check Sheet"),
				async primary_action(v) {
					d.hide();
					const doc = await frappe.db.insert({
						doctype: "ITO Bulk Import", exam_detail: v.exam_detail, import_file: v.import_file, customer: frm.doc.name,
					});
					await frappe.call({
						method: "run_doc_method", args: { docs: doc, method: "validate_file" },
						freeze: true, freeze_message: __("Checking the file..."),
					}).catch(() => {});
					frappe.set_route("Form", "ITO Bulk Import", doc.name);
				},
			});
			d.show();
		}, group);
	},
});
