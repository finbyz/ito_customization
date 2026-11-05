import base64
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from ito_customization.ito_customization import school_student_list as ssl

SUBJECTS = [
    {"name": "Hindi Olympiad (NHO)", "abbr": "NHO"},
    {"name": "Drawing Olympiad (IDO)", "abbr": "IDO"},
    {"name": "Computer Olympiad (ICO)", "abbr": "ICO"},
    {"name": "Commerce Olympiad (CIO)", "abbr": "CIO"},
    {"name": "Social Studies Olympiad (NSSO)", "abbr": "NSSO"},
]
HEADER = "Sr. No.,Student Name,Class,Mobile No.,NHO,IDO,ICO,CIO,NSSO"


def encode(*lines: str) -> str:
    return base64.b64encode("\n".join(lines).encode()).decode()


def parse(*lines: str) -> dict:
    return ssl.parse_student_sheet(ssl.read_sheet(encode(*lines), "list.csv"), SUBJECTS)


class TestParseStudentSheet(IntegrationTestCase):
    def test_mixed_classes_and_multiple_subjects_are_one_row(self):
        result = parse(
            HEADER,
            "1,Asha Rao,5,9876543210,Yes,Yes,Yes,No,No",
            "2,Ravi Kumar,Class 12,,1,0,0,0,0",
            "3,Meena Das,8th,,0,1,0,0,1",
        )
        self.assertEqual(result["errors"], [])
        self.assertEqual([r["class"] for r in result["rows"]], ["5", "12", "8"])
        self.assertEqual(result["rows"][0]["subjects"], ["NHO", "IDO", "ICO"])
        self.assertTrue(all(not r["errors"] for r in result["rows"]))
        self.assertEqual(result["rows"][0]["student_name"], "ASHA RAO")

    def test_missing_required_columns(self):
        result = parse("Sr. No.,Student Name,NHO", "1,Asha,Yes")
        self.assertTrue(any("Class" in e for e in result["errors"]))

    def test_no_subject_columns(self):
        result = parse("Student Name,Class,Phone", "Asha,5,1")
        self.assertTrue(any("No subject columns" in e for e in result["errors"]))

    def test_row_errors(self):
        result = parse(
            HEADER,
            "1,Bad Class,13,,Yes,No,No,No,No",
            "2,Zero Class,0,,Yes,No,No,No,No",
            "3,No Subject,5,,No,No,No,No,No",
            "4,,5,,Yes,No,No,No,No",
            "5,Bad Cell,5,,maybe,No,No,No,No",
            "6,Bad Mobile,5,abc,Yes,No,No,No,No",
        )
        errors = [" ".join(r["errors"]) for r in result["rows"]]
        self.assertIn("Class must be a number from 1 to 12", errors[0])
        self.assertIn("Class must be a number from 1 to 12", errors[1])
        self.assertIn("at least one subject", errors[2])
        self.assertIn("name is missing", errors[3])
        self.assertIn("use Yes/No or 1/0", errors[4])
        self.assertIn("Mobile number", errors[5])

    def test_duplicate_students_flagged_but_same_name_in_other_class_allowed(self):
        result = parse(
            HEADER,
            "1,Asha Rao,5,9876543210,Yes,No,No,No,No",
            "2,asha  rao,5,98765 43210,No,Yes,No,No,No",
            "3,Asha Rao,6,9876543210,Yes,No,No,No,No",
        )
        self.assertEqual(result["rows"][0]["errors"], [])
        self.assertIn("Duplicate of row 2", result["rows"][1]["errors"][0])
        self.assertEqual(result["rows"][2]["errors"], [])

    def test_blank_rows_are_skipped_and_unknown_columns_warned(self):
        result = parse(HEADER + ",Notes", "1,Asha,5,,Yes,No,No,No,No,hello", ",,,,,,,,,", "")
        self.assertEqual(len(result["rows"]), 1)
        self.assertTrue(any("Notes" in w for w in result["warnings"]))


class TestSchoolStudentList(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        frappe.set_user("Administrator")
        cls.users = []
        cls.schools = []
        for index in (1, 2):
            email = f"ssl-school-{index}@example.com"
            if not frappe.db.exists("User", email):
                frappe.get_doc(
                    {"doctype": "User", "email": email, "first_name": f"School{index}", "send_welcome_email": 0}
                ).insert(ignore_permissions=True)
            customer = frappe.get_doc(
                {
                    "doctype": "Customer",
                    "customer_name": f"SSL Test School {index}",
                    "customer_type": "Company",
                    "custom_ito_school_code": f"SSLT{index}",
                    "custom_current_academic_year": "AY-2026/27",
                }
            )
            customer.append("portal_users", {"user": email})
            customer.insert(ignore_permissions=True)
            cls.users.append(email)
            cls.schools.append(customer.name)

    def setUp(self):
        frappe.set_user("Administrator")
        for school in self.schools:
            for name in frappe.get_all("Registered Students", filters={"school": school}, pluck="name"):
                frappe.delete_doc("Registered Students", name, force=True, ignore_permissions=True)
            for name in frappe.get_all("Exams Summary", filters={"customer": school}, pluck="name"):
                frappe.delete_doc("Exams Summary", name, force=True, ignore_permissions=True)
            frappe.db.set_value("Customer", school, "custom_registration_date", None)
        frappe.set_user(self.users[0])
        patcher = patch.object(ssl, "get_subject_columns", return_value=SUBJECTS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(frappe.set_user, "Administrator")

    def upload(self, *lines, replace=0, user=None):
        if user:
            frappe.set_user(user)
        return ssl.save_school_student_list(encode(*lines), "list.csv", replace)

    def make_link_student(self, school, name, class_name="5"):
        student = frappe.new_doc("Registered Students")
        student.student_name = name
        student.school = school
        student.set("class", class_name)
        student.academic_year = "AY-2026/27"
        student.registration_source = ssl.SOURCE_LINK
        student.append("subjects_registered", {"subject": "Hindi Olympiad (NHO)", "abbr": "NHO"})
        student.insert(ignore_permissions=True)
        return student

    def test_upload_creates_one_record_per_student_with_all_subjects(self):
        result = self.upload(
            HEADER,
            "1,Asha Rao,5,,Yes,Yes,Yes,No,No",
            "2,Ravi Kumar,6,,Yes,No,No,No,No",
        )
        self.assertTrue(result["success"], result)
        students = frappe.get_all(
            "Registered Students",
            filters={"school": self.schools[0]},
            fields=["name", "class", "registration_source", "from_student_portal", "academic_year"],
        )
        self.assertEqual(len(students), 2)
        self.assertTrue(all(s.registration_source == "School" for s in students))
        asha = frappe.get_doc("Registered Students", {"school": self.schools[0], "student_name": "ASHA RAO"})
        self.assertEqual(sorted(r.abbr for r in asha.subjects_registered), ["ICO", "IDO", "NHO"])

    def test_invalid_file_saves_nothing(self):
        result = self.upload(HEADER, "1,Good,5,,Yes,No,No,No,No", "2,Bad,14,,Yes,No,No,No,No")
        self.assertFalse(result["success"])
        self.assertEqual(frappe.db.count("Registered Students", {"school": self.schools[0]}), 0)

    def test_same_name_students_can_both_be_saved(self):
        result = self.upload(HEADER, "1,Asha Rao,5,,Yes,No,No,No,No", "2,Asha Rao,6,,Yes,No,No,No,No")
        self.assertTrue(result["success"], result)
        self.assertEqual(frappe.db.count("Registered Students", {"school": self.schools[0]}), 2)

    def test_exam_summary_counts_are_derived_and_client_counts_ignored(self):
        self.upload(
            HEADER,
            "1,A One,5,,Yes,Yes,No,No,No",
            "2,B Two,5,,Yes,No,No,No,No",
            "3,C Three,6,,Yes,No,No,No,No",
        )
        # The browser claims 999 students and names a class that has none.
        ssl.sync_exam_summary(
            self.schools[0],
            {
                "Hindi Olympiad (NHO)": [
                    {"class": "5", "teacher_name": "Mrs Gupta", "whatsapp": "+91 9000000001", "students": "999"},
                    {"class": "11", "teacher_name": "Ghost", "whatsapp": "1", "students": "50"},
                ]
            },
        )
        doc = frappe.get_doc(
            "Exams Summary", {"customer": self.schools[0], "is_little_champ": 0, "is_wof": 0}
        )
        rows = {(r.get("class"), r.subject): r for r in doc.exam_summary}
        self.assertEqual(
            {key: row.no_of_students for key, row in rows.items()},
            {
                ("5", "Hindi Olympiad (NHO)"): 2,
                ("6", "Hindi Olympiad (NHO)"): 1,
                ("5", "Drawing Olympiad (IDO)"): 1,
            },
        )
        self.assertEqual(rows[("5", "Hindi Olympiad (NHO)")].teacher_name, "Mrs Gupta")
        self.assertEqual(rows[("5", "Hindi Olympiad (NHO)")].whatsapp_no, "+91 9000000001")
        self.assertNotIn(("11", "Hindi Olympiad (NHO)"), rows)

    def test_a_student_is_never_double_counted(self):
        self.upload(HEADER, "1,A One,5,,Yes,Yes,Yes,No,No")
        derived = ssl.derive_registration(ssl.get_session_school())
        self.assertEqual(derived["students"], 1)
        self.assertEqual(derived["entries"], 3)
        self.assertEqual(sorted(derived["counts"].values()), [1, 1, 1])

    def test_one_subject_in_four_is_free_per_student(self):
        self.upload(
            HEADER,
            "1,Three Subjects,5,,Yes,Yes,Yes,No,No",
            "2,Four Subjects,5,,Yes,Yes,Yes,Yes,No",
            "3,Five Subjects,6,,Yes,Yes,Yes,Yes,Yes",
            "4,One Subject,6,,Yes,No,No,No,No",
        )
        basis = ssl.get_fee_basis(self.schools[0])
        self.assertEqual(basis["students"], 4)
        self.assertEqual(basis["entries"], 3 + 4 + 5 + 1)
        self.assertEqual(basis["free"], 0 + 1 + 1 + 0)
        self.assertEqual(basis["paid"], 13 - 2)

    def test_fee_basis_ignores_link_students_and_other_schools(self):
        self.make_link_student(self.schools[0], "LINK STUDENT")
        self.assertFalse(ssl.get_fee_basis(self.schools[0])["has_list"])
        self.upload(HEADER, "1,Own,5,,Yes,No,No,No,No", user=self.users[1])
        self.assertFalse(ssl.get_fee_basis(self.schools[0])["has_list"])
        self.assertTrue(ssl.get_fee_basis(self.schools[1])["has_list"])

    def test_replace_needs_confirmation_and_then_replaces(self):
        self.upload(HEADER, "1,Old One,5,,Yes,No,No,No,No", "2,Old Two,5,,Yes,No,No,No,No")
        result = self.upload(HEADER, "1,New One,7,,Yes,No,No,No,No")
        self.assertTrue(result.get("needs_confirmation"))
        self.assertEqual(frappe.db.count("Registered Students", {"school": self.schools[0]}), 2)

        result = self.upload(HEADER, "1,New One,7,,Yes,No,No,No,No", replace=1)
        self.assertTrue(result["success"], result)
        names = frappe.get_all("Registered Students", filters={"school": self.schools[0]}, pluck="student_name")
        # a site-wide Case Format rule may re-case names on save, so compare without case
        self.assertEqual([n.upper() for n in names], ["NEW ONE"])
        doc = frappe.get_doc("Exams Summary", {"customer": self.schools[0], "is_little_champ": 0, "is_wof": 0})
        self.assertEqual([(r.get("class"), r.no_of_students) for r in doc.exam_summary], [("7", 1)])

    def test_replace_keeps_link_students(self):
        self.make_link_student(self.schools[0], "LINK STUDENT")
        self.upload(HEADER, "1,Own,5,,Yes,No,No,No,No")
        self.upload(HEADER, "1,Own Again,5,,Yes,No,No,No,No", replace=1)
        self.assertTrue(frappe.db.exists("Registered Students", {"student_name": "LINK STUDENT", "not_registered": 0}))

    def test_list_stays_editable_after_registration_is_submitted(self):
        self.upload(HEADER, "1,Own,5,,Yes,No,No,No,No")
        frappe.db.set_value("Customer", self.schools[0], "custom_registration_date", frappe.utils.today())
        result = self.upload(HEADER, "1,Other,5,,Yes,No,No,No,No", replace=1)
        self.assertTrue(result["success"], result)
        # a changed list reopens payment: the school must submit again
        self.assertFalse(frappe.db.get_value("Customer", self.schools[0], "custom_registration_date"))

    def test_list_is_locked_after_application_deadline(self):
        self.upload(HEADER, "1,Own,5,,Yes,No,No,No,No")
        frappe.db.set_value(
            "Customer", self.schools[0], "custom_application_deadline", frappe.utils.add_days(frappe.utils.today(), -1)
        )
        with self.assertRaises(frappe.ValidationError):
            self.upload(HEADER, "1,Other,5,,Yes,No,No,No,No", replace=1)

    def test_list_is_open_on_the_deadline_day(self):
        frappe.db.set_value("Customer", self.schools[0], "custom_application_deadline", frappe.utils.today())
        result = self.upload(HEADER, "1,Own,5,,Yes,No,No,No,No")
        self.assertTrue(result["success"], result)

    def test_view_lists_both_sources_scoped_to_the_session_school(self):
        self.upload(HEADER, "1,School Kid,5,,Yes,Yes,No,No,No", "2,Other Kid,6,,No,Yes,No,No,No")
        self.make_link_student(self.schools[0], "LINK KID", "5")
        self.upload(HEADER, "1,Foreign Kid,5,,Yes,No,No,No,No", user=self.users[1])
        frappe.set_user(self.users[0])

        everything = ssl.get_school_student_list()
        self.assertEqual(everything["totals"], {"school": 2, "link": 1, "not_recorded": 0, "total": 3})
        self.assertNotIn("FOREIGN KID", [s["student_name"].upper() for s in everything["students"]])
        labels = {s["student_name"].upper(): s["source_label"] for s in everything["students"]}
        self.assertEqual(labels["LINK KID"], "Paid individually via Link")
        self.assertEqual(labels["SCHOOL KID"], "Paid via School")

        self.assertEqual(ssl.get_school_student_list({"class": "5"})["shown"], 2)
        self.assertEqual(ssl.get_school_student_list({"subject": "IDO"})["shown"], 2)
        self.assertEqual(ssl.get_school_student_list({"source": "Student Portal Link"})["shown"], 1)
        # filters narrow the table, not the headline totals
        self.assertEqual(ssl.get_school_student_list({"class": "6"})["totals"]["total"], 3)

    def test_client_cannot_name_another_school(self):
        self.upload(HEADER, "1,Foreign Kid,5,,Yes,No,No,No,No", user=self.users[1])
        frappe.set_user(self.users[0])
        result = ssl.get_school_student_list({"school": self.schools[1]})
        self.assertEqual(result["totals"]["total"], 0)

    def test_guest_and_unlinked_users_are_refused(self):
        frappe.set_user("Guest")
        with self.assertRaises(frappe.PermissionError):
            ssl.get_school_student_list()
        frappe.set_user("Administrator")
        with self.assertRaises(frappe.PermissionError):
            ssl.get_school_student_list()

    def test_portal_link_source_keeps_legacy_flag_in_step(self):
        student = self.make_link_student(self.schools[0], "LINK KID")
        self.assertEqual(student.from_student_portal, 1)
        legacy = frappe.new_doc("Registered Students")
        legacy.student_name = "LEGACY LINK"
        legacy.school = self.schools[0]
        legacy.from_student_portal = 1
        legacy.insert(ignore_permissions=True)
        self.assertEqual(legacy.registration_source, "Student Portal Link")
