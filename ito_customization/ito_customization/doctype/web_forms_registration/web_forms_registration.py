# apps/ito_customization/ito_customization/ito_customization/doctype/web_forms_registration/web_forms_registration.py
import frappe
from frappe.model.document import Document
from frappe.rate_limiter import rate_limit

ALREADY_REGISTERED_MSG = "Registration has already been done with this email ID."
MOBILE_ALREADY_REGISTERED_MSG = "Registration has already been done with this mobile number."


class WebFormsRegistration(Document):
	def validate(self):
		# normalise so "A@x.com " and "a@x.com" count as the same person
		if self.email:
			self.email = self.email.strip().lower()
			if _email_exists(self.email, exclude=self.name):
				frappe.throw(ALREADY_REGISTERED_MSG, title="Already Registered")
		if self.mobile_number:
			self.mobile_number = self.mobile_number.strip()
			if _mobile_exists(self.mobile_number, exclude=self.name):
				frappe.throw(MOBILE_ALREADY_REGISTERED_MSG, title="Already Registered")

	def before_insert(self):
		if not self.epass_token:
			self.epass_token = frappe.generate_hash(length=24)

	def on_update(self):
		previous = self.get_doc_before_save()
		if not previous:
			return

		from ito_customization.ito_customization.doc_events.epass import is_approved, queue_epass

		if is_approved(previous) or not is_approved(self):
			return
		queue_epass(self)


def _email_exists(email, exclude=None):
	filters = {"email": email.strip().lower()}
	if exclude:
		filters["name"] = ["!=", exclude]
	return bool(frappe.db.exists("Web Forms Registration", filters))


def _mobile_exists(mobile_number, exclude=None):
	filters = {"mobile_number": mobile_number.strip()}
	if exclude:
		filters["name"] = ["!=", exclude]
	return bool(frappe.db.exists("Web Forms Registration", filters))


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=30, seconds=60)
def email_registered(email=None):
	"""Used by the web form to warn early. Returns only True/False."""
	if not email:
		return False
	return _email_exists(email)


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=30, seconds=60)
def mobile_registered(mobile_number=None):
	"""Used by the web form to warn early. Returns only True/False."""
	if not mobile_number:
		return False
	return _mobile_exists(mobile_number)