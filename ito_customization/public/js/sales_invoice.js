frappe.ui.form.on("Sales Invoice", {
    refresh(frm) {
        show_amount_mismatch_banner(frm);
    },
    total(frm) {
        show_amount_mismatch_banner(frm);
    },
    custom_amount(frm) {
        show_amount_mismatch_banner(frm);
    },
});

function show_amount_mismatch_banner(frm) {
    if (frm.is_new() || frm.doc.custom_amount == null || frm.doc.custom_amount === "") {
        return;
    }

    const precision = frappe.defaults.get_default("float_precision") || 2;
    const total = flt(frm.doc.total, precision);
    const amount_paid = flt(frm.doc.custom_amount, precision);

    // Values match -> show nothing custom
    if (total === amount_paid) {
        return;
    }

    // Values differ -> show both amounts
    frm.dashboard.add_comment(
        __("Total Amount: {0}, Amount Paid: {1}", [
            `<b>${format_currency(total, frm.doc.currency)}</b>`,
            `<b>${format_currency(amount_paid, frm.doc.currency)}</b>`,
        ]),
        "orange",
        true
    );
}