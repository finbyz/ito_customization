// ============================================================
// Client Script | DocType: Registered Students | Apply To: List
// ============================================================

frappe.listview_settings["Registered Students"] = {
    onload(listview) {
        listview.page.add_inner_button(__("Import from Image/PDF"), () => {
            openStudentImport(listview);
        });

        const generate = () => {
            // --------------------------------------------------------
            // Mandatory filter check
            // --------------------------------------------------------
            const filters = listview.filter_area.get();

            const school = filters.find((f) => f[1] === "school" && f[3])?.[3];
            const studentClass = filters.find((f) => f[1] === "class" && f[3])?.[3];

            if (!school || !studentClass) {
                frappe.msgprint({
                    title: __("Filters Required"),
                    message: __(
                        "Please filter the list by <b>School</b> and <b>Class</b> before generating roll numbers and hall tickets."
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

            const ticketWindow = window.open("about:blank", "_blank");
            if (!ticketWindow) {
                frappe.msgprint(__("Please enable pop-ups to generate hall tickets."));
                return;
            }

            frappe.call({
                method: "ito_customization.ito_customization.doctype.registered_students.registered_students.generate_roll_numbers_for_hall_tickets",
                args: {
                    school,
                    student_class: studentClass,
                    names,
                },
                freeze: true,
                freeze_message: __("Generating roll numbers..."),
                silent: true,
            }).then((response) => {
                const generatedNames = response.message?.names;
                if (!generatedNames?.length) {
                    ticketWindow.close();
                    frappe.msgprint(__("No selected students were available for hall-ticket generation."));
                    return;
                }

                listview.refresh();
                const url =
                    "/api/method/ito_customization.ito_customization.doc_events.hall_ticket.download_hall_tickets" +
                    "?names=" +
                    encodeURIComponent(JSON.stringify(generatedNames));
                ticketWindow.location.replace(url);
            }).catch((error) => {
                ticketWindow.close();
                console.error("Unable to generate roll numbers for hall tickets", error);
                frappe.msgprint({
                    title: __("Roll Number Generation Failed"),
                    message: getServerErrorMessage(error),
                    indicator: "red",
                });
            });
        };

        // ------------------------------------------------------------
        // Add button to toolbar
        // ------------------------------------------------------------
        listview.page.add_inner_button(
            __("Generate Roll Nos & Hall Tickets"),
            generate
        );

        // ------------------------------------------------------------
        // Add to Actions menu
        // ------------------------------------------------------------
        if (listview.page.add_actions_menu_item) {
            listview.page.add_actions_menu_item(
                __("Generate Roll Nos & Hall Tickets"),
                generate
            );
        } else {
            listview.page.add_menu_item(
                __("Generate Roll Nos & Hall Tickets"),
                generate
            );
        }
    },
};

const studentImportMethod =
    "ito_customization.ito_customization.doctype.registered_students.student_import";

function openStudentImport(listview) {
    let fileName = null;
    const dialog = new frappe.ui.Dialog({
        title: __("Import Registered Students"),
        fields: [
            { fieldtype: "Link", fieldname: "school", label: __("School"), options: "Customer", reqd: 1 },
            { fieldtype: "Link", fieldname: "academic_year", label: __("Academic Year"), options: "School Academic Year", reqd: 1 },
            { fieldtype: "HTML", fieldname: "upload_control" },
        ],
        primary_action_label: __("Extract Students"),
        primary_action: async (values) => {
            if (!fileName) {
                frappe.msgprint(__("Upload a private image or PDF first."));
                return;
            }
            dialog.disable_primary_action();
            try {
                const response = await frappe.call({
                    method: `${studentImportMethod}.extract_students`,
                    args: { ...values, file_name: fileName },
                    freeze: true,
                    freeze_message: __("Reading the student list..."),
                });
                const rows = response.message?.rows || [];
                if (!rows.length) {
                    frappe.msgprint(__("No student rows were found in the file."));
                    return;
                }
                dialog.hide();
                openStudentReview(listview, rows, values, fileName, response.message?.file_url);
            } finally {
                dialog.enable_primary_action();
            }
        },
    });
    dialog.show();

    const wrapper = dialog.fields_dict.upload_control.$wrapper;
    const button = $(`<button class="btn btn-default btn-sm">${__("Upload Image/PDF")}</button>`);
    const label = $('<span class="text-muted" style="margin-left: 10px"></span>');
    wrapper.append(button, label);
    button.on("click", () => {
        new frappe.ui.FileUploader({
            allow_multiple: false,
            make_attachments_public: 0,
            allow_toggle_private: false,
            allow_web_link: false,
            restrictions: { allowed_file_types: ["image/jpeg", "image/png", "image/webp", "application/pdf"] },
            on_success(file) {
                fileName = file.name;
                label.text(file.file_name || file.name);
            },
        });
    });
}

function openStudentReview(listview, rows, context, fileName, fileUrl) {
    rows.forEach((row) => {
        row.initial_review = row.subject_review_required
            ? __("Check subjects")
            : row.issues ? __("Check details") : __("OK");
    });

    // `columns` must be whole numbers; the visible ones below add up to 10.
    const fields = [
        // { fieldtype: "Data", fieldname: "initial_review", label: __("Initial Review"), in_list_view: 1, read_only: 1, columns: 1 },
        { fieldtype: "Link", fieldname: "class", label: __("Class"), options: "Class", in_list_view: 1, columns: 1 },
        { fieldtype: "Data", fieldname: "serial_no", label: __("Serial"), in_list_view: 1, columns: 1 },
        { fieldtype: "Data", fieldname: "student_name", label: __("Student Name"), in_list_view: 1, columns: 2 },
        { fieldtype: "Data", fieldname: "mobile", label: __("Mobile") },   // editable in the row form, not shown in the grid
        { fieldtype: "Small Text", fieldname: "subjects", label: __("Subjects"), in_list_view: 1, columns: 2 },
        // { fieldtype: "Check", fieldname: "confirm_subjects", label: __("Checked"), in_list_view: 1, columns: 1,
        //   read_only_depends_on: "eval:!doc.subject_review_required" },
        { fieldtype: "Check", fieldname: "subject_review_required", hidden: 1 },
        { fieldtype: "Small Text", fieldname: "issues", label: __("Review Notes"), in_list_view: 1, read_only: 1, columns: 2 },
    ];

    const dialog = new frappe.ui.Dialog({
        title: __("Review Extracted Students"),
        size: "extra-large",
        fields: [
            { fieldtype: "HTML", fieldname: "instructions" },
            {
                fieldtype: "Table", fieldname: "students", label: __("Students"),
                data: rows, fields, cannot_add_rows: false, in_place_edit: true,
            },
        ],
        primary_action_label: __("Create Students"),
        primary_action: async () => {
            frappe.ui.form.close_grid_form();
            const selectedRows = dialog.fields_dict.students.grid.get_data();
            if (!selectedRows.length) {
                frappe.msgprint(__("Keep at least one student row to import."));
                return;
            }
            const unchecked = selectedRows.filter((row) => row.subject_review_required && !row.confirm_subjects);
            if (unchecked.length) {
                frappe.msgprint(__(
                    "{0} row(s) need their subjects checked against the image. Tick Checked once confirmed.",
                    [unchecked.length]
                ));
                return;
            }
            dialog.disable_primary_action();
            try {
                const response = await frappe.call({
                    method: `${studentImportMethod}.import_students`,
                    args: { ...context, file_name: fileName, rows: JSON.stringify(selectedRows) },
                    freeze: true,
                    freeze_message: __("Creating students..."),
                });
                const results = response.message?.results || [];
                const created = results.filter((row) => row.name);
                const errors = results.filter((row) => row.error);
                if (created.length) listview.refresh();
                frappe.msgprint({
                    title: __("Student Import Result"),
                    message: [
                        __("Created {0} student(s).", [created.length]),
                        ...errors.map((row) => __("Row {0}: {1}", [
                            frappe.utils.escape_html(String(row.row_id)),
                            frappe.utils.escape_html(row.error),
                        ])),
                    ].join("<br>"),
                });
                if (!errors.length) dialog.hide();
            } finally {
                dialog.enable_primary_action();
            }
        },
    });
    dialog.show();

    const flagged = rows.filter((row) => row.issues || row.subject_review_required).length;
    const toConfirm = rows.filter((row) => row.subject_review_required).length;
    const sourceLink = fileUrl
        ? `<a class="btn btn-default btn-xs" href="${frappe.utils.escape_html(frappe.utils.get_file_link(fileUrl))}" target="_blank" rel="noopener">${__("Open original file")}</a>`
        : "";
    dialog.fields_dict.instructions.$wrapper.html(
        `<style>
            .student-import-review { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:12px; margin-bottom:12px; }
            .student-import-review__summary { display:flex; flex-wrap:wrap; gap:8px; }
            .student-import-review__stat { padding:8px 12px; border:1px solid var(--border-color); border-radius:6px; background:var(--card-bg); }
            .student-import-review__stat strong { display:block; font-size:16px; }
            .student-import-review__hint { flex-basis:100%; margin:0; }
        </style>
        <div class="student-import-review">
            <div class="student-import-review__summary">
                <div class="student-import-review__stat"><strong>${rows.length}</strong>${__("Students found")}</div>
                <div class="student-import-review__stat"><strong>${flagged}</strong>${__("Rows flagged")}</div>
                <div class="student-import-review__stat"><strong>${toConfirm}</strong>${__("Subjects to confirm")}</div>
            </div>
            ${sourceLink}
            <p class="student-import-review__hint text-muted">${__("Compare names, classes, serial numbers and subjects with the original file. Flags are from extraction and do not update as you edit. Rows marked Check subjects must be ticked Checked before import. Click a row to edit Mobile. Roll numbers are assigned alphabetically within each class.")}</p>
        </div>`
    );
}

function getServerErrorMessage(error) {
    const serverMessages = error?._server_messages || error?.responseJSON?._server_messages;
    if (serverMessages) {
        const messages = [
            ...new Set(
                JSON.parse(serverMessages)
                    .map((message) => JSON.parse(message).message)
                    .filter(Boolean)
            ),
        ];
        if (messages.length) {
            return messages.join("<br>");
        }
    }

    return (
        error?.message ||
        (typeof error === "string" && error) ||
        __("Could not generate roll numbers. Please try again.")
    );
}