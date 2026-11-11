frappe.listview_settings["Web Forms Registration"] = {
	refresh(listview) {
		listview.page.clear_inner_toolbar();
		listview.page.add_inner_button(__("Send Bulk Email"), () => {
			const names = listview.get_checked_items(true);
			if (!names.length) {
				frappe.msgprint(__("Please select at least one record."));
				return;
			}

			const d = new frappe.ui.Dialog({
				title: __("Send Bulk Email"),
				fields: [
					{
						fieldname: "email_template",
						label: __("Email Template"),
						fieldtype: "Link",
						options: "Email Template",
						reqd: 1,
					},
				],
				primary_action_label: __("Send"),
				primary_action({ email_template }) {
					frappe.call({
						method: "ito_customization.ito_customization.doctype.web_forms_registration.web_forms_registration.send_bulk_email",
						args: { names, email_template },
						freeze: true,
						freeze_message: __("Queuing emails..."),
						callback(r) {
							if (!r.exc) {
								const m = r.message || {};
								frappe.show_alert({
									message: __("{0} email(s) queued, {1} skipped (no email)", [
										m.queued || 0,
										m.skipped || 0,
									]),
									indicator: "green",
								});
								d.hide();
								listview.refresh();
							}
						},
					});
				},
			});
			d.show();
		});
	},
};
