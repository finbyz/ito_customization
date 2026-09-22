// ============================================================
// Client Script  |  DocType: Registered Students  |  Apply To: Form
// ============================================================
frappe.ui.form.on("Registered Students", {
    refresh(frm) {
        if (frm.is_new()) return;

        const url = (base) =>
            base +
            "?doctype=Registered Students" +
            "&name=" + encodeURIComponent(frm.doc.name) +
            "&format=ITO Hall Ticket&no_letterhead=1";

        frm.add_custom_button(__("Print"), () =>
            window.open(url("/printview"), "_blank")
        , __("Hall Ticket"));

        frm.add_custom_button(__("Download PDF"), () =>
            window.open(url("/api/method/frappe.utils.print_format.download_pdf"), "_blank")
        , __("Hall Ticket"));

        frm.page.set_inner_btn_group_as_primary(__("Hall Ticket"));
    },
});

