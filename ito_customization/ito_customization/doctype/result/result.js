// Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors
// For license information, please see license.txt

function sync_round_1_values(frm, source_row) {
    if (!source_row.subject || !frm.doc.qualified) return;

    const target_row = (frm.doc.round_2 || []).find(
        (row) => row.subject_2 === source_row.subject
    ) || frm.add_child("round_2", { subject_2: source_row.subject });

    target_row.round_1_score = source_row.score ?? "";
    target_row.round_1_topper = source_row.topper || "";
    frm.refresh_field("round_2");
}

function sync_all_round_1_values(frm) {
    (frm.doc.round_1 || []).forEach((row) => sync_round_1_values(frm, row));
}

frappe.ui.form.on("Result", {
    qualified(frm) {
        sync_all_round_1_values(frm);
    },
    round_1_add(frm, cdt, cdn) {
        sync_round_1_values(frm, frappe.get_doc(cdt, cdn));
    },
});

frappe.ui.form.on("Result Round 1", {
    subject(frm, cdt, cdn) {
        sync_round_1_values(frm, frappe.get_doc(cdt, cdn));
    },
    score(frm, cdt, cdn) {
        sync_round_1_values(frm, frappe.get_doc(cdt, cdn));
    },
    topper(frm, cdt, cdn) {
        sync_round_1_values(frm, frappe.get_doc(cdt, cdn));
    },
});
