# Copyright (c) 2026, FinByz Tech Pvt Ltd and Contributors
# See license.txt

from contextlib import nullcontext
from unittest.mock import MagicMock, patch

from frappe.tests import UnitTestCase

from ito_customization.ito_customization.doctype.student_portal import student_portal


# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]



class UnitTestStudentPortal(UnitTestCase):
	"""
	Unit tests for Student Portal registration behavior.
	"""

	def test_roll_number_does_not_block_registration_payment(self):
		customer_doc = MagicMock()
		customer_doc.get.return_value = "TEST-SCHOOL"
		data = {
			"token": "valid-token",
			"student_full_name": "Test Student",
			"email_id": "student@example.com",
			"class": "Class 1",
			"roll_no": "IGNORED-ROLL",
			"selected_exams": [
				{"school_subject": "Math", "selected": True},
			],
		}

		with (
			patch.object(student_portal, "IgnorePermissionsContext", return_value=nullcontext()),
			patch.object(student_portal, "_resolve_customer_via_token", return_value=("Test School", None)),
			patch.object(student_portal.frappe, "get_doc", return_value=customer_doc),
			patch.object(student_portal, "_get_valid_class_name", return_value="Class 1"),
			patch.object(student_portal, "_get_student_portal_registered_student_by_email", return_value=None),
			patch.object(student_portal.frappe.db, "sql", return_value=[]),
			patch.object(student_portal.frappe.db, "exists", return_value=True),
			patch.object(student_portal.frappe.db, "get_value", return_value="M"),
			patch.object(
				student_portal,
				"initiate_student_portal_registration_payment",
				return_value=("INV-001", {"checkout_url": "https://example.com/checkout"}),
			) as initiate_payment,
		):
			result = student_portal.create_student_portal_entry(data)

		self.assertTrue(result["success"])
		self.assertEqual(result["sales_invoice"], "INV-001")
		initiate_payment.assert_called_once()
		self.assertNotIn("roll_no", initiate_payment.call_args.args[0])

	def test_registered_student_can_be_named_without_roll_number(self):
		doc = student_portal.frappe.new_doc("Registered Students")
		doc.roll_no = None
		doc.student_name = "Student Without Roll Number"

		doc.set_new_name()

		self.assertEqual(doc.name, doc.student_name)
		self.assertFalse(doc.roll_no)

	def test_public_hall_ticket_download_scopes_print_permission_bypass(self):
		flag = student_portal.frappe.flags
		previous_ignore = getattr(flag, "ignore_print_permissions", False)

		def download(name):
			self.assertEqual(name, "STUDENT-001")
			self.assertTrue(flag.ignore_print_permissions)

		with patch(
			"ito_customization.ito_customization.doc_events.hall_ticket.download_hall_ticket",
			side_effect=download,
		):
			student_portal._download_validated_hall_ticket("STUDENT-001")

		self.assertEqual(getattr(flag, "ignore_print_permissions", False), previous_ignore)

	def test_public_hall_ticket_download_restores_print_permissions_on_error(self):
		flag = student_portal.frappe.flags
		previous_ignore = getattr(flag, "ignore_print_permissions", False)

		with patch(
			"ito_customization.ito_customization.doc_events.hall_ticket.download_hall_ticket",
			side_effect=RuntimeError("PDF failed"),
		):
			with self.assertRaises(RuntimeError):
				student_portal._download_validated_hall_ticket("STUDENT-001")

		self.assertEqual(getattr(flag, "ignore_print_permissions", False), previous_ignore)

	def test_registration_saves_registered_student_and_selected_subjects(self):
		doc = MagicMock()
		doc.doctype = "Registered Students"
		doc.roll_no = None
		doc.get.return_value = []
		doc.is_new.return_value = True
		payload = {
			"school_name": "Test School",
			"student_full_name": "Test Student",
			"parent_full_name": "Test Parent",
			"email_id": "student@example.com",
			"mobile_number": "1234567890",
			"class": "Class 1",
			"roll_no": "IGNORED-ROLL",
			"exam_list": [
				{"school_subject": "Math", "abbr": "M", "check_ptxn": 1},
				{"school_subject": "Science", "abbr": "S", "check_ptxn": 0},
			],
		}
		school_doc = MagicMock()
		school_doc.name = "Test School"
		school_doc.customer_name = "Test School Name"
		school_doc.get.side_effect = lambda fieldname: {
			"custom_ito_school_code": "ITO01",
		}.get(fieldname)

		with (
			patch.object(student_portal, "_get_student_portal_registered_student_by_email", return_value=None),
			patch.object(student_portal.frappe, "new_doc", return_value=doc),
			patch.object(student_portal.frappe, "get_doc", return_value=school_doc),
		):
			result = student_portal._save_student_portal_registered_student(payload)

		self.assertIs(result, doc)
		self.assertEqual(doc.doctype, "Registered Students")
		self.assertEqual(doc.school, "Test School")
		self.assertEqual(doc.student_name, "Test Student")
		self.assertEqual(doc.parent_name, "Test Parent")
		self.assertEqual(doc.email_id, "student@example.com")
		self.assertEqual(doc.mobile_number, "1234567890")
		self.assertIsNone(doc.roll_no)
		self.assertEqual(doc.ito_school_code, "ITO01")
		self.assertEqual(doc.full_name, "Test School Name")
		self.assertEqual(doc.from_student_portal, 1)
		doc.append.assert_called_once_with(
			"subjects_registered",
			{"subject": "Math", "abbr": "M"},
		)
		doc.insert.assert_called_once_with(ignore_permissions=True)
