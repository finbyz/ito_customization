frappe.ui.form.on('School Subject', {
    onload: function(frm) {
        // text_book & work_book → little_champ = 1 (checked)
        frm.set_query('class', 'text_book', function() {
            return { filters: { 'little_champ': 1 } };
        });
        
        frm.set_query('class', 'work_book', function() {
            return { filters: { 'little_champ': 1 } };
        });
        
        // practice_workbook_110, prev_year_paper_160, student_guide_220 → little_champ = 0 (unchecked)
        frm.set_query('class', 'practice_workbook_110', function() {
            return { filters: { 'little_champ': 0 } };
        });
        
        frm.set_query('class', 'prev_year_paper_160', function() {
            return { filters: { 'little_champ': 0 } };
        });
        
        frm.set_query('class', 'student_guide_220', function() {
            return { filters: { 'little_champ': 0 } };
        });
    }
});