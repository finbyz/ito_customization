import frappe
from erpnext.selling.doctype.customer.customer import Customer, make_contact, make_address

class CustomCustomer(Customer):
	def create_primary_contact(self):
		roles = frappe.get_roles(frappe.session.user)
		if "Customer" in roles:
			self.flags.ignore_permissions = True
			
			if not self.customer_primary_contact and not self.lead_name:
				if self.mobile_no or self.email_id or self.first_name or self.last_name:
					contact = make_contact(self)
					self.db_set("customer_primary_contact", contact.name)
					self.db_set("mobile_no", self.mobile_no)
					self.db_set("email_id", self.email_id)
			elif self.customer_primary_contact:
				frappe.db.set_value("Contact", self.customer_primary_contact, "is_primary_contact", 1)
				
			self.flags.ignore_permissions = False
		else:
			super(CustomCustomer, self).create_primary_contact()

	def create_primary_address(self):
		from frappe.contacts.doctype.address.address import get_address_display

		roles = frappe.get_roles(frappe.session.user)
		if "Customer" in roles:
			self.flags.ignore_permissions = True

			if self.flags.is_new_doc and self.get("address_line1"):
				address = make_address(self)
				address_display = get_address_display(address.name)

				self.db_set("customer_primary_address", address.name)
				self.db_set("primary_address", address_display)
			elif self.customer_primary_address:
				frappe.db.set_value("Address", self.customer_primary_address, "is_primary_address", 1)  # ensure

			self.flags.ignore_permissions = False
		else:
			super(CustomCustomer, self).create_primary_address()
