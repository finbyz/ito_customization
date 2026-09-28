

frappe.ui.form.on('Customer', {
	setup(frm) {
		frm.previous_academic_year = frm.doc.custom_current_academic_year;
	},
	onload(frm) {
		toggle_country_state(frm);
		set_coordinator_state_options(frm);
		set_customer_state_options(frm);
	},
	refresh(frm) {
		if (frm.previous_academic_year === undefined) {
			frm.previous_academic_year = frm.doc.custom_current_academic_year;
		}
		load_customer_summary(frm);
		toggle_country_state(frm);
		set_coordinator_state_options(frm);
		set_customer_state_options(frm);

		// frm.add_custom_button(__('Create URL'), function () {
		// 	if (!frm.doc.customer_name) {
		// 		frappe.msgprint(__('Please set the Customer Name first.'));
		// 		return;
		// 	}

		// 	frappe.call({
		// 		method: 'ito_customization.ito_customization.doctype.student_portal.student_portal.generate_student_portal_token',
		// 		args: { customer: frm.doc.name },
		// 		callback: function (r) {
		// 			if (!r.message) return;

		// 			frappe.show_alert({
		// 				message: __('Student portal URL generated and saved'),
		// 				indicator: 'green'
		// 			});

		// 			frm.reload_doc();
		// 		}
		// 	});
		// });

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

	custom_customer_summary(frm) {
        load_customer_summary(frm);
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


function load_customer_summary(frm) {
    const wrapper = frm.fields_dict.custom_customer_summary?.$wrapper;

    if (!wrapper) {
        return;
    }

    if (frm.is_new()) {
        render_customer_summary(wrapper, {
            registration_count: 0,
            registration_amount: 0,
            book_order_count: 0,
            book_order_amount: 0
        });
        return;
    }

    wrapper.html(`
        <div style="
            padding: 20px;
            text-align: center;
            color: var(--text-muted);
        ">
            Loading customer summary...
        </div>
    `);

    frappe.call({
        method:
            "ito_customization.ito_customization.doc_events.customer.get_customer_summary",

        args: {
            customer: frm.doc.name
        },

        callback(r) {
            if (r.message) {
                render_customer_summary(
                    wrapper,
                    r.message
                );
            }
        },

        error() {
            wrapper.html(`
                <div style="
                    padding: 20px;
                    text-align: center;
                    color: var(--text-muted);
                ">
                    Unable to load customer summary.
                </div>
            `);
        }
    });
}


function render_customer_summary(wrapper, data) {
	data = data || {};

	const format_currency = (value) => {
		const amount = Number(value || 0);

		return `₹ ${amount.toLocaleString("en-IN", {
			minimumFractionDigits: 2,
			maximumFractionDigits: 2,
		})}`;
	};

	const format_count = (value) =>
		Number(value || 0).toLocaleString("en-IN");

	const icons = {
		users: `<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>`,
		wallet: `<path d="M21 12V7a2 2 0 0 0-2-2H5a2 2 0 0 1 0-4h14"/><path d="M3 5v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4z"/>`,
		book: `<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>`,
		receipt: `<path d="M4 2v20l3-2 3 2 3-2 3 2 3-2 1 .67V2l-1 .67L17 2l-3 2-3-2-3 2-3-2z"/><path d="M8 8h8"/><path d="M8 12h8"/>`,
	};

	// Order matters: items 1-2 fill the left column, items 3-4 the right column
	const cards = [
		{
			label: "Total Registration",
			value: format_count(data.registration_count),
			icon: icons.users,
			color: "#3b82f6",
		},
		{
			label: "Registration Amount",
			value: format_currency(data.registration_amount),
			icon: icons.wallet,
			color: "#10b981",
		},
		{
			label: "Book Order Count",
			value: format_count(data.book_order_count),
			icon: icons.book,
			color: "#f59e0b",
		},
		{
			label: "Book Order Amount",
			value: format_currency(data.book_order_amount),
			icon: icons.receipt,
			color: "#8b5cf6",
		},
	];

	// Progress of book orders against the 60% requirement
	const REQUIRED_PERCENT = 60;
	const registration_amount = Number(data.registration_amount || 0);
	const book_order_amount = Number(data.book_order_amount || 0);
	const required_amount = (registration_amount * REQUIRED_PERCENT) / 100;

	let progress_html = "";

	if (registration_amount > 0) {
		const achieved_percent =
			(book_order_amount / registration_amount) * 100;
		const bar_width = Math.min(
			(achieved_percent / REQUIRED_PERCENT) * 100,
			100
		);
		const is_met = book_order_amount >= required_amount;
		const bar_color = is_met ? "#10b981" : "#f59e0b";

		progress_html = `
			<div class="cs-progress">
				<div class="cs-progress-head">
					<span class="cs-progress-title">
						Book order requirement (${REQUIRED_PERCENT}% of registration)
					</span>
					<span class="cs-badge" style="
						color: ${bar_color};
						background: ${bar_color}1a;
					">
						${is_met ? "Requirement met" : "Below requirement"}
					</span>
				</div>

				<div class="cs-progress-track">
					<div class="cs-progress-bar" style="
						width: ${bar_width}%;
						background: ${bar_color};
					"></div>
				</div>

				<div class="cs-progress-foot">
					<span>${achieved_percent.toFixed(1)}% achieved</span>
					<span>
						${format_currency(book_order_amount)}
						of ${format_currency(required_amount)} required
					</span>
				</div>
			</div>
		`;
	}

	wrapper.html(`
		<style>
			.cs-grid {
				display: grid;
				grid-template-columns: repeat(2, minmax(0, 1fr));
				grid-template-rows: repeat(2, auto);
				grid-auto-flow: column;
				gap: 10px 14px;
				margin: 8px 0 10px;
			}

			@media (max-width: 640px) {
				.cs-grid {
					grid-template-columns: minmax(0, 1fr);
					grid-template-rows: none;
					grid-auto-flow: row;
				}
			}

			.cs-card {
				display: flex;
				align-items: center;
				gap: 12px;
				padding: 10px 14px;
				border: 1px solid var(--border-color);
				border-left: 3px solid var(--accent);
				border-radius: 10px;
				background: var(--card-bg);
				box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
				transition: transform 0.15s ease, box-shadow 0.15s ease;
			}

			.cs-card:hover {
				transform: translateY(-1px);
				box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
			}

			.cs-icon {
				flex: 0 0 auto;
				display: flex;
				align-items: center;
				justify-content: center;
				width: 34px;
				height: 34px;
				border-radius: 9px;
				color: var(--accent);
				background: color-mix(in srgb, var(--accent) 12%, transparent);
			}

			.cs-icon svg {
				width: 18px;
				height: 18px;
				fill: none;
				stroke: currentColor;
				stroke-width: 2;
				stroke-linecap: round;
				stroke-linejoin: round;
			}

			.cs-label {
				font-size: 11px;
				font-weight: 500;
				letter-spacing: 0.04em;
				text-transform: uppercase;
				color: var(--text-muted);
				margin-bottom: 2px;
			}

			.cs-value {
				font-size: 18px;
				font-weight: 700;
				line-height: 1.2;
				color: var(--text-color);
			}

			.cs-progress {
				margin: 0 0 16px;
				padding: 10px 14px;
				border: 1px solid var(--border-color);
				border-radius: 10px;
				background: var(--card-bg);
			}

			.cs-progress-head,
			.cs-progress-foot {
				display: flex;
				justify-content: space-between;
				align-items: center;
				flex-wrap: wrap;
				gap: 6px;
			}

			.cs-progress-title {
				font-size: 12px;
				font-weight: 600;
				color: var(--text-color);
			}

			.cs-badge {
				font-size: 11px;
				font-weight: 600;
				padding: 2px 9px;
				border-radius: 999px;
			}

			.cs-progress-track {
				height: 6px;
				margin: 8px 0 6px;
				border-radius: 999px;
				background: var(--control-bg, rgba(128, 128, 128, 0.15));
				overflow: hidden;
			}

			.cs-progress-bar {
				height: 100%;
				border-radius: 999px;
				transition: width 0.4s ease;
			}

			.cs-progress-foot {
				font-size: 11px;
				color: var(--text-muted);
			}
		</style>

		<div class="cs-grid">
			${cards
				.map(
					(card) => `
				<div class="cs-card" style="--accent: ${card.color};">
					<div class="cs-icon">
						<svg viewBox="0 0 24 24">${card.icon}</svg>
					</div>
					<div>
						<div class="cs-label">${card.label}</div>
						<div class="cs-value">${card.value}</div>
					</div>
				</div>
			`
				)
				.join("")}
		</div>

		${progress_html}
	`);
}