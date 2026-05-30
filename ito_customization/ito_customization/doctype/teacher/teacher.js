// Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Teacher", {
	refresh(frm) {
		frm.trigger("render_addresses");
		
		// Add "Make Co-ordinator" button if teacher doesn't have a co-ordinator yet
		if (!frm.doc.__islocal && !frm.doc.co_ordinator) {
			frm.add_custom_button(__('Make Co-ordinator'), function() {
				make_coordinator(frm);
			}, __('Create'));
		}
		
		// Add button to view Sales Partner if co-ordinator exists
		if (frm.doc.co_ordinator) {
			frm.add_custom_button(__('View Co-ordinator'), function() {
				frappe.set_route('Form', 'Sales Partner', frm.doc.co_ordinator);
			});
		}
	},

	render_addresses(frm) {
		if (frm.doc.__islocal) {
			frm.set_df_property("address", "hidden", 1);
			return;
		}

		frappe.call({
			method: "ito_customization.ito_customization.doctype.teacher.teacher.get_addresses",
			args: { teacher_id: frm.doc.name },
			callback: function (r) {
				let html = "";
				if (r.message && r.message.length > 0) {
					// Set doc.address so depends_on is satisfied
					frm.doc.address = "has_address";

					html = `<div style="border: 1px solid #d1d5db; border-radius: 8px; padding: 15px; background-color: #f9fafb; box-shadow: 0 1px 3px rgba(0,0,0,0.05); margin-top: 10px;">`;
					r.message.forEach((addr, idx) => {
						html += `
							<div class="address-card" style="margin-bottom: 15px; padding-bottom: 15px; ${idx < r.message.length - 1 ? 'border-bottom: 1px solid #e5e7eb;' : ''} position: relative;">
								<div style="font-weight: 600; color: #1f2937; margin-bottom: 5px; font-size: 1.1em; display: flex; align-items: center; justify-content: space-between;">
									<span>
										${frappe.utils.escape_html(addr.address_title || addr.name)}
										${addr.is_primary_address ? '<span class="label label-success" style="font-size: 0.75em; margin-left: 8px; vertical-align: middle; background-color: #2ec4b6; color: white; padding: 2px 6px; border-radius: 4px;">Primary</span>' : ''}
									</span>
									<a href="/app/address/${addr.name}" class="btn btn-xs btn-default edit-address" style="font-weight: 500; font-size: 0.85em; padding: 2px 8px;">
										<i class="fa fa-pencil"></i> Edit
									</a>
								</div>
								<div style="color: #4b5563; line-height: 1.5; font-size: 0.95em;">
									${addr.display}
								</div>
							</div>
						`;
					});
					html += `</div>`;
				} else {
					// No address
					frm.doc.address = "";
					html = `
						<div style="border: 1px dashed #d1d5db; border-radius: 8px; padding: 20px; text-align: center; color: #6b7280; margin-top: 10px;">
							<i class="fa fa-map-marker" style="font-size: 2em; margin-bottom: 8px; display: block; color: #9ca3af;"></i>
							No Address added yet. Click <strong>Add Address</strong> to add one.
						</div>
					`;
				}

				frm.set_df_property("address", "hidden", 0);
				frm.fields_dict.address.wrapper.innerHTML = html;
				frm.refresh_field("add_address");
			}
		});
	},

	add_address(frm) {
		if (frm.doc.__islocal) {
			frappe.msgprint(__("Please save the Teacher document before adding an address."));
			return;
		}

		frappe.dynamic_link = {
			doctype: frm.doc.doctype,
			doc: frm.doc,
			fieldname: "name",
		};

		if (frappe.boot.enable_address_autocompletion === 1) {
			new frappe.ui.AddressAutocompleteDialog({
				title: __("New Address"),
				link_doctype: frm.doc.doctype,
				link_name: frm.doc.name,
				after_insert: function (doc) {
					frm.trigger("render_addresses");
				},
			}).show();
		} else {
			frappe.new_doc("Address", {
				address_title: frm.doc.name1 || frm.doc.name,
				links: [
					{
						link_doctype: frm.doc.doctype,
						link_name: frm.doc.name,
						link_title: frm.doc.name1 || frm.doc.name
					}
				]
			});
		}
	},

	profile_image: function (frm) {
		// Refresh the form to update the image display
		refresh_field("profile_image");
	}
});


function make_coordinator(frm) {
	frappe.confirm(
		__('Are you sure you want to create a Sales Partner (Co-ordinator) for {0}?', [frm.doc.name1]),
		function() {
			// User confirmed, proceed with creation
			frappe.call({
				method: 'ito_customization.ito_customization.doctype.teacher.teacher.make_sales_partner',
				args: {
					teacher: frm.doc.name
				},
				freeze: true,
				freeze_message: __('Creating Co-ordinator...'),
				callback: function(r) {
					if (r.message) {
						frappe.show_alert({
							message: __('Co-ordinator created successfully: {0}', [r.message]),
							indicator: 'green'
						}, 5);
						
						// Refresh the form to show the new co_ordinator link
						frm.reload_doc();
						
						// Ask if user wants to open the Sales Partner
						frappe.confirm(
							__('Do you want to open the newly created Co-ordinator?'),
							function() {
								frappe.set_route('Form', 'Sales Partner', r.message);
							}
						);
					}
				},
				error: function(r) {
					frappe.msgprint({
						title: __('Error'),
						message: __('Failed to create Co-ordinator. Please try again.'),
						indicator: 'red'
					});
				}
			});
		}
	);
}
