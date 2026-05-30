frappe.ui.form.on("Lead", {
	refresh(frm) {

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
						ITO — LEAD CATEGORY
					</div>

					<div style="
						display:flex;
						gap:14px;
						flex-wrap:wrap;
					">

						<div
							class="lead-category-card"
							data-value="School Lead"
							style="
								flex:1;
								min-width:260px;
								padding:16px;
								border-radius:12px;
								cursor:${is_editable ? "pointer" : "not-allowed"};
								text-align:center;
								font-weight:600;
								opacity:${is_editable ? "1" : "0.85"};
								border:2px solid ${frm.doc.custom_lead_category === "School Lead" ? "#3b82f6" : "#d1d5db"};
								background:${frm.doc.custom_lead_category === "School Lead" ? "#eff6ff" : "#ffffff"};
								color:${frm.doc.custom_lead_category === "School Lead" ? "#2563eb" : "#111827"};
							"
						>
							🏫 School Lead
						</div>

						<div
							class="lead-category-card"
							data-value="Online Student Lead"
							style="
								flex:1;
								min-width:260px;
								padding:16px;
								border-radius:12px;
								cursor:${is_editable ? "pointer" : "not-allowed"};
								text-align:center;
								font-weight:600;
								opacity:${is_editable ? "1" : "0.85"};
								border:2px solid ${frm.doc.custom_lead_category === "Online Student Lead" ? "#3b82f6" : "#d1d5db"};
								background:${frm.doc.custom_lead_category === "Online Student Lead" ? "#eff6ff" : "#ffffff"};
								color:${frm.doc.custom_lead_category === "Online Student Lead" ? "#2563eb" : "#111827"};
							"
						>
							🎓 Online Student Lead
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
							message: __("Lead Category cannot be changed after creation"),
							indicator: "orange"
						});

						return;
					}

					let value = $(this).attr("data-value");

					frm.set_value(
						"custom_lead_category",
						value
					);

					frm.refresh();

				});

		}, 500);

	},

	custom_no_of_students(frm) {

		let count = cint(frm.doc.custom_no_of_students || 0);

		let category = "";

		if (count >= 1000) {
			category = "VVIP";
		}

		else if (count >= 500) {
			category = "VIP";
		}

		else if (count >= 300 && count <= 500) {
			category = "High";
		}

		else if (count >= 100 && count < 300) {
			category = "Medium";
		}

		else if (count >= 80 && count < 100) {
			category = "Average";
		}

		else if (count < 70) {
			category = "Low";
		}

		frm.set_value(
			"custom_school_category",
			category
		);

	},

	custom_office_contact_number(frm) {

		if (frm.doc.custom_office_contact_number) {

			frm.set_value(
				"phone",
				frm.doc.custom_office_contact_number
			);

		}

	},

	custom_stateprovince_copy(frm) {

		if (frm.doc.custom_stateprovince_copy) {

			frm.set_value(
				"state",
				frm.doc.custom_stateprovince_copy
			);

		}

	},
	country(frm) {
        set_state_options(frm);
    },

	custom_request_category(frm) {

	let subcategory_map = {
		"Ind-Awards & Cheques": [
		"Incorrect Details In Certificate",
		"Awards/Certificate Not Delivered",
		"Missing Certificate",
		"Awards/Certificate Returned",
		"Cheque Name Correction",
		"Award Parcel Stuck",
		"Cheque Expired (Re-Issue)",
		"Certificate Name Correction"
		],
		"Ind-Profile Update": [
			"Number Change",
			"Delete Profile",
			"Email Change",
			"Name Change",
			"Address Change",
			"Class Change",
			"School Name Change"
		],

		"Ind-Registration": [
			"Payment Link",
			"Refund",
			"Product Change/Update",
			"Adjusting Payment",
			"Subject Change",
			"Activate Subscription",
			"Payment Pending"
		],

		"Ind-Study Material": [
			"Undelivered / Not Received",
			"Missing Study Material",
			"Study Material Change",
			"Order Returned - Redispatch",
			"Activate Ebooks / Epqps",
			"Product Change/Update",
			"Update Address / Contact (Dispatch)",
			"Dispatch Status",
			"Incorrect Material Received"
		],

		"Online Exam": [
			"Submission Error",
			"Slot Change",
			"Check Exam Submission",
			"Technical Error During Exam",
			"Re-Attempt"
		],

		"Payment": [
			"Missing Payment Details",
			"Amount Discrepancy",
			"Partial Payment Received",
			"Dispatch Details Not Provided",
			"Additional Order Required"
		],

		"Study Material Not Received": [
			"Out Of Stock",
			"Incorrect Book Received",
			"Exchange Request",
			"Missing Book",
			"Sample Or Teachers Copy Request"
		],

		"Tasks": [
			"Others"
		]

	};

	let options = subcategory_map[
		frm.doc.custom_request_category
	] || [];
	frm.fields_dict.custom_request_subcategory.df.options =
		options.join("\n");

	frm.refresh_field(
		"custom_request_subcategory"
	);

	frm.set_value(
		"custom_request_subcategory",
		""
	);

}
});

function set_state_options(frm) {
    let field = frm.fields_dict.custom_stateprovince;

    if (!field) return;

    if (frm.doc.country === "India") {
        field.set_data(frappe.boot.india_state_options || []);
    } else {
        field.set_data([]);
    }
}