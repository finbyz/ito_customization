// ============================================================
// Client Script  |  DocType: Registered Students  |  Apply To: List
// ============================================================
frappe.listview_settings["Registered Students"] = {
    onload(listview) {
        const generate = () => {
            const names = listview.get_checked_items(true);
            if (!names.length) {
                frappe.msgprint(__("Please select at least one student."));
                return;
            }
            window.open(
                "/api/method/ito_customization.ito_customization.doc_events.hall_ticket.download_hall_tickets" +
                "?names=" + encodeURIComponent(JSON.stringify(names)),
                "_blank"
            );
        };

        // Add direct button to top toolbar
        listview.page.add_inner_button(__("Generate Hall Tickets"), generate);

        // Add to Actions dropdown menu when checkboxes are selected
        if (listview.page.add_actions_menu_item) {
            listview.page.add_actions_menu_item(__("Generate Hall Tickets"), generate);
        } else {
            listview.page.add_menu_item(__("Generate Hall Tickets"), generate);
        }
    },
};
