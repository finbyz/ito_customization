// "Import Registrations" on the Customer list opens the ITO bulk import.
frappe.listview_settings["Customer"] = frappe.listview_settings["Customer"] || {};
(() => {
	const settings = frappe.listview_settings["Customer"];
	const previous_onload = settings.onload;
	settings.onload = function (listview) {
		if (previous_onload) previous_onload.apply(this, arguments);
		if (frappe.model.can_create("ITO Bulk Import")) {
			listview.page.add_inner_button(__("Import Registrations"), () => frappe.new_doc("ITO Bulk Import"));
		}
	};
})();
