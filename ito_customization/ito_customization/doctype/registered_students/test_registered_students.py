# Copyright (c) 2026, FinByz Tech Pvt Ltd and Contributors
# See license.txt

from unittest.mock import MagicMock, patch

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
		school.get.return_value = "ITO01"
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
