// ============================================================
// Client Script  |  DocType: Registered Students  |  Apply To: Form
// ============================================================
frappe.ui.form.on("Registered Students", {
    refresh(frm) {
        if (frm.is_new()) return;

        const printUrl =
            "/printview" +
            "?doctype=Registered Students" +
            "&name=" + encodeURIComponent(frm.doc.name) +
            "&format=ITO Hall Ticket&no_letterhead=1";
        const downloadUrl =
            "/api/method/ito_customization.ito_customization.doc_events.hall_ticket.download_hall_ticket" +
            "?name=" + encodeURIComponent(frm.doc.name);

        frm.add_custom_button(__("Print"), () =>
            window.open(printUrl, "_blank")
        , __("Hall Ticket"));

        frm.add_custom_button(__("Download PDF"), () =>
            window.open(downloadUrl, "_blank")
        , __("Hall Ticket"));

        frm.page.set_inner_btn_group_as_primary(__("Hall Ticket"));
    },
});
