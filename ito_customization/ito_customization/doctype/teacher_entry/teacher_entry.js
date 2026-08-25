frappe.ui.form.on("Teacher Entry", {
    onload: function(frm) {
        if (!frm.is_new() || frm.doc.entries.length) {
            return;
        }

        const slabs = [
            ["0 - 100", "Certificate of Appreciation"],
            ["101 - 249", "₹1,000 + Certificate"],
            ["250 - 499", "₹2,000 + Certificate"],
            ["500 - 749", "₹3,500 + Certificate"],
            ["750 - 999", "₹5,000 + Certificate"],
            ["1,000 - 1,249", "₹7,500 + Certificate"],
            ["1,250 - 1,499", "₹10,000 + Certificate"],
            ["1,500 - 1,749", "₹13,000 + Certificate"],
            ["1,750 - 1,999", "₹15,000 + Certificate"],
            ["2,000 - 2,499", "₹20,000 + Certificate"],
            ["2,500 - 4,999", "₹25,000 + Certificate"],
            ["5,000 & Above", "₹30,000 + Certificate"]
        ];

        slabs.forEach(([total_participants, recognition_and_honorarium]) => {
            let row = frm.add_child("entries");

            row.total_participants = total_participants;
            row.recognition_and_honorarium = recognition_and_honorarium;
            row.select = 0;
        });

        frm.refresh_field("entries");
    }
});