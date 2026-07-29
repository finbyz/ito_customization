

frappe.ui.form.on('Customer', {
	setup(frm) {
        frm.previous_academic_year = frm.doc.custom_current_academic_year;
    },
	onload(frm){
		toggle_country_state(frm);
		set_coordinator_state_options(frm);
		set_customer_state_options(frm);
	},
	refresh(frm) {
		if (frm.previous_academic_year === undefined) {
            frm.previous_academic_year = frm.doc.custom_current_academic_year;
        }
		toggle_country_state(frm);
		set_coordinator_state_options(frm);
		set_customer_state_options(frm);

		frm.add_custom_button(__('Create URL'), function() {
			if (!frm.doc.customer_name) {
				frappe.msgprint(__('Please set the Customer Name first.'));
				return;
			}

			frappe.call({
				method: 'ito_customization.ito_customization.doc_events.web_page.olympiad_book_order.generate_consent_token',
				args: { customer: frm.doc.name },
				callback: function(r) {
					if (!r.message) return;

					frappe.show_alert({
						message: __('Parent consent form URL generated and saved'),
						indicator: 'green'
					});

					frm.reload_doc();
				}
			});
		});

		setTimeout(() => {

			$("#lead-category-section").remove();

			let is_editable = frm.is_new();

			let html = `
				<div id="lead-category-section" style="
					background:#ffffff;
					border:1px solid #e5e7eb;
					border-radius:12px;
					padding:18px;
					margin-bottom:18px;
					box-shadow:0 1px 3px rgba(0,0,0,0.06);
				">

					<div style="
						font-size:12px;
						font-weight:700;
						color:#6b7280;
						margin-bottom:14px;
						text-transform:uppercase;
						letter-spacing:0.5px;
					">
						ITO — CUSTOMER CATEGORY
					</div>

					<div style="
						display:flex;
						gap:14px;
						flex-wrap:wrap;
					">

						<div
							class="lead-category-card"
							data-value="School Customer"
							style="
								flex:1;
								min-width:260px;
								padding:16px;
								border-radius:12px;
								cursor:${is_editable ? "pointer" : "not-allowed"};
								text-align:center;
								font-weight:600;
								opacity:${is_editable ? "1" : "0.85"};
								border:2px solid ${frm.doc.custom_customer_category === "School Customer" ? "#3b82f6" : "#d1d5db"};
								background:${frm.doc.custom_customer_category === "School Customer" ? "#eff6ff" : "#ffffff"};
								color:${frm.doc.custom_customer_category === "School Customer" ? "#2563eb" : "#111827"};
							"
						>
							🏫 School Customer
						</div>

						<div
							class="lead-category-card"
							data-value="Online Student Customer"
							style="
								flex:1;
								min-width:260px;
								padding:16px;
								border-radius:12px;
								cursor:${is_editable ? "pointer" : "not-allowed"};
								text-align:center;
								font-weight:600;
								opacity:${is_editable ? "1" : "0.85"};
								border:2px solid ${frm.doc.custom_customer_category === "Online Student Customer" ? "#3b82f6" : "#d1d5db"};
								background:${frm.doc.custom_customer_category === "Online Student Customer" ? "#eff6ff" : "#ffffff"};
								color:${frm.doc.custom_customer_category === "Online Student Customer" ? "#2563eb" : "#111827"};
							"
						>
							🎓 Online Student Customer
						</div>

					</div>

				</div>
			`;

			let target = frm.fields_dict.first_name.$wrapper.closest(".form-page");

			if (target.length) {
				target.prepend(html);
			}

			$(".lead-category-card")
				.off("click")
				.on("click", function () {

					// prevent editing after save
					if (!is_editable) {

						frappe.show_alert({
							message: __("Customer Category cannot be changed after creation"),
							indicator: "orange"
						});

						return;
					}

					let value = $(this).attr("data-value");

					frm.set_value(
						"custom_customer_category",
						value
					);

					frm.refresh();

				});

		}, 500);

	},
	custom_current_academic_year(frm) {
        let old_year = frm.previous_academic_year;
        let new_year = frm.doc.custom_current_academic_year;

        // First time or same value — nothing to archive
        if (!old_year || old_year === new_year) {
            frm.previous_academic_year = new_year;
            return;
        }

        // Check if old year already exists in the child table
        let exists = (frm.doc.custom_acedemic_years || []).some(
            row => row.registration_from === old_year
        );

        if (!exists) {
            let child = frm.add_child("custom_acedemic_years", {
                registration_from: old_year
            });
            frm.refresh_field("custom_acedemic_years");
        }

        // Update tracker to the newly selected year
        frm.previous_academic_year = new_year;
    },
	custom_copy(frm) {

			if (!frm.doc.custom_student_registration_link) {
				frappe.msgprint("No student registration link found to copy.");
				return;
			}

			navigator.clipboard.writeText(frm.doc.custom_student_registration_link)
				.then(() => {
					frappe.show_alert({
						message: __("Student registration link copied to clipboard"),
						indicator: "green"
					});
				})
				.catch(() => {
					frappe.msgprint("Unable to copy student registration link.");
				});
		},

	custom_customer_category(frm) {

		let customer_type = {
			"School Customer": ["Individual", "School"],
			"Online Student Customer": ["Offline Student", "Online Student"]
		};

		let options = customer_type[frm.doc.custom_customer_category] || [];

		frm.fields_dict.customer_type.df.options = options.join("\n");
		frm.refresh_field("customer_type");
		frm.set_value("customer_type", "");

		// Re-evaluate State/Country visibility for the new category
		toggle_country_state(frm);
	},
	// Fires when GST Category changes on the Tax tab.
	// This is the ONLY place custom_country should be written from the
	// client side — refresh/onload must never mutate values, only
	// toggle visibility, or the form will show "Not Saved" on load and
	// re-dirty itself right after saving.
	gst_category(frm) {
		toggle_country_state(frm);

		if (frm.doc.gst_category === "Overseas") {
			if (frm.doc.custom_country === "India") {
				frm.set_value("custom_country", "");
			}
		} else {
			if (frm.doc.custom_country !== "India") {
				frm.set_value("custom_country", "India");
			}
		}
	},

	// If user clears state after it's been used to set school_code, no auto action needed here —
	// school_code generation is handled server-side in Python (validate hook).
	custom_state(frm) {
		// placeholder in case you want a client-side preview of the prefix later
	}

});


function set_coordinator_state_options(frm) {

    const field = frm.fields_dict.custom_stateprovince;

    if (!field) return;

    field.set_data(
        frappe.boot.india_state_options || []
    );
}

function set_customer_state_options(frm) {

    const field = frm.fields_dict.custom_state;

    if (!field) return;

    // set_data only exists on Autocomplete controls. If custom_state is still
    // fieldtype Data (or anything else), calling it throws and silently kills
    // the rest of refresh() — including toggle_country_state below. Guard it.
    if (typeof field.set_data !== "function") {
        console.warn(
            "custom_state is not an Autocomplete field — change its fieldtype " +
            "in Customize Form to Autocomplete for the state dropdown to work."
        );
        return;
    }

    field.set_data(
        frappe.boot.india_state_options || []
    );
}

// Display-only now: toggles which field is visible based on GST Category.
// Does NOT set any values — that happens only in the gst_category handler
// above (client) and in the validate hook (server), never here. Calling
// frm.set_value() from this function was what caused the form to appear
// "Not Saved" immediately on load/reload, since refresh()/onload() call
// this on every page open and every post-save reload.
function toggle_country_state(frm) {
    // State/Country only apply to School Customers — they drive school
    // code generation. Online Student Customers store location on the
    // Address instead, so hide both regardless of GST Category.
    if (frm.doc.custom_customer_category === "Online Student Customer") {
        frm.set_df_property("custom_country", "hidden", 1);
        frm.set_df_property("custom_state", "hidden", 1);

        frm.refresh_field("custom_country");
        frm.refresh_field("custom_state");
        return;
    }

    const is_overseas = frm.doc.gst_category === "Overseas";

    if (is_overseas) {
        // Overseas: show Country, hide State
        frm.set_df_property("custom_country", "hidden", 0);
        frm.set_df_property("custom_state", "hidden", 1);
    } else {
        // Domestic: hide Country (force India), show State
        frm.set_df_property("custom_country", "hidden", 1);
        frm.set_df_property("custom_state", "hidden", 0);
    }

    frm.refresh_field("custom_country");
    frm.refresh_field("custom_state");
}