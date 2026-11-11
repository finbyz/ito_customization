// Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Web Forms Registration", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.status !== "Approve") return;

		frm.add_custom_button(__("Resend Email"), () => {
			frappe.confirm(__("Resend the E-Pass email to {0}?", [frm.doc.email]), () => {
				frappe.call({
					method: "ito_customization.ito_customization.doc_events.epass.resend_epass_email",
					args: { docname: frm.doc.name },
					freeze: true,
					freeze_message: __("Queuing email..."),
					callback(r) {
						if (!r.exc) {
							frappe.show_alert({ message: __("Email queued"), indicator: "green" });
						}
					},
				});
			});
		});
	},
});
