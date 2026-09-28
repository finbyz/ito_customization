frappe.listview_settings["Result"] = {
    onload(listview) {
        listview.page.add_inner_button(
            __("Upload Results"),
            () => {
                open_result_upload_dialog();
            }
        );
    },
};


function open_result_upload_dialog() {
    const dialog = new frappe.ui.Dialog({
        title: __("Result Import"),
        fields: [
            {
                fieldtype: "HTML",
                fieldname: "instructions",
                options: `
                    <div class="mb-4">
                        <p>
                            ${__("Download the template, fill in the student result data, and upload it here.")}
                        </p>
                    </div>
                `,
            },
            {
                fieldname: "result_file",
                label: __("Result File"),
                fieldtype: "Attach",
                reqd: 1,
                description: __(
                    "Upload the completed CSV or Excel template."
                ),
            },
        ],

        primary_action_label: __("Upload Results"),

        primary_action(values) {
            if (!values.result_file) {
                frappe.msgprint(
                    __("Please select a result file.")
                );
                return;
            }

            frappe.call({
                method:
                    "ito_customization.ito_customization.doctype.result.result.upload_results",

                args: {
                    file_url: values.result_file,
                },

                freeze: true,

                freeze_message: __(
                    "Importing results..."
                ),

                callback(r) {
                    if (r.message) {
                        dialog.hide();
                        show_import_result(r.message);
                    }
                },
            });
        },
    });

    // ---------------------------------------------------------
    // Download Template Button
    // ---------------------------------------------------------

    dialog.set_secondary_action_label(
        __("Download Template")
    );

    dialog.set_secondary_action(() => {
        download_result_template();
    });

    dialog.show();
}


function download_result_template() {
    const url =
        "/api/method/ito_customization.ito_customization.doctype.result.result.download_result_template";

    window.open(url, "_blank");
}


function show_import_result(result) {
    let message = `
        <div>
            <p>
                <b>${__("Created")}:</b>
                ${result.created || 0}
            </p>

            <p>
                <b>${__("Updated")}:</b>
                ${result.updated || 0}
            </p>

            <p>
                <b>${__("Rows Processed")}:</b>
                ${result.rows_processed || 0}
            </p>

            <p>
                <b>${__("Errors")}:</b>
                ${result.errors?.length || 0}
            </p>
        </div>
    `;

    if (result.errors?.length) {
        message += `
            <hr>

            <b>${__("Errors")}</b>

            <div style="max-height: 250px; overflow-y: auto;">
                <ul>
                    ${result.errors
                        .map(
                            (error) =>
                                `<li>${frappe.utils.escape_html(error)}</li>`
                        )
                        .join("")}
                </ul>
            </div>
        `;
    }

    frappe.msgprint({
        title: __("Import Result"),
        message,
        indicator: result.errors?.length
            ? "orange"
            : "green",
    });

    if (!result.errors?.length) {
        setTimeout(() => {
            window.location.reload();
        }, 1000);
    }
}