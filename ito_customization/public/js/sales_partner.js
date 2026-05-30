frappe.ui.form.on('Sales Partner', {

    onload(frm) {
        set_coordinator_state_options(frm);
    },

    refresh(frm) {
        set_coordinator_state_options(frm);
    }

});

function set_coordinator_state_options(frm) {

    const field = frm.fields_dict.custom_stateprovince;

    if (!field) return;

    field.set_data(
        frappe.boot.india_state_options || []
    );
}