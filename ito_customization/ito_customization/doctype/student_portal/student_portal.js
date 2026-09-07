// Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Student Portal", {
	school_name(frm) {
		if (frm.doc.school_name) {
			frappe.call({
				method: "ito_customization.ito_customization.doctype.student_portal.student_portal.get_customer_exam_details",
				args: {
					customer: frm.doc.school_name
				},
				callback: function(r) {
					if (r.message) {
						if (r.message.school_code) {
							frm.set_value("school_code", r.message.school_code);
						}
						frm.clear_table("exam_list");
						if (r.message.subjects && r.message.subjects.length) {
							r.message.subjects.forEach(function(item) {
								let row = frm.add_child("exam_list");
								row.school_subject = item.school_subject;
								row.abbr = item.abbr;
							});
						}
						frm.refresh_field("exam_list");
					}
				}
			});
		} else {
			frm.set_value("school_code", "");
			frm.clear_table("exam_list");
			frm.refresh_field("exam_list");
		}
	}
});

