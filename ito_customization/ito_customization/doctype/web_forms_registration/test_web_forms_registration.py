# Copyright (c) 2026, FinByz Tech Pvt Ltd and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase


# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]



class IntegrationTestWebFormsRegistration(IntegrationTestCase):
	"""
	Integration tests for WebFormsRegistration.
	Use this class for testing interactions between multiple components.
	"""

	def test_duplicate_mobile_number_is_rejected(self):
		mobile_number = "9" + str(int(frappe.generate_hash(length=8), 16) % 100000000).zfill(8)
		frappe.get_doc({
			"doctype": "Web Forms Registration",
			"mobile_number": mobile_number,
		}).insert(ignore_permissions=True)

		duplicate = frappe.get_doc({
			"doctype": "Web Forms Registration",
			"mobile_number": f" {mobile_number} ",
		})
		with self.assertRaises(frappe.ValidationError):
			duplicate.insert(ignore_permissions=True)
