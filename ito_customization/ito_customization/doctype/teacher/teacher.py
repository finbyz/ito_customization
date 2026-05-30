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
def make_sales_partner(teacher):
	"""Create a Sales Partner from Teacher data"""
	
	# Get the teacher document
	teacher_doc = frappe.get_doc("Teacher", teacher)
	
	# Check if Sales Partner already exists
	if teacher_doc.co_ordinator:
		frappe.throw(_("This teacher already has a Co-ordinator: {0}").format(teacher_doc.co_ordinator))
	
	# Check if a Sales Partner with the same name already exists
	if frappe.db.exists("Sales Partner", teacher_doc.name1):
		frappe.throw(_("A Sales Partner with the name {0} already exists").format(teacher_doc.name1))
	
	# Create new Sales Partner
	sales_partner = frappe.new_doc("Sales Partner")
	
	# Basic Information
	sales_partner.partner_name = teacher_doc.name1
	sales_partner.partner_type = "Dealer"
	sales_partner.territory = "India"
	
	# Custom fields from Teacher
	if teacher_doc.email_id:
		sales_partner.custom_email_id = teacher_doc.email_id
	
	if teacher_doc.phone_number:
		sales_partner.custom_mobile_number = teacher_doc.phone_number
	
	if teacher_doc.date_of_birth:
		sales_partner.custom_date_of_birth = teacher_doc.date_of_birth
	
	if teacher_doc.school_name:
		sales_partner.custom_school_name = teacher_doc.school_name
	
	# Set profession as Teacher
	sales_partner.custom_profession_occupation = "Teacher"
	
	# Get Teacher's address and copy to Sales Partner custom fields
	teacher_address = get_teacher_primary_address(teacher_doc.name)
	if teacher_address:
		# Copy address fields to Sales Partner custom fields
		if teacher_address.get("city"):
			sales_partner.custom_city = teacher_address.get("city")
		
		if teacher_address.get("state"):
			sales_partner.custom_state = teacher_address.get("state")
		
		if teacher_address.get("country"):
			sales_partner.custom_country = teacher_address.get("country")
		
		if teacher_address.get("pincode"):
			sales_partner.custom_pincode = teacher_address.get("pincode")
		
		if teacher_address.get("custom_district"):
			sales_partner.custom_district = teacher_address.get("custom_district")
	
	# Add description
	sales_partner.description = f"<p>Created from Teacher: {teacher_doc.name}</p>"
	sales_partner.introduction = f"Co-ordinator created from Teacher {teacher_doc.name1}"
	
	# Reference to Teacher
	sales_partner.custom_teacher_ref = teacher_doc.name
	
	# Insert the Sales Partner
	sales_partner.insert(ignore_permissions=True)
	
	# Update Teacher with Sales Partner reference
	teacher_doc.co_ordinator = sales_partner.name
	teacher_doc.save(ignore_permissions=True)
	
	frappe.db.commit()
	
	return sales_partner.name


def get_teacher_primary_address(teacher_name):
	"""Get the primary address of a teacher"""
	
	# Get all addresses linked to this teacher
	addresses = frappe.get_all(
		"Dynamic Link",
		filters={
			"link_doctype": "Teacher",
			"link_name": teacher_name,
			"parenttype": "Address"
		},
		fields=["parent"]
	)
	
	if not addresses:
		return None
	
	# Try to get primary address first
	for addr in addresses:
		address_doc = frappe.get_doc("Address", addr.parent)
		if address_doc.is_primary_address:
			return {
				"name": address_doc.name,
				"city": address_doc.city,
				"state": address_doc.state,
				"country": address_doc.country,
				"pincode": address_doc.pincode,
				"custom_district": address_doc.get("custom_district")
			}
	
	# If no primary address, return the first address
	if addresses:
		address_doc = frappe.get_doc("Address", addresses[0].parent)
		return {
			"name": address_doc.name,
			"city": address_doc.city,
			"state": address_doc.state,
			"country": address_doc.country,
			"pincode": address_doc.pincode,
			"custom_district": address_doc.get("custom_district")
		}
	
	return None


@frappe.whitelist()
def get_addresses(teacher_id):
	"""Get all addresses linked to this teacher"""
	addresses = frappe.get_all(
		"Dynamic Link",
		filters={
			"link_doctype": "Teacher",
			"link_name": teacher_id,
			"parenttype": "Address"
		},
		fields=["parent"]
	)
	
	address_list = []
	for addr in addresses:
		address_doc = frappe.get_doc("Address", addr.parent)
		address_list.append({
			"name": address_doc.name,
			"address_title": address_doc.address_title,
			"display": address_doc.get_display(),
			"is_primary_address": address_doc.is_primary_address
		})
	
	return address_list

