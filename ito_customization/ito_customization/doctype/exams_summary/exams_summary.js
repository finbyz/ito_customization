frappe.ui.form.on("Exams Summary", {
    refresh(frm) {
        set_subject_filter(frm);
    },

    is_little_champ(frm) {
        set_subject_filter(frm);

        // Optional: Clear already selected subjects
        frm.fields_dict.exam_summary.grid.refresh();
    }
});

function set_subject_filter(frm) {
    frm.fields_dict.exam_summary.grid.get_field("subject").get_query = function () {

        let filters = {};

        if (frm.doc.is_little_champ) {
            filters.little_champ = 1;
        }

        return {
            filters: filters
        };
    };
}