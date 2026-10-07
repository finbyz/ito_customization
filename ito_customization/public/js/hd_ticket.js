frappe.ui.form.on("HD Ticket", {
	setup(frm) {
		frm.set_query("custom_sub_ticket_1", () => ({
			filters: {
				ticket_type: frm.doc.ticket_type || "__no_ticket_type__",
			},
		}));

		frm.set_query("custom_sub_ticket_2", () => ({
			filters: {
				sub_ticket_type_1: frm.doc.custom_sub_ticket_1 || "__no_sub_ticket_type_1__",
			},
		}));
	},

	ticket_type(frm) {
		frm.set_value("custom_sub_ticket_1", "");
		frm.set_value("custom_sub_ticket_2", "");
	},

	custom_sub_ticket_1(frm) {
		frm.set_value("custom_sub_ticket_2", "");
	},
});
