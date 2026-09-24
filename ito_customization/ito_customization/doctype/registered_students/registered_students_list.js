// ============================================================
// Client Script | DocType: Registered Students | Apply To: List
// ============================================================

frappe.listview_settings["Registered Students"] = {
    onload(listview) {
        const generate = () => {
            // --------------------------------------------------------
            // Mandatory filter check
            // --------------------------------------------------------
            const filters = listview.filter_area.get();

            const has_school = filters.some(
                (f) => f[1] === "school" && f[3]
            );

            const has_class = filters.some(
                (f) => f[1] === "class" && f[3]
            );

            const has_academic_year = filters.some(
                (f) => f[1] === "academic_year" && f[3]
            );

            if (!has_school || !has_class || !has_academic_year) {
                frappe.msgprint({
                    title: __("Filters Required"),
                    message: __(
                        "Please filter the list by <b>School</b>, <b>Class</b>, and <b>Academic Year</b> before generating hall tickets."
                    ),
                    indicator: "red",
                });
                return;
            }

            // --------------------------------------------------------
            // Selection check
            // --------------------------------------------------------
            const names = listview.get_checked_items(true);

            if (!names.length) {
                frappe.msgprint(
                    __("Please select at least one student.")
                );
                return;
            }

            // Sort selected students in ascending natural order
            // e.g. 001, 002, 003...
            names.sort((a, b) =>
                a.localeCompare(b, undefined, {
                    numeric: true,
                    sensitivity: "base",
                })
            );

            // --------------------------------------------------------
            // Generate Hall Tickets
            // --------------------------------------------------------
            const url =
                "/api/method/ito_customization.ito_customization.doc_events.hall_ticket.download_hall_tickets" +
                "?names=" +
                encodeURIComponent(JSON.stringify(names));

            window.open(url, "_blank");
        };

        // ------------------------------------------------------------
        // Add button to toolbar
        // ------------------------------------------------------------
        listview.page.add_inner_button(
            __("Generate Hall Tickets"),
            generate
        );

        // ------------------------------------------------------------
        // Add to Actions menu
        // ------------------------------------------------------------
        if (listview.page.add_actions_menu_item) {
            listview.page.add_actions_menu_item(
                __("Generate Hall Tickets"),
                generate
            );
        } else {
            listview.page.add_menu_item(
                __("Generate Hall Tickets"),
                generate
            );
        }
    },
};