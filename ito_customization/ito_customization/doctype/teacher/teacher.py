# Copyright (c) 2026, FinByz Tech Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, today
from dateutil.relativedelta import relativedelta


class Teacher(Document):
	def validate(self):
		self.validate_date_of_birth()
	
	def validate_date_of_birth(self):
		"""Validate that teacher's age is at least 15 years from current date"""
		if self.date_of_birth:
			dob = getdate(self.date_of_birth)
			current_date = getdate(today())
			
			# Calculate age using relativedelta for accurate year calculation
			age = relativedelta(current_date, dob)
			
			if age.years < 15:
				frappe.throw(
					_("Teacher must be at least 15 years old. Current age is {0} years.").format(age.years),
					title=_("Invalid Date of Birth")
				)
			
			# Also check if date of birth is in the future
			if dob > current_date:
				frappe.throw(
					_("Date of Birth cannot be in the future."),
					title=_("Invalid Date of Birth")
				)


@frappe.whitelist()
def get_addresses(teacher_id):
	from frappe.contacts.doctype.address.address import get_address_display_list
	return get_address_display_list("Teacher", teacher_id)
