frappe.ui.form.on("Registered Students", {
    refresh(frm) {
        if (frm.is_new()) return;

        const getFormat = async () => {
            if (!frm.doc.school) return "ITO Hall Ticket";
            const r = await frappe.db.get_value("Customer", frm.doc.school, "custom_is_little_champ");
            return r?.message?.custom_is_little_champ ? "Little Champ Hall Ticket" : "ITO Hall Ticket";
        };

        const printTicket = () => {
            // Open synchronously inside the click handler.
            const w = window.open("about:blank", "_blank");
            if (!w) {
                frappe.msgprint(__("Please enable pop-ups to open the hall ticket."));
                return;
            }
            w.document.body.textContent = __("Preparing hall ticket…");
            w.document.body.style.cssText = "font:14px sans-serif;padding:24px";

            getFormat()
                .then((format) => {
                    if (w.closed) return;
                    w.location.replace(
                        "/printview" +
                        "?doctype=" + encodeURIComponent(frm.doctype) +
                        "&name=" + encodeURIComponent(frm.doc.name) +
                        "&format=" + encodeURIComponent(format) +
                        "&no_letterhead=1"
                    );
                })
                .catch((e) => {
                    if (!w.closed) w.close();
                    console.error("Unable to resolve hall ticket format", e);
                    frappe.msgprint(__("Could not determine the hall ticket format. Please try again."));
                });
        };

        // No popup needed: navigating the current page to a download doesn't unload it.
        const downloadTicket = () => {
            window.location.href =
                "/api/method/ito_customization.ito_customization.doc_events.hall_ticket.download_hall_ticket" +
                "?name=" + encodeURIComponent(frm.doc.name);
        };

        frm.add_custom_button(__("Print"), printTicket, __("Hall Ticket"));
        frm.add_custom_button(__("Download PDF"), downloadTicket, __("Hall Ticket"));
        frm.page.set_inner_btn_group_as_primary(__("Hall Ticket"));
    },
});