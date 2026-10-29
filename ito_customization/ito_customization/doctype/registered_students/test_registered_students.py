# Copyright (c) 2026, FinByz Tech Pvt Ltd and Contributors
# See license.txt

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase
from ito_customization.ito_customization.doctype.registered_students import registered_students


# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]



class IntegrationTestRegisteredStudents(IntegrationTestCase):
	"""
	Integration tests for RegisteredStudents.
	Use this class for testing interactions between multiple components.
	"""

	pass


class UnitTestRegisteredStudents(UnitTestCase):
	def test_roll_numbers_are_generated_in_alphabetical_order(self):
		school = MagicMock()
		school.get.side_effect = lambda fieldname: {
			"custom_ito_school_code": "ITO01",
			"custom_application_deadline": "2025-12-31",
		}.get(fieldname)
		student_b = MagicMock()
		student_b.name = "STU-B"
		student_b.student_name = "Zoe"
		student_b.serial_number = ""
		student_b.school = "Test School"
		student_b.roll_no = None
		student_b.get.side_effect = lambda fieldname: {
			"class": "Class 1",
			"not_registered": 0,
		}.get(fieldname)
		student_a = MagicMock()
		student_a.name = "STU-A"
		student_a.student_name = "Alice"
		student_a.serial_number = ""
		student_a.school = "Test School"
		student_a.roll_no = None
		student_a.get.side_effect = lambda fieldname: {
			"class": "Class 1",
			"not_registered": 0,
		}.get(fieldname)
		students = {"STU-A": student_a, "STU-B": student_b}
		roll_numbers = iter(("ITO0101.001", "ITO0101.002"))

		with (
			patch.object(registered_students.frappe, "has_permission", return_value=True),
			patch.object(
				registered_students.frappe,
				"get_doc",
				side_effect=lambda doctype, name: school if doctype == "Customer" else students[name],
			),
			patch.object(
				registered_students,
				"make_autoname",
				side_effect=lambda pattern: next(roll_numbers),
			) as make_autoname,
			patch.object(registered_students, "today", return_value="2026-01-01"),
		):
			result = registered_students.generate_roll_numbers_for_hall_tickets(
				"Test School", "Class 1", ["STU-B", "STU-A"]
			)

		self.assertEqual(result["names"], ["STU-A", "STU-B"])
		self.assertEqual(student_a.roll_no, "ITO0101.001")
		self.assertEqual(student_b.roll_no, "ITO0101.002")
		student_a.save.assert_called_once_with()
		student_b.save.assert_called_once_with()
		self.assertEqual(make_autoname.call_count, 2)

	def test_roll_numbers_are_blocked_until_application_deadline_has_passed(self):
		school = MagicMock()
		school.get.side_effect = lambda fieldname: {
			"custom_application_deadline": "2026-01-01",
		}.get(fieldname)

		with (
			patch.object(registered_students.frappe, "has_permission", return_value=True),
			patch.object(registered_students.frappe, "get_doc", return_value=school),
			patch.object(registered_students, "today", return_value="2026-01-01"),
			patch.object(registered_students, "format_date", return_value="Jan 1, 2026"),
			patch.object(registered_students, "make_autoname") as make_autoname,
		):
			with self.assertRaises(frappe.ValidationError) as context:
				registered_students.generate_roll_numbers_for_hall_tickets(
					"Test School", "Class 1", ["STU-A"]
				)

		self.assertIn("Application deadline: Jan 1, 2026", str(context.exception))
		make_autoname.assert_not_called()

	def test_roll_numbers_are_blocked_when_application_deadline_is_missing(self):
		school = MagicMock()
		school.get.return_value = None

		with (
			patch.object(registered_students.frappe, "has_permission", return_value=True),
			patch.object(registered_students.frappe, "get_doc", return_value=school),
			patch.object(registered_students, "make_autoname") as make_autoname,
		):
			with self.assertRaises(frappe.ValidationError) as context:
				registered_students.generate_roll_numbers_for_hall_tickets(
					"Test School", "Class 1", ["STU-A"]
				)

		self.assertIn("Application Deadline", str(context.exception))
		make_autoname.assert_not_called()
